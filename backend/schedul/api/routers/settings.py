"""Organisation settings: the house standard.

Everything that varies between firms lives here and nowhere else, which is what
makes a second organisation a profile rather than a fork.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core.house import HouseStandard
from ...core.naming import NamingScheme
from ...db.models import HouseStandardRow, Organisation
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

    if row is None:
        row = HouseStandardRow(organisation_id=org.id)
        session.add(row)
    row.name = house.name
    row.data = house.to_dict()
    session.flush()

    scheme = NamingScheme.from_dict(house.naming)
    return {
        "house_standard": house.to_dict(),
        "naming_problems": scheme.validate(),
        "pattern_tokens": scheme.pattern_tokens,
    }
