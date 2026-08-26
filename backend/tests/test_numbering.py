"""Allocation and renumbering, per building -- SPEC.md 5.3 to 5.7."""

from __future__ import annotations

import pytest

from schedul.core.numbering import (
    AllocationError,
    ScheduleRef,
    allocate,
    audit,
    compact,
    insert_at,
    next_number,
    rebase,
    retire,
    set_number,
    swap,
)


def refs(*pairs: tuple[str, int], locked: set[str] = frozenset()) -> list[ScheduleRef]:
    return [
        ScheduleRef(
            code=code,
            number=number,
            title=f"{code} Schedule",
            docnum=f"DOC-{code}-{number:08d}",
            filename=f"DOC-{code}-{number:08d}.xlsx",
            locked=code in locked,
            lock_reason="issued document" if code in locked else "",
        )
        for code, number in pairs
    ]


def renderer(sched: ScheduleRef, number: int) -> tuple[str, str]:
    doc = f"DOC-{sched.code}-{number:08d}"
    return doc, f"{doc}.xlsx"


class TestAllocation:
    def test_first_schedule_takes_the_start_number(self):
        assert next_number([], start=10) == 10

    def test_next_is_one_above_the_highest(self):
        assert next_number([10, 11, 12], start=10) == 13

    def test_retired_numbers_are_never_reused(self):
        """SPEC.md acceptance step 8: remove FCU (11), add EWH -> it gets 13."""
        assert next_number([10, 12], retired=[11], start=10) == 13

    def test_numbering_restarts_per_building(self):
        # Each building allocates from its own set; nothing is shared.
        hq049 = [10, 11, 12]
        hq014: list[int] = []
        assert next_number(hq014, start=10) == 10
        assert next_number(hq049, start=10) == 13

    def test_warns_near_width_exhaustion(self):
        _, warnings = allocate(refs(("A", 99999995)), width=8)
        assert warnings and "numbers left" in warnings[0]

    def test_refuses_to_exceed_the_width(self):
        with pytest.raises(AllocationError, match="does not fit"):
            allocate(refs(("A", 99999999)), width=8)

    def test_retire_is_idempotent_and_sorted(self):
        assert retire([12, 10], 11) == [10, 11, 12]
        assert retire([10, 11], 11) == [10, 11]


class TestSetNumber:
    def test_moves_one_schedule(self):
        plan = set_number(refs(("MVHR", 10), ("AHU", 11)), "AHU", 20, renderer)
        assert plan.can_apply
        moves = {c.code: (c.old_number, c.new_number) for c in plan.moves}
        assert moves == {"AHU": (11, 20)}

    def test_collision_with_a_live_schedule_is_blocked(self):
        plan = set_number(refs(("MVHR", 10), ("AHU", 11)), "AHU", 10, renderer)
        assert not plan.can_apply
        assert plan.blocked

    def test_recomputes_document_number_and_filename(self):
        plan = set_number(refs(("AHU", 11)), "AHU", 20, renderer)
        change = plan.moves[0]
        assert change.old_docnum == "DOC-AHU-00000011"
        assert change.new_docnum == "DOC-AHU-00000020"
        assert change.new_filename == "DOC-AHU-00000020.xlsx"

    def test_unknown_code_raises(self):
        with pytest.raises(AllocationError, match="no schedule"):
            set_number(refs(("AHU", 11)), "NOPE", 20, renderer)


class TestSwap:
    def test_exchanges_two_numbers(self):
        plan = swap(refs(("MVHR", 10), ("AHU", 11), ("FCU", 12)), "MVHR", "FCU", renderer)
        assert plan.can_apply
        moves = {c.code: c.new_number for c in plan.moves}
        assert moves == {"MVHR": 12, "FCU": 10}


class TestInsertAt:
    def test_shifts_everything_at_or_above_up_one(self):
        plan = insert_at(refs(("A", 10), ("B", 11), ("C", 12), ("D", 20)), "D", 11, renderer)
        assert plan.can_apply
        result = {c.code: c.new_number for c in plan.changes}
        assert result == {"A": 10, "D": 11, "B": 12, "C": 13}


class TestCompact:
    def test_closes_gaps_preserving_order(self):
        plan = compact(refs(("A", 10), ("B", 13), ("C", 17)), renderer)
        assert {c.code: c.new_number for c in plan.changes} == {"A": 10, "B": 11, "C": 12}

    def test_already_compact_has_nothing_to_do(self):
        plan = compact(refs(("A", 10), ("B", 11)), renderer)
        assert not plan.moves
        assert not plan.can_apply


class TestRebase:
    def test_renumbers_all_in_order_from_a_new_start(self):
        plan = rebase(refs(("A", 10), ("B", 13)), 100, renderer)
        assert {c.code: c.new_number for c in plan.changes} == {"A": 100, "B": 101}


class TestIssuedLock:
    """SPEC.md acceptance step 16: renumbering an issued schedule must refuse."""

    def test_a_locked_schedule_blocks_its_row(self):
        plan = set_number(refs(("MVHR", 10), ("AHU", 11), locked={"AHU"}), "AHU", 20, renderer)
        assert not plan.can_apply
        assert plan.blocked[0].code == "AHU"
        assert "issued" in plan.blocked[0].blocked

    def test_override_lets_it_through(self):
        # The GUI requires the filename typed before passing allow_locked.
        plan = set_number(
            refs(("MVHR", 10), ("AHU", 11), locked={"AHU"}),
            "AHU", 20, renderer, allow_locked=["AHU"],
        )
        assert plan.can_apply

    def test_a_lock_elsewhere_does_not_block_an_unaffected_row(self):
        plan = set_number(refs(("MVHR", 10), ("AHU", 11), locked={"AHU"}), "MVHR", 20, renderer)
        assert plan.can_apply


class TestAudit:
    def test_clean_building_reports_nothing(self):
        assert audit(refs(("A", 10), ("B", 11))) == []

    def test_duplicate_numbers_are_an_error(self):
        issues = audit(refs(("A", 10), ("B", 10)))
        assert any(i.kind == "duplicate-number" for i in issues)

    def test_a_schedule_using_a_retired_number_is_an_error(self):
        issues = audit(refs(("A", 10)), retired=[10])
        assert any(i.kind == "retired-number" for i in issues)

    def test_document_number_drift_is_an_error(self):
        """The database cannot be renamed in Explorer, but the tokens can move
        under a stored document number, which is the same class of bug."""
        issues = audit(refs(("A", 10)), expected_docnums={"A": "DOC-A-00000099"})
        assert any(i.kind == "docnum-drift" for i in issues)

    def test_stale_type_version_is_a_warning(self):
        issues = audit(
            refs(("A", 10)),
            catalogue_versions={"A": 3},
            schedule_versions={"A": 1},
        )
        assert any(i.kind == "stale-type" for i in issues)

    def test_a_never_built_schedule_is_flagged(self):
        r = refs(("A", 10))
        r[0].state = "allocated"
        assert any(i.kind == "never-built" for i in audit(r))
