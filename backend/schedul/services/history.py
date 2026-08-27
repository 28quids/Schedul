"""Undo and redo for the grid's risky operations.

Paste, delete, duplicate, fill and a bulk override change all rewrite several
rows at once, and until now the only way back from one was retyping. That is the
gap SPEC.md's safety rule leaves: previewing a destructive action helps, but a
user who confirms it and then sees it was wrong still needs a way out.

**An edit records the rows before and after it, and undo restores the before.**
Not an inverse operation per action: that has to be derived separately for every
action, and the one that is subtly wrong is the one that loses somebody's work.
A schedule is tens of rows of small JSON, so a whole-schedule snapshot is
cheaper than being clever.

The stack is per schedule and bounded. Recording a new edit discards anything
that had been undone, which is what every spreadsheet does and what stops the
history branching into something nobody can reason about.
"""

from __future__ import annotations

from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import Schedule, ScheduleEdit, ScheduleRow

__all__ = [
    "HISTORY_LIMIT",
    "snapshot_rows",
    "record_edit",
    "undo",
    "redo",
    "history_state",
    "UndoError",
]

#: How many edits per schedule are kept. Twenty covers a working session's worth
#: of mistakes; keeping them forever would make the table the biggest thing in
#: the database for no benefit anybody has asked for.
HISTORY_LIMIT = 20

#: What each action is called when the UI offers to undo it.
LABELS = {
    "paste": "paste",
    "delete_rows": "row deletion",
    "duplicate_row": "row duplication",
    "fill": "fill",
    "cells": "cell edit",
    "add_row": "added row",
    "clear_cells": "clearing cells",
    "overrides": "override change",
}


class UndoError(Exception):
    """There is nothing to undo or redo."""


def snapshot_rows(schedule: Schedule) -> list[dict[str, Any]]:
    """The schedule's rows as plain data, ordered by position."""
    return [
        {
            "id": row.id,
            "position": row.position,
            "values": dict(row.values or {}),
            "overrides": dict(row.overrides or {}),
        }
        for row in sorted(schedule.rows, key=lambda r: r.position)
    ]


def record_edit(
    session: Session,
    schedule: Schedule,
    action: str,
    before: Sequence[dict[str, Any]],
    after: Sequence[dict[str, Any]] | None = None,
    *,
    summary: str = "",
) -> ScheduleEdit | None:
    """Record one undoable edit. Returns None when nothing actually changed.

    ``after`` defaults to the schedule's current state, so the usual call is
    made after the change has been applied.
    """
    after_rows = list(after) if after is not None else snapshot_rows(schedule)
    before_rows = list(before)
    if before_rows == after_rows:
        return None

    # A new edit invalidates the redo stack: the future it led to no longer
    # exists once history has taken a different branch.
    for stale in session.scalars(
        select(ScheduleEdit).where(
            ScheduleEdit.schedule_id == schedule.id, ScheduleEdit.undone == True  # noqa: E712
        )
    ):
        session.delete(stale)
    session.flush()

    top = session.scalar(
        select(ScheduleEdit.seq)
        .where(ScheduleEdit.schedule_id == schedule.id)
        .order_by(ScheduleEdit.seq.desc())
        .limit(1)
    )
    entry = ScheduleEdit(
        schedule_id=schedule.id,
        seq=(top or 0) + 1,
        action=action,
        summary=summary or LABELS.get(action, action),
        before=before_rows,
        after=after_rows,
    )
    session.add(entry)
    session.flush()
    _trim(session, schedule.id)
    return entry


def _trim(session: Session, schedule_id: str) -> None:
    entries = list(
        session.scalars(
            select(ScheduleEdit)
            .where(ScheduleEdit.schedule_id == schedule_id)
            .order_by(ScheduleEdit.seq.desc())
        )
    )
    for old in entries[HISTORY_LIMIT:]:
        session.delete(old)
    session.flush()


def _restore(session: Session, schedule: Schedule, rows: Sequence[dict[str, Any]]) -> None:
    """Put the schedule's rows back to a recorded state.

    Row ids are preserved where the snapshot has them, so anything holding a row
    id -- the grid, a comparison -- still points at the same line afterwards.
    """
    existing = {row.id: row for row in schedule.rows}
    keep: set[str] = set()

    for index, snapshot in enumerate(rows):
        row_id = snapshot.get("id")
        row = existing.get(row_id) if row_id else None
        if row is None:
            # A row the edit had deleted comes back under its own id, so
            # anything still pointing at it lines up again.
            row = ScheduleRow(schedule_id=schedule.id, position=index)
            if row_id:
                row.id = row_id
            session.add(row)
        else:
            keep.add(row_id)
        row.position = int(snapshot.get("position", index))
        row.values = dict(snapshot.get("values") or {})
        row.overrides = dict(snapshot.get("overrides") or {})

    for row_id, row in existing.items():
        if row_id not in keep:
            session.delete(row)

    session.flush()
    session.expire(schedule, ["rows"])


def undo(session: Session, schedule: Schedule) -> ScheduleEdit:
    """Step back one edit. Raises :class:`UndoError` when there is nothing to undo."""
    entry = session.scalar(
        select(ScheduleEdit)
        .where(
            ScheduleEdit.schedule_id == schedule.id,
            ScheduleEdit.undone == False,  # noqa: E712
        )
        .order_by(ScheduleEdit.seq.desc())
        .limit(1)
    )
    if entry is None:
        raise UndoError("there is nothing to undo on this schedule")
    _restore(session, schedule, entry.before)
    entry.undone = True
    session.flush()
    return entry


def redo(session: Session, schedule: Schedule) -> ScheduleEdit:
    """Step forward again after an undo."""
    entry = session.scalar(
        select(ScheduleEdit)
        .where(
            ScheduleEdit.schedule_id == schedule.id,
            ScheduleEdit.undone == True,  # noqa: E712
        )
        .order_by(ScheduleEdit.seq.asc())
        .limit(1)
    )
    if entry is None:
        raise UndoError("there is nothing to redo on this schedule")
    _restore(session, schedule, entry.after)
    entry.undone = False
    session.flush()
    return entry


def history_state(session: Session, schedule_id: str) -> dict[str, Any]:
    """What the toolbar needs: whether undo and redo are available, and of what."""
    entries = list(
        session.scalars(
            select(ScheduleEdit)
            .where(ScheduleEdit.schedule_id == schedule_id)
            .order_by(ScheduleEdit.seq.desc())
        )
    )
    pending = [e for e in entries if not e.undone]
    undone = [e for e in entries if e.undone]
    return {
        "can_undo": bool(pending),
        "can_redo": bool(undone),
        "undo_label": pending[0].summary if pending else "",
        "redo_label": undone[-1].summary if undone else "",
        "depth": len(pending),
    }
