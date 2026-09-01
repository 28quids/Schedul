"""Organisation settings: the house standard.

Everything that varies between firms lives here and nowhere else, which is what
makes a second organisation a profile rather than a fork.
"""

from __future__ import annotations

import datetime as _dt
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core.branding import (
    COVER_FIELDS, REVISION_FIELDS, SAFE_FONTS, Branding, validate_branding,
)
from ...core.house import DEFAULT_GENERAL_NOTES, HouseStandard
from ...core.naming import NamingScheme
from ...db.models import HouseStandardRow, Organisation
from ...services import impact as impact_svc
from ...services import projects as svc
from ..deps import current_org, get_db
from ..schemas import HouseStandardIn

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
def read_settings(
    session: Session = Depends(get_db), org: Organisation = Depends(current_org)
) -> dict[str, object]:
    house = svc.house_standard_for(session, org.id)
    scheme = NamingScheme.from_dict(house.naming)
    return {
        "organisation": {"id": org.id, "name": org.name, "slug": org.slug},
        "house_standard": house.to_dict(),
        "naming_problems": scheme.validate(),
        "pattern_tokens": scheme.pattern_tokens,
        # What a fresh practice starts with, so a screen that has emptied its
        # notes has somewhere to get them back from. Stored notes and the
        # built-in wording are different things, and once the stored list is an
        # empty one the defaults never reappear on their own -- deliberately, as
        # "we print no notes" is a real answer -- so the way back has to be an
        # offer rather than a fallback.
        "default_general_notes": list(DEFAULT_GENERAL_NOTES),
    }


# ----------------------------------------------------------- where it lives ---


def _sqlite_path(url: str) -> Path | None:
    """The file a SQLite URL points at, or None for a database server.

    Read from the URL the engine actually uses rather than from the default
    directory, so a ``SCHEDUL_DATABASE_URL`` pointing somewhere else is reported
    honestly instead of being papered over with where it would have been.
    """
    if not url.startswith("sqlite"):
        return None
    _, _, tail = url.partition(":///")
    return Path(tail) if tail else None


@router.get("/storage")
def read_storage() -> dict[str, object]:
    """Where the record is kept, and whether it survived the last update.

    Worth a screen of its own because the answer used to be "inside the folder
    you downloaded", and updating by downloading a fresh copy therefore looked
    exactly like losing everything. Somebody who has been bitten by that once
    needs to be able to see the path.
    """
    from ...db import session as db_session

    url = db_session.database_url()
    path = _sqlite_path(url)
    external = path is None
    legacy = db_session.legacy_data_dir() / "schedul.db"

    return {
        "external": external,
        "database_url": url if external else "",
        "directory": str(path.parent) if path else "",
        "database": str(path) if path else "",
        "exists": bool(path and path.exists()),
        "size_bytes": path.stat().st_size if (path and path.exists()) else 0,
        # Reported rather than assumed: if a copy is still sitting in the old
        # in-checkout location, say so, because it is the thing somebody will
        # want to check against before deleting the folder it is in.
        "legacy_copy": str(legacy) if legacy.exists() else "",
        "override_env": "SCHEDUL_DATA",
    }


@router.get("/backup.db")
def download_backup() -> FileResponse:
    """A consistent copy of the database, to keep somewhere else.

    Taken through SQLite's own backup API rather than by copying the file: a
    plain copy of a database being written to is a copy that may not open, and a
    backup nobody can restore is worse than no backup because of what it is
    believed to be.
    """
    import sqlite3

    from ...db import session as db_session

    source = _sqlite_path(db_session.database_url())
    if source is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "this instance is on a database server rather than a file, so a "
                "backup is the server's own job"
            ),
        )
    if not source.exists():
        raise HTTPException(status_code=404, detail="there is no database yet")

    stamp = _dt.datetime.now().strftime("%Y-%m-%d")
    target = Path(tempfile.mkdtemp(prefix="schedul-backup-")) / f"schedul-{stamp}.db"
    with sqlite3.connect(source) as live, sqlite3.connect(target) as copy:
        live.backup(copy)
    return FileResponse(
        target, filename=target.name, media_type="application/octet-stream"
    )


@router.get("/branding")
def read_branding(
    session: Session = Depends(get_db), org: Organisation = Depends(current_org)
) -> dict[str, object]:
    """The practice's branding, and what can be configured about a document.

    The field lists come from the domain rather than being written out in the
    browser, so a field the workbook reads by formula is shown as fixed in the
    same breath as being listed -- there is no way to offer a switch that would
    produce a broken document.
    """
    house = svc.house_standard_for(session, org.id)
    branding = Branding.from_dict(house.branding)
    return {
        "branding": branding.to_dict(),
        "fonts": list(SAFE_FONTS),
        "cover_fields": [
            {"key": f.key, "label": f.label, "optional": f.optional, "hint": f.hint}
            for f in COVER_FIELDS
        ],
        "revision_fields": [
            {"key": f.key, "label": f.label, "optional": f.optional, "hint": f.hint}
            for f in REVISION_FIELDS
        ],
        "preview": _branding_preview(branding),
    }


def _branding_preview(branding: Branding) -> dict[str, object]:
    """What the cover and the revision page would carry, without rendering one.

    A faithful list of the rows in the order they would appear, so the settings
    screen can show the effect of a change without generating a file for every
    keystroke. It is the same resolution the renderer uses, so what it shows is
    what would be produced.
    """
    return {
        "cover": [
            {"key": f.key, "label": f.label} for f in branding.cover_layout()
        ],
        "revision": [
            {"key": f.key, "label": f.label} for f in branding.revision_layout()
        ],
        "cover_font": branding.cover_font,
        "schedule_font": branding.schedule_font,
        "title_size": branding.title_size,
        "palette": dict(branding.palette),
        "has_logo": bool(branding.logo),
        "cover_subtitle": branding.cover_subtitle,
        "cover_footer": branding.cover_footer,
    }


@router.post("/branding/preview")
def preview_branding(
    payload: HouseStandardIn,
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> dict[str, object]:
    """What a branding change would produce, without saving it."""
    branding = Branding.from_dict(payload.branding or {})
    return {
        "problems": validate_branding(payload.branding or {}),
        "preview": _branding_preview(branding),
    }


@router.put("")
def update_settings(
    payload: HouseStandardIn,
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> dict[str, object]:
    row = session.scalar(
        select(HouseStandardRow).where(HouseStandardRow.organisation_id == org.id)
    )
    house = svc.house_standard_for(session, org.id)
    # What it said before, so the change log can say what moved. The house
    # standard reaches every document in the practice, so "the notes changed"
    # is not a footnote -- it is the reason a schedule prints differently.
    was = house.to_dict()

    if payload.name is not None:
        house.name = payload.name
    if payload.naming is not None:
        house.naming = payload.naming
    if payload.general_notes is not None:
        house.general_notes = payload.general_notes
    if payload.design_constants is not None:
        house.design_constants = payload.design_constants
    if payload.house_style is not None:
        house.house_style = {**house.house_style, **payload.house_style}
    if payload.volume_lookup is not None:
        house.volume_lookup = payload.volume_lookup
    if payload.status_codes is not None:
        house.status_codes = [tuple(p) for p in payload.status_codes]
    if payload.volume_discipline is not None:
        house.volume_discipline = payload.volume_discipline
    if payload.numbering_scope is not None:
        if payload.numbering_scope not in ("building", "building_volume"):
            raise HTTPException(
                status_code=400,
                detail="numbering_scope must be 'building' or 'building_volume'",
            )
        house.numbering_scope = payload.numbering_scope
    if payload.branding is not None:
        problems = validate_branding(payload.branding)
        if problems:
            raise HTTPException(status_code=400, detail=problems)
        house.branding = payload.branding
        # Branding owns the fonts and the two title colours; the house style
        # holds them because that is what the renderer reads. Keeping them in
        # step here means there is still one answer to "what font is this in".
        house.house_style = {
            **house.house_style,
            **Branding.from_dict(payload.branding).house_style_overrides(),
        }

    if row is None:
        row = HouseStandardRow(organisation_id=org.id)
        session.add(row)
    row.name = house.name
    row.data = house.to_dict()
    session.flush()
    _record_changes(session, org.id, was, row.data)

    scheme = NamingScheme.from_dict(house.naming)
    return {
        "house_standard": house.to_dict(),
        "naming_problems": scheme.validate(),
        "pattern_tokens": scheme.pattern_tokens,
    }


#: Which part of the house standard a field belongs to, for the change log.
_AREAS: dict[str, tuple[str, str]] = {
    "general_notes": ("notes", "the notes printed on every schedule"),
    "branding": ("branding", "the organisation's branding"),
    "house_style": ("branding", "the fonts and sizes documents are set in"),
    "naming": ("numbering", "the document numbering pattern"),
    "numbering_scope": ("numbering", "how numbers are allocated"),
    "volume_lookup": ("numbering", "the volume list"),
    "volume_discipline": ("numbering", "which discipline each volume implies"),
    "design_constants": ("type", "the design constants derived columns calculate from"),
    "status_codes": ("export", "the suitability codes"),
}


def _record_changes(
    session: Session, organisation_id: str, was: dict, now: dict
) -> None:
    """Record each part of the house standard that actually moved.

    Field by field rather than one 'settings saved' line: the useful question is
    "did the branding change", and an entry that cannot answer it is noise.
    """
    for key, (area, description) in _AREAS.items():
        if was.get(key) == now.get(key):
            continue
        impact_svc.record(
            session,
            organisation_id,
            area,
            subject=key,
            summary=f"Changed {description}",
            # Everything here reaches every document; notes and branding also
            # change what an unissued one prints, which is worth flagging.
            severity="warn" if area in ("notes", "branding") else "info",
            detail={"field": key},
        )
