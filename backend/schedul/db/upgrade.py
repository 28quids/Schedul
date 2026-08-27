"""Bringing an existing database up to the current schema, additively.

``Base.metadata.create_all`` creates tables that are missing but never touches a
table that already exists, so a column added to ``project`` or ``schedule``
after somebody has been using the tool would simply not be there. This closes
that gap without pulling in a migration framework: it compares the models
against what the database actually has and issues ``ALTER TABLE ADD COLUMN`` for
what is missing.

Deliberately one-directional. Nothing here drops a column, renames one, or
rewrites a value -- an additive change is the only kind that cannot destroy
somebody's work, and anything beyond that is a real migration that deserves a
real tool and a backup.
"""

from __future__ import annotations

import logging
from typing import Iterable

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from .models import Base

__all__ = ["missing_columns", "upgrade"]

log = logging.getLogger(__name__)


def missing_columns(engine: Engine) -> list[tuple[str, str]]:
    """``(table, column)`` pairs the models declare but the database lacks."""
    inspector = inspect(engine)
    present = set(inspector.get_table_names())
    out: list[tuple[str, str]] = []
    for name, table in Base.metadata.tables.items():
        if name not in present:
            continue  # create_all will make the whole table
        existing = {c["name"] for c in inspector.get_columns(name)}
        for column in table.columns:
            if column.name not in existing:
                out.append((name, column.name))
    return out


def upgrade(engine: Engine) -> list[str]:
    """Add every missing column. Returns what was added, for the log."""
    added: list[str] = []
    for table_name, column_name in missing_columns(engine):
        column = Base.metadata.tables[table_name].columns[column_name]
        ddl = _add_column_sql(table_name, column)
        if ddl is None:
            log.warning(
                "cannot add %s.%s automatically; it needs a migration",
                table_name, column_name,
            )
            continue
        with engine.begin() as connection:
            connection.execute(text(ddl))
        added.append(f"{table_name}.{column_name}")
    if added:
        log.info("added columns: %s", ", ".join(added))
    return added


def _add_column_sql(table: str, column) -> str | None:
    """The ``ALTER TABLE`` for one column, or None if it cannot be added safely.

    A NOT NULL column with no default cannot be added to a table that already
    has rows -- there would be nothing to put in it. Those are reported rather
    than guessed at.
    """
    try:
        type_sql = column.type.compile()
    except Exception:  # a dialect-specific type with no generic form
        return None

    parts = [f'ALTER TABLE "{table}" ADD COLUMN "{column.name}" {type_sql}']
    default = _default_literal(column)
    if not column.nullable:
        if default is None:
            return None
        parts.append(f"NOT NULL DEFAULT {default}")
    elif default is not None:
        parts.append(f"DEFAULT {default}")
    return " ".join(parts)


def _default_literal(column) -> str | None:
    """A SQL literal for the column's Python-side default, if it has a simple one."""
    default = column.default
    if default is None:
        return None
    if getattr(default, "is_callable", False):
        try:
            value = default.arg(None)
        except Exception:
            return None
    else:
        value = getattr(default, "arg", None)
    if callable(value):
        return None
    if value is None:
        return None
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, dict)):
        import json

        return "'" + json.dumps(value).replace("'", "''") + "'"
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return None
