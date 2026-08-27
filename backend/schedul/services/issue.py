"""Issuing a revision, and comparing issued states.

An issued schedule is a document that has left the office. Somebody correcting a
product in the equipment library next week must not change what that document
said, and a formula fixed in the designer must not retrospectively alter a duty
a contractor has already priced.

So issuing takes a **snapshot**: the computed values as well as the typed ones,
plus the columns and notes as they were. A past revision then renders from its
snapshot rather than from live data, and cannot drift.

Issuing is an explicit action rather than something inferred from the revision
log. It matches how a document actually leaves the office, and it gives the
issued-document lock a precise moment to attach to.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Iterable

from sqlalchemy.orm import Session

from ..core.revisions import sort_key
from ..db.models import Organisation, RevisionRow, Schedule
from .columns import columns_for
from . import notes as _notes
from .converters import constant_aliases, design_constants_for
from .grid import build_grid

__all__ = ["SNAPSHOT_VERSION", "take_snapshot", "issue_revision", "diff_snapshots"]

#: Bumped if the snapshot shape ever changes, so an old one stays readable.
SNAPSHOT_VERSION = 1


def take_snapshot(
    session: Session, schedule: Schedule, org: Organisation
) -> dict[str, Any]:
    """Everything needed to reproduce this schedule without consulting live data."""
    from . import projects as svc

    building = schedule.building
    project = building.project
    house = svc.house_standard_for(session, org.id)
    scheme = svc.naming_scheme_for(session, org.id)
    stype = columns_for(schedule)
    constants = design_constants_for(project, house)

    grid = build_grid(session, schedule, stype, org.id, constant_aliases(constants))

    try:
        docnum = svc.document_number_for(schedule, scheme)
    except Exception:
        docnum = schedule.docnum

    return {
        "snapshot_version": SNAPSHOT_VERSION,
        "taken_at": _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None).isoformat(),
        "docnum": docnum,
        "type_code": stype.code,
        "type_title": stype.title,
        "type_version": schedule.type_version,
        "building": building.label,
        "project_fields": project.project_fields,
        "design_constants": constants,
        # The notes as they resolved at the moment of issue, not the layers they
        # came from: a note reworded in the house standard next month must not
        # change what an issued document said.
        "notes": [n.text for n in _notes.resolved_notes(schedule, stype, house, project)],
        "columns": [c.to_dict() for c in stype.columns],
        # The computed values are the point: they are what stops a later library
        # correction changing the meaning of a document already issued.
        "rows": [
            {
                "position": row.position,
                "values": dict(stored.values or {}),
                "overrides": dict(stored.overrides or {}),
                "computed": {name: cell.value for name, cell in row.cells.items()},
            }
            for row, stored in zip(grid.rows, schedule.rows)
        ],
    }


def issue_revision(
    session: Session, schedule: Schedule, revision: RevisionRow, org: Organisation
) -> RevisionRow:
    """Freeze a revision. Idempotent: re-issuing does not overwrite the record.

    Refusing to re-snapshot is deliberate. If a revision could be re-issued in
    place, the guarantee it exists to provide -- that this is what went out --
    would be worth nothing.
    """
    if revision.snapshot is not None:
        return revision

    revision.snapshot = take_snapshot(session, schedule, org)
    revision.issued_at = _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)
    if revision.code:
        revision.sort_key = sort_key(revision.code)
    session.flush()
    return revision


# ------------------------------------------------------------------ diff ---


def _row_key(row: dict[str, Any], columns: list[dict[str, Any]]) -> str:
    """Identify a row across revisions by its first input column.

    That column is the equipment reference in every house schedule, which is
    what makes two revisions comparable at all. Falling back to position would
    report every row below an insertion as changed.
    """
    for column in columns:
        if column.get("kind") == "input":
            name = column.get("name", "")
            unit = column.get("unit", "")
            key = f"{name} ({unit})" if unit else name
            value = (row.get("values") or {}).get(key)
            if value not in (None, ""):
                return str(value)
    return f"#{row.get('position', 0)}"


def diff_snapshots(
    before: dict[str, Any] | None, after: dict[str, Any] | None
) -> dict[str, Any]:
    """What changed between two snapshots, row by row and field by field.

    Compares the **computed** values, because that is what a reader of the
    document sees: a duty that moved because the library was corrected is a real
    change to them, even though nobody retyped anything.
    """
    if not before or not after:
        return {"comparable": False, "added": [], "removed": [], "changed": [], "unchanged": 0}

    columns = after.get("columns") or before.get("columns") or []
    names = [
        f"{c['name']} ({c['unit']})" if c.get("unit") else c["name"]
        for c in columns
    ]

    old = {_row_key(r, columns): r for r in before.get("rows", [])}
    new = {_row_key(r, columns): r for r in after.get("rows", [])}

    def is_blank(row: dict[str, Any]) -> bool:
        return not any(v not in (None, "") for v in (row.get("values") or {}).values())

    added = [k for k in new if k not in old and not is_blank(new[k])]
    removed = [k for k in old if k not in new and not is_blank(old[k])]

    changed: list[dict[str, Any]] = []
    unchanged = 0
    for key in new:
        if key not in old:
            continue
        fields = []
        for name in names:
            was = (old[key].get("computed") or {}).get(name)
            now = (new[key].get("computed") or {}).get(name)
            if _differs(was, now):
                fields.append({"column": name, "before": was, "after": now})
        if fields:
            changed.append({"reference": key, "fields": fields})
        else:
            unchanged += 1

    return {
        "comparable": True,
        "added": sorted(added),
        "removed": sorted(removed),
        "changed": changed,
        "unchanged": unchanged,
    }


def _differs(a: Any, b: Any) -> bool:
    """Compare two cell values, treating blank forms of nothing as equal."""
    if a in (None, "") and b in (None, ""):
        return False
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        # A recomputation can move a float in the last bit without meaning
        # anything; a real change to a duty is never that small.
        return abs(float(a) - float(b)) > 1e-9
    return str(a) != str(b)
