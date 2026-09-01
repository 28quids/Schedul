"""Bringing a supplier's product list into the equipment library.

A manufacturer's range arrives as forty rows of a spreadsheet, and entering it
one modal at a time is the reason the library is thin. So: paste the block, say
which column is which, and see exactly what would happen before anything is
written.

**Nothing is written until it has been shown.** The plan says, per row, whether
it would create a product, update one, leave one alone or be refused, and why.
Applying it takes that same plan and carries it out. That is SPEC.md's rule for
a destructive operation, and an import is one: a careless mapping can overwrite
a hundred correct values with a hundred wrong ones in a single click.

**Updating never blanks a field.** A supplier's sheet is usually partial -- it
has the dimensions and not the weights -- so a blank cell means "not stated
here", not "delete what you know". Clearing a value stays a deliberate edit on
the product itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field as _field
from typing import Any, Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.catalogue import MODEL_REFERENCE, ScheduleType
from ..core.tabular import Block, map_columns, read_block, rows_to_values
from ..db.models import Equipment
from .library import keynorm, norm, save_equipment

__all__ = [
    "RowPlan", "ImportPlan", "plan_import", "apply_import", "target_columns",
    "is_template_example",
]

#: What an import may do to one row.
CREATE = "create"
UPDATE = "update"
UNCHANGED = "unchanged"
SKIP = "skip"


#: The reference the blank template puts in its example row. It is scaffolding
#: rather than a product, and importing the template unedited should add nothing.
_EXAMPLE = re.compile(r"^(?P<code>[A-Z0-9]+)-EXAMPLE-\d+$", re.IGNORECASE)


def is_template_example(reference: str, type_code: str) -> bool:
    """Whether this row is the exported template's own example row."""
    match = _EXAMPLE.match(str(reference or "").strip())
    return bool(match) and match.group("code").upper() == str(type_code).upper()


def target_columns(schedule_type: ScheduleType) -> list[str]:
    """The columns an import can fill: the lookup key and the library fields.

    Input and derived columns are deliberately not offered. An input value
    differs per unit and a derived one is calculated, so either would be a
    stale copy the moment it landed.
    """
    return [MODEL_REFERENCE, *(c.legacy_name for c in schedule_type.library)]


@dataclass(slots=True)
class RowPlan:
    """What would happen to one row of the pasted block."""

    line: int
    model_reference: str
    values: dict[str, Any]
    action: str
    #: Existing values this row would change, as ``{column: [before, after]}``.
    changes: dict[str, list[Any]] = _field(default_factory=dict)
    reason: str = ""
    equipment_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "line": self.line,
            "model_reference": self.model_reference,
            "values": self.values,
            "action": self.action,
            "changes": [
                {"column": c, "before": pair[0], "after": pair[1]}
                for c, pair in self.changes.items()
            ],
            "reason": self.reason,
            "equipment_id": self.equipment_id,
        }


@dataclass(slots=True)
class ImportPlan:
    """The whole import, before any of it happens."""

    type_code: str
    columns: list[str | None] = _field(default_factory=list)
    header: list[str] | None = None
    header_detected: bool = False
    rows: list[RowPlan] = _field(default_factory=list)
    warnings: list[str] = _field(default_factory=list)
    #: Set once the plan has been carried out.
    applied: int = 0

    @property
    def counts(self) -> dict[str, int]:
        out = {CREATE: 0, UPDATE: 0, UNCHANGED: 0, SKIP: 0}
        for row in self.rows:
            out[row.action] = out.get(row.action, 0) + 1
        return out

    @property
    def can_apply(self) -> bool:
        return any(r.action in (CREATE, UPDATE) for r in self.rows)

    @property
    def destructive(self) -> bool:
        """Whether applying would change values already in the library."""
        return any(r.action == UPDATE and r.changes for r in self.rows)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type_code": self.type_code,
            "columns": list(self.columns),
            "header": self.header,
            "header_detected": self.header_detected,
            "rows": [r.to_dict() for r in self.rows],
            "counts": self.counts,
            "warnings": list(self.warnings),
            "can_apply": self.can_apply,
            "destructive": self.destructive,
            "applied": self.applied,
        }


def _existing(
    session: Session, organisation_id: str, type_code: str
) -> dict[str, Equipment]:
    """Everything already in the library for this type, keyed for tolerant match.

    Keyed on a normalised reference so ``SYS-VSR-500`` and ``sys-vsr-500`` are
    the same product. A supplier's capitalisation is not a new item.
    """
    stmt = select(Equipment).where(
        Equipment.organisation_id == organisation_id,
        Equipment.type_code == type_code,
    )
    return {e.model_reference.strip().lower(): e for e in session.scalars(stmt)}


def plan_import(
    session: Session,
    organisation_id: str,
    schedule_type: ScheduleType,
    text: str,
    *,
    mapping: Sequence[str | None] | None = None,
    header: bool | None = None,
    update_existing: bool = True,
) -> ImportPlan:
    """Work out what importing this block would do. Writes nothing.

    ``mapping`` is the caller's column choice, position by position; without one
    the header is matched by name, or the columns are taken left to right.
    """
    columns = target_columns(schedule_type)
    block: Block = read_block(text, column_names=columns, header=header)

    resolved: list[str | None] = (
        [c if c in columns else None for c in mapping]
        if mapping is not None
        else map_columns(block.header, columns, width=block.width)
    )

    plan = ImportPlan(
        type_code=schedule_type.code,
        columns=resolved,
        header=block.header,
        header_detected=block.header_detected,
        warnings=list(block.warnings),
    )

    if MODEL_REFERENCE not in [c for c in resolved if c]:
        plan.warnings.append(
            f"No column is mapped to {MODEL_REFERENCE}. It is the key every "
            f"schedule row points at, so nothing can be imported without it."
        )
        return plan

    existing = _existing(session, organisation_id, schedule_type.code)
    seen: dict[str, int] = {}

    for index, values in enumerate(rows_to_values(block, resolved), start=1):
        reference = str(values.pop(MODEL_REFERENCE, "") or "").strip()
        row = RowPlan(line=index, model_reference=reference, values=values, action=SKIP)

        if not reference:
            row.reason = f"no {MODEL_REFERENCE}, so there is nothing to key this on"
            plan.rows.append(row)
            continue

        if is_template_example(reference, schedule_type.code):
            row.reason = "the template's own example row, not a product"
            plan.rows.append(row)
            continue

        key = reference.lower()
        if key in seen:
            row.action = SKIP
            row.reason = f"the same reference appears on line {seen[key]} of this paste"
            plan.rows.append(row)
            continue
        seen[key] = index

        match = existing.get(key)
        if match is None:
            row.action = CREATE
            plan.rows.append(row)
            continue

        row.equipment_id = match.id
        if not update_existing:
            row.action = SKIP
            row.reason = f"{reference} is already in the library"
            plan.rows.append(row)
            continue

        # A blank cell means "not stated here", never "delete what you know".
        current = match.values or {}
        changes = {
            column: [current.get(column), value]
            for column, value in values.items()
            if norm(value) and keynorm(norm(current.get(column))) != keynorm(norm(value))
        }
        row.changes = changes
        row.action = UPDATE if changes else UNCHANGED
        if not changes:
            row.reason = f"{reference} is already in the library with these values"
        plan.rows.append(row)

    if not plan.rows:
        plan.warnings.append("nothing to import: no data rows were found")
    return plan


def apply_import(
    session: Session,
    organisation_id: str,
    schedule_type: ScheduleType,
    plan: ImportPlan,
    *,
    created_by: str = "",
) -> ImportPlan:
    """Carry out a plan. Only the rows that would create or update are written.

    Saving goes through ``library.save_equipment``, so an imported product is
    flagged for review and logged exactly like one typed on a schedule -- an
    import is a faster way in, not a way round the checks.
    """
    for row in plan.rows:
        if row.action not in (CREATE, UPDATE):
            continue
        save_equipment(
            session,
            organisation_id,
            schedule_type,
            row.model_reference,
            row.values,
            created_by=created_by,
            source="import",
        )
        plan.applied += 1
    session.flush()
    return plan
