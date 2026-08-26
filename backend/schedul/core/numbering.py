"""Document number allocation and the renumber operations.

This is the most-used part of the tool and the part most likely to be got wrong,
so none of it lives in the UI.

**Numbering restarts per building.** HQ049's schedules are 10, 11, 12 and
HQ014's are also 10, 11, 12. That is deliberate: the ``building`` token already
differentiates the document numbers, so ``...-HQ049-SC-M-00000010-...`` and
``...-HQ014-SC-M-00000010-...`` are distinct documents. Restarting per building
also means adding a block later does not depend on what the others did, and two
buildings with different equipment do not produce baffling gaps in each other's
sequences. Allocation, retirement and the issued-document lock all operate
**within a building**, never across the project.

**Free-text number editing produces collisions immediately**, so it is not
offered. The four explicit operations here, plus ``rebase``, cover the real
cases, and every one of them returns a plan the user confirms before anything
changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field as _field
from typing import Callable, Iterable, Literal, Sequence

__all__ = [
    "ScheduleRef",
    "NumberChange",
    "RenumberPlan",
    "AllocationError",
    "next_number",
    "allocate",
    "retire",
    "set_number",
    "swap",
    "insert_at",
    "compact",
    "rebase",
    "audit",
    "AuditIssue",
]

#: How close to exhausting the number width before we warn.
EXHAUSTION_WARNING_MARGIN = 10


class AllocationError(Exception):
    """A number cannot be allocated or an operation cannot be planned."""


@dataclass(slots=True)
class ScheduleRef:
    """What numbering needs to know about one schedule in a building.

    Deliberately not the database row: numbering is pure, so it can be tested
    and reasoned about without a session.
    """

    code: str
    number: int
    title: str = ""
    volume: str = ""
    docnum: str = ""
    filename: str = ""
    status: str = "S0"
    locked: bool = False
    lock_reason: str = ""
    state: Literal["allocated", "built", "missing"] = "built"


@dataclass(slots=True)
class NumberChange:
    """One schedule's move, as the plan table shows it."""

    code: str
    old_number: int
    new_number: int
    old_docnum: str = ""
    new_docnum: str = ""
    old_filename: str = ""
    new_filename: str = ""
    blocked: str | None = None

    @property
    def changed(self) -> bool:
        return self.old_number != self.new_number


@dataclass(slots=True)
class RenumberPlan:
    """What an operation would do. Nothing has happened yet.

    The UI shows this as a table with a blocked-row count, and Apply stays
    disabled until nothing is blocked or the user has overridden the lock.
    """

    operation: str
    changes: list[NumberChange] = _field(default_factory=list)
    warnings: list[str] = _field(default_factory=list)

    @property
    def blocked(self) -> list[NumberChange]:
        return [c for c in self.changes if c.blocked]

    @property
    def moves(self) -> list[NumberChange]:
        """Only the rows that actually change number."""
        return [c for c in self.changes if c.changed]

    @property
    def can_apply(self) -> bool:
        return not self.blocked and bool(self.moves)

    def describe(self) -> str:
        if not self.moves:
            return f"{self.operation}: nothing to change"
        lines = [f"{self.operation}: {len(self.moves)} schedule(s) would change"]
        for c in self.moves:
            mark = f"  BLOCKED ({c.blocked})" if c.blocked else ""
            lines.append(f"  {c.code}: {c.old_number} -> {c.new_number}{mark}")
        return "\n".join(lines)


# --------------------------------------------------------------- allocation ---


def next_number(
    existing: Iterable[int], retired: Iterable[int] = (), *, start: int = 10
) -> int:
    """The next number to allocate in this building.

    ``max(everything ever used) + 1``, or ``start`` if the building has none.
    "Ever used" includes retired numbers, so removing a schedule and adding
    another does not silently reuse a number that has already been issued.
    """
    used = [*existing, *retired]
    return max(used) + 1 if used else start


def allocate(
    schedules: Sequence[ScheduleRef],
    retired: Sequence[int] = (),
    *,
    start: int = 10,
    width: int = 8,
) -> tuple[int, list[str]]:
    """Allocate the next number, with any warnings about the sequence."""
    number = next_number((s.number for s in schedules), retired, start=start)
    warnings: list[str] = []
    ceiling = 10**width - 1
    if number > ceiling:
        raise AllocationError(
            f"number {number} does not fit in {width} digits; "
            f"widen the 'number' token or rebase this building"
        )
    if ceiling - number < EXHAUSTION_WARNING_MARGIN:
        warnings.append(
            f"only {ceiling - number} numbers left before this building exhausts "
            f"the {width}-digit width"
        )
    return number, warnings


def retire(retired: Sequence[int], number: int) -> list[int]:
    """Retire a number within a building. Retired numbers are not reused.

    Removing a schedule never deletes the file or the data -- "remove" means
    remove from the record -- so the number stays spoken for.
    """
    if number in retired:
        return list(retired)
    return sorted([*retired, number])


# ------------------------------------------------------------- operations ---

#: Recomputes ``(docnum, filename)`` for a schedule at a proposed number.
Renderer = Callable[[ScheduleRef, int], tuple[str, str]]


def _plan(
    operation: str,
    schedules: Sequence[ScheduleRef],
    assignment: dict[str, int],
    renderer: Renderer | None,
    *,
    allow_locked: Sequence[str] = (),
) -> RenumberPlan:
    """Turn a proposed ``{code: number}`` assignment into a reviewable plan."""
    plan = RenumberPlan(operation=operation)
    by_code = {s.code: s for s in schedules}

    # Collisions among the proposal itself.
    seen: dict[int, str] = {}
    collided: dict[str, str] = {}
    for code, number in assignment.items():
        if number in seen:
            collided[code] = f"number {number} also assigned to {seen[number]}"
            collided.setdefault(seen[number], f"number {number} also assigned to {code}")
        else:
            seen[number] = code

    for code, new_number in assignment.items():
        sched = by_code[code]
        change = NumberChange(
            code=code,
            old_number=sched.number,
            new_number=new_number,
            old_docnum=sched.docnum,
            old_filename=sched.filename,
        )
        if new_number != sched.number and renderer is not None:
            change.new_docnum, change.new_filename = renderer(sched, new_number)
        else:
            change.new_docnum, change.new_filename = sched.docnum, sched.filename

        if code in collided:
            change.blocked = collided[code]
        elif new_number != sched.number and sched.locked and code not in allow_locked:
            change.blocked = sched.lock_reason or "issued document; renumbering is locked"
        elif new_number < 1:
            change.blocked = f"number {new_number} is not valid"

        plan.changes.append(change)

    plan.changes.sort(key=lambda c: c.new_number)
    return plan


def set_number(
    schedules: Sequence[ScheduleRef],
    code: str,
    number: int,
    renderer: Renderer | None = None,
    *,
    allow_locked: Sequence[str] = (),
) -> RenumberPlan:
    """Give one schedule an explicit number. Rejected on collision with a live one."""
    by_code = {s.code: s for s in schedules}
    if code not in by_code:
        raise AllocationError(f"no schedule {code!r} in this building")

    assignment = {s.code: s.number for s in schedules}
    assignment[code] = number
    return _plan(f"set {code} to {number}", schedules, assignment, renderer,
                 allow_locked=allow_locked)


def swap(
    schedules: Sequence[ScheduleRef],
    code_a: str,
    code_b: str,
    renderer: Renderer | None = None,
    *,
    allow_locked: Sequence[str] = (),
) -> RenumberPlan:
    """Exchange two schedules' numbers."""
    by_code = {s.code: s for s in schedules}
    for code in (code_a, code_b):
        if code not in by_code:
            raise AllocationError(f"no schedule {code!r} in this building")

    assignment = {s.code: s.number for s in schedules}
    assignment[code_a], assignment[code_b] = by_code[code_b].number, by_code[code_a].number
    return _plan(f"swap {code_a} and {code_b}", schedules, assignment, renderer,
                 allow_locked=allow_locked)


def insert_at(
    schedules: Sequence[ScheduleRef],
    code: str,
    number: int,
    renderer: Renderer | None = None,
    *,
    allow_locked: Sequence[str] = (),
) -> RenumberPlan:
    """Move a schedule to ``number``, shifting everything at or above it up one."""
    by_code = {s.code: s for s in schedules}
    if code not in by_code:
        raise AllocationError(f"no schedule {code!r} in this building")

    assignment: dict[str, int] = {}
    for s in schedules:
        if s.code == code:
            assignment[s.code] = number
        elif s.number >= number:
            assignment[s.code] = s.number + 1
        else:
            assignment[s.code] = s.number
    return _plan(f"insert {code} at {number}", schedules, assignment, renderer,
                 allow_locked=allow_locked)


def compact(
    schedules: Sequence[ScheduleRef],
    renderer: Renderer | None = None,
    *,
    start: int | None = None,
    allow_locked: Sequence[str] = (),
) -> RenumberPlan:
    """Close gaps in the sequence, preserving the current order."""
    ordered = sorted(schedules, key=lambda s: s.number)
    if not ordered:
        return RenumberPlan(operation="compact")
    first = start if start is not None else ordered[0].number
    assignment = {s.code: first + i for i, s in enumerate(ordered)}
    return _plan("compact", schedules, assignment, renderer, allow_locked=allow_locked)


def rebase(
    schedules: Sequence[ScheduleRef],
    start: int,
    renderer: Renderer | None = None,
    *,
    allow_locked: Sequence[str] = (),
) -> RenumberPlan:
    """Renumber every schedule in order from a new starting number."""
    return compact(schedules, renderer, start=start, allow_locked=allow_locked)


# ------------------------------------------------------------------ audit ---


@dataclass(frozen=True, slots=True)
class AuditIssue:
    """One inconsistency between the record, the catalogue and reality."""

    severity: Literal["error", "warning"]
    kind: str
    message: str
    code: str | None = None

    def __str__(self) -> str:
        where = f" [{self.code}]" if self.code else ""
        return f"{self.severity}{where} {self.kind}: {self.message}"


def audit(
    schedules: Sequence[ScheduleRef],
    *,
    retired: Sequence[int] = (),
    catalogue_versions: dict[str, int] | None = None,
    schedule_versions: dict[str, int] | None = None,
    expected_docnums: dict[str, str] | None = None,
) -> list[AuditIssue]:
    """Report every inconsistency in one building's numbering.

    Run automatically before any build or renumber, and surfaced as a per-project
    health check. Under the v1 file-as-record model this also had to catch a file
    renamed in Explorer; the database cannot drift that way, but a document
    number that disagrees with what the tokens now produce still can, and that is
    the same class of bug.
    """
    issues: list[AuditIssue] = []

    seen: dict[int, str] = {}
    for s in schedules:
        if s.number in seen:
            issues.append(
                AuditIssue(
                    "error", "duplicate-number",
                    f"number {s.number} is used by both {seen[s.number]} and {s.code}",
                    s.code,
                )
            )
        seen[s.number] = s.code

        if s.number in retired:
            issues.append(
                AuditIssue(
                    "error", "retired-number",
                    f"number {s.number} is marked retired but {s.code} is using it",
                    s.code,
                )
            )
        if s.state == "missing":
            issues.append(
                AuditIssue("warning", "missing", f"{s.code} has no built file", s.code)
            )
        if s.state == "allocated":
            issues.append(
                AuditIssue(
                    "warning", "never-built",
                    f"{s.code} holds number {s.number} but was never rendered; "
                    f"a crashed build leaves a reserved number rather than an orphan",
                    s.code,
                )
            )

    if expected_docnums:
        for s in schedules:
            expected = expected_docnums.get(s.code)
            if expected and s.docnum and expected != s.docnum:
                issues.append(
                    AuditIssue(
                        "error", "docnum-drift",
                        f"stored document number {s.docnum!r} disagrees with the "
                        f"tokens, which now produce {expected!r}",
                        s.code,
                    )
                )

    if catalogue_versions and schedule_versions:
        for s in schedules:
            pinned = schedule_versions.get(s.code)
            latest = catalogue_versions.get(s.code)
            if pinned is not None and latest is not None and pinned < latest:
                issues.append(
                    AuditIssue(
                        "warning", "stale-type",
                        f"{s.code} is built against type version {pinned}; "
                        f"the catalogue is at version {latest}",
                        s.code,
                    )
                )

    return issues
