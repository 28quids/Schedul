"""Organisation settings: the house standard.

Everything that varies between firms lives here and nowhere else, which is what
makes a second organisation a profile rather than a fork.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core.house import HouseStandard
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
        house.branding = payload.branding

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
