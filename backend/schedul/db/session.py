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
        _engine = make_engine()
        Base.metadata.create_all(_engine)
        upgrade(_engine)
        SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return SessionLocal


def init_db(url: str | None = None) -> Engine:
    """Create the schema, and add any column a previous version did not have.

    Safe to call repeatedly. The upgrade step is additive only -- see
    ``db/upgrade.py`` -- so starting a newer build against an existing database
    cannot lose anything already in it.
    """
    global _engine, SessionLocal
    _engine = make_engine(url)
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
