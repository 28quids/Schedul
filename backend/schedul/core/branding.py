"""The organisation's branding, and what the cover and revision page carry.

Two things live here, and they are the same thing seen twice: what a practice's
documents look like, and which facts they show. Both are house standard --
settings on the organisation, not decoration applied per file -- because the
whole point is that every schedule that leaves the office looks like it came
from the same place.

**Configuration, not a canvas.** SPEC.md's warning is that the hand-made
branded originals contain drawing objects openpyxl cannot round-trip, so
anything that pretends to be a freeform document designer would either lose them
or lie about what it produced. What is offered instead is a set of decisions the
renderer can honestly carry out: which fields appear, in what order, in which
fonts and colours, and where a logo sits. Hiding Building Number is a checkbox;
moving it two inches left is not on offer, and saying so is better than
half-doing it.

The renderer asks this module what to draw and never guesses. A field a practice
has hidden is not written at all, and one nothing else depends on can be hidden
freely -- the module knows which those are, because the workbook's own formulas
reference some of them.
"""

from __future__ import annotations

from dataclasses import dataclass, field as _field
from typing import Any, Iterable, Sequence

__all__ = [
    "SAFE_FONTS",
    "COVER_FIELDS",
    "REVISION_FIELDS",
    "PROJECT_KEYS",
    "Branding",
    "Field",
    "resolve_fields",
    "validate_branding",
    "with_project_overrides",
]

#: The parts of branding a single job may answer differently.
#:
#: Which fields a document carries is a decision about a document, and two jobs
#: legitimately differ: one has buildings and a client who wants a BSUID, the
#: next has neither. Fonts, colours and the logo are not on this list and are
#: not meant to be -- the whole point of a house standard is that every document
#: that leaves the office looks like it came from the same place, and a
#: per-project typeface would quietly end that.
PROJECT_KEYS: tuple[str, ...] = (
    "cover_fields",
    "cover_order",
    "revision_fields",
    "revision_order",
    "cover_subtitle",
)

#: Fonts a document may be set in.
#:
#: A short list on purpose. A font the recipient does not have is substituted by
#: whatever their machine decides, which is how a careful layout becomes a
#: ragged one on somebody else's screen -- so the choice is limited to faces
#: that ship with Windows, macOS and LibreOffice alike.
SAFE_FONTS: tuple[str, ...] = (
    "Arial",
    "Calibri",
    "Verdana",
    "Tahoma",
    "Trebuchet MS",
    "Georgia",
    "Times New Roman",
    "Courier New",
)


@dataclass(frozen=True, slots=True)
class Field:
    """One line the cover or the revision page can show."""

    key: str
    label: str
    #: False when the workbook's own formulas read this row, so hiding it would
    #: break something rather than just tidy the page.
    optional: bool = True
    #: What it is for, shown beside the checkbox.
    hint: str = ""


#: The revision page's summary block, in its default order.
#:
#: The entries marked not optional are the ones the cover and the Metadata sheet
#: read by formula. They stay, and the settings screen shows them as fixed
#: rather than offering a switch that would produce a broken workbook.
REVISION_FIELDS: tuple[Field, ...] = (
    Field("project_name", "Project Name", False, "Reads from the project."),
    Field("project_number", "Project no.", True, "The job number."),
    Field("building", "Building", True, "Hide this on a single-building job."),
    Field("recipient", "Recipient", True, "The client the document is issued to."),
    Field("document_type", "Document type", True, "SC for a schedule."),
    Field("revision", "Revision", False, "The cover reads this row."),
    Field("date", "Date", False, "The cover reads this row."),
    Field("prepared_by", "Prepared by", True, ""),
    Field("checked_by", "Checked by", True, ""),
    Field("approved_by", "Approved by", True, ""),
    Field("document_number", "Document no", False, "The cover and Metadata read this row."),
    Field("status", "Suitability Status", False, "The cover and Metadata read this row."),
    Field("status_description", "Suitability Description", False, "Metadata reads this row."),
    Field("schedule_name", "Schedule name", False, "Metadata reads this row."),
    Field("classification", "Delref Classification", True, "House classification code."),
    Field("bsuid", "BSUID", True, "Asset identifier, if the practice uses one."),
    Field("trigger_events", "Trigger Events", True, ""),
)

#: The cover's labelled block, in its default order.
COVER_FIELDS: tuple[Field, ...] = (
    Field("recipient", "Intended for", True, "The client."),
    Field("date", "Date", True, ""),
    Field("document_number", "Document number", True, ""),
    Field("revision", "Revision", True, "Revision and suitability, together."),
    Field("prepared_by", "Prepared by", True, ""),
    Field("checked_by", "Checked by", True, ""),
    Field("approved_by", "Approved by", True, ""),
    Field("building", "Building", True, "Hide this on a single-building job."),
)

_COVER_BY_KEY = {f.key: f for f in COVER_FIELDS}
_REVISION_BY_KEY = {f.key: f for f in REVISION_FIELDS}

DEFAULT_PALETTE: dict[str, str] = {
    #: The two title colours the house cover uses.
    "title": "4D4D4D",
    "accent": "009DF0",
    #: Header shading on the schedule, in the issue theme.
    "header": "D9D9D9",
    #: Rules and borders.
    "rule": "808080",
}


@dataclass(slots=True)
class Branding:
    """One practice's document appearance, as data the renderer can carry out."""

    #: A data URI or a path. Written onto the cover as a real image, which is
    #: the one piece of branding openpyxl can produce faithfully.
    logo: str = ""
    logo_scale: float = 1.0
    #: Where the image is anchored on the cover, as a cell reference.
    logo_anchor: str = "A1"
    cover_font: str = "Verdana"
    schedule_font: str = "Arial"
    title_size: int = 30
    palette: dict[str, str] = _field(default_factory=lambda: dict(DEFAULT_PALETTE))
    #: Which cover fields show, keyed by field key. Absent means shown.
    cover_fields: dict[str, bool] = _field(default_factory=dict)
    #: Their order, by key. Anything not listed keeps its default position.
    cover_order: list[str] = _field(default_factory=list)
    revision_fields: dict[str, bool] = _field(default_factory=dict)
    revision_order: list[str] = _field(default_factory=list)
    #: Free text a practice puts at the foot of the cover -- a company name, a
    #: registration number. A slot, not a canvas.
    cover_footer: str = ""
    #: Shown under the title block on the cover.
    cover_subtitle: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "Branding":
        data = data or {}
        palette = {**DEFAULT_PALETTE, **(data.get("palette") or {})}
        return cls(
            logo=data.get("logo", "") or "",
            logo_scale=float(data.get("logo_scale") or 1.0),
            logo_anchor=data.get("logo_anchor") or "A1",
            cover_font=data.get("cover_font") or "Verdana",
            schedule_font=data.get("schedule_font") or "Arial",
            title_size=int(data.get("title_size") or 30),
            palette={k: _clean_colour(v) for k, v in palette.items()},
            cover_fields=dict(data.get("cover_fields") or {}),
            cover_order=list(data.get("cover_order") or []),
            revision_fields=dict(data.get("revision_fields") or {}),
            revision_order=list(data.get("revision_order") or []),
            cover_footer=data.get("cover_footer", "") or "",
            cover_subtitle=data.get("cover_subtitle", "") or "",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "logo": self.logo,
            "logo_scale": self.logo_scale,
            "logo_anchor": self.logo_anchor,
            "cover_font": self.cover_font,
            "schedule_font": self.schedule_font,
            "title_size": self.title_size,
            "palette": dict(self.palette),
            "cover_fields": dict(self.cover_fields),
            "cover_order": list(self.cover_order),
            "revision_fields": dict(self.revision_fields),
            "revision_order": list(self.revision_order),
            "cover_footer": self.cover_footer,
            "cover_subtitle": self.cover_subtitle,
        }

    # -- what the renderer asks -------------------------------------------
    def cover_layout(self) -> list[Field]:
        """The cover's fields, in order, with the hidden ones dropped."""
        return resolve_fields(COVER_FIELDS, self.cover_fields, self.cover_order)

    def revision_layout(self) -> list[Field]:
        """The revision page's summary rows, in order, hidden ones dropped."""
        return resolve_fields(REVISION_FIELDS, self.revision_fields, self.revision_order)

    def colour(self, name: str) -> str:
        """A palette colour as ``AARRGGBB``, which is what openpyxl wants."""
        return "FF" + self.palette.get(name, DEFAULT_PALETTE.get(name, "000000"))

    def rgb(self, name: str) -> str:
        return self.palette.get(name, DEFAULT_PALETTE.get(name, "000000"))

    def house_style_overrides(self) -> dict[str, Any]:
        """The parts of the house style branding decides.

        Kept as an overlay rather than a second copy: the house style holds
        sizes and row counts that are nothing to do with branding, and two
        places holding the font would eventually disagree.
        """
        return {
            "cover_font": self.cover_font,
            "schedule_font": self.schedule_font,
            "title_size": self.title_size,
            "title_grey": self.colour("title"),
            "title_blue": self.colour("accent"),
        }


def with_project_overrides(
    house: dict[str, Any] | None, project: dict[str, Any] | None
) -> dict[str, Any]:
    """The organisation's branding as one project sees it.

    Only :data:`PROJECT_KEYS` are taken from the project, and the show/hide maps
    are merged key by key rather than replaced: a project that hides Building
    should not silently un-hide everything the practice had hidden, which is
    what a wholesale replacement would do.
    """
    merged = dict(house or {})
    for key in PROJECT_KEYS:
        if not project or key not in project:
            continue
        value = project[key]
        if key.endswith("_fields"):
            merged[key] = {**(merged.get(key) or {}), **(value or {})}
        elif value not in (None, "", []):
            merged[key] = value
    return merged


def resolve_fields(
    available: Sequence[Field],
    shown: dict[str, bool],
    order: Sequence[str],
) -> list[Field]:
    """Apply a practice's show/hide and ordering to a set of fields.

    A field the workbook's own formulas read is never dropped, whatever the
    configuration says: hiding it would not tidy the page, it would produce a
    document with a broken reference in it, which is the failure this whole
    approach exists to avoid.
    """
    by_key = {f.key: f for f in available}
    ordered: list[Field] = []

    for key in order:
        field = by_key.get(key)
        if field is not None and field not in ordered:
            ordered.append(field)
    for field in available:
        if field not in ordered:
            ordered.append(field)

    return [f for f in ordered if not f.optional or shown.get(f.key, True)]


def validate_branding(data: dict[str, Any] | None) -> list[str]:
    """Problems with a branding payload, in the practice's own terms."""
    branding = Branding.from_dict(data)
    problems: list[str] = []

    for name, font in (("cover", branding.cover_font), ("schedule", branding.schedule_font)):
        if font not in SAFE_FONTS:
            problems.append(
                f"{font!r} is not one of the fonts every machine has, so a recipient "
                f"would see something else. Choose one of: {', '.join(SAFE_FONTS)}."
            )
    for key, value in (data or {}).get("palette", {}).items():
        if not _is_hex(value):
            problems.append(f"{key} colour {value!r} is not a six-digit hex colour")
    if not 0.1 <= branding.logo_scale <= 4:
        problems.append("the logo scale has to be between 0.1 and 4")
    if not 8 <= branding.title_size <= 60:
        problems.append("the title size has to be between 8 and 60 points")

    for group, known in (
        ("cover_fields", _COVER_BY_KEY), ("revision_fields", _REVISION_BY_KEY),
    ):
        for key in (data or {}).get(group, {}):
            if key not in known:
                problems.append(f"{key!r} is not a field on the {group.split('_')[0]}")
            elif not known[key].optional and (data or {})[group][key] is False:
                problems.append(
                    f"{known[key].label!r} cannot be hidden: the workbook reads that row"
                )
    return problems


def _clean_colour(value: Any) -> str:
    text = str(value or "").strip().lstrip("#")
    if len(text) == 8:  # an AARRGGBB value from the house style
        text = text[2:]
    return text.upper()


def _is_hex(value: Any) -> bool:
    text = _clean_colour(value)
    return len(text) == 6 and all(c in "0123456789ABCDEF" for c in text)
