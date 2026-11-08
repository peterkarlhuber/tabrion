"""Error classification, safe messages and the exception hierarchy (layer A, standard library only).

Contract: ``docs/api/contract.md`` section 1, ADR 0004. Driver errors are recognized by duck typing (``sqlstate``,
``diag``, ``sqlite_errorname``, the module of the exception class, SQLAlchemy ``.orig``), never by importing a
driver, so this module stays standard-library only (TAB-T-E16).

Two rules hold for everything here:

* No text of a driver error ever reaches an output. :func:`safe_message` and ``str(TabrionError)`` are built from
  :class:`Classification` fields only (kind, SQLSTATE or SQLite code, object names); ``DETAIL``, ``HINT``,
  ``CONTEXT``, statement parameters, paths and passwords never appear (finding B-06-1). The text of a driver error is
  read only to tell a few cases apart that have no code of their own (SQLite ``SQLITE_ERROR``, PostgreSQL 57014).
* :func:`classify` never raises, and ``retryable`` is never true when ``outcome_unknown`` is (R-NORETRY): a statement
  or COMMIT that may have run must not be repeated.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
import enum
import logging
import re
import sqlite3
import traceback
from types import MappingProxyType, TracebackType
from typing import Any, Literal

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


# ---------------------------------------------------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------------------------------------------------

_MAX_DEPTH = 5
"""``classify`` follows ``.orig``, ``__cause__`` and ``__context__`` this many links deep (contract section 1.3)."""

_MAX_CHAIN = 10_000
"""The redaction walks the whole chain (each exception once, so cycles end); this only bounds the work."""

_MAX_NAME = 128
"""Object names longer than this are dropped (PostgreSQL identifiers have at most 63 bytes)."""

_SQLSTATE = re.compile(r"[0-9A-Z]{5}")
_SQLITE_CODE = re.compile(r"SQLITE_[A-Z_]+")

_TRANSIENT = (True, True)
"""(retryable, connection_usable) of LOCKED, DEADLOCK and SERIALIZATION."""

# SQLSTATE -> (kind, retryable, connection_usable). CONNECTION rows are retryable only while the outcome is known;
# ``classify`` applies that rule (R-NORETRY).
_PG_STATES: dict[str, tuple[Kind, bool, bool]] = {
    "53300": (Kind.CONNECTION, True, False),  # too_many_connections (only while connecting)
    "57P01": (Kind.CONNECTION, True, False),  # admin_shutdown
    "57P02": (Kind.CONNECTION, True, False),  # crash_shutdown
    "57P03": (Kind.CONNECTION, True, False),  # cannot_connect_now
    "57P04": (Kind.CONNECTION, False, False),  # database_dropped
    "57P05": (Kind.CONNECTION, True, False),  # idle_session_timeout (PostgreSQL 14)
    "25P03": (Kind.TIMEOUT, False, False),  # idle_in_transaction_session_timeout
    "25P04": (Kind.TIMEOUT, False, False),  # transaction_timeout (PostgreSQL 17)
    "55P03": (Kind.LOCKED, *_TRANSIENT),  # lock_not_available (lock_timeout, NOWAIT)
    "40P01": (Kind.DEADLOCK, *_TRANSIENT),
    "40001": (Kind.SERIALIZATION, *_TRANSIENT),
    "23505": (Kind.UNIQUE, False, True),
    "23503": (Kind.FOREIGN_KEY, False, True),
    "23001": (Kind.FOREIGN_KEY, False, True),  # restrict_violation
    "23502": (Kind.NOT_NULL, False, True),
    "23514": (Kind.CHECK, False, True),
    "23P01": (Kind.EXCLUSION, False, True),
    "42P01": (Kind.UNDEFINED_OBJECT, False, True),  # undefined_table
    "42703": (Kind.UNDEFINED_OBJECT, False, True),  # undefined_column
    "42883": (Kind.UNDEFINED_OBJECT, False, True),  # undefined_function
    "42704": (Kind.UNDEFINED_OBJECT, False, True),  # undefined_object
    "3F000": (Kind.UNDEFINED_OBJECT, False, True),  # invalid_schema_name
    "42601": (Kind.SYNTAX, False, True),
    "42501": (Kind.PERMISSION, False, True),
    "25006": (Kind.READ_ONLY, False, True),
    "XX001": (Kind.CORRUPT, False, True),  # data_corrupted
    "XX002": (Kind.CORRUPT, False, True),  # index_corrupted
    "53100": (Kind.DISK_FULL, False, True),
    "53200": (Kind.RESOURCE, False, True),  # out_of_memory
}
# SQLSTATE class -> (kind, retryable, connection_usable), when the SQLSTATE itself is not listed above.
_PG_CLASSES: dict[str, tuple[Kind, bool, bool]] = {
    "08": (Kind.CONNECTION, True, False),
    "22": (Kind.DATA, False, True),
    "53": (Kind.RESOURCE, False, True),
    "54": (Kind.RESOURCE, False, True),
}
_ESTABLISHMENT_STATES = frozenset({"53300"})
"""Raised only while a connection is being established: nothing was sent, so the outcome is known (``retryable``
without condition, contract mapping table)."""

_QUERY_CANCELED = "57014"
_STATEMENT_TIMEOUT_TEXT = "statement timeout"
"""Part of the English server message of a statement timeout. Other ``lc_messages`` give CANCELED (R-T02)."""

# SQLite primary result codes (the part of ``sqlite_errorname`` before an extended suffix).
_SQLITE_PRIMARY: dict[str, tuple[Kind, bool, bool]] = {
    "SQLITE_BUSY": (Kind.LOCKED, *_TRANSIENT),
    "SQLITE_LOCKED": (Kind.LOCKED, *_TRANSIENT),
    "SQLITE_READONLY": (Kind.READ_ONLY, False, True),
    "SQLITE_CORRUPT": (Kind.CORRUPT, False, False),
    "SQLITE_NOTADB": (Kind.CORRUPT, False, False),
    "SQLITE_FULL": (Kind.DISK_FULL, False, True),
    "SQLITE_INTERRUPT": (Kind.CANCELED, False, True),
    "SQLITE_AUTH": (Kind.PERMISSION, False, True),
    "SQLITE_PERM": (Kind.PERMISSION, False, True),
    "SQLITE_CANTOPEN": (Kind.CONNECTION, False, False),
    "SQLITE_IOERR": (Kind.CONNECTION, False, False),
    "SQLITE_NOMEM": (Kind.RESOURCE, False, True),
    "SQLITE_TOOBIG": (Kind.DATA, False, True),
    "SQLITE_MISMATCH": (Kind.DATA, False, True),
}
_SQLITE_CONSTRAINTS: dict[str, Kind] = {
    "SQLITE_CONSTRAINT_UNIQUE": Kind.UNIQUE,
    "SQLITE_CONSTRAINT_PRIMARYKEY": Kind.UNIQUE,
    "SQLITE_CONSTRAINT_FOREIGNKEY": Kind.FOREIGN_KEY,
    "SQLITE_CONSTRAINT_NOTNULL": Kind.NOT_NULL,
    "SQLITE_CONSTRAINT_CHECK": Kind.CHECK,
    "SQLITE_CONSTRAINT_DATATYPE": Kind.DATA,
}
# Text recognition for SQLite errors without a usable code (``SQLITE_ERROR``, or errors raised by the sqlite3 module
# itself), like Orkexa's ``is_corruption``. Lower-case substrings, checked in order.
_SQLITE_TEXTS: tuple[tuple[str, Kind], ...] = (
    ("no such table", Kind.UNDEFINED_OBJECT),
    ("no such column", Kind.UNDEFINED_OBJECT),
    ("no such function", Kind.UNDEFINED_OBJECT),
    ("no such index", Kind.UNDEFINED_OBJECT),
    ("no such view", Kind.UNDEFINED_OBJECT),
    ("no such trigger", Kind.UNDEFINED_OBJECT),
    ("syntax error", Kind.SYNTAX),
    ("incomplete input", Kind.SYNTAX),
    ("database is locked", Kind.LOCKED),
    ("database table is locked", Kind.LOCKED),
    ("database disk image is malformed", Kind.CORRUPT),
    ("file is not a database", Kind.CORRUPT),
    ("readonly database", Kind.READ_ONLY),
    ("database or disk is full", Kind.DISK_FULL),
    ("interrupted", Kind.CANCELED),
    ("unable to open database", Kind.CONNECTION),
    ("disk i/o error", Kind.CONNECTION),
    ("unique constraint failed", Kind.UNIQUE),
    ("foreign key constraint failed", Kind.FOREIGN_KEY),
    ("not null constraint failed", Kind.NOT_NULL),
    ("check constraint failed", Kind.CHECK),
)
_SQLITE_TEXT_SCOPE: dict[str, frozenset[Kind]] = {
    "SQLITE_ERROR": frozenset({Kind.UNDEFINED_OBJECT, Kind.SYNTAX}),
    "SQLITE_CONSTRAINT": frozenset({Kind.UNIQUE, Kind.FOREIGN_KEY, Kind.NOT_NULL, Kind.CHECK}),
}
"""Which kinds the text may give for a code that has no finer one; without a code, every kind of the list."""
_SQLITE_TEXT_FLAGS: dict[Kind, tuple[bool, bool]] = {
    Kind.LOCKED: _TRANSIENT,
    Kind.CORRUPT: (False, False),
    Kind.CONNECTION: (False, False),
}
_SQLITE_NAMES = re.compile(r"constraint failed: (.+)$")
_IDENT = r"[^\W\d][\w$]*"
_QUALIFIED = re.compile(rf"({_IDENT})\.({_IDENT})")
_PLAIN_NAME = re.compile(_IDENT)

_DRIVER_PACKAGES = frozenset({"psycopg", "psycopg2", "psycopg_pool"})
"""Packages whose exceptions are driver errors (``psycopg_pool``'s derive from ``psycopg.OperationalError``)."""


def _attr(obj: object, name: str) -> Any:
    """``getattr(obj, name, None)`` that also survives a property raising anything else."""
    try:
        return getattr(obj, name, None)
    except Exception:
        return None


def _module_root(cls: type) -> str:
    module = _attr(cls, "__module__")
    return module.partition(".")[0] if isinstance(module, str) else ""


def _mro_names(exc: BaseException) -> Iterator[tuple[str, str]]:
    """``(package, class name)`` for every class of ``exc``'s MRO."""
    for cls in type(exc).__mro__:
        name = _attr(cls, "__name__")
        yield _module_root(cls), name if isinstance(name, str) else ""


def _is_psycopg(exc: BaseException) -> bool:
    return any(package == "psycopg" for package, _ in _mro_names(exc))


def _is_driver_error(exc: BaseException) -> bool:
    if isinstance(exc, sqlite3.Error):
        return True
    return any(package in _DRIVER_PACKAGES for package, _ in _mro_names(exc))


def _is_sqlalchemy(exc: BaseException) -> bool:
    return _module_root(type(exc)) == "sqlalchemy"


def _sqlstate(exc: BaseException) -> str | None:
    """psycopg ``sqlstate``, psycopg2 ``pgcode``; only a well-formed five-character code counts."""
    for name in ("sqlstate", "pgcode"):
        value = _attr(exc, name)
        if isinstance(value, str) and _SQLSTATE.fullmatch(value):
            return value
    return None


def _sqlite_code(exc: BaseException) -> str | None:
    if not isinstance(exc, sqlite3.Error):
        return None
    value = _attr(exc, "sqlite_errorname")
    if isinstance(value, str) and _SQLITE_CODE.fullmatch(value):
        return value
    return None


def _name(value: object) -> str | None:
    """An object name from ``diag``, kept only when it is a short, printable string."""
    if isinstance(value, str) and 0 < len(value) <= _MAX_NAME and value.isprintable():
        return value
    return None


def _text(exc: BaseException) -> str:
    """``str(exc)``, for recognizing a case only; never returned or logged."""
    try:
        return str(exc)
    except Exception:
        return ""


def _links(exc: BaseException) -> Iterator[BaseException]:
    for name in ("orig", "__cause__", "__context__"):
        linked = _attr(exc, name)
        if isinstance(linked, BaseException):
            yield linked


def _classify_chain(error: BaseException) -> tuple[BaseException, list[BaseException]]:
    """The exception to classify and every exception visited (breadth first, at most ``_MAX_DEPTH`` links).

    The first exception with a SQLSTATE or a SQLite code wins; then the first driver error; then ``error`` itself.
    """
    visited: list[BaseException] = []
    seen: set[int] = set()
    level = [error]
    for _ in range(_MAX_DEPTH + 1):
        following: list[BaseException] = []
        for exc in level:
            if id(exc) in seen:
                continue
            seen.add(id(exc))
            visited.append(exc)
            following.extend(_links(exc))
        level = following
        if not level:
            break
    for exc in visited:
        if _sqlstate(exc) is not None or _sqlite_code(exc) is not None:
            return exc, visited
    for exc in visited:
        if _is_driver_error(exc):
            return exc, visited
    return error, visited


def _sqlite_names(exc: BaseException) -> tuple[str | None, str | None, str | None]:
    """``(constraint, table, column)`` from ``UNIQUE constraint failed: t.a, t.b`` (identifiers only)."""
    match = _SQLITE_NAMES.search(_text(exc).strip())
    if match is None:
        return None, None, None
    parts = [part.strip() for part in match.group(1).split(",")]
    qualified = [_QUALIFIED.fullmatch(part) for part in parts]
    if all(qualified):
        pairs = [(m.group(1), m.group(2)) for m in qualified if m is not None]
        constraint = _name(",".join(f"{t}.{c}" for t, c in pairs))
        tables = {t for t, _ in pairs}
        table = _name(pairs[0][0]) if len(tables) == 1 else None
        column = _name(pairs[0][1]) if len(pairs) == 1 else None
        return constraint, table, column
    if len(parts) == 1 and _PLAIN_NAME.fullmatch(parts[0]):
        return _name(parts[0]), None, None  # a named CHECK constraint
    return None, None, None


def _classify_sqlite(exc: BaseException) -> tuple[Kind, bool, bool, str | None, str | None, str | None, str | None]:
    code = _sqlite_code(exc)
    kind: Kind | None = None
    retryable, usable = False, True
    if code is not None:
        if code in _SQLITE_CONSTRAINTS:
            kind = _SQLITE_CONSTRAINTS[code]
        else:
            primary = next((p for p in _SQLITE_PRIMARY if code == p or code.startswith(p + "_")), None)
            if primary is not None:
                kind, retryable, usable = _SQLITE_PRIMARY[primary]
    allowed = _SQLITE_TEXT_SCOPE.get(code) if code is not None else None
    if kind is None and (code is None or allowed is not None):
        text = _text(exc).lower()
        kind = next((k for needle, k in _SQLITE_TEXTS if needle in text and (allowed is None or k in allowed)), None)
        if kind is not None:
            retryable, usable = _SQLITE_TEXT_FLAGS.get(kind, (False, True))
    if kind is None:
        kind = Kind.UNKNOWN
    constraint = table = column = None
    if kind in (Kind.UNIQUE, Kind.NOT_NULL, Kind.CHECK):
        constraint, table, column = _sqlite_names(exc)
    return kind, retryable, usable, code, constraint, table, column


def _classify_pg(exc: BaseException, sqlstate: str) -> tuple[Kind, bool, bool]:
    if sqlstate == _QUERY_CANCELED:
        diag = _attr(exc, "diag")
        primary = _attr(diag, "message_primary") if diag is not None else None
        text = primary if isinstance(primary, str) else _text(exc)
        if _STATEMENT_TIMEOUT_TEXT in text:
            return Kind.TIMEOUT, False, True
        return Kind.CANCELED, False, True
    if sqlstate in _PG_STATES:
        return _PG_STATES[sqlstate]
    if sqlstate[:2] in _PG_CLASSES:
        return _PG_CLASSES[sqlstate[:2]]
    return Kind.UNKNOWN, False, True


def _classify_without_code(exc: BaseException) -> tuple[Kind, bool, bool]:
    """psycopg errors raised by the client (no SQLSTATE) and SQLAlchemy errors without a driver error."""
    names = {name for package, name in _mro_names(exc) if package in ("psycopg", "psycopg2", "psycopg_pool")}
    if "PipelineAborted" in names:
        return Kind.UNKNOWN, False, True
    if "OperationalError" in names:
        # A lost or refused connection ("server closed the connection unexpectedly", "the connection is closed",
        # "connection failed", a pool timeout): the connection is broken.
        return Kind.CONNECTION, True, False
    if "DataError" in names:
        return Kind.DATA, False, True
    if _is_sqlalchemy(exc) and type(exc).__name__ in ("TimeoutError", "DisconnectionError"):
        return Kind.CONNECTION, True, False
    return Kind.UNKNOWN, False, True


def _driver_of(exc: BaseException) -> Literal["sqlite3", "psycopg", "other"]:
    if isinstance(exc, sqlite3.Error):
        return "sqlite3"
    if _is_psycopg(exc):
        return "psycopg"
    return "other"


_UNKNOWN = Classification(
    kind=Kind.UNKNOWN,
    retryable=False,
    outcome_unknown=False,
    connection_usable=True,
    driver="other",
    sqlstate=None,
    sqlite_code=None,
    constraint=None,
    table=None,
    column=None,
)


def classify(error: BaseException, *, during_commit: bool = False, sent: bool = True) -> Classification:
    """Classify a driver error (psycopg, sqlite3, SQLAlchemy ``DBAPIError``) without importing a driver.

    Follows ``.orig``, ``__cause__`` and ``__context__`` up to depth 5. Unknown errors give ``Kind.UNKNOWN`` and
    ``retryable=False``. Never raises (TAB-T-E01..E16).
    """
    try:
        return _classify(error, during_commit=during_commit, sent=sent)
    except Exception:
        return _UNKNOWN


def _classify(error: BaseException, *, during_commit: bool, sent: bool) -> Classification:
    if not isinstance(error, BaseException):
        return _UNKNOWN
    exc, visited = _classify_chain(error)
    sqlstate = _sqlstate(exc)
    constraint = table = column = sqlite_code = None
    if isinstance(exc, sqlite3.Error):
        kind, retryable, usable, sqlite_code, constraint, table, column = _classify_sqlite(exc)
    elif sqlstate is not None:
        kind, retryable, usable = _classify_pg(exc, sqlstate)
        diag = _attr(exc, "diag")
        if diag is not None:
            constraint = _name(_attr(diag, "constraint_name"))
            table = _name(_attr(diag, "table_name"))
            column = _name(_attr(diag, "column_name"))
    else:
        kind, retryable, usable = _classify_without_code(exc)
    # SQLAlchemy marks a connection it invalidated (``DBAPIError.connection_invalidated``).
    if any(_attr(e, "connection_invalidated") is True for e in visited):
        usable = False
        if kind is Kind.UNKNOWN:
            kind, retryable = Kind.CONNECTION, True
    outcome_unknown = kind is Kind.CONNECTION and bool(sent or during_commit) and sqlstate not in _ESTABLISHMENT_STATES
    if outcome_unknown:
        retryable = False  # R-NORETRY: never repeat what may have run
    return Classification(
        kind=kind,
        retryable=retryable,
        outcome_unknown=outcome_unknown,
        connection_usable=usable,
        driver=_driver_of(exc),
        sqlstate=sqlstate,
        sqlite_code=sqlite_code,
        constraint=constraint,
        table=table,
        column=column,
    )


# ---------------------------------------------------------------------------------------------------------------------
# Safe messages
# ---------------------------------------------------------------------------------------------------------------------

_KIND_TEXT: dict[Kind, str] = {
    Kind.CONNECTION: "connection error",
    Kind.TIMEOUT: "timeout",
    Kind.CANCELED: "statement canceled",
    Kind.LOCKED: "lock not available",
    Kind.DEADLOCK: "deadlock detected",
    Kind.SERIALIZATION: "serialization failure",
    Kind.UNIQUE: "unique violation",
    Kind.FOREIGN_KEY: "foreign key violation",
    Kind.NOT_NULL: "not null violation",
    Kind.CHECK: "check violation",
    Kind.EXCLUSION: "exclusion violation",
    Kind.DATA: "invalid data",
    Kind.UNDEFINED_OBJECT: "undefined object",
    Kind.SYNTAX: "syntax error",
    Kind.PERMISSION: "permission denied",
    Kind.READ_ONLY: "read-only violation",
    Kind.CORRUPT: "database corrupt",
    Kind.DISK_FULL: "disk full",
    Kind.RESOURCE: "insufficient resources",
    Kind.UNKNOWN: "database error",
}


def _format(c: Classification) -> str:
    parts: list[str] = []
    if c.sqlstate is not None:
        parts.append(f"SQLSTATE {c.sqlstate}")
    if c.sqlite_code is not None:
        parts.append(f"SQLite {c.sqlite_code}")
    if c.constraint is not None:
        parts.append(f"constraint {c.constraint}")
    if c.table is not None:
        parts.append(f"table {c.table}")
    if c.column is not None:
        parts.append(f"column {c.column}")
    if c.outcome_unknown:
        parts.append("outcome unknown")
    text = _KIND_TEXT.get(c.kind, _KIND_TEXT[Kind.UNKNOWN])
    return f"{text} ({', '.join(parts)})" if parts else text


def safe_message(error: BaseException | Classification) -> str:
    """An English message built only from classification fields, e.g. ``"... (SQLSTATE 23505, constraint x)"``.

    Never contains ``DETAIL``, ``HINT``, ``CONTEXT``, parameters, paths or passwords.
    """
    try:
        classification = error if isinstance(error, Classification) else classify(error)
        return _format(classification)
    except Exception:
        return _KIND_TEXT[Kind.UNKNOWN]


# ---------------------------------------------------------------------------------------------------------------------
# Log redaction
# ---------------------------------------------------------------------------------------------------------------------

#: The lines psycopg appends to a server message (``DETAIL:  Key (email)=(...) already exists.``, ``HINT:``,
#: ``CONTEXT:``, ``QUERY:``/``LINE n:`` with statement text) and SQLAlchemy's ``[parameters: ...]`` line: removed
#: from every text the filter writes, wherever they come from (a message formatted before logging, a wrapper's text).
_SERVER_TEXT_LINE = re.compile(r"(?m)^[ \t]*(?:DETAIL|HINT|CONTEXT|QUERY|LINE \d+):.*(?:\r?\n|$)")
_SERVER_TEXT_INLINE = re.compile(r"[ \t]*\b(?:DETAIL|HINT|CONTEXT): {2}.*")
_PARAMETERS_LINE = re.compile(r"(?m)^[ \t]*\[parameters: .*(?:\r?\n|$)")
_PARAMETERS_INLINE = re.compile(r"\[parameters: .*")
_SUSPICIOUS = ("DETAIL:", "HINT:", "CONTEXT:", "[parameters: ")
_REDACTED = "[redacted]"


def _strip_server_text(text: str) -> str:
    if not any(marker in text for marker in _SUSPICIOUS):
        return text
    text = _PARAMETERS_LINE.sub("", _SERVER_TEXT_LINE.sub("", text))
    return _PARAMETERS_INLINE.sub("", _SERVER_TEXT_INLINE.sub("", text)).rstrip("\n")


def _may_hold_server_text(value: object) -> bool:
    return isinstance(value, str) and any(marker in value for marker in _SUSPICIOUS)


def _is_db_error(exc: BaseException) -> bool:
    """A driver error, a wrapper that keeps one in ``orig``, or a SQLAlchemy statement error (``[parameters]``)."""
    if _is_driver_error(exc):
        return True
    orig = _attr(exc, "orig")
    if isinstance(orig, BaseException) and _is_driver_error(orig):
        return True
    return _is_sqlalchemy(exc) and hasattr(exc, "statement") and hasattr(exc, "params")


def _chain(exc: BaseException) -> list[BaseException]:
    """``exc`` and every exception linked to it (cause, context, ``orig``, exception group members), each once."""
    out: list[BaseException] = []
    seen: set[int] = set()
    stack: list[BaseException] = [exc]
    while stack and len(out) < _MAX_CHAIN:
        current = stack.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        out.append(current)
        linked: list[object] = [_attr(current, "__cause__"), _attr(current, "__context__"), _attr(current, "orig")]
        members = _attr(current, "exceptions")
        if isinstance(members, (tuple, list)):
            linked.extend(members)
        stack.extend(e for e in reversed(linked) if isinstance(e, BaseException))
    return out


def _db_errors_in(exc: BaseException) -> list[BaseException]:
    return [e for e in _chain(exc) if _is_db_error(e)]


def _scrub(text: str, errors: list[BaseException]) -> str:
    """``text`` with each database error's text replaced by its safe message, its ``DETAIL``, ``HINT`` and
    ``CONTEXT`` values replaced by ``[redacted]``, and every server text and ``[parameters:`` line removed."""
    for err in errors:
        whole = _text(err)
        if whole and whole in text:
            text = text.replace(whole, safe_message(err))
        for candidate in (err, _attr(err, "orig")):
            diag = _attr(candidate, "diag")
            if diag is None:
                continue
            for part in ("message_detail", "message_hint", "context"):
                value = _attr(diag, part)
                if isinstance(value, str) and value and value in text:
                    text = text.replace(value, _REDACTED)
    return _strip_server_text(text)


def _redact_traceback(top: traceback.TracebackException, exc: BaseException, errors: list[BaseException]) -> None:
    """Replace the text of every exception in ``top``: a database error's by its safe message, any other one's by
    :func:`_scrub` (a wrapper that quotes the database error)."""
    seen: set[int] = set()
    stack: list[tuple[traceback.TracebackException, BaseException]] = [(top, exc)]
    while stack:
        te, e = stack.pop()
        if id(te) in seen:
            continue
        seen.add(id(te))
        # ``_str`` is the text ``format()`` prints after the type name (Python 3.11 to 3.14; a unit test fails if a
        # Python version drops it).
        if _is_db_error(e):
            te._str = safe_message(e)  # type: ignore[attr-defined]
            te.__notes__ = None
        else:
            te._str = _scrub(str(te._str), errors)  # type: ignore[attr-defined]
            notes = getattr(te, "__notes__", None)
            if isinstance(notes, list):
                te.__notes__ = [_scrub(str(n), errors) for n in notes]
        if te.__cause__ is not None and isinstance(e.__cause__, BaseException):
            stack.append((te.__cause__, e.__cause__))
        if te.__context__ is not None and isinstance(e.__context__, BaseException):
            stack.append((te.__context__, e.__context__))
        members = _attr(e, "exceptions")
        if te.exceptions and isinstance(members, (tuple, list)):
            for sub_te, sub in zip(te.exceptions, members, strict=False):
                if isinstance(sub, BaseException):
                    stack.append((sub_te, sub))


def _summary(exc: BaseException) -> str:
    lines = []
    for e in _chain(exc):
        name = f"{type(e).__module__}.{type(e).__qualname__}"
        lines.append(f"{name}: {safe_message(e)}" if _is_db_error(e) else name)
    return "\n".join(lines)


def _redacted_traceback(exc: BaseException, tb: TracebackType | None, errors: list[BaseException]) -> str:
    """The traceback of ``exc`` as ``logging.Formatter.formatException`` prints it, without database error values;
    file names and line numbers stay."""
    te = traceback.TracebackException(type(exc), exc, tb if tb is not None else exc.__traceback__, compact=True)
    if not hasattr(te, "_str"):  # pragma: no cover - a Python version without the attribute
        return _summary(exc)
    _redact_traceback(te, exc, errors)
    return _strip_server_text("".join(te.format())).rstrip("\n")


def _is_db_exc(value: object) -> bool:
    return isinstance(value, BaseException) and _is_db_error(value)


class DbErrorRedactionFilter(logging.Filter):
    """A logging filter that rewrites database exceptions in a record to their safe messages.

    It never suppresses a record (``filter`` always returns ``True``) and never changes the exception itself; it
    sets ``record.db_sqlstate`` and ``record.db_constraint``. Consumers install it; Tabrion installs it nowhere.

    What it rewrites, when a database error (psycopg, sqlite3, SQLAlchemy) is anywhere in the record:

    - ``exc_info``: ``exc_text`` becomes the formatted traceback in which every exception line carries
      :func:`safe_message` (wrappers that quote the database error are scrubbed), and ``exc_info`` is cleared, so no
      later formatter can format the original exception again (the standard formatter prints ``exc_text``);
    - arguments (positional or a mapping's values) that are database errors become their safe message;
    - the final message loses the text of the database errors involved and every ``DETAIL:``, ``HINT:``,
      ``CONTEXT:`` and ``[parameters:`` part, also when the message was formatted before it was logged.

    A record without a database error is left unchanged, except that ``DETAIL:``/``HINT:``/``CONTEXT:``/
    ``[parameters:`` parts are removed from a message that carries them.
    """

    def __init__(self, name: str = "") -> None:
        super().__init__(name)

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            self._redact(record)
        except Exception:
            self._fallback(record)
        return True

    @staticmethod
    def _fallback(record: logging.LogRecord) -> None:
        """Keep the record with a fixed message and the redacted traceback (never the original text)."""
        exc_info = record.exc_info
        exc = exc_info[1] if isinstance(exc_info, tuple) and len(exc_info) == 3 else None
        record.msg, record.args = "log message replaced by DbErrorRedactionFilter after an internal error", None
        if isinstance(exc, BaseException) and isinstance(exc_info, tuple):
            try:
                record.exc_text = _redacted_traceback(exc, exc_info[2], _db_errors_in(exc))
            except Exception:
                try:
                    record.exc_text = _summary(exc)
                except Exception:
                    record.exc_text = f"{type(exc).__module__}.{type(exc).__qualname__}"
            record.exc_info = None
        elif isinstance(record.exc_text, str):
            record.exc_text = _strip_server_text(record.exc_text)

    def _redact(self, record: logging.LogRecord) -> None:
        exc_info = record.exc_info
        exc = exc_info[1] if isinstance(exc_info, tuple) and len(exc_info) == 3 else None
        found: list[BaseException] = _db_errors_in(exc) if isinstance(exc, BaseException) else []

        def note(value: object) -> None:
            if isinstance(value, BaseException):
                found.extend(e for e in _db_errors_in(value) if all(e is not f for f in found))

        args: Any = record.args
        suspicious = _may_hold_server_text(record.msg)
        if isinstance(args, tuple):
            for a in args:
                note(a)
                suspicious = suspicious or _may_hold_server_text(a)
            if any(_is_db_exc(a) for a in args):
                record.args = tuple(safe_message(a) if _is_db_exc(a) else a for a in args)
        elif isinstance(args, Mapping):
            for v in args.values():
                note(v)
                suspicious = suspicious or _may_hold_server_text(v)
            if any(_is_db_exc(v) for v in args.values()):
                record.args = {k: safe_message(v) if _is_db_exc(v) else v for k, v in args.items()}
        note(record.msg)
        msg: object = record.msg
        if isinstance(msg, BaseException) and _is_db_error(msg):
            record.msg = safe_message(msg)

        if isinstance(exc, BaseException) and isinstance(exc_info, tuple) and found:
            record.exc_text = _redacted_traceback(exc, exc_info[2], found)
            record.exc_info = None
        elif isinstance(record.exc_text, str) and _may_hold_server_text(record.exc_text):
            record.exc_text = _strip_server_text(record.exc_text)

        if found or suspicious:
            try:
                message = record.getMessage()
            except Exception:
                # A broken call (for example too few arguments): keep the record and say what failed.
                template = _scrub(str(record.msg), found)
                summary = "; ".join(safe_message(e) for e in found)
                record.msg = f"log message could not be formatted: {template!r}" + (f" ({summary})" if summary else "")
                record.args = None
            else:
                redacted = _scrub(message, found)
                if redacted != message:
                    record.msg, record.args = redacted, None
        if found:
            first = classify(found[0])
            record.db_sqlstate = first.sqlstate
            record.db_constraint = first.constraint


# ---------------------------------------------------------------------------------------------------------------------
# Exceptions raised by Tabrion
# ---------------------------------------------------------------------------------------------------------------------

_STATEMENT_LIMIT = 200


def _rebuild(
    cls: type[TabrionError],
    code: str,
    classification: Classification | None,
    original: BaseException | None,
    statement: str | None,
    details: dict[str, str | int | None],
) -> TabrionError:
    """Unpickle a :class:`TabrionError` (``__reduce__``)."""
    return cls(code, classification=classification, original=original, statement=statement, details=details)


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
        super().__init__(code)
        self.code = code
        self.classification = classification
        self.original = original
        self.statement = statement[:_STATEMENT_LIMIT] if statement is not None else None
        self.details = MappingProxyType(dict(details) if details is not None else {})

    def __str__(self) -> str:
        text = self.code
        if self.details:
            text += " (" + ", ".join(f"{key}={value}" for key, value in self.details.items()) + ")"
        if self.classification is not None:
            text += ": " + safe_message(self.classification)
        return text

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.code!r})"

    def __reduce__(self) -> tuple[Any, ...]:
        return (
            _rebuild,
            (type(self), self.code, self.classification, self.original, self.statement, dict(self.details)),
        )


class UsageError(TabrionError):
    """Misuse: nested transaction, closed database, lock in a transaction ..."""


class ConfigurationError(TabrionError):
    """Invalid profile, TLS rule violated, environment variable missing."""


class DatabaseError(TabrionError):
    """Layer B: a translated driver error; ``kind == classification.kind``."""

    @property
    def kind(self) -> Kind:
        """``classification.kind``; ``Kind.UNKNOWN`` when the error carries no classification (F-12)."""
        return self.classification.kind if self.classification is not None else Kind.UNKNOWN


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


_ERROR_CLASS: dict[Kind, type[DatabaseError]] = {
    Kind.CONNECTION: ConnectionFailed,
    Kind.RESOURCE: ConnectionFailed,
    Kind.TIMEOUT: StatementTimeout,
    Kind.CANCELED: Canceled,
    Kind.LOCKED: TransientError,
    Kind.DEADLOCK: TransientError,
    Kind.SERIALIZATION: TransientError,
    Kind.UNIQUE: IntegrityViolation,
    Kind.FOREIGN_KEY: IntegrityViolation,
    Kind.NOT_NULL: IntegrityViolation,
    Kind.CHECK: IntegrityViolation,
    Kind.EXCLUSION: IntegrityViolation,
    Kind.PERMISSION: PermissionDenied,
    Kind.READ_ONLY: ReadOnlyViolation,
    Kind.SYNTAX: StatementError,
    Kind.UNDEFINED_OBJECT: StatementError,
    Kind.DATA: StatementError,
    Kind.CORRUPT: StorageCorrupt,
    Kind.DISK_FULL: StorageCorrupt,
    Kind.UNKNOWN: UnknownDatabaseError,
}
"""Kind -> the :class:`DatabaseError` subclass layer B raises (contract section 1.4; internal, for TAB-018)."""


def _error_class(kind: Kind) -> type[DatabaseError]:
    """The :class:`DatabaseError` subclass for ``kind`` (a new ``Kind`` member falls back to the unknown class)."""
    return _ERROR_CLASS.get(kind, UnknownDatabaseError)


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
