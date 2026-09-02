"""Engine and session setup, and where the record actually lives.

SQLite locally, PostgreSQL later. The only SQLite-specific thing here is the
foreign-key pragma, which is off by default and would otherwise let a cascade
silently not happen.

**The database does not live in the source folder.** It used to: ``./data``
relative to wherever the server was started, which is inside the checkout. That
is fine until somebody updates by downloading the new version into a new folder,
at which point their equipment library, their projects and their branding are
all still sitting in the old one, and the tool comes up looking like a fresh
install. Nothing was lost, but nothing was found either, and "it wiped
everything" is what it looks like from the outside.

So the default is a per-user data directory outside the checkout, the same place
every other desktop application keeps its data, and a database found in the old
location is copied up to it once. Copied rather than moved: a migration that
goes wrong should leave the original where it was.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Base
from .upgrade import upgrade

__all__ = [
    "DATA_DIR", "default_data_dir", "legacy_data_dir", "adopt_legacy_database",
    "database_url", "make_engine", "SessionLocal", "get_session", "init_db",
    "check_database", "DatabaseUnreadable",
]

log = logging.getLogger(__name__)


def default_data_dir() -> Path:
    """Where a user's data lives when nothing says otherwise.

    Per-user and outside the checkout, so updating the tool -- by pulling, by
    downloading a fresh zip, by unpacking it somewhere else entirely -- cannot
    separate somebody from their library.
    """
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / "Schedul"
    elif sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Schedul"
    else:
        xdg = os.environ.get("XDG_DATA_HOME")
        if xdg:
            return Path(xdg) / "schedul"
        return Path.home() / ".local" / "share" / "schedul"
    return Path.home() / ".schedul"


def legacy_data_dir() -> Path:
    """Where earlier builds put it: ``./data``, next to wherever they were run."""
    return Path.cwd() / "data"


DATA_DIR = Path(os.environ.get("SCHEDUL_DATA") or default_data_dir())


def adopt_legacy_database() -> Path | None:
    """Bring a database from the old in-checkout location up to the new one.

    Once, and only when there is nothing at the new location to overwrite.
    Returns what was adopted, for the log and for the settings screen to
    report -- a migration nobody is told about is one nobody can check.
    """
    target = DATA_DIR / "schedul.db"
    if target.exists():
        return None
    source = legacy_data_dir() / "schedul.db"
    if not source.exists() or source.resolve() == target.resolve():
        return None
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    log.info("adopted the database from %s into %s", source, target)
    return source


#: Every SQLite file begins with these bytes. A file that does not is not one.
_SQLITE_MAGIC = b"SQLite format 3\x00"


def check_database(path: Path) -> str | None:
    """Why this file cannot be used as the database, or None if it can.

    Called before anything tries to open it, because the alternative is a
    hundred lines of SQLAlchemy traceback ending in "file is not a database" --
    which is true, and says nothing about what to do next.

    The usual cause is the file having been copied through something that
    rewrites text. A ``.db`` is binary: opening one in Notepad and pasting it
    somewhere re-encodes every byte and rewrites the line endings, and what
    comes out the other side is the right size and completely unreadable. It is
    an easy mistake to make and an impossible one to diagnose from the error.
    """
    if not path.exists():
        return None
    try:
        size = path.stat().st_size
    except OSError as exc:
        return f"{path} cannot be read ({exc})."
    if size == 0:
        return None  # SQLite treats an empty file as a database it may create

    try:
        with path.open("rb") as handle:
            header = handle.read(16)
    except OSError as exc:
        return f"{path} cannot be read ({exc})."

    if header != _SQLITE_MAGIC:
        return (
            f"{path} is not a database file.\n"
            f"It starts with {header[:16]!r} rather than SQLite's own header, which "
            f"is what happens when a .db is copied through a text editor: it is a "
            f"binary file, and opening it in Notepad and pasting it somewhere "
            f"re-encodes every byte of it.\n"
            f"Copy the original again in File Explorer, or with `copy` at a command "
            f"prompt, and put it back at this path. The original is unharmed -- what "
            f"is here is the damaged copy."
        )

    import sqlite3

    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
            connection.execute("SELECT count(*) FROM sqlite_master").fetchone()
    except sqlite3.DatabaseError as exc:
        return (
            f"{path} has SQLite's header but cannot be read ({exc}).\n"
            f"A .db copied through a text editor looks like this: the first bytes "
            f"survive and the rest does not. Copy the original again in File "
            f"Explorer, or with `copy` at a command prompt.\n"
            f"If the original is gone, a backup taken from Settings -> Your data "
            f"can be put here under this name."
        )
    return None


def database_url() -> str:
    """Where the database lives. ``SCHEDUL_DATABASE_URL`` overrides."""
    configured = os.environ.get("SCHEDUL_DATABASE_URL")
    if configured:
        return configured
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    adopt_legacy_database()
    return f"sqlite:///{DATA_DIR / 'schedul.db'}"


def make_engine(url: str | None = None) -> Engine:
    url = url or database_url()
    is_sqlite = url.startswith("sqlite")
    engine = create_engine(
        url,
        future=True,
        # A local single-process server serves requests on a threadpool, and
        # SQLite objects are otherwise pinned to the creating thread.
        connect_args={"check_same_thread": False} if is_sqlite else {},
    )
    if is_sqlite:
        @event.listens_for(engine, "connect")
        def _enable_foreign_keys(dbapi_connection, _record):  # type: ignore[no-untyped-def]
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


_engine: Engine | None = None
SessionLocal: sessionmaker[Session] | None = None


def _ensure() -> sessionmaker[Session]:
    global _engine, SessionLocal
    if SessionLocal is None:
        url = database_url()
        _refuse_if_unreadable(url)
        _engine = make_engine(url)
        Base.metadata.create_all(_engine)
        upgrade(_engine)
        SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return SessionLocal


class DatabaseUnreadable(RuntimeError):
    """The file where the record lives cannot be opened as a database.

    Raised instead of letting the driver's own error out, because "file is not a
    database" at the bottom of a page of traceback tells somebody nothing about
    which file, or what to do. It is deliberately fatal: starting up on a fresh
    empty database while an unreadable one sits beside it would look exactly
    like every project having been deleted.
    """


def _refuse_if_unreadable(url: str) -> None:
    if not url.startswith("sqlite"):
        return
    _, _, tail = url.partition(":///")
    if not tail:
        return
    problem = check_database(Path(tail))
    if problem:
        raise DatabaseUnreadable(problem)


def init_db(url: str | None = None) -> Engine:
    """Create the schema, and add any column a previous version did not have.

    Safe to call repeatedly. The upgrade step is additive only -- see
    ``db/upgrade.py`` -- so starting a newer build against an existing database
    cannot lose anything already in it.
    """
    global _engine, SessionLocal
    resolved = url or database_url()
    _refuse_if_unreadable(resolved)
    _engine = make_engine(resolved)
    Base.metadata.create_all(_engine)
    upgrade(_engine)
    SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def get_session() -> Iterator[Session]:
    """FastAPI dependency: one session per request, rolled back on error."""
    factory = _ensure()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
