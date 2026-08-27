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
from ...services.columns import columns_for
from ...services.issue import diff_snapshots, issue_revision
from ...services import history as hist
from ...services import notes as notes_svc
from ...services.grid import build_grid, editable_payload, override_payload
from ..deps import current_org, get_db, get_schedule, schedule_view
from ...core.references import fill_series
from ...core import tabular
from ..schemas import (
    CellsIn, DeleteRowsIn, FillIn, GridColumn, GridOut, PasteIn, PastePreviewIn,
    RevisionIn, RevisionOut, RowIn, RowOut, ScheduleNotesIn,
)

router = APIRouter(prefix="/api/schedules", tags=["schedules"])


def _grid(session: Session, schedule: Schedule, org: Organisation) -> GridOut:
    building = schedule.building
    project = building.project
    house = svc.house_standard_for(session, org.id)
    scheme = svc.naming_scheme_for(session, org.id)
    # The project's extra columns are part of this schedule's shape, and the
    # editor sees every column the practice has not hidden from it.
    stype = columns_for(schedule, target="editor")
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
                overrides=dict(stored.overrides or {}),
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
        **{
            k: v for k, v in notes_svc.notes_view(schedule, stype, house, project).items()
            if k in ("notes", "note_layers", "notes_customised")
        },
        history=hist.history_state(session, schedule.id),
        type_drift=_type_drift(schedule),
    )


def _type_drift(schedule: Schedule) -> dict[str, object]:
    """Whether the catalogue type has moved on since this schedule was built.

    The columns themselves are always the type's current ones -- that is what
    makes a width or an order change in the designer show up here -- so this is
    not a warning that the schedule is stale. It is the schedule saying which
    version it was set up against, so somebody who finds a new column can see
    where it came from.
    """
    type_row = schedule.schedule_type
    if type_row is None or type_row.version <= schedule.type_version:
        return {}
    history = [
        h for h in (type_row.history or [])
        if int(h.get("version", 0)) >= schedule.type_version
    ]
    return {
        "built_against": schedule.type_version,
        "current": type_row.version,
        "changes": [
            {"version": h.get("version"), "date": h.get("date", ""), "change": h.get("change", "")}
            for h in history[-6:]
        ],
    }


@router.get("/{schedule_id}", response_model=GridOut)
def read_grid(
    schedule: Schedule = Depends(get_schedule),
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> GridOut:
    return _grid(session, schedule, org)


def _editable_names(stype) -> list[str]:
    """The columns a user may type into, in the order they sit on the sheet."""
    from ...core.catalogue import MODEL_REFERENCE

    return [*(c.legacy_name for c in stype.inputs), MODEL_REFERENCE]


def _row_dicts(schedule: Schedule) -> list[dict]:
    return [
        dict(r.values or {}) for r in sorted(schedule.rows, key=lambda r: r.position)
    ]


@router.post("/{schedule_id}/rows", response_model=GridOut, status_code=201)
def add_row(
    payload: RowIn,
    schedule: Schedule = Depends(get_schedule),
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> GridOut:
    stype = columns_for(schedule)
    before = hist.snapshot_rows(schedule)
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
            overrides=override_payload(payload.overrides or {}, stype),
        )
    )
    session.flush()
    session.expire(schedule, ["rows"])
    hist.record_edit(session, schedule, "add_row", before, summary="added a row")
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

    stype = columns_for(schedule)
    before = hist.snapshot_rows(schedule)
    had_overrides = dict(row.overrides or {})

    # Only input columns are accepted here. Library and derived values are
    # computed, so taking them from the client would store a stale value and
    # render it as fact on the export. A deliberate divergence goes in
    # 'overrides', which is unambiguous.
    row.values = editable_payload(payload.values, stype)
    if payload.overrides is not None:
        row.overrides = override_payload(payload.overrides, stype)
    if payload.position is not None:
        row.position = payload.position
    session.flush()
    session.expire(schedule, ["rows"])

    # Typing is saved keystroke by keystroke and is undone by the browser's own
    # undo; it does not belong on the stack. An override appearing or being
    # cleared is a decision about where a value comes from, and does.
    if (row.overrides or {}) != had_overrides:
        hist.record_edit(
            session, schedule, "overrides", before, summary="override change"
        )
    return _grid(session, schedule, org)


@router.post("/{schedule_id}/rows/cells", response_model=GridOut)
def edit_cells(
    payload: CellsIn,
    schedule: Schedule = Depends(get_schedule),
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> GridOut:
    """Write a block of cells in one undoable step.

    This is what a multi-cell paste and a range delete both come down to:
    several rows, a few columns each, applied together so undo takes the whole
    block back rather than one cell at a time. Values are merged into each row,
    so columns the block does not name are left alone.
    """
    stype = columns_for(schedule)
    rows = {r.id: r for r in schedule.rows}
    before = hist.snapshot_rows(schedule)

    for edit in payload.edits:
        row = rows.get(edit.row_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"no row {edit.row_id!r} on this schedule")
        if edit.values:
            row.values = {**(row.values or {}), **editable_payload(edit.values, stype)}
        if edit.overrides is not None:
            # An empty value clears the override and restores the library value,
            # which is the same contract update_row uses.
            merged = {**(row.overrides or {}), **edit.overrides}
            row.overrides = override_payload(merged, stype)

    session.flush()
    session.expire(schedule, ["rows"])
    hist.record_edit(
        session, schedule, payload.action, before,
        summary=f"{len(payload.edits)} row(s) edited",
    )
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
    before = hist.snapshot_rows(schedule)
    session.delete(row)
    session.flush()
    session.expire(schedule, ["rows"])
    _renumber(sorted(schedule.rows, key=lambda r: r.position))
    session.flush()
    hist.record_edit(session, schedule, "delete_rows", before, summary="deleted a row")
    return _grid(session, schedule, org)


@router.post("/{schedule_id}/rows/delete", response_model=GridOut)
def delete_rows(
    payload: DeleteRowsIn,
    schedule: Schedule = Depends(get_schedule),
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> GridOut:
    """Delete a selection of rows as one undoable step."""
    wanted = set(payload.row_ids)
    rows = [r for r in schedule.rows if r.id in wanted]
    missing = wanted - {r.id for r in rows}
    if missing:
        raise HTTPException(
            status_code=404,
            detail=f"{len(missing)} of the rows are not on this schedule",
        )
    if not rows:
        return _grid(session, schedule, org)

    before = hist.snapshot_rows(schedule)
    for row in rows:
        session.delete(row)
    session.flush()
    session.expire(schedule, ["rows"])
    _renumber(sorted(schedule.rows, key=lambda r: r.position))
    session.flush()
    hist.record_edit(
        session, schedule, "delete_rows", before,
        summary=f"deleted {len(rows)} row(s)",
    )
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

    before = hist.snapshot_rows(schedule)
    ordered = sorted(schedule.rows, key=lambda r: r.position)
    copy = ScheduleRow(
        schedule_id=schedule.id,
        position=source.position + 1,
        values=dict(source.values or {}),
        overrides=dict(source.overrides or {}),
    )
    index = ordered.index(source) + 1
    ordered.insert(index, copy)
    session.add(copy)
    _renumber(ordered)
    session.flush()
    session.expire(schedule, ["rows"])
    hist.record_edit(
        session, schedule, "duplicate_row", before, summary="duplicated a row"
    )
    return _grid(session, schedule, org)


def _paste_plan(schedule: Schedule, stype, *, mode: str, text: str, header, position: int):
    return tabular.plan_paste(
        text,
        mode=mode,
        column_names=_editable_names(stype),
        existing=_row_dicts(schedule),
        position=position,
        header=header,
    )


@router.post("/{schedule_id}/rows/paste/preview")
def preview_paste(
    payload: PastePreviewIn,
    schedule: Schedule = Depends(get_schedule),
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> dict[str, object]:
    """What this paste would do, without doing any of it.

    The dry run is the whole reason paste is safe now: the same planner that
    produces this is the one the apply uses, so the numbers a user confirms are
    the numbers that happen.
    """
    stype = columns_for(schedule)
    plan = _paste_plan(
        schedule, stype,
        mode=payload.mode, text=payload.text,
        header=payload.header, position=payload.position,
    )
    return {**plan.to_dict(), "editable_columns": _editable_names(stype)}


@router.post("/{schedule_id}/rows/paste", response_model=GridOut)
def paste_rows(
    payload: PasteIn,
    schedule: Schedule = Depends(get_schedule),
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> GridOut:
    """Paste rows, appending, inserting or replacing.

    Appending is the default, 'replace' is the only mode that removes anything,
    and it is refused unless the caller confirms it -- so a paste can neither
    wipe a filled-in schedule by accident nor do it without being asked twice.
    """
    stype = columns_for(schedule)

    if payload.text.strip():
        plan = _paste_plan(
            schedule, stype,
            mode=payload.mode, text=payload.text,
            header=payload.header, position=payload.position,
        )
        parsed = [RowIn(values=values) for values in plan.rows]
    else:
        plan = None
        parsed = list(payload.rows)

    if payload.mode == "replace" and not payload.confirm:
        populated = (
            plan.populated_removed
            if plan is not None
            else sum(1 for r in _row_dicts(schedule) if any(v not in (None, "") for v in r.values()))
        )
        if populated:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"replacing every row would remove {populated} row(s) that have been "
                    f"filled in. Confirm the preview to go ahead, or append instead."
                ),
            )

    before = hist.snapshot_rows(schedule)
    incoming = [
        ScheduleRow(
            schedule_id=schedule.id,
            position=0,
            values=editable_payload(item.values, stype),
            overrides=override_payload(item.overrides or {}, stype),
        )
        for item in parsed
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
    hist.record_edit(
        session, schedule, "paste", before,
        summary=f"pasted {len(incoming)} row(s)",
    )
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
        PasteIn(mode="replace", rows=payload, confirm=True), schedule, session, org
    )


@router.post("/{schedule_id}/rows/fill", response_model=GridOut)
def fill_down(
    payload: FillIn,
    schedule: Schedule = Depends(get_schedule),
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> GridOut:
    """Fill one or more columns down from a row, counting up where a value ends
    in digits.

    The increment rule lives in core.references so the grid, an importer and a
    bulk-add all produce the same thing. ``count`` bounds the fill to a selected
    range rather than running to the end of the schedule.
    """
    stype = columns_for(schedule)
    editable = set(_editable_names(stype))

    keys: list[str] = []
    for name in payload.target_columns:
        column = stype.column(name)
        key = column.legacy_name if column is not None else name
        if key not in editable:
            raise HTTPException(
                status_code=400,
                detail=f"{name!r} is calculated or looked up, so it cannot be filled",
            )
        keys.append(key)
    if not keys:
        raise HTTPException(status_code=400, detail="no column to fill was given")

    ordered = sorted(schedule.rows, key=lambda r: r.position)
    start = next((i for i, r in enumerate(ordered) if r.position == payload.start_position), None)
    if start is None:
        raise HTTPException(status_code=404, detail="no row at that position")

    below = ordered[start + 1 :]
    if payload.count is not None:
        below = below[: max(0, payload.count)]
    if not below:
        return _grid(session, schedule, org)

    before = hist.snapshot_rows(schedule)
    for key in keys:
        seed = (ordered[start].values or {}).get(key, "")
        for row, value in zip(below, fill_series(seed, len(below), mode=payload.mode)):
            row.values = {**(row.values or {}), key: value}

    session.flush()
    session.expire(schedule, ["rows"])
    hist.record_edit(
        session, schedule, "fill", before,
        summary=f"filled {len(below)} row(s)",
    )
    return _grid(session, schedule, org)


# ------------------------------------------------------------------ undo ---


@router.post("/{schedule_id}/undo", response_model=GridOut)
def undo_edit(
    schedule: Schedule = Depends(get_schedule),
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> GridOut:
    """Step back one recorded edit."""
    try:
        hist.undo(session, schedule)
    except hist.UndoError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _grid(session, schedule, org)


@router.post("/{schedule_id}/redo", response_model=GridOut)
def redo_edit(
    schedule: Schedule = Depends(get_schedule),
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> GridOut:
    """Step forward again after an undo."""
    try:
        hist.redo(session, schedule)
    except hist.UndoError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _grid(session, schedule, org)


# ----------------------------------------------------------------- notes ---


def _notes_payload(session: Session, schedule: Schedule, org: Organisation) -> dict[str, object]:
    house = svc.house_standard_for(session, org.id)
    stype = columns_for(schedule)
    return notes_svc.notes_view(schedule, stype, house)


@router.get("/{schedule_id}/notes")
def read_notes(
    schedule: Schedule = Depends(get_schedule),
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> dict[str, object]:
    """The notes that print here, each layer of them, and what they resolve to."""
    return _notes_payload(session, schedule, org)


@router.put("/{schedule_id}/notes")
def write_notes(
    payload: ScheduleNotesIn,
    schedule: Schedule = Depends(get_schedule),
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> dict[str, object]:
    """Give this schedule its own notes, or hand it back to the layers.

    ``notes: null`` reverts. It is the only way back, and it is deliberately the
    same shape as never having diverged, so there is no third state to reason
    about.
    """
    notes_svc.set_schedule_notes(schedule, payload.notes)
    session.flush()
    return _notes_payload(session, schedule, org)


@router.post("/{schedule_id}/notes/customise")
def customise_notes(
    schedule: Schedule = Depends(get_schedule),
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> dict[str, object]:
    """Take the notes over, starting from what they say now.

    Diverging starts from the resolved wording rather than from nothing: the
    point is almost always to change one line.
    """
    house = svc.house_standard_for(session, org.id)
    stype = columns_for(schedule)
    if schedule.notes is None:
        schedule.notes = notes_svc.starting_point(schedule, stype, house)
        session.flush()
    return _notes_payload(session, schedule, org)


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
            issued=r.snapshot is not None,
            issued_at=r.issued_at,
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


@router.post(
    "/{schedule_id}/revisions/{revision_id}/issue", response_model=list[RevisionOut]
)
def issue(
    revision_id: str,
    schedule: Schedule = Depends(get_schedule),
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> list[RevisionOut]:
    """Freeze this revision: record what the schedule says right now.

    From here the revision renders from its snapshot, so a later library
    correction or formula fix cannot change what an issued document said.
    """
    revision = session.get(RevisionRow, revision_id)
    if revision is None or revision.schedule_id != schedule.id:
        raise HTTPException(status_code=404, detail="no such revision")
    issue_revision(session, schedule, revision, org)
    session.expire(schedule, ["revisions"])
    return _revision_views(schedule)


@router.get("/{schedule_id}/revisions/{revision_id}/snapshot")
def read_snapshot(
    revision_id: str,
    schedule: Schedule = Depends(get_schedule),
    session: Session = Depends(get_db),
) -> dict[str, object]:
    """What the schedule looked like when this revision was issued."""
    revision = session.get(RevisionRow, revision_id)
    if revision is None or revision.schedule_id != schedule.id:
        raise HTTPException(status_code=404, detail="no such revision")
    if revision.snapshot is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"revision {revision.code} has not been issued, so there is no frozen "
                f"copy of it. It reflects the schedule as it stands now."
            ),
        )
    return {"code": revision.code, "issued_at": revision.issued_at, **revision.snapshot}


@router.get("/{schedule_id}/revisions/{revision_id}/diff")
def compare_revisions(
    revision_id: str,
    against: str | None = None,
    schedule: Schedule = Depends(get_schedule),
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> dict[str, object]:
    """Compare an issued revision with another, or with the schedule as it stands.

    Comparing the computed values rather than the typed ones is deliberate: a
    duty that moved because the library was corrected is a real change to
    whoever reads the document, even though nobody retyped anything.
    """
    from ...services.issue import take_snapshot

    revision = session.get(RevisionRow, revision_id)
    if revision is None or revision.schedule_id != schedule.id:
        raise HTTPException(status_code=404, detail="no such revision")
    if revision.snapshot is None:
        raise HTTPException(
            status_code=400,
            detail=f"revision {revision.code} was never issued, so there is nothing to compare",
        )

    if against:
        other = session.get(RevisionRow, against)
        if other is None or other.schedule_id != schedule.id:
            raise HTTPException(status_code=404, detail="no such revision to compare with")
        if other.snapshot is None:
            raise HTTPException(
                status_code=400,
                detail=f"revision {other.code} was never issued",
            )
        later, earlier = (
            (other, revision) if other.sort_key >= revision.sort_key else (revision, other)
        )
        return {
            "from": earlier.code, "to": later.code,
            **diff_snapshots(earlier.snapshot, later.snapshot),
        }

    return {
        "from": revision.code, "to": "now (unissued)",
        **diff_snapshots(revision.snapshot, take_snapshot(session, schedule, org)),
    }


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
    if revision.snapshot is not None and payload.code != revision.code:
        raise HTTPException(
            status_code=409,
            detail=(
                f"revision {revision.code} has been issued, so its code is fixed. "
                f"Everything else about it can still be corrected."
            ),
        )
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
    if revision.snapshot is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"revision {revision.code} has been issued. Deleting it would remove the "
                f"only record of what that document said; supersede it with a new "
                f"revision instead."
            ),
        )
    session.delete(revision)
    session.flush()
    session.expire(schedule, ["revisions"])
    return _revision_views(schedule)
