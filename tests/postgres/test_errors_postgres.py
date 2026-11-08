"""TAB-010 against a real PostgreSQL (16, 17, 18 in CI): the mapping table with server errors, E12, E14, E15, E21, E23.

Needs ``TABRION_TEST_POSTGRES`` (a superuser conninfo, as in the CI jobs "pg (...)"); skips without it unless
``TABRION_REQUIRE_POSTGRES=1``. Every test works in its own schema and drops it afterwards.
"""

# The SQL is built from schema names this file generates (uuid) and fixed markers, never from outside input.
# ruff: noqa: S608

from __future__ import annotations

from collections.abc import Callable, Iterator
import contextlib
import io
import logging
import os
import socket
import threading
import time
from typing import Any
import uuid

import pytest

from tabrion.errors import DbErrorRedactionFilter, Kind, classify, safe_message

psycopg = pytest.importorskip("psycopg")

pytestmark = pytest.mark.postgres

CANARY = "SECRET-4711"


def _conninfo() -> str:
    conninfo = os.environ.get("TABRION_TEST_POSTGRES")
    if not conninfo:
        if os.environ.get("TABRION_REQUIRE_POSTGRES") == "1":
            pytest.fail("TABRION_REQUIRE_POSTGRES=1 but TABRION_TEST_POSTGRES is not set")
        pytest.skip("TABRION_TEST_POSTGRES is not set")
    return conninfo


def _with(conninfo: str, **changes: Any) -> str:
    params = psycopg.conninfo.conninfo_to_dict(conninfo)
    params.update(changes)
    return str(psycopg.conninfo.make_conninfo(**params))


@pytest.fixture(scope="module")
def conninfo() -> str:
    return _conninfo()


@pytest.fixture
def schema(conninfo: str) -> Iterator[str]:
    name = f"tabrion_e_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(conninfo, autocommit=True) as admin:
        admin.execute(f"CREATE SCHEMA {name}")
        admin.execute(
            f"CREATE TABLE {name}.parts (id int PRIMARY KEY, email text UNIQUE, n int NOT NULL CHECK (n > 0), "
            f"r int4range, EXCLUDE USING gist (r WITH &&))"
        )
        admin.execute(f"CREATE TABLE {name}.items (id int PRIMARY KEY, part int REFERENCES {name}.parts (id))")
        admin.execute(f"INSERT INTO {name}.parts VALUES (1, %s, 1, '[1,5)')", (f"{CANARY}@example.com",))
    try:
        yield name
    finally:
        with psycopg.connect(conninfo, autocommit=True) as admin:
            admin.execute(f"DROP SCHEMA {name} CASCADE")


@pytest.fixture
def connect(conninfo: str, schema: str) -> Iterator[Callable[..., Any]]:
    opened: list[Any] = []

    def factory(**kwargs: Any) -> Any:
        kwargs.setdefault("autocommit", True)
        conn = psycopg.connect(conninfo, options=f"-c search_path={schema}", connect_timeout=10, **kwargs)
        opened.append(conn)
        return conn

    yield factory
    for conn in opened:
        conn.close()


def _error(conn: Any, sql: str, params: Any = None) -> Exception:
    try:
        conn.execute(sql, params)
    except psycopg.Error as exc:
        error: Exception = exc
        return error
    raise AssertionError(f"{sql!r} did not fail")


# --- TAB-T-E01..E09 with server errors ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sql", "kind", "sqlstate", "constraint", "column"),
    [
        (
            f"INSERT INTO parts VALUES (2, '{CANARY}@example.com', 1, NULL)",
            Kind.UNIQUE,
            "23505",
            "parts_email_key",
            None,
        ),
        ("INSERT INTO items VALUES (1, 99)", Kind.FOREIGN_KEY, "23503", "items_part_fkey", None),
        ("INSERT INTO parts VALUES (3, 'b', NULL, NULL)", Kind.NOT_NULL, "23502", None, "n"),
        ("INSERT INTO parts VALUES (4, 'c', -1, NULL)", Kind.CHECK, "23514", "parts_n_check", None),
        ("INSERT INTO parts VALUES (5, 'd', 1, '[2,3)')", Kind.EXCLUSION, "23P01", "parts_r_excl", None),
        ("SELECT 1 / 0", Kind.DATA, "22012", None, None),
        ("SELECT 'x'::int", Kind.DATA, "22P02", None, None),
        ("SELECT * FROM missing", Kind.UNDEFINED_OBJECT, "42P01", None, None),
        ("SELECT missing FROM parts", Kind.UNDEFINED_OBJECT, "42703", None, None),
        ("SELECT missing()", Kind.UNDEFINED_OBJECT, "42883", None, None),
        ("SELECT 1::missing_type", Kind.UNDEFINED_OBJECT, "42704", None, None),
        ("CREATE TABLE missing_schema.t (x int)", Kind.UNDEFINED_OBJECT, "3F000", None, None),
        ("SELEC 1", Kind.SYNTAX, "42601", None, None),
    ],
)
def test_e01_e09_server_errors(
    connect: Callable[..., Any], sql: str, kind: Kind, sqlstate: str, constraint: str | None, column: str | None
) -> None:
    conn = connect()
    exc = _error(conn, sql)
    c = classify(exc)
    assert (c.kind, c.sqlstate, c.constraint, c.column, c.driver) == (kind, sqlstate, constraint, column, "psycopg")
    assert c.connection_usable and not c.retryable and not c.outcome_unknown
    assert CANARY not in safe_message(exc)
    assert conn.execute("SELECT 1").fetchone() == (1,)


def test_e05_permission_and_read_only(conninfo: str, connect: Callable[..., Any], schema: str) -> None:
    role = f"tabrion_e_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(conninfo, autocommit=True) as admin:
        admin.execute(f"CREATE ROLE {role} LOGIN PASSWORD 'tabrion-test'")
        admin.execute(f"GRANT USAGE ON SCHEMA {schema} TO {role}")
    try:
        user = psycopg.connect(_with(conninfo, user=role, password="tabrion-test"), autocommit=True)
        with user:
            denied = classify(_error(user, f"SELECT * FROM {schema}.parts"))
            assert (denied.kind, denied.sqlstate, denied.connection_usable) == (Kind.PERMISSION, "42501", True)
    finally:
        with psycopg.connect(conninfo, autocommit=True) as admin:
            admin.execute(f"DROP OWNED BY {role}")
            admin.execute(f"DROP ROLE {role}")
    conn = connect(autocommit=False)
    conn.execute("SET TRANSACTION READ ONLY")
    read_only = classify(_error(conn, "INSERT INTO parts VALUES (9, 'x', 1, NULL)"))
    assert (read_only.kind, read_only.sqlstate) == (Kind.READ_ONLY, "25006")


def test_e06_statement_timeout_and_cancel(connect: Callable[..., Any]) -> None:
    conn = connect()
    conn.execute("SET statement_timeout = '50ms'")
    timeout = classify(_error(conn, "SELECT pg_sleep(2)"))
    assert (timeout.kind, timeout.sqlstate, timeout.retryable, timeout.connection_usable) == (
        Kind.TIMEOUT,
        "57014",
        False,
        True,
    )
    conn.execute("SET statement_timeout = 0")
    timer = threading.Timer(0.3, conn.cancel_safe)
    timer.start()
    try:
        canceled = classify(_error(conn, "SELECT pg_sleep(5)"))
    finally:
        timer.cancel()
    assert (canceled.kind, canceled.sqlstate, canceled.connection_usable) == (Kind.CANCELED, "57014", True)
    assert conn.execute("SELECT 1").fetchone() == (1,)


def test_e07_lock_deadlock_serialization(connect: Callable[..., Any]) -> None:
    a, b = connect(autocommit=False), connect(autocommit=False)
    a.execute("SELECT * FROM parts WHERE id = 1 FOR UPDATE")
    b.execute("SET lock_timeout = '50ms'")
    locked = classify(_error(b, "SELECT * FROM parts WHERE id = 1 FOR UPDATE"))
    assert (locked.kind, locked.sqlstate, locked.retryable, locked.connection_usable) == (
        Kind.LOCKED,
        "55P03",
        True,
        True,
    )
    a.rollback()
    b.rollback()

    # Deadlock: a locks 1 then 2, b locks 2 then 1.
    a.execute("INSERT INTO parts VALUES (2, 'two', 1, NULL)")
    a.commit()
    a.execute("UPDATE parts SET n = 2 WHERE id = 1")
    b.execute("UPDATE parts SET n = 2 WHERE id = 2")
    errors: list[Exception] = []

    def second() -> None:
        try:
            b.execute("UPDATE parts SET n = 3 WHERE id = 1")
        except psycopg.Error as exc:
            errors.append(exc)

    worker = threading.Thread(target=second)
    worker.start()
    time.sleep(0.3)
    try:
        a.execute("UPDATE parts SET n = 3 WHERE id = 2")
    except psycopg.Error as exc:
        errors.append(exc)
    worker.join(10)
    a.rollback()
    b.rollback()
    (deadlock,) = errors
    c = classify(deadlock)
    assert (c.kind, c.sqlstate, c.retryable) == (Kind.DEADLOCK, "40P01", True)

    # Serialization failure under REPEATABLE READ.
    for conn in (a, b):
        conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
    a.execute("SELECT n FROM parts WHERE id = 1")
    b.execute("SELECT n FROM parts WHERE id = 1")
    a.execute("UPDATE parts SET n = 5 WHERE id = 1")
    a.commit()
    serialization = classify(_error(b, "UPDATE parts SET n = 6 WHERE id = 1"))
    assert (serialization.kind, serialization.sqlstate, serialization.retryable) == (Kind.SERIALIZATION, "40001", True)
    b.rollback()


def test_e08_lost_connections(conninfo: str, connect: Callable[..., Any]) -> None:
    victim = connect()
    with psycopg.connect(conninfo, autocommit=True) as admin:
        admin.execute("SELECT pg_terminate_backend(%s)", (victim.info.backend_pid,))
    terminated = classify(_error(victim, "SELECT 1"))
    assert terminated.kind is Kind.CONNECTION and not terminated.connection_usable
    assert terminated.outcome_unknown and not terminated.retryable  # R-NORETRY
    closed = classify(_error(victim, "SELECT 1"))  # psycopg: "the connection is closed", no SQLSTATE
    assert (closed.kind, closed.sqlstate, closed.connection_usable) == (Kind.CONNECTION, None, False)

    idle = connect(autocommit=False)
    idle.execute("SET idle_in_transaction_session_timeout = '100ms'")
    idle.execute("SELECT 1")
    time.sleep(0.6)
    timed_out = classify(_error(idle, "SELECT 1"))
    assert timed_out.kind in (Kind.TIMEOUT, Kind.CONNECTION), timed_out  # 25P03, or the closed socket first
    assert not timed_out.connection_usable


def test_e12_conninfo_password_appears_in_no_message(conninfo: str) -> None:
    for target in (_with(conninfo, password=CANARY), _with(conninfo, host="127.0.0.1", port="1", password=CANARY)):
        with pytest.raises(psycopg.OperationalError) as info:
            psycopg.connect(target, connect_timeout=3)
        c = classify(info.value, sent=False)
        assert (c.kind, c.retryable, c.outcome_unknown) == (Kind.CONNECTION, True, False)
        assert CANARY not in safe_message(info.value) and CANARY not in repr(c)
        stream = io.StringIO()
        _log_to(stream).error("connect failed", exc_info=info.value)
        assert CANARY not in stream.getvalue()


def test_e15_localized_messages(connect: Callable[..., Any]) -> None:
    """With lc_messages=de_DE a statement timeout reads differently; classify must not crash (R-T02)."""
    conn = connect()
    try:
        conn.execute("SET lc_messages = 'de_DE.UTF-8'")
    except psycopg.Error:
        pytest.skip("the server has no de_DE.UTF-8 locale")
    conn.execute("SET statement_timeout = '50ms'")
    c = classify(_error(conn, "SELECT pg_sleep(2)"))
    assert c.sqlstate == "57014" and c.kind in (Kind.TIMEOUT, Kind.CANCELED)


# --- TAB-T-E14: outcome_unknown by fault injection -------------------------------------------------------------------


class CuttingProxy:
    """A TCP proxy that forwards a statement containing ``trigger`` and cuts both sockets before the reply arrives.

    Adapted from the planning probe ``proben/jobqueue_retry_probe.py``; TAB-034 brings the full fault proxy.
    """

    def __init__(self, host: str, port: int) -> None:
        self.target = (host, port)
        self.trigger: bytes | None = None
        self.fired = 0
        self.listener = socket.create_server(("127.0.0.1", 0))
        self.port = self.listener.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        with contextlib.suppress(OSError):
            while True:
                client, _ = self.listener.accept()
                server = socket.create_connection(self.target)
                state = {"cut": False}
                threading.Thread(target=self._pipe, args=(client, server, True, state), daemon=True).start()
                threading.Thread(target=self._pipe, args=(server, client, False, state), daemon=True).start()

    def _pipe(self, src: socket.socket, dst: socket.socket, upstream: bool, state: dict[str, bool]) -> None:
        with contextlib.suppress(OSError):
            while data := src.recv(65536):
                if state["cut"]:
                    return
                hit = upstream and self.trigger is not None and self.trigger in data
                dst.sendall(data)
                if hit:
                    state["cut"] = True
                    self.trigger = None
                    self.fired += 1
                    time.sleep(0.5)  # the server runs (and commits) the statement
                    for s in (src, dst):
                        with contextlib.suppress(OSError):
                            s.shutdown(socket.SHUT_RDWR)
                        s.close()
                    return

    def close(self) -> None:
        self.listener.close()


@pytest.fixture
def proxy(conninfo: str) -> Iterator[CuttingProxy]:
    params = psycopg.conninfo.conninfo_to_dict(conninfo)
    host = str(params.get("host") or "127.0.0.1")
    if host.startswith("/"):
        pytest.skip("the fault proxy needs a TCP server address")
    cutter = CuttingProxy(host, int(params.get("port") or 5432))
    yield cutter
    cutter.close()


def test_e14_reply_lost_after_sending(conninfo: str, schema: str, proxy: CuttingProxy) -> None:
    through = _with(conninfo, host="127.0.0.1", port=str(proxy.port), sslmode="disable", gssencmode="disable")
    marker = f"tabrion-e14-{uuid.uuid4().hex[:8]}"
    with psycopg.connect(through, autocommit=True, connect_timeout=10) as conn:
        proxy.trigger = marker.encode()
        with pytest.raises(psycopg.OperationalError) as info:
            conn.execute(f"INSERT INTO {schema}.parts VALUES (100, '{marker}', 1, NULL)")
    assert proxy.fired == 1
    c = classify(info.value)
    assert (c.kind, c.outcome_unknown, c.retryable, c.connection_usable) == (Kind.CONNECTION, True, False, False)
    # The statement did run: retrying it would have written twice (R-NORETRY).
    with psycopg.connect(conninfo, autocommit=True) as check:
        assert check.execute(f"SELECT count(*) FROM {schema}.parts WHERE email = %s", (marker,)).fetchone() == (1,)


def test_e14_reply_lost_during_commit(conninfo: str, schema: str, proxy: CuttingProxy) -> None:
    through = _with(conninfo, host="127.0.0.1", port=str(proxy.port), sslmode="disable", gssencmode="disable")
    marker = f"tabrion-e14c-{uuid.uuid4().hex[:8]}"
    with psycopg.connect(through, connect_timeout=10) as conn:
        conn.execute(f"INSERT INTO {schema}.parts VALUES (101, '{marker}', 1, NULL)")
        proxy.trigger = b"COMMIT"
        with pytest.raises(psycopg.OperationalError) as info:
            conn.commit()
    assert proxy.fired == 1
    c = classify(info.value, sent=False, during_commit=True)
    assert (c.kind, c.outcome_unknown, c.retryable) == (Kind.CONNECTION, True, False)


# --- TAB-T-E21, E23 with real driver errors --------------------------------------------------------------------------


def _log_to(stream: io.StringIO) -> logging.Logger:
    logger = logging.getLogger(f"tabrion.test.pg.{uuid.uuid4().hex}")
    logger.propagate = False
    handler = logging.StreamHandler(stream)
    handler.addFilter(DbErrorRedactionFilter())
    logger.addHandler(handler)
    return logger


def test_e21_real_unique_violation_is_redacted(connect: Callable[..., Any]) -> None:
    conn = connect()
    stream = io.StringIO()
    logger = _log_to(stream)
    try:
        conn.execute("INSERT INTO parts VALUES (7, %s, 1, NULL)", (f"{CANARY}@example.com",))
    except psycopg.Error as exc:
        assert CANARY in str(exc)  # the leak this filter closes (B-06-1)
        logger.exception("insert failed: %s", exc)
    output = stream.getvalue()
    assert CANARY not in output
    assert "SQLSTATE 23505" in output and "parts_email_key" in output


def test_e23_real_sqlalchemy_parameters_are_redacted(conninfo: str, schema: str) -> None:
    sqlalchemy = pytest.importorskip("sqlalchemy")
    params = psycopg.conninfo.conninfo_to_dict(conninfo)
    url = sqlalchemy.engine.URL.create(
        "postgresql+psycopg",
        username=params.get("user"),
        password=params.get("password"),
        host=params.get("host"),
        port=int(params["port"]) if params.get("port") else None,
        database=params.get("dbname"),
    )
    engine = sqlalchemy.create_engine(url)
    stream = io.StringIO()
    logger = _log_to(stream)
    try:
        with engine.begin() as conn:
            conn.execute(
                sqlalchemy.text(f"INSERT INTO {schema}.parts VALUES (8, :email, 1, NULL)"),
                {"email": f"{CANARY}@example.com"},
            )
    except sqlalchemy.exc.IntegrityError as exc:
        assert CANARY in str(exc) and "[parameters:" in str(exc)
        c = classify(exc)
        assert (c.kind, c.sqlstate, c.constraint) == (Kind.UNIQUE, "23505", "parts_email_key")
        logger.exception("orm failed")
        logger.error("again: %s", exc)
    finally:
        engine.dispose()
    output = stream.getvalue()
    assert output and CANARY not in output and "[parameters:" not in output
    assert "SQLSTATE 23505" in output


# --- Review fixes: H-1 (R-NORETRY with an implicit __context__), H-2 (repr and container leaks) ---------------------


def _cut(fileno: int) -> None:
    """Shut the connection's socket down under the driver, as a lost network would."""
    sock = socket.socket(fileno=os.dup(fileno))
    try:
        sock.shutdown(socket.SHUT_RDWR)
    finally:
        sock.close()


def test_h1_commit_lost_while_handling_a_serialization_failure(connect: Callable[..., Any]) -> None:
    a, b = connect(autocommit=False), connect(autocommit=False)
    for conn in (a, b):
        conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
    a.execute("SELECT n FROM parts WHERE id = 1")
    b.execute("SELECT n FROM parts WHERE id = 1")
    a.execute("UPDATE parts SET n = 5 WHERE id = 1")
    a.commit()
    lost: BaseException | None = None
    try:
        b.execute("UPDATE parts SET n = 6 WHERE id = 1")
    except psycopg.errors.SerializationFailure:
        # The retry runs inside the handler; its COMMIT loses the connection.
        b.rollback()
        b.execute("UPDATE parts SET n = 7 WHERE id = 1")
        _cut(b.fileno())
        try:
            b.commit()
        except psycopg.OperationalError as exc:
            lost = exc
    assert lost is not None and isinstance(lost.__context__, psycopg.errors.SerializationFailure)
    assert getattr(lost, "sqlstate", None) is None
    c = classify(lost, during_commit=True)
    assert (c.kind, c.outcome_unknown, c.retryable, c.connection_usable) == (Kind.CONNECTION, True, False, False)


def test_h1_sqlalchemy_upsert_fallback_loses_the_connection(conninfo: str, schema: str) -> None:
    sqlalchemy = pytest.importorskip("sqlalchemy")
    params = psycopg.conninfo.conninfo_to_dict(conninfo)
    url = sqlalchemy.engine.URL.create(
        "postgresql+psycopg",
        username=params.get("user"),
        password=params.get("password"),
        host=params.get("host"),
        port=int(params["port"]) if params.get("port") else None,
        database=params.get("dbname"),
    )
    engine = sqlalchemy.create_engine(url, poolclass=sqlalchemy.pool.NullPool)
    lost: BaseException | None = None
    try:
        with engine.connect() as conn:
            try:
                conn.execute(sqlalchemy.text(f"INSERT INTO {schema}.parts VALUES (1, 'x', 1, NULL)"))
            except sqlalchemy.exc.IntegrityError:
                conn.rollback()
                conn.execute(sqlalchemy.text(f"UPDATE {schema}.parts SET n = 2 WHERE id = 1"))
                _cut(conn.connection.dbapi_connection.fileno())
                try:
                    conn.commit()
                except sqlalchemy.exc.OperationalError as exc:
                    lost = exc
    finally:
        engine.dispose()
    assert lost is not None
    contexts: list[BaseException] = []
    current: BaseException | None = lost
    while current is not None and len(contexts) < 5:
        contexts.append(current)
        current = current.__context__
    assert any(isinstance(e, sqlalchemy.exc.IntegrityError) for e in contexts), contexts  # the older 23505
    c = classify(lost, during_commit=True)
    assert (c.kind, c.outcome_unknown, c.retryable, c.connection_usable) == (Kind.CONNECTION, True, False, False)
    assert c.constraint is None


def test_h2_real_unique_violation_in_repr_and_containers(connect: Callable[..., Any]) -> None:
    conn = connect()
    exc = _error(conn, "INSERT INTO parts VALUES (7, %s, 1, NULL)", (f"{CANARY}@example.com",))
    assert CANARY in repr(exc)  # the leak the filter has to close
    stream = io.StringIO()
    logger = _log_to(stream)
    logger.error(f"x {exc!r}")
    logger.error("x %s", repr(exc))
    logger.error("x %r", str(exc))
    logger.error("x %s", [exc])
    logger.error("x %s", (exc,))
    logger.error("x %s", exc.args)
    logger.error({"err": exc})
    output = stream.getvalue()
    assert output.count("\n") == 7 and CANARY not in output, output
