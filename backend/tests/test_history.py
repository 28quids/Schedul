"""Undo and redo on the grid.

The risky operations rewrite several rows at once. Somebody who confirms one and
then sees it was wrong needs a way back that is not retyping, and the way back
has to be exact -- an undo that restores nearly the right thing is worse than
none, because it is believed.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


@pytest.fixture()
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("SCHEDUL_DATABASE_URL", f"sqlite:///{tmp_path / 'history.db'}")
    import schedul.db.session as session_module

    session_module.SessionLocal = None
    session_module.init_db(f"sqlite:///{tmp_path / 'history.db'}")
    from schedul.api.main import app

    return TestClient(app)


@pytest.fixture()
def schedule(client) -> str:
    project = client.post("/api/projects", json={"number": "CM1", "name": "J"}).json()
    building = project["buildings"][0]["id"]
    result = client.post(
        f"/api/projects/{project['id']}/buildings/{building}/schedules",
        json={"code": "MVHR"},
    ).json()
    return result["buildings"][0]["schedules"][0]["id"]


def add(client, schedule, **values):
    return client.post(f"/api/schedules/{schedule}/rows", json={"values": values}).json()


def refs(grid):
    return [r["values"].get("Unit Reference") for r in grid["rows"]]


class TestUndo:
    def test_undo_puts_deleted_rows_back_exactly(self, client, schedule):
        add(client, schedule, **{"Unit Reference": "A", "Supply Airflow (l/s)": 450})
        grid = add(client, schedule, **{"Unit Reference": "B"})
        first = grid["rows"][0]["id"]

        client.delete(f"/api/schedules/{schedule}/rows/{first}")
        grid = client.post(f"/api/schedules/{schedule}/undo").json()

        assert refs(grid) == ["A", "B"]
        assert grid["rows"][0]["values"]["Supply Airflow (l/s)"] == 450
        assert grid["rows"][0]["id"] == first, "the row comes back as itself"

    def test_undo_takes_back_a_whole_paste_in_one_step(self, client, schedule):
        add(client, schedule, **{"Unit Reference": "KEEP"})
        client.post(f"/api/schedules/{schedule}/rows/paste", json={
            "mode": "append", "text": "A\nB\nC",
        })
        grid = client.get(f"/api/schedules/{schedule}").json()
        assert refs(grid) == ["KEEP", "A", "B", "C"]

        grid = client.post(f"/api/schedules/{schedule}/undo").json()
        assert refs(grid) == ["KEEP"]

    def test_undo_reverses_a_confirmed_replace(self, client, schedule):
        add(client, schedule, **{"Unit Reference": "OLD"})
        client.post(f"/api/schedules/{schedule}/rows/paste", json={
            "mode": "replace", "confirm": True, "text": "NEW",
        })
        grid = client.post(f"/api/schedules/{schedule}/undo").json()
        assert refs(grid) == ["OLD"]

    def test_undo_reverses_a_fill(self, client, schedule):
        add(client, schedule, **{"Unit Reference": "RAD-001"})
        add(client, schedule)
        add(client, schedule)
        client.post(f"/api/schedules/{schedule}/rows/fill", json={
            "column": "Unit Reference", "start_position": 0, "mode": "series",
        })
        grid = client.post(f"/api/schedules/{schedule}/undo").json()
        assert refs(grid) == ["RAD-001", None, None]

    def test_undo_reverses_a_duplicate(self, client, schedule):
        grid = add(client, schedule, **{"Unit Reference": "A"})
        row = grid["rows"][0]["id"]
        client.post(f"/api/schedules/{schedule}/rows/{row}/duplicate")
        grid = client.post(f"/api/schedules/{schedule}/undo").json()
        assert refs(grid) == ["A"]

    def test_undo_reverses_a_bulk_row_delete(self, client, schedule):
        for ref in ("A", "B", "C"):
            grid = add(client, schedule, **{"Unit Reference": ref})
        ids = [r["id"] for r in grid["rows"][:2]]

        grid = client.post(
            f"/api/schedules/{schedule}/rows/delete", json={"row_ids": ids}
        ).json()
        assert refs(grid) == ["C"]
        assert [r["position"] for r in grid["rows"]] == [0], "positions close up"

        grid = client.post(f"/api/schedules/{schedule}/undo").json()
        assert refs(grid) == ["A", "B", "C"]

    def test_undo_reverses_a_block_of_cell_edits(self, client, schedule):
        for ref in ("A", "B"):
            grid = add(client, schedule, **{"Unit Reference": ref, "Location": "Roof"})
        edits = [{"row_id": r["id"], "values": {"Location": ""}} for r in grid["rows"]]

        grid = client.post(
            f"/api/schedules/{schedule}/rows/cells",
            json={"edits": edits, "action": "clear_cells"},
        ).json()
        assert [r["values"].get("Location") for r in grid["rows"]] == ["", ""]

        grid = client.post(f"/api/schedules/{schedule}/undo").json()
        assert [r["values"].get("Location") for r in grid["rows"]] == ["Roof", "Roof"]

    def test_several_steps_can_be_undone_in_order(self, client, schedule):
        add(client, schedule, **{"Unit Reference": "A"})
        add(client, schedule, **{"Unit Reference": "B"})
        add(client, schedule, **{"Unit Reference": "C"})

        assert refs(client.post(f"/api/schedules/{schedule}/undo").json()) == ["A", "B"]
        assert refs(client.post(f"/api/schedules/{schedule}/undo").json()) == ["A"]
        assert refs(client.post(f"/api/schedules/{schedule}/undo").json()) == []

    def test_undoing_with_nothing_to_undo_says_so(self, client, schedule):
        response = client.post(f"/api/schedules/{schedule}/undo")
        assert response.status_code == 409
        assert "nothing to undo" in response.json()["detail"]

    def test_typing_in_a_cell_does_not_fill_the_undo_stack(self, client, schedule):
        grid = add(client, schedule, **{"Unit Reference": "A"})
        row = grid["rows"][0]["id"]
        for value in ("MV", "MVH", "MVHR-01"):
            grid = client.put(
                f"/api/schedules/{schedule}/rows/{row}",
                json={"values": {"Unit Reference": value}},
            ).json()
        # One entry: the row being added. Keystroke saves are the browser's own
        # undo to reverse, not the schedule's.
        assert grid["history"]["depth"] == 1


class TestRedo:
    def test_redo_reapplies_what_undo_took_back(self, client, schedule):
        add(client, schedule, **{"Unit Reference": "A"})
        client.post(f"/api/schedules/{schedule}/rows/paste", json={
            "mode": "append", "text": "B\nC",
        })
        client.post(f"/api/schedules/{schedule}/undo")
        grid = client.post(f"/api/schedules/{schedule}/redo").json()
        assert refs(grid) == ["A", "B", "C"]

    def test_a_new_edit_discards_the_redo_stack(self, client, schedule):
        add(client, schedule, **{"Unit Reference": "A"})
        add(client, schedule, **{"Unit Reference": "B"})
        client.post(f"/api/schedules/{schedule}/undo")

        grid = add(client, schedule, **{"Unit Reference": "C"})
        assert refs(grid) == ["A", "C"]
        assert grid["history"]["can_redo"] is False

        response = client.post(f"/api/schedules/{schedule}/redo")
        assert response.status_code == 409

    def test_the_grid_reports_what_undo_would_reverse(self, client, schedule):
        add(client, schedule, **{"Unit Reference": "A"})
        grid = client.post(f"/api/schedules/{schedule}/rows/paste", json={
            "mode": "append", "text": "B\nC",
        }).json()
        assert grid["history"]["can_undo"] is True
        assert grid["history"]["undo_label"] == "pasted 2 row(s)"

        grid = client.post(f"/api/schedules/{schedule}/undo").json()
        assert grid["history"]["can_redo"] is True
        assert grid["history"]["redo_label"] == "pasted 2 row(s)"


class TestBoundedStack:
    def test_the_stack_does_not_grow_without_limit(self, client, schedule):
        from schedul.services.history import HISTORY_LIMIT

        for i in range(HISTORY_LIMIT + 5):
            grid = add(client, schedule, **{"Unit Reference": f"R{i}"})
        assert grid["history"]["depth"] == HISTORY_LIMIT

        for _ in range(HISTORY_LIMIT):
            client.post(f"/api/schedules/{schedule}/undo")
        # Five rows are older than the stack, so they stay. Losing them would be
        # the bounded history quietly destroying data instead of forgetting it.
        assert len(client.get(f"/api/schedules/{schedule}").json()["rows"]) == 5


class TestPastePreview:
    def test_the_preview_reports_what_would_happen_without_doing_it(self, client, schedule):
        add(client, schedule, **{"Unit Reference": "OLD"})
        plan = client.post(f"/api/schedules/{schedule}/rows/paste/preview", json={
            "mode": "replace",
            "text": "Unit Reference\tLocation\nA\tRoof\nB\tPlant",
        }).json()

        assert plan["detected_rows"] == 2
        assert plan["header_detected"] is True
        assert plan["to_remove"] == 1
        assert plan["populated_removed"] == 1
        assert plan["destructive"] is True
        assert plan["total_after"] == 2

        grid = client.get(f"/api/schedules/{schedule}").json()
        assert refs(grid) == ["OLD"], "a preview changes nothing"

    def test_append_is_never_destructive(self, client, schedule):
        add(client, schedule, **{"Unit Reference": "OLD"})
        plan = client.post(f"/api/schedules/{schedule}/rows/paste/preview", json={
            "mode": "append", "text": "A\nB",
        }).json()
        assert plan["destructive"] is False
        assert plan["to_append"] == 2
        assert plan["total_after"] == 3

    def test_the_preview_and_the_apply_agree_on_the_rows(self, client, schedule):
        text = "Unit Reference\tSupply Airflow (l/s)\nMVHR-01\t450"
        plan = client.post(f"/api/schedules/{schedule}/rows/paste/preview", json={
            "mode": "append", "text": text,
        }).json()
        grid = client.post(f"/api/schedules/{schedule}/rows/paste", json={
            "mode": "append", "text": text,
        }).json()

        assert plan["detected_rows"] == len(grid["rows"])
        assert grid["rows"][0]["values"]["Unit Reference"] == "MVHR-01"
        assert grid["rows"][0]["values"]["Supply Airflow (l/s)"] == 450, (
            "pasted numbers are still coerced"
        )

    def test_a_pasted_header_row_is_not_stored_as_data(self, client, schedule):
        grid = client.post(f"/api/schedules/{schedule}/rows/paste", json={
            "mode": "append",
            "text": "Unit Reference\tLocation\nMVHR-01\tRoof",
        }).json()
        assert refs(grid) == ["MVHR-01"]


class TestRangeFill:
    def test_a_fill_can_be_bounded_to_a_selected_range(self, client, schedule):
        add(client, schedule, **{"Unit Reference": "RAD-001"})
        for _ in range(3):
            add(client, schedule)

        grid = client.post(f"/api/schedules/{schedule}/rows/fill", json={
            "column": "Unit Reference", "start_position": 0, "count": 2,
            "mode": "series",
        }).json()
        assert refs(grid) == ["RAD-001", "RAD-002", "RAD-003", None]

    def test_several_columns_fill_at_once(self, client, schedule):
        add(client, schedule, **{"Unit Reference": "RAD-001", "Location": "Roof"})
        add(client, schedule)

        grid = client.post(f"/api/schedules/{schedule}/rows/fill", json={
            "columns": ["Unit Reference", "Location"],
            "start_position": 0, "mode": "series",
        }).json()
        assert grid["rows"][1]["values"]["Unit Reference"] == "RAD-002"
        assert grid["rows"][1]["values"]["Location"] == "Roof", "text repeats"

    def test_filling_a_computed_column_is_still_refused(self, client, schedule):
        add(client, schedule, **{"Unit Reference": "A"})
        add(client, schedule)
        response = client.post(f"/api/schedules/{schedule}/rows/fill", json={
            "columns": ["Unit Reference", "Total Airflow (l/s)"], "start_position": 0,
        })
        assert response.status_code == 400
        grid = client.get(f"/api/schedules/{schedule}").json()
        assert refs(grid) == ["A", None], "a refused fill changes nothing at all"


class TestDraggingTheFillHandle:
    """What the corner handle asks the server to do.

    The increment rule stays in ``core.references`` -- the browser sends a
    direction and a count, not a list of values -- so a drag, a toolbar button
    and an importer all produce the same references.
    """

    def test_dragging_down_counts_up(self, client, schedule):
        add(client, schedule, **{"Unit Reference": "RAD-001"})
        for _ in range(2):
            add(client, schedule)
        grid = client.post(f"/api/schedules/{schedule}/rows/fill", json={
            "columns": ["Unit Reference"], "start_position": 0, "count": 2,
            "mode": "series", "direction": "down",
        }).json()
        assert refs(grid) == ["RAD-001", "RAD-002", "RAD-003"]

    def test_dragging_up_counts_down(self, client, schedule):
        for _ in range(2):
            add(client, schedule)
        add(client, schedule, **{"Unit Reference": "RAD-005"})
        grid = client.post(f"/api/schedules/{schedule}/rows/fill", json={
            "columns": ["Unit Reference"], "start_position": 2, "count": 2,
            "mode": "series", "direction": "up",
        }).json()
        assert refs(grid) == ["RAD-003", "RAD-004", "RAD-005"], (
            "dragging a reference upwards counts down, as a spreadsheet does"
        )

    def test_holding_ctrl_copies_instead(self, client, schedule):
        add(client, schedule, **{"Unit Reference": "RAD-001"})
        add(client, schedule)
        grid = client.post(f"/api/schedules/{schedule}/rows/fill", json={
            "columns": ["Unit Reference"], "start_position": 0, "count": 1,
            "mode": "copy", "direction": "down",
        }).json()
        assert refs(grid) == ["RAD-001", "RAD-001"]

    def test_a_drag_is_one_undo(self, client, schedule):
        add(client, schedule, **{"Unit Reference": "RAD-001"})
        for _ in range(3):
            add(client, schedule)
        client.post(f"/api/schedules/{schedule}/rows/fill", json={
            "columns": ["Unit Reference"], "start_position": 0, "count": 3,
            "mode": "series",
        })
        grid = client.post(f"/api/schedules/{schedule}/undo").json()
        assert refs(grid) == ["RAD-001", None, None, None]


class TestOverridingASelection:
    """Taking several library cells over in one step.

    A row that diverges from the library usually diverges in company, and one
    pencil at a time is the difference between a feature people use and one they
    work around.
    """

    def _with_products(self, client, schedule):
        client.post("/api/library", json={
            "type_code": "MVHR",
            "model_reference": "SYS-VSR-500",
            "values": {"Manufacturer": "Systemair", "Width (mm)": 900},
        })
        first = add(client, schedule, **{
            "Unit Reference": "MVHR-01", "Model Reference": "SYS-VSR-500",
        })
        add(client, schedule, **{
            "Unit Reference": "MVHR-02", "Model Reference": "SYS-VSR-500",
        })
        return client.get(f"/api/schedules/{schedule}").json()

    def test_a_block_of_library_cells_is_taken_over_at_once(self, client, schedule):
        grid = self._with_products(client, schedule)
        edits = [
            {
                "row_id": row["id"],
                "values": {},
                "overrides": {"Width (mm)": row["computed"]["Width (mm)"]},
            }
            for row in grid["rows"]
        ]
        after = client.post(f"/api/schedules/{schedule}/rows/cells", json={
            "edits": edits, "action": "override_cells",
        }).json()
        assert all("Width (mm)" in r["overrides"] for r in after["rows"])
        assert after["history"]["can_undo"], "a bulk override is one undoable step"

    def test_restoring_the_block_puts_every_cell_back_on_the_library(
        self, client, schedule
    ):
        grid = self._with_products(client, schedule)
        client.post(f"/api/schedules/{schedule}/rows/cells", json={
            "edits": [
                {"row_id": r["id"], "values": {}, "overrides": {"Width (mm)": 1234}}
                for r in grid["rows"]
            ],
            "action": "override_cells",
        })
        after = client.post(f"/api/schedules/{schedule}/rows/cells", json={
            "edits": [
                {"row_id": r["id"], "values": {}, "overrides": {"Width (mm)": ""}}
                for r in grid["rows"]
            ],
            "action": "restore_cells",
        }).json()
        assert all(not r["overrides"] for r in after["rows"])
        assert all(r["computed"]["Width (mm)"] == 900 for r in after["rows"])
