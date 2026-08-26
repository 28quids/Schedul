"""The catalogue and the schedule-type designer."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core.catalogue import Column, ScheduleType, validate_type
from ...core.formula import ALLOWED_FUNCTIONS, BANNED_FUNCTIONS, CONSTANTS
from ...db.models import Organisation, Schedule, ScheduleTypeRow
from ...services import projects as svc
from ...services.converters import type_from_row, type_to_row_fields
from ..deps import current_org, get_db, not_found
from ..schemas import TypeIn, TypeOut, TypeSummary

router = APIRouter(prefix="/api/catalogue", tags=["catalogue"])


def _row(session: Session, org: Organisation, type_id: str) -> ScheduleTypeRow:
    row = session.get(ScheduleTypeRow, type_id)
    if row is None or row.organisation_id != org.id:
        raise not_found("schedule type")
    return row


def _summary(row: ScheduleTypeRow, volume_label: str = "") -> TypeSummary:
    return TypeSummary(
        id=row.id,
        code=row.code,
        title=row.title,
        short=row.short or "",
        version=row.version,
        volume=row.volume or "",
        volume_label=volume_label,
        column_count=len(row.columns or []),
        updated_at=row.updated_at,
    )


def _detail(session: Session, org: Organisation, row: ScheduleTypeRow) -> TypeOut:
    house = svc.house_standard_for(session, org.id)
    st = type_from_row(row)
    others = [
        t.code
        for t in session.scalars(
            select(ScheduleTypeRow).where(
                ScheduleTypeRow.organisation_id == org.id, ScheduleTypeRow.id != row.id
            )
        )
    ]
    issues = validate_type(st, other_codes=others)
    return TypeOut(
        **_summary(row, house.volume_label(row.volume or "")).model_dump(),
        columns=[c.to_dict() for c in st.columns],
        notes=list(st.notes),
        history=list(st.history),
        # Shown greyed above the type's own notes in the designer, so the author
        # can see the full rendered block and does not duplicate generic wording.
        project_notes=list(house.general_notes),
        issues=[
            {"severity": i.severity, "message": i.message, "column": i.column or ""}
            for i in issues
        ],
    )


@router.get("/meta")
def formula_meta(
    session: Session = Depends(get_db), org: Organisation = Depends(current_org)
) -> dict[str, object]:
    """What the designer needs to validate and to offer choices."""
    house = svc.house_standard_for(session, org.id)
    return {
        "constants": [
            {"alias": alias, "name": name} for alias, name in CONSTANTS.items()
        ],
        "allowed_functions": sorted(ALLOWED_FUNCTIONS),
        "banned_functions": sorted(BANNED_FUNCTIONS),
        "volume_lookup": house.volume_lookup,
        "status_codes": [list(p) for p in house.status_codes],
        "kinds": [
            {
                "kind": "input",
                "label": "Input",
                "hint": "the user types it, and it differs per unit",
            },
            {
                "kind": "library",
                "label": "From library",
                "hint": "looked up from the equipment library on Model Reference",
            },
            {
                "kind": "derived",
                "label": "Derived",
                "hint": "calculated by formula, read-only",
            },
        ],
    }


@router.get("", response_model=list[TypeSummary])
def list_types(
    session: Session = Depends(get_db), org: Organisation = Depends(current_org)
) -> list[TypeSummary]:
    house = svc.house_standard_for(session, org.id)
    rows = session.scalars(
        select(ScheduleTypeRow)
        .where(
            ScheduleTypeRow.organisation_id == org.id,
            ScheduleTypeRow.archived == False,  # noqa: E712
        )
        .order_by(ScheduleTypeRow.code)
    )
    return [_summary(r, house.volume_label(r.volume or "")) for r in rows]


@router.get("/{type_id}", response_model=TypeOut)
def read_type(
    type_id: str,
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> TypeOut:
    return _detail(session, org, _row(session, org, type_id))


@router.post("/validate")
def validate_draft(
    payload: TypeIn,
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> dict[str, object]:
    """Validate a draft without saving it, for the designer's live feedback."""
    draft = ScheduleType(
        code=payload.code,
        title=payload.title,
        short=payload.short,
        volume=payload.volume,
        columns=[Column(**c.model_dump()) for c in payload.columns],
        notes=payload.notes,
    )
    others = [
        t.code
        for t in session.scalars(
            select(ScheduleTypeRow).where(
                ScheduleTypeRow.organisation_id == org.id,
                ScheduleTypeRow.code != draft.code,
            )
        )
    ]
    issues = validate_type(draft, other_codes=others)
    return {
        "issues": [
            {"severity": i.severity, "message": i.message, "column": i.column or ""}
            for i in issues
        ],
        "ok": not [i for i in issues if i.severity == "error"],
        "preview": {
            "headers": [c.name for c in draft.layout()],
            "units": [
                __import__(
                    "schedul.core.units", fromlist=["pretty_unit"]
                ).pretty_unit(c.unit)
                for c in draft.layout()
            ],
            "kinds": [c.kind for c in draft.layout()],
            "examples": [c.example for c in draft.layout()],
        },
    }


@router.post("", response_model=TypeOut, status_code=201)
def create_type(
    payload: TypeIn,
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> TypeOut:
    draft = ScheduleType(
        code=payload.code,
        title=payload.title,
        short=payload.short,
        volume=payload.volume,
        columns=[Column(**c.model_dump()) for c in payload.columns],
        notes=payload.notes,
    )
    others = [
        t.code
        for t in session.scalars(
            select(ScheduleTypeRow).where(ScheduleTypeRow.organisation_id == org.id)
        )
    ]
    errors = [
        i.message for i in validate_type(draft, other_codes=others) if i.severity == "error"
    ]
    if errors:
        raise HTTPException(status_code=400, detail=errors)

    row = ScheduleTypeRow(organisation_id=org.id, **type_to_row_fields(draft))
    session.add(row)
    session.flush()
    return _detail(session, org, row)


@router.put("/{type_id}", response_model=TypeOut)
def update_type(
    type_id: str,
    payload: TypeIn,
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> TypeOut:
    """Save a change, bumping the version when the columns actually moved.

    A project pins the version it was built against, so editing a type never
    silently invalidates a schedule already issued from it.
    """
    row = _row(session, org, type_id)
    draft = ScheduleType(
        code=payload.code,
        title=payload.title,
        short=payload.short,
        version=row.version,
        volume=payload.volume,
        columns=[Column(**c.model_dump()) for c in payload.columns],
        notes=payload.notes,
        history=list(row.history or []),
    )
    others = [
        t.code
        for t in session.scalars(
            select(ScheduleTypeRow).where(
                ScheduleTypeRow.organisation_id == org.id, ScheduleTypeRow.id != row.id
            )
        )
    ]
    errors = [
        i.message for i in validate_type(draft, other_codes=others) if i.severity == "error"
    ]
    if errors:
        raise HTTPException(status_code=400, detail=errors)

    columns_changed = [c.to_dict() for c in draft.columns] != (row.columns or [])
    if columns_changed:
        draft.bump(payload.change or "columns edited")

    for key, value in type_to_row_fields(draft).items():
        setattr(row, key, value)
    session.flush()
    return _detail(session, org, row)


@router.get("/{type_id}/usage")
def type_usage(
    type_id: str,
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> dict[str, object]:
    """Which projects use this type, and at which version.

    Saving a change to an in-use type warns which projects are on older
    versions, so the author can see the blast radius first.
    """
    row = _row(session, org, type_id)
    schedules = session.scalars(
        select(Schedule).where(
            Schedule.schedule_type_id == row.id, Schedule.deleted_marker == ""
        )
    )
    out = []
    for s in schedules:
        building = s.building
        out.append(
            {
                "schedule_id": s.id,
                "project_id": building.project.id,
                "project": building.project.name or building.project.number,
                "building": building.label,
                "pinned_version": s.type_version,
                "current_version": row.version,
                "stale": s.type_version < row.version,
            }
        )
    return {"code": row.code, "version": row.version, "used_by": out}


@router.delete("/{type_id}", response_model=list[TypeSummary])
def archive_type(
    type_id: str,
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> list[TypeSummary]:
    """Retire a type from the catalogue. Schedules built from it are untouched."""
    row = _row(session, org, type_id)
    row.archived = True
    session.flush()
    return list_types(session, org)
