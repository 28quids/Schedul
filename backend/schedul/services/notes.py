"""Resolving a schedule's notes from the layers above it.

The rule lives in ``core.notes``; this is the part that knows where each layer
is stored — the house standard, the project, the catalogue type, the schedule
itself — so that everything which renders notes asks one function and cannot
drift from the others.
"""

from __future__ import annotations

from typing import Any, Sequence

from sqlalchemy.orm import Session

from ..core.catalogue import ScheduleType
from ..core.house import HouseStandard
from ..core.notes import ResolvedNote, note_texts, resolve_notes, seed_from
from ..db.models import Project, Schedule

__all__ = ["resolved_notes", "notes_view", "set_schedule_notes", "revert_schedule_notes"]


def resolved_notes(
    schedule: Schedule,
    schedule_type: ScheduleType,
    house: HouseStandard,
    project: Project | None = None,
) -> list[ResolvedNote]:
    """What prints on this schedule, in order, each note knowing its layer."""
    project = project or schedule.building.project
    return resolve_notes(
        organisation=house.general_notes,
        project=project.notes or [],
        type_notes=schedule_type.notes,
        schedule=schedule.notes,
    )


def notes_view(
    schedule: Schedule,
    schedule_type: ScheduleType,
    house: HouseStandard,
    project: Project | None = None,
) -> dict[str, Any]:
    """Everything the notes tab shows: the merged result and each layer of it.

    The layers are returned even when the schedule has diverged, because "what
    you would go back to" is the thing somebody needs to see before deciding
    whether to.
    """
    project = project or schedule.building.project
    inherited = resolve_notes(
        organisation=house.general_notes,
        project=project.notes or [],
        type_notes=schedule_type.notes,
    )
    resolved = resolved_notes(schedule, schedule_type, house, project)
    return {
        "notes": note_texts(resolved),
        "note_layers": [n.to_dict() for n in resolved],
        "notes_customised": schedule.notes is not None,
        "inherited": [n.to_dict() for n in inherited],
        "layers": {
            "organisation": list(house.general_notes),
            "project": list(project.notes or []),
            "type": list(schedule_type.notes),
        },
    }


def set_schedule_notes(
    schedule: Schedule, notes: Sequence[str] | None
) -> list[str] | None:
    """Give a schedule its own notes, or hand it back to the layers with None."""
    schedule.notes = None if notes is None else [str(n) for n in notes]
    return schedule.notes


def revert_schedule_notes(
    schedule: Schedule,
    schedule_type: ScheduleType,
    house: HouseStandard,
    project: Project | None = None,
) -> list[ResolvedNote]:
    """Drop this schedule's own notes and go back to inheriting."""
    schedule.notes = None
    return resolved_notes(schedule, schedule_type, house, project)


def starting_point(
    schedule: Schedule,
    schedule_type: ScheduleType,
    house: HouseStandard,
    project: Project | None = None,
) -> list[str]:
    """What a schedule's own notes start as when it first diverges."""
    return seed_from(resolved_notes(schedule, schedule_type, house, project))
