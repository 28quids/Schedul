"""Reading a pasted block, and planning what pasting it would do.

The planner is the thing that makes paste safe, so it is tested on its own:
every count it reports is something a user is about to confirm, and a wrong one
is a wrong decision made on our word.
"""

from __future__ import annotations

import pytest

from schedul.core.tabular import (
    looks_like_header,
    map_columns,
    plan_paste,
    read_block,
    rows_to_values,
)

COLUMNS = [
    "Unit Reference",
    "Location",
    "Supply Airflow (l/s)",
    "Model Reference",
]


class TestReadingABlock:
    def test_tabs_separate_cells_and_newlines_separate_rows(self):
        block = read_block("A\tRoof\t450\nB\tPlant\t300", column_names=COLUMNS)
        assert block.cells == [["A", "Roof", "450"], ["B", "Plant", "300"]]
        assert block.header is None

    def test_blank_lines_are_dropped(self):
        block = read_block("A\t1\n\n\nB\t2\n", column_names=COLUMNS)
        assert block.row_count == 2

    def test_windows_line_endings_do_not_leave_stray_carriage_returns(self):
        block = read_block("A\t1\r\nB\t2\r\n", column_names=COLUMNS)
        assert block.cells == [["A", "1"], ["B", "2"]]

    def test_a_header_row_is_detected_and_removed(self):
        block = read_block(
            "Unit Reference\tLocation\nMVHR-01\tRoof", column_names=COLUMNS
        )
        assert block.header == ["Unit Reference", "Location"]
        assert block.header_detected is True
        assert block.cells == [["MVHR-01", "Roof"]]

    def test_the_unit_suffix_does_not_stop_a_header_matching(self):
        block = read_block("Unit Reference\tSupply Airflow\nA\t450", column_names=COLUMNS)
        assert block.header is not None
        assert block.cells == [["A", "450"]]

    def test_a_data_row_is_never_mistaken_for_a_header(self):
        # 'Location' is a real column name, but the numbers give the row away.
        block = read_block("Location\t450\t900", column_names=COLUMNS)
        assert block.header is None
        assert block.row_count == 1

    def test_the_caller_can_force_the_header_decision(self):
        block = read_block("A\tB\nC\tD", column_names=COLUMNS, header=True)
        assert block.header == ["A", "B"]
        assert block.cells == [["C", "D"]]

        block = read_block(
            "Unit Reference\tLocation\nA\tB", column_names=COLUMNS, header=False
        )
        assert block.header is None
        assert block.row_count == 2

    def test_a_wider_block_than_the_schedule_warns_rather_than_silently_dropping(self):
        block = read_block("a\tb\tc\td\te\tf", column_names=COLUMNS)
        assert any("ignored" in w for w in block.warnings)

    def test_ragged_rows_are_reported(self):
        block = read_block("a\tb\tc\nd", column_names=COLUMNS)
        assert any("not all the same width" in w for w in block.warnings)

    def test_comma_separated_text_is_accepted_when_there_are_no_tabs(self):
        block = read_block("A,Roof,450", column_names=COLUMNS)
        assert block.cells == [["A", "Roof", "450"]]


class TestMapping:
    def test_without_a_header_columns_map_left_to_right(self):
        assert map_columns(None, COLUMNS, width=2) == ["Unit Reference", "Location"]

    def test_a_header_maps_by_name_in_whatever_order_it_arrives(self):
        mapping = map_columns(["Location", "Unit Reference"], COLUMNS)
        assert mapping == ["Location", "Unit Reference"]

    def test_an_unrecognised_header_maps_to_nothing_rather_than_being_guessed(self):
        assert map_columns(["Colour"], COLUMNS) == [None]

    def test_blank_cells_leave_the_column_alone(self):
        block = read_block("A\t\t450", column_names=COLUMNS)
        values = rows_to_values(block, map_columns(None, COLUMNS, width=3))
        assert values == [{"Unit Reference": "A", "Supply Airflow (l/s)": "450"}]


class TestPlanning:
    existing = [{"Unit Reference": "OLD"}, {}]

    def test_append_adds_to_the_end_and_removes_nothing(self):
        plan = plan_paste(
            "A\nB", mode="append", column_names=COLUMNS, existing=self.existing
        )
        assert (plan.to_append, plan.to_remove, plan.to_insert) == (2, 0, 0)
        assert plan.total_after == 4
        assert plan.destructive is False

    def test_insert_pushes_rows_in_at_a_position(self):
        plan = plan_paste(
            "A", mode="insert", column_names=COLUMNS, existing=self.existing, position=1
        )
        assert plan.to_insert == 1
        assert plan.position == 1
        assert plan.total_after == 3
        assert plan.destructive is False

    def test_insert_beyond_the_end_lands_at_the_end(self):
        plan = plan_paste(
            "A", mode="insert", column_names=COLUMNS, existing=self.existing, position=99
        )
        assert plan.position == 2

    def test_replace_counts_what_would_be_lost_and_only_counts_filled_rows(self):
        plan = plan_paste(
            "A", mode="replace", column_names=COLUMNS, existing=self.existing
        )
        assert plan.to_remove == 2
        assert plan.populated_removed == 1, "the blank row is not a loss"
        assert plan.destructive is True

    def test_replacing_an_empty_schedule_is_not_destructive(self):
        plan = plan_paste("A", mode="replace", column_names=COLUMNS, existing=[])
        assert plan.destructive is False

    def test_the_plan_carries_the_mapped_rows_for_a_preview(self):
        plan = plan_paste(
            "MVHR-01\tRoof\t450", mode="append", column_names=COLUMNS, existing=[]
        )
        assert plan.rows == [
            {"Unit Reference": "MVHR-01", "Location": "Roof", "Supply Airflow (l/s)": "450"}
        ]

    def test_an_empty_paste_says_so_rather_than_silently_doing_nothing(self):
        plan = plan_paste("   ", mode="append", column_names=COLUMNS, existing=[])
        assert plan.detected_rows == 0
        assert any("nothing to paste" in w for w in plan.warnings)

    def test_unmapped_columns_are_reported(self):
        plan = plan_paste(
            "Colour\tUnit Reference\nRed\tA",
            mode="append", column_names=COLUMNS, existing=[],
        )
        assert plan.unmapped_columns == 1
        assert any("did not match" in w for w in plan.warnings)

    def test_an_unknown_mode_is_refused(self):
        with pytest.raises(ValueError):
            plan_paste("A", mode="obliterate", column_names=COLUMNS)

    def test_planning_never_touches_the_rows_it_was_given(self):
        existing = [{"Unit Reference": "OLD"}]
        plan_paste("A", mode="replace", column_names=COLUMNS, existing=existing)
        assert existing == [{"Unit Reference": "OLD"}]


def test_header_detection_needs_a_known_column_name():
    assert looks_like_header(["Colour", "Size"], COLUMNS) is False
    assert looks_like_header(["Unit Reference"], COLUMNS) is True
    assert looks_like_header([], COLUMNS) is False
