"""Revision ranking: which row of the revision log is "current".

Both existing implementations get this wrong, in different ways, and it is the
one place SPEC.md 6.1 says the v1 downgrade was itself a bug.

**The hand-made original** takes the maximum of ``RevisionTable[Revision]``
after stripping ``P``. Out-of-order rows work, which is good. But
``SUBSTITUTE(rev,"P","")`` leaves ``C01`` as ``C01``, ``--"C01"`` errors,
``IFERROR`` turns it into ``0``, and a published C-revision therefore sorts
*below every preliminary revision*. The moment a schedule goes to C01 the front
cover reverts to showing the last P revision.

**The v1 generator** takes the last non-empty row via
``INDEX(range, MAX(1, COUNTA(range)))``. C-revisions are fine, but a row entered
out of order, or a blank row left in the middle, breaks it.

**Correct:** rank by series then number, with the published series always above
the preliminary series, so ``P01 < P02 < ... < C01 < C02``.

The exported workbook gets this as a hidden helper column holding
:func:`sort_key`, with the summary block driven off ``INDEX`` /
``MATCH(MAX(...))`` against it. No ``LET``, no ``XLOOKUP``, no spilling --
openpyxl cannot author dynamic arrays reliably, and a helper column is easier
for an engineer to debug than a nested ``LET``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

__all__ = [
    "PRELIMINARY_BASE",
    "PUBLISHED_BASE",
    "Revision",
    "parse_code",
    "sort_key",
    "rank",
    "current",
    "next_code",
    "is_issued",
]

#: Series bases. Published sits above preliminary so C01 outranks every Pnn.
PRELIMINARY_BASE = 1000
PUBLISHED_BASE = 2000

_CODE_RE = re.compile(r"^\s*([PC])\s*(\d{1,3})\s*$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Revision:
    """One row of a schedule's revision log."""

    code: str
    status: str = ""
    date: Any = None
    prepared_by: str = ""
    checked_by: str = ""
    approved_by: str = ""
    description: str = ""

    @property
    def key(self) -> int:
        return sort_key(self.code)

    @property
    def is_published(self) -> bool:
        series, _ = parse_code(self.code)
        return series == "C"


def parse_code(code: str) -> tuple[str, int]:
    """``'P02'`` -> ``('P', 2)``. Raises :class:`ValueError` on anything else."""
    m = _CODE_RE.match(code or "")
    if not m:
        raise ValueError(f"{code!r} is not a revision code; expected P01-style or C01-style")
    return m.group(1).upper(), int(m.group(2))


def sort_key(code: str) -> int:
    """Rank one revision code. Higher is more recent.

    ``P01`` -> 1001, ``P02`` -> 1002, ``C01`` -> 2001. An unparseable code ranks
    at 0 so it never wins, matching the exported helper column's ``IFERROR``.
    """
    try:
        series, number = parse_code(code)
    except ValueError:
        return 0
    return (PUBLISHED_BASE if series == "C" else PRELIMINARY_BASE) + number


def rank(revisions: Iterable[Revision]) -> list[Revision]:
    """Revisions ordered oldest to newest, ignoring the order they were entered."""
    return sorted((r for r in revisions if r.code), key=lambda r: r.key)


def current(revisions: Iterable[Revision]) -> Revision | None:
    """The revision the front cover should show, or ``None`` if the log is empty.

    Correct where both v1 implementations are not: a published C-revision beats
    every preliminary one, and rows entered out of order still rank properly.
    """
    ordered = rank(revisions)
    return ordered[-1] if ordered else None


def next_code(revisions: Sequence[Revision], *, published: bool = False) -> str:
    """The code the next revision row should carry.

    Continues the requested series from its own highest number, so adding the
    first C-revision after P03 gives C01, not C04.
    """
    series = "C" if published else "P"
    highest = 0
    for r in revisions:
        try:
            s, n = parse_code(r.code)
        except ValueError:
            continue
        if s == series:
            highest = max(highest, n)
    return f"{series}{highest + 1:02d}"


def is_issued(revisions: Sequence[Revision]) -> bool:
    """Whether the issued-document lock applies (SPEC.md 5.5).

    True once the log carries anything beyond an initial preliminary row, or any
    published revision. ISO 19650's premise is that an issued reference is
    stable, so renumbering an issued schedule must be refused unless explicitly
    overridden.
    """
    real = [r for r in revisions if r.code]
    if any(r.is_published for r in real):
        return True
    return len(real) > 1
