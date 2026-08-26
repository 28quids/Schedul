"""Project, building and schedule operations.

Every rule here delegates to ``core/``; this module's job is transactions and
lookups, not policy.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, replace as _replace
from typing import Any, Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core import numbering
from ..core.catalogue import ScheduleType
from ..core.house import HouseStandard
from ..core.naming import NamingScheme, NamingError
from ..core.numbering import RenumberPlan, ScheduleRef
from ..db.models import (
    Building,
    HouseStandardRow,
    Organisation,
    Project,
    Schedule,
    ScheduleRow,
    ScheduleTypeRow,
)
from .converters import (
    context_for,
    house_from_row,
    scheme_for,
    schedule_ref,
    type_from_row,
)

__all__ = [
    "ServiceError",
    "live_schedules",
    "buildings_of",
    "clone_candidates",
    "house_standard_for",
    "naming_scheme_for",
    "document_number_for",
    "filename_for",
    "add_building",
    "clone_building",
    "delete_building",
    "add_schedule",
    "archive_schedule",
    "restore_schedule",
    "schedule_refs",
    "plan_operation",
    "apply_plan",
    "rename_building_plan",
    "apply_building_rename",
    "run_audit",
]


class ServiceError(Exception):
    """An operation the caller asked for cannot be performed."""


# ------------------------------------------------------------- lookups ---


def live_schedules(session: Session, building: Building) -> list[Schedule]:
    """A building's live schedules, read fresh.

    Not ``building.live_schedules``: a loaded relationship does not see rows
    added later in the same session, so allocating two schedules in a row would
    hand out the same number twice.
    """
    return list(
        session.scalars(
            select(Schedule)
            .where(Schedule.building_id == building.id, Schedule.deleted_marker == "")
            .order_by(Schedule.number)
        )
    )


def buildings_of(session: Session, project: Project) -> list[Building]:
    """A project's buildings, read fresh. See :func:`live_schedules`."""
    return list(
        session.scalars(
            select(Building)
            .where(Building.project_id == project.id)
            .order_by(Building.position)
        )
    )


def _refresh(session: Session, building: Building) -> None:
    """Drop the cached schedule collection so callers see current rows."""
    session.expire(building, ["schedules"])


def _refresh_project(session: Session, project: Project) -> None:
    session.expire(project, ["buildings"])


def house_standard_for(session: Session, organisation_id: str) -> HouseStandard:
    row = session.scalar(
        select(HouseStandardRow).where(HouseStandardRow.organisation_id == organisation_id)
    )
    return house_from_row(row)


def naming_scheme_for(session: Session, organisation_id: str) -> NamingScheme:
    return scheme_for(house_standard_for(session, organisation_id))


def _type_row(session: Session, organisation_id: str, code: str) -> ScheduleTypeRow:
    row = session.scalar(
        select(ScheduleTypeRow).where(
            ScheduleTypeRow.organisation_id == organisation_id,
            ScheduleTypeRow.code == code.strip().upper(),
        )
    )
    if row is None:
        raise ServiceError(f"no schedule type {code!r} in this organisation's catalogue")
    return row


def document_number_for(
    schedule: Schedule,
    scheme: NamingScheme,
    *,
    number: int | None = None,
    house: HouseStandard | None = None,
) -> str:
    """Derive a schedule's document number from the tokens, live.

    Not read from a stored copy: SPEC.md 5.1 makes the document number one cell,
    and deriving it means a building rename cannot leave the number stale.
    """
    building = schedule.building
    project = building.project
    st = type_from_row(schedule.schedule_type)
    ctx = context_for(
        project, building, st, schedule, number=number, scheme=scheme, house=house
    )
    return scheme.document_number(ctx)


def filename_for(
    schedule: Schedule,
    scheme: NamingScheme,
    *,
    number: int | None = None,
    house: HouseStandard | None = None,
) -> str:
    building = schedule.building
    project = building.project
    st = type_from_row(schedule.schedule_type)
    ctx = context_for(
        project, building, st, schedule, number=number, scheme=scheme, house=house
    )
    return scheme.filename(ctx, st.title)


# ------------------------------------------------------------ buildings ---


def add_building(
    session: Session, project: Project, ref: str, name: str = ""
) -> Building:
    """Add a building with an empty schedule selection.

    Adding from scratch must be as easy as cloning, so this is the plain path
    and ``clone_building`` is the head start.
    """
    ref = ref.strip()
    if not ref:
        raise ServiceError("a building needs a reference")
    existing = buildings_of(session, project)
    if any(b.ref.lower() == ref.lower() for b in existing):
        raise ServiceError(f"this project already has a building {ref!r}")

    building = Building(
        project_id=project.id,
        ref=ref,
        name=name.strip(),
        position=len(existing),
    )
    session.add(building)
    session.flush()
    _refresh_project(session, project)
    return building


def clone_building(
    session: Session,
    project: Project,
    source: Building,
    ref: str,
    name: str,
    codes: Sequence[str],
) -> Building:
    """Create a building carrying a chosen subset of another's schedule types.

    Copies the *selection*, never filled-in data, and allocates fresh numbers in
    the new building. Buildings on the same job frequently share some types and
    differ on others, so the caller passes exactly which codes to bring across
    rather than getting a copy of the source.
    """
    building = add_building(session, project, ref, name)

    # The checklist is editable in both directions: the source's types arrive
    # pre-ticked, and the user may untick some and add others. So a code the
    # source does not have is a legitimate addition, not an error -- only a code
    # missing from the catalogue is, and add_schedule reports that.
    for code in [c.strip().upper() for c in codes]:
        add_schedule(session, building, code)
    return building


def clone_candidates(session: Session, source: Building) -> list[str]:
    """The types to pre-tick when cloning: whatever the source building has."""
    return [s.code for s in live_schedules(session, source)]


def delete_building(session: Session, building: Building) -> None:
    """Remove a building and archive its schedules, keeping their data.

    A project does not silently collapse back to single-building presentation
    when one of three is deleted; that is the UI's decision based on how many
    remain, and it is only cosmetic.
    """
    project = building.project
    for schedule in live_schedules(session, building):
        archive_schedule(session, schedule)
    session.delete(building)
    session.flush()
    _refresh_project(session, project)


# ------------------------------------------------------------ schedules ---


def add_schedule(session: Session, building: Building, code: str) -> Schedule:
    """Allocate a number within this building and create the schedule.

    The number is recorded with ``state='allocated'`` before anything is
    rendered and flipped to ``'built'`` after, so an interrupted export leaves a
    reserved number rather than an orphan.
    """
    project = building.project
    type_row = _type_row(session, project.organisation_id, code)

    if any(s.code == type_row.code for s in live_schedules(session, building)):
        raise ServiceError(
            f"{building.ref} already has a {type_row.code} schedule; "
            f"one schedule of each type per building is the rule"
        )

    house = house_standard_for(session, project.organisation_id)
    scheme = scheme_for(house)
    number_token = scheme.tokens.get("number")
    start = (number_token.start if number_token else None) or 10
    width = (number_token.width if number_token else None) or 8

    # With per-volume numbering, 5.2-00001 and 5.3-00001 are separate sequences,
    # so only schedules sharing this volume constrain the next number.
    volume = type_row.volume or ""
    scoped = [
        s for s in live_schedules(session, building)
        if not house.numbers_per_volume or (s.volume or "") == volume
    ]
    refs = [schedule_ref(s) for s in scoped]
    number, warnings = numbering.allocate(
        refs, retired_numbers(building, volume, house), start=start, width=width
    )

    schedule = Schedule(
        building_id=building.id,
        schedule_type_id=type_row.id,
        code=type_row.code,
        number=number,
        volume=volume,
        type_version=type_row.version,
        state="allocated",
    )
    session.add(schedule)
    session.flush()

    # Refresh the relationship so the document number can be derived.
    session.refresh(schedule)
    try:
        schedule.docnum = document_number_for(schedule, scheme, house=house)
    except NamingError:
        # An incomplete token set is a project-setup problem, not a reason to
        # refuse the schedule; the audit and the preview both report it.
        schedule.docnum = ""
    schedule.state = "built"
    session.flush()
    _refresh(session, building)
    return schedule


def retired_numbers(
    building: Building, volume: str, house: HouseStandard
) -> list[int]:
    """Numbers given up in this building, scoped to a volume when configured.

    Stored either as a flat list (one sequence per building) or as a dict keyed
    by volume. Both shapes are read here so a database written before per-volume
    numbering existed keeps working without a migration step of its own.
    """
    stored = building.retired_numbers or []
    if isinstance(stored, dict):
        if house.numbers_per_volume:
            return list(stored.get(volume or "", []))
        return sorted({n for numbers in stored.values() for n in numbers})
    return list(stored)


def _retire(building: Building, volume: str, number: int, house: HouseStandard) -> None:
    """Record a number as given up, in whichever shape this building uses."""
    stored = building.retired_numbers or []
    if house.numbers_per_volume or isinstance(stored, dict):
        as_dict = dict(stored) if isinstance(stored, dict) else {"": list(stored)}
        key = volume or ""
        as_dict[key] = numbering.retire(as_dict.get(key, []), number)
        building.retired_numbers = as_dict
    else:
        building.retired_numbers = numbering.retire(stored, number)


def archive_schedule(session: Session, schedule: Schedule) -> Schedule:
    """Remove a schedule from the record, retiring its number.

    The rows are kept. The number is retired and is not reallocated, so adding
    the same type again later gets a fresh number rather than reusing an
    identity that may already have been issued.
    """
    building = schedule.building
    house = house_standard_for(session, building.project.organisation_id)
    _retire(building, schedule.volume or "", schedule.number, house)
    schedule.deleted_marker = schedule.id
    schedule.archived_at = _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)
    session.flush()
    _refresh(session, building)
    return schedule


def restore_schedule(session: Session, schedule: Schedule) -> Schedule:
    """Undo an archive, giving the schedule a fresh number if its old one clashes."""
    building = schedule.building
    taken = {s.number for s in live_schedules(session, building)}
    if schedule.number in taken:
        scheme = naming_scheme_for(session, building.project.organisation_id)
        token = scheme.tokens.get("number")
        refs = [schedule_ref(s) for s in live_schedules(session, building)]
        schedule.number, _ = numbering.allocate(
            refs,
            building.retired_numbers or [],
            start=(token.start if token else None) or 10,
            width=(token.width if token else None) or 8,
        )
    schedule.deleted_marker = ""
    schedule.archived_at = None
    building.retired_numbers = [
        n for n in (building.retired_numbers or []) if n != schedule.number
    ]
    session.flush()
    _refresh(session, building)
    return schedule


# ------------------------------------------------------------ numbering ---


def schedule_refs(session: Session, building: Building) -> list[ScheduleRef]:
    """The numbering view of a building's live schedules."""
    house = house_standard_for(session, building.project.organisation_id)
    scheme = scheme_for(house)
    refs = []
    for s in live_schedules(session, building):
        try:
            docnum = document_number_for(s, scheme, house=house)
            filename = filename_for(s, scheme, house=house)
        except NamingError:
            docnum, filename = s.docnum, ""
        refs.append(schedule_ref(s, docnum=docnum, filename=filename))
    return refs


def _renderer_for(session: Session, building: Building):
    """Recompute (docnum, filename) for a schedule at a proposed number."""
    house = house_standard_for(session, building.project.organisation_id)
    scheme = scheme_for(house)
    by_code = {s.code: s for s in live_schedules(session, building)}

    def render(ref: ScheduleRef, number: int) -> tuple[str, str]:
        schedule = by_code[ref.code]
        try:
            return (
                document_number_for(schedule, scheme, number=number, house=house),
                filename_for(schedule, scheme, number=number, house=house),
            )
        except NamingError:
            return "", ""

    return render


def plan_operation(
    session: Session,
    building: Building,
    operation: str,
    *,
    code: str | None = None,
    other_code: str | None = None,
    number: int | None = None,
    allow_locked: Sequence[str] = (),
) -> RenumberPlan:
    """Produce a reviewable plan for one of the five renumber operations."""
    refs = schedule_refs(session, building)
    render = _renderer_for(session, building)

    if operation == "set":
        if code is None or number is None:
            raise ServiceError("set needs a code and a number")
        return numbering.set_number(refs, code, number, render, allow_locked=allow_locked)
    if operation == "swap":
        if not code or not other_code:
            raise ServiceError("swap needs two codes")
        return numbering.swap(refs, code, other_code, render, allow_locked=allow_locked)
    if operation == "insert":
        if code is None or number is None:
            raise ServiceError("insert needs a code and a number")
        return numbering.insert_at(refs, code, number, render, allow_locked=allow_locked)
    if operation == "compact":
        return numbering.compact(refs, render, allow_locked=allow_locked)
    if operation == "rebase":
        if number is None:
            raise ServiceError("rebase needs a starting number")
        return numbering.rebase(refs, number, render, allow_locked=allow_locked)
    raise ServiceError(f"unknown operation {operation!r}")


def apply_plan(session: Session, building: Building, plan: RenumberPlan) -> int:
    """Apply a plan that has no blocked rows. Returns how many schedules moved.

    Numbers are moved out of the way first, because the live uniqueness
    constraint would otherwise reject a legitimate swap partway through.
    """
    if plan.blocked:
        raise ServiceError(
            f"{len(plan.blocked)} row(s) are blocked; the plan cannot be applied"
        )
    moves = plan.moves
    if not moves:
        return 0

    by_code = {s.code: s for s in live_schedules(session, building)}
    parked = -1
    for change in moves:
        by_code[change.code].number = parked
        parked -= 1
    session.flush()

    house = house_standard_for(session, building.project.organisation_id)
    scheme = scheme_for(house)
    for change in moves:
        schedule = by_code[change.code]
        schedule.number = change.new_number
        try:
            schedule.docnum = document_number_for(schedule, scheme, house=house)
        except NamingError:
            schedule.docnum = ""
    session.flush()
    _refresh(session, building)
    return len(moves)


def _names_with_building_ref(
    schedule: Schedule, scheme: NamingScheme, ref: str,
    house: HouseStandard | None = None,
) -> tuple[str, str]:
    """The document number and filename this schedule would have under ``ref``."""
    building = schedule.building
    project = building.project
    st = type_from_row(schedule.schedule_type)
    ctx = context_for(project, building, st, schedule, scheme=scheme, house=house)
    ctx.building = {**ctx.building, "building": ref}
    return scheme.document_number(ctx), scheme.filename(ctx, st.title)


def rename_building_plan(
    session: Session, building: Building, new_ref: str
) -> RenumberPlan:
    """What changing a building's ref would do, scoped to that building.

    This is the "swap the -PROJECTNUMBER- placeholder for a real block code"
    flow. It touches that building and nothing else, which is what makes it safe
    on a live multi-block job.
    """
    house = house_standard_for(session, building.project.organisation_id)
    scheme = scheme_for(house)
    plan = RenumberPlan(operation=f"rename building {building.ref} to {new_ref}")

    for schedule in live_schedules(session, building):
        try:
            old_doc = document_number_for(schedule, scheme, house=house)
            old_name = filename_for(schedule, scheme, house=house)
        except NamingError:
            old_doc, old_name = schedule.docnum, ""

        # Compute the proposed value by overriding the building token, never by
        # mutating the building: a query during the preview would autoflush the
        # temporary ref straight into the database.
        try:
            new_doc, new_name = _names_with_building_ref(schedule, scheme, new_ref, house)
        except NamingError:
            new_doc, new_name = "", ""

        change = numbering.NumberChange(
            code=schedule.code,
            old_number=schedule.number,
            new_number=schedule.number,
            old_docnum=old_doc,
            new_docnum=new_doc,
            old_filename=old_name,
            new_filename=new_name,
        )
        ref = schedule_ref(schedule)
        if ref.locked:
            change.blocked = ref.lock_reason
        plan.changes.append(change)

    return plan


def apply_building_rename(
    session: Session, building: Building, new_ref: str, *, force: bool = False
) -> RenumberPlan:
    """Change a building's ref, refreshing every affected document number."""
    project = building.project
    if any(
        b.id != building.id and b.ref.lower() == new_ref.strip().lower()
        for b in buildings_of(session, project)
    ):
        raise ServiceError(f"this project already has a building {new_ref!r}")

    plan = rename_building_plan(session, building, new_ref)
    if plan.blocked and not force:
        raise ServiceError(
            f"{len(plan.blocked)} schedule(s) in {building.ref} have been issued; "
            f"renaming would change their document numbers"
        )

    building.ref = new_ref.strip()
    house = house_standard_for(session, project.organisation_id)
    scheme = scheme_for(house)
    for schedule in live_schedules(session, building):
        try:
            schedule.docnum = document_number_for(schedule, scheme, house=house)
        except NamingError:
            schedule.docnum = ""
    session.flush()
    return plan


def run_audit(session: Session, building: Building) -> list[numbering.AuditIssue]:
    """Health check for one building. Run before any export or renumber."""
    house = house_standard_for(session, building.project.organisation_id)
    scheme = scheme_for(house)
    refs = schedule_refs(session, building)

    expected: dict[str, str] = {}
    pinned: dict[str, int] = {}
    latest: dict[str, int] = {}
    for s in live_schedules(session, building):
        try:
            expected[s.code] = document_number_for(s, scheme, house=house)
        except NamingError:
            pass
        pinned[s.code] = s.type_version
        if s.schedule_type is not None:
            latest[s.code] = s.schedule_type.version

    # Compare the stored document number against what the tokens now produce.
    stored = {s.code: s.docnum for s in live_schedules(session, building) if s.docnum}
    drifted = {c: expected[c] for c in expected if c in stored and stored[c] != expected[c]}

    refs_with_stored = [
        _replace(r, docnum=stored.get(r.code, r.docnum)) for r in refs
    ]

    return numbering.audit(
        refs_with_stored,
        retired=building.retired_numbers or [],
        catalogue_versions=latest,
        schedule_versions=pinned,
        expected_docnums=drifted or None,
    )
