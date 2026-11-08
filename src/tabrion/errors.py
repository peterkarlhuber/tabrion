"""Error classification, safe messages and the exception hierarchy (layer A, standard library only).

Contract: ``docs/api/contract.md`` section 1. Stub for the API freeze G-T1; the implementation follows in TAB-010.
Driver errors are recognised by duck typing (``sqlstate``, ``diag``, ``sqlite_errorname``, SQLAlchemy ``.orig``),
never by importing a driver, so this module stays standard-library only.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import enum
import logging
from typing import Literal

__all__ = [
    "BackupFailed",
    "BackupRequired",
    "Canceled",
    "Classification",
    "ConfigurationError",
    "ConnectionFailed",
    "DatabaseError",
    "DbErrorRedactionFilter",
    "IntegrityViolation",
    "Kind",
    "MigrationError",
    "MigrationFailed",
    "MigrationMissing",
    "MultipleRowsError",
    "PermissionDenied",
    "ReadOnlyViolation",
    "RoleMissing",
    "RoleUnsafe",
    "SchemaNotCurrent",
    "SchemaTooNew",
    "StatementError",
    "StatementTimeout",
    "StorageCorrupt",
    "TabrionError",
    "TransientError",
    "UnknownDatabaseError",
    "UnsafePath",
    "UsageError",
    "classify",
    "safe_message",
]


class Kind(enum.StrEnum):
    """What kind of database error happened. New members are additive (S-4): consumers handle ``UNKNOWN``."""

    CONNECTION = "connection"
    TIMEOUT = "timeout"
    CANCELED = "canceled"
    LOCKED = "locked"
    DEADLOCK = "deadlock"
    SERIALIZATION = "serialization"
    UNIQUE = "unique"
    FOREIGN_KEY = "foreign_key"
    NOT_NULL = "not_null"
    CHECK = "check"
    EXCLUSION = "exclusion"
    DATA = "data"
    UNDEFINED_OBJECT = "undefined_object"
    SYNTAX = "syntax"
    PERMISSION = "permission"
    READ_ONLY = "read_only"
    CORRUPT = "corrupt"
    DISK_FULL = "disk_full"
    RESOURCE = "resource"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Classification:
    """The value-free classification of a driver error (no ``DETAIL``, ``HINT`` or ``str(exc)``)."""

    kind: Kind
    retryable: bool
    """The whole transaction may be retried."""
    outcome_unknown: bool
    """The statement or COMMIT may have run (connection lost during execution)."""
    connection_usable: bool
    driver: Literal["sqlite3", "psycopg", "other"]
    sqlstate: str | None
    sqlite_code: str | None
    constraint: str | None
    table: str | None
    column: str | None


def classify(error: BaseException, *, during_commit: bool = False, sent: bool = True) -> Classification:
    """Classify a driver error (psycopg, sqlite3, SQLAlchemy ``DBAPIError``) without importing a driver.

    Follows ``.orig``, ``__cause__`` and ``__context__`` up to depth 5. Unknown errors give ``Kind.UNKNOWN`` and
    ``retryable=False``. Never raises (TAB-T-E01..E16).
    """
    raise NotImplementedError("tabrion.errors.classify: implemented in TAB-010")


def safe_message(error: BaseException | Classification) -> str:
    """An English message built only from classification fields, e.g. ``"... (SQLSTATE 23505, constraint x)"``.

    Never contains ``DETAIL``, ``HINT``, ``CONTEXT``, parameters, paths or passwords.
    """
    raise NotImplementedError("tabrion.errors.safe_message: implemented in TAB-010")


class DbErrorRedactionFilter(logging.Filter):
    """A logging filter that rewrites database exceptions in a record to their safe messages.

    It never suppresses a record (``filter`` always returns ``True``) and never changes the exception itself; it
    sets ``record.db_sqlstate`` and ``record.db_constraint``. Consumers install it; Tabrion installs it nowhere.
    """

    def __init__(self, name: str = "") -> None:
        raise NotImplementedError("tabrion.errors.DbErrorRedactionFilter: implemented in TAB-010")

    def filter(self, record: logging.LogRecord) -> bool:
        raise NotImplementedError("tabrion.errors.DbErrorRedactionFilter.filter: implemented in TAB-010")


class TabrionError(Exception):
    """Base class of every error Tabrion raises. ``str`` and ``repr`` never contain data values.

    ``original`` holds the driver error and is never chained as ``__cause__`` (``raise ... from None``).
    """

    code: str
    """Stable code, e.g. ``"sqlite.schema_too_new"`` (part of the stable API, S-2)."""
    classification: Classification | None
    original: BaseException | None
    statement: str | None
    """Layer B only: the statement shortened to at most 200 characters, without parameters."""
    details: Mapping[str, str | int | None]
    """Value-free details, e.g. ``{"found": 5, "supported": 4}``."""

    def __init__(
        self,
        code: str,
        /,
        *,
        classification: Classification | None = None,
        original: BaseException | None = None,
        statement: str | None = None,
        details: Mapping[str, str | int | None] | None = None,
    ) -> None:
        raise NotImplementedError("tabrion.errors.TabrionError: implemented in TAB-010")

    def __str__(self) -> str:
        raise NotImplementedError("tabrion.errors.TabrionError.__str__: implemented in TAB-010")

    def __repr__(self) -> str:
        raise NotImplementedError("tabrion.errors.TabrionError.__repr__: implemented in TAB-010")


class UsageError(TabrionError):
    """Misuse: nested transaction, closed database, lock in a transaction ..."""


class ConfigurationError(TabrionError):
    """Invalid profile, TLS rule violated, environment variable missing."""


class DatabaseError(TabrionError):
    """Layer B: a translated driver error; ``kind == classification.kind``."""

    @property
    def kind(self) -> Kind:
        raise NotImplementedError("tabrion.errors.DatabaseError.kind: implemented in TAB-010")


class ConnectionFailed(DatabaseError):
    """``Kind.CONNECTION`` and ``Kind.RESOURCE`` (SQLSTATE 53300)."""


class StatementTimeout(DatabaseError):
    """``Kind.TIMEOUT``."""


class Canceled(DatabaseError):
    """``Kind.CANCELED``."""


class TransientError(DatabaseError):
    """``Kind.LOCKED``, ``Kind.DEADLOCK``, ``Kind.SERIALIZATION``."""


class IntegrityViolation(DatabaseError):
    """``Kind.UNIQUE``, ``FOREIGN_KEY``, ``NOT_NULL``, ``CHECK``, ``EXCLUSION``."""


class PermissionDenied(DatabaseError):
    """``Kind.PERMISSION``."""


class ReadOnlyViolation(DatabaseError):
    """``Kind.READ_ONLY``."""


class StatementError(DatabaseError):
    """``Kind.SYNTAX``, ``UNDEFINED_OBJECT``, ``DATA``."""


class StorageCorrupt(DatabaseError):
    """``Kind.CORRUPT``, ``DISK_FULL``."""


class UnknownDatabaseError(DatabaseError):
    """``Kind.UNKNOWN`` and anything not mapped."""


class MultipleRowsError(TabrionError):
    """``fetch_one``/``fetch_scalar`` found more than one row."""


class MigrationError(TabrionError):
    """``tabrion.sqlite.migrate`` failed; nothing was written."""


class SchemaTooNew(MigrationError):
    """Code ``sqlite.schema_too_new``; details ``found``, ``supported``."""


class MigrationMissing(MigrationError):
    """Code ``sqlite.migration_missing``; details ``found``, ``supported``, ``missing``."""


class BackupRequired(MigrationError):
    """Code ``sqlite.backup_required``; details ``found``, ``supported``."""


class BackupFailed(MigrationError):
    """Code ``sqlite.backup_failed``; details ``found``, ``supported``; ``original`` set."""


class MigrationFailed(MigrationError):
    """Code ``sqlite.migration_failed``; details ``found``, ``supported``, ``step``; ``original`` set."""


class SchemaNotCurrent(TabrionError):
    """Layer B Alembic gate; details ``current``, ``head``."""


class RoleMissing(TabrionError):
    """Code ``postgres.role_missing``."""


class RoleUnsafe(TabrionError):
    """Code ``postgres.role_unsafe``; details ``reasons`` (comma list of superuser, bypass_rls, table_owner)."""


class UnsafePath(TabrionError):
    """Codes ``sqlite.symlink`` and ``sqlite.unsafe_path``. The message never names the path."""
