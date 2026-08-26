"""Shared dependencies and view-model assembly for the API."""

from __future__ import annotations

from typing import Any, Iterable

from fastapi import Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.house import HouseStandard
from ..core.naming import NamingError, NamingScheme, ResolutionContext, volume_context
from ..core.revisions import current as current_revision
from ..core.revisions import is_issued, sort_key
from ..db.models import (
    Building,
    Organisation,
    Project,
    RevisionRow,
    Schedule,
    ScheduleRow,
    ScheduleTypeRow,
)
from ..db.session import get_session
from ..services import projects as svc
from ..services.converters import (
    context_for,
    design_constants_for,
    revisions_of,
    type_from_row,
)
from ..services.seed import ensure_default_organisation
from .schemas import BuildingOut, ProjectOut, ScheduleOut

__all__ = [
    "get_db",
    "current_org",
    "get_project",
    "get_building",
    "get_schedule",
    "schedule_view",
    "building_view",
    "project_view",
    "not_found",
]


def get_db(session: Session = Depends(get_session)) -> Session:
    return session


def current_org(session: Session = Depends(get_db)) -> Organisation:
    """The organisation this request acts for.

    A local install runs single-tenant, so this resolves to the one seeded
    organisation. When logins arrive it reads the session instead, and nothing
    below it changes: every query is already scoped by organisation.
    """
    return ensure_default_organisation(session)


def not_found(what: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"no such {what}")


def get_project(
    project_id: str, session: Session = Depends(get_db), org: Organisation = Depends(current_org)
) -> Project:
    project = session.get(Project, project_id)
    if project is None or project.organisation_id != org.id:
        raise not_found("project")
    return project


def get_building(
    building_id: str, session: Session = Depends(get_db), org: Organisation = Depends(current_org)
) -> Building:
    building = session.get(Building, building_id)
    if building is None or building.project.organisation_id != org.id:
        raise not_found("building")
    return building


def get_schedule(
    schedule_id: str, session: Session = Depends(get_db), org: Organisation = Depends(current_org)
) -> Schedule:
    schedule = session.get(Schedule, schedule_id)
    if schedule is None or schedule.building.project.organisation_id != org.id:
        raise not_found("schedule")
    return schedule


# ------------------------------------------------------------ view models ---


def _row_count(session: Session, schedule_id: str) -> int:
    """How many rows carry any typed value.

    A schedule padded with blank rows should not read as populated, because
    "has data" gates rebuilding and upgrading a type version.
    """
    rows = session.scalars(
        select(ScheduleRow).where(ScheduleRow.schedule_id == schedule_id)
    )
    return sum(1 for r in rows if any(v not in (None, "") for v in (r.values or {}).values()))


def schedule_view(
    session: Session,
    schedule: Schedule,
    scheme: NamingScheme,
    house: HouseStandard,
) -> ScheduleOut:
    try:
        docnum = svc.document_number_for(schedule, scheme)
        filename = svc.filename_for(schedule, scheme)
    except NamingError:
        docnum, filename = schedule.docnum, ""

    log = revisions_of(schedule)
    latest = current_revision(log)
    status_code = ""
    status_desc = ""
    if latest and latest.status:
        text = str(latest.status)
        if " - " in text:
            status_code, status_desc = text.split(" - ", 1)
        else:
            status_code = text
            status_desc = house.status_description(text)

    type_row = schedule.schedule_type
    return ScheduleOut(
        id=schedule.id,
        code=schedule.code,
        title=type_row.title if type_row else schedule.code,
        number=schedule.number,
        docnum=docnum,
        filename=filename,
        state=schedule.state,
        type_version=schedule.type_version,
        latest_type_version=type_row.version if type_row else schedule.type_version,
        volume=type_row.volume if type_row else "",
        row_count=_row_count(session, schedule.id),
        revision=latest.code if latest else "",
        issue_date=latest.date if latest else None,
        status=status_code,
        status_description=status_desc,
        locked=is_issued(log),
        lock_reason=(
            "this schedule has been issued; ISO 19650 expects an issued "
            "reference to stay stable"
            if is_issued(log)
            else ""
        ),
    )


def building_view(
    session: Session, building: Building, scheme: NamingScheme, house: HouseStandard
) -> BuildingOut:
    return BuildingOut(
        id=building.id,
        ref=building.ref,
        name=building.name,
        label=building.label,
        position=building.position,
        retired_numbers=list(building.retired_numbers or []),
        schedules=[
            schedule_view(session, s, scheme, house)
            for s in svc.live_schedules(session, building)
        ],
    )


def project_view(session: Session, project: Project, org: Organisation) -> ProjectOut:
    house = svc.house_standard_for(session, org.id)
    scheme = svc.naming_scheme_for(session, org.id)
    buildings = [
        building_view(session, b, scheme, house)
        for b in svc.buildings_of(session, project)
    ]

    # A live preview of the number the next schedule would take, so the tokens
    # can be checked before anything is created.
    first_building = svc.buildings_of(session, project)
    preview_ctx = context_for(
        project,
        first_building[0] if first_building else None,
        None,
        None,
        number=(
            max(
                [s.number for b in buildings for s in b.schedules],
                default=(scheme.tokens["number"].start or 10) - 1,
            )
            + 1
        ),
        scheme=scheme,
    )
    if "volume" not in preview_ctx.type:
        preview_ctx.type = volume_context("5.6", scheme)

    return ProjectOut(
        id=project.id,
        name=project.name,
        number=project.number,
        client=project.client,
        site_address=project.site_address,
        architect=project.architect,
        main_contractor=project.main_contractor,
        riba_stage=project.riba_stage,
        prepared_by=project.prepared_by,
        checked_by=project.checked_by,
        approved_by=project.approved_by,
        naming_overrides=dict(project.naming_overrides or {}),
        design_constants=dict(project.design_constants or {}),
        effective_constants=design_constants_for(project, house),
        building_count=len(buildings),
        schedule_count=sum(len(b.schedules) for b in buildings),
        updated_at=project.updated_at,
        buildings=buildings,
        naming_preview=scheme.preview(preview_ctx, "Example Schedule"),
    )
