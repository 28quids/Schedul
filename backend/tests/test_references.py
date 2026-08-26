"""Incrementing references for fill-down."""

from __future__ import annotations

import pytest

from schedul.core.references import (
    fill_series,
    is_incrementable,
    next_reference,
    split_reference,
)


class TestSplit:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("RAD-001", ("RAD-", "001")),
            ("MVHR-1", ("MVHR-", "1")),
            ("7", ("", "7")),
            ("AHU-2-01", ("AHU-2-", "01")),
        ],
    )
    def test_splits_the_trailing_number(self, value, expected):
        assert split_reference(value) == expected

    @pytest.mark.parametrize("value", ["RAD", "", "L02 Plantroom", "RAD-01a"])
    def test_no_trailing_number(self, value):
        assert split_reference(value) is None
        assert is_incrementable(value) is False


class TestNext:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("RAD-001", "RAD-002"),
            ("RAD-009", "RAD-010"),
            ("MVHR-1", "MVHR-2"),
            ("AHU-2-01", "AHU-2-02"),
            ("7", "8"),
        ],
    )
    def test_increments_preserving_padding(self, value, expected):
        assert next_reference(value) == expected

    def test_padding_widens_only_when_outgrown(self):
        assert next_reference("RAD-099") == "RAD-100"
        assert next_reference("RAD-99") == "RAD-100"

    def test_text_without_a_number_is_returned_unchanged(self):
        assert next_reference("Roof Plantroom") == "Roof Plantroom"


class TestFill:
    def test_a_reference_counts_up(self):
        assert fill_series("RAD-001", 3) == ["RAD-002", "RAD-003", "RAD-004"]

    def test_text_ending_in_digits_still_counts_up_in_series_mode(self):
        """Excel's fill handle does this too: 'Level 02' -> 'Level 03'. It is
        why Ctrl+D is bound to copy rather than series."""
        assert fill_series("Level 02", 2) == ["Level 03", "Level 04"]

    def test_text_with_no_trailing_number_repeats(self):
        assert fill_series("Roof Plantroom", 3) == ["Roof Plantroom"] * 3

    def test_copy_mode_never_increments(self):
        assert fill_series("RAD-001", 2, mode="copy") == ["RAD-001", "RAD-001"]

    def test_zero_or_negative_count(self):
        assert fill_series("RAD-001", 0) == []

    def test_padding_survives_the_whole_series(self):
        assert fill_series("RAD-008", 4) == ["RAD-009", "RAD-010", "RAD-011", "RAD-012"]

    def test_an_unknown_mode_is_rejected(self):
        with pytest.raises(ValueError):
            fill_series("RAD-001", 2, mode="nonsense")
