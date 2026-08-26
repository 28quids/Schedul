"""Translation between database rows and core domain objects.

Kept in one place so ``core/`` never learns what a database row is, and the
service layer never re-implements a domain rule.
"""

from __future__ import annotations

from typing import Any, Iterable

from ..core.catalogue import Column, ScheduleType
from ..core.house import HouseStandard
from ..core.naming import NamingScheme, ResolutionContext, volume_context
from ..core.numbering import ScheduleRef
from ..core.revisions import Revision, is_issued
from ..db.models import (
    Building,
    Equipment,
    HouseStandardRow,
    Project,
    RevisionRow,
    Schedule,
    ScheduleTypeRow,
)

__all__ = [
    "type_from_row",
    "type_to_row_fields",
    "house_from_row",
    "scheme_for",
    "context_for",
    "revisions_of",
    "schedule_ref",
    "design_constants_for",
    "constant_aliases",
]


def type_from_row(row: ScheduleTypeRow) -> ScheduleType:
    """Rebuild the domain object from its stored form."""
    return ScheduleType(
        code=row.code,
        title=row.title,
        short=row.short or "",
        version=row.version,
        volume=row.volume or "",
        columns=[Column.from_dict(c) for c in (row.columns or [])],
        notes=list(row.notes or []),
        created=row.created_at.date().isoformat() if row.created_at else "",
        updated=row.updated_at.date().isoformat() if row.updated_at else "",
        history=list(row.history or []),
    )


def type_to_row_fields(st: ScheduleType) -> dict[str, Any]:
    """The column values that store a schedule type."""
    return {
        "code": st.code,
        "title": st.title,
        "short": st.short,
        "version": st.version,
        "volume": st.volume,
        "columns": [c.to_dict() for c in st.columns],
        "notes": list(st.notes),
        "history": list(st.history),
    }


def house_from_row(row: HouseStandardRow | None) -> HouseStandard:
    """The organisation's house standard, or the built-in default."""
    if row is None or not row.data:
        return HouseStandard()
    return HouseStandard.from_dict(row.data)


def scheme_for(house: HouseStandard) -> NamingScheme:
    return NamingScheme.from_dict(house.naming)


def context_for(
    project: Project,
    building: Building | None = None,
    schedule_type: ScheduleType | None = None,
    schedule: Schedule | None = None,
    *,
    number: int | None = None,
    scheme: NamingScheme | None = None,
    house: HouseStandard | None = None,
) -> ResolutionContext:
    """Assemble the token layers for one schedule.

    Company values come from the house standard's token defaults, so nothing is
    supplied at that layer here; resolution falls through to them.
    """
    ctx = ResolutionContext(
        project={
            "project_number": project.number,
            **(project.naming_overrides or {}),
        }
    )

    if building is not None:
        ctx.building = {"building": building.ref, **(building.naming_overrides or {})}

    if schedule_type is not None and schedule_type.volume and scheme is not None:
        ctx.type = volume_context(schedule_type.volume, scheme)
        # Discipline follows the volume where the house standard says it does:
        # ventilation is mechanical, drainage is public health.
        #
        # Only when the project has not set one explicitly. Resolution puts the
        # type layer above the project layer, so supplying it unconditionally
        # would make a deliberate project-wide discipline impossible to express
        # -- the derived value is a sensible default, not a mandate.
        if house is not None and "discipline" not in (project.naming_overrides or {}):
            discipline = house.discipline_for(schedule_type.volume)
            if discipline:
                ctx.type["discipline"] = discipline

    if schedule is not None:
        ctx.schedule = {
            "number": schedule.number,
            **(schedule.naming_overrides or {}),
        }
    if number is not None:
        ctx.schedule["number"] = number

    return ctx


def revisions_of(schedule: Schedule) -> list[Revision]:
    """The schedule's revision log as domain objects."""
    return [
        Revision(
            code=r.code,
            status=r.status,
            date=r.issue_date,
            prepared_by=r.prepared_by,
            checked_by=r.checked_by,
            approved_by=r.approved_by,
            description=r.description,
        )
        for r in schedule.revisions
    ]


def schedule_ref(schedule: Schedule, *, docnum: str = "", filename: str = "") -> ScheduleRef:
    """The numbering view of a schedule, including whether it is locked.

    The issued-document lock is computed from the revision log rather than
    stored, so it can never disagree with the log itself.
    """
    log = revisions_of(schedule)
    locked = is_issued(log)
    current_status = ""
    if log:
        from ..core.revisions import current as _current

        latest = _current(log)
        current_status = latest.status if latest else ""

    reason = ""
    if locked:
        reason = (
            "this schedule has been issued; ISO 19650 expects an issued "
            "reference to stay stable"
        )

    return ScheduleRef(
        code=schedule.code,
        number=schedule.number,
        title=schedule.schedule_type.title if schedule.schedule_type else "",
        volume=schedule.schedule_type.volume if schedule.schedule_type else "",
        docnum=docnum or schedule.docnum,
        filename=filename,
        status=current_status or "S0",
        locked=locked,
        lock_reason=reason,
        state=schedule.state,  # type: ignore[arg-type]
    )


def design_constants_for(project: Project, house: HouseStandard) -> dict[str, float]:
    """The project's design constants, falling back to the house standard."""
    merged = dict(house.design_constants)
    merged.update(project.design_constants or {})
    return {k: float(v) for k, v in merged.items() if v not in (None, "")}


def constant_aliases(constants: dict[str, float]) -> dict[str, float]:
    """Map the ``SETUP_*`` aliases a formula uses onto their values."""
    from ..core.formula import CONSTANTS

    return {alias: constants[name] for alias, name in CONSTANTS.items() if name in constants}
