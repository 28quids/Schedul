"""The house standard: everything that varies between firms, in one place.

This is the single most important separation in the codebase for the eventual
product. ISO 19650 already defines the field structure, which is why the naming
looks the way it does; what differs between firms is the token values, the
pattern order and the branding. The product is a configurable implementation of
an existing standard, not a new standard.

Treat any company-specific value found outside this module as a bug. Under the
multi-tenant model each organisation owns exactly one house standard, so a
second firm is a second row, never a fork.
"""

from __future__ import annotations

from dataclasses import dataclass, field as _field
from typing import Any

__all__ = [
    "HouseStandard",
    "DEFAULT_STATUS_CODES",
    "DEFAULT_VOLUME_LOOKUP",
    "DEFAULT_GENERAL_NOTES",
    "DEFAULT_DESIGN_CONSTANTS",
    "DEFAULT_HOUSE_STYLE",
    "DEFAULT_NAMING",
]

#: ISO 19650 suitability codes. Verified against the v1 generator's Lists sheet.
DEFAULT_STATUS_CODES: list[tuple[str, str]] = [
    ("S0", "Work in Progress"),
    ("S1", "Suitable for Coordination"),
    ("S2", "Suitable for Information"),
    ("S3", "Suitable for Review and Comment"),
    ("S4", "Suitable for Stage Approval"),
    ("S5", "Suitable for Client Acceptance"),
    ("A1", "Authorised and Accepted"),
    ("B1", "Partial Sign-off, Accepted with Comments"),
]

#: Volume follows the equipment type, not the project: an AHU is always
#: ventilation. Picking from this list stops anyone typing free text.
DEFAULT_VOLUME_LOOKUP: dict[str, str] = {
    "5.2": "Above ground drainage",
    "5.3": "Domestic services",
    "5.6": "Heating and cooling",
    "5.7": "Ventilation",
}

#: Project-level notes. Rendered above the type's own notes in Schedule A2.
DEFAULT_GENERAL_NOTES: list[str] = [
    "This equipment schedule must be read in conjunction and in compliance with "
    "the associated drawings, specification and design risk assessment.",
    "All equipment is scheduled on a performance basis. The contractor is "
    "responsible for confirming final selections with the manufacturer and for "
    "verifying that the selected equipment meets the scheduled duties.",
    "Dimensions and weights are indicative and must be confirmed against "
    "manufacturer certified drawings prior to builderswork and structural sign-off.",
    "Where a duty or dimension is amended, the contractor shall notify the "
    "designer before ordering.",
    "Calculated columns are derived from the entered design data and the project "
    "design constants. Do not overwrite them.",
]

#: The seven constants derived formulas may reference, keyed by their full name.
#: ``core.formula.CONSTANTS`` maps the SETUP_* aliases onto these.
DEFAULT_DESIGN_CONSTANTS: dict[str, float] = {
    "LPHW Flow Temperature (degC)": 70,
    "LPHW Return Temperature (degC)": 50,
    "CHW Flow Temperature (degC)": 6,
    "CHW Return Temperature (degC)": 12,
    "Design Ambient Temperature (degC)": 21,
    "Specific Heat Capacity of Water (kJ/kgK)": 4.18,
    "EN 442 Radiator Exponent (n)": 1.3,
}

DEFAULT_HOUSE_STYLE: dict[str, Any] = {
    "cover_font": "Verdana",
    "schedule_font": "Arial",
    "title_grey": "FF4D4D4D",
    "title_blue": "FF009DF0",
    "title_size": 30,
    "cover_body_size": 11,
    "schedule_body_size": 8,
    "data_rows": 40,
    "revision_rows": 20,
}

#: Cell colours carrying the meaning of each column kind. See catalogue.py.
DEFAULT_COLOURS: dict[str, str] = {
    "input_fill": "FFFFCC",
    "input_font": "0000FF",
    "library_font": "008000",
    "derived_font": "000000",
}

DEFAULT_NAMING: dict[str, Any] = {
    "pattern": (
        "{project_number}-{originator}-{volume}-{building}-{doc_type}-"
        "{discipline}-{number}-{classification}-{level}-{location}"
    ),
    "separator": "-",
    "suffix": "_-_{title_slug}",
    "tokens": {
        "project_number": {"scope": "project", "value": ""},
        "originator": {"scope": "company", "value": "BOV"},
        "volume": {"scope": "type", "value": "5.6", "filename_value": "5_6"},
        "building": {"scope": "building", "value": "PROJECTNUMBER"},
        "doc_type": {"scope": "project", "value": "SC"},
        "discipline": {"scope": "project", "value": "M"},
        "number": {"scope": "schedule", "width": 8, "start": 10},
        "classification": {"scope": "project", "value": "G00300"},
        "level": {"scope": "project", "value": "XX"},
        "location": {"scope": "project", "value": "XX"},
    },
}


@dataclass(slots=True)
class HouseStandard:
    """One firm's conventions. Owned by an organisation, referenced everywhere."""

    name: str = "Default house standard"
    naming: dict[str, Any] = _field(default_factory=lambda: _deepcopy(DEFAULT_NAMING))
    volume_lookup: dict[str, str] = _field(default_factory=lambda: dict(DEFAULT_VOLUME_LOOKUP))
    status_codes: list[tuple[str, str]] = _field(
        default_factory=lambda: list(DEFAULT_STATUS_CODES)
    )
    revision_codes: dict[str, Any] = _field(
        default_factory=lambda: {"preliminary": "P{nn}", "published": "C{nn}", "max": 20}
    )
    house_style: dict[str, Any] = _field(default_factory=lambda: dict(DEFAULT_HOUSE_STYLE))
    colours: dict[str, str] = _field(default_factory=lambda: dict(DEFAULT_COLOURS))
    general_notes: list[str] = _field(default_factory=lambda: list(DEFAULT_GENERAL_NOTES))
    design_constants: dict[str, float] = _field(
        default_factory=lambda: dict(DEFAULT_DESIGN_CONSTANTS)
    )
    cover_template: str = ""

    def volume_label(self, volume: str) -> str:
        """``'5.7'`` -> ``'Ventilation'``, or the code itself if unknown."""
        return self.volume_lookup.get(volume, volume)

    def status_description(self, code: str) -> str:
        for c, desc in self.status_codes:
            if c == code:
                return desc
        return ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "naming": self.naming,
            "volume_lookup": self.volume_lookup,
            "status_codes": [list(pair) for pair in self.status_codes],
            "revision_codes": self.revision_codes,
            "house_style": self.house_style,
            "colours": self.colours,
            "general_notes": self.general_notes,
            "design_constants": self.design_constants,
            "cover_template": self.cover_template,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HouseStandard":
        return cls(
            name=data.get("name", "Default house standard"),
            naming=data.get("naming") or _deepcopy(DEFAULT_NAMING),
            volume_lookup=data.get("volume_lookup") or dict(DEFAULT_VOLUME_LOOKUP),
            status_codes=[tuple(p) for p in data.get("status_codes", DEFAULT_STATUS_CODES)],
            revision_codes=data.get("revision_codes")
            or {"preliminary": "P{nn}", "published": "C{nn}", "max": 20},
            house_style=data.get("house_style") or dict(DEFAULT_HOUSE_STYLE),
            colours=data.get("colours") or dict(DEFAULT_COLOURS),
            general_notes=list(data.get("general_notes", DEFAULT_GENERAL_NOTES)),
            design_constants=dict(data.get("design_constants", DEFAULT_DESIGN_CONSTANTS)),
            cover_template=data.get("cover_template", ""),
        )


def _deepcopy(value: Any) -> Any:
    import copy

    return copy.deepcopy(value)
