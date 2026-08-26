"""The schedule editor: the grid, its rows, and the revision log."""

from __future__ import annotations

import datetime as _dt

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core.revisions import current as current_revision
from ...core.revisions import next_code, sort_key
from ...db.models import Organisation, RevisionRow, Schedule, ScheduleRow
from ...services import projects as svc
from ...services.converters import (
    constant_aliases,
    design_constants_for,
    revisions_of,
    type_from_row,
)
from ...services.grid import build_grid, editable_payload
from ..deps import current_org, get_db, get_schedule, schedule_view
from ...core.references import fill_series
from ..schemas import (
    FillIn, GridColumn, GridOut, PasteIn, RevisionIn, RevisionOut, RowIn, RowOut,
)

router = APIRouter(prefix="/api/schedules", tags=["schedules"])


def _grid(session: Session, schedule: Schedule, org: Organisation) -> GridOut:
    building = schedule.building
    project = building.project
    house = svc.house_standard_for(session, org.id)
    scheme = svc.naming_scheme_for(session, org.id)
    stype = type_from_row(schedule.schedule_type)
    constants = constant_aliases(design_constants_for(project, house))

    grid = build_grid(session, schedule, stype, org.id, constants)

    rows: list[RowOut] = []
    for row, stored in zip(grid.rows, schedule.rows):
        computed = {name: cell.value for name, cell in row.cells.items()}
        problems = {name: cell.problem for name, cell in row.cells.items() if cell.problem}
        rows.append(
            RowOut(
                id=row.id,
                position=row.position,
                values=dict(stored.values or {}),
                computed=computed,
                problems=problems,
            )
        )

    return GridOut(
        schedule=schedule_view(session, schedule, scheme, house),
        columns=[GridColumn(**c) for c in grid.columns],
        rows=rows,
        project_id=project.id,
        project_name=project.number or project.name or "Project",
        building_id=building.id,
        building_ref=building.ref,
        building_count=len(svc.buildings_of(session, project)),
        notes=[*house.general_notes, *stype.notes],
    )


@router.get("/{schedule_id}", response_model=GridOut)
def read_grid(
    schedule: Schedule = Depends(get_schedule),
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> GridOut:
    return _grid(session, schedule, org)


@router.post("/{schedule_id}/rows", response_model=GridOut, status_code=201)
def add_row(
    payload: RowIn,
    schedule: Schedule = Depends(get_schedule),
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> GridOut:
    stype = type_from_row(schedule.schedule_type)
    position = (
        payload.position
        if payload.position is not None
        else (max((r.position for r in schedule.rows), default=-1) + 1)
    )
    session.add(
        ScheduleRow(
            schedule_id=schedule.id,
            position=position,
            values=editable_payload(payload.values, stype),
        )
    )
    session.flush()
    session.expire(schedule, ["rows"])
    return _grid(session, schedule, org)


@router.put("/{schedule_id}/rows/{row_id}", response_model=GridOut)
def update_row(
    row_id: str,
    payload: RowIn,
    schedule: Schedule = Depends(get_schedule),
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> GridOut:
    row = session.get(ScheduleRow, row_id)
    if row is None or row.schedule_id != schedule.id:
        raise HTTPException(status_code=404, detail="no such row")

    stype = type_from_row(schedule.schedule_type)
    # Only input columns are accepted. Library and derived values are computed,
    # so taking them from the client would store a stale value and render it as
    # fact on the export.
    row.values = editable_payload(payload.values, stype)
    if payload.position is not None:
        row.position = payload.position
    session.flush()
    session.expire(schedule, ["rows"])
    return _grid(session, schedule, org)


@router.delete("/{schedule_id}/rows/{row_id}", response_model=GridOut)
def delete_row(
    row_id: str,
    schedule: Schedule = Depends(get_schedule),
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> GridOut:
    row = session.get(ScheduleRow, row_id)
    if row is None or row.schedule_id != schedule.id:
        raise HTTPException(status_code=404, detail="no such row")
    session.delete(row)
    session.flush()
    session.expire(schedule, ["rows"])
    return _grid(session, schedule, org)


def _renumber(rows: list[ScheduleRow]) -> None:
    """Make positions contiguous from zero, in list order."""
    for i, row in enumerate(rows):
        row.position = i


@router.post("/{schedule_id}/rows/{row_id}/duplicate", response_model=GridOut, status_code=201)
def duplicate_row(
    row_id: str,
    schedule: Schedule = Depends(get_schedule),
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> GridOut:
    """Copy a row and insert the copy directly beneath it.

    Schedules are repetitive: most rows differ from the one above in two or
    three fields. Copying the whole row and editing those is far less typing
    than starting blank.
    """
    source = session.get(ScheduleRow, row_id)
    if source is None or source.schedule_id != schedule.id:
        raise HTTPException(status_code=404, detail="no such row")

    ordered = sorted(schedule.rows, key=lambda r: r.position)
    copy = ScheduleRow(
        schedule_id=schedule.id,
        position=source.position + 1,
        values=dict(source.values or {}),
    )
    index = ordered.index(source) + 1
    ordered.insert(index, copy)
    session.add(copy)
    _renumber(ordered)
    session.flush()
    session.expire(schedule, ["rows"])
    return _grid(session, schedule, org)


@router.post("/{schedule_id}/rows/paste", response_model=GridOut)
def paste_rows(
    payload: PasteIn,
    schedule: Schedule = Depends(get_schedule),
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> GridOut:
    """Paste rows, replacing, appending or inserting.

    'replace' is the only destructive mode and the caller has to ask for it by
    name, so a paste can no longer wipe a schedule somebody has filled in.
    """
    stype = type_from_row(schedule.schedule_type)
    incoming = [
        ScheduleRow(
            schedule_id=schedule.id,
            position=0,
            values=editable_payload(item.values, stype),
        )
        for item in payload.rows
    ]

    if payload.mode == "replace":
        for row in list(schedule.rows):
            session.delete(row)
        session.flush()
        ordered = incoming
    else:
        ordered = sorted(schedule.rows, key=lambda r: r.position)
        at = len(ordered) if payload.mode == "append" else max(0, min(payload.position, len(ordered)))
        ordered[at:at] = incoming

    for row in incoming:
        session.add(row)
    _renumber(ordered)
    session.flush()
    session.expire(schedule, ["rows"])
    return _grid(session, schedule, org)


@router.post("/{schedule_id}/rows/bulk", response_model=GridOut, deprecated=True)
def replace_rows(
    payload: list[RowIn],
    schedule: Schedule = Depends(get_schedule),
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> GridOut:
    """Replace every row at once. Superseded by /rows/paste with mode=replace."""
    return paste_rows(
        PasteIn(mode="replace", rows=payload), schedule, session, org
    )


@router.post("/{schedule_id}/rows/fill", response_model=GridOut)
def fill_down(
    payload: FillIn,
    schedule: Schedule = Depends(get_schedule),
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> GridOut:
    """Fill one column down from a row, counting up where the value ends in digits.

    The increment rule lives in core.references so the grid, an importer and a
    bulk-add all produce the same thing.
    """
    stype = type_from_row(schedule.schedule_type)
    column = stype.column(payload.column)
    key = column.legacy_name if column is not None else payload.column
    editable = {c.legacy_name for c in stype.inputs} | {"Model Reference"}
    if key not in editable:
        raise HTTPException(
            status_code=400,
            detail=f"{payload.column!r} is calculated or looked up, so it cannot be filled",
        )

    ordered = sorted(schedule.rows, key=lambda r: r.position)
    start = next((i for i, r in enumerate(ordered) if r.position == payload.start_position), None)
    if start is None:
        raise HTTPException(status_code=404, detail="no row at that position")

    below = ordered[start + 1 :]
    if payload.count is not None:
        below = below[: max(0, payload.count)]
    if not below:
        return _grid(session, schedule, org)

    seed = (ordered[start].values or {}).get(key, "")
    for row, value in zip(below, fill_series(seed, len(below), mode=payload.mode)):
        row.values = {**(row.values or {}), key: value}

    session.flush()
    session.expire(schedule, ["rows"])
    return _grid(session, schedule, org)


# -------------------------------------------------------------- revisions ---


def _revision_views(schedule: Schedule) -> list[RevisionOut]:
    log = revisions_of(schedule)
    latest = current_revision(log)
    latest_code = latest.code if latest else None
    return [
        RevisionOut(
            id=r.id,
            position=r.position,
            code=r.code,
            status=r.status,
            issue_date=r.issue_date,
            prepared_by=r.prepared_by,
            checked_by=r.checked_by,
            approved_by=r.approved_by,
            description=r.description,
            sort_key=r.sort_key,
            # Ranked by series then number, so a published C-revision is current
            # even when a preliminary row was entered after it.
            is_current=bool(latest_code) and r.code == latest_code,
        )
        for r in schedule.revisions
    ]


@router.get("/{schedule_id}/revisions", response_model=list[RevisionOut])
def list_revisions(schedule: Schedule = Depends(get_schedule)) -> list[RevisionOut]:
    return _revision_views(schedule)


@router.get("/{schedule_id}/revisions/next")
def suggest_revision(
    published: bool = False, schedule: Schedule = Depends(get_schedule)
) -> dict[str, str]:
    return {"code": next_code(revisions_of(schedule), published=published)}


@router.post("/{schedule_id}/revisions", response_model=list[RevisionOut], status_code=201)
def add_revision(
    payload: RevisionIn,
    schedule: Schedule = Depends(get_schedule),
    session: Session = Depends(get_db),
) -> list[RevisionOut]:
    code = payload.code or next_code(revisions_of(schedule))
    session.add(
        RevisionRow(
            schedule_id=schedule.id,
            position=max((r.position for r in schedule.revisions), default=-1) + 1,
            code=code,
            status=payload.status,
            issue_date=payload.issue_date or _dt.date.today(),
            prepared_by=payload.prepared_by,
            checked_by=payload.checked_by,
            approved_by=payload.approved_by,
            description=payload.description,
            sort_key=sort_key(code),
        )
    )
    session.flush()
    session.expire(schedule, ["revisions"])
    return _revision_views(schedule)


@router.put("/{schedule_id}/revisions/{revision_id}", response_model=list[RevisionOut])
def update_revision(
    revision_id: str,
    payload: RevisionIn,
    schedule: Schedule = Depends(get_schedule),
    session: Session = Depends(get_db),
) -> list[RevisionOut]:
    revision = session.get(RevisionRow, revision_id)
    if revision is None or revision.schedule_id != schedule.id:
        raise HTTPException(status_code=404, detail="no such revision")
    revision.code = payload.code
    revision.status = payload.status
    revision.issue_date = payload.issue_date
    revision.prepared_by = payload.prepared_by
    revision.checked_by = payload.checked_by
    revision.approved_by = payload.approved_by
    revision.description = payload.description
    revision.sort_key = sort_key(payload.code)
    session.flush()
    session.expire(schedule, ["revisions"])
    return _revision_views(schedule)


@router.delete("/{schedule_id}/revisions/{revision_id}", response_model=list[RevisionOut])
def delete_revision(
    revision_id: str,
    schedule: Schedule = Depends(get_schedule),
    session: Session = Depends(get_db),
) -> list[RevisionOut]:
    revision = session.get(RevisionRow, revision_id)
    if revision is None or revision.schedule_id != schedule.id:
        raise HTTPException(status_code=404, detail="no such revision")
    session.delete(revision)
    session.flush()
    session.expire(schedule, ["revisions"])
    return _revision_views(schedule)
