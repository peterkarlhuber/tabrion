"""SQLite foundations (layer A, standard library only).

Contract: ``docs/api/contract.md`` section 3. Stub for the API freeze G-T1; the implementation follows in TAB-013
(connection and profile) and TAB-014 (transactions, migrate, backup, checks, diagnose).
Rule T-NOTX: functions that take a connection never end a transaction they did not open themselves.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
import enum
import os
from pathlib import Path
import sqlite3
from typing import TypeAlias

from tabrion.errors import UnsafePath

__all__ = [
    "BackupResult",
    "ForeignKeyViolation",
    "IntegrityReport",
    "JournalMode",
    "Migration",
    "MigrationResult",
    "SQLiteDiagnosis",
    "SQLiteProfile",
    "Synchronous",
    "UnsafePath",
    "apply_profile",
    "backup",
    "check_path",
    "connect",
    "create_private_file",
    "diagnose",
    "foreign_key_check",
    "integrity_check",
    "loading_extensions",
    "migrate",
    "quick_check",
    "read_transaction",
    "write_transaction",
]


class JournalMode(enum.StrEnum):
    WAL = "wal"
    DELETE = "delete"
    TRUNCATE = "truncate"


class Synchronous(enum.StrEnum):
    OFF = "off"
    NORMAL = "normal"
    FULL = "full"
    EXTRA = "extra"


@dataclass(frozen=True, slots=True)
class SQLiteProfile:
    """Connection profile for SQLite files (defaults: 02 section 4.1, TAB-ADR-0005).

    Validation (``busy_timeout >= 0``, ``file_mode & 0o077 == 0`` unless ``allow_shared_file``) raises
    ``ConfigurationError`` and is part of TAB-013.
    """

    journal_mode: JournalMode = JournalMode.WAL
    synchronous: Synchronous = Synchronous.FULL
    foreign_keys: bool = True
    busy_timeout: float = 30.0
    """Seconds."""
    secure_delete: bool = True
    trusted_schema: bool = False
    file_mode: int = 0o600
    allow_shared_file: bool = False
    """Allow a ``file_mode`` readable by group or others (not recommended)."""

    @classmethod
    def orkexa(cls) -> SQLiteProfile:
        """WAL, FULL, busy 30 s, ``secure_delete=True`` (Orkexa ``_storage.py:144-166``)."""
        raise NotImplementedError("tabrion.sqlite.SQLiteProfile.orkexa: implemented in TAB-013")

    @classmethod
    def nodestro(cls) -> SQLiteProfile:
        """WAL, FULL, busy 30 s, ``secure_delete=False`` (Nodestro ``storage.py:280-282``)."""
        raise NotImplementedError("tabrion.sqlite.SQLiteProfile.nodestro: implemented in TAB-013")


def create_private_file(path: str | os.PathLike[str]) -> None:
    """Create ``path`` with mode 0600 via ``O_CREAT|O_EXCL|O_WRONLY|O_NOFOLLOW``; an existing file is unchanged.

    ``":memory:"`` is ignored.
    """
    raise NotImplementedError("tabrion.sqlite.create_private_file: implemented in TAB-013")


def connect(
    path: str | os.PathLike[str],
    *,
    profile: SQLiteProfile = SQLiteProfile(),
    read_only: bool = False,
    create: bool = True,
    check_same_thread: bool = False,
) -> sqlite3.Connection:
    """Open a SQLite database under ``profile`` (``isolation_level=None``; new files 0600; symlinks refused).

    Raises ``UnsafePath``, ``FileNotFoundError`` (``create=False`` or ``read_only`` and the file is missing) and
    passes ``sqlite3.DatabaseError`` through (classified as ``CORRUPT`` for a file that is no database).
    """
    raise NotImplementedError("tabrion.sqlite.connect: implemented in TAB-013")


def apply_profile(
    connection: sqlite3.Connection,
    profile: SQLiteProfile = SQLiteProfile(),
    *,
    file_backed: bool | None = None,
) -> None:
    """Apply the profile's pragmas (idempotent). ``UsageError`` while a transaction is open."""
    raise NotImplementedError("tabrion.sqlite.apply_profile: implemented in TAB-013")


def check_path(path: str | os.PathLike[str]) -> None:
    """Refuse symlinks for the database file and its ``-wal``, ``-shm`` and ``-journal`` files (``UnsafePath``)."""
    raise NotImplementedError("tabrion.sqlite.check_path: implemented in TAB-013")


@contextmanager
def loading_extensions(connection: sqlite3.Connection) -> Iterator[None]:
    """Enable extension loading inside the block only. ``UsageError("sqlite.extensions_unavailable")``."""
    raise NotImplementedError("tabrion.sqlite.loading_extensions: implemented in TAB-013")


@contextmanager
def write_transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """``BEGIN IMMEDIATE``; commit on normal exit, rollback on any ``BaseException``.

    ``UsageError("sqlite.nested_transaction")`` when a transaction is already open; ``UsageError`` when
    ``isolation_level`` is not ``None``.
    """
    raise NotImplementedError("tabrion.sqlite.write_transaction: implemented in TAB-014")


@contextmanager
def read_transaction(connection: sqlite3.Connection, *, enforce: bool = True) -> Iterator[sqlite3.Connection]:
    """Deferred ``BEGIN`` (consistent snapshot); with ``enforce`` also ``PRAGMA query_only`` for the block."""
    raise NotImplementedError("tabrion.sqlite.read_transaction: implemented in TAB-014")


Migration: TypeAlias = Callable[[sqlite3.Connection], None]
"""One migration step: called inside the single write transaction of ``migrate``."""


@dataclass(frozen=True, slots=True)
class MigrationResult:
    previous: int
    current: int
    backed_up: bool


def migrate(
    connection: sqlite3.Connection,
    migrations: Mapping[int, Migration],
    *,
    target: int,
    backup: Callable[[int, int], None] | None,
    is_fresh: Callable[[sqlite3.Connection], bool] | None = None,
) -> MigrationResult:
    """Migrate ``PRAGMA user_version`` to ``target`` in one write transaction (Nodestro semantics).

    Raises ``UsageError``, ``SchemaTooNew``, ``MigrationMissing``, ``BackupRequired``, ``BackupFailed`` and
    ``MigrationFailed``; on any error the database is unchanged.
    """
    raise NotImplementedError("tabrion.sqlite.migrate: implemented in TAB-014")


@dataclass(frozen=True, slots=True)
class BackupResult:
    path: Path
    pages: int
    bytes: int
    quick_check: str
    """``"ok"`` or the first message."""


def backup(
    connection: sqlite3.Connection,
    target: str | os.PathLike[str],
    *,
    overwrite: bool = False,
    pages_per_step: int = -1,
    verify: bool = True,
) -> BackupResult:
    """Online backup to a 0600 temporary file, ``quick_check``, ``fsync`` and atomic ``os.replace``."""
    raise NotImplementedError("tabrion.sqlite.backup: implemented in TAB-014")


@dataclass(frozen=True, slots=True)
class IntegrityReport:
    ok: bool
    messages: tuple[str, ...]
    """SQLite messages (page and table names, never row data)."""
    truncated: bool


@dataclass(frozen=True, slots=True)
class ForeignKeyViolation:
    table: str
    rowid: int | None
    parent: str
    fk_index: int


def integrity_check(connection: sqlite3.Connection, *, max_errors: int = 100) -> IntegrityReport:
    """``PRAGMA integrity_check`` (read only)."""
    raise NotImplementedError("tabrion.sqlite.integrity_check: implemented in TAB-014")


def quick_check(connection: sqlite3.Connection, *, max_errors: int = 100) -> IntegrityReport:
    """``PRAGMA quick_check`` (read only)."""
    raise NotImplementedError("tabrion.sqlite.quick_check: implemented in TAB-014")


def foreign_key_check(
    connection: sqlite3.Connection, *, table: str | None = None, limit: int = 1000
) -> list[ForeignKeyViolation]:
    """``PRAGMA foreign_key_check`` (read only; ``table`` is quoted)."""
    raise NotImplementedError("tabrion.sqlite.foreign_key_check: implemented in TAB-014")


@dataclass(frozen=True, slots=True)
class SQLiteDiagnosis:
    sqlite_version: str
    path: str | None
    """``None`` for ``:memory:``."""
    file_mode: int | None
    is_symlink: bool
    journal_mode: str
    synchronous: str
    foreign_keys: bool
    busy_timeout_ms: int
    secure_delete: bool
    trusted_schema: bool
    query_only: bool
    user_version: int
    page_size: int
    page_count: int
    freelist_count: int
    wal_bytes: int | None
    compile_options: tuple[str, ...]
    quick_check: str | None
    """Only with ``run_quick_check=True``."""


def diagnose(connection: sqlite3.Connection, *, run_quick_check: bool = False) -> SQLiteDiagnosis:
    """Read-only diagnosis of a connection and its file; the basis of ``tabrion.check`` (TAB-SQ-*)."""
    raise NotImplementedError("tabrion.sqlite.diagnose: implemented in TAB-014")
