"""Producing the deliverables: .xlsx, PDF, MAINPROJECTINFO, and the register.

The register is the "read table" the whole tool is arranged around: every
schedule in a project with its filename, current revision, issue date and
status. Under v1 it was a Power Query scrape of Metadata sheets across a folder,
refreshed by hand. It is now a query.
"""

from __future__ import annotations

import datetime as _dt
import shutil
import tempfile
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core.naming import NamingError, slug
from ...core.revisions import current as current_revision
from ...db.models import Building, Organisation, Project, Schedule
from ...export import pdf as pdf_export
from ...export.projectinfo import render_project_info
from ...export.schedule import ScheduleContent, render_schedule
from ...services import projects as svc
from ...services.converters import design_constants_for, revisions_of, type_from_row
from ...services.grid import library_index
from ..deps import current_org, get_db, get_project, get_schedule, not_found
from ..schemas import RegisterRow

router = APIRouter(prefix="/api", tags=["export"])


def _content(session: Session, schedule: Schedule, org: Organisation) -> ScheduleContent:
    building = schedule.building
    project = building.project
    house = svc.house_standard_for(session, org.id)
    scheme = svc.naming_scheme_for(session, org.id)
    stype = type_from_row(schedule.schedule_type)

    try:
        docnum = svc.document_number_for(schedule, scheme)
    except NamingError:
        docnum = schedule.docnum

    products = library_index(session, org.id, stype.code)
    product_rows = [{"Model Reference": ref, **values} for ref, values in products.items()]

    tokens = {**(project.naming_overrides or {})}
    return ScheduleContent(
        schedule_type=stype,
        house=house,
        project_fields=project.project_fields,
        design_constants=design_constants_for(project, house),
        docnum=docnum,
        building_ref=building.ref,
        building_name=building.name,
        rows=[dict(r.values or {}) for r in schedule.rows],
        revisions=revisions_of(schedule),
        products=product_rows,
        doc_type=str(tokens.get("doc_type") or scheme.tokens["doc_type"].value),
        classification=str(
            tokens.get("classification") or scheme.tokens["classification"].value
        ),
    )


def _filename(session: Session, schedule: Schedule, org: Organisation) -> str:
    scheme = svc.naming_scheme_for(session, org.id)
    try:
        return svc.filename_for(schedule, scheme)
    except NamingError:
        return f"{schedule.code}_{schedule.number}.xlsx"


@router.get("/schedules/{schedule_id}/export.xlsx")
def export_schedule_xlsx(
    schedule: Schedule = Depends(get_schedule),
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> FileResponse:
    tmp = Path(tempfile.mkdtemp(prefix="schedul-"))
    name = _filename(session, schedule, org)
    path = render_schedule(_content(session, schedule, org), tmp / name)
    return FileResponse(
        path,
        filename=name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@router.get("/schedules/{schedule_id}/export.pdf")
def export_schedule_pdf(
    schedule: Schedule = Depends(get_schedule),
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> FileResponse:
    if not pdf_export.available():
        raise HTTPException(
            status_code=503,
            detail=(
                "PDF export needs LibreOffice, which was not found on this machine. "
                "The Excel export works, and prints to PDF from Excel."
            ),
        )
    tmp = Path(tempfile.mkdtemp(prefix="schedul-"))
    name = _filename(session, schedule, org)
    xlsx = render_schedule(_content(session, schedule, org), tmp / name)
    try:
        produced = pdf_export.to_pdf(xlsx, tmp)
    except pdf_export.PdfError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return FileResponse(produced, filename=produced.name, media_type="application/pdf")


@router.get("/projects/{project_id}/export.zip")
def export_project_zip(
    fmt: str = "xlsx",
    project: Project = Depends(get_project),
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> FileResponse:
    """A whole project as a zip, laid out the way the folder convention expects.

    Files sit directly in ``Schedules/`` for a single-building project and in
    ``Schedules/<ref>/`` when there are several -- SPEC.md 4.3.1. Because the
    layout is decided here from current data rather than migrated on disk, the
    promotion trap that section describes cannot happen.
    """
    if fmt == "pdf" and not pdf_export.available():
        raise HTTPException(status_code=503, detail="LibreOffice was not found")

    buildings = svc.buildings_of(session, project)
    multi = len(buildings) > 1
    tmp = Path(tempfile.mkdtemp(prefix="schedul-zip-"))
    staging = tmp / "Schedules"
    staging.mkdir(parents=True)

    produced_any = False
    for building in buildings:
        target = staging / building.ref if multi else staging
        target.mkdir(parents=True, exist_ok=True)
        for schedule in svc.live_schedules(session, building):
            name = _filename(session, schedule, org)
            xlsx = render_schedule(_content(session, schedule, org), target / name)
            if fmt == "pdf":
                try:
                    pdf_export.to_pdf(xlsx, target)
                finally:
                    xlsx.unlink(missing_ok=True)
            produced_any = True

    render_project_info(session, project, org, staging / "MAINPROJECTINFO.xlsx")

    if not produced_any:
        raise HTTPException(status_code=400, detail="this project has no schedules yet")

    archive_name = slug(project.number or project.name or "project") + "_schedules"
    archive = tmp / f"{archive_name}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(staging.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(tmp))
    return FileResponse(archive, filename=archive.name, media_type="application/zip")


@router.get("/projects/{project_id}/projectinfo.xlsx")
def export_project_info(
    project: Project = Depends(get_project),
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> FileResponse:
    tmp = Path(tempfile.mkdtemp(prefix="schedul-"))
    path = render_project_info(session, project, org, tmp / "MAINPROJECTINFO.xlsx")
    return FileResponse(
        path,
        filename="MAINPROJECTINFO.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# --------------------------------------------------------------- register ---


@router.get("/register", response_model=list[RegisterRow])
def register(
    project_id: str | None = None,
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> list[RegisterRow]:
    """Every schedule, with its current revision, issue date and status."""
    house = svc.house_standard_for(session, org.id)
    scheme = svc.naming_scheme_for(session, org.id)

    stmt = select(Project).where(Project.organisation_id == org.id)
    if project_id:
        stmt = stmt.where(Project.id == project_id)

    rows: list[RegisterRow] = []
    for project in session.scalars(stmt):
        for building in svc.buildings_of(session, project):
            for schedule in svc.live_schedules(session, building):
                try:
                    docnum = svc.document_number_for(schedule, scheme)
                    filename = svc.filename_for(schedule, scheme)
                except NamingError:
                    docnum, filename = schedule.docnum, ""

                latest = current_revision(revisions_of(schedule))
                status_code, status_desc = "", ""
                if latest and latest.status:
                    text = str(latest.status)
                    if " - " in text:
                        status_code, status_desc = text.split(" - ", 1)
                    else:
                        status_code = text
                        status_desc = house.status_description(text)

                populated = sum(
                    1
                    for r in schedule.rows
                    if any(v not in (None, "") for v in (r.values or {}).values())
                )
                rows.append(
                    RegisterRow(
                        project_id=project.id,
                        project_name=project.name or project.number,
                        project_number=project.number,
                        building_id=building.id,
                        building=building.label,
                        schedule_id=schedule.id,
                        code=schedule.code,
                        document_number=docnum,
                        schedule_name=(
                            schedule.schedule_type.title
                            if schedule.schedule_type
                            else schedule.code
                        ),
                        file_name=filename,
                        revision=latest.code if latest else "",
                        issue_date=latest.date if latest else None,
                        status=status_code,
                        status_description=status_desc,
                        row_count=populated,
                        state=schedule.state,
                    )
                )
    return rows


@router.get("/export/pdf-available")
def pdf_available() -> dict[str, object]:
    return {"available": pdf_export.available(), "soffice": pdf_export.soffice_path()}
