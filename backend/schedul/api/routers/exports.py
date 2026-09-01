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
from ...services.columns import columns_for
from ...services.converters import design_constants_for, revisions_of, type_from_row
from ...services.grid import library_index
from ...services import notes as notes_svc
from ..deps import current_org, get_db, get_project, get_schedule, not_found
from ..schemas import RegisterRow

router = APIRouter(prefix="/api", tags=["export"])

#: 'issue' is a document being sent to somebody: plain, no editing aids.
#: 'editor' keeps the yellow input fill and the colour contract, for a working
#: copy. The default is 'issue' because that is what an export is usually for.
THEMES = ("issue", "editor")


def _check_theme(theme: str) -> None:
    if theme not in THEMES:
        raise HTTPException(
            status_code=400,
            detail=f"theme must be one of {', '.join(THEMES)}",
        )


def _content(
    session: Session,
    schedule: Schedule,
    org: Organisation,
    *,
    target: str = "xlsx",
    theme: str = "issue",
) -> ScheduleContent:
    building = schedule.building
    project = building.project
    house = svc.house_standard_for(session, org.id)
    scheme = svc.naming_scheme_for(session, org.id)
    # Columns hidden from this target never reach the deliverable, which is how
    # a practice keeps internal data such as Price off an issued document.
    stype = columns_for(schedule, target=target)

    try:
        docnum = svc.document_number_for(schedule, scheme, house=house)
    except NamingError:
        docnum = schedule.docnum

    products = library_index(session, org.id, stype.code)
    product_rows = [{"Model Reference": ref, **values} for ref, values in products.items()]

    tokens = {**(project.naming_overrides or {})}
    resolved = notes_svc.resolved_notes(schedule, stype, house, project)
    return ScheduleContent(
        notes=[n.text for n in resolved],
        branding_overrides=project.branding_overrides or {},
        schedule_type=stype,
        house=house,
        project_fields=project.project_fields,
        design_constants=design_constants_for(project, house),
        docnum=docnum,
        building_ref=building.ref,
        building_name=building.name,
        rows=[dict(r.values or {}) for r in schedule.rows],
        overrides=[dict(r.overrides or {}) for r in schedule.rows],
        theme=theme,
        revisions=revisions_of(schedule),
        products=product_rows,
        doc_type=str(tokens.get("doc_type") or scheme.tokens["doc_type"].value),
        classification=str(
            tokens.get("classification") or scheme.tokens["classification"].value
        ),
    )


def _filename(session: Session, schedule: Schedule, org: Organisation) -> str:
    house = svc.house_standard_for(session, org.id)
    scheme = svc.scheme_for(house)
    try:
        return svc.filename_for(schedule, scheme, house=house)
    except NamingError:
        return f"{schedule.code}_{schedule.number}.xlsx"


@router.get("/schedules/{schedule_id}/export.xlsx")
def export_schedule_xlsx(
    theme: str = "issue",
    schedule: Schedule = Depends(get_schedule),
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> FileResponse:
    """The workbook, in the issue theme unless a working copy is asked for.

    A file that leaves the office is read, not filled in, so it defaults to the
    neutral print theme: no yellow input fill, no blue-green-black colour
    contract. ``?theme=editor`` gives the working colours back for somebody who
    wants the file to look like the screen they were typing into.
    """
    _check_theme(theme)
    tmp = Path(tempfile.mkdtemp(prefix="schedul-"))
    name = _filename(session, schedule, org)
    path = render_schedule(_content(session, schedule, org, theme=theme), tmp / name)
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
    xlsx = render_schedule(
        _content(session, schedule, org, target="pdf", theme="issue"), tmp / name
    )
    try:
        produced = pdf_export.to_pdf(xlsx, tmp)
    except pdf_export.PdfError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return FileResponse(produced, filename=produced.name, media_type="application/pdf")


@router.get("/schedules/{schedule_id}/revisions/{revision_id}/export.xlsx")
def export_issued_revision(
    revision_id: str,
    schedule: Schedule = Depends(get_schedule),
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> FileResponse:
    """Re-issue exactly what went out, from the snapshot rather than live data."""
    from ...core.catalogue import Column, ScheduleType
    from ...db.models import RevisionRow

    revision = session.get(RevisionRow, revision_id)
    if revision is None or revision.schedule_id != schedule.id:
        raise not_found("revision")
    if revision.snapshot is None:
        raise HTTPException(
            status_code=404,
            detail=f"revision {revision.code} was never issued, so there is no frozen copy",
        )

    snap = revision.snapshot
    house = svc.house_standard_for(session, org.id)
    house.general_notes = list(snap.get("notes") or [])

    stype = ScheduleType(
        code=snap.get("type_code", schedule.code),
        title=snap.get("type_title", schedule.code),
        version=snap.get("type_version", 1),
        columns=[Column.from_dict(c) for c in snap.get("columns", [])],
        notes=[],
    )

    content = ScheduleContent(
        schedule_type=stype,
        house=house,
        project_fields=snap.get("project_fields") or {},
        design_constants=snap.get("design_constants") or {},
        docnum=snap.get("docnum", ""),
        building_ref=str(snap.get("building", "")).split(" - ")[0],
        building_name=(
            str(snap.get("building", "")).split(" - ", 1)[1]
            if " - " in str(snap.get("building", "")) else ""
        ),
        rows=[r.get("values", {}) for r in snap.get("rows", [])],
        overrides=[r.get("overrides", {}) for r in snap.get("rows", [])],
        computed=[r.get("computed", {}) for r in snap.get("rows", [])],
        frozen=True,
        revisions=revisions_of(schedule),
        theme="pdf",
    )

    tmp = Path(tempfile.mkdtemp(prefix="schedul-rev-"))
    name = f"{snap.get('docnum') or schedule.code}_{revision.code}.xlsx"
    path = render_schedule(content, tmp / name)
    return FileResponse(
        path, filename=name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@router.get("/projects/{project_id}/export.zip")
def export_project_zip(
    fmt: str = "xlsx",
    theme: str = "issue",
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
    _check_theme(theme)
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
            xlsx = render_schedule(
                _content(
                    session, schedule, org,
                    target="pdf" if fmt == "pdf" else "xlsx",
                    theme="issue" if fmt == "pdf" else theme,
                ),
                target / name,
            )
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
                    docnum = svc.document_number_for(schedule, scheme, house=house)
                    filename = svc.filename_for(schedule, scheme, house=house)
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


ROOM_HINTS = ("room number", "room", "space", "room served", "location", "area served")


@router.get("/projects/{project_id}/rooms")
def room_summary(
    project: Project = Depends(get_project),
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> dict[str, object]:
    """Equipment grouped by the room or space it serves.

    Answers "what is in RM8.64", which is the question the schedules already
    hold the answer to but cannot be asked of them one file at a time. Which
    column names a room varies by type, so the first input column whose name
    looks like one is used, and which column was chosen is reported rather than
    hidden -- a wrong guess should be visible.
    """
    from ...services.columns import columns_for

    rooms: dict[str, list[dict[str, object]]] = {}
    used_columns: dict[str, str] = {}
    unassigned = 0

    for building in svc.buildings_of(session, project):
        for schedule in svc.live_schedules(session, building):
            stype = columns_for(schedule)
            room_column = _room_column(stype)
            if room_column is None:
                continue
            used_columns[schedule.code] = room_column.name

            reference = stype.inputs[0].legacy_name if stype.inputs else None
            for row in schedule.rows:
                values = row.values or {}
                room = str(values.get(room_column.legacy_name, "") or "").strip()
                if not room:
                    if any(v not in (None, "") for v in values.values()):
                        unassigned += 1
                    continue
                rooms.setdefault(room, []).append(
                    {
                        "building": building.label,
                        "schedule_id": schedule.id,
                        "code": schedule.code,
                        "title": schedule.schedule_type.title if schedule.schedule_type else "",
                        "reference": values.get(reference) if reference else None,
                        "model_reference": values.get("Model Reference") or "",
                    }
                )

    return {
        "project": project.name or project.number,
        "room_columns": used_columns,
        "unassigned": unassigned,
        "rooms": [
            {
                "room": room,
                "count": len(items),
                "by_type": _counted(items),
                "items": sorted(items, key=lambda i: (i["code"], str(i["reference"] or ""))),
            }
            for room, items in sorted(rooms.items(), key=lambda kv: _natural(kv[0]))
        ],
    }


def _room_column(stype):
    """The input column that names a room, or None if this type has no such thing."""
    for hint in ROOM_HINTS:
        for column in stype.inputs:
            if column.name.strip().lower() == hint:
                return column
    for column in stype.inputs:
        if "room" in column.name.lower() or "space" in column.name.lower():
            return column
    return None


def _counted(items: list[dict[str, object]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        counts[str(item["code"])] = counts.get(str(item["code"]), 0) + 1
    return dict(sorted(counts.items()))


def _natural(text: str) -> tuple:
    """Sort 'RM2' before 'RM10', which plain string ordering gets backwards."""
    import re

    return tuple(
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r"(\d+)", text)
    )


@router.get("/export/pdf-available")
def pdf_available() -> dict[str, object]:
    return {"available": pdf_export.available(), "soffice": pdf_export.soffice_path()}
