"""What "the columns of this schedule" means, in one place.

A schedule's columns are the catalogue type's, plus anything the project adds on
top, filtered by where they are being shown. Three things need that answer — the
editor grid, the workbook renderer, and the formula validator — and if each
worked it out separately they would disagree the first time somebody added a
project column that a derived formula referenced.
"""

from __future__ import annotations

from typing import Any, Sequence

from ..core.catalogue import Column, ScheduleType
from ..db.models import Project, Schedule
from .converters import type_from_row

__all__ = ["project_extras", "merged_type", "columns_for", "set_project_extras"]


def project_extras(project: Project, type_code: str) -> list[Column]:
    """The extra columns this project adds to one catalogue type."""
    raw = (project.type_extras or {}).get(type_code.upper()) or []
    return [Column.from_dict(c) for c in raw]


def merged_type(
    project: Project, schedule_type: ScheduleType, *, target: str | None = None
) -> ScheduleType:
    """The type as this project sees it, optionally filtered for one target.

    ``target`` is ``editor``, ``xlsx`` or ``pdf``. Omit it to get every column,
    which is what validation and snapshotting want.
    """
    merged = schedule_type.with_extras(project_extras(project, schedule_type.code))
    return merged.visible_columns(target) if target else merged


def columns_for(
    schedule: Schedule, *, target: str | None = None
) -> ScheduleType:
    """The merged type for a schedule, resolved from its own project."""
    return merged_type(
        schedule.building.project, type_from_row(schedule.schedule_type), target=target
    )


def set_project_extras(
    project: Project, type_code: str, columns: Sequence[Column]
) -> None:
    """Replace one type's extra columns on a project.

    Reassigns the whole dict rather than mutating in place: SQLAlchemy does not
    track mutation inside a JSON column, so an in-place edit would not be saved.
    """
    extras = dict(project.type_extras or {})
    code = type_code.upper()
    if columns:
        extras[code] = [
            {**c.to_dict(), "project_extra": True} for c in columns
        ]
    else:
        extras.pop(code, None)
    project.type_extras = extras
