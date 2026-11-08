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
from typing import Any

import pytest

from tabrion.postgres import (
    SessionSettings,
    check_role,
    configure,
    diagnose,
    extension_status,
    inspect_role,
    ping,
    role_info,
    session_configurator,
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


def test_catalog_functions_cannot_be_hijacked(connect: Callable[..., Any], admin: Any, schema: str) -> None:
    """A schema before pg_catalog in search_path with functions of the same names changes nothing."""
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
        "to_regtype(text)",
        "pg_backend_pid()",
    ):
        returns = "text"
        admin.execute(
            f"CREATE FUNCTION {schema}.{signature} RETURNS {returns} LANGUAGE plpgsql AS"
            " $$BEGIN RAISE EXCEPTION 'hijacked'; END$$"
        )
    admin.execute(f"GRANT USAGE ON SCHEMA {schema} TO PUBLIC")
    conn = connect(autocommit=True, options=f"-c search_path={schema},pg_catalog")
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("SELECT current_setting('server_version_num')")  # the trap works for unqualified calls
    assert ping(conn).ok
    diagnosis = diagnose(conn)
    assert diagnosis.schema == schema
    assert extension_status(conn, "plpgsql", probe_type="int4").state == "active"
    assert role_info(conn).superuser and role_info(conn, owner_scope="rls").superuser
    assert inspect_role(conn, conn.info.user, schema=schema).tables == 1
    configure(conn, SessionSettings(statement_timeout_ms=1_000, search_path=schema))
    assert _value(conn, "SELECT pg_catalog.current_setting('statement_timeout')") == "1s"
    session_configurator(SessionSettings(lock_timeout_ms=2_000))(conn)
    assert _value(conn, "SELECT pg_catalog.current_setting('lock_timeout')") == "2s"
