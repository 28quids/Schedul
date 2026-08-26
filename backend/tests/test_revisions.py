"""Revision ranking -- SPEC.md acceptance step 21.

"This is the case both existing implementations get wrong."
"""

from __future__ import annotations

import pytest

from schedul.core.revisions import (
    Revision,
    current,
    is_issued,
    next_code,
    parse_code,
    rank,
    sort_key,
)


def rev(code: str, **kw) -> Revision:
    return Revision(code=code, **kw)


class TestSortKey:
    def test_published_outranks_every_preliminary(self):
        # The real file's bug: SUBSTITUTE(rev,"P","") leaves C01 as C01,
        # IFERROR turns the failed coercion into 0, and C01 sorts below P01.
        assert sort_key("C01") > sort_key("P20")
        assert sort_key("C01") > sort_key("P99")

    def test_within_a_series_it_is_numeric(self):
        assert sort_key("P01") < sort_key("P02") < sort_key("P10")
        assert sort_key("C01") < sort_key("C02")

    def test_unparseable_never_wins(self):
        assert sort_key("nonsense") == 0
        assert sort_key("") == 0

    @pytest.mark.parametrize("code,expected", [("P01", ("P", 1)), ("c12", ("C", 12))])
    def test_parse_code(self, code, expected):
        assert parse_code(code) == expected

    def test_parse_code_rejects_rubbish(self):
        with pytest.raises(ValueError):
            parse_code("REV1")


class TestCurrent:
    def test_acceptance_step_21_c01_wins(self):
        """P01, P02, then C01 -- the cover must show C01."""
        log = [rev("P01"), rev("P02"), rev("C01")]
        assert current(log).code == "C01"

    def test_acceptance_step_21_out_of_order_p03(self):
        """Delete C01, add P03 above P02 out of order -- the cover shows P03."""
        log = [rev("P01"), rev("P03"), rev("P02")]
        assert current(log).code == "P03"

    def test_the_v1_generator_bug_a_blank_row_in_the_middle(self):
        # INDEX(range, MAX(1, COUNTA(range))) counts non-empty cells, so a gap
        # makes it read the wrong row. Ranking does not care.
        log = [rev("P01"), rev(""), rev("P02")]
        assert current(log).code == "P02"

    def test_empty_log(self):
        assert current([]) is None

    def test_rank_orders_oldest_to_newest(self):
        log = [rev("C01"), rev("P02"), rev("P01")]
        assert [r.code for r in rank(log)] == ["P01", "P02", "C01"]


class TestNextCode:
    def test_continues_the_preliminary_series(self):
        assert next_code([rev("P01"), rev("P02")]) == "P03"

    def test_first_published_starts_at_c01_after_p03(self):
        assert next_code([rev("P01"), rev("P02"), rev("P03")], published=True) == "C01"

    def test_continues_the_published_series(self):
        assert next_code([rev("P01"), rev("C01")], published=True) == "C02"

    def test_empty_log_starts_at_p01(self):
        assert next_code([]) == "P01"


class TestIssuedLock:
    """SPEC.md 5.5: an issued reference is stable, so renumbering is refused."""

    def test_a_single_preliminary_row_is_not_issued(self):
        assert is_issued([rev("P01")]) is False

    def test_a_second_row_counts_as_issued(self):
        assert is_issued([rev("P01"), rev("P02")]) is True

    def test_any_published_revision_counts_as_issued(self):
        assert is_issued([rev("C01")]) is True

    def test_empty_log_is_not_issued(self):
        assert is_issued([]) is False
