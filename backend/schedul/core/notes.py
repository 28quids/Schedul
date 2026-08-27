"""Which notes print on a schedule, and where each one came from.

Notes arrive from four places and the order they print in is not an accident:

===============  =========================================================
``organisation`` the practice's standing wording. On every schedule.
``project``      what this job adds -- a client's requirement, a site rule.
``type``         equipment-specific wording, from the catalogue type.
``schedule``     this one document's own, when it has to differ.
===============  =========================================================

General to specific, which is the order a reader expects and the order the v1
house files used: the compliance paragraph first, "radiant panels are to be
sized with a 55degC flow" after it.

**A schedule either inherits or diverges, and says which.** When it has its own
notes they replace the resolved set rather than being appended to it, because
the reason to override is usually that one of the inherited notes is wrong for
this document -- and a model that can only add cannot express that. Reverting is
therefore always possible and always means the same thing: drop the schedule's
own copy and go back to the layers.

Resolution is here rather than in the renderer because three things need the
same answer -- the editor, the exported workbook and an issued snapshot -- and
they must not each work it out.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence

__all__ = ["ResolvedNote", "LAYERS", "resolve_notes", "note_texts", "seed_from"]

#: General to specific. The order they print in.
LAYERS: tuple[str, ...] = ("organisation", "project", "type", "schedule")


@dataclass(frozen=True, slots=True)
class ResolvedNote:
    """One note as it will print, and which layer it came from."""

    number: int
    text: str
    source: str

    def to_dict(self) -> dict[str, Any]:
        return {"number": self.number, "text": self.text, "source": self.source}


def _clean(notes: Iterable[str] | None) -> list[str]:
    return [str(n).strip() for n in (notes or []) if str(n).strip()]


def resolve_notes(
    *,
    organisation: Sequence[str] | None = (),
    project: Sequence[str] | None = (),
    type_notes: Sequence[str] | None = (),
    schedule: Sequence[str] | None = None,
) -> list[ResolvedNote]:
    """The notes that print, numbered continuously, each knowing its layer.

    ``schedule=None`` means this schedule inherits. A list -- including an empty
    one -- means it has taken the notes over, and only its own print.
    """
    if schedule is not None:
        return [
            ResolvedNote(i, text, "schedule")
            for i, text in enumerate(_clean(schedule), start=1)
        ]

    out: list[ResolvedNote] = []
    for layer, notes in (
        ("organisation", organisation),
        ("project", project),
        ("type", type_notes),
    ):
        for text in _clean(notes):
            out.append(ResolvedNote(len(out) + 1, text, layer))
    return out


def note_texts(resolved: Sequence[ResolvedNote]) -> list[str]:
    """Just the wording, for a renderer that does not care where it came from."""
    return [n.text for n in resolved]


def seed_from(resolved: Sequence[ResolvedNote]) -> list[str]:
    """The starting point when a schedule takes its notes over.

    Diverging begins from what it says now, never from nothing: the point is to
    change one line, and starting empty would make that a retype of all of them.
    """
    return note_texts(resolved)
