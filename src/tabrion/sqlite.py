"""SQLite foundations (layer A, standard library only).

Contract: ``docs/api/contract.md`` section 3 and the implementation notes I-21..I-31. TAB-013 implements the
connection and the profile (:class:`SQLiteProfile`, :func:`create_private_file`, :func:`connect`,
:func:`apply_profile`, :func:`check_path`, :func:`loading_extensions`); TAB-014 the transactions, :func:`migrate`,
:func:`backup`, the checks and :func:`diagnose`.

Rule T-NOTX: functions that take a connection never end a transaction they did not open themselves. Functions that
need a transaction of their own (:func:`write_transaction`, :func:`read_transaction`, :func:`migrate`) raise
``UsageError("sqlite.nested_transaction")`` when one is open; the checks and :func:`diagnose` read inside the
caller's transaction, or in a short read transaction of their own when the connection is idle.

Files: a new database file is created owner-only (``file_mode``, default ``0600``) with
``O_CREAT|O_EXCL|O_NOFOLLOW`` before SQLite opens it, and its mode is set with ``fchmod`` on the new descriptor, so
the umask never decides it; SQLite creates ``-wal``, ``-shm`` and ``-journal`` with the main file's mode. Symbolic
links are refused for the main file and those three. No error message names a path; paths are logged at DEBUG only.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
import contextlib
from contextlib import contextmanager
from dataclasses import dataclass
import enum
import errno
import logging
import math
import os
from pathlib import Path
import secrets
import sqlite3
import stat
from typing import Any, TypeAlias

from tabrion.errors import (
    BackupFailed,
    BackupRequired,
    ConfigurationError,
    Kind,
    MigrationFailed,
    MigrationMissing,
    SchemaTooNew,
    TabrionError,
    UnsafePath,
    UsageError,
    classify,
    safe_message,
)

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

_log = logging.getLogger("tabrion.sqlite")

_MEMORY = ":memory:"
_SIDE_FILES = ("-wal", "-shm", "-journal")
"""The files SQLite derives from a database path (``-journal`` in rollback mode, ``-wal``/``-shm`` in WAL mode)."""
_POSIX = os.name == "posix"
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
"""Not on Windows: there only the ``lstat`` check guards against links (TAB-T-S12, documented)."""
_O_BINARY = getattr(os, "O_BINARY", 0)
_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_O_NONBLOCK = getattr(os, "O_NONBLOCK", 0)
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_MAX_BUSY_TIMEOUT = (2**31 - 1) / 1000
"""SQLite takes the busy timeout as a C ``int`` of milliseconds."""
_PRIVATE_MODE = 0o600


class JournalMode(enum.StrEnum):
    WAL = "wal"
    DELETE = "delete"
    TRUNCATE = "truncate"


class Synchronous(enum.StrEnum):
    OFF = "off"
    NORMAL = "normal"
    FULL = "full"
    EXTRA = "extra"


_SYNCHRONOUS_NAMES = {0: "off", 1: "normal", 2: "full", 3: "extra"}


def _invalid(field: str) -> ConfigurationError:
    return ConfigurationError("sqlite.invalid_profile", details={"field": field})


@dataclass(frozen=True, slots=True)
class SQLiteProfile:
    """Connection profile for SQLite files (defaults: 02 section 4.1, TAB-ADR-0005).

    Validation (``busy_timeout >= 0``, ``file_mode & 0o077 == 0`` unless ``allow_shared_file``) raises
    ``ConfigurationError("sqlite.invalid_profile", details={"field": ...})``. A ``journal_mode`` or ``synchronous``
    given as a string is converted to the enum member; the booleans must be ``bool``; ``file_mode`` must keep the
    owner's read and write bits and have no bits above ``0o777`` (contract I-21).
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

    def __post_init__(self) -> None:
        try:
            journal_mode = JournalMode(self.journal_mode)
        except ValueError:
            journal_mode = None
        if journal_mode is None:
            raise _invalid("journal_mode")
        try:
            synchronous = Synchronous(self.synchronous)
        except ValueError:
            synchronous = None
        if synchronous is None:
            raise _invalid("synchronous")
        object.__setattr__(self, "journal_mode", journal_mode)
        object.__setattr__(self, "synchronous", synchronous)
        for name in ("foreign_keys", "secure_delete", "trusted_schema", "allow_shared_file"):
            if not isinstance(getattr(self, name), bool):
                raise _invalid(name)
        timeout = self.busy_timeout
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or not 0 <= timeout <= _MAX_BUSY_TIMEOUT
        ):
            raise _invalid("busy_timeout")
        mode = self.file_mode
        if isinstance(mode, bool) or not isinstance(mode, int) or not 0 <= mode <= 0o777:
            raise _invalid("file_mode")
        if mode & _PRIVATE_MODE != _PRIVATE_MODE:
            raise _invalid("file_mode")
        if mode & 0o077 and not self.allow_shared_file:
            raise _invalid("file_mode")

    @classmethod
    def orkexa(cls) -> SQLiteProfile:
        """WAL, FULL, busy 30 s, ``secure_delete=True`` (Orkexa ``_storage.py:144-166``)."""
        return cls(secure_delete=True)

    @classmethod
    def nodestro(cls) -> SQLiteProfile:
        """WAL, FULL, busy 30 s, ``secure_delete=False`` (Nodestro ``storage.py:280-282``)."""
        return cls(secure_delete=False)


# ---------------------------------------------------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------------------------------------------------


def _is_memory(path: str) -> bool:
    """``":memory:"`` and ``""`` (a private temporary database) have no file of their own."""
    return path in (_MEMORY, "")


def _is_link(path: str) -> bool:
    """Whether ``path`` is a symbolic link (or, on Windows, any reparse point such as a junction)."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError:
        # Unreadable metadata (a permission problem in a parent directory): sqlite3 reports it when it opens.
        return False
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & _REPARSE_POINT)


def _unsafe(code: str, path: str) -> UnsafePath:
    _log.debug("refusing the database path %s (%s)", path, code)
    return UnsafePath(code)


def check_path(path: str | os.PathLike[str]) -> None:
    """Refuse symlinks for the database file and its ``-wal``, ``-shm`` and ``-journal`` files (``UnsafePath``).

    Code ``sqlite.symlink``; the message names no path (the path is logged at DEBUG). ``":memory:"`` and ``""`` are
    accepted. On Windows a reparse point (symbolic link or junction) counts as a link.
    """
    name = os.fspath(path)
    if _is_memory(name):
        return
    for candidate in (name, *(name + suffix for suffix in _SIDE_FILES)):
        if _is_link(candidate):
            raise _unsafe("sqlite.symlink", candidate)


def _create(path: str, mode: int) -> tuple[int, int] | None:
    """Create ``path`` exclusively with exactly ``mode``; ``None`` when it exists already, else its ``(dev, ino)``.

    ``O_EXCL`` fails on any existing name, a dangling symbolic link included, so a link planted after a check is
    never followed (POSIX; on Windows a dangling link is checked with ``lstat`` first, TAB-T-S12). ``fchmod`` on
    the new descriptor makes the mode independent of the umask (POSIX).
    """
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | _O_NOFOLLOW | _O_CLOEXEC | _O_BINARY
    if not _O_NOFOLLOW and _is_link(path):
        # Windows: CREATE_NEW follows a dangling link and creates its target; only this check guards it (S12).
        return None
    try:
        descriptor = os.open(path, flags, mode)
    except FileExistsError:
        return None
    try:
        if _POSIX:
            os.fchmod(descriptor, mode)
        info = os.fstat(descriptor)
    except BaseException:
        os.close(descriptor)
        with contextlib.suppress(OSError):
            os.unlink(path)
        raise
    os.close(descriptor)
    return info.st_dev, info.st_ino


def create_private_file(path: str | os.PathLike[str]) -> None:
    """Create ``path`` with mode 0600 via ``O_CREAT|O_EXCL|O_WRONLY|O_NOFOLLOW``; an existing file is unchanged.

    ``":memory:"`` is ignored. The mode is exactly ``0600`` whatever the umask (``fchmod``). An existing name (a
    file, or a symbolic link, which is not followed) is left as it is; :func:`check_path` refuses links.
    """
    name = os.fspath(path)
    if _is_memory(name):
        return
    _create(name, _PRIVATE_MODE)


_SHARED_WRITE = 0o022
"""Group and other write bits: such a file (or one owned by another user) can be read or changed by someone else."""


def _owner_and_mode(path: str, info: os.stat_result, *, allow_shared_file: bool) -> None:
    """POSIX: refuse a file owned by another user or writable by group or others (review M-1, TM-03).

    ``allow_shared_file`` lifts both rules. A group- or world-*readable* file is accepted (existing Nodestro files
    are 0644, B-06-5; MIG-NOD-9 tightens them); ``diagnose`` reports its mode (TAB-SQ-003).
    """
    if not _POSIX or allow_shared_file:
        return
    if info.st_uid != os.geteuid():
        raise _unsafe("sqlite.unsafe_owner", path)
    if info.st_mode & _SHARED_WRITE:
        raise _unsafe("sqlite.unsafe_mode", path)


def _check_side_files(path: str, *, allow_shared_file: bool) -> None:
    """The existing ``-wal``, ``-shm`` and ``-journal`` files follow the same owner and mode rules."""
    if not _POSIX or allow_shared_file:
        return
    for candidate in (path + suffix for suffix in _SIDE_FILES):
        try:
            info = os.lstat(candidate)
        except OSError:
            continue
        _owner_and_mode(candidate, info, allow_shared_file=False)


def _identity(path: str, *, allow_shared_file: bool = True) -> tuple[int, int]:
    """``(dev, ino)`` of the existing regular file ``path``, opened without following a link (``UnsafePath``).

    Without ``allow_shared_file`` the file must belong to this user and not be group- or world-writable.
    """
    if _POSIX:
        failure: int | None = None
        try:
            descriptor = os.open(path, os.O_RDONLY | _O_NOFOLLOW | _O_NONBLOCK | _O_CLOEXEC)
        except OSError as exc:
            failure = exc.errno or -1
        else:
            try:
                info = os.fstat(descriptor)
            finally:
                os.close(descriptor)
        if failure == errno.ELOOP:
            raise _unsafe("sqlite.symlink", path)
        if failure is not None:
            # Missing or unreadable: sqlite3 gives the precise error when it opens the file.
            return 0, 0
    else:
        if _is_link(path):
            raise _unsafe("sqlite.symlink", path)
        try:
            info = os.lstat(path)
        except OSError:
            return 0, 0
    if not stat.S_ISREG(info.st_mode):
        raise _unsafe("sqlite.unsafe_path", path)
    _owner_and_mode(path, info, allow_shared_file=allow_shared_file)
    return info.st_dev, info.st_ino


def _same_file(path: str, expected: tuple[int, int], connection: sqlite3.Connection) -> bool:
    """After opening: ``path`` is still the file that was checked, and SQLite opened that file, not a link target."""
    check_path(path)
    try:
        info = os.lstat(path)
    except OSError:
        return False
    if expected[1] and (info.st_dev, info.st_ino) != expected:
        return False
    opened = _main_file(connection)
    if not opened:
        return True
    # SQLite's unix VFS resolves links in the name it opens; a link followed at open time shows up here even when
    # it was swapped back before the check above.
    return os.path.normcase(os.path.realpath(opened)) == os.path.normcase(os.path.realpath(path))


def _rows(connection: sqlite3.Connection, statement: str, limit: int | None = None) -> list[tuple[Any, ...]]:
    """Rows as plain tuples whatever the caller's ``row_factory`` (T-NOTX: Tabrion never changes it)."""
    cursor = connection.cursor()
    try:
        cursor.row_factory = None
        cursor.execute(statement)
        return cursor.fetchall() if limit is None else cursor.fetchmany(limit)
    finally:
        cursor.close()


def _scalar(connection: sqlite3.Connection, statement: str) -> Any:
    rows = _rows(connection, statement, 1)
    return rows[0][0] if rows else None


def _str(value: object) -> str:
    """Text from SQLite whatever the caller's ``text_factory`` (``bytes`` included)."""
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", "replace")
    return "" if value is None else str(value)


def _main_file(connection: sqlite3.Connection) -> str:
    """The file of the ``main`` database as SQLite reports it; ``""`` for ``:memory:`` and temporary databases."""
    for row in _rows(connection, "PRAGMA database_list"):
        if _str(row[1]) == "main":
            return _str(row[2])
    return ""


# ---------------------------------------------------------------------------------------------------------------------
# Connection and profile (TAB-013)
# ---------------------------------------------------------------------------------------------------------------------


def _open(target: str, *, uri: bool, timeout: float, check_same_thread: bool) -> sqlite3.Connection:
    """``sqlite3.connect`` in autocommit mode (a seam for the race tests, TAB-T-S07)."""
    return sqlite3.connect(target, uri=uri, timeout=timeout, isolation_level=None, check_same_thread=check_same_thread)


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

    ``read_only`` opens the URI ``file:...?mode=ro`` and sets ``PRAGMA query_only=ON``; it never creates or changes
    the database file (for a WAL database SQLite may create ``-wal``/``-shm`` with the main file's mode). After
    opening, the path must still be the file that was checked, and SQLite must have opened that file, not a link
    target (``UnsafePath``, contract I-22).
    """
    name = os.fspath(path)
    memory = _is_memory(name)
    expected = (0, 0)
    if not memory:
        check_path(name)
        if not os.path.lexists(name):
            if read_only or not create:
                raise FileNotFoundError(errno.ENOENT, "the database file does not exist")
            created = _create(name, profile.file_mode)
            shared = profile.allow_shared_file
            expected = created if created is not None else _identity(name, allow_shared_file=shared)
        else:
            expected = _identity(name, allow_shared_file=profile.allow_shared_file)
        _check_side_files(name, allow_shared_file=profile.allow_shared_file)
    if read_only and not memory:
        target, uri = Path(name).absolute().as_uri() + "?mode=ro", True
    else:
        target, uri = name, False
    connection = _open(target, uri=uri, timeout=profile.busy_timeout, check_same_thread=check_same_thread)
    try:
        # SQLite opened the database file in ``_open``: check it before the profile writes anything to it, and the
        # side files again once WAL mode has opened them.
        safe = memory or _same_file(name, expected, connection)
        if safe:
            _apply(connection, profile, file_backed=not memory, read_only=read_only)
            if not memory:
                check_path(name)
    except BaseException:
        connection.close()
        raise
    if not safe:
        connection.close()
        raise _unsafe("sqlite.unsafe_path", name)
    return connection


def _apply(connection: sqlite3.Connection, profile: SQLiteProfile, *, file_backed: bool, read_only: bool) -> None:
    # Every value is an int or an enum member validated by SQLiteProfile, never caller text.
    statements = [
        f"PRAGMA busy_timeout = {round(profile.busy_timeout * 1000)}",
        f"PRAGMA foreign_keys = {'ON' if profile.foreign_keys else 'OFF'}",
        f"PRAGMA synchronous = {Synchronous(profile.synchronous).value.upper()}",
        f"PRAGMA secure_delete = {'ON' if profile.secure_delete else 'OFF'}",
        f"PRAGMA trusted_schema = {'ON' if profile.trusted_schema else 'OFF'}",
    ]
    for statement in statements:
        connection.execute(statement)
    if read_only:
        connection.execute("PRAGMA query_only = ON")
    if file_backed:
        # Reading the mode also reads the file header, so a file that is no database fails here (CORRUPT).
        current = _str(_scalar(connection, "PRAGMA journal_mode")).lower()
        wanted = JournalMode(profile.journal_mode).value
        if current != wanted and not read_only:
            _rows(connection, f"PRAGMA journal_mode = {wanted.upper()}")


def apply_profile(
    connection: sqlite3.Connection,
    profile: SQLiteProfile = SQLiteProfile(),
    *,
    file_backed: bool | None = None,
) -> None:
    """Apply the profile's pragmas (idempotent). ``UsageError`` while a transaction is open.

    Sets ``busy_timeout``, ``foreign_keys``, ``synchronous``, ``secure_delete``, ``trusted_schema`` and, for a file
    (``file_backed=None`` asks ``PRAGMA database_list``), ``journal_mode``; the journal mode is written only when it
    differs. Code ``sqlite.transaction_open``: ``foreign_keys`` has no effect inside a transaction.
    """
    if connection.in_transaction:
        raise UsageError("sqlite.transaction_open")
    if file_backed is None:
        file_backed = bool(_main_file(connection))
    _apply(connection, profile, file_backed=file_backed, read_only=False)


@contextmanager
def loading_extensions(connection: sqlite3.Connection) -> Iterator[None]:
    """Enable extension loading inside the block only. ``UsageError("sqlite.extensions_unavailable")``.

    The ``sqlite3`` build of some Python distributions has no ``enable_load_extension``; then the block is never
    entered. Loading is disabled again on every exit, an exception included (Orkexa ``index.py:176-194``), even
    when it was enabled before the block: ``sqlite3`` has no getter for the setting, so it cannot be restored
    (review I-3, contract I-23).
    """
    enable: Callable[[bool], None] | None = getattr(connection, "enable_load_extension", None)
    if enable is None:
        raise UsageError("sqlite.extensions_unavailable")
    enable(True)
    try:
        yield
    finally:
        enable(False)


# ---------------------------------------------------------------------------------------------------------------------
# Transactions (TAB-014)
# ---------------------------------------------------------------------------------------------------------------------


def _is_autocommit(connection: sqlite3.Connection) -> bool:
    """No driver-managed transactions: ``isolation_level=None``, or Python 3.12 ``autocommit=True``."""
    autocommit = getattr(connection, "autocommit", getattr(sqlite3, "LEGACY_TRANSACTION_CONTROL", -1))
    legacy = autocommit == getattr(sqlite3, "LEGACY_TRANSACTION_CONTROL", -1)
    return not (autocommit is False or (legacy and connection.isolation_level is not None))


def _require_own_transaction(connection: sqlite3.Connection) -> None:
    """The connection must be in autocommit mode (no driver transactions) and idle (T-NOTX)."""
    if not _is_autocommit(connection):
        raise UsageError("sqlite.not_autocommit")
    if connection.in_transaction:
        raise UsageError("sqlite.nested_transaction")


def _rollback(connection: sqlite3.Connection) -> None:
    """``ROLLBACK``; an error is suppressed only when no transaction is open any more (contract 3.4)."""
    try:
        connection.execute("ROLLBACK")
    except sqlite3.Error:
        if connection.in_transaction:
            raise


def _commit(connection: sqlite3.Connection) -> None:
    """``COMMIT``; when it fails and the transaction is still open (a deferred constraint), roll it back."""
    if not connection.in_transaction:
        raise UsageError("sqlite.transaction_ended")
    try:
        connection.execute("COMMIT")
    except BaseException:
        if connection.in_transaction:
            with contextlib.suppress(sqlite3.Error):
                connection.execute("ROLLBACK")
        raise


@contextmanager
def write_transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """``BEGIN IMMEDIATE``; commit on normal exit, rollback on any ``BaseException``.

    ``UsageError("sqlite.nested_transaction")`` when a transaction is already open; ``UsageError`` when
    ``isolation_level`` is not ``None`` (``sqlite.not_autocommit``; Python 3.12 ``autocommit=True`` is accepted).
    A block that ends the transaction itself gets ``UsageError("sqlite.transaction_ended")``; a failing ``COMMIT``
    is rolled back, so the connection is idle afterwards (contract I-24).
    """
    _require_own_transaction(connection)
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
    except BaseException:
        _rollback(connection)
        raise
    _commit(connection)


def _query_only(connection: sqlite3.Connection) -> bool:
    return bool(_scalar(connection, "PRAGMA query_only"))


@contextmanager
def read_transaction(connection: sqlite3.Connection, *, enforce: bool = True) -> Iterator[sqlite3.Connection]:
    """Deferred ``BEGIN`` (consistent snapshot); with ``enforce`` also ``PRAGMA query_only`` for the block.

    ``query_only`` is set before ``BEGIN`` and afterwards restored to the value it had (``OFF`` unless the
    connection was opened ``read_only``), also after an exception (contract I-25). The transaction ends with
    ``COMMIT`` on a normal exit (a no-op for a read) and ``ROLLBACK`` on an exception. Same ``UsageError`` rules as
    :func:`write_transaction`.
    """
    _require_own_transaction(connection)
    previous = _query_only(connection) if enforce else False
    if enforce and not previous:
        connection.execute("PRAGMA query_only = ON")
    try:
        connection.execute("BEGIN")
        try:
            yield connection
        except BaseException:
            _rollback(connection)
            raise
        _commit(connection)
    finally:
        if enforce and not previous:
            connection.execute("PRAGMA query_only = OFF")


@contextmanager
def _reading(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Inside the caller's transaction as it is (T-NOTX), else a short enforced read transaction of our own.

    An idle connection with driver-managed transactions (``isolation_level=""``, the ``sqlite3`` default) cannot
    take one of ours; the checks are single read-only ``PRAGMA`` statements that the driver runs without opening a
    transaction, so they run as they are (review L-2, contract I-29).
    """
    if connection.in_transaction or not _is_autocommit(connection):
        yield connection
    else:
        with read_transaction(connection) as own:
            yield own


# ---------------------------------------------------------------------------------------------------------------------
# Migrations (TAB-014)
# ---------------------------------------------------------------------------------------------------------------------

Migration: TypeAlias = Callable[[sqlite3.Connection], None]
"""One migration step: called inside the single write transaction of ``migrate``."""


@dataclass(frozen=True, slots=True)
class MigrationResult:
    previous: int
    current: int
    backed_up: bool


def _user_version(connection: sqlite3.Connection) -> int:
    return int(_scalar(connection, "PRAGMA user_version"))


def _is_fresh(connection: sqlite3.Connection) -> bool:
    """New and empty: no table other than SQLite's own (``sqlite_%``)."""
    count = _scalar(
        connection,
        "SELECT count(*) FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\'",
    )
    return int(count) == 0


def _check_version(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2**31 - 1:
        raise UsageError("sqlite.invalid_version", details={"name": name})
    return value


def _plan(current: int, target: int, migrations: Mapping[int, Migration]) -> SchemaTooNew | MigrationMissing | None:
    """The error for migrating ``current`` to ``target``, or ``None`` when every step is there."""
    versions = {"found": current, "supported": target}
    if current > target:
        return SchemaTooNew("sqlite.schema_too_new", details=versions)
    missing = [version for version in range(current + 1, target + 1) if version not in migrations]
    if missing:
        return MigrationMissing("sqlite.migration_missing", details={**versions, "missing": missing[0]})
    return None


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

    The version is read again after ``BEGIN IMMEDIATE``: when another connection migrated in the meantime, the
    steps that are still needed run (none when it reached ``target``), so two processes migrating at once apply
    each step once (contract I-26). ``BackupFailed`` and ``MigrationFailed`` carry the exception as ``original``,
    never as ``__cause__`` or ``__context__``, and never its text.

    A step must not end the transaction: no ``commit``, no ``rollback`` and no ``executescript``, which commits
    before it runs its script. The lost transaction is detected after the step (``MigrationFailed`` with
    ``UsageError("sqlite.transaction_ended")`` as ``original``), but what the step committed stays while
    ``user_version`` is unchanged (review L-1, contract I-26). Run several statements with one ``execute`` each.
    """
    _require_own_transaction(connection)
    target = _check_version(target, "target")
    current = _user_version(connection)
    if current == target:
        return MigrationResult(current, current, False)
    planned = _plan(current, target, migrations)
    if planned is not None:
        raise planned
    versions = {"found": current, "supported": target}
    fresh = (is_fresh or _is_fresh)(connection)
    backed_up = False
    if not fresh:
        if backup is None:
            raise BackupRequired("sqlite.backup_required", details=versions)
        backup_error: Exception | None = None
        try:
            backup(current, target)
        except Exception as exc:
            backup_error = exc
        if backup_error is not None:
            raise BackupFailed("sqlite.backup_failed", details=versions, original=backup_error)
        backed_up = True

    step = current
    entered = False
    found = current
    late: SchemaTooNew | MigrationMissing | None = None
    failure: Exception | None = None
    try:
        with write_transaction(connection):
            entered = True
            found = _user_version(connection)
            if found != current:
                late = _plan(found, target, migrations)
            if late is None and found != target:
                for step in range(found + 1, target + 1):
                    migrations[step](connection)
                    if not connection.in_transaction:
                        raise UsageError("sqlite.transaction_ended")
                step = target
                connection.execute(f"PRAGMA user_version = {int(target)}")
    except Exception as exc:
        if not entered:
            raise  # BEGIN IMMEDIATE failed (busy, read-only file): the driver error passes through unchanged
        failure = exc
    if failure is not None:
        raise MigrationFailed("sqlite.migration_failed", details={**versions, "step": step}, original=failure)
    if late is not None:
        raise late
    return MigrationResult(found if found == target else current, target, backed_up)


# ---------------------------------------------------------------------------------------------------------------------
# Backup (TAB-014)
# ---------------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BackupResult:
    path: Path
    pages: int
    bytes: int
    quick_check: str
    """``"ok"`` or the first message."""


_NOT_CHECKED = "not checked"
"""``BackupResult.quick_check`` with ``verify=False`` (contract I-28)."""


def _fsync_directory(directory: str) -> None:
    """Make a rename durable (POSIX); a platform or file system that cannot ``fsync`` a directory is skipped."""
    if not _POSIX:
        return
    try:
        descriptor = os.open(directory, os.O_RDONLY | _O_CLOEXEC)
    except OSError:
        return
    try:
        with contextlib.suppress(OSError):
            os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_file(path: str, expected: tuple[int, int]) -> None:
    """``fsync`` the finished copy; it must still be the file that was created (``UnsafePath`` otherwise)."""
    descriptor = os.open(path, os.O_RDWR | _O_NOFOLLOW | _O_BINARY | _O_CLOEXEC)
    try:
        info = os.fstat(descriptor)
        if expected[1] and expected != (info.st_dev, info.st_ino):
            raise _unsafe("sqlite.unsafe_path", path)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _verify(path: str) -> str:
    """``quick_check`` of a closed backup file on its own read-only connection (``immutable``: no side files)."""
    uri = Path(path).absolute().as_uri() + "?mode=ro&immutable=1"
    check = sqlite3.connect(uri, uri=True, isolation_level=None)
    try:
        report = quick_check(check, max_errors=1)
    finally:
        check.close()
    return "ok" if report.ok else (report.messages[0] if report.messages else "not ok")


def _publish(temporary: str, target: str, *, overwrite: bool) -> None:
    """Move the finished file to ``target`` atomically; without ``overwrite`` an existing target is never replaced."""
    if overwrite:
        os.replace(temporary, target)
        return
    linked = True
    try:
        os.link(temporary, target)
    except FileExistsError:
        linked = False
        exists = True
    except OSError:
        # No hard links here (FAT, some network file systems): check, then rename.
        linked = False
        exists = os.path.lexists(target)
    else:
        exists = False
    if exists:
        raise FileExistsError(errno.EEXIST, "the backup target exists")
    if linked:
        os.unlink(temporary)
    else:
        os.replace(temporary, target)


def backup(
    connection: sqlite3.Connection,
    target: str | os.PathLike[str],
    *,
    overwrite: bool = False,
    pages_per_step: int = -1,
    verify: bool = True,
) -> BackupResult:
    """Online backup to a 0600 temporary file, ``quick_check``, ``fsync`` and atomic ``os.replace``.

    The temporary file ``<target>.<random>.tmp`` sits in the target's directory and is removed on every error.
    ``FileExistsError`` when the target exists and ``overwrite`` is false (also when it appears during the copy);
    ``UnsafePath`` for a link; ``UsageError("sqlite.transaction_open")`` while the connection has an open
    transaction (contract I-27); ``TabrionError("sqlite.backup_corrupt")`` when the copy fails ``quick_check``.
    The backup is written in rollback-journal mode (``journal_mode=DELETE``), so it is one self-contained file;
    :func:`connect` switches it back to WAL when it is opened (contract I-28).
    """
    destination = os.fspath(target)
    if _is_memory(destination):
        raise UsageError("sqlite.invalid_target")
    if isinstance(pages_per_step, bool) or not isinstance(pages_per_step, int):
        raise UsageError("sqlite.invalid_pages")
    if connection.in_transaction:
        raise UsageError("sqlite.transaction_open")
    check_path(destination)
    if os.path.lexists(destination) and not overwrite:
        raise FileExistsError(errno.EEXIST, "the backup target exists")
    directory = os.path.dirname(os.path.abspath(destination))
    temporary = f"{destination}.{secrets.token_hex(8)}.tmp"
    check_path(temporary)
    created = _create(temporary, _PRIVATE_MODE)
    if created is None:
        raise FileExistsError(errno.EEXIST, "the temporary backup file exists")
    try:
        copy = _open(temporary, uri=False, timeout=5.0, check_same_thread=True)
        try:
            # Review M-3: the name is opened again, so check that SQLite opened the file created above before a
            # single page is written (a link swapped in would receive the whole database).
            if not _same_file(temporary, created, copy):
                raise _unsafe("sqlite.unsafe_path", temporary)
            connection.backup(copy, pages=pages_per_step)
            copy.execute("PRAGMA journal_mode = DELETE").fetchall()
            pages = int(copy.execute("PRAGMA page_count").fetchone()[0])
        finally:
            copy.close()
        result = _verify(temporary) if verify else _NOT_CHECKED
        if verify and result != "ok":
            raise TabrionError("sqlite.backup_corrupt", details={"pages": pages})
        _fsync_file(temporary, created)
        size = os.lstat(temporary).st_size
        check_path(destination)
        _publish(temporary, destination, overwrite=overwrite)
    except BaseException:
        for leftover in (temporary, *(temporary + suffix for suffix in _SIDE_FILES)):
            with contextlib.suppress(OSError):
                os.unlink(leftover)
        raise
    _fsync_directory(directory)
    return BackupResult(path=Path(destination), pages=pages, bytes=size, quick_check=result)


# ---------------------------------------------------------------------------------------------------------------------
# Checks (TAB-014)
# ---------------------------------------------------------------------------------------------------------------------


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


def _limit(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 2**31 - 2:
        raise UsageError("sqlite.invalid_limit", details={"name": name})
    return value


def _check(connection: sqlite3.Connection, pragma: str, max_errors: int) -> IntegrityReport:
    limit = _limit(max_errors, "max_errors")
    corrupt: str | None = None
    try:
        with _reading(connection) as reading:
            # One row more than asked tells whether SQLite stopped early.
            rows = _rows(reading, f"PRAGMA {pragma}({limit + 1})")
    except sqlite3.DatabaseError as exc:
        if classify(exc).kind is not Kind.CORRUPT:
            raise
        corrupt = safe_message(exc)
    if corrupt is not None:
        return IntegrityReport(ok=False, messages=(corrupt,), truncated=False)
    messages = tuple(_str(row[0]) for row in rows)
    if messages == ("ok",):
        return IntegrityReport(ok=True, messages=(), truncated=False)
    return IntegrityReport(ok=False, messages=messages[:limit], truncated=len(messages) > limit)


def integrity_check(connection: sqlite3.Connection, *, max_errors: int = 100) -> IntegrityReport:
    """``PRAGMA integrity_check`` (read only).

    A file whose damage makes the check itself fail gives ``ok=False`` with the safe message of that error (contract
    I-29), not an exception. ``messages`` is empty when ``ok``.
    """
    return _check(connection, "integrity_check", max_errors)


def quick_check(connection: sqlite3.Connection, *, max_errors: int = 100) -> IntegrityReport:
    """``PRAGMA quick_check`` (read only); same rules as :func:`integrity_check`."""
    return _check(connection, "quick_check", max_errors)


def _quote(name: str) -> str:
    if "\x00" in name:
        raise UsageError("sqlite.invalid_name")
    return '"' + name.replace('"', '""') + '"'


def foreign_key_check(
    connection: sqlite3.Connection, *, table: str | None = None, limit: int = 1000
) -> list[ForeignKeyViolation]:
    """``PRAGMA foreign_key_check`` (read only; ``table`` is quoted).

    At most ``limit`` violations; a missing ``table`` passes SQLite's error through.
    """
    count = _limit(limit, "limit")
    statement = "PRAGMA foreign_key_check" if table is None else f"PRAGMA foreign_key_check({_quote(table)})"
    with _reading(connection) as reading:
        rows = _rows(reading, statement, count)
    return [
        ForeignKeyViolation(
            table=_str(row[0]), rowid=None if row[1] is None else int(row[1]), parent=_str(row[2]), fk_index=int(row[3])
        )
        for row in rows
    ]


# ---------------------------------------------------------------------------------------------------------------------
# Diagnosis (TAB-014)
# ---------------------------------------------------------------------------------------------------------------------


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


_COMPILE_OPTIONS = (
    "DEFAULT_FOREIGN_KEYS",
    "DEFAULT_WAL_SYNCHRONOUS",
    "ENABLE_DBSTAT_VTAB",
    "ENABLE_FTS3",
    "ENABLE_FTS4",
    "ENABLE_FTS5",
    "ENABLE_JSON1",
    "ENABLE_LOAD_EXTENSION",
    "ENABLE_MATH_FUNCTIONS",
    "ENABLE_RTREE",
    "OMIT_LOAD_EXTENSION",
    "SECURE_DELETE",
    "TEMP_STORE",
    "THREADSAFE",
)
"""The compile options :func:`diagnose` reports (the others say nothing about features or safety)."""


def _pragma(connection: sqlite3.Connection, name: str) -> object:
    return _scalar(connection, f"PRAGMA {name}")


def _int(value: object) -> int:
    return int(value) if isinstance(value, (int, str)) else 0


def _directory_risk(path: str) -> str | None:
    """TM-03 for the directory of ``path`` (POSIX): ``"replace"`` when others may write it and it has no sticky bit
    (any local user can rename or replace the database and its side files), ``"create"`` when the group or others
    may write it otherwise, sticky or not (they can still create the ``-journal``, ``-wal`` and ``-shm`` names that
    SQLite opens later, #4 re-review DL-1), else ``None``."""
    if not _POSIX:
        return None
    try:
        mode = os.stat(os.path.dirname(os.path.abspath(path))).st_mode
    except OSError:
        return None
    if mode & stat.S_IWOTH and not mode & stat.S_ISVTX:
        return "replace"
    if mode & (stat.S_IWOTH | stat.S_IWGRP):
        return "create"
    return None


def _warn_about_directory(path: str) -> None:
    """Review M-2, DL-1: a warning on the logger ``tabrion.sqlite`` without the path (the path at DEBUG; the frozen
    ``SQLiteDiagnosis`` has no field for it, TB-043)."""
    risk = _directory_risk(path)
    if risk is None:
        return
    _log.debug("database directory writable by others (%s): %s", risk, os.path.dirname(os.path.abspath(path)))
    if risk == "replace":
        _log.warning(
            "the database directory is writable by other users and has no sticky bit (TM-03): another user can "
            "replace the database file; use a directory only the owner can write"
        )
    else:
        _log.warning(
            "the database directory is writable by the group or other users (TM-03): other users can create files "
            "next to the database (side files); use a directory only the owner can write"
        )


def diagnose(connection: sqlite3.Connection, *, run_quick_check: bool = False) -> SQLiteDiagnosis:
    """Read-only diagnosis of a connection and its file; the basis of ``tabrion.check`` (TAB-SQ-*).

    ``path`` is the file SQLite opened (``PRAGMA database_list``). ``file_mode`` is ``None`` on Windows, where POSIX
    modes say nothing (contract I-30); ``is_symlink`` covers the file and its ``-wal``, ``-shm`` and ``-journal``
    files. Reads inside the caller's transaction, if any, and changes no setting.
    """
    name = _main_file(connection) or None
    file_mode: int | None = None
    linked = False
    wal_bytes: int | None = None
    if name is not None:
        linked = any(_is_link(c) for c in (name, *(name + suffix for suffix in _SIDE_FILES)))
        if _POSIX:
            with contextlib.suppress(OSError):
                file_mode = stat.S_IMODE(os.stat(name).st_mode)
        with contextlib.suppress(OSError):
            wal_bytes = os.stat(name + "-wal").st_size
        _warn_about_directory(name)
    options = tuple(
        option
        for option in (_str(row[0]) for row in _rows(connection, "PRAGMA compile_options"))
        if option.partition("=")[0] in _COMPILE_OPTIONS
    )
    synchronous = _int(_pragma(connection, "synchronous"))
    checked = None
    if run_quick_check:
        report = quick_check(connection, max_errors=1)
        checked = "ok" if report.ok else (report.messages[0] if report.messages else "not ok")
    return SQLiteDiagnosis(
        sqlite_version=sqlite3.sqlite_version,
        path=name,
        file_mode=file_mode,
        is_symlink=linked,
        journal_mode=_str(_pragma(connection, "journal_mode")).lower(),
        synchronous=_SYNCHRONOUS_NAMES.get(synchronous, str(synchronous)),
        foreign_keys=_int(_pragma(connection, "foreign_keys")) == 1,
        busy_timeout_ms=_int(_pragma(connection, "busy_timeout")),
        secure_delete=_int(_pragma(connection, "secure_delete")) == 1,
        trusted_schema=_int(_pragma(connection, "trusted_schema")) == 1,
        query_only=_int(_pragma(connection, "query_only")) == 1,
        user_version=_int(_pragma(connection, "user_version")),
        page_size=_int(_pragma(connection, "page_size")),
        page_count=_int(_pragma(connection, "page_count")),
        freelist_count=_int(_pragma(connection, "freelist_count")),
        wal_bytes=wal_bytes,
        compile_options=options,
        quick_check=checked,
    )
