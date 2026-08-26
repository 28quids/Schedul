"""Schedule type definitions: the reusable shape of one kind of schedule.

Replaces v1's single ``schema.json``. A schedule type is a code, a title, an
ordered list of columns and its own equipment-specific notes.

**Three column kinds, not two.** This is the correction to v1's mental model and
it is the thing that makes an IFC or COBie export possible later:

===========  ==================  ==============================  ================
kind         v1 schema key       behaviour                       colour
===========  ==================  ==============================  ================
``input``    ``instance_fields``  the user types it, per unit     blue on yellow
``library``  ``type_fields``      INDEX/MATCH on Model Reference  green
``derived``  ``derived_fields``   formula, read-only              black
===========  ==================  ==============================  ================

That maps onto COBie/IFC (ISO 16739-1) Component data versus Type data, with
derived being neither. A field must not sit in the wrong kind for convenience.

``Model Reference`` is inserted automatically between the input and library
columns. It is the lookup key and the user never defines it.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field as _field
from typing import Any, Iterable, Literal, Sequence

from . import formula as _formula
from .units import join_unit, plain_unit, split_unit

__all__ = [
    "ColumnKind",
    "Column",
    "ScheduleType",
    "CatalogueError",
    "ValidationIssue",
    "MODEL_REFERENCE",
    "validate_type",
    "validate_catalogue",
    "from_legacy",
    "to_legacy",
]

ColumnKind = Literal["input", "library", "derived"]

#: The automatic lookup-key column, inserted between input and library columns.
MODEL_REFERENCE = "Model Reference"

_CODE_RE = re.compile(r"^[A-Z][A-Z0-9]*$")

_KIND_ORDER: dict[str, int] = {"input": 0, "library": 1, "derived": 2}


class CatalogueError(Exception):
    """A schedule type is structurally invalid and cannot be used."""


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """One problem with a schedule type.

    ``severity`` is ``"error"`` (the type cannot be rendered) or ``"warning"``
    (it can, but something is probably wrong).
    """

    severity: Literal["error", "warning"]
    message: str
    column: str | None = None

    def __str__(self) -> str:
        where = f" [{self.column}]" if self.column else ""
        return f"{self.severity}{where}: {self.message}"


@dataclass(slots=True)
class Column:
    """One column of a schedule.

    ``name`` and ``unit`` are stored separately -- house format renders the name
    on row 4 and the unit on row 5. ``unit`` is stored plain (``degC``) and
    rendered pretty (``°C``).
    """

    kind: ColumnKind
    name: str
    unit: str = ""
    width: int = 14
    example: Any = ""
    formula: str | None = None
    note: str | None = None
    #: Where this column appears. An absent key means visible, so a catalogue
    #: written before visibility existed needs no migration.
    visibility: dict[str, bool] = _field(default_factory=dict)
    #: True for a column a project added on top of the catalogue type.
    project_extra: bool = False

    def __post_init__(self) -> None:
        self.name = self.name.strip()
        self.unit = plain_unit(self.unit.strip())

    def visible_in(self, target: str) -> bool:
        """Whether this column shows in ``editor``, ``xlsx`` or ``pdf``.

        Lets a practice keep internal data such as ``Price`` on the schedule
        without it reaching an issued document.
        """
        return self.visibility.get(target, True)

    @property
    def legacy_name(self) -> str:
        """The v1 single-string field name, e.g. ``Supply Airflow (l/s)``."""
        return join_unit(self.name, self.unit)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "kind": self.kind,
            "name": self.name,
            "unit": self.unit,
            "width": self.width,
            "example": self.example,
        }
        if self.kind == "derived":
            out["formula"] = self.formula or ""
            out["note"] = self.note or ""
        if self.visibility:
            out["visibility"] = dict(self.visibility)
        if self.project_extra:
            out["project_extra"] = True
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Column":
        return cls(
            kind=data["kind"],
            name=data["name"],
            unit=data.get("unit", ""),
            width=int(data.get("width", 14)),
            example=data.get("example", ""),
            formula=data.get("formula") or None,
            note=data.get("note") or None,
            visibility=dict(data.get("visibility") or {}),
            project_extra=bool(data.get("project_extra", False)),
        )


@dataclass(slots=True)
class ScheduleType:
    """One catalogue entry: the reusable definition of a kind of schedule."""

    code: str
    title: str
    short: str = ""
    version: int = 1
    volume: str = ""
    columns: list[Column] = _field(default_factory=list)
    notes: list[str] = _field(default_factory=list)
    created: str = ""
    updated: str = ""
    history: list[dict[str, Any]] = _field(default_factory=list)

    def __post_init__(self) -> None:
        self.code = self.code.strip().upper()
        today = _dt.date.today().isoformat()
        self.created = self.created or today
        self.updated = self.updated or self.created

    # -- column access ----------------------------------------------------
    def of_kind(self, kind: ColumnKind) -> list[Column]:
        """Columns of one kind, in authored order."""
        return [c for c in self.columns if c.kind == kind]

    @property
    def inputs(self) -> list[Column]:
        return self.of_kind("input")

    @property
    def library(self) -> list[Column]:
        return self.of_kind("library")

    @property
    def derived(self) -> list[Column]:
        return self.of_kind("derived")

    def column(self, name: str) -> Column | None:
        """Look a column up by name, ignoring the unit suffix if one is given."""
        for c in self.columns:
            if c.name == name or c.legacy_name == name:
                return c
        return None

    def visible_columns(self, target: str) -> "ScheduleType":
        """A copy of this type carrying only the columns visible in ``target``.

        Derived columns that reference a hidden column are kept, because the
        formula still needs its operands; hiding is presentational, not a
        removal from the model.
        """
        keep = [c for c in self.columns if c.visible_in(target)]
        clone = ScheduleType(
            code=self.code, title=self.title, short=self.short,
            version=self.version, volume=self.volume, columns=keep,
            notes=list(self.notes), created=self.created, updated=self.updated,
            history=list(self.history),
        )
        return clone

    def with_extras(self, extras: Sequence[Column]) -> "ScheduleType":
        """This type plus a project's additional columns.

        Additions only. A project cannot remove or reorder the catalogue's
        columns, or two projects' schedules of the same type stop being
        comparable and the catalogue stops meaning anything.
        """
        if not extras:
            return self
        existing = {c.legacy_name.lower() for c in self.columns}
        added = [
            Column(**{**c.to_dict(), "project_extra": True})
            for c in extras
            if c.legacy_name.lower() not in existing
        ]
        return ScheduleType(
            code=self.code, title=self.title, short=self.short,
            version=self.version, volume=self.volume,
            columns=[*self.columns, *added],
            notes=list(self.notes), created=self.created, updated=self.updated,
            history=list(self.history),
        )

    def layout(self) -> list[Column]:
        """Columns in physical left-to-right sheet order.

        Inputs, then the automatic ``Model Reference``, then library columns,
        then derived. That grouping is what gives the sheet its blue / green /
        black colour blocks.
        """
        mr = Column(kind="input", name=MODEL_REFERENCE, width=18, example="")
        return [*self.inputs, mr, *self.library, *self.derived]

    @property
    def field_names(self) -> list[str]:
        """Every name a formula may reference, including ``Model Reference``."""
        names = [c.legacy_name for c in self.columns]
        names.append(MODEL_REFERENCE)
        return names

    def parse_formula(self, column: Column) -> _formula.Node:
        """Parse one derived column's formula against this type's columns."""
        if column.kind != "derived" or not column.formula:
            raise CatalogueError(f"{column.name} is not a derived column")
        return _formula.parse(column.formula, known_fields=self.field_names)

    def evaluation_order(self) -> list[Column]:
        """Derived columns ordered so each comes after everything it references.

        Derived columns may reference other derived columns; the grid has to
        evaluate them in dependency order. Raises :class:`CatalogueError` on a
        cycle, though :func:`validate_type` reports that as an issue first.
        """
        derived = {c.legacy_name: c for c in self.derived}
        ordered: list[Column] = []
        state: dict[str, int] = {}

        def visit(name: str, trail: tuple[str, ...]) -> None:
            mark = state.get(name, 0)
            if mark == 2:
                return
            if mark == 1:
                cycle = " -> ".join([*trail[trail.index(name) :], name])
                raise CatalogueError(f"circular reference between derived columns: {cycle}")
            state[name] = 1
            col = derived[name]
            try:
                node = self.parse_formula(col)
            except _formula.FormulaError:
                node = None
            if node is not None:
                for ref in _formula.field_names(node):
                    if ref in derived:
                        visit(ref, (*trail, name))
            state[name] = 2
            ordered.append(col)

        for name in derived:
            visit(name, ())
        return ordered

    # -- serialisation ----------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "title": self.title,
            "short": self.short,
            "version": self.version,
            "volume": self.volume,
            "created": self.created,
            "updated": self.updated,
            "columns": [c.to_dict() for c in self.columns],
            "notes": list(self.notes),
            "history": list(self.history),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScheduleType":
        return cls(
            code=data["code"],
            title=data["title"],
            short=data.get("short", ""),
            version=int(data.get("version", 1)),
            volume=data.get("volume", ""),
            columns=[Column.from_dict(c) for c in data.get("columns", [])],
            notes=list(data.get("notes", [])),
            created=data.get("created", ""),
            updated=data.get("updated", ""),
            history=list(data.get("history", [])),
        )

    def bump(self, change: str, *, today: str | None = None) -> None:
        """Record a change to ``columns``: bump the version, append to history.

        A project pins the version it built against, so editing a type does not
        silently invalidate every schedule already issued against it.
        """
        stamp = today or _dt.date.today().isoformat()
        self.history.append({"version": self.version, "date": self.updated, "change": change})
        self.version += 1
        self.updated = stamp


# ------------------------------------------------------------ validation ---


def validate_type(
    st: ScheduleType, *, other_codes: Iterable[str] = ()
) -> list[ValidationIssue]:
    """Every structural check on a schedule type.

    The v1 renderer enforced none of these, so a bad type failed at build time
    or, worse, produced a workbook with ``#REF!`` in it. Collected here so the
    designer can show them all at once.
    """
    issues: list[ValidationIssue] = []
    add = issues.append

    # -- code and titles --------------------------------------------------
    if not st.code:
        add(ValidationIssue("error", "code is required"))
    elif not _CODE_RE.match(st.code):
        add(
            ValidationIssue(
                "error",
                f"code {st.code!r} must be uppercase letters and digits, "
                f"start with a letter, and contain no spaces",
            )
        )
    # 'other_codes' is every OTHER type's code, so any match is a real collision.
    # Comparing against a set that included this type's own code would make a
    # duplicate impossible to detect.
    if st.code and st.code in {c.strip().upper() for c in other_codes}:
        add(ValidationIssue("error", f"code {st.code!r} is already used by another type"))
    if not st.title.strip():
        add(ValidationIssue("error", "title is required"))

    # -- columns ----------------------------------------------------------
    if not st.columns:
        add(ValidationIssue("error", "a schedule type needs at least one column"))
        return issues

    seen: dict[str, Column] = {}
    for col in st.columns:
        if not col.name:
            add(ValidationIssue("error", "a column has no name"))
            continue
        if col.name.strip().lower() == MODEL_REFERENCE.lower():
            add(
                ValidationIssue(
                    "error",
                    f"{MODEL_REFERENCE!r} is inserted automatically between the "
                    f"input and library columns and must not be defined by hand",
                    col.name,
                )
            )
            continue
        key = col.legacy_name.lower()
        if key in seen:
            add(ValidationIssue("error", f"duplicate column name {col.legacy_name!r}", col.name))
            continue
        seen[key] = col

        if col.kind not in _KIND_ORDER:
            add(ValidationIssue("error", f"unknown column kind {col.kind!r}", col.name))
        if col.width < 4 or col.width > 80:
            add(
                ValidationIssue(
                    "warning", f"width {col.width} is outside the usual 4-80 range", col.name
                )
            )
        # A unit that is not a unit belongs in the name, not the unit row.
        name_part, unit_part = split_unit(col.legacy_name)
        if unit_part and name_part != col.name:
            add(
                ValidationIssue(
                    "warning",
                    f"name and unit disagree: reads as {name_part!r} + {unit_part!r}",
                    col.name,
                )
            )

    if not st.inputs:
        add(
            ValidationIssue(
                "error",
                "a schedule type needs at least one input column, or there is "
                "nothing for the engineer to fill in",
            )
        )
    if not st.library:
        add(
            ValidationIssue(
                "error",
                "a schedule type needs at least one library column, or the "
                "equipment library serves no purpose on this schedule",
            )
        )

    # -- derived formulas -------------------------------------------------
    known = st.field_names
    for col in st.derived:
        if not col.formula or not col.formula.strip():
            add(ValidationIssue("error", "derived column has no formula", col.name))
            continue
        try:
            node = _formula.parse(col.formula, known_fields=known)
        except _formula.FormulaError as exc:
            add(ValidationIssue("error", str(exc), col.name))
            continue
        if not col.note:
            add(
                ValidationIssue(
                    "warning",
                    "derived column has no note; the note becomes the cell "
                    "comment explaining the calculation",
                    col.name,
                )
            )
        # A derived column reading a library column is fine; reading itself is not.
        if col.legacy_name in _formula.field_names(node):
            add(ValidationIssue("error", "formula refers to its own column", col.name))

    try:
        st.evaluation_order()
    except CatalogueError as exc:
        add(ValidationIssue("error", str(exc)))

    return issues


def validate_catalogue(types: Sequence[ScheduleType]) -> dict[str, list[ValidationIssue]]:
    """Validate a whole catalogue, checking code uniqueness across it.

    Each type is compared against the others by position, not by value, so two
    types genuinely sharing a code are both flagged.
    """
    return {
        t.code: validate_type(
            t, other_codes=[o.code for j, o in enumerate(types) if j != i]
        )
        for i, t in enumerate(types)
    }


# ------------------------------------------------------ v1 interop ---------


def from_legacy(entry: dict[str, Any], *, volume: str = "") -> ScheduleType:
    """Build a schedule type from one v1 ``schema.json`` equipment type.

    The three parallel field lists collapse into one ordered ``columns`` array,
    and the dead ``number`` field is dropped -- the builder never read it, and
    document numbers are allocated per building now (SPEC.md fact 4).
    """
    columns: list[Column] = []

    for name, width, example in entry.get("instance_fields", []):
        base, unit = split_unit(name)
        columns.append(
            Column(kind="input", name=base, unit=unit, width=int(width), example=example)
        )
    for name, width, example in entry.get("type_fields", []):
        base, unit = split_unit(name)
        columns.append(
            Column(kind="library", name=base, unit=unit, width=int(width), example=example)
        )
    for name, width, expr, note in entry.get("derived_fields", []):
        base, unit = split_unit(name)
        columns.append(
            Column(
                kind="derived",
                name=base,
                unit=unit,
                width=int(width),
                example="",
                formula=expr,
                note=note,
            )
        )

    return ScheduleType(
        code=entry["code"],
        title=entry["title"],
        short=entry.get("short", ""),
        version=1,
        volume=volume,
        columns=columns,
        notes=[],
    )


def to_legacy(st: ScheduleType) -> dict[str, Any]:
    """Render a schedule type back into the v1 three-list shape.

    Exists so the vendored renderer body keeps working on
    ``instance_fields / type_fields / derived_fields`` (SPEC.md 6.2), and so the
    migration can be round-trip tested against the original ``schema.json``.
    """
    return {
        "code": st.code,
        "title": st.title,
        "short": st.short,
        "instance_fields": [[c.legacy_name, c.width, c.example] for c in st.inputs],
        "type_fields": [[c.legacy_name, c.width, c.example] for c in st.library],
        "derived_fields": [
            [c.legacy_name, c.width, c.formula or "", c.note or ""] for c in st.derived
        ],
    }
