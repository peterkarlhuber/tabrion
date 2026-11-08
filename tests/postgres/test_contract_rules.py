"""Rules T-NOTX and R-SET for every implemented layer-A PostgreSQL function (TAB-T-G01, TAB-T-G02).

* G01: ``connection.info.transaction_status`` before and after each function in three states (IDLE with autocommit,
  IDLE without autocommit, INTRANS): INTRANS stays INTRANS, IDLE stays IDLE; the only documented exception is
  ``configure`` on IDLE without autocommit (INTRANS, no commit). Row factory, cursor factory and ``autocommit`` stay
  unchanged, and a caller's open write is neither committed nor rolled back.
* G02: the host's settings survive: a transaction-local ``set_config('app.tenant_id', ..., true)``, a session-level
  ``app.*`` setting and ``current_user``. Tabrion never runs ``RESET ALL`` or ``DISCARD``.
* Catalog functions are schema-qualified: a ``search_path`` that puts a hostile schema before ``pg_catalog`` cannot
  replace them.
"""

# The SQL is built from names this file generates (uuid hex) and fixed text, never from outside input.
# ruff: noqa: S608

from __future__ import annotations

from collections.abc import Callable
import contextlib
from typing import Any

import pytest

import tabrion.errors
from tabrion.postgres import (
    RoleReport,
    SessionSettings,
    check_role,
    configure,
    diagnose,
    extension_status,
    inspect_role,
    ping,
    role_info,
    session_configurator,
    try_advisory_lock,
    wait_advisory_lock,
)

from .conftest import Role

psycopg = pytest.importorskip("psycopg")
from psycopg import pq  # noqa: E402
from psycopg.rows import dict_row, namedtuple_row, tuple_row  # noqa: E402

pytestmark = pytest.mark.postgres

IDLE = pq.TransactionStatus.IDLE
INTRANS = pq.TransactionStatus.INTRANS

READERS: dict[str, Callable[[Any], object]] = {
    "ping": lambda c: ping(c, timeout=5.0),
    "diagnose": diagnose,
    "extension_status": lambda c: extension_status(c, "plpgsql"),
    "extension_status(probe_type)": lambda c: extension_status(c, "pg_trgm", probe_type="gtrgm"),
    "role_info": role_info,
    "role_info(rls)": lambda c: role_info(c, owner_scope="rls"),
    "check_role": lambda c: check_role(c, allow_superuser=True),
    "inspect_role": lambda c: inspect_role(c, c.info.user),
}
WRITERS: dict[str, Callable[[Any], object]] = {
    "configure": lambda c: configure(c, SessionSettings(statement_timeout_ms=45_000, application_name="g01")),
    "session_configurator": session_configurator(SessionSettings(statement_timeout_ms=45_000)),
}
STATES = ["idle-autocommit", "idle", "intrans"]
FACTORIES = {
    "tuple": {},
    "dict_row": {"row_factory": dict_row},
    "namedtuple_row+ClientCursor": {"row_factory": namedtuple_row, "cursor_factory": psycopg.ClientCursor},
    "RawCursor": {"cursor_factory": psycopg.RawCursor},
}


def _value(conn: Any, sql: str) -> Any:
    with psycopg.Cursor(conn, row_factory=tuple_row) as cur:
        cur.execute(sql)
        return cur.fetchone()[0]


def _prepare(connect: Callable[..., Any], url: str, state: str, factory: dict[str, Any], schema: str) -> Any:
    conn = connect(url, autocommit=state == "idle-autocommit", **factory)
    with psycopg.Cursor(conn, row_factory=tuple_row) as cur:
        cur.execute("SELECT pg_catalog.set_config('app.marker', 'host', false)")  # a host session setting
        if state == "idle":
            conn.commit()
        if state == "intrans":
            cur.execute(f"INSERT INTO {schema}.parts VALUES (7, 'open')")
            cur.execute("SELECT pg_catalog.set_config('app.tenant_id', 'tenant-7', true)")
    return conn


def _snapshot(conn: Any) -> dict[str, Any]:
    return {
        "status": conn.info.transaction_status,
        "autocommit": conn.autocommit,
        "row_factory": conn.row_factory,
        "cursor_factory": conn.cursor_factory,
        "backend": conn.info.backend_pid,
    }


def _host_settings(conn: Any) -> tuple[Any, ...]:
    with psycopg.Cursor(conn, row_factory=tuple_row) as cur:
        cur.execute(
            "SELECT pg_catalog.current_setting('app.tenant_id', true), pg_catalog.current_setting('app.marker', true),"
            " CURRENT_USER::text, SESSION_USER::text, pg_catalog.current_setting('statement_timeout')"
        )
        return tuple(cur.fetchone())


@pytest.fixture(params=["superuser", "runtime role"])
def url(request: pytest.FixtureRequest, admin_url: str, make_role: Callable[..., Role], admin: Any, schema: str) -> str:
    if request.param == "superuser":
        return admin_url
    role = make_role()
    admin.execute(f'GRANT USAGE ON SCHEMA {schema} TO "{role.name}"')
    admin.execute(f'GRANT SELECT, INSERT ON {schema}.parts TO "{role.name}"')
    return role.url


@pytest.mark.parametrize("factory", list(FACTORIES))
@pytest.mark.parametrize("state", STATES)
@pytest.mark.parametrize("name", list(READERS))
def test_g01_g02_readers(
    connect: Callable[..., Any], url: str, admin: Any, schema: str, name: str, state: str, factory: str
) -> None:
    conn = _prepare(connect, url, state, FACTORIES[factory], schema)
    before = _snapshot(conn)
    settings = _host_settings(conn)
    if state == "idle":
        conn.rollback()  # _host_settings opened an implicit transaction
    READERS[name](conn)
    assert _snapshot(conn) == before
    assert _host_settings(conn) == settings
    if state == "intrans":
        assert _value(conn, f"SELECT count(*) FROM {schema}.parts WHERE id = 7") == 1  # not rolled back
        assert admin.execute(f"SELECT count(*) FROM {schema}.parts").fetchone() == (0,)  # not committed
    conn.rollback()


@pytest.mark.parametrize("state", STATES)
@pytest.mark.parametrize("name", list(WRITERS))
def test_g01_g02_writers(connect: Callable[..., Any], url: str, admin: Any, schema: str, name: str, state: str) -> None:
    conn = _prepare(connect, url, state, {"row_factory": dict_row}, schema)
    before = _snapshot(conn)
    settings = _host_settings(conn)
    if state == "idle":
        conn.rollback()
    WRITERS[name](conn)
    after = _snapshot(conn)
    if state == "idle" and name == "configure":
        # The documented exception: configure opened a transaction and does not commit it (contract 4.1).
        assert after == {**before, "status": INTRANS}
    else:
        assert after == before
    tenant, marker, user, session_user, timeout = _host_settings(conn)
    assert (tenant, marker, user, session_user) == settings[:4]
    assert timeout == "45s"
    if state == "intrans":
        assert _value(conn, f"SELECT count(*) FROM {schema}.parts WHERE id = 7") == 1
        assert admin.execute(f"SELECT count(*) FROM {schema}.parts").fetchone() == (0,)
    conn.rollback()


def test_g01_failed_transaction_is_left_alone(connect: Callable[..., Any]) -> None:
    """INERROR: the readers raise the driver error (25P02) and leave the caller's failed transaction as it was."""
    for name, fn in READERS.items():
        conn = connect()
        with pytest.raises(psycopg.errors.DivisionByZero):
            conn.execute("SELECT 1 / 0")
        if name == "ping":
            assert fn(conn).ok is False  # type: ignore[attr-defined]
        else:
            with pytest.raises(psycopg.errors.InFailedSqlTransaction):
                fn(conn)
        assert conn.info.transaction_status == pq.TransactionStatus.INERROR, name
        conn.rollback()


FAILING: dict[str, Callable[[Any], object]] = {
    # Review finding M-1: valid-looking inputs whose statement fails (or used to fail) on the server.
    "inspect_role(missing schema)": lambda c: inspect_role(c, c.info.user, schema="t_no_such_schema"),
    "extension_status(bad probe_type)": lambda c: extension_status(c, "plpgsql", probe_type="int["),
    "configure(unknown time zone)": lambda c: configure(c, SessionSettings(timezone="Mars/Olympus")),
    "inspect_role(missing role)": lambda c: inspect_role(c, "t_no_such_role_4711"),
}


@pytest.mark.parametrize("explicit", [False, True], ids=["implicit", "transaction()"])
@pytest.mark.parametrize("name", list(FAILING))
def test_g01_failure_keeps_the_callers_transaction(
    connect: Callable[..., Any], url: str, admin: Any, schema: str, name: str, explicit: bool
) -> None:
    """T-NOTX: whatever the function raises or returns, the caller's open write stays and can be committed."""
    conn = _prepare(connect, url, "idle-autocommit" if explicit else "intrans", {"row_factory": dict_row}, schema)
    with conn.transaction() if explicit else contextlib.nullcontext():
        if explicit:
            conn.execute(f"INSERT INTO {schema}.parts VALUES (7, 'open')")
        timezone = _value(conn, "SELECT pg_catalog.current_setting('TimeZone')")
        try:
            result = FAILING[name](conn)
        except (ValueError, psycopg.Error, tabrion.errors.TabrionError):
            result = None
        assert conn.info.transaction_status == INTRANS, name
        assert _value(conn, f"SELECT count(*) FROM {schema}.parts WHERE id = 7") == 1
        assert _value(conn, "SELECT pg_catalog.current_setting('TimeZone')") == timezone
        if not explicit:
            conn.commit()
    assert admin.execute(f"SELECT count(*) FROM {schema}.parts WHERE id = 7").fetchone() == (1,)
    if name.startswith("inspect_role(missing schema)"):
        assert isinstance(result, RoleReport)
        assert (result.schema, result.tables, result.owned_tables, result.can_create) == (
            "t_no_such_schema",
            0,
            (),
            False,
        )


@pytest.mark.parametrize("state", STATES)
def test_m1_defined_results(connect: Callable[..., Any], admin_url: str, state: str, schema: str) -> None:
    """The defined results of review finding M-1 in every state: a report for a missing schema, ``ValueError`` (or,
    on a server whose to_regtype returns NULL for it, an unresolved probe) for a malformed type name."""
    conn = _prepare(connect, admin_url, state, {}, schema)
    if state == "idle":
        conn.rollback()
    before = conn.info.transaction_status
    report = inspect_role(conn, conn.info.user, schema="t_no_such_schema")
    assert (report.tables, report.can_create, report.superuser) == (0, False, True)
    try:
        status = extension_status(conn, "plpgsql", probe_type="int[")
    except ValueError as exc:
        assert "probe_type" in str(exc) and "int[" not in str(exc)
    else:
        assert status.state == "other_schema"
    assert conn.info.transaction_status == before
    conn.rollback()


def test_catalog_functions_cannot_be_hijacked(connect: Callable[..., Any], admin: Any, schema: str) -> None:
    """A schema before pg_catalog in search_path with functions, operators and types (domains) of the same names
    changes nothing: none of them runs (contract I-16, review finding M-2)."""
    for signature in (
        "current_setting(text)",
        "current_setting(text, boolean)",
        "pg_is_in_recovery()",
        "current_schema()",
        "current_schemas(boolean)",
        "current_database()",
        "set_config(text, text, boolean)",
        "has_table_privilege(text, text)",
        "has_database_privilege(text, text, text)",
        "has_schema_privilege(text, text, text)",
        "pg_has_role(name, oid, text)",
        "pg_has_role(text, oid, text)",
        "pg_has_role(oid, oid, text)",
        "has_database_privilege(oid, text, text)",
        "has_schema_privilege(oid, oid, text)",
        "to_regtype(text)",
        "pg_backend_pid()",
    ):
        returns = "text"
        admin.execute(
            f"CREATE FUNCTION {schema}.{signature} RETURNS {returns} LANGUAGE plpgsql AS"
            " $$BEGIN RAISE EXCEPTION 'hijacked'; END$$"
        )
    # Review finding M-2: operators and types resolve through search_path too. Every operator Tabrion's SQL uses,
    # for every pair of argument types it meets, and domains named like the types it casts to; any use raises.
    for number, (left, right) in enumerate(
        (
            ("name", "name"),
            ("name", "text"),
            ("text", "text"),
            ("oid", "oid"),
            ('"char"', '"char"'),
            ("int4", "int4"),
        )
    ):
        admin.execute(
            f"CREATE FUNCTION {schema}.trap_op{number}(pg_catalog.{left}, pg_catalog.{right}) RETURNS boolean"
            " LANGUAGE plpgsql AS $$BEGIN RAISE EXCEPTION 'hijacked operator'; END$$"
        )
        admin.execute(
            f"CREATE OPERATOR {schema}.= (leftarg = pg_catalog.{left}, rightarg = pg_catalog.{right},"
            f" function = {schema}.trap_op{number})"
        )
    for domain in ("text", "name", "oid", "regtype"):
        admin.execute(
            f"CREATE FUNCTION {schema}.trap_{domain}(pg_catalog.{domain}) RETURNS boolean LANGUAGE plpgsql AS"
            " $$BEGIN RAISE EXCEPTION 'hijacked type'; END$$"
        )
        admin.execute(f"CREATE DOMAIN {schema}.{domain} AS pg_catalog.{domain} CHECK ({schema}.trap_{domain}(VALUE))")
    admin.execute(f"GRANT USAGE ON SCHEMA {schema} TO PUBLIC")
    conn = connect(autocommit=True, options=f"-c search_path={schema},pg_catalog")
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("SELECT current_setting('server_version_num')")  # the trap works for unqualified calls
    for probe in (
        "SELECT 'a'::pg_catalog.name = 'b'::pg_catalog.name",
        "SELECT 1::pg_catalog.oid = 2::pg_catalog.oid",
        "SELECT 'r'::pg_catalog.\"char\" = 'p'",
        "SELECT CURRENT_USER::text",
        "SELECT 'x'::name",
    ):
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute(probe)  # each trap works for unqualified use
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("SELECT 'a'::pg_catalog.name = 'b'::pg_catalog.text")
    assert ping(conn).ok
    diagnosis = diagnose(conn)
    assert diagnosis.schema == schema
    assert extension_status(conn, "plpgsql", probe_type="int4").state == "active"
    assert role_info(conn).superuser and role_info(conn, owner_scope="rls").superuser
    assert inspect_role(conn, conn.info.user, schema=schema).tables == 1
    assert inspect_role(conn, conn.info.user).superuser
    assert check_role(conn, allow_superuser=True).superuser
    assert extension_status(conn, "plpgsql").state == "active"
    assert extension_status(conn, "t_no_such_extension").state == "unavailable"
    configure(conn, SessionSettings(statement_timeout_ms=1_000, search_path=schema))
    assert _value(conn, "SELECT pg_catalog.current_setting('statement_timeout')") == "1s"
    session_configurator(SessionSettings(lock_timeout_ms=2_000))(conn)
    assert _value(conn, "SELECT pg_catalog.current_setting('lock_timeout')") == "2s"


def test_i1_ping_in_failed_transaction_logs_nothing(
    connect: Callable[..., Any], caplog: pytest.LogCaptureFixture
) -> None:
    """Review I-1: in an aborted transaction ``ping`` opens no savepoint, so psycopg logs no failed rollback."""
    conn = connect()
    with pytest.raises(psycopg.errors.DivisionByZero):
        conn.execute("SELECT 1 / 0")
    with caplog.at_level("DEBUG", logger="psycopg"):
        result = ping(conn)
    assert result.ok is False and result.classification is not None
    assert result.classification.sqlstate == "25P02"
    assert [r.getMessage() for r in caplog.records if r.name.startswith("psycopg")] == []
    assert conn.info.transaction_status == pq.TransactionStatus.INERROR
    conn.rollback()


@pytest.mark.parametrize("name", [*FAILING, "ping", "role_info", "diagnose"])
def test_dl1_pipeline_mode_keeps_the_callers_transaction(
    connect: Callable[..., Any], admin: Any, schema: str, name: str
) -> None:
    """Review DL-1: inside ``connection.pipeline()`` the status is ACTIVE until a sync; the caller's pipeline is synced
    first, so the savepoint applies and a failing statement does not abort the caller's pipeline transaction."""
    function = FAILING.get(name) or READERS[name]
    conn = connect()
    with conn.pipeline():
        conn.execute(f"INSERT INTO {schema}.parts VALUES (7, 'queued before')")  # queued, not yet synced
        assert conn.info.transaction_status == pq.TransactionStatus.ACTIVE
        try:
            function(conn)
        except (ValueError, psycopg.Error, tabrion.errors.TabrionError) as exc:
            assert not isinstance(exc, psycopg.errors.PipelineAborted), name
        conn.execute(f"INSERT INTO {schema}.parts VALUES (8, 'queued after')")
    assert conn.info.transaction_status == INTRANS, name
    conn.commit()
    assert admin.execute(f"SELECT count(*) FROM {schema}.parts WHERE id IN (7, 8)").fetchone() == (2,)


@pytest.mark.parametrize("autocommit", [False, True])
def test_dl1_pipeline_mode_on_an_idle_connection(connect: Callable[..., Any], autocommit: bool) -> None:
    """A reader in a pipeline that holds nothing leaves the connection as it found it (its own transaction is rolled
    back after the sync)."""
    conn = connect(autocommit=autocommit)
    with conn.pipeline():
        assert role_info(conn).superuser
        assert ping(conn).ok
        with pytest.raises(ValueError, match="probe_type"):
            extension_status(conn, "plpgsql", probe_type="int[")
    assert conn.info.transaction_status == IDLE


@pytest.mark.parametrize("autocommit", [False, True])
@pytest.mark.parametrize("name", ["ping", "role_info", "try_advisory_lock", "wait_advisory_lock"])
def test_m1_6_pipeline_error_of_the_caller_propagates(
    connect: Callable[..., Any], admin: Any, schema: str, name: str, autocommit: bool
) -> None:
    """Review M-1 (#6): a statement the caller queued in its pipeline fails; the function raises that error (it is
    the caller's). ``ping`` used to report it as a failed ping, so psycopg did not raise it again and the caller's
    COMMIT silently ended its aborted transaction."""
    admin.execute(f"INSERT INTO {schema}.parts VALUES (1, 'there')")
    calls: dict[str, Callable[[Any], object]] = {
        "ping": ping,
        "role_info": role_info,
        "try_advisory_lock": lambda c: try_advisory_lock(c, 4711),
        "wait_advisory_lock": lambda c: wait_advisory_lock(c, 4712, timeout=0),
    }
    conn = connect(autocommit=autocommit)
    with conn.pipeline():
        conn.execute(f"INSERT INTO {schema}.parts VALUES (2, 'lost')")
        conn.execute(f"INSERT INTO {schema}.parts VALUES (1, 'duplicate')")  # fails on the server
        with pytest.raises(psycopg.errors.UniqueViolation):
            calls[name](conn)
    if not autocommit:
        assert conn.info.transaction_status == pq.TransactionStatus.INERROR
        conn.rollback()
    # The queued statements up to the sync form one implicit transaction, also with autocommit: both are gone, and
    # the caller was told so by the error.
    assert admin.execute(f"SELECT id FROM {schema}.parts ORDER BY id").fetchall() == [(1,)]
