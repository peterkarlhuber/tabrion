"""Read-only configuration and security checks with stable finding codes (layer A, standard library only).

Contract: ``docs/api/contract.md`` section 7 and implementation notes I-50..I-55; the codes are listed in
``docs/specification.md`` section 9 (TAB-017). The SQLite checks (TAB-SQ-*) live here, the PostgreSQL checks
(TAB-PG-*) in ``tabrion.postgres._check``; the layer-B checks (TAB-B-*) follow with layer B.

Dispatch (G-T1 condition C-1, fix B; contract section 7, ADR 0012): this module checks SQLite connections itself and
never imports ``tabrion.postgres`` or layer B, not even inside a function. The PostgreSQL checks live in
``tabrion.postgres._check`` and the layer-B checks in ``tabrion._sa._check``; ``check()`` picks the collector by the
module of the target's type and loads it by name from the fixed table ``_COLLECTORS``. The import contracts stay
unchanged, and ``tests/architecture`` checks that this module has no static import of either package and that the
table names only those two collectors.
"""

from __future__ import annotations

from dataclasses import dataclass
import enum
import importlib
import os
import sqlite3
import stat
from types import ModuleType
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    import psycopg

    from tabrion._sa.database import Database

__all__ = ["Finding", "Severity", "check"]


class Severity(enum.StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class Finding:
    code: str
    """Stable, e.g. ``TAB-SQ-001``."""
    severity: Severity
    title: str
    detail: str
    """Never contains secrets."""
    remedy: str


_COLLECTORS: dict[str, str] = {
    "psycopg": "tabrion.postgres._check",
    "tabrion._sa.database": "tabrion._sa._check",
}
"""Module of the target's type (or of a base class) -> the collector module with ``collect(target, *, expect)``.

The keys match the type's module exactly or as a package prefix (``psycopg`` covers ``psycopg.connection``)."""


def _collector(target: object) -> ModuleType | None:
    """The collector module for ``target``; ``None`` for a ``sqlite3.Connection`` (checked here).

    Raises ``TypeError`` for any other type. Only modules named in ``_COLLECTORS`` are ever imported.
    """
    if isinstance(target, sqlite3.Connection):
        return None
    for cls in type(target).__mro__:
        module = cls.__module__
        for prefix, collector in _COLLECTORS.items():
            if module == prefix or module.startswith(prefix + "."):
                return importlib.import_module(collector)
    raise TypeError(
        "tabrion.check.check expects a sqlite3.Connection, a psycopg.Connection or a tabrion.Database, "
        f"not {type(target).__name__}"
    )


_EXPECT = ("runtime", "owner", "any")

_MIN_BUSY_TIMEOUT_MS = 1_000
_MIN_SQLITE_VERSION = (3, 37, 0)
_MAX_WAL_BYTES = 64 * 1024 * 1024
_SIDE_FILES = ("-wal", "-shm", "-journal")
_SQLITE_DAMAGE = (11, 26)  # SQLITE_CORRUPT, SQLITE_NOTADB (primary codes), as tabrion.sqlite.integrity_check


def _finding(code: str, severity: Severity, title: str, detail: str, remedy: str) -> Finding:
    return Finding(code=code, severity=severity, title=title, detail=detail, remedy=remedy)


def _not_checked(code: str, what: str) -> Finding:
    """The ``info`` finding for a check that could not run (a missing privilege, an unreadable file; I-51)."""
    return _finding(
        code,
        Severity.INFO,
        "Not checked",
        f"{what} could not be read, so {code} was not checked.",
        "Run the check with a role or user that may read it.",
    )


def _safe_name(value: object) -> str:
    """A name read from the database, for a finding text: control, ANSI and bidi characters escaped (I-50)."""
    out: list[str] = []
    for char in str(value):
        if char == "\\":
            out.append("\\\\")
        elif char.isprintable():
            out.append(char)
        else:
            code = ord(char)
            out.append(f"\\x{code:02x}" if code < 0x100 else f"\\u{code:04x}" if code < 0x10000 else f"\\U{code:08x}")
    return "".join(out)


def _is_busy(exc: sqlite3.Error) -> bool:
    """``SQLITE_BUSY`` or ``SQLITE_LOCKED``: another connection holds a lock (I-52)."""
    return getattr(exc, "sqlite_errorcode", 0) & 0xFF in (5, 6)


def _capitalized(text: str) -> str:
    return text[:1].upper() + text[1:]


def _text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def _pragma(connection: sqlite3.Connection, statement: str) -> object:
    """One value from a fixed ``PRAGMA``, whatever the connection's ``row_factory`` (T-NOTX: nothing changes)."""
    cursor = connection.cursor()
    try:
        cursor.row_factory = None
        row = cursor.execute(statement).fetchone()
    finally:
        cursor.close()
    return None if row is None else row[0]


def _database_files(connection: sqlite3.Connection) -> list[tuple[str, str]]:
    """``(schema, file)`` of every database with a file (``main`` first, then attached ones; never ``temp``)."""
    cursor = connection.cursor()
    try:
        cursor.row_factory = None
        rows = cursor.execute("PRAGMA database_list").fetchall()
    finally:
        cursor.close()
    files = [(_text(row[1]), _text(row[2] or "")) for row in rows if _text(row[1]) != "temp"]
    return sorted((item for item in files if item[1]), key=lambda item: item[0] != "main")


def _sqlite_version() -> tuple[int, ...]:
    """A seam for the version test (TAB-T-K07)."""
    return sqlite3.sqlite_version_info


def _sqlite_findings(connection: sqlite3.Connection) -> list[Finding]:
    findings: list[Finding] = []
    if _pragma(connection, "PRAGMA foreign_keys") == 0:
        findings.append(_finding(
            "TAB-SQ-001", Severity.WARNING, "Foreign keys are off",
            "PRAGMA foreign_keys is OFF, so SQLite does not enforce FOREIGN KEY constraints on this connection.",
            "Open the connection with tabrion.sqlite.connect, or run PRAGMA foreign_keys = ON outside a transaction.",
        ))  # fmt: skip
    files = _database_files(connection)
    if files and files[0][0] == "main":
        try:
            journal_mode = _text(_pragma(connection, "PRAGMA journal_mode")).lower()
        except sqlite3.OperationalError as exc:
            if not _is_busy(exc):
                raise
            journal_mode = "wal"  # no TAB-SQ-002 below; the not-checked finding stands for it
            findings.append(_not_checked("TAB-SQ-002", "The journal mode (the database was busy)"))
        if journal_mode != "wal":
            findings.append(_finding(
                "TAB-SQ-002", Severity.INFO, "Journal mode is not WAL",
                f"The database file uses journal_mode {_safe_name(journal_mode.upper())}; readers block the writer.",
                "Use WAL (the default of SQLiteProfile) unless the file lives on a network drive.",
            ))  # fmt: skip
    if files:
        findings += _file_findings(files)
    busy_timeout = _pragma(connection, "PRAGMA busy_timeout")
    if isinstance(busy_timeout, int) and busy_timeout < _MIN_BUSY_TIMEOUT_MS:
        findings.append(_finding(
            "TAB-SQ-005", Severity.WARNING, "Busy timeout below one second",
            f"PRAGMA busy_timeout is {busy_timeout} ms; concurrent writers fail with 'database is locked' at once.",
            "Set a busy timeout of at least 1000 ms (SQLiteProfile uses 30 s).",
        ))  # fmt: skip
    findings += _quick_check(connection)
    version = _sqlite_version()
    if tuple(version[:3]) < _MIN_SQLITE_VERSION:
        findings.append(_finding(
            "TAB-SQ-007", Severity.WARNING, "Old SQLite version",
            f"SQLite {'.'.join(str(part) for part in version[:3])} is older than 3.37.",
            "Use a Python build with SQLite 3.37 or later.",
        ))  # fmt: skip
    if _pragma(connection, "PRAGMA trusted_schema") == 1:
        findings.append(_finding(
            "TAB-SQ-009", Severity.INFO, "Trusted schema is on",
            "PRAGMA trusted_schema is ON: views and triggers may call SQL functions with side effects.",
            "Run PRAGMA trusted_schema = OFF (the default of SQLiteProfile).",
        ))  # fmt: skip
    return findings


def _quick_check(connection: sqlite3.Connection) -> list[Finding]:
    cursor = connection.cursor()
    try:
        cursor.row_factory = None
        rows = cursor.execute("PRAGMA quick_check(1)").fetchall()
        verdict = _text(rows[0][0]) if rows else "no result"
    except sqlite3.DatabaseError as exc:
        if getattr(exc, "sqlite_errorcode", 0) & 0xFF not in _SQLITE_DAMAGE:
            # Busy, locked, interrupted, I/O: says nothing about damage (review M-1 of #13, I-52).
            return [_not_checked("TAB-SQ-006", "The integrity check (the database was busy or not readable)")]
        verdict = "failed"  # SQLITE_CORRUPT or SQLITE_NOTADB: the check itself cannot read the file
    finally:
        cursor.close()
    if verdict == "ok":
        return []
    return [_finding(
        "TAB-SQ-006", Severity.ERROR, "Integrity check failed",
        "PRAGMA quick_check did not report ok; the database file is damaged.",
        "Restore from a backup, or recover the data with the sqlite3 .recover command.",
    )]  # fmt: skip


def _file_findings(files: list[tuple[str, str]]) -> list[Finding]:
    """TAB-SQ-003, -004 and -008 for each database file and its side files; the details name no path (I-50)."""
    findings: list[Finding] = []
    readable: list[str] = []
    linked: list[str] = []
    unread: list[str] = []
    wal_bytes: int | None = None
    candidates = [(schema, suffix, path + suffix) for schema, path in files for suffix in ("", *_SIDE_FILES)]
    for schema, suffix, name in candidates:
        owner = "the database" if schema == "main" else f"the attached database {_safe_name(schema)}"
        label = (
            f"{owner} file"
            if not suffix
            else f"the {suffix} file of {owner}"
            if schema != "main"
            else f"the {suffix} file"
        )
        try:
            info = os.lstat(name)
        except FileNotFoundError:
            continue
        except OSError:
            unread.append(label)
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            linked.append(label)
            continue
        if os.name == "posix" and info.st_mode & 0o066:  # readable or writable by group or others (I-52)
            readable.append(label)
        if suffix == "-wal" and schema == "main":
            wal_bytes = info.st_size
    if unread:
        findings.append(_not_checked("TAB-SQ-003", ", ".join(unread)))
    if readable:
        findings.append(_finding(
            "TAB-SQ-003", Severity.ERROR, "Database file readable by others",
            f"Group or others may read or write {', '.join(readable)}.",
            "chmod 600 the database and its -wal, -shm and -journal files, and keep them in a private directory.",
        ))  # fmt: skip
    if linked:
        findings.append(_finding(
            "TAB-SQ-004", Severity.ERROR, "Symbolic link in the database files",
            f"{_capitalized(', '.join(linked))} is a symbolic link; writes can be redirected.",
            "Replace the link by the real file; tabrion.sqlite.connect refuses links.",
        ))  # fmt: skip
    if wal_bytes is not None and wal_bytes > _MAX_WAL_BYTES:
        findings.append(_finding(
            "TAB-SQ-008", Severity.WARNING, "Large WAL file",
            f"The -wal file has {wal_bytes // (1024 * 1024)} MiB; checkpoints do not keep up (a reader holds them).",
            "End long read transactions, or run PRAGMA wal_checkpoint(TRUNCATE).",
        ))  # fmt: skip
    return findings


def check(
    target: sqlite3.Connection | psycopg.Connection[Any] | Database,
    *,
    expect: Literal["runtime", "owner", "any"] = "runtime",
) -> list[Finding]:
    """Check a SQLite connection, a psycopg connection or a ``Database``; read only.

    Never raises for a missing privilege (an ``info`` finding "not checked" instead). ``expect="owner"``
    suppresses TAB-PG-003 and TAB-PG-009.
    """
    if expect not in _EXPECT:
        raise ValueError("expect must be 'runtime', 'owner' or 'any'")
    collector = _collector(target)
    if collector is None and isinstance(target, sqlite3.Connection):
        return _sqlite_findings(target)
    if collector is None:  # pragma: no cover - _collector returns None only for a sqlite3.Connection
        raise TypeError("unexpected target")
    findings: list[Finding] = collector.collect(target, expect=expect)
    return findings
