"""The descriptor on the end of an exported filename.

The v1 files carried the full schedule title after the document number, which
makes an issued filename long enough to be awkward in a document management
system. The ending is a house-standard setting, and the eight v1 samples must
still regenerate exactly under the default -- shortening it is a choice a
practice makes, not a change to what the tool produces out of the box.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from schedul.core.house import DEFAULT_NAMING
from schedul.core.naming import NamingScheme, ResolutionContext

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def context() -> ResolutionContext:
    return ResolutionContext(
        project={"project_number": "CM4220"},
        building={"building": "HQ049"},
        type={"volume": "5_6"},
        schedule={"number": 10},
    )


class TestTheSuffix:
    def test_the_default_is_the_v1_ending(self):
        scheme = NamingScheme.from_dict(DEFAULT_NAMING)
        assert scheme.filename(context(), "Fan Coil Unit Schedule").endswith(
            "_-_Fan_Coil_Unit_Schedule.xlsx"
        )

    def test_it_can_be_dropped_for_a_short_issued_filename(self):
        scheme = NamingScheme.from_dict({**DEFAULT_NAMING, "suffix": ""})
        name = scheme.filename(context(), "Fan Coil Unit Schedule")
        assert name == "CM4220-BOV-5_6-HQ049-SC-M-00000010-G00300-XX-XX.xlsx"

    def test_a_practice_can_use_its_own_ending(self):
        scheme = NamingScheme.from_dict({**DEFAULT_NAMING, "suffix": " ({title_slug})"})
        assert scheme.filename(context(), "AHU Schedule").endswith(" (AHU_Schedule).xlsx")

    def test_the_document_number_is_never_affected(self):
        for suffix in ("", "_-_{title_slug}", "-x"):
            scheme = NamingScheme.from_dict({**DEFAULT_NAMING, "suffix": suffix})
            assert scheme.document_number(context()) == (
                "CM4220-BOV-5_6-HQ049-SC-M-00000010-G00300-XX-XX"
            )


@pytest.fixture()
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("SCHEDUL_DATABASE_URL", f"sqlite:///{tmp_path / 'names.db'}")
    import schedul.db.session as session_module

    session_module.SessionLocal = None
    session_module.init_db(f"sqlite:///{tmp_path / 'names.db'}")
    from schedul.api.main import app

    return TestClient(app)


@pytest.fixture()
def schedule(client) -> tuple[str, str]:
    project = client.post("/api/projects", json={"number": "CM4220", "name": "J"}).json()
    building = project["buildings"][0]["id"]
    result = client.post(
        f"/api/projects/{project['id']}/buildings/{building}/schedules",
        json={"code": "MVHR"},
    ).json()
    return project["id"], result["buildings"][0]["schedules"][0]["id"]


class TestThroughTheApi:
    def test_the_ending_is_a_house_standard_setting(self, client, schedule):
        project_id, schedule_id = schedule
        settings = client.get("/api/settings").json()["house_standard"]

        client.put("/api/settings", json={"naming": {**settings["naming"], "suffix": ""}})
        response = client.get(f"/api/schedules/{schedule_id}/export.xlsx")
        name = response.headers["content-disposition"]

        assert "Mechanical_Ventilation" not in name
        assert name.endswith('.xlsx"')

    def test_the_register_and_the_schedule_agree_on_the_filename(self, client, schedule):
        project_id, schedule_id = schedule
        settings = client.get("/api/settings").json()["house_standard"]
        client.put("/api/settings", json={"naming": {**settings["naming"], "suffix": ""}})

        registered = client.get(f"/api/register?project_id={project_id}").json()[0]
        grid = client.get(f"/api/schedules/{schedule_id}").json()
        assert registered["file_name"] == grid["schedule"]["filename"]
        assert registered["file_name"].count("_-_") == 0

    def test_a_project_summary_says_which_buildings_it_has(self, client, schedule):
        project_id, _ = schedule
        client.post(f"/api/projects/{project_id}/buildings", json={
            "ref": "HQ014", "name": "East Wing",
        })
        summary = next(
            p for p in client.get("/api/projects").json() if p["id"] == project_id
        )
        assert summary["building_count"] == 2
        assert "HQ014 - East Wing" in summary["buildings"]
