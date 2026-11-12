"""Database infrastructure of the baseline: the local SQLite file with plain SQLAlchemy, no Tabrion (RA-B-4).

The counterpart of ``examples/measurements/measurements/db.py`` for metric MW-1 (``cloc`` on both). It implements
the features the application specifies, the way the SQLAlchemy documentation shows them:

- an ``Engine`` on the file, with a ``connect`` event for the pragmas (WAL, ``synchronous=FULL``, foreign keys, a
  busy timeout) and the documented pysqlite recipe that takes ``BEGIN`` over from the driver, so a write transaction
  starts with ``BEGIN IMMEDIATE``;
- migrations by ``PRAGMA user_version``, each step and the version in one transaction;
- a mandatory backup before migrating a database that holds data (``sqlite3``'s online backup API).

It does not add what Tabrion does on top (RA-EVAL counts those as MW-2 probe hits): owner-only file modes, the
refusal of symbolic links, a verified and atomic backup. ``hide_parameters`` keeps the parameters out of
SQLAlchemy's messages, as SQLAlchemy documents it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
import os
from pathlib import Path
import sqlite3
from typing import Any

import sqlalchemy as sa


class BackupRequired(Exception):
    """A database that holds data is migrated only after a backup."""


class BackupFailed(Exception):
    """The backup before a migration failed; nothing was migrated."""


def _v1(connection: sa.Connection) -> None:
    connection.exec_driver_sql(
        """
        CREATE TABLE measurement (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT NOT NULL CHECK (length(device_id) BETWEEN 1 AND 64),
            channel TEXT NOT NULL CHECK (length(channel) BETWEEN 1 AND 64),
            ts_utc TEXT NOT NULL CHECK (length(ts_utc) = 27 AND substr(ts_utc, 27) = 'Z'),
            value REAL NOT NULL
                CHECK (typeof(value) = 'real' AND value BETWEEN -1.7976931348623157e308 AND 1.7976931348623157e308),
            unit TEXT NOT NULL CHECK (length(unit) BETWEEN 1 AND 16),
            quality INTEGER NOT NULL DEFAULT 0 CHECK (typeof(quality) = 'integer' AND quality BETWEEN 0 AND 255),
            UNIQUE (device_id, channel, ts_utc)
        )
        """
    )


def _v2(connection: sa.Connection) -> None:
    connection.exec_driver_sql("CREATE INDEX measurement_ts ON measurement (ts_utc)")


def _v3(connection: sa.Connection) -> None:
    connection.exec_driver_sql(
        """
        CREATE TABLE transfer_mark (
            target TEXT PRIMARY KEY CHECK (length(target) BETWEEN 1 AND 64),
            seq INTEGER NOT NULL CHECK (typeof(seq) = 'integer' AND seq >= 0)
        )
        """
    )


Migration = Callable[[sa.Connection], None]
MIGRATIONS: dict[int, Migration] = {1: _v1, 2: _v2, 3: _v3}
SCHEMA_VERSION = max(MIGRATIONS)


def create_engine(path: str | os.PathLike[str], *, synchronous: str = "FULL") -> sa.Engine:
    """An ``Engine`` on the SQLite file with the application's pragmas and ``BEGIN IMMEDIATE`` transactions."""
    # hide_parameters: SQLAlchemy's documented flag that keeps the statement's parameters out of its messages.
    engine = sa.create_engine(f"sqlite:///{os.fspath(path)}", hide_parameters=True)

    @sa.event.listens_for(engine, "connect")
    def _connect(dbapi_connection: sqlite3.Connection, _record: Any) -> None:
        dbapi_connection.isolation_level = None  # pysqlite's own BEGIN off: SQLAlchemy's "begin" event below
        for pragma in (
            "journal_mode = WAL",
            f"synchronous = {synchronous}",
            "foreign_keys = ON",
            "busy_timeout = 5000",
        ):
            dbapi_connection.execute(f"PRAGMA {pragma}")

    @sa.event.listens_for(engine, "begin")
    def _begin(connection: sa.Connection) -> None:
        immediate = connection.get_execution_options().get("immediate", False)
        connection.exec_driver_sql("BEGIN IMMEDIATE" if immediate else "BEGIN")

    return engine


@contextmanager
def write_transaction(engine: sa.Engine) -> Iterator[sa.Connection]:
    """A ``BEGIN IMMEDIATE`` transaction: committed at the end, rolled back on an error."""
    with engine.connect() as connection:
        connection = connection.execution_options(immediate=True)
        with connection.begin():
            yield connection


def user_version(engine: sa.Engine) -> int:
    with engine.connect() as connection:
        return int(connection.exec_driver_sql("PRAGMA user_version").scalar_one())


def _holds_data(engine: sa.Engine) -> bool:
    with engine.connect() as connection:
        return int(connection.exec_driver_sql("SELECT count(*) FROM sqlite_master").scalar_one()) > 0


def migrate(engine: sa.Engine, *, target: int = SCHEMA_VERSION, backup: Callable[[int, int], None] | None) -> None:
    """Move ``user_version`` to ``target``, one step per transaction; a database with data needs ``backup``."""
    current = user_version(engine)
    if current >= target:
        return
    if _holds_data(engine):
        if backup is None:
            raise BackupRequired("a backup is required before migrating a database that holds data")
        try:
            backup(current, target)
        except Exception as exc:
            raise BackupFailed("the backup before the migration failed") from exc
    for version in range(current + 1, target + 1):
        with write_transaction(engine) as connection:
            MIGRATIONS[version](connection)
            connection.exec_driver_sql(f"PRAGMA user_version = {version}")


def open_database(
    path: str | os.PathLike[str],
    *,
    backup_dir: str | os.PathLike[str],
    target: int = SCHEMA_VERSION,
    synchronous: str = "FULL",
) -> sa.Engine:
    """Open (or create) the database and migrate it to ``target``, with a backup first for an existing database.

    The backup is ``<backup_dir>/measurements-v<from>-to-v<to>-<UTC time>.db``.
    """
    engine = create_engine(path, synchronous=synchronous)
    directory = Path(backup_dir)

    def backup(current: int, wanted: int) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        target_file = sqlite3.connect(directory / f"measurements-v{current}-to-v{wanted}-{stamp}.db")
        raw = engine.raw_connection()
        try:
            source: sqlite3.Connection = raw.driver_connection  # type: ignore[assignment]
            source.backup(target_file)
        finally:
            raw.close()
            target_file.close()

    try:
        migrate(engine, target=target, backup=backup)
    except BaseException:
        engine.dispose()
        raise
    return engine
