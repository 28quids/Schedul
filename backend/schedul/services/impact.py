"""The change log: why a schedule says something different from last week.

Almost everything in this tool is shared on purpose. A library value is read
rather than copied, so correcting a product corrects every schedule using it. A
type's columns are the type's, so widening one widens it everywhere. The house
notes print on every document. That sharing is the feature -- and it is also the
reason somebody opens a schedule they have not touched and finds it changed.

Each of those changes is already recorded where it happened: a type keeps its
own history, the library keeps a change log. What was missing is one place that
answers "what has moved under me", which is the question actually being asked.
This assembles that from the three sources, newest first, and adds the standing
condition that is not an event at all: schedules built against an older version
of their type.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.catalogue import ColumnDiff
from ..db.models import (
    Building,
    ChangeEvent,
    Equipment,
    EquipmentChange,
    Project,
    Schedule,
    ScheduleTypeRow,
)

__all__ = [
    "record",
    "record_type_change",
    "affected_schedules",
    "stale_schedules",
    "change_log",
]


def record(
    session: Session,
    organisation_id: str,
    area: str,
    *,
    subject: str = "",
    summary: str = "",
    severity: str = "info",
    detail: dict[str, Any] | None = None,
    actor: str = "",
) -> ChangeEvent:
    """Record one change worth telling the practice about."""
    event = ChangeEvent(
        organisation_id=organisation_id,
        area=area,
        subject=subject,
        summary=summary,
        severity=severity,
        detail=detail or {},
        actor=actor,
    )
    session.add(event)
    session.flush()
    return event


def affected_schedules(
    session: Session, organisation_id: str, type_id: str
) -> list[dict[str, Any]]:
    """Every live schedule built from this type, with how much is typed into it.

    The row count is the part that decides whether a structural change is
    trivial or expensive: renaming a column on a type nobody has filled in costs
    nothing, and renaming one on forty filled rows loses forty values.
    """
    stmt = (
        select(Schedule, Building, Project)
        .join(Building, Schedule.building_id == Building.id)
        .join(Project, Building.project_id == Project.id)
        .where(
            Project.organisation_id == organisation_id,
            Schedule.schedule_type_id == type_id,
            Schedule.deleted_marker == "",
        )
    )
    out: list[dict[str, Any]] = []
    for schedule, building, project in session.execute(stmt).all():
        populated = sum(
            1 for r in schedule.rows
            if any(v not in (None, "") for v in (r.values or {}).values())
        )
        out.append(
            {
                "schedule_id": schedule.id,
                "code": schedule.code,
                "project": project.number or project.name,
                "project_id": project.id,
                "building": building.label,
                "rows": populated,
                "built_against": schedule.type_version,
            }
        )
    return sorted(out, key=lambda s: (-s["rows"], s["project"], s["code"]))


def record_type_change(
    session: Session,
    organisation_id: str,
    type_row: ScheduleTypeRow,
    diff: ColumnDiff,
    *,
    note: str = "",
    actor: str = "",
) -> ChangeEvent | None:
    """Record a schedule-type edit, with what it lands on.

    A presentational change is recorded too, at a lower severity. Somebody who
    finds their columns a different width should be able to see that it was
    deliberate rather than wonder whether the tool did it.
    """
    if diff.empty:
        return None
    affected = affected_schedules(session, organisation_id, type_row.id)
    return record(
        session,
        organisation_id,
        "type",
        subject=type_row.code,
        summary=f"{type_row.code}: {diff.summary()}" + (f" ({note})" if note else ""),
        severity=diff.severity if affected else "info",
        detail={
            "type_id": type_row.id,
            "version": type_row.version,
            "diff": diff.to_dict(),
            "affected": affected,
            "affected_count": len(affected),
            "rows_at_risk": sum(s["rows"] for s in affected) if diff.structural else 0,
        },
        actor=actor,
    )


def stale_schedules(session: Session, organisation_id: str) -> list[dict[str, Any]]:
    """Schedules pinned to an older version of their type than the current one.

    Not an error. The columns a schedule shows are always its type's current
    ones -- that is what makes an edit in the designer take effect -- so this is
    the record of which documents were set up before a change, which is what a
    reviewer wants when a duty has moved.
    """
    stmt = (
        select(Schedule, Building, Project, ScheduleTypeRow)
        .join(Building, Schedule.building_id == Building.id)
        .join(Project, Building.project_id == Project.id)
        .join(ScheduleTypeRow, Schedule.schedule_type_id == ScheduleTypeRow.id)
        .where(
            Project.organisation_id == organisation_id,
            Schedule.deleted_marker == "",
            Schedule.type_version < ScheduleTypeRow.version,
        )
    )
    return [
        {
            "schedule_id": schedule.id,
            "code": schedule.code,
            "project": project.number or project.name,
            "project_id": project.id,
            "building": building.label,
            "built_against": schedule.type_version,
            "current": type_row.version,
            "behind": type_row.version - schedule.type_version,
        }
        for schedule, building, project, type_row in session.execute(stmt).all()
    ]


def _library_entries(
    session: Session, organisation_id: str, limit: int
) -> list[dict[str, Any]]:
    """Library corrections, as impact-log entries.

    Read from the equipment change log rather than duplicated into the event
    table: one record of a change is enough, and two would eventually disagree.
    """
    stmt = (
        select(EquipmentChange, Equipment)
        .join(Equipment, EquipmentChange.equipment_id == Equipment.id)
        .where(Equipment.organisation_id == organisation_id)
        .order_by(EquipmentChange.at.desc(), EquipmentChange.id.desc())
        .limit(limit)
    )
    out: list[dict[str, Any]] = []
    for change, entry in session.execute(stmt).all():
        columns = list((change.changes or {}).keys())
        # A correction reaches every schedule using the product, which is the
        # whole point of the library and the reason it belongs in this log.
        severity = "warn" if change.action == "updated" and columns else "info"
        out.append(
            {
                "at": change.at,
                "area": "library",
                "subject": f"{entry.type_code} {entry.model_reference}",
                "summary": _library_summary(change.action, entry.model_reference, columns),
                "severity": severity,
                "actor": change.actor or "",
                "detail": {
                    "equipment_id": entry.id,
                    "type_code": entry.type_code,
                    "columns": columns,
                    "changes": [
                        {"column": c, "before": pair[0], "after": pair[1]}
                        for c, pair in (change.changes or {}).items()
                    ],
                },
            }
        )
    return out


def _library_summary(action: str, reference: str, columns: Sequence[str]) -> str:
    if action == "created":
        return f"{reference} added to the library"
    if action == "updated":
        if not columns:
            return f"{reference} updated"
        return (
            f"{reference}: {', '.join(columns[:4])}"
            + (f" and {len(columns) - 4} more" if len(columns) > 4 else "")
            + " changed, which moves every schedule using it"
        )
    return f"{reference} {action}"


def change_log(
    session: Session,
    organisation_id: str,
    *,
    limit: int = 60,
    area: str = "",
) -> dict[str, Any]:
    """The whole impact log: recorded events, library changes, and what is behind."""
    stmt = (
        select(ChangeEvent)
        .where(ChangeEvent.organisation_id == organisation_id)
        .order_by(ChangeEvent.at.desc())
        .limit(limit)
    )
    if area:
        stmt = stmt.where(ChangeEvent.area == area)

    entries: list[dict[str, Any]] = [
        {
            "at": e.at,
            "area": e.area,
            "subject": e.subject,
            "summary": e.summary,
            "severity": e.severity,
            "actor": e.actor or "",
            "detail": e.detail or {},
        }
        for e in session.scalars(stmt)
    ]

    if not area or area == "library":
        entries.extend(_library_entries(session, organisation_id, limit))

    entries.sort(key=lambda e: e["at"] or _dt.datetime.min, reverse=True)
    stale = stale_schedules(session, organisation_id)

    return {
        "entries": entries[:limit],
        "stale_schedules": stale,
        "counts": {
            "entries": len(entries),
            "warnings": sum(1 for e in entries if e["severity"] == "warn"),
            "stale": len(stale),
        },
    }
