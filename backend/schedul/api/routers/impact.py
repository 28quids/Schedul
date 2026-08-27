"""The change log: what has moved, and what it lands on.

One page for the changes that reach across documents -- schedule types, the
equipment library, the house standard and its branding -- because each of them
is already recorded where it happened and nowhere a person would look.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ...db.models import Organisation
from ...services import impact as impact_svc
from ..deps import current_org, get_db

router = APIRouter(prefix="/api/impact", tags=["impact"])


@router.get("")
def read_log(
    limit: int = 60,
    area: str = "",
    session: Session = Depends(get_db),
    org: Organisation = Depends(current_org),
) -> dict[str, object]:
    """Recent changes, newest first, plus schedules behind their type version."""
    return impact_svc.change_log(session, org.id, limit=limit, area=area)
