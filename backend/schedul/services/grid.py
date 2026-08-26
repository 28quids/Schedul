"""Computing what the schedule grid shows.

The user types input columns and a Model Reference. Everything else is produced:
library columns are looked up from the organisation's equipment library, and
derived columns are evaluated from the same AST the exported workbook emits as
an Excel formula.

Only what the user typed is stored (``ScheduleRow.values``). Library and derived
values are computed on read, so a product corrected in the library shows through
on every schedule that uses it, and a formula fixed in the designer takes effect
without touching any row.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field as _field
from typing import Any, Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.catalogue import MODEL_REFERENCE, Column, ScheduleType
from ..core.formula import BLANK, FormulaError, Node, evaluate
from ..db.models import Equipment, Schedule, ScheduleRow

__all__ = ["CellValue", "GridRow", "Grid", "build_grid", "compute_row", "library_index"]


@dataclass(slots=True)
class CellValue:
    """One computed cell, with enough context for the grid to render it."""

    column: str
    kind: str
    value: Any = None
    #: Set when a library lookup or a formula could not produce a value.
    problem: str | None = None
    #: True when the user may type here.
    editable: bool = False
    #: True for a library value this row deliberately diverges on.
    overridden: bool = False


@dataclass(slots=True)
class GridRow:
    id: str
    position: int
    cells: dict[str, CellValue] = _field(default_factory=dict)

    def value(self, column: str) -> Any:
        cell = self.cells.get(column)
        return None if cell is None else cell.value


@dataclass(slots=True)
class Grid:
    """A schedule's full computed contents."""

    columns: list[dict[str, Any]] = _field(default_factory=list)
    rows: list[GridRow] = _field(default_factory=list)

    @property
    def problem_count(self) -> int:
        return sum(1 for r in self.rows for c in r.cells.values() if c.problem)


def library_index(
    session: Session, organisation_id: str, type_code: str
) -> dict[str, dict[str, Any]]:
    """Every product for this type, keyed by Model Reference.

    One query per schedule rather than one per row: a 40-row schedule against a
    thousand-product library is still two round trips.
    """
    stmt = select(Equipment).where(
        Equipment.organisation_id == organisation_id,
        Equipment.type_code == type_code,
        Equipment.review_state != "rejected",
    )
    return {e.model_reference: (e.values or {}) for e in session.scalars(stmt)}


def _column_spec(col: Column, editable: bool) -> dict[str, Any]:
    from ..core.units import pretty_unit

    return {
        "name": col.name,
        "legacy_name": col.legacy_name,
        "kind": col.kind,
        "unit": col.unit,
        "unit_display": pretty_unit(col.unit),
        "width": col.width,
        "example": col.example,
        "formula": col.formula,
        "note": col.note,
        "editable": editable,
        "visibility": dict(col.visibility),
        "project_extra": col.project_extra,
    }


def compute_row(
    stored: dict[str, Any],
    schedule_type: ScheduleType,
    products: dict[str, dict[str, Any]],
    constants: dict[str, float],
    parsed: dict[str, Node] | None = None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, CellValue]:
    """Compute one row: typed values, library lookups, then derived formulas.

    ``parsed`` caches the ASTs across rows; parsing 40 rows' worth of the same
    formulas would otherwise dominate the cost of loading a schedule.
    """
    cells: dict[str, CellValue] = {}
    #: Formula operands are looked up by the v1 full field name, which is what
    #: '{Field Name}' references resolve against.
    values: dict[str, Any] = {}

    for col in schedule_type.inputs:
        raw = stored.get(col.legacy_name, stored.get(col.name))
        cells[col.legacy_name] = CellValue(col.legacy_name, "input", raw, None, True)
        values[col.legacy_name] = BLANK if raw in (None, "") else raw

    model_ref = stored.get(MODEL_REFERENCE) or ""
    cells[MODEL_REFERENCE] = CellValue(MODEL_REFERENCE, "input", model_ref, None, True)
    values[MODEL_REFERENCE] = BLANK if not model_ref else model_ref

    product = products.get(model_ref) if model_ref else None
    missing = bool(model_ref) and product is None

    overrides = overrides or {}
    for col in schedule_type.library:
        # A deliberate override wins over the library, and over the absence of a
        # model reference: the whole point is that this row diverges.
        if col.legacy_name in overrides:
            raw = overrides[col.legacy_name]
            cells[col.legacy_name] = CellValue(
                col.legacy_name, "library", raw, None, True, overridden=True
            )
            values[col.legacy_name] = BLANK if raw in (None, "") else raw
            continue
        if not model_ref:
            cells[col.legacy_name] = CellValue(col.legacy_name, "library", None)
            values[col.legacy_name] = BLANK
            continue
        if missing:
            cells[col.legacy_name] = CellValue(
                col.legacy_name, "library", None,
                f"{model_ref!r} is not in the equipment library",
            )
            values[col.legacy_name] = BLANK
            continue
        raw = (product or {}).get(col.legacy_name, (product or {}).get(col.name))
        cells[col.legacy_name] = CellValue(col.legacy_name, "library", raw)
        values[col.legacy_name] = BLANK if raw in (None, "") else raw

    # A row with no reference in its first input column renders blank, matching
    # the exported workbook's IF($A6="","",...) wrapper.
    anchor = schedule_type.inputs[0].legacy_name if schedule_type.inputs else None
    row_is_empty = anchor is not None and values.get(anchor) is BLANK

    parsed = parsed if parsed is not None else {}
    for col in schedule_type.evaluation_order():
        key = col.legacy_name
        if row_is_empty:
            cells[key] = CellValue(key, "derived", None)
            values[key] = BLANK
            continue
        node = parsed.get(key)
        if node is None:
            try:
                node = schedule_type.parse_formula(col)
            except FormulaError as exc:
                cells[key] = CellValue(key, "derived", None, str(exc))
                values[key] = BLANK
                continue
            parsed[key] = node
        try:
            result = evaluate(node, values, constants)
        except FormulaError as exc:
            # The workbook wraps every derived cell in IFERROR(..., ""), so a
            # bad row shows blank there; the grid can say why instead.
            cells[key] = CellValue(key, "derived", None, str(exc))
            values[key] = BLANK
            continue
        clean = None if result is BLANK else result
        cells[key] = CellValue(key, "derived", clean)
        values[key] = BLANK if clean in (None, "") else clean

    return cells


def build_grid(
    session: Session,
    schedule: Schedule,
    schedule_type: ScheduleType,
    organisation_id: str,
    constants: dict[str, float],
) -> Grid:
    """Compute a whole schedule for display."""
    products = library_index(session, organisation_id, schedule_type.code)
    parsed: dict[str, Node] = {}

    grid = Grid()
    for col in schedule_type.layout():
        editable = col.kind == "input"
        grid.columns.append(_column_spec(col, editable))

    for row in schedule.rows:
        cells = compute_row(
            row.values or {}, schedule_type, products, constants, parsed,
            overrides=row.overrides or {},
        )
        grid.rows.append(GridRow(id=row.id, position=row.position, cells=cells))

    return grid


#: A value that is unambiguously a number. Leading zeros are excluded on
#: purpose: '0123' is a reference someone typed, not the number 123.
_NUMERIC = re.compile(r"^-?(0|[1-9]\d*)(\.\d+)?$")


def coerce(value: Any) -> Any:
    """Turn a numeric-looking string into a number, leaving anything else alone.

    A duty typed into a web form arrives as text. Stored as text it would reach
    the exported workbook as text: left-aligned, ignored by SUM, and awkward in
    any formula an engineer adds later. Converting here means the database and
    the workbook both hold a real number.
    """
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text or not _NUMERIC.match(text):
        return value
    number = float(text)
    return int(number) if number.is_integer() and "." not in text else number


def override_payload(
    submitted: dict[str, Any], schedule_type: ScheduleType
) -> dict[str, Any]:
    """Keep only overrides naming a real library column, dropping blanks.

    Clearing an override is how a row is reset to the library value, so an empty
    string removes the key rather than storing an empty override.
    """
    library = {c.legacy_name: c for c in schedule_type.library}
    cleaned: dict[str, Any] = {}
    for key, value in (submitted or {}).items():
        column = library.get(key) or next(
            (c for c in schedule_type.library if c.name == key), None
        )
        if column is None or value in (None, ""):
            continue
        cleaned[column.legacy_name] = coerce(value)
    return cleaned


def editable_payload(stored: dict[str, Any], schedule_type: ScheduleType) -> dict[str, Any]:
    """Strip a submitted row down to the columns the user is allowed to set.

    Library and derived values are computed, so accepting them from the client
    would let a stale or forged value be stored and then rendered as fact.
    """
    allowed = {c.legacy_name for c in schedule_type.inputs} | {MODEL_REFERENCE}
    cleaned: dict[str, Any] = {}
    for key, value in (stored or {}).items():
        # The Model Reference is a key, never a quantity: coercing it would turn
        # a numeric product code into a number and break the lookup.
        if key in allowed:
            cleaned[key] = value if key == MODEL_REFERENCE else coerce(value)
        else:
            # Accept the bare name too, so a client using display names works.
            match = schedule_type.column(key)
            if match is not None and match.kind == "input":
                cleaned[match.legacy_name] = coerce(value)
    return cleaned
