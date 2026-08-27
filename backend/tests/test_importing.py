"""Bringing a supplier's range into the library, forty rows at a time.

The rule being defended is that nothing is written until it has been shown. A
careless mapping can overwrite a hundred correct values in one click, so the dry
run is the default and applying is the exception -- and applying carries out the
plan that was shown rather than working it out again.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

BLOCK = (
    "Model Reference\tManufacturer\tLength (mm)\n"
    "SYS-VSR-500\tSystemair\t1200\n"
    "SYS-VSR-700\tSystemair\t1400\n"
)


@pytest.fixture()
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("SCHEDUL_DATABASE_URL", f"sqlite:///{tmp_path / 'import.db'}")
    import schedul.db.session as session_module

    session_module.SessionLocal = None
    session_module.init_db(f"sqlite:///{tmp_path / 'import.db'}")
    from schedul.api.main import app

    return TestClient(app)


def plan(client, **body):
    response = client.post("/api/library/import", json={"type_code": "MVHR", **body})
    assert response.status_code == 200, response.text
    return response.json()


def library(client, code="MVHR"):
    return {e["model_reference"]: e for e in client.get(f"/api/library/{code}").json()}


class TestPlanning:
    def test_the_plan_says_what_each_row_would_do(self, client):
        result = plan(client, text=BLOCK)
        assert result["counts"]["create"] == 2
        assert [r["model_reference"] for r in result["rows"]] == ["SYS-VSR-500", "SYS-VSR-700"]
        assert result["header_detected"] is True

    def test_planning_writes_nothing(self, client):
        plan(client, text=BLOCK)
        assert library(client) == {}

    def test_a_header_matches_the_columns_by_name_in_any_order(self, client):
        result = plan(client, text=(
            "Manufacturer\tModel Reference\n"
            "Systemair\tSYS-1\n"
        ))
        assert result["rows"][0]["values"]["Manufacturer"] == "Systemair"
        assert result["rows"][0]["model_reference"] == "SYS-1"

    def test_without_a_header_the_caller_maps_the_columns(self, client):
        result = plan(
            client,
            text="SYS-1\tSystemair\n",
            mapping=["Model Reference", "Manufacturer"],
        )
        assert result["rows"][0]["values"] == {"Manufacturer": "Systemair"}

    def test_an_unmapped_column_is_ignored_rather_than_guessed_at(self, client):
        result = plan(
            client,
            text="SYS-1\tred\n",
            mapping=["Model Reference", None],
        )
        assert result["rows"][0]["values"] == {}

    def test_a_column_that_is_not_a_library_field_cannot_be_mapped(self, client):
        # 'Supply Airflow' is an input column: it differs per unit, so a value
        # imported into it would be a stale copy the moment it landed.
        offered = client.get("/api/library/MVHR/import/columns").json()["columns"]
        assert "Supply Airflow (l/s)" not in offered
        assert "Model Reference" in offered and "Manufacturer" in offered

    def test_without_a_model_reference_nothing_can_be_imported(self, client):
        result = plan(client, text="Systemair\t1200\n", mapping=["Manufacturer", "Length (mm)"])
        assert result["rows"] == []
        assert any("Model Reference" in w for w in result["warnings"])
        assert result["can_apply"] is False

    def test_a_row_with_a_blank_reference_is_refused_with_a_reason(self, client):
        result = plan(client, text="Model Reference\tManufacturer\n\tSystemair\n")
        assert result["rows"][0]["action"] == "skip"
        assert "nothing to key this on" in result["rows"][0]["reason"]

    def test_the_same_reference_twice_in_one_paste_is_caught(self, client):
        result = plan(client, text=(
            "Model Reference\tManufacturer\nSYS-1\tSystemair\nSYS-1\tSystemair AB\n"
        ))
        assert result["counts"]["create"] == 1
        assert "line 1" in result["rows"][1]["reason"]

    def test_an_empty_paste_says_so(self, client):
        result = plan(client, text="   ")
        assert any("nothing to import" in w for w in result["warnings"])


class TestDuplicatesAndUpdates:
    def test_an_existing_product_is_an_update_not_a_second_entry(self, client):
        plan(client, text=BLOCK, apply=True)
        result = plan(client, text=(
            "Model Reference\tManufacturer\tLength (mm)\nSYS-VSR-500\tSystemair\t1250\n"
        ))
        row = result["rows"][0]
        assert row["action"] == "update"
        assert row["changes"] == [{"column": "Length (mm)", "before": 1200, "after": "1250"}]

    def test_a_reference_that_differs_only_in_case_is_the_same_product(self, client):
        plan(client, text=BLOCK, apply=True)
        result = plan(client, text="Model Reference\tManufacturer\nsys-vsr-500\tSystemair\n")
        assert result["rows"][0]["action"] == "unchanged"

    def test_a_row_that_changes_nothing_is_reported_as_such(self, client):
        plan(client, text=BLOCK, apply=True)
        result = plan(client, text=BLOCK)
        assert result["counts"]["unchanged"] == 2
        assert result["can_apply"] is False
        assert result["destructive"] is False

    def test_updates_can_be_turned_off_entirely(self, client):
        plan(client, text=BLOCK, apply=True)
        result = plan(client, text=(
            "Model Reference\tManufacturer\tLength (mm)\nSYS-VSR-500\tSystemair\t9999\n"
        ), update_existing=False)
        assert result["rows"][0]["action"] == "skip"
        assert "already in the library" in result["rows"][0]["reason"]

    def test_an_import_that_would_change_values_says_it_is_destructive(self, client):
        plan(client, text=BLOCK, apply=True)
        result = plan(client, text=(
            "Model Reference\tLength (mm)\nSYS-VSR-500\t1250\n"
        ))
        assert result["destructive"] is True

    def test_a_blank_cell_never_deletes_what_is_already_known(self, client):
        plan(client, text=BLOCK, apply=True)
        # A supplier's sheet is usually partial. A blank Length means "not
        # stated here", not "forget the 1200 you already have".
        plan(client, text=(
            "Model Reference\tManufacturer\tLength (mm)\nSYS-VSR-500\tSystemair AB\t\n"
        ), apply=True)
        entry = library(client)["SYS-VSR-500"]["values"]
        assert entry["Length (mm)"] == 1200
        assert entry["Manufacturer"] == "Systemair AB"


class TestApplying:
    def test_applying_creates_the_products(self, client):
        result = plan(client, text=BLOCK, apply=True)
        assert result["applied"] == 2
        entries = library(client)
        assert set(entries) == {"SYS-VSR-500", "SYS-VSR-700"}
        assert entries["SYS-VSR-500"]["values"]["Manufacturer"] == "Systemair"

    def test_numbers_arrive_as_numbers(self, client):
        plan(client, text=BLOCK, apply=True)
        assert library(client)["SYS-VSR-500"]["values"]["Length (mm)"] == 1200

    def test_applying_updates_only_what_changed(self, client):
        plan(client, text=BLOCK, apply=True)
        result = plan(client, text=(
            "Model Reference\tManufacturer\tLength (mm)\n"
            "SYS-VSR-500\tSystemair\t1250\n"
            "SYS-VSR-700\tSystemair\t1400\n"
        ), apply=True)
        assert result["applied"] == 1, "the unchanged row is not rewritten"
        assert library(client)["SYS-VSR-500"]["values"]["Length (mm)"] == 1250

    def test_an_import_with_nothing_to_do_is_refused_rather_than_silently_passing(self, client):
        plan(client, text=BLOCK, apply=True)
        response = client.post("/api/library/import", json={
            "type_code": "MVHR", "text": BLOCK, "apply": True,
        })
        assert response.status_code == 400
        assert "nothing to import" in response.json()["detail"]

    def test_imported_products_are_flagged_for_review_like_any_other(self, client):
        plan(client, text=BLOCK, apply=True)
        queue = client.get("/api/library/review/queue").json()
        assert {e["model_reference"] for e in queue} >= {"SYS-VSR-500", "SYS-VSR-700"}

    def test_an_import_is_recorded_in_the_change_log(self, client):
        plan(client, text=BLOCK, apply=True)
        entries = client.get("/api/impact?area=library").json()["entries"]
        assert any("SYS-VSR-500" in e["summary"] for e in entries)

    def test_the_preview_and_the_apply_agree(self, client):
        preview = plan(client, text=BLOCK)
        applied = plan(client, text=BLOCK, apply=True)
        assert preview["counts"]["create"] == applied["applied"]
        assert [r["action"] for r in preview["rows"]] == [r["action"] for r in applied["rows"]]


class TestGridEntry:
    def test_several_products_save_in_one_go(self, client):
        response = client.post("/api/library/bulk", json={
            "type_code": "MVHR",
            "rows": [
                {"type_code": "MVHR", "model_reference": "RAD-001",
                 "values": {"Manufacturer": "Merriott"}},
                {"type_code": "MVHR", "model_reference": "RAD-002",
                 "values": {"Manufacturer": "Merriott"}},
            ],
        })
        assert response.status_code == 201
        assert len(response.json()) == 2
        assert set(library(client)) == {"RAD-001", "RAD-002"}

    def test_a_row_with_no_reference_refuses_the_whole_batch(self, client):
        response = client.post("/api/library/bulk", json={
            "type_code": "MVHR",
            "rows": [
                {"type_code": "MVHR", "model_reference": "RAD-001", "values": {}},
                {"type_code": "MVHR", "model_reference": "  ", "values": {"Manufacturer": "X"}},
            ],
        })
        assert response.status_code == 400
        assert "row(s) 2" in response.json()["detail"]
        assert library(client) == {}, "nothing is saved when the batch is refused"
