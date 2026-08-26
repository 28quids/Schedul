"""Creating an organisation with a working catalogue.

Every new tenant starts from the same place: a house standard and the eight
equipment types migrated from v1, plus the Radiant Panel type SPEC.md 1a
describes as a ninth.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.catalogue import Column, ScheduleType
from ..core.house import HouseStandard
from ..core.migrate import VOLUME_BY_CODE, import_schema
from ..db.models import HouseStandardRow, Organisation, ScheduleTypeRow
from .converters import type_to_row_fields

__all__ = ["radiant_panel_type", "seed_organisation", "ensure_default_organisation"]

VENDOR_SCHEMA = Path(__file__).resolve().parents[3] / "vendor" / "schema.json"


def radiant_panel_type() -> ScheduleType:
    """The ninth catalogue type, from the real house file (SPEC.md 1a).

    The hand-made original was not supplied, so this is reconstructed from the
    column list and notes the spec records. Its notes are the example of why
    notes are per equipment type rather than per project: they are
    radiant-panel-specific wording, not generic compliance text.
    """
    return ScheduleType(
        code="RADPANEL",
        title="Radiant Panel Schedule",
        short="Radiant Panels",
        volume=VOLUME_BY_CODE["RADPANEL"],
        columns=[
            Column("input", "Ref", width=12, example="RP-01"),
            Column("input", "Level", width=12, example="Level 02"),
            Column("input", "Room Number", width=14, example="2.14"),
            Column("input", "Location", width=22, example="Open Plan Office"),
            Column(
                "input", "Room Setpoint", unit="degC Dry Resultant", width=16, example=21
            ),
            Column("input", "Minimum Heat Required", unit="W", width=16, example=1200),
            Column("library", "Radiant Panel Type", width=20, example="MRP-600"),
            Column("input", "Quantity", width=10, example=2),
            Column("library", "Height", unit="mm", width=12, example=600),
            Column("library", "Length", unit="mm", width=12, example=3000),
            Column("library", "Depth", unit="mm", width=12, example=50),
            Column("library", "Panel output", unit="W", width=14, example=700),
            Column(
                "library", "Flow Rate Per Panel", unit="Kg/s", width=16, example=0.008
            ),
            Column(
                "library",
                "Pressure drop in each panel",
                unit="kPa",
                width=18,
                example=3.5,
            ),
            Column(
                "derived",
                "Total Output",
                unit="W",
                width=14,
                formula="={Quantity}*{Panel output (W)}",
                note="Scheduled quantity multiplied by the selected panel's output",
            ),
            Column(
                "derived",
                "Output Check",
                width=14,
                formula=(
                    '=IF({Quantity}*{Panel output (W)}>={Minimum Heat Required (W)},'
                    '"OK","UNDERSIZED")'
                ),
                note="Compares the scheduled output against the room's heat requirement",
            ),
            Column("input", "Notes", width=28, example=""),
        ],
        notes=[
            "Where radiant panels are fed by the LTHW system they are to be sized "
            "with a 55degC flow and 45degC return temperature.",
            "LTHW radiant panels to be Merriott or equal and approved.",
            "Panels are to be installed at the heights indicated on the associated "
            "drawings; deviations are to be agreed with the designer.",
            "Panel outputs are quoted at the scheduled flow and return temperatures "
            "and are to be confirmed against manufacturer certified data.",
        ],
    )


def seed_organisation(
    session: Session,
    name: str,
    slug: str,
    *,
    schema_path: Path | None = None,
    include_radiant_panel: bool = True,
) -> Organisation:
    """Create an organisation with a house standard and a full catalogue."""
    org = Organisation(name=name, slug=slug)
    session.add(org)
    session.flush()

    house = HouseStandard(name=f"{name} house standard")
    session.add(
        HouseStandardRow(organisation_id=org.id, name=house.name, data=house.to_dict())
    )

    path = schema_path or VENDOR_SCHEMA
    types: list[ScheduleType] = list(import_schema(path)) if path.exists() else []
    if include_radiant_panel:
        types.append(radiant_panel_type())

    for st in types:
        session.add(ScheduleTypeRow(organisation_id=org.id, **type_to_row_fields(st)))

    session.flush()
    return org


def ensure_default_organisation(session: Session) -> Organisation:
    """The single tenant a local install runs as.

    Multi-tenant in the schema, single-tenant in practice until there is a
    reason not to be.
    """
    org = session.scalar(select(Organisation).order_by(Organisation.created_at))
    if org is not None:
        return org
    return seed_organisation(session, "My Practice", "default")
