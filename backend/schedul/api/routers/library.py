"""The shared equipment library, its review queue, and the workbook round trip.

Products get in three ways, and all three end at the same planner: typed on a
schedule, pasted as a block, or filled into the workbook this hands out. The
workbook is the one that scales -- a practice can take the whole library out,
one sheet per type, correct it where correcting a hundred rows is a drag of the
fill handle, and bring it back.
"""

from __future__ import annotations

import io
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core.catalogue import ScheduleType
from ...db.models import Equipment, Organisation, ScheduleTypeRow
from ...export.library import (
    library_columns, read_library_workbook, render_library_workbook,
)
from ...services import importing as imp
from ...services import library as lib
from ...services.converters import type_from_row
from ...services.grid import coerce
from ..deps import current_org, get_db, not_found
from ..schemas import BulkEquipmentIn, EquipmentIn, EquipmentOut, LibraryImportIn

router = APIRouter(prefix="/api/library", tags=["library"])

XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _types(session: Session, org: Organisation) -> list[ScheduleType]:
    """Every live type in this organisation, in code order."""
    rows = session.scalars(
        select(ScheduleTypeRow).where(
            ScheduleTypeRow.organisation_id == org.id,
            ScheduleTypeRow.archived.is_(False),
        )
    )
    return sorted((type_from_row(r) for r in rows), key=lambda t: t.code)


def _products_for(
    session: Session, org: Organisation, schedule_type: ScheduleType
) -> list[dict[str, object]]:
    """One type's entries, shaped as the workbook's rows.

    Rejected entries are left out. They are still in the record for the sake of
    a schedule that referenced one, but a file somebody is about to correct and
    send back should not carry rows the practice has already decided against.
    """
    columns = library_columns(schedule_type)
    entries = session.scalars(
        select(Equipment).where(
            Equipment.organisation_id == org.id,
            Equipment.type_code == schedule_type.code,
            Equipment.review_state != "rejected",
        )
    )
    out = []
    for entry in sorted(entries, key=lambda e: e.model_reference.lower()):
        values = entry.values or {}
        row: dict[str, object] = {"Model Reference": entry.model_reference}
        for key in columns[1:]:
            row[key] = values.get(key)
        out.append(row)
    return out


def _type(session: Session, org: Organisation, code: str):
    row = session.scalar(
        select(ScheduleTypeRow).where(
            ScheduleTypeRow.organisation_id == org.id,
            ScheduleTypeRow.code == code.strip().upper(),
        )
    )
    if row is None:
        raise not_found(f"schedule type {code!r}")
    return type_from_row(row)


def _view(entry: Equipment) -> EquipmentOut:
    return EquipmentOut(
        id=entry.id,
        type_code=entry.type_code,
        model_reference=entry.model_reference,
        values=dict(entry.values or {}),
        review_state=entry.review_state,
        created_by=entry.created_by or "",
        updated_at=entry.updated_at,
        flags=[
            {"id": f.id, "kind": f.kind, "message": f.message, "resolved": f.resolved}
            for f in entry.flags
            if not f.resolved
        ],
    )


# ------------------------------------------------------------- workbooks ---
#
# Declared above ``/{type_code}``: a path parameter would otherwise swallow
# ``workbook.xlsx`` and try to look up a schedule type called that.


@router.get("/workbook.xlsx")
def export_workbook(
    code: str = "",
    data: bool = True,
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> FileResponse:
    """The library as a workbook: one sheet per type, headings on row 1.

    ``code`` narrows it to one type; ``data=false`` gives the same file with
    nothing in it, which is the blank template. It is deliberately one endpoint
    and one renderer -- a separate template generator would eventually disagree
    with the exporter about the headings, and the file that came back would stop
    matching the file that went out.
    """
    types = _types(session, org)
    if code:
        wanted = code.strip().upper()
        types = [t for t in types if t.code == wanted]
        if not types:
            raise not_found(f"schedule type {code!r}")
    if not types:
        raise HTTPException(
            status_code=400, detail="this organisation has no schedule types yet"
        )

    products = (
        {t.code: _products_for(session, org, t) for t in types} if data else {}
    )
    stem = (
        f"{types[0].code}_library" if len(types) == 1 else "equipment_library"
    )
    name = f"{stem}{'' if data else '_template'}.xlsx"
    tmp = Path(tempfile.mkdtemp(prefix="schedul-lib-"))
    path = render_library_workbook(types, products, tmp / name)
    return FileResponse(path, filename=name, media_type=XLSX_MEDIA)


@router.post("/workbook/import")
async def import_workbook(
    file: UploadFile = File(...),
    apply: bool = Form(False),
    update_existing: bool = Form(True),
    created_by: str = Form(""),
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> dict[str, object]:
    """Read a filled-in workbook back, a sheet at a time.

    Every sheet goes through the same planner a pasted block does, so duplicate
    handling, the "a blank cell means not stated" rule and the plan-before-it-
    happens guarantee are the ones that already exist rather than a second set
    written for files. Without ``apply`` nothing is written.

    A sheet whose name does not match a schedule type is reported rather than
    guessed at: importing a hundred radiators into the fan coil library because
    somebody renamed a tab is not a recoverable mistake.
    """
    if not (file.filename or "").lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(
            status_code=400,
            detail="that is not an Excel workbook. Save it as .xlsx and try again.",
        )

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="the file is empty")

    try:
        blocks = read_library_workbook(io.BytesIO(raw))
    except Exception as exc:  # a corrupt or password-protected file
        raise HTTPException(
            status_code=400, detail=f"that workbook could not be read: {exc}"
        ) from exc

    known = {t.code: t for t in _types(session, org)}
    sheets: list[dict[str, object]] = []
    total = {"create": 0, "update": 0, "unchanged": 0, "skip": 0}
    applied = 0

    for block in blocks:
        stype = known.get(str(block["code"]))
        if stype is None:
            sheets.append(
                {
                    "sheet": block["sheet"],
                    "type_code": block["code"],
                    "recognised": False,
                    "message": (
                        f"no schedule type called {block['code']!r}, so this sheet "
                        f"was left alone. Name the tab after the type's code."
                    ),
                }
            )
            continue

        plan = imp.plan_import(
            session, org.id, stype, str(block["text"]),
            header=True,
            update_existing=update_existing,
        )
        if apply and plan.can_apply:
            imp.apply_import(session, org.id, stype, plan, created_by=created_by)
            applied += plan.applied
        for key, value in plan.counts.items():
            total[key] = total.get(key, 0) + value
        sheets.append({"sheet": block["sheet"], "recognised": True, **plan.to_dict()})

    if not sheets:
        raise HTTPException(
            status_code=400,
            detail="no sheet in that workbook had a heading row and data under it",
        )

    return {
        "applied": applied,
        "counts": total,
        "sheets": sheets,
        "can_apply": any(s.get("can_apply") for s in sheets),
        "destructive": any(s.get("destructive") for s in sheets),
    }


@router.get("/{type_code}", response_model=list[EquipmentOut])
def list_equipment(
    type_code: str,
    q: str = "",
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> list[EquipmentOut]:
    stmt = select(Equipment).where(
        Equipment.organisation_id == org.id,
        Equipment.type_code == type_code.strip().upper(),
        Equipment.review_state != "rejected",
    )
    entries = list(session.scalars(stmt))
    if q:
        needle = q.strip().lower()
        entries = [
            e
            for e in entries
            if needle in e.model_reference.lower()
            or any(needle in str(v).lower() for v in (e.values or {}).values())
        ]
    entries.sort(key=lambda e: e.model_reference)
    return [_view(e) for e in entries]


@router.post("", response_model=EquipmentOut, status_code=201)
def save_equipment(
    payload: EquipmentIn,
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> EquipmentOut:
    """Save a product. It is usable at once and flagged for review.

    The queue ranks rather than gates: v1's submissions inbox existed to stop
    concurrent writes corrupting a shared .xlsx, which a database does not do.
    """
    stype = _type(session, org, payload.type_code)
    try:
        entry, _ = lib.save_equipment(
            session,
            org.id,
            stype,
            payload.model_reference,
            payload.values,
            created_by=payload.created_by,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _view(entry)


@router.post("/bulk", response_model=list[EquipmentOut], status_code=201)
def save_many(
    payload: BulkEquipmentIn,
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> list[EquipmentOut]:
    """Save several products at once, from the grid editor.

    Each goes through the same path as one typed on a schedule, so it is live
    immediately, flagged for review and logged. A row with no model reference is
    refused rather than saved under a blank key -- the reference is what every
    schedule row points at.
    """
    stype = _type(session, org, payload.type_code)
    missing = [i + 1 for i, r in enumerate(payload.rows) if not r.model_reference.strip()]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=(
                f"row(s) {', '.join(map(str, missing))} have no Model Reference. "
                f"It is the key every schedule row points at, so it cannot be blank."
            ),
        )

    saved = []
    for item in payload.rows:
        entry, _ = lib.save_equipment(
            session, org.id, stype, item.model_reference, item.values,
            created_by=payload.created_by or item.created_by,
        )
        saved.append(entry)
    return [_view(e) for e in saved]


@router.get("/{type_code}/import/columns")
def import_columns(
    type_code: str,
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> dict[str, object]:
    """Which columns an import can fill for this type.

    Input and derived columns are not offered: one differs per unit and the
    other is calculated, so either would be a stale copy the moment it landed.
    """
    stype = _type(session, org, type_code)
    return {"type_code": stype.code, "columns": imp.target_columns(stype)}


@router.post("/import")
def import_products(
    payload: LibraryImportIn,
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> dict[str, object]:
    """Plan an import, and carry it out only when asked to.

    Without ``apply`` this writes nothing at all: it reports, row by row, what
    would be created, updated, left alone or refused, and why. Applying takes
    that same plan and performs it, so what was confirmed is what happens.
    """
    stype = _type(session, org, payload.type_code)
    plan = imp.plan_import(
        session, org.id, stype, payload.text,
        mapping=payload.mapping,
        header=payload.header,
        update_existing=payload.update_existing,
    )
    if payload.apply:
        if not plan.can_apply:
            raise HTTPException(
                status_code=400,
                detail="there is nothing to import: no row would create or update a product",
            )
        imp.apply_import(session, org.id, stype, plan, created_by=payload.created_by)
    return plan.to_dict()


@router.post("/inspect")
def inspect_equipment(
    payload: EquipmentIn,
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> dict[str, object]:
    """What saving this entry would flag, without saving it."""
    stype = _type(session, org, payload.type_code)
    findings = lib.inspect_entry(
        session, org.id, stype, payload.model_reference, payload.values
    )
    return {
        "findings": [
            {"kind": f.kind, "message": f.message, "related_id": f.related_id}
            for f in findings
        ]
    }


@router.get("/review/queue")
def review_queue(
    include_resolved: bool = False,
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> list[dict[str, object]]:
    """Entries needing a look, worst first."""
    return lib.review_queue(session, org.id, include_resolved=include_resolved)


@router.post("/review/{equipment_id}/{state}", response_model=EquipmentOut)
def set_state(
    equipment_id: str,
    state: str,
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> EquipmentOut:
    entry = session.get(Equipment, equipment_id)
    if entry is None or entry.organisation_id != org.id:
        raise not_found("equipment")
    try:
        lib.set_review_state(session, equipment_id, state)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.refresh(entry)
    return _view(entry)


@router.post("/review/flags/{flag_id}/resolve", status_code=204)
def resolve_flag(
    flag_id: str, session: Session = Depends(get_db), org: Organisation = Depends(current_org)
) -> None:
    lib.resolve_flag(session, flag_id)


@router.get("/review/changes")
def change_log(
    type_code: str = "",
    limit: int = 100,
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> list[dict[str, object]]:
    """What has changed in the library recently, newest first."""
    return lib.change_log(session, org.id, type_code=type_code, limit=limit)


@router.get("/{equipment_id}/affected")
def affected(
    equipment_id: str,
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> dict[str, object]:
    """Which schedules use this product and would move if it changed.

    Library values are read rather than copied, so editing a product is not a
    local act. This is the blast radius, before doing it.
    """
    entry = session.get(Equipment, equipment_id)
    if entry is None or entry.organisation_id != org.id:
        raise not_found("equipment")
    schedules = lib.affected_schedules(session, org.id, equipment_id)
    return {
        "model_reference": entry.model_reference,
        "type_code": entry.type_code,
        "schedules": schedules,
        "total_rows": sum(s["rows"] for s in schedules),
        "rows_overriding": sum(s["rows_overriding"] for s in schedules),
    }


@router.put("/{equipment_id}", response_model=EquipmentOut)
def update_equipment(
    equipment_id: str,
    payload: EquipmentIn,
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> EquipmentOut:
    entry = session.get(Equipment, equipment_id)
    if entry is None or entry.organisation_id != org.id:
        raise not_found("equipment")
    stype = _type(session, org, entry.type_code)
    allowed = {c.legacy_name for c in stype.library}
    before = dict(entry.values or {})
    # Coerced for the same reason a saved entry is: a duty stored as text
    # reaches the workbook as text and stops being a number there.
    entry.values = {
        k: coerce(v) for k, v in payload.values.items() if k in allowed
    }
    if payload.model_reference.strip():
        entry.model_reference = payload.model_reference.strip()
    session.flush()
    lib.record_change(session, entry, "updated", before=before, actor=payload.created_by)
    return _view(entry)


@router.delete("/{equipment_id}", status_code=204)
def delete_equipment(
    equipment_id: str,
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> None:
    """Reject rather than delete, so a schedule referencing it keeps its record."""
    entry = session.get(Equipment, equipment_id)
    if entry is None or entry.organisation_id != org.id:
        raise not_found("equipment")
    lib.set_review_state(session, equipment_id, "rejected")
