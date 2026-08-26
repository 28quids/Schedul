"""The shared equipment library and its review queue."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db.models import Equipment, Organisation, ScheduleTypeRow
from ...services import library as lib
from ...services.converters import type_from_row
from ..deps import current_org, get_db, not_found
from ..schemas import EquipmentIn, EquipmentOut

router = APIRouter(prefix="/api/library", tags=["library"])


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
    entry.values = {k: v for k, v in payload.values.items() if k in allowed}
    if payload.model_reference.strip():
        entry.model_reference = payload.model_reference.strip()
    session.flush()
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
