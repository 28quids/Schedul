"""Engine and session setup.

SQLite locally, PostgreSQL later. The only SQLite-specific thing here is the
foreign-key pragma, which is off by default and would otherwise let a cascade
silently not happen.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Base

__all__ = ["DATA_DIR", "database_url", "make_engine", "SessionLocal", "get_session", "init_db"]

DATA_DIR = Path(os.environ.get("SCHEDUL_DATA", Path.cwd() / "data"))


def database_url() -> str:
    """Where the database lives. ``SCHEDUL_DATABASE_URL`` overrides."""
    configured = os.environ.get("SCHEDUL_DATABASE_URL")
    if configured:
        return configured
    DATA_DIR.mkdir(parents=True, exist_ok=True)
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
        SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return SessionLocal


def init_db(url: str | None = None) -> Engine:
    """Create the schema. Safe to call repeatedly."""
    global _engine, SessionLocal
    _engine = make_engine(url)
    Base.metadata.create_all(_engine)
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
