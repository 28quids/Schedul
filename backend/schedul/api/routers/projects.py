"""Projects, buildings, schedules and numbering."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core.numbering import RenumberPlan
from ...db.models import Building, Organisation, Project, Schedule, ScheduleTypeRow
from ...services import projects as svc
from ...services.projects import ServiceError
from ..deps import (
    building_view,
    current_org,
    get_building,
    get_db,
    get_project,
    get_schedule,
    project_view,
    schedule_view,
)
from ..schemas import (
    BuildingIn,
    BulkRevisionIn,
    ProjectColumnsIn,
    BuildingOut,
    CloneIn,
    PlanChange,
    PlanOut,
    ProjectIn,
    ProjectOut,
    ProjectSummary,
    RenameBuildingIn,
    RenumberIn,
    ScheduleIn,
    ScheduleOut,
)

router = APIRouter(prefix="/api/projects", tags=["projects"])


def _plan_out(plan: RenumberPlan, applied: int = 0) -> PlanOut:
    return PlanOut(
        operation=plan.operation,
        changes=[
            PlanChange(
                code=c.code,
                old_number=c.old_number,
                new_number=c.new_number,
                old_docnum=c.old_docnum,
                new_docnum=c.new_docnum,
                old_filename=c.old_filename,
                new_filename=c.new_filename,
                blocked=c.blocked,
                changed=c.changed,
            )
            for c in plan.changes
        ],
        warnings=plan.warnings,
        blocked_count=len(plan.blocked),
        can_apply=plan.can_apply,
        applied=applied,
    )


@router.get("", response_model=list[ProjectSummary])
def list_projects(
    session: Session = Depends(get_db), org: Organisation = Depends(current_org)
) -> list[ProjectSummary]:
    projects = session.scalars(
        select(Project)
        .where(Project.organisation_id == org.id)
        .order_by(Project.updated_at.desc())
    )
    out: list[ProjectSummary] = []
    for p in projects:
        buildings = svc.buildings_of(session, p)
        out.append(
            ProjectSummary(
                id=p.id,
                name=p.name,
                number=p.number,
                client=p.client,
                building_count=len(buildings),
                schedule_count=sum(
                    len(svc.live_schedules(session, b)) for b in buildings
                ),
                updated_at=p.updated_at,
            )
        )
    return out


@router.post("", response_model=ProjectOut, status_code=201)
def create_project(
    payload: ProjectIn,
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> ProjectOut:
    fields = payload.model_dump()
    fields["notes"] = fields.get("notes") or []
    project = Project(organisation_id=org.id, **fields)
    session.add(project)
    session.flush()
    # Every project has at least one building in the data model; the UI hides
    # the layer while there is only one, so small jobs never see it.
    svc.add_building(session, project, project.number or "Building 1", "")
    return project_view(session, project, org)


@router.get("/{project_id}", response_model=ProjectOut)
def read_project(
    project: Project = Depends(get_project),
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> ProjectOut:
    return project_view(session, project, org)


@router.put("/{project_id}", response_model=ProjectOut)
def update_project(
    payload: ProjectIn,
    project: Project = Depends(get_project),
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> ProjectOut:
    for key, value in payload.model_dump().items():
        # Omitting the notes leaves them alone. A setup form that does not carry
        # them must not blank them just by being saved.
        if key == "notes" and value is None:
            continue
        setattr(project, key, value)
    session.flush()
    return project_view(session, project, org)


@router.delete("/{project_id}", status_code=204)
def delete_project(
    project: Project = Depends(get_project), session: Session = Depends(get_db)
) -> None:
    session.delete(project)


# ------------------------------------------------------------- buildings ---


@router.post("/{project_id}/buildings", response_model=ProjectOut, status_code=201)
def add_building(
    payload: BuildingIn,
    project: Project = Depends(get_project),
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> ProjectOut:
    try:
        svc.add_building(session, project, payload.ref, payload.name)
    except ServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return project_view(session, project, org)


@router.post("/{project_id}/buildings/{building_id}/clone", response_model=ProjectOut)
def clone_building(
    payload: CloneIn,
    project: Project = Depends(get_project),
    building: Building = Depends(get_building),
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> ProjectOut:
    try:
        svc.clone_building(
            session, project, building, payload.ref, payload.name, payload.codes
        )
    except ServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return project_view(session, project, org)


@router.get("/{project_id}/buildings/{building_id}/clone-candidates")
def clone_candidates(
    building: Building = Depends(get_building), session: Session = Depends(get_db)
) -> dict[str, list[str]]:
    return {"codes": svc.clone_candidates(session, building)}


@router.put("/{project_id}/buildings/{building_id}", response_model=ProjectOut)
def update_building(
    payload: BuildingIn,
    project: Project = Depends(get_project),
    building: Building = Depends(get_building),
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> ProjectOut:
    building.name = payload.name
    if payload.ref.strip() and payload.ref.strip() != building.ref:
        try:
            svc.apply_building_rename(session, building, payload.ref)
        except ServiceError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.flush()
    return project_view(session, project, org)


@router.post("/{project_id}/buildings/{building_id}/rename", response_model=PlanOut)
def rename_building(
    payload: RenameBuildingIn,
    building: Building = Depends(get_building),
    session: Session = Depends(get_db),
) -> PlanOut:
    """Preview or apply a building-ref change.

    Scoped to one building, which is what makes the "-PROJECTNUMBER- placeholder
    to a real block code" flow safe on a live multi-block job.
    """
    if not payload.apply:
        return _plan_out(svc.rename_building_plan(session, building, payload.ref))
    try:
        plan = svc.apply_building_rename(
            session, building, payload.ref, force=payload.force
        )
    except ServiceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _plan_out(plan, applied=len(plan.changes))


@router.delete("/{project_id}/buildings/{building_id}", response_model=ProjectOut)
def delete_building(
    project: Project = Depends(get_project),
    building: Building = Depends(get_building),
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> ProjectOut:
    svc.delete_building(session, building)
    return project_view(session, project, org)


# ------------------------------------------------------------- schedules ---


@router.post(
    "/{project_id}/buildings/{building_id}/schedules",
    response_model=ProjectOut,
    status_code=201,
)
def add_schedule(
    payload: ScheduleIn,
    project: Project = Depends(get_project),
    building: Building = Depends(get_building),
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> ProjectOut:
    try:
        svc.add_schedule(session, building, payload.code)
    except ServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return project_view(session, project, org)


@router.delete("/{project_id}/schedules/{schedule_id}", response_model=ProjectOut)
def archive_schedule(
    project: Project = Depends(get_project),
    schedule: Schedule = Depends(get_schedule),
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> ProjectOut:
    """Remove a schedule from the record. Its data and number are kept."""
    svc.archive_schedule(session, schedule)
    return project_view(session, project, org)


@router.post("/{project_id}/schedules/{schedule_id}/restore", response_model=ProjectOut)
def restore_schedule(
    project: Project = Depends(get_project),
    schedule: Schedule = Depends(get_schedule),
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> ProjectOut:
    svc.restore_schedule(session, schedule)
    return project_view(session, project, org)


@router.get("/{project_id}/archived", response_model=list[ScheduleOut])
def list_archived(
    project: Project = Depends(get_project),
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> list[ScheduleOut]:
    house = svc.house_standard_for(session, org.id)
    scheme = svc.naming_scheme_for(session, org.id)
    out: list[ScheduleOut] = []
    for building in svc.buildings_of(session, project):
        archived = session.scalars(
            select(Schedule).where(
                Schedule.building_id == building.id, Schedule.deleted_marker != ""
            )
        )
        out.extend(schedule_view(session, s, scheme, house) for s in archived)
    return out


# ----------------------------------------------------------- revisions ---


@router.post("/{project_id}/revisions/bulk")
def bulk_revision(
    payload: BulkRevisionIn,
    project: Project = Depends(get_project),
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> dict[str, object]:
    """Append the same revision across many schedules, or preview doing so.

    Each schedule continues **its own** series rather than being forced to a
    shared code, because two schedules on the same job are rarely at the same
    revision and forcing them level would misstate history.
    """
    import datetime as _date

    from ...core.revisions import next_code, sort_key
    from ...db.models import RevisionRow
    from ...services.converters import revisions_of
    from ...services.issue import issue_revision

    wanted = set(payload.schedule_ids)
    targets: list[Schedule] = []
    for building in svc.buildings_of(session, project):
        for schedule in svc.live_schedules(session, building):
            if not wanted or schedule.id in wanted:
                targets.append(schedule)

    if not targets:
        raise HTTPException(status_code=400, detail="no schedules selected")

    planned = [
        {
            "schedule_id": s.id,
            "code": s.code,
            "title": s.schedule_type.title if s.schedule_type else s.code,
            "building": s.building.label,
            "from": (revisions_of(s)[-1].code if revisions_of(s) else "—"),
            "to": next_code(revisions_of(s), published=payload.published),
        }
        for s in targets
    ]

    if not payload.apply:
        return {"applied": 0, "changes": planned}

    issue_date = payload.issue_date or _date.date.today()
    for schedule, plan in zip(targets, planned):
        revision = RevisionRow(
            schedule_id=schedule.id,
            position=max((r.position for r in schedule.revisions), default=-1) + 1,
            code=plan["to"],
            status=payload.status,
            issue_date=issue_date,
            prepared_by=payload.prepared_by,
            checked_by=payload.checked_by,
            approved_by=payload.approved_by,
            description=payload.description,
            sort_key=sort_key(plan["to"]),
        )
        session.add(revision)
        session.flush()
        session.expire(schedule, ["revisions"])
        if payload.issue:
            issue_revision(session, schedule, revision, org)

    return {"applied": len(targets), "changes": planned}


# --------------------------------------------------------- extra columns ---


@router.get("/{project_id}/columns/{type_code}")
def read_project_columns(
    type_code: str,
    project: Project = Depends(get_project),
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> dict[str, object]:
    """The base type's columns and this project's additions to them."""
    from ...core.catalogue import ScheduleType
    from ...services.columns import merged_type, project_extras
    from ...services.converters import type_from_row

    row = session.scalar(
        select(ScheduleTypeRow).where(
            ScheduleTypeRow.organisation_id == org.id,
            ScheduleTypeRow.code == type_code.strip().upper(),
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail=f"no schedule type {type_code!r}")

    base = type_from_row(row)
    return {
        "type_code": base.code,
        "title": base.title,
        "base_columns": [c.to_dict() for c in base.columns],
        "extra_columns": [c.to_dict() for c in project_extras(project, base.code)],
        "merged": [c.to_dict() for c in merged_type(project, base).columns],
    }


@router.put("/{project_id}/columns", response_model=ProjectOut)
def set_project_columns(
    payload: ProjectColumnsIn,
    project: Project = Depends(get_project),
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> ProjectOut:
    """Add columns to one schedule type, for this project only.

    Additions only: the base type's columns cannot be removed or reordered here,
    because two projects' schedules of the same type have to stay comparable.
    Validation runs against the merged list, so a project-specific derived
    column may reference the base columns and is checked properly.
    """
    from ...core.catalogue import Column, validate_type
    from ...services.columns import merged_type, set_project_extras
    from ...services.converters import type_from_row

    row = session.scalar(
        select(ScheduleTypeRow).where(
            ScheduleTypeRow.organisation_id == org.id,
            ScheduleTypeRow.code == payload.type_code.strip().upper(),
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail=f"no schedule type {payload.type_code!r}")

    base = type_from_row(row)
    extras = [Column(**c.model_dump()) for c in payload.columns]

    clash = {c.legacy_name.lower() for c in base.columns} & {
        c.legacy_name.lower() for c in extras
    }
    if clash:
        raise HTTPException(
            status_code=400,
            detail=f"{', '.join(sorted(clash))} already exists on the {base.code} type",
        )

    candidate = base.with_extras(extras)
    errors = [i.message for i in validate_type(candidate) if i.severity == "error"]
    if errors:
        raise HTTPException(status_code=400, detail=errors)

    set_project_extras(project, base.code, extras)
    session.flush()
    return project_view(session, project, org)


# ------------------------------------------------------------- numbering ---


@router.post("/{project_id}/buildings/{building_id}/renumber", response_model=PlanOut)
def renumber(
    payload: RenumberIn,
    building: Building = Depends(get_building),
    session: Session = Depends(get_db),
) -> PlanOut:
    """Preview a renumber operation, or apply it.

    Free-text number editing produces collisions immediately, so it is not
    offered: these five operations plus a plan the user confirms are the whole
    interface.
    """
    try:
        plan = svc.plan_operation(
            session,
            building,
            payload.operation,
            code=payload.code,
            other_code=payload.other_code,
            number=payload.number,
            allow_locked=payload.allow_locked,
        )
    except ServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not payload.apply:
        return _plan_out(plan)

    try:
        applied = svc.apply_plan(session, building, plan)
    except ServiceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _plan_out(plan, applied=applied)


@router.get("/{project_id}/buildings/{building_id}/audit")
def audit_building(
    building: Building = Depends(get_building), session: Session = Depends(get_db)
) -> dict[str, object]:
    issues = svc.run_audit(session, building)
    return {
        "building_id": building.id,
        "building": building.label,
        "issues": [
            {"severity": i.severity, "kind": i.kind, "message": i.message, "code": i.code or ""}
            for i in issues
        ],
    }
