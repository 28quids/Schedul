"""Incrementing references for fill-down."""

from __future__ import annotations

import pytest

from schedul.core.references import (
    digit_runs,
    fill_series,
    is_incrementable,
    next_reference,
    split_reference,
    varying_run,
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

    @pytest.mark.parametrize("value", ["RAD", "", "Roof Plantroom"])
    def test_no_number_at_all_does_not_count(self, value):
        assert is_incrementable(value) is False

    @pytest.mark.parametrize("value", ["L02 Plantroom", "RAD-01a", "RM0.01 2 Bedroom"])
    def test_a_number_anywhere_counts(self, value):
        """A number that is not at the end is still a number somebody counts.

        'RM0.01 2 Bedroom' repeated two hundred times is not what dragging a
        room reference down means, and refusing to count it because of the word
        after it was the reason people gave up on the fill and used Excel.
        """
        assert is_incrementable(value) is True


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


class TestWhichNumberCounts:
    """A value can hold several numbers. Which one a fill counts is the question.

    One seed cannot say, so the last run wins, which is what a spreadsheet does.
    Two seeds can say, and then they decide.
    """

    def test_the_last_number_counts_by_default(self):
        assert fill_series("RM0.01 2 Bedroom", 2) == [
            "RM0.01 3 Bedroom", "RM0.01 4 Bedroom"
        ]

    def test_a_chosen_run_counts_instead(self):
        assert fill_series("RM0.01 2 Bedroom", 3, index=1) == [
            "RM0.02 2 Bedroom", "RM0.03 2 Bedroom", "RM0.04 2 Bedroom"
        ]

    def test_two_seeds_say_which_number_varies(self):
        assert varying_run(["RM0.01 2 Bedroom", "RM0.02 2 Bedroom"]) == 1
        assert varying_run(["RAD-001", "RAD-002"]) == 0

    def test_seeds_that_differ_in_two_places_say_nothing(self):
        assert varying_run(["A1-1", "A2-2"]) is None

    def test_one_seed_says_nothing(self):
        assert varying_run(["RAD-001"]) is None
        assert varying_run([]) is None

    def test_seeds_of_different_shapes_say_nothing(self):
        assert varying_run(["RAD-001", "Level 2 Room 3"]) is None

    def test_the_runs_of_a_value_are_found_left_to_right(self):
        assert digit_runs("RM0.01 2 Bedroom") == [(2, 3), (4, 6), (7, 8)]
        assert digit_runs("no numbers") == []

    def test_padding_is_kept_wherever_the_run_sits(self):
        assert fill_series("RM0.09 2 Bed", 2, index=1) == [
            "RM0.10 2 Bed", "RM0.11 2 Bed"
        ]

    def test_counting_backwards_stops_rather_than_going_negative(self):
        assert fill_series("RAD-001", 3, step=-1) == ["RAD-000", "RAD-000", "RAD-000"]
