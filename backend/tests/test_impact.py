"""What changed, what it lands on, and whether anyone had to be told.

Everything here is shared on purpose -- a library value is read rather than
copied, a type's columns are the type's -- which is exactly why a schedule can
change under somebody who did not touch it. These are the tests that the tool
can say why.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from schedul.core.catalogue import Column, compare_columns

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def columns(*specs):
    return [Column(kind=k, name=n, **kw) for k, n, kw in specs]


class TestColumnDiff:
    base = [
        Column("input", "Ref", width=12),
        Column("input", "Location", width=20),
        Column("library", "Manufacturer", width=18),
        Column("derived", "Total", unit="W", formula="={Ref}", note="n"),
    ]

    def test_no_change_is_no_change(self):
        assert compare_columns(self.base, list(self.base)).empty is True

    def test_a_width_change_is_presentational_and_propagates_quietly(self):
        after = [Column("input", "Ref", width=30), *self.base[1:]]
        diff = compare_columns(self.base, after)
        assert diff.presentational is True
        assert diff.structural is False
        assert diff.severity == "info"
        assert diff.resized == ("Ref",)

    def test_reordering_is_noticed(self):
        after = [self.base[1], self.base[0], *self.base[2:]]
        diff = compare_columns(self.base, after)
        assert diff.reordered is True
        assert diff.structural is False

    def test_adding_a_column_is_structural(self):
        after = [*self.base, Column("input", "Notes")]
        diff = compare_columns(self.base, after)
        assert diff.added == ("Notes",)
        assert diff.structural is True
        assert diff.severity == "warn"

    def test_removing_a_column_warns_about_what_was_typed_into_it(self):
        after = [c for c in self.base if c.name != "Location"]
        diff = compare_columns(self.base, after)
        assert diff.removed == ("Location",)
        assert any("kept in the record" in w for w in diff.warnings())

    def test_a_column_replaced_in_place_reads_as_a_rename(self):
        after = [Column("input", "Unit Reference", width=12), *self.base[1:]]
        diff = compare_columns(self.base, after)
        assert diff.renamed == (("Ref", "Unit Reference"),)
        assert diff.added == () and diff.removed == ()
        assert any("read as empty" in w for w in diff.warnings())

    def test_changing_a_kind_is_called_out_because_the_source_changes(self):
        after = [
            Column("library", "Ref", width=12), *self.base[1:],
        ]
        diff = compare_columns(self.base, after)
        assert diff.kind_changed == ("Ref",)
        assert any("where its value comes from" in w for w in diff.warnings())

    def test_a_formula_change_is_reported_but_orphans_nothing(self):
        after = [*self.base[:3], Column("derived", "Total", unit="W", formula="={Ref}*2")]
        diff = compare_columns(self.base, after)
        assert diff.formula_changed == ("Total (W)",)
        assert diff.structural is False

    def test_the_summary_reads_as_a_sentence(self):
        after = [*self.base, Column("input", "Notes")]
        assert compare_columns(self.base, after).summary() == "added Notes"


@pytest.fixture()
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("SCHEDUL_DATABASE_URL", f"sqlite:///{tmp_path / 'impact.db'}")
    import schedul.db.session as session_module

    session_module.SessionLocal = None
    session_module.init_db(f"sqlite:///{tmp_path / 'impact.db'}")
    from schedul.api.main import app

    return TestClient(app)


@pytest.fixture()
def mvhr(client) -> dict:
    types = client.get("/api/catalogue").json()
    return client.get(f"/api/catalogue/{next(t['id'] for t in types if t['code'] == 'MVHR')}").json()


@pytest.fixture()
def schedule(client, mvhr) -> str:
    project = client.post("/api/projects", json={"number": "CM1", "name": "J"}).json()
    building = project["buildings"][0]["id"]
    result = client.post(
        f"/api/projects/{project['id']}/buildings/{building}/schedules",
        json={"code": "MVHR"},
    ).json()
    sid = result["buildings"][0]["schedules"][0]["id"]
    client.post(f"/api/schedules/{sid}/rows", json={
        "values": {"Unit Reference": "MVHR-01", "Supply Airflow (l/s)": 450},
    })
    return sid


def save(client, type_detail, columns, change=""):
    return client.put(f"/api/catalogue/{type_detail['id']}", json={
        "code": type_detail["code"], "title": type_detail["title"],
        "short": type_detail["short"], "volume": type_detail["volume"],
        "columns": columns, "notes": type_detail["notes"], "change": change,
    })


class TestLayoutPropagation:
    def test_a_width_change_reaches_a_schedule_already_built(self, client, mvhr, schedule):
        widened = [dict(c) for c in mvhr["columns"]]
        widened[0]["width"] = 44
        assert save(client, mvhr, widened).status_code == 200

        grid = client.get(f"/api/schedules/{schedule}").json()
        assert grid["columns"][0]["width"] == 44

    def test_reordering_reaches_a_schedule_already_built(self, client, mvhr, schedule):
        moved = [dict(c) for c in mvhr["columns"]]
        moved[0], moved[1] = moved[1], moved[0]
        assert save(client, mvhr, moved).status_code == 200

        grid = client.get(f"/api/schedules/{schedule}").json()
        assert grid["columns"][0]["name"] == mvhr["columns"][1]["name"]

    def test_a_new_column_appears_and_the_typed_values_survive(self, client, mvhr, schedule):
        added = [dict(c) for c in mvhr["columns"]]
        added.append({"kind": "input", "name": "Commissioning Note", "unit": "", "width": 20})
        assert save(client, mvhr, added).status_code == 200

        grid = client.get(f"/api/schedules/{schedule}").json()
        assert "Commissioning Note" in [c["name"] for c in grid["columns"]]
        assert grid["rows"][0]["values"]["Unit Reference"] == "MVHR-01"

    def test_a_schedule_says_which_version_it_was_built_against(self, client, mvhr, schedule):
        assert client.get(f"/api/schedules/{schedule}").json()["type_drift"] == {}

        widened = [dict(c) for c in mvhr["columns"]]
        widened[0]["width"] = 40
        save(client, mvhr, widened, change="widened the reference column")

        drift = client.get(f"/api/schedules/{schedule}").json()["type_drift"]
        assert drift["built_against"] == 1
        assert drift["current"] == 2
        assert any("widened" in c["change"] for c in drift["changes"])


class TestImpactPreview:
    def test_the_designer_can_ask_what_a_change_would_land_on(self, client, mvhr, schedule):
        renamed = [dict(c) for c in mvhr["columns"]]
        renamed[0]["name"] = "Unit Ref"

        preview = client.post(f"/api/catalogue/{mvhr['id']}/impact", json={
            "code": mvhr["code"], "title": mvhr["title"], "volume": mvhr["volume"],
            "columns": renamed, "notes": mvhr["notes"],
        }).json()

        assert preview["diff"]["structural"] is True
        assert preview["affected_count"] == 1
        assert preview["rows_at_risk"] == 1
        assert any("read as empty" in w for w in preview["diff"]["warnings"])

    def test_a_presentational_change_puts_nothing_at_risk(self, client, mvhr, schedule):
        widened = [dict(c) for c in mvhr["columns"]]
        widened[0]["width"] = 40

        preview = client.post(f"/api/catalogue/{mvhr['id']}/impact", json={
            "code": mvhr["code"], "title": mvhr["title"], "volume": mvhr["volume"],
            "columns": widened, "notes": mvhr["notes"],
        }).json()

        assert preview["diff"]["structural"] is False
        assert preview["rows_at_risk"] == 0
        assert preview["affected_count"] == 1, "it still reaches the schedule"

    def test_the_preview_changes_nothing(self, client, mvhr, schedule):
        renamed = [dict(c) for c in mvhr["columns"]]
        renamed[0]["name"] = "Unit Ref"
        client.post(f"/api/catalogue/{mvhr['id']}/impact", json={
            "code": mvhr["code"], "title": mvhr["title"], "volume": mvhr["volume"],
            "columns": renamed, "notes": mvhr["notes"],
        })
        assert client.get(f"/api/catalogue/{mvhr['id']}").json()["version"] == 1


class TestTheLog:
    def test_a_type_change_is_recorded_with_what_it_reaches(self, client, mvhr, schedule):
        widened = [dict(c) for c in mvhr["columns"]]
        widened[0]["width"] = 40
        save(client, mvhr, widened, change="widened for long references")

        log = client.get("/api/impact").json()
        entry = next(e for e in log["entries"] if e["area"] == "type")
        assert "MVHR" in entry["summary"]
        assert entry["detail"]["affected_count"] == 1

    def test_a_structural_change_is_a_warning_and_a_width_change_is_not(
        self, client, mvhr, schedule
    ):
        renamed = [dict(c) for c in mvhr["columns"]]
        renamed[0]["name"] = "Unit Ref"
        save(client, mvhr, renamed)
        assert client.get("/api/impact").json()["entries"][0]["severity"] == "warn"

    def test_library_corrections_appear_because_they_move_every_schedule(self, client):
        client.post("/api/library", json={
            "type_code": "MVHR", "model_reference": "SYS-1",
            "values": {"Manufacturer": "Systemair"},
        })
        entry_id = client.get("/api/library/MVHR").json()[0]["id"]
        client.put(f"/api/library/{entry_id}", json={
            "type_code": "MVHR", "model_reference": "SYS-1",
            "values": {"Manufacturer": "Systemair AB"},
        })

        log = client.get("/api/impact").json()
        updates = [e for e in log["entries"] if e["area"] == "library"]
        assert any("moves every schedule using it" in e["summary"] for e in updates)

    def test_changing_the_house_notes_is_recorded(self, client):
        client.put("/api/settings", json={"general_notes": ["a new standing note"]})
        log = client.get("/api/impact").json()
        assert any(e["area"] == "notes" for e in log["entries"])

    def test_saving_settings_unchanged_records_nothing(self, client):
        settings = client.get("/api/settings").json()["house_standard"]
        client.put("/api/settings", json={"general_notes": settings["general_notes"]})
        assert not [e for e in client.get("/api/impact").json()["entries"] if e["area"] == "notes"]

    def test_schedules_behind_their_type_are_listed(self, client, mvhr, schedule):
        widened = [dict(c) for c in mvhr["columns"]]
        widened[0]["width"] = 40
        save(client, mvhr, widened)

        log = client.get("/api/impact").json()
        assert log["counts"]["stale"] == 1
        assert log["stale_schedules"][0]["behind"] == 1

    def test_the_log_can_be_narrowed_to_one_area(self, client, mvhr, schedule):
        widened = [dict(c) for c in mvhr["columns"]]
        widened[0]["width"] = 40
        save(client, mvhr, widened)
        client.put("/api/settings", json={"general_notes": ["changed"]})

        only_types = client.get("/api/impact?area=type").json()
        assert {e["area"] for e in only_types["entries"]} == {"type"}

    def test_an_untouched_organisation_has_an_empty_log(self, client):
        log = client.get("/api/impact").json()
        assert log["entries"] == []
        assert log["counts"] == {"entries": 0, "warnings": 0, "stale": 0}
