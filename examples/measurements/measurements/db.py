"""Database infrastructure of the measurements application: connection, profile, schema migrations, backup.

This module holds every line of database plumbing (metric MW-1 of planning document 09 section 7 counts it with
``cloc``); ``store.py`` holds the domain code. Everything goes through ``tabrion.sqlite``:

- ``connect`` creates the file owner-only (``0600``) and refuses symbolic links; the WAL side files get the same mode.
- ``migrate`` moves ``PRAGMA user_version`` to ``SCHEMA_VERSION`` in one write transaction. Backup is mandatory:
  an existing database is copied with ``tabrion.sqlite.backup`` (``0600``, verified, atomic) before any step runs;
  only a fresh, empty file skips it.
"""

from __future__ import annotations

from datetime import UTC, datetime
import os
from pathlib import Path
import sqlite3

from tabrion import sqlite as tsqlite


def _v1(connection: sqlite3.Connection) -> None:
    # seq: the local insertion order, the high-water mark of the transfer (RA-B-2). AUTOINCREMENT keeps the largest seq
    # ever used in sqlite_sequence, so a seq is never handed out twice, also after purging transferred rows (review
    # M-1 of #16); a plain INTEGER PRIMARY KEY would reuse max(rowid) + 1. The natural key is UNIQUE, so a
    # reading captured twice is stored once. ts_utc is fixed-width ISO 8601 text in UTC ("...Z"), so text order is
    # time order. value is a finite REAL (no NaN or infinity, specification section 7).
    connection.execute(
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


def _v2(connection: sqlite3.Connection) -> None:
    # Queries by time across devices ("everything since ..."); the natural key's index covers device and channel.
    connection.execute("CREATE INDEX measurement_ts ON measurement (ts_utc)")


MIGRATIONS: dict[int, tsqlite.Migration] = {1: _v1, 2: _v2}
SCHEMA_VERSION = max(MIGRATIONS)


def open_database(
    path: str | os.PathLike[str],
    *,
    backup_dir: str | os.PathLike[str],
    target: int = SCHEMA_VERSION,
    profile: tsqlite.SQLiteProfile = tsqlite.SQLiteProfile(),
) -> sqlite3.Connection:
    """Open (or create) the database and migrate it to ``target``, with a backup first for an existing database.

    The backup is ``<backup_dir>/measurements-v<from>-<UTC time>.db``. A failed backup raises
    ``tabrion.errors.BackupFailed`` and leaves the database unchanged.
    """
    directory = Path(backup_dir)
    connection = tsqlite.connect(path, profile=profile)
    try:

        def backup(current: int, wanted: int) -> None:
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
            tsqlite.backup(connection, directory / f"measurements-v{current}-to-v{wanted}-{stamp}.db")

        tsqlite.migrate(connection, MIGRATIONS, target=target, backup=backup)
    except BaseException:
        connection.close()
        raise
    return connection
