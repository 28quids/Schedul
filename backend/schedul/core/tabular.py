"""Reading a block of spreadsheet cells, and deciding what pasting it would do.

Two features need this: pasting rows into a schedule, and importing a supplier's
product list into the equipment library. Both start with the same thing -- a
lump of tab-separated text off somebody's clipboard -- and both have to answer
the same questions before anything is written:

* how many rows are actually in it,
* whether the first line is a header rather than data,
* which column each field lands in, and
* what applying it would do to what is already there.

That last one is the point. SPEC.md's safety rule is that a destructive
operation is previewed before it happens and nothing is overwritten silently, so
the plan is a value the caller can show a user and then either apply or throw
away. Producing the plan touches nothing.

It lives in ``core/`` because it is a rule about the data rather than about the
web: the same planner drives the grid's paste dialog, the library importer, and
any future file upload, and none of them get to disagree about what "append"
means.
"""

from __future__ import annotations

from dataclasses import dataclass, field as _field
from typing import Any, Iterable, Literal, Sequence

__all__ = [
    "PasteMode",
    "Block",
    "PastePlan",
    "read_block",
    "looks_like_header",
    "map_columns",
    "plan_paste",
    "rows_to_values",
]

PasteMode = Literal["replace", "append", "insert"]

#: What a spreadsheet puts on the clipboard. Excel and LibreOffice both use tabs
#: between cells and newlines between rows; a CSV pasted by hand uses commas, so
#: that is accepted as a fallback when a line has no tab in it at all.
_TAB = "\t"


def _split_line(line: str) -> list[str]:
    if _TAB in line:
        return line.split(_TAB)
    if "," in line:
        return line.split(",")
    return [line]


@dataclass(slots=True)
class Block:
    """A parsed clipboard block: its cells, and whether line one was a header."""

    cells: list[list[str]] = _field(default_factory=list)
    header: list[str] | None = None
    #: True when the header was detected rather than declared by the caller.
    header_detected: bool = False
    warnings: list[str] = _field(default_factory=list)

    @property
    def row_count(self) -> int:
        return len(self.cells)

    @property
    def width(self) -> int:
        return max((len(row) for row in self.cells), default=0)


def read_block(
    text: str,
    *,
    column_names: Sequence[str] = (),
    header: bool | None = None,
) -> Block:
    """Parse pasted text into rows of cells, splitting off a header if there is one.

    ``header`` forces the answer; leaving it ``None`` detects one by comparing
    the first line against ``column_names``. Detection is deliberately
    conservative -- a wrongly discarded first row is a row of somebody's data
    silently going missing.
    """
    lines = [line for line in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    rows = [_split_line(line) for line in lines if line.strip()]

    block = Block()
    if not rows:
        return block

    first = [c.strip() for c in rows[0]]
    detected = looks_like_header(first, column_names)
    take_header = detected if header is None else bool(header)

    if take_header:
        block.header = first
        block.header_detected = header is None and detected
        rows = rows[1:]

    block.cells = [[c.strip() for c in row] for row in rows]

    if column_names and block.width > len(column_names):
        block.warnings.append(
            f"the pasted block is {block.width} columns wide but this schedule has "
            f"{len(column_names)} you can type into; the extra columns are ignored"
        )
    ragged = {len(row) for row in block.cells}
    if len(ragged) > 1:
        block.warnings.append(
            "the pasted rows are not all the same width; short rows are padded with blanks"
        )
    return block


def looks_like_header(cells: Sequence[str], column_names: Sequence[str]) -> bool:
    """Whether this line names columns rather than carrying data.

    Two rules, both of which have to hold: at least one cell matches a column
    name, and no cell looks like a number. The second is what stops a row whose
    first field happens to read ``Ref`` from eating the user's first unit.
    """
    if not cells or not column_names:
        return False
    known = {_norm(name) for name in column_names}
    matches = sum(1 for c in cells if c and _norm(c) in known)
    if not matches:
        return False
    if any(_is_number(c) for c in cells):
        return False
    return matches >= max(1, min(2, len([c for c in cells if c]) // 2))


def _norm(text: str) -> str:
    """Match column names tolerantly: case, spacing and the unit suffix."""
    text = str(text).strip().lower()
    if text.endswith(")") and "(" in text:
        text = text[: text.rindex("(")].strip()
    return " ".join(text.split())


def _is_number(value: str) -> bool:
    text = str(value).strip().replace(",", "")
    if not text:
        return False
    try:
        float(text)
    except ValueError:
        return False
    return True


def map_columns(
    header: Sequence[str] | None,
    column_names: Sequence[str],
    *,
    width: int = 0,
) -> list[str | None]:
    """Decide which target column each position in the block belongs to.

    With a header, names are matched tolerantly and an unrecognised one maps to
    ``None`` rather than being guessed at. Without one, positions map left to
    right, which is what a user dragging a block out of Excel expects.
    """
    if header:
        lookup = {_norm(name): name for name in column_names}
        return [lookup.get(_norm(cell)) for cell in header]
    count = width or len(column_names)
    return [
        column_names[i] if i < len(column_names) else None
        for i in range(count)
    ]


def rows_to_values(
    block: Block, mapping: Sequence[str | None]
) -> list[dict[str, Any]]:
    """Turn parsed cells into ``{column: value}`` dicts using a column mapping.

    Blank cells are dropped rather than stored as empty strings: a short pasted
    row must leave the columns it does not reach alone, not blank them.
    """
    out: list[dict[str, Any]] = []
    for row in block.cells:
        values: dict[str, Any] = {}
        for index, cell in enumerate(row):
            if index >= len(mapping):
                break
            target = mapping[index]
            if target is None:
                continue
            text = cell.strip()
            if text != "":
                values[target] = text
        out.append(values)
    return out


@dataclass(slots=True)
class PastePlan:
    """What a paste would do, before it does it.

    Every count here is something a user can check against what they meant.
    ``destructive`` is the one that gates the extra confirmation: it is true only
    when applying would remove rows that already carry typed values.
    """

    mode: str
    detected_rows: int = 0
    header_detected: bool = False
    header: list[str] | None = None
    existing_rows: int = 0
    #: Rows the paste creates at the end of the schedule.
    to_append: int = 0
    #: Rows the paste pushes in above existing ones, which keeps their data.
    to_insert: int = 0
    #: Rows the paste removes. Only 'replace' ever removes anything.
    to_remove: int = 0
    #: Of the removed rows, how many actually carry typed values.
    populated_removed: int = 0
    position: int = 0
    total_after: int = 0
    warnings: list[str] = _field(default_factory=list)
    columns: list[str | None] = _field(default_factory=list)
    #: The mapped rows, so the caller can show the first few as a preview.
    rows: list[dict[str, Any]] = _field(default_factory=list)

    @property
    def destructive(self) -> bool:
        return self.populated_removed > 0

    @property
    def unmapped_columns(self) -> int:
        return sum(1 for c in self.columns if c is None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "detected_rows": self.detected_rows,
            "header_detected": self.header_detected,
            "header": self.header,
            "existing_rows": self.existing_rows,
            "to_append": self.to_append,
            "to_insert": self.to_insert,
            "to_remove": self.to_remove,
            "populated_removed": self.populated_removed,
            "position": self.position,
            "total_after": self.total_after,
            "destructive": self.destructive,
            "warnings": list(self.warnings),
            "columns": list(self.columns),
            "rows": list(self.rows),
        }


def plan_paste(
    text: str,
    *,
    mode: str = "append",
    column_names: Sequence[str],
    existing: Sequence[dict[str, Any]] = (),
    position: int = 0,
    header: bool | None = None,
) -> PastePlan:
    """Work out what pasting ``text`` would do, without doing any of it.

    ``existing`` is the schedule's current rows as ``{column: value}`` dicts,
    used only to count what would be lost. Nothing here mutates anything.
    """
    if mode not in ("replace", "append", "insert"):
        raise ValueError(f"unknown paste mode {mode!r}")

    block = read_block(text, column_names=column_names, header=header)
    mapping = map_columns(block.header, column_names, width=block.width)
    rows = rows_to_values(block, mapping)

    existing_rows = list(existing)
    plan = PastePlan(
        mode=mode,
        detected_rows=len(rows),
        header_detected=block.header_detected,
        header=block.header,
        existing_rows=len(existing_rows),
        warnings=list(block.warnings),
        columns=list(mapping),
        rows=rows,
    )

    if mode == "replace":
        plan.to_remove = len(existing_rows)
        plan.populated_removed = sum(1 for r in existing_rows if _populated(r))
        plan.to_append = len(rows)
        plan.total_after = len(rows)
        plan.position = 0
    elif mode == "append":
        plan.to_append = len(rows)
        plan.total_after = len(existing_rows) + len(rows)
        plan.position = len(existing_rows)
    else:
        at = max(0, min(int(position), len(existing_rows)))
        plan.to_insert = len(rows)
        plan.position = at
        plan.total_after = len(existing_rows) + len(rows)

    if plan.unmapped_columns:
        plan.warnings.append(
            f"{plan.unmapped_columns} pasted column(s) did not match a column on this "
            f"schedule and will be ignored"
        )
    if not rows:
        plan.warnings.append("nothing to paste: no data rows were found")
    return plan


def _populated(row: dict[str, Any]) -> bool:
    return any(v not in (None, "") for v in (row or {}).values())
