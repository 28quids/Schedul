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


class TestAFileThatIsNotADatabase:
    """The failure somebody actually hits when they move their data by hand.

    A ``.db`` is binary. Opened in a text editor and pasted somewhere it comes
    out the right sort of size and completely unreadable, and SQLAlchemy's own
    answer -- "file is not a database", at the foot of fifty frames -- names
    neither the file nor the cause.
    """

    @staticmethod
    def _real_database(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE project (id TEXT)")
            connection.execute("INSERT INTO project VALUES ('one')")
        return path

    @staticmethod
    def _text_editor_copy(source, target):
        """What a text editor does to a binary file: re-encode every byte."""
        target.write_text(source.read_bytes().decode("latin-1"), encoding="utf-8")
        return target

    def test_a_good_database_has_nothing_to_say(self, tmp_path):
        assert db_session.check_database(self._real_database(tmp_path / "ok.db")) is None

    def test_a_missing_file_is_not_a_problem(self, tmp_path):
        assert db_session.check_database(tmp_path / "nothing.db") is None

    def test_an_empty_file_is_not_a_problem(self, tmp_path):
        # SQLite makes a database out of an empty file, so a half-finished copy
        # that produced nothing is not something to refuse to start over.
        (tmp_path / "empty.db").write_bytes(b"")
        assert db_session.check_database(tmp_path / "empty.db") is None

    def test_something_that_is_not_a_database_says_so(self, tmp_path):
        (tmp_path / "notes.db").write_text("pasted out of Notepad")
        problem = db_session.check_database(tmp_path / "notes.db")
        assert problem and "is not a database file" in problem
        assert "text editor" in problem, "the cause, not just the symptom"
        assert "File Explorer" in problem, "and what to do about it"

    def test_a_text_editor_copy_is_caught(self, tmp_path):
        good = self._real_database(tmp_path / "good.db")
        damaged = self._text_editor_copy(good, tmp_path / "damaged.db")
        problem = db_session.check_database(damaged)
        assert problem, "a re-encoded copy is the exact case this exists for"
        assert "text editor" in problem

    def test_startup_refuses_rather_than_beginning_afresh(self, tmp_path, monkeypatch):
        # Coming up on an empty database with an unreadable one beside it would
        # look exactly like every project having been deleted.
        (tmp_path / "schedul.db").write_text("not a database")
        monkeypatch.delenv("SCHEDUL_DATABASE_URL", raising=False)
        monkeypatch.setattr(db_session, "DATA_DIR", tmp_path)
        db_session.SessionLocal = None
        with pytest.raises(db_session.DatabaseUnreadable) as raised:
            db_session.init_db()
        assert "schedul.db" in str(raised.value)

    def test_the_damaged_file_is_left_exactly_as_it_was(self, tmp_path, monkeypatch):
        (tmp_path / "schedul.db").write_text("not a database")
        before = (tmp_path / "schedul.db").read_bytes()
        monkeypatch.delenv("SCHEDUL_DATABASE_URL", raising=False)
        monkeypatch.setattr(db_session, "DATA_DIR", tmp_path)
        db_session.SessionLocal = None
        with pytest.raises(db_session.DatabaseUnreadable):
            db_session.init_db()
        assert (tmp_path / "schedul.db").read_bytes() == before


class TestMovingTheDatabaseWithoutATextEditor:
    """``python -m schedul.dbtool``, so the copy is a command rather than a chore."""

    @pytest.fixture()
    def live(self, tmp_path, monkeypatch):
        from schedul import dbtool

        monkeypatch.setattr(db_session, "DATA_DIR", tmp_path / "userdata")
        monkeypatch.setattr(dbtool, "DATA_DIR", tmp_path / "userdata")
        return dbtool

    def _database(self, path, projects=1):
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE project (id TEXT)")
            for i in range(projects):
                connection.execute("INSERT INTO project VALUES (?)", (str(i),))
        return path

    def test_it_puts_a_database_in_place(self, live, tmp_path, capsys):
        source = self._database(tmp_path / "old" / "schedul.db")
        target = live.restore(source)
        assert target.exists()
        assert db_session.check_database(target) is None
        assert "1 project" in capsys.readouterr().out, (
            "a restore says what it restored, so it can be checked rather than believed"
        )

    def test_it_refuses_a_damaged_file_before_touching_anything(self, live, tmp_path):
        good = self._database(tmp_path / "userdata" / "schedul.db", projects=3)
        before = good.read_bytes()
        (tmp_path / "bad.db").write_text("pasted out of Notepad")

        with pytest.raises(SystemExit) as raised:
            live.restore(tmp_path / "bad.db")
        assert "cannot be used" in str(raised.value)
        assert good.read_bytes() == before, "the live database is untouched"

    def test_what_was_there_is_moved_aside_not_over(self, live, tmp_path):
        existing = self._database(tmp_path / "userdata" / "schedul.db", projects=7)
        kept = existing.read_bytes()
        live.restore(self._database(tmp_path / "old" / "schedul.db", projects=1))

        spares = [p for p in (tmp_path / "userdata").iterdir() if "replaced" in p.name]
        assert len(spares) == 1, "being wrong about which database is the good one "
        assert spares[0].read_bytes() == kept, "must not be final"

    def test_a_missing_source_is_said_plainly(self, live, tmp_path):
        with pytest.raises(SystemExit) as raised:
            live.restore(tmp_path / "nowhere.db")
        assert "no file at" in str(raised.value)

    def test_backup_produces_a_database_that_opens(self, live, tmp_path):
        self._database(tmp_path / "userdata" / "schedul.db", projects=4)
        copy = live.backup(tmp_path / "backups")
        assert db_session.check_database(copy) is None
        with sqlite3.connect(copy) as connection:
            assert connection.execute("SELECT count(*) FROM project").fetchone()[0] == 4

    def test_where_prints_the_path(self, live, tmp_path, capsys):
        self._database(tmp_path / "userdata" / "schedul.db")
        assert live.main(["where"]) == 0
        assert str(tmp_path / "userdata" / "schedul.db") in capsys.readouterr().out
