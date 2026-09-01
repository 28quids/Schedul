"""What "the columns of this schedule" means, in one place.

A schedule's columns are the catalogue type's, plus anything the project adds on
top, filtered by where they are being shown. Three things need that answer — the
editor grid, the workbook renderer, and the formula validator — and if each
worked it out separately they would disagree the first time somebody added a
project column that a derived formula referenced.

**Hiding is decided at two levels.** The catalogue says where a column belongs
in general; one schedule can then hide a column on its own deliverables, because
"keep the cost off the copy that goes to this client" is a decision about one
document. A schedule's answer wins where it has one.
"""

from __future__ import annotations

from typing import Any, Sequence

from ..core.catalogue import MODEL_REFERENCE, Column, ScheduleType
from ..core.formula import field_names
from ..db.models import Project, Schedule
from .converters import type_from_row

__all__ = [
    "project_extras",
    "merged_type",
    "columns_for",
    "set_project_extras",
    "TARGETS",
    "apply_schedule_visibility",
    "formula_dependencies",
    "visibility_view",
    "validate_visibility",
]

#: Where a column can be shown or hidden. The editor is included because a
#: column nobody on this job fills in is noise on the screen as much as on the
#: page, and a schedule that hides it everywhere is saying exactly that.
TARGETS: tuple[str, ...] = ("editor", "xlsx", "pdf")


def project_extras(project: Project, type_code: str) -> list[Column]:
    """The extra columns this project adds to one catalogue type."""
    raw = (project.type_extras or {}).get(type_code.upper()) or []
    return [Column.from_dict(c) for c in raw]


def merged_type(
    project: Project, schedule_type: ScheduleType, *, target: str | None = None
) -> ScheduleType:
    """The type as this project sees it, optionally filtered for one target.

    ``target`` is ``editor``, ``xlsx`` or ``pdf``. Omit it to get every column,
    which is what validation and snapshotting want.
    """
    merged = schedule_type.with_extras(project_extras(project, schedule_type.code))
    return merged.visible_columns(target) if target else merged


def apply_schedule_visibility(
    stype: ScheduleType, hidden: dict[str, Any] | None
) -> ScheduleType:
    """Fold one schedule's own show/hide decisions into a type's columns.

    Returns a type whose columns carry the merged visibility, so everything
    downstream keeps asking ``visible_in`` and nothing has to know that a
    schedule can have an opinion.
    """
    if not hidden:
        return stype
    columns: list[Column] = []
    for column in stype.columns:
        own = hidden.get(column.legacy_name) or hidden.get(column.name)
        if not own:
            columns.append(column)
            continue
        merged = {**column.visibility}
        for target, shown in own.items():
            if target in TARGETS:
                merged[target] = bool(shown)
        columns.append(Column(**{**column.to_dict(), "visibility": merged}))
    return ScheduleType(
        code=stype.code, title=stype.title, short=stype.short, version=stype.version,
        volume=stype.volume, columns=columns, notes=list(stype.notes),
        created=stype.created, updated=stype.updated, history=list(stype.history),
    )


def columns_for(
    schedule: Schedule, *, target: str | None = None
) -> ScheduleType:
    """The merged type for a schedule, resolved from its own project.

    The schedule's own hidden columns are applied before the target filter, so
    ``columns_for(schedule, target="pdf")`` is the honest answer to "what goes on
    the PDF" wherever it is asked.
    """
    full = apply_schedule_visibility(
        merged_type(schedule.building.project, type_from_row(schedule.schedule_type)),
        schedule.column_visibility,
    )
    return full.visible_columns(target) if target else full


def formula_dependencies(stype: ScheduleType) -> dict[str, list[str]]:
    """Which columns each derived column reads, keyed by the operand's name.

    A hidden column is simply not written to the sheet, so a formula that reads
    one would emit a reference to a column that is not there. Rather than
    letting that produce a broken workbook, this is what lets the API refuse the
    hide and say which calculation needs the column.
    """
    needed: dict[str, list[str]] = {}
    for column in stype.derived:
        try:
            node = stype.parse_formula(column)
        except Exception:
            continue
        for name in field_names(node):
            needed.setdefault(name, []).append(column.name)
    return needed


def visibility_view(schedule: Schedule) -> list[dict[str, Any]]:
    """Every column on this schedule, with where it shows and whether it can be
    hidden.

    The lookup key and any column a calculation reads are reported as fixed, in
    the same breath as being listed, so the screen cannot offer a switch that
    would produce a workbook with a broken reference in it.
    """
    stype = columns_for(schedule)
    needed = formula_dependencies(stype)
    own = schedule.column_visibility or {}

    out: list[dict[str, Any]] = []
    for column in stype.layout():
        key = column.legacy_name
        is_reference = column.name == MODEL_REFERENCE
        readers = needed.get(key, []) if not is_reference else []
        stored = own.get(key) or {}
        out.append(
            {
                "name": column.name,
                "legacy_name": key,
                "kind": column.kind,
                "unit": column.unit,
                "visibility": {t: bool(stored.get(t, column.visible_in(t))) for t in TARGETS},
                "hideable": not is_reference and not readers,
                "reason": (
                    "the lookup key every row points at"
                    if is_reference
                    else (
                        f"read by {', '.join(sorted(set(readers)))}"
                        if readers
                        else ""
                    )
                ),
            }
        )
    return out


def validate_visibility(
    schedule: Schedule, wanted: dict[str, Any]
) -> tuple[dict[str, dict[str, bool]], list[str]]:
    """Clean a requested set of hidden columns, and say what was refused.

    Returns the visibility to store and the problems worth telling somebody
    about. Anything already visible is dropped rather than stored as ``True``:
    the stored value is the exceptions, so an empty dict means "as the catalogue
    says", which is what a schedule should read as by default.
    """
    stype = columns_for(schedule)
    by_key = {c.legacy_name: c for c in stype.layout()}
    needed = formula_dependencies(stype)

    cleaned: dict[str, dict[str, bool]] = {}
    problems: list[str] = []

    for key, targets in (wanted or {}).items():
        column = by_key.get(key)
        if column is None:
            problems.append(f"{key!r} is not a column on this schedule")
            continue
        hidden_targets = {
            t: False for t, shown in (targets or {}).items()
            if t in TARGETS and shown is False
        }
        if not hidden_targets:
            continue
        if column.name == MODEL_REFERENCE:
            problems.append(
                f"{MODEL_REFERENCE} is the key every row points at, so it cannot be hidden"
            )
            continue
        if key in needed:
            readers = ", ".join(sorted(set(needed[key])))
            problems.append(
                f"{column.name} is read by {readers}, so hiding it would leave a "
                f"calculation with nothing to read"
            )
            continue
        cleaned[key] = hidden_targets
    return cleaned, problems


def set_project_extras(
    project: Project, type_code: str, columns: Sequence[Column]
) -> None:
    """Replace one type's extra columns on a project.

    Reassigns the whole dict rather than mutating in place: SQLAlchemy does not
    track mutation inside a JSON column, so an in-place edit would not be saved.
    """
    extras = dict(project.type_extras or {})
    code = type_code.upper()
    if columns:
        extras[code] = [
            {**c.to_dict(), "project_extra": True} for c in columns
        ]
    else:
        extras.pop(code, None)
    project.type_extras = extras
