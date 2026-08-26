"""The shared equipment library, and the review list.

Equipment entered on a schedule is saved to the organisation's library
immediately and is usable at once. v1's submissions inbox existed to stop
concurrent writes corrupting a shared ``.xlsx``; a database does not have that
problem, so the queue no longer has to gate use.

What is worth keeping from ``merge_submissions.py`` is its **detection** --
NEW / DUPLICATE / CONFLICT and spelling drift (``GRUNDFOS`` against an existing
``Grundfos``) -- which becomes the review list's ranking rather than a gate.
The tolerant key matching and the 0.86 similarity threshold are ported
unchanged; they were tuned against real submissions.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field as _field
from typing import Any, Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.catalogue import ScheduleType
from ..db.models import Equipment, EquipmentFlag

__all__ = [
    "DRIFT_RATIO",
    "keynorm",
    "norm",
    "Finding",
    "inspect_entry",
    "save_equipment",
    "review_queue",
    "resolve_flag",
    "set_review_state",
]

#: Similarity at or above which two values are treated as spelling drift.
#: Ported from merge_submissions.py, where it was tuned on real submissions.
DRIFT_RATIO = 0.86


def keynorm(key: str) -> str:
    """Tolerant field-key match: m2/m², degC/°C, spacing and case."""
    key = (
        key.replace("²", "2")
        .replace("³", "3")
        .replace("°C", "degC")
        .replace("°", "deg")
    )
    return " ".join(key.split()).lower()


def norm(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in ("none", "nan") else text


@dataclass(slots=True)
class Finding:
    """One thing worth telling the reviewer about an entry."""

    kind: str  # NEW | DUPLICATE | CONFLICT | DRIFT | INCOMPLETE
    message: str
    related_id: str | None = None


def _existing(session: Session, organisation_id: str, type_code: str) -> list[Equipment]:
    return list(
        session.scalars(
            select(Equipment).where(
                Equipment.organisation_id == organisation_id,
                Equipment.type_code == type_code,
            )
        )
    )


def _find_drift(existing: Sequence[Equipment], column: str, value: str) -> list[str]:
    """Near-miss spellings of ``value`` already in the library for this column."""
    if not value or len(value) < 3 or value.replace(".", "").isdigit():
        return []
    seen: set[str] = set()
    out: list[str] = []
    target = value.lower()
    for entry in existing:
        other = norm((entry.values or {}).get(column))
        if not other or other == value or other.lower() in seen:
            continue
        seen.add(other.lower())
        if difflib.SequenceMatcher(None, other.lower(), target).ratio() >= DRIFT_RATIO:
            out.append(other)
    return out


def inspect_entry(
    session: Session,
    organisation_id: str,
    schedule_type: ScheduleType,
    model_reference: str,
    values: dict[str, Any],
) -> list[Finding]:
    """Everything the review list should know about a submitted entry.

    Read-only: this decides how the entry is *ranked*, not whether it is saved.
    """
    findings: list[Finding] = []
    existing = _existing(session, organisation_id, schedule_type.code)
    by_ref = {e.model_reference.lower(): e for e in existing}

    match = by_ref.get(model_reference.strip().lower())
    if match is None:
        findings.append(Finding("NEW", f"{model_reference!r} is new to the library"))
    else:
        differing = []
        for col in schedule_type.library:
            key = col.legacy_name
            was = norm((match.values or {}).get(key))
            now = norm(values.get(key))
            if was and now and keynorm(was) != keynorm(now):
                differing.append(f"{col.name}: {was!r} -> {now!r}")
        if differing:
            findings.append(
                Finding(
                    "CONFLICT",
                    f"{model_reference!r} already exists with different values: "
                    + "; ".join(differing[:5]),
                    match.id,
                )
            )
        else:
            findings.append(
                Finding("DUPLICATE", f"{model_reference!r} already exists, unchanged", match.id)
            )

    for col in schedule_type.library:
        key = col.legacy_name
        value = norm(values.get(key))
        for near in _find_drift(existing, key, value):
            findings.append(
                Finding(
                    "DRIFT",
                    f"{col.name}: {value!r} looks like a misspelling of "
                    f"the existing {near!r}",
                )
            )

    blank = [c.name for c in schedule_type.library if not norm(values.get(c.legacy_name))]
    if blank:
        findings.append(
            Finding(
                "INCOMPLETE",
                f"{len(blank)} product field(s) left blank: {', '.join(blank[:6])}",
            )
        )

    return findings


def save_equipment(
    session: Session,
    organisation_id: str,
    schedule_type: ScheduleType,
    model_reference: str,
    values: dict[str, Any],
    *,
    created_by: str = "",
    source: str = "schedule",
) -> tuple[Equipment, list[Finding]]:
    """Save a product to the library and flag it for review.

    Live immediately, so nobody is blocked mid-schedule. The findings are
    attached as flags for the review list to rank by.
    """
    model_reference = model_reference.strip()
    if not model_reference:
        raise ValueError("a library entry needs a Model Reference")

    findings = inspect_entry(
        session, organisation_id, schedule_type, model_reference, values
    )

    # Only the type's library columns belong in the library; input and derived
    # values are per-unit or calculated and would be stale the moment they land.
    allowed = {c.legacy_name for c in schedule_type.library}
    cleaned = {k: v for k, v in values.items() if k in allowed}

    entry = session.scalar(
        select(Equipment).where(
            Equipment.organisation_id == organisation_id,
            Equipment.type_code == schedule_type.code,
            Equipment.model_reference == model_reference,
        )
    )
    if entry is None:
        entry = Equipment(
            organisation_id=organisation_id,
            type_code=schedule_type.code,
            model_reference=model_reference,
            values=cleaned,
            created_by=created_by,
            source=source,
        )
        session.add(entry)
        session.flush()
    else:
        merged = dict(entry.values or {})
        merged.update({k: v for k, v in cleaned.items() if norm(v)})
        entry.values = merged
        if entry.review_state == "rejected":
            entry.review_state = "live"
        session.flush()

    for finding in findings:
        if finding.kind == "DUPLICATE":
            continue  # nothing changed; not worth a reviewer's attention
        session.add(
            EquipmentFlag(
                equipment_id=entry.id,
                kind=finding.kind,
                message=finding.message,
                related_id=finding.related_id,
            )
        )
    session.flush()
    return entry, findings


def review_queue(
    session: Session, organisation_id: str, *, include_resolved: bool = False
) -> list[dict[str, Any]]:
    """Entries needing a look, worst first.

    Ranked by what the flags say rather than by date: a conflict between two
    people's numbers for the same product matters more than an incomplete row.
    """
    priority = {"CONFLICT": 0, "DRIFT": 1, "INCOMPLETE": 2, "NEW": 3}

    stmt = select(Equipment).where(Equipment.organisation_id == organisation_id)
    out: list[dict[str, Any]] = []
    for entry in session.scalars(stmt):
        flags = [f for f in entry.flags if include_resolved or not f.resolved]
        if not flags:
            continue
        flags.sort(key=lambda f: priority.get(f.kind, 9))
        worst = priority.get(flags[0].kind, 9)
        out.append(
            {
                "id": entry.id,
                "type_code": entry.type_code,
                "model_reference": entry.model_reference,
                "review_state": entry.review_state,
                "created_by": entry.created_by,
                "updated_at": entry.updated_at,
                "values": entry.values,
                "rank": worst,
                "flags": [
                    {
                        "id": f.id,
                        "kind": f.kind,
                        "message": f.message,
                        "related_id": f.related_id,
                        "resolved": f.resolved,
                    }
                    for f in flags
                ],
            }
        )

    out.sort(key=lambda e: (e["rank"], e["type_code"], e["model_reference"]))
    return out


def resolve_flag(session: Session, flag_id: str) -> None:
    flag = session.get(EquipmentFlag, flag_id)
    if flag is not None:
        flag.resolved = True
        session.flush()


def set_review_state(session: Session, equipment_id: str, state: str) -> Equipment:
    """Approve or reject an entry.

    Rejecting hides it from lookups; it never deletes it, so a schedule that
    already references the product keeps a record of what was chosen.
    """
    if state not in ("live", "approved", "rejected"):
        raise ValueError(f"unknown review state {state!r}")
    entry = session.get(Equipment, equipment_id)
    if entry is None:
        raise ValueError("no such equipment")
    entry.review_state = state
    if state in ("approved", "rejected"):
        for flag in entry.flags:
            flag.resolved = True
    session.flush()
    return entry
