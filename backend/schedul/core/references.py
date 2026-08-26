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
from typing import Any

__all__ = ["split_reference", "next_reference", "fill_series", "is_incrementable"]

#: A trailing run of digits, and everything before it.
_TRAILING_DIGITS = re.compile(r"^(.*?)(\d+)$", re.DOTALL)


def split_reference(value: str) -> tuple[str, str] | None:
    """Split ``'RAD-007'`` into ``('RAD-', '007')``, or ``None`` if it has no
    trailing number."""
    match = _TRAILING_DIGITS.match(value)
    if not match:
        return None
    return match.group(1), match.group(2)


def is_incrementable(value: Any) -> bool:
    """Whether filling this value should count up rather than repeat it."""
    return isinstance(value, (str, int)) and split_reference(str(value)) is not None


def next_reference(value: str, step: int = 1) -> str:
    """The reference after this one, keeping the zero-padding.

    ``RAD-009`` -> ``RAD-010``. A value with no trailing number is returned
    unchanged, so filling a column of text repeats it rather than mangling it.
    Overflowing the padding widens it: ``RAD-099`` -> ``RAD-100``.
    """
    parts = split_reference(str(value))
    if parts is None:
        return value
    prefix, digits = parts
    incremented = int(digits) + step
    if incremented < 0:
        return value
    # Keep the original width unless the number has outgrown it.
    return f"{prefix}{str(incremented).zfill(len(digits))}"


def fill_series(seed: Any, count: int, *, mode: str = "series") -> list[Any]:
    """The values a fill-down should produce, starting from ``seed``.

    ``mode='series'`` counts up where the seed ends in digits and repeats it
    otherwise, which is what a user means by dragging a reference down.
    ``mode='copy'`` always repeats.
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
        current = next_reference(current)
        out.append(current)
    return out
