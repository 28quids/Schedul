"""Moving the record around, without a text editor anywhere near it.

Somebody updating the tool has one file to carry across, and carrying it by
hand went wrong in the way binary files always go wrong: opened in a text
editor, pasted, saved, and now every byte of it is re-encoded. The file is the
right size and completely unreadable, and the error the server gives for it --
"file is not a database" -- says nothing about what happened or what to do.

So the copy is a command rather than an instruction:

    python -m schedul.dbtool where
    python -m schedul.dbtool restore "C:\\old\\backend\\data\\schedul.db"
    python -m schedul.dbtool backup

``restore`` checks the file is really a database before it touches anything, and
moves whatever is already there aside rather than over. Nothing here deletes a
database: the worst case is a folder with one more file in it than expected.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import shutil
import sqlite3
import sys
from pathlib import Path

from .db.session import DATA_DIR, check_database

__all__ = ["main", "restore", "backup", "live_database"]


def live_database() -> Path:
    """The file Schedul reads and writes."""
    return DATA_DIR / "schedul.db"


def _stamp() -> str:
    return _dt.datetime.now().strftime("%Y-%m-%d-%H%M%S")


def restore(source: Path) -> Path:
    """Put ``source`` in place as the live database.

    Refuses a file that is not a database, so a damaged copy is caught here --
    where it can be explained -- rather than at the next startup. Anything
    already in place is renamed out of the way rather than overwritten: being
    wrong about which of two databases is the good one is entirely possible, and
    should not be final.
    """
    source = Path(source).expanduser()
    if not source.exists():
        raise SystemExit(f"There is no file at {source}.")

    problem = check_database(source)
    if problem:
        raise SystemExit(
            f"That file cannot be used:\n\n{problem}\n\n"
            f"Nothing has been changed."
        )
    if source.stat().st_size == 0:
        raise SystemExit(f"{source} is empty, so there is nothing to restore.")

    target = live_database()
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() == target.resolve():
        raise SystemExit(f"{source} is already the live database.")

    if target.exists() and target.stat().st_size:
        aside = target.with_name(f"schedul-replaced-{_stamp()}.db")
        target.rename(aside)
        print(f"Moved the database that was here to {aside}")

    shutil.copy2(source, target)
    print(f"Restored {source}\n       to {target}")
    print(_describe(target))
    return target


def backup(into: Path | None = None) -> Path:
    """A consistent copy of the live database, taken through SQLite's own API.

    A plain file copy of a database being written to is a copy that may not
    open, and a backup nobody can restore is worse than none for what it is
    believed to be.
    """
    source = live_database()
    if not source.exists():
        raise SystemExit(f"There is no database at {source} yet.")

    folder = Path(into).expanduser() if into else Path.cwd()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"schedul-backup-{_stamp()}.db"
    with sqlite3.connect(source) as live, sqlite3.connect(target) as copy:
        live.backup(copy)
    print(f"Backed up to {target}")
    return target


def _describe(path: Path) -> str:
    """What is in a database, so a restore can be checked rather than believed."""
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
            counts = []
            for table, label in (
                ("project", "project"),
                ("schedule", "schedule"),
                ("equipment", "library entry"),
            ):
                try:
                    n = connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                except sqlite3.DatabaseError:
                    continue
                counts.append(f"{n} {label}{'' if n == 1 else 's'}")
    except sqlite3.DatabaseError as exc:  # pragma: no cover - already validated
        return f"(could not be read back: {exc})"
    return "It holds " + ", ".join(counts) + "." if counts else "It is empty."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m schedul.dbtool",
        description="Move Schedul's database around safely.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("where", help="print where the database lives")

    restore_command = commands.add_parser(
        "restore", help="put a database file in place as the live one"
    )
    restore_command.add_argument("source", help="the .db file to restore")

    backup_command = commands.add_parser("backup", help="take a copy of the live one")
    backup_command.add_argument(
        "--into", default=None, help="where to put it (default: here)"
    )

    args = parser.parse_args(argv)

    if args.command == "where":
        path = live_database()
        print(path)
        if not path.exists():
            print("There is no database there yet; one is created on first run.")
        else:
            print(_describe(path))
        return 0

    if args.command == "restore":
        restore(Path(args.source))
        return 0

    backup(Path(args.into) if args.into else None)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
