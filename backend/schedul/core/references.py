"""Incrementing equipment references.

Schedules are repetitive: ``RAD-001``, ``RAD-002``, ``RAD-003``. Working out
what comes next is a domain rule, not a UI convenience, so it lives here and is
tested here. The grid's fill-down uses it, and an importer or a bulk-add would
use the same function rather than reimplementing the padding logic and getting
it subtly different.

The rule is deliberately narrow: only a trailing run of digits increments, and
its width is preserved. ``RAD-009`` becomes ``RAD-010``, not ``RAD-0010``.
"""

from __future__ import annotations

import re
from typing import Any, Sequence

__all__ = [
    "split_reference", "next_reference", "fill_series", "is_incrementable",
    "digit_runs", "varying_run",
]

#: A trailing run of digits, and everything before it.
_TRAILING_DIGITS = re.compile(r"^(.*?)(\d+)$", re.DOTALL)

#: Every run of digits in a value, wherever it sits.
_DIGITS = re.compile(r"\d+")


def digit_runs(value: Any) -> list[tuple[int, int]]:
    """The ``(start, end)`` span of every run of digits, left to right.

    ``'RM0.01 2 Bedroom'`` has three: the 0, the 01 and the 2. Which of them a
    fill should count is the whole question -- see :func:`varying_run`.
    """
    return [m.span() for m in _DIGITS.finditer(str(value))]


def split_reference(value: str) -> tuple[str, str] | None:
    """Split ``'RAD-007'`` into ``('RAD-', '007')``, or ``None`` if it has no
    trailing number."""
    match = _TRAILING_DIGITS.match(value)
    if not match:
        return None
    return match.group(1), match.group(2)


def is_incrementable(value: Any) -> bool:
    """Whether filling this value should count up rather than repeat it.

    True for anything with a number in it, not only a trailing one:
    ``'RM0.01 2 Bedroom'`` is a room reference somebody expects to count, and
    repeating it two hundred times is not what dragging it down means.
    """
    return isinstance(value, (str, int)) and bool(digit_runs(value))


def varying_run(values: Sequence[Any]) -> int | None:
    """Which run of digits differs across these seeds, as an index.

    Two or more filled cells say what a single one cannot. ``RM0.01 2 Bedroom``
    alone could count either number; alongside ``RM0.02 2 Bedroom`` it plainly
    counts the second run and leaves the bed count alone. Returns None when the
    seeds do not agree on exactly one run.
    """
    texts = [str(v) for v in values if v not in (None, "")]
    if len(texts) < 2:
        return None
    runs = [digit_runs(t) for t in texts]
    if len({len(r) for r in runs}) != 1 or not runs[0]:
        return None

    differing = [
        index
        for index in range(len(runs[0]))
        if len({t[span[0]:span[1]] for t, span in zip(texts, [r[index] for r in runs])}) > 1
    ]
    return differing[0] if len(differing) == 1 else None


def next_reference(value: str, step: int = 1, index: int = -1) -> str:
    """The reference after this one, keeping the zero-padding.

    ``RAD-009`` -> ``RAD-010``. ``index`` chooses which run of digits counts,
    from the left; the default -1 is the last one, which is what a spreadsheet
    does. A value with no number at all is returned unchanged, so filling a
    column of text repeats it rather than mangling it. Overflowing the padding
    widens it: ``RAD-099`` -> ``RAD-100``.
    """
    text = str(value)
    runs = digit_runs(text)
    if not runs:
        return value
    try:
        start, end = runs[index]
    except IndexError:
        start, end = runs[-1]

    digits = text[start:end]
    incremented = int(digits) + step
    if incremented < 0:
        return value
    # Keep the original width unless the number has outgrown it.
    return text[:start] + str(incremented).zfill(len(digits)) + text[end:]


def fill_series(
    seed: Any, count: int, *, mode: str = "series", step: int = 1, index: int = -1
) -> list[Any]:
    """The values a fill-down should produce, starting from ``seed``.

    ``mode='series'`` counts up where the seed ends in digits and repeats it
    otherwise, which is what a user means by dragging a reference down.
    ``mode='copy'`` always repeats.

    ``step`` is what a fill handle dragged upwards needs: from ``RAD-005`` a
    spreadsheet gives ``RAD-004, RAD-003``, not the same value five times. A
    step that would take the number below zero stops counting and repeats the
    last value it could reach, because ``RAD--001`` is not a reference.
    """
    if count <= 0:
        return []
    if mode not in ("series", "copy"):
        raise ValueError(f"unknown fill mode {mode!r}")

    if mode == "copy" or not is_incrementable(seed):
        return [seed] * count

    out: list[Any] = []
    current = str(seed)
    for _ in range(count):
        current = next_reference(current, step, index)
        out.append(current)
    return out
