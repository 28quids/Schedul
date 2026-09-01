"""Where the record is kept, and what happens to it when the tool is updated.

The failure this guards against is the worst one the tool has: somebody
downloads the new version into a new folder, opens it, and finds an empty
equipment library. Nothing was deleted -- the database was in the folder they
stopped using -- but from where they are standing it is indistinguishable from
having lost everything.
"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from schedul.db import session as db_session

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


class TestTheDefaultLocation:
    def test_it_is_never_inside_the_checkout(self, monkeypatch, tmp_path, repo_root):
        monkeypatch.chdir(tmp_path)
        chosen = db_session.default_data_dir()
        assert repo_root not in chosen.parents and chosen != repo_root, (
            "a database inside the source folder is one an update leaves behind"
        )

    def test_it_does_not_move_when_the_server_is_started_elsewhere(
        self, monkeypatch, tmp_path
    ):
        first = db_session.default_data_dir()
        monkeypatch.chdir(tmp_path)
        assert db_session.default_data_dir() == first, (
            "the record must not depend on which directory somebody ran from"
        )

    def test_windows_puts_it_under_local_app_data(self, monkeypatch):
        monkeypatch.setattr(db_session.sys, "platform", "win32")
        monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\a\AppData\Local")
        assert db_session.default_data_dir().name == "Schedul"
        assert "AppData" in str(db_session.default_data_dir())


class TestAdoptingAnOlderDatabase:
    def test_a_database_in_the_old_place_is_copied_up_once(self, monkeypatch, tmp_path):
        legacy = tmp_path / "checkout" / "data"
        legacy.mkdir(parents=True)
        sqlite3.connect(legacy / "schedul.db").close()
        target = tmp_path / "userdata"

        monkeypatch.chdir(tmp_path / "checkout")
        monkeypatch.setattr(db_session, "DATA_DIR", target)

        assert db_session.adopt_legacy_database() == legacy / "schedul.db"
        assert (target / "schedul.db").exists()
        assert (legacy / "schedul.db").exists(), (
            "copied, not moved: a migration that goes wrong must leave the original"
        )

    def test_it_never_overwrites_a_database_already_there(self, monkeypatch, tmp_path):
        legacy = tmp_path / "checkout" / "data"
        legacy.mkdir(parents=True)
        (legacy / "schedul.db").write_bytes(b"old")
        target = tmp_path / "userdata"
        target.mkdir()
        (target / "schedul.db").write_bytes(b"current")

        monkeypatch.chdir(tmp_path / "checkout")
        monkeypatch.setattr(db_session, "DATA_DIR", target)

        assert db_session.adopt_legacy_database() is None
        assert (target / "schedul.db").read_bytes() == b"current"

    def test_nothing_to_adopt_is_not_an_error(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(db_session, "DATA_DIR", tmp_path / "userdata")
        assert db_session.adopt_legacy_database() is None


class TestTheSettingsScreenCanSayWhereItIs:
    @pytest.fixture()
    def client(self, tmp_path, monkeypatch) -> TestClient:
        monkeypatch.setenv("SCHEDUL_DATABASE_URL", f"sqlite:///{tmp_path / 'store.db'}")
        db_session.SessionLocal = None
        db_session.init_db(f"sqlite:///{tmp_path / 'store.db'}")
        from schedul.api.main import app

        return TestClient(app)

    def test_it_reports_the_file_actually_in_use(self, client, tmp_path):
        data = client.get("/api/settings/storage").json()
        assert data["database"] == str(tmp_path / "store.db")
        assert data["exists"] and data["size_bytes"] > 0
        assert not data["external"]

    def test_a_backup_is_a_database_that_opens(self, client, tmp_path):
        response = client.get("/api/settings/backup.db")
        assert response.status_code == 200
        copy = tmp_path / "backup.db"
        copy.write_bytes(response.content)
        with sqlite3.connect(copy) as connection:
            tables = {
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        assert {"project", "equipment", "schedule"} <= tables, (
            "a backup nobody can restore is worse than none, because of what it is "
            "believed to be"
        )

    def test_the_backup_carries_the_data(self, client, tmp_path):
        client.post("/api/library", json={
            "type_code": "MVHR", "model_reference": "SYS-VSR-500",
            "values": {"Manufacturer": "Systemair"},
        })
        copy = tmp_path / "backup.db"
        copy.write_bytes(client.get("/api/settings/backup.db").content)
        with sqlite3.connect(copy) as connection:
            references = [
                row[0] for row in connection.execute(
                    "SELECT model_reference FROM equipment"
                )
            ]
        assert references == ["SYS-VSR-500"]
