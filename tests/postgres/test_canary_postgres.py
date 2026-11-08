"""QA-2: the canary suite on PostgreSQL, both layers (TAB-T-110..113; plan 07 TM-02, SR-05, SR-06).

Every ``Kind`` is raised by a real server with a fresh canary in the bind parameters, so that it lands in the
server's ``DETAIL`` (``Key (v)=(<canary>)``, ``Failing row contains (...)``), in the message itself (``invalid input
syntax for type integer: "<canary>"``) or in SQLAlchemy's ``[parameters: ...]``. Kinds no portable statement
provokes (``CORRUPT``, ``DISK_FULL``, ``RESOURCE``, a server-side ``CONNECTION`` state) come from the server through
``RAISE ... USING ERRCODE``, with the canary in ``MESSAGE``, ``DETAIL`` and ``HINT``; they are still psycopg errors
of a live server. Layer A (psycopg errors that Tabrion classifies) must keep the canary out of ``safe_message``,
``classify`` and ``DbErrorRedactionFilter``; layer B (``TabrionError``) out of every rendering (``tests/canary.py``).
The ``tabrion`` loggers are captured at DEBUG for every test.
"""

# The SQL is built from names this file generates (the plugin's schema) and fixed text, never from outside input.
# ruff: noqa: S608

from __future__ import annotations

from collections.abc import Callable, Iterator
import logging
import threading
from typing import Any

import pytest

from tabrion.errors import Kind, classify
from tabrion.testing import PostgresSchema
from tests.canary import assert_clean, assert_logs_clean, assert_redacted, module_fully_selected, new_canary

from .conftest import Role, with_params

psycopg = pytest.importorskip("psycopg")
sqlalchemy = pytest.importorskip("sqlalchemy")

from tabrion import Database, Profile  # noqa: E402
from tabrion.errors import ConnectionFailed, DatabaseError  # noqa: E402
from tabrion.postgres import SessionLock, SessionSettings, diagnose, ping  # noqa: E402

pytestmark = [pytest.mark.postgres, pytest.mark.security]

COVERED_A: set[Kind] = set()
COVERED_B: set[Kind] = set()

RAISED: dict[Kind, str] = {
    Kind.CONNECTION: "57P01",
    Kind.CORRUPT: "XX001",
    Kind.DISK_FULL: "53100",
    Kind.RESOURCE: "53200",
    Kind.UNKNOWN: "P0001",
}
"""Kinds raised through ``RAISE ... USING ERRCODE`` (the SQLSTATE a real server sends for them)."""


@pytest.fixture
def canary() -> str:
    return new_canary()


@pytest.fixture(autouse=True)
def tabrion_logs(caplog: pytest.LogCaptureFixture, canary: str) -> Iterator[None]:
    caplog.set_level(logging.DEBUG, logger="tabrion")
    yield
    assert_logs_clean(caplog.records, canary)


@pytest.fixture
def s(admin_url: str, tabrion_postgres_schema: PostgresSchema, canary: str) -> str:
    """The test schema with the canary tables and a ``raise_canary(sqlstate, text)`` function."""
    name = tabrion_postgres_schema.name
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(
            f"CREATE TABLE {name}.parent (id int PRIMARY KEY);"
            f"CREATE TABLE {name}.t (id int PRIMARY KEY, v text UNIQUE, n int NOT NULL DEFAULT 1 CHECK (n > 0),"
            f" p int REFERENCES {name}.parent (id), r int4range, EXCLUDE USING gist (r WITH &&));"
            f"CREATE FUNCTION {name}.raise_canary(code text, value text) RETURNS int LANGUAGE plpgsql AS $$"
            " BEGIN RAISE EXCEPTION USING ERRCODE = code, MESSAGE = 'failed for ' || value,"
            " DETAIL = 'detail ' || value, HINT = 'hint ' || value; END $$"
        )
        conn.execute(f"INSERT INTO {name}.t (id, v, r) VALUES (1, %s, '[1,5)')", (canary,))
    return name


# --- Scenarios: (kind, sql, leaks); ``:v`` is the canary, ``{s}`` the schema -----------------------------------------

STATEMENTS: list[tuple[Kind, str, bool]] = [
    (Kind.UNIQUE, "INSERT INTO {s}.t (id, v) VALUES (2, :v)", True),  # DETAIL: Key (v)=(<canary>)
    (Kind.UNIQUE, "INSERT INTO {s}.t (id, v) VALUES (1, :v || 'x')", False),
    (Kind.FOREIGN_KEY, "INSERT INTO {s}.t (id, v, p) VALUES (3, :v || 'f', 99)", False),
    (Kind.NOT_NULL, "INSERT INTO {s}.t (id, v, n) VALUES (4, :v || 'n', NULL)", True),  # Failing row contains
    (Kind.CHECK, "INSERT INTO {s}.t (id, v, n) VALUES (5, :v || 'c', -1)", True),
    (Kind.EXCLUSION, "INSERT INTO {s}.t (id, v, r) VALUES (6, :v || 'e', '[2,3)')", False),
    (Kind.DATA, "SELECT CAST(:v AS int)", True),  # invalid input syntax for type integer: "<canary>"
    (Kind.UNDEFINED_OBJECT, "SELECT missing FROM {s}.t WHERE v = :v", False),
    (Kind.SYNTAX, "SELECT 1 FROM {s}.t WHERE v = :v AND AND", False),
    *[(kind, f"SELECT {{s}}.raise_canary('{code}', :v)", True) for kind, code in RAISED.items()],
]


def _psycopg_sql(sql: str, schema: str) -> str:
    return sql.format(s=schema).replace(":v", "%(v)s")


def _ids(case: tuple[Kind, str, bool]) -> str:
    return f"{case[0]}-{case[1][:24]}"


@pytest.fixture
def connect(admin_url: str) -> Iterator[Callable[..., Any]]:
    opened: list[Any] = []

    def factory(url: str | None = None, **kwargs: Any) -> Any:
        kwargs.setdefault("autocommit", True)
        conn = psycopg.connect(url or admin_url, connect_timeout=10, **kwargs)
        opened.append(conn)
        return conn

    yield factory
    for conn in opened:
        conn.close()


def _check_a(exc: BaseException, kind: Kind, canary: str, *, leaks: bool = False) -> None:
    assert classify(exc).kind is kind, (classify(exc), exc)
    assert_redacted(exc, canary, leaks=leaks)
    COVERED_A.add(kind)


def _check_b(exc: BaseException, kind: Kind, canary: str, **kwargs: Any) -> None:
    assert isinstance(exc, DatabaseError) and exc.kind is kind, (exc, getattr(exc, "classification", None))
    assert_clean(exc, canary, **kwargs)
    COVERED_B.add(kind)


# --- Layer A ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("case", STATEMENTS, ids=_ids)
def test_t110_layer_a_statement_errors(
    connect: Callable[..., Any], s: str, canary: str, case: tuple[Kind, str, bool]
) -> None:
    kind, sql, leaks = case
    conn = connect()
    with pytest.raises(psycopg.Error) as info:
        conn.execute(_psycopg_sql(sql, s), {"v": canary})
    _check_a(info.value, kind, canary, leaks=leaks)
    if leaks:
        # A caller's wrapper that quotes the driver error, with a note: the filter scrubs the quoted server text.
        try:
            raise RuntimeError(f"operation failed: {info.value}") from info.value
        except RuntimeError as wrapper:
            wrapper.add_note(f"cause: {info.value}")
            assert_redacted(wrapper, canary, leaks=True)


def test_t110_layer_a_timeout_canceled_locked(connect: Callable[..., Any], s: str, canary: str) -> None:
    conn = connect()
    conn.execute("SET statement_timeout = '50ms'")
    with pytest.raises(psycopg.errors.QueryCanceled) as timeout:
        conn.execute("SELECT pg_sleep(5) WHERE %(v)s <> ''", {"v": canary})
    _check_a(timeout.value, Kind.TIMEOUT, canary)
    conn.execute("RESET statement_timeout")

    timer = threading.Timer(0.2, conn.cancel_safe if hasattr(conn, "cancel_safe") else conn.cancel)
    timer.start()
    try:
        with pytest.raises(psycopg.errors.QueryCanceled) as canceled:
            conn.execute("SELECT pg_sleep(5) WHERE %(v)s <> ''", {"v": canary})
    finally:
        timer.cancel()
    _check_a(canceled.value, Kind.CANCELED, canary)

    holder = connect(autocommit=False)
    holder.execute(f"SELECT * FROM {s}.t WHERE id = 1 FOR UPDATE")
    try:
        conn.execute("SET lock_timeout = '50ms'")
        with pytest.raises(psycopg.errors.LockNotAvailable) as locked:
            conn.execute(f"UPDATE {s}.t SET v = %(v)s || 'l' WHERE id = 1", {"v": canary})
        _check_a(locked.value, Kind.LOCKED, canary)
    finally:
        holder.rollback()


def test_t110_layer_a_deadlock(connect: Callable[..., Any], s: str, canary: str) -> None:
    a, b = connect(autocommit=False), connect(autocommit=False)
    a.execute(f"INSERT INTO {s}.parent VALUES (1), (2)")
    a.commit()
    a.execute(f"UPDATE {s}.parent SET id = id WHERE id = 1")
    b.execute(f"UPDATE {s}.parent SET id = id WHERE id = 2")
    errors: list[BaseException] = []

    def second() -> None:
        try:
            b.execute(f"UPDATE {s}.parent SET id = id WHERE id = 1 AND %(v)s <> ''", {"v": canary})
        except psycopg.Error as exc:
            errors.append(exc)

    worker = threading.Thread(target=second)
    worker.start()
    try:
        a.execute(f"UPDATE {s}.parent SET id = id WHERE id = 2 AND %(v)s <> ''", {"v": canary})
    except psycopg.Error as exc:
        errors.append(exc)
    worker.join(30)
    a.rollback()
    b.rollback()
    deadlocks = [e for e in errors if classify(e).kind is Kind.DEADLOCK]
    assert len(deadlocks) == 1, errors
    _check_a(deadlocks[0], Kind.DEADLOCK, canary)


def test_t110_layer_a_serialization(connect: Callable[..., Any], s: str, canary: str) -> None:
    a, b = connect(autocommit=False), connect(autocommit=False)
    for conn in (a, b):
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        conn.execute(f"SELECT v FROM {s}.t WHERE id = 1").fetchone()
    a.execute(f"UPDATE {s}.t SET v = %(v)s || 'a' WHERE id = 1", {"v": canary})
    a.commit()
    with pytest.raises(psycopg.errors.SerializationFailure) as info:
        b.execute(f"UPDATE {s}.t SET v = %(v)s || 'b' WHERE id = 1", {"v": canary})
    b.rollback()
    _check_a(info.value, Kind.SERIALIZATION, canary)


def test_t110_layer_a_permission_and_read_only(
    connect: Callable[..., Any], make_role: Callable[..., Role], s: str, canary: str
) -> None:
    role = make_role()
    with pytest.raises(psycopg.errors.InsufficientPrivilege) as denied:
        connect(role.url).execute(f"SELECT * FROM {s}.t WHERE v = %(v)s", {"v": canary})
    _check_a(denied.value, Kind.PERMISSION, canary)
    conn = connect(autocommit=False)
    conn.execute("SET TRANSACTION READ ONLY")
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction) as read_only:
        conn.execute(f"UPDATE {s}.t SET v = %(v)s WHERE id = 1", {"v": canary})
    conn.rollback()
    _check_a(read_only.value, Kind.READ_ONLY, canary)


def test_t110_layer_a_connection_lost(connect: Callable[..., Any], admin: Any, canary: str) -> None:
    victim = connect()
    pid = victim.info.backend_pid
    admin.execute("SELECT pg_terminate_backend(%s)", (pid,))
    with pytest.raises(psycopg.OperationalError) as info:
        victim.execute("SELECT %(v)s", {"v": canary})
    _check_a(info.value, Kind.CONNECTION, canary)


def test_t110_layer_a_wrong_password(admin_url: str, make_role: Callable[..., Role], canary: str) -> None:
    """TAB-T-E12 with a canary password: libpq's message names the user, never the password."""
    role = make_role()
    with pytest.raises(psycopg.OperationalError) as info:
        psycopg.connect(with_params(role.url, password=canary), connect_timeout=10)
    _check_a(info.value, Kind.CONNECTION, canary)
    assert canary not in str(info.value)


def test_t110_layer_a_helpers(
    admin_url: str, make_role: Callable[..., Role], connect: Callable[..., Any], canary: str
) -> None:
    """``SessionLock``, ``ping``, ``diagnose`` and ``check`` with a canary password: no result, error or repr has it."""
    from tabrion.check import check

    role = make_role()
    bad = with_params(role.url, password=canary)
    lock = SessionLock(bad)
    try:
        assert canary not in repr(lock)
        with pytest.raises(psycopg.OperationalError) as info:
            lock.try_acquire(1)
        _check_a(info.value, Kind.CONNECTION, canary)
    finally:
        lock.close()
    conn = connect(role.url)
    texts = [repr(ping(conn)), repr(diagnose(conn)), repr(check(conn))]
    assert all(canary not in text for text in texts)


# --- Layer B ---------------------------------------------------------------------------------------------------------


@pytest.fixture
def db(admin_url: str) -> Iterator[Database]:
    database = Database.from_url(f"postgresql+psycopg:///?{_query(admin_url)}")
    try:
        yield database
    finally:
        database.close()


def _query(conninfo: str) -> str:
    from urllib.parse import urlencode

    return urlencode(psycopg.conninfo.conninfo_to_dict(conninfo))


@pytest.mark.parametrize("case", STATEMENTS, ids=_ids)
def test_t110_layer_b_statement_errors(db: Database, s: str, canary: str, case: tuple[Kind, str, bool]) -> None:
    kind, sql, _ = case
    with pytest.raises(DatabaseError) as info:
        db.execute(sql.format(s=s), {"v": canary})
    _check_b(info.value, kind, canary)


def test_t110_layer_b_timeout_canceled_locked(db: Database, s: str, connect: Callable[..., Any], canary: str) -> None:
    with pytest.raises(DatabaseError) as timeout:
        db.fetch_scalar("SELECT pg_sleep(5) WHERE :v <> ''", {"v": canary}, timeout=0.05)
    _check_b(timeout.value, Kind.TIMEOUT, canary)

    with pytest.raises(DatabaseError) as canceled, db.read() as tx:
        raw: Any = tx.connection.connection.dbapi_connection
        timer = threading.Timer(0.2, raw.cancel_safe if hasattr(raw, "cancel_safe") else raw.cancel)
        timer.start()
        try:
            tx.fetch_scalar("SELECT pg_sleep(5) WHERE :v <> ''", {"v": canary})
        finally:
            timer.cancel()
    _check_b(canceled.value, Kind.CANCELED, canary)

    holder = connect(autocommit=False)
    holder.execute(f"SELECT * FROM {s}.t WHERE id = 1 FOR UPDATE")
    try:
        with pytest.raises(DatabaseError) as locked, db.transaction() as tx:
            tx.execute("SET LOCAL lock_timeout = '50ms'")
            tx.execute(f"UPDATE {s}.t SET v = :v || 'l' WHERE id = 1", {"v": canary})
        _check_b(locked.value, Kind.LOCKED, canary)
    finally:
        holder.rollback()


def test_t110_layer_b_serialization_and_deadlock(admin_url: str, s: str, canary: str) -> None:
    url = f"postgresql+psycopg:///?{_query(admin_url)}"
    with Database.from_url(url) as first, Database.from_url(url) as second:
        with pytest.raises(DatabaseError) as info, first.transaction(isolation="repeatable_read") as a:
            a.fetch_scalar(f"SELECT v FROM {s}.t WHERE id = 1")
            second.execute(f"UPDATE {s}.t SET v = :v || 'b' WHERE id = 1", {"v": canary})
            a.execute(f"UPDATE {s}.t SET v = :v || 'a' WHERE id = 1", {"v": canary})
        _check_b(info.value, Kind.SERIALIZATION, canary)
        # A real deadlock needs two blocked sessions; the server's 40P01 through RAISE covers the translation.
        with pytest.raises(DatabaseError) as deadlock:
            first.execute(f"SELECT {s}.raise_canary('40P01', :v)", {"v": canary})
        _check_b(deadlock.value, Kind.DEADLOCK, canary)


def test_t110_layer_b_permission_read_only_connection(
    admin_url: str, make_role: Callable[..., Role], db: Database, s: str, canary: str
) -> None:
    role = make_role()
    with Database.from_url(f"postgresql+psycopg:///?{_query(role.url)}") as limited:
        with pytest.raises(DatabaseError) as denied:
            limited.fetch_all(f"SELECT * FROM {s}.t WHERE v = :v", {"v": canary})
        _check_b(denied.value, Kind.PERMISSION, canary)
    with pytest.raises(DatabaseError) as read_only, db.read() as tx:
        tx.execute(f"UPDATE {s}.t SET v = :v WHERE id = 1", {"v": canary})
    _check_b(read_only.value, Kind.READ_ONLY, canary)
    # A wrong (canary) password: the checkout fails with ConnectionFailed, the URL stays masked.
    wrong = f"postgresql+psycopg:///?{_query(with_params(role.url, password=canary))}"
    with Database.from_url(wrong, profile=Profile(pool_pre_ping=False)) as database:
        assert canary not in repr(database) and canary not in database.safe_url
        with pytest.raises(ConnectionFailed) as refused:
            database.fetch_scalar("SELECT 1")
    _check_b(refused.value, Kind.CONNECTION, canary)


def test_t110_layer_b_caller_connection_and_check(db: Database, s: str, canary: str) -> None:
    with pytest.raises(DatabaseError) as info, db.transaction() as tx:
        tx.connection.execute(sqlalchemy.text(f"INSERT INTO {s}.t (id, v) VALUES (2, :v)"), {"v": canary})
    assert canary in str(info.value.__context__)  # positive control: DETAIL and [parameters]
    _check_b(info.value, Kind.UNIQUE, canary, context=sqlalchemy.exc.IntegrityError)
    texts = [repr(db.check()), repr(db.health())]
    assert all(canary not in text for text in texts)


def test_t110_layer_b_session_settings_profile(admin_url: str, s: str, canary: str) -> None:
    """A profile ``search_path`` and the application name never put a value into an error."""
    profile = Profile(postgres=SessionSettings(search_path=s, statement_timeout_ms=5_000))
    url = f"postgresql+psycopg:///?{_query(admin_url)}"
    with Database.from_url(url, profile=profile) as database, pytest.raises(DatabaseError) as info:
        database.execute("INSERT INTO t (id, v) VALUES (2, :v)", {"v": canary})
    _check_b(info.value, Kind.UNIQUE, canary)


# --- Coverage ---------------------------------------------------------------------------------------------------------


def test_t110_every_kind_on_both_layers(request: pytest.FixtureRequest, tabrion_postgres_url: str) -> None:
    """Defined last, so it runs last: every kind was raised for real on layer A and on layer B (QA-2). Asks for the
    server fixture, so it skips with the other tests when no server is configured (the job "security tests")."""
    if not module_fully_selected(request):
        pytest.skip("only meaningful when every test of this file runs")
    assert set(Kind) <= COVERED_A, f"layer A never raised {sorted(set(Kind) - COVERED_A)}"
    assert set(Kind) <= COVERED_B, f"layer B never raised {sorted(set(Kind) - COVERED_B)}"
