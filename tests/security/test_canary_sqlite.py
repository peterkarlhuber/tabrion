"""QA-2: the canary suite on SQLite, both layers (TAB-T-110, TAB-T-113; plan 07 TM-02, SR-05, SR-06).

Every ``Kind`` SQLite can produce is raised for real, with a fresh canary (``tabrion-canary-<uuid>``) in the bind
parameters, in literal SQL text where the error message quotes it, or in a row value. Layer A passes the ``sqlite3``
error through: what Tabrion makes of it (``safe_message``, ``classify``, ``DbErrorRedactionFilter``) must not hold
the canary. Layer B raises a ``TabrionError``: none of its renderings may hold it (``tests/canary.py``). Tabrion's own
log records (logger ``tabrion``, at DEBUG) are captured for every scenario and must not hold it either.

``DEADLOCK``, ``SERIALIZATION`` and ``EXCLUSION`` exist on PostgreSQL only (``tests/postgres/test_canary_postgres.py``);
the coverage test at the end of each file checks that every kind of its engine was raised on both layers.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
import logging
import os
from pathlib import Path
import sqlite3
import threading
from typing import Any

import pytest

from tabrion import sqlite as tsqlite
from tabrion.errors import Kind, MigrationFailed, TabrionError, UnsafePath, classify
from tests.canary import assert_clean, assert_logs_clean, assert_redacted, module_fully_selected, new_canary

pytestmark = pytest.mark.security

SQLITE_KINDS = frozenset(Kind) - {Kind.DEADLOCK, Kind.SERIALIZATION, Kind.EXCLUSION}
"""Kinds that SQLite errors map to (contract section 1.3); the other three need PostgreSQL."""
SQLITE_KINDS_A = SQLITE_KINDS - {Kind.TIMEOUT}
"""Layer A has no statement timeout: ``TIMEOUT`` is layer B's deadline (I-40); an interrupt is ``CANCELED``."""

COVERED_A: set[Kind] = set()
COVERED_B: set[Kind] = set()

SCHEMA = (
    "CREATE TABLE parent (id INTEGER PRIMARY KEY);"
    "CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT UNIQUE, n INTEGER NOT NULL DEFAULT 1 CHECK (n > 0),"
    " p INTEGER REFERENCES parent (id), blob BLOB);"
    "CREATE TABLE s (id INTEGER PRIMARY KEY, n INTEGER)"
    + (" STRICT;" if sqlite3.sqlite_version_info >= (3, 37) else ";")
)


@pytest.fixture
def canary() -> str:
    return new_canary()


@pytest.fixture
def path_canary() -> str:
    """A canary in a file or directory name. Paths are no data values: Tabrion logs them at DEBUG only (module
    docstring of ``tabrion.sqlite``), never in an error or a record at INFO or above."""
    return new_canary("path-")


@pytest.fixture(autouse=True)
def tabrion_logs(caplog: pytest.LogCaptureFixture, canary: str, path_canary: str) -> Iterator[None]:
    """TAB-T-113: Tabrion's loggers at DEBUG; no record of the test may carry the canary."""
    caplog.set_level(logging.DEBUG, logger="tabrion")
    yield
    assert_logs_clean(caplog.records, canary)
    assert_logs_clean([r for r in caplog.records if r.levelno >= logging.INFO], path_canary)


@pytest.fixture
def path(tmp_path: Path, canary: str) -> str:
    """A database with the test schema; one row holds the canary."""
    name = str(tmp_path / "canary.db")
    conn = tsqlite.connect(name)
    try:
        conn.executescript(SCHEMA)
        conn.execute("INSERT INTO t (id, v) VALUES (1, ?)", (canary,))
    finally:
        conn.close()
    return name


# ---------------------------------------------------------------------------------------------------------------------
# Scenarios: each raises the driver error of one kind; ``conn`` is a tabrion.sqlite connection to ``path``
# ---------------------------------------------------------------------------------------------------------------------

Scenario = Callable[[Any, str, str], Any]
"""``scenario(run, path, canary)``: ``run(sql, params)`` executes on the layer under test."""


def _unique(run: Any, path: str, canary: str) -> None:
    run("INSERT INTO t (id, v) VALUES (2, :v)", {"v": canary})


def _primary_key(run: Any, path: str, canary: str) -> None:
    run("INSERT INTO t (id, v) VALUES (1, :v)", {"v": canary + "x"})


def _not_null(run: Any, path: str, canary: str) -> None:
    run("INSERT INTO t (id, v, n) VALUES (3, :v, NULL)", {"v": canary + "n"})


def _check(run: Any, path: str, canary: str) -> None:
    run("INSERT INTO t (id, v, n) VALUES (4, :v, -1)", {"v": canary + "c"})


def _foreign_key(run: Any, path: str, canary: str) -> None:
    run("INSERT INTO t (id, v, p) VALUES (5, :v, 99)", {"v": canary + "f"})


def _data(run: Any, path: str, canary: str) -> None:
    if sqlite3.sqlite_version_info < (3, 37):
        pytest.skip("STRICT tables need SQLite 3.37")
    run("INSERT INTO s (id, n) VALUES (1, :v)", {"v": canary})  # STRICT: TEXT into INTEGER


def _undefined(run: Any, path: str, canary: str) -> None:
    run("SELECT missing_column FROM t WHERE v = :v", {"v": canary})


def _syntax(run: Any, path: str, canary: str) -> None:
    # The value is literal SQL text (a Studio editor statement): SQLite quotes the token near the error.
    run(f"SELECT ('{canary}' '{canary}')", None)


def _unknown(run: Any, path: str, canary: str) -> None:
    # sqlite3 refuses the parameters itself (no SQLite result code).
    run("SELECT :a, :b", {"a": canary})


SQL_SCENARIOS: dict[Kind, Scenario] = {
    Kind.UNIQUE: _unique,
    Kind.NOT_NULL: _not_null,
    Kind.CHECK: _check,
    Kind.FOREIGN_KEY: _foreign_key,
    Kind.DATA: _data,
    Kind.UNDEFINED_OBJECT: _undefined,
    Kind.SYNTAX: _syntax,
}
"""Scenarios that are one failing statement, the same on both layers."""


# ---------------------------------------------------------------------------------------------------------------------
# Layer A
# ---------------------------------------------------------------------------------------------------------------------


def _layer_a_run(conn: sqlite3.Connection) -> Callable[[str, Any], None]:
    def run(sql: str, params: Any) -> None:
        with tsqlite.write_transaction(conn):
            conn.execute(sql, params or {})

    return run


def _check_a(exc: BaseException, kind: Kind, canary: str, *, leaks: bool = False) -> None:
    assert classify(exc).kind is kind, (classify(exc), exc)
    assert_redacted(exc, canary, leaks=leaks)
    COVERED_A.add(kind)


@pytest.mark.parametrize("kind", list(SQL_SCENARIOS), ids=str)
def test_t110_layer_a_statement_errors(path: str, canary: str, kind: Kind) -> None:
    conn = tsqlite.connect(path)
    try:
        with pytest.raises(sqlite3.Error) as info:
            SQL_SCENARIOS[kind](_layer_a_run(conn), path, canary)
    finally:
        conn.close()
    _check_a(info.value, kind, canary, leaks=kind is Kind.SYNTAX)


def test_t110_layer_a_primary_key_and_unknown(path: str, canary: str) -> None:
    conn = tsqlite.connect(path)
    try:
        with pytest.raises(sqlite3.IntegrityError) as pk:
            _primary_key(_layer_a_run(conn), path, canary)
        with pytest.raises(sqlite3.ProgrammingError) as unknown:
            _unknown(_layer_a_run(conn), path, canary)
    finally:
        conn.close()
    _check_a(pk.value, Kind.UNIQUE, canary)
    _check_a(unknown.value, Kind.UNKNOWN, canary)


def test_t110_layer_a_notes(path: str, canary: str) -> None:
    """A caller's ``add_note`` with the value on a driver error: the filter drops the notes of database errors."""
    conn = tsqlite.connect(path)
    try:
        with pytest.raises(sqlite3.IntegrityError) as info:
            conn.execute("INSERT INTO t (id, v) VALUES (1, ?)", (canary,))
    finally:
        conn.close()
    exc = info.value
    exc.add_note(f"while inserting {canary}")
    _check_a(exc, Kind.UNIQUE, canary, leaks=True)  # assert_redacted also logs it wrapped in a RuntimeError


def test_t110_layer_a_locked(path: str, canary: str) -> None:
    holder = tsqlite.connect(path)
    waiter = tsqlite.connect(path, profile=tsqlite.SQLiteProfile(busy_timeout=0.0))
    try:
        holder.execute("BEGIN IMMEDIATE")
        holder.execute("UPDATE t SET v = ? WHERE id = 1", (canary + "l",))
        with pytest.raises(sqlite3.OperationalError) as info:
            _layer_a_run(waiter)("UPDATE t SET v = :v", {"v": canary})
    finally:
        holder.rollback()
        holder.close()
        waiter.close()
    _check_a(info.value, Kind.LOCKED, canary)


def test_t110_layer_a_read_only(path: str, canary: str) -> None:
    conn = tsqlite.connect(path, read_only=True)
    try:
        with pytest.raises(sqlite3.OperationalError) as info:
            conn.execute("UPDATE t SET v = :v", {"v": canary})
    finally:
        conn.close()
    _check_a(info.value, Kind.READ_ONLY, canary)


def test_t110_layer_a_permission(path: str, canary: str) -> None:
    conn = tsqlite.connect(path)
    conn.set_authorizer(lambda action, *args: sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_UPDATE else 0)
    try:
        with pytest.raises(sqlite3.DatabaseError) as info:
            conn.execute("UPDATE t SET v = :v", {"v": canary})
    finally:
        conn.set_authorizer(None)
        conn.close()
    _check_a(info.value, Kind.PERMISSION, canary)


def test_t110_layer_a_canceled(path: str, canary: str) -> None:
    conn = tsqlite.connect(path)
    conn.set_progress_handler(lambda: 1, 1)
    try:
        with pytest.raises(sqlite3.OperationalError) as info:
            conn.execute("SELECT count(*) FROM t WHERE v = :v", {"v": canary})
    finally:
        conn.set_progress_handler(None, 1)
        conn.close()
    _check_a(info.value, Kind.CANCELED, canary)


def test_t110_layer_a_disk_full(path: str, canary: str) -> None:
    conn = tsqlite.connect(path)
    try:
        conn.execute("PRAGMA max_page_count = 8")
        with pytest.raises(sqlite3.Error) as info:
            _layer_a_run(conn)("INSERT INTO t (id, v, blob) VALUES (9, :v, zeroblob(200000))", {"v": canary + "d"})
    finally:
        conn.close()
    _check_a(info.value, Kind.DISK_FULL, canary)


def test_t110_layer_a_corrupt_and_connection(tmp_path: Path, canary: str) -> None:
    # A file with the canary as content (a secret written to the wrong path): SQLite refuses it as no database.
    garbage = tmp_path / "garbage.db"
    garbage.write_bytes((canary * 200).encode())
    os.chmod(garbage, 0o600)
    with pytest.raises(sqlite3.DatabaseError) as corrupt:
        tsqlite.connect(str(garbage)).close()
    _check_a(corrupt.value, Kind.CORRUPT, canary)
    # A directory as the database (``SQLITE_CANTOPEN``), named by the canary: the path is in no message.
    folder = tmp_path / canary
    folder.mkdir(mode=0o700)
    with pytest.raises(sqlite3.Error) as cantopen:
        sqlite3.connect(str(folder)).execute("SELECT 1")
    _check_a(cantopen.value, Kind.CONNECTION, canary)


def test_t110_layer_a_resource(path: str, canary: str) -> None:
    # ``SQLITE_NOMEM`` cannot be provoked portably: a real sqlite3 error object carrying the code stands in.
    exc = sqlite3.OperationalError(f"out of memory while binding {canary}")
    exc.sqlite_errorname = "SQLITE_NOMEM"
    exc.sqlite_errorcode = 7
    _check_a(exc, Kind.RESOURCE, canary, leaks=True)


def test_t110_layer_a_unsafe_path_and_missing_file(tmp_path: Path, path_canary: str) -> None:
    """``UnsafePath`` and ``FileNotFoundError`` name no path (the canary is a directory name)."""
    canary = path_canary
    folder = tmp_path / canary
    folder.mkdir(mode=0o700)
    target = folder / "real.db"
    tsqlite.connect(str(target)).close()
    link = folder / "link.db"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links cannot be created here")
    with pytest.raises(UnsafePath) as info:
        tsqlite.connect(str(link))
    assert_clean(info.value, canary)
    with pytest.raises(FileNotFoundError) as missing:
        tsqlite.connect(str(folder / "missing.db"), create=False)
    assert canary not in str(missing.value) and canary not in repr(missing.value)


def test_t110_layer_a_migrate_and_backup(path: str, tmp_path: Path, canary: str) -> None:
    """``MigrationFailed`` and ``BackupFailed`` keep the failure as ``original`` only, never its text."""
    conn = tsqlite.connect(path)
    try:

        def step(c: sqlite3.Connection) -> None:
            c.execute("INSERT INTO t (id, v) VALUES (1, ?)", (canary,))

        with pytest.raises(MigrationFailed) as failed:
            tsqlite.migrate(conn, {1: step}, target=1, backup=lambda found, target: None)
        assert_clean(failed.value, canary)

        def broken_backup(found: int, target: int) -> None:
            raise OSError(f"cannot write {canary}")

        with pytest.raises(TabrionError) as backup_failed:
            tsqlite.migrate(conn, {1: step}, target=1, backup=broken_backup)
        assert backup_failed.value.code == "sqlite.backup_failed"
        assert_clean(backup_failed.value, canary)
        assert canary in str(backup_failed.value.original)  # kept for the application, as an attribute
    finally:
        conn.close()


def test_t110_layer_a_backup_and_diagnose(path: str, tmp_path: Path, canary: str) -> None:
    """``backup``, ``diagnose`` and the checks over canary rows: no value in a result or a record."""
    conn = tsqlite.connect(path)
    try:
        result = tsqlite.backup(conn, str(tmp_path / "copy.db"))
        assert canary not in repr(result)
        diagnosis = tsqlite.diagnose(conn, run_quick_check=True)
        assert canary not in repr(diagnosis)
        assert canary not in repr(tsqlite.integrity_check(conn)) + repr(tsqlite.foreign_key_check(conn))
    finally:
        conn.close()


# ---------------------------------------------------------------------------------------------------------------------
# Layer B
# ---------------------------------------------------------------------------------------------------------------------

sqlalchemy = pytest.importorskip("sqlalchemy")
from tabrion import Database, Profile  # noqa: E402
from tabrion.errors import DatabaseError, UsageError  # noqa: E402


@pytest.fixture
def db(path: str) -> Iterator[Database]:
    database = Database.from_url(f"sqlite:///{path}")
    try:
        yield database
    finally:
        database.close()


def _check_b(exc: BaseException, kind: Kind, canary: str, **kwargs: Any) -> None:
    assert isinstance(exc, DatabaseError) and exc.kind is kind, (exc, getattr(exc, "classification", None))
    assert_clean(exc, canary, **kwargs)
    COVERED_B.add(kind)


@pytest.mark.parametrize("kind", list(SQL_SCENARIOS), ids=str)
def test_t110_layer_b_statement_errors(db: Database, path: str, canary: str, kind: Kind) -> None:
    with pytest.raises(DatabaseError) as info:
        SQL_SCENARIOS[kind](db.execute, path, canary)
    _check_b(info.value, kind, canary)
    # The original keeps the parameters: SQLAlchemy prints them, Tabrion never does (positive control).
    original = info.value.original
    assert original is not None
    if kind is not Kind.SYNTAX:
        wrapper = sqlalchemy.exc.DBAPIError.instance("x", {"v": canary}, original, sqlite3.Error)
        assert canary in str(wrapper)


def test_t110_layer_b_every_entry_point(db: Database, path: str, canary: str) -> None:
    """``execute``/``fetch_*`` on ``Database`` and on a ``Transaction``, ``executemany``, Core expressions."""
    params = {"v": canary}
    calls: list[Callable[[], Any]] = [
        lambda: db.fetch_one("SELECT * FROM t WHERE v = :v AND nope = 1", params),
        lambda: db.fetch_all("SELECT * FROM t WHERE v = :v AND nope = 1", params),
        lambda: db.fetch_scalar("SELECT nope FROM t WHERE v = :v", params),
        lambda: db.execute("INSERT INTO t (id, v) VALUES (:id, :v)", [{"id": 7, "v": canary}, {"id": 8, "v": canary}]),
    ]

    def in_transaction() -> None:
        with db.transaction() as tx:
            tx.execute("INSERT INTO t (id, v) VALUES (2, :v)", params)

    def in_read() -> None:
        with db.read() as tx:
            tx.execute("UPDATE t SET v = :v", params)

    def core() -> None:
        table = sqlalchemy.table("t", sqlalchemy.column("id"), sqlalchemy.column("v"))
        db.execute(sqlalchemy.insert(table).values(id=1, v=canary))

    calls += [in_transaction, in_read, core]
    for call in calls:
        with pytest.raises(DatabaseError) as info:
            call()
        assert_clean(info.value, canary)
    COVERED_B.update({Kind.READ_ONLY})


def test_t110_layer_b_caller_connection(db: Database, canary: str) -> None:
    """The caller's own use of ``Transaction.connection``: the caller's error is a suppressed ``__context__``
    (contract I-39); nothing of it reaches ``str``, the traceback or a log."""
    with pytest.raises(DatabaseError) as info, db.transaction() as tx:
        tx.connection.execute(sqlalchemy.text("INSERT INTO t (id, v) VALUES (2, :v)"), {"v": canary})
    assert canary in str(info.value.__context__)  # positive control: SQLAlchemy's text has the parameters
    _check_b(info.value, Kind.UNIQUE, canary, context=sqlalchemy.exc.IntegrityError)


def test_t110_layer_b_usage_errors(db: Database, canary: str) -> None:
    with pytest.raises(UsageError) as missing_bind:
        db.execute("INSERT INTO t (id, v) VALUES (:id, :v)", {"v": canary})
    assert_clean(missing_bind.value, canary)
    with pytest.raises(TabrionError) as multiple:
        db.fetch_one("SELECT v FROM t UNION ALL SELECT :v", {"v": canary})
    assert_clean(multiple.value, canary)


def test_t110_layer_b_locked_and_timeout(path: str, canary: str) -> None:
    holder = tsqlite.connect(path)
    database = Database.from_url(f"sqlite:///{path}", profile=Profile(sqlite=tsqlite.SQLiteProfile(busy_timeout=0.05)))
    try:
        holder.execute("BEGIN IMMEDIATE")
        with pytest.raises(DatabaseError) as locked:
            database.execute("UPDATE t SET v = :v", {"v": canary})
        holder.rollback()
        _check_b(locked.value, Kind.LOCKED, canary)
        recursive = (
            "WITH RECURSIVE r(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM r) SELECT count(*) FROM r WHERE :v <> ''"
        )
        with pytest.raises(DatabaseError) as timeout:
            database.fetch_scalar(recursive, {"v": canary}, timeout=0.05)
        _check_b(timeout.value, Kind.TIMEOUT, canary)
    finally:
        holder.close()
        database.close()


def test_t110_layer_b_canceled(db: Database, canary: str) -> None:
    recursive = "WITH RECURSIVE r(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM r) SELECT count(*) FROM r WHERE :v <> ''"
    with pytest.raises(DatabaseError) as info, db.read() as tx:
        raw: Any = tx.connection.connection.dbapi_connection
        timer = threading.Timer(0.1, raw.interrupt)
        timer.start()
        try:
            tx.fetch_scalar(recursive, {"v": canary})
        finally:
            timer.cancel()
    _check_b(info.value, Kind.CANCELED, canary)


def test_t110_layer_b_permission_full_corrupt(db: Database, path: str, tmp_path: Path, canary: str) -> None:
    with db.transaction() as tx:
        raw: Any = tx.connection.connection.dbapi_connection
        raw.set_authorizer(lambda action, *args: sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_DELETE else 0)
        try:
            with pytest.raises(DatabaseError) as denied:
                tx.execute("DELETE FROM t WHERE v = :v", {"v": canary})
        finally:
            raw.set_authorizer(None)
    _check_b(denied.value, Kind.PERMISSION, canary)
    with pytest.raises(DatabaseError) as full, db.transaction() as tx:
        tx.execute("PRAGMA max_page_count = 8")
        tx.execute("INSERT INTO t (id, v, blob) VALUES (9, :v, zeroblob(200000))", {"v": canary + "d"})
    _check_b(full.value, Kind.DISK_FULL, canary)
    garbage = tmp_path / "garbage.db"
    garbage.write_bytes((canary * 200).encode())
    os.chmod(garbage, 0o600)
    with pytest.raises(DatabaseError) as corrupt:
        Database.from_url(f"sqlite:///{garbage}").close()
    _check_b(corrupt.value, Kind.CORRUPT, canary)


def test_t110_layer_b_missing_file(tmp_path: Path, path_canary: str) -> None:
    """A read-only profile on a missing file, in a directory named by the canary: no path in the error."""
    folder = tmp_path / path_canary
    folder.mkdir(mode=0o700)
    with pytest.raises(FileNotFoundError) as missing:
        Database.from_url(f"sqlite:///{folder}/missing.db", profile=Profile.readonly()).close()
    assert path_canary not in str(missing.value) and path_canary not in repr(missing.value)
    with pytest.raises(UnsafePath) as unsafe:
        Database.from_url(f"sqlite:///{folder}").close()  # a directory
    assert_clean(unsafe.value, path_canary)


def test_t110_layer_b_connection_unknown_resource(tmp_path: Path, canary: str) -> None:
    """Kinds layer B cannot reach portably on SQLite: the translation of real ``sqlite3`` error objects."""
    from tabrion._sa.database import _translate

    folder = tmp_path / canary
    folder.mkdir(mode=0o700)
    try:
        sqlite3.connect(str(folder)).execute("SELECT 1")  # SQLITE_CANTOPEN
    except sqlite3.Error as exc:
        cantopen = exc
    for kind, error in [
        (Kind.CONNECTION, cantopen),
        (Kind.UNKNOWN, sqlite3.ProgrammingError(f"unsupported value {canary}")),
        (Kind.RESOURCE, sqlite3.OperationalError(f"out of memory near {canary}")),
    ]:
        if kind is Kind.RESOURCE:
            error.sqlite_errorname = "SQLITE_NOMEM"
        wrapped = sqlalchemy.exc.DBAPIError.instance("SELECT :v", {"v": canary}, error, sqlite3.Error)
        try:
            raise _translate(wrapped, "SELECT :v") from None
        except DatabaseError as exc:
            _check_b(exc, kind, canary)


def test_t110_layer_b_unclosed_warning(path: str, canary: str) -> None:
    """A forgotten ``close`` warns with ``repr``: the masked URL, never a value."""
    database = Database.from_url(f"sqlite:///{path}")
    database.execute("UPDATE t SET v = :v WHERE id = 1", {"v": canary + "u"})
    assert canary not in repr(database)
    with pytest.warns(ResourceWarning) as warned:
        database.__del__()
    assert all(canary not in str(w.message) for w in warned)


def test_t110_every_sqlite_kind_on_both_layers(request: pytest.FixtureRequest) -> None:
    """Defined last, so it runs last: every kind SQLite has was raised on layer A and on layer B (QA-2)."""
    if not module_fully_selected(request):
        pytest.skip("only meaningful when every test of this file runs")
    assert SQLITE_KINDS_A <= COVERED_A, f"layer A never raised {sorted(SQLITE_KINDS_A - COVERED_A)}"
    assert SQLITE_KINDS <= COVERED_B, f"layer B never raised {sorted(SQLITE_KINDS - COVERED_B)}"
