"""TAB-015 against a real PostgreSQL: ``ping`` (TAB-T-P06..P09), ``diagnose`` (P10..P12), ``extension_status``
(P13..P15).

Runs as the superuser of ``TABRION_TEST_POSTGRES`` and as roles without superuser; extension and privilege tests
work in a throwaway database (``scratch_database``). The pgvector job (``TABRION_REQUIRE_PGVECTOR=1``) also checks
the extension ``vector`` with ``probe_type="vector"``, the Nodestro case.
"""

# The SQL is built from names this file generates (uuid hex) and fixed text, never from outside input.

from __future__ import annotations

from collections.abc import Callable, Iterator
import os
import threading
import time
from typing import Any

import pytest

from tabrion.errors import Kind
from tabrion.postgres import diagnose, extension_status, ping

from .conftest import Role, new_name, with_params

psycopg = pytest.importorskip("psycopg")
psycopg_pool = pytest.importorskip("psycopg_pool")
from psycopg import pq  # noqa: E402
from psycopg.rows import tuple_row  # noqa: E402

pytestmark = pytest.mark.postgres

IDLE = pq.TransactionStatus.IDLE


def _setting(conn: Any, name: str) -> str:
    with psycopg.Cursor(conn, row_factory=tuple_row) as cur:
        cur.execute("SELECT pg_catalog.current_setting(%s)", (name,))
        return str(cur.fetchone()[0])


# ---------------------------------------------------------------------------------------------------------------------
# ping
# ---------------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("autocommit", [True, False])
def test_p06_ok(connect: Callable[..., Any], autocommit: bool) -> None:
    conn = connect(autocommit=autocommit)
    before = _setting(conn, "statement_timeout")
    if not autocommit:
        conn.rollback()
    result = ping(conn, timeout=2.0)
    assert result.ok
    assert result.classification is None
    assert result.server_version == conn.info.server_version
    assert result.in_recovery is False
    assert result.latency_ms is not None and 0 <= result.latency_ms < 2_000
    assert conn.info.transaction_status == IDLE
    assert _setting(conn, "statement_timeout") == before, "SET LOCAL must end with ping's own transaction"


def test_p06_ok_as_runtime_role(connect: Callable[..., Any], make_role: Callable[..., Role]) -> None:
    result = ping(connect(make_role().url, autocommit=True))
    assert result.ok and result.server_version is not None


def test_p07_terminated_backend(connect: Callable[..., Any], admin: Any) -> None:
    """The server ended the session (as on a shutdown): ok=False, CONNECTION, never an exception."""
    victim = connect(autocommit=True)
    admin.execute("SELECT pg_terminate_backend(%s)", (victim.info.backend_pid,))
    time.sleep(0.2)
    result = ping(victim, timeout=2.0)
    assert not result.ok
    assert result.classification is not None and result.classification.kind is Kind.CONNECTION
    assert result.latency_ms is None and result.server_version is None and result.in_recovery is None


def test_p07_closed_connection(connect: Callable[..., Any]) -> None:
    conn = connect(autocommit=True)
    conn.close()
    result = ping(conn)
    assert not result.ok
    assert result.classification is not None and result.classification.kind is Kind.CONNECTION


def test_p07_server_unreachable_through_a_pool(admin_url: str) -> None:
    """A pool whose server does not answer: the checkout fails after ``timeout``; nothing was sent."""
    unreachable = with_params(admin_url, host="127.0.0.1", port="1", connect_timeout="1")
    pool = psycopg_pool.ConnectionPool(unreachable, min_size=1, open=False)
    pool.open(wait=False)
    try:
        started = time.monotonic()
        result = ping(pool, timeout=1.0)
        elapsed = time.monotonic() - started
    finally:
        pool.close()
    assert not result.ok
    assert result.classification is not None
    assert result.classification.kind is Kind.CONNECTION
    assert result.classification.outcome_unknown is False  # sent=False: a checkout error
    assert elapsed < 5


def test_p08_statement_timeout(connect: Callable[..., Any], admin_url: str) -> None:
    """The SELECT waits on the server (a safe snapshot for SERIALIZABLE READ ONLY DEFERRABLE while a serializable
    writer is open) longer than ``timeout``: the server cancels it, ping gives TIMEOUT, and the connection is IDLE
    and usable afterwards with its own statement_timeout unchanged."""
    writer = connect()
    writer.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
    writer.execute("CREATE TEMP TABLE p08 (x int)")
    writer.execute("INSERT INTO p08 VALUES (1)")
    deferred = connect(
        autocommit=True,
        options="-c default_transaction_isolation=serializable -c default_transaction_read_only=on"
        " -c default_transaction_deferrable=on",
    )
    # Without the server-side limit the SELECT would wait until the writer ends: end it after 5 s, so a ping that
    # ignores its timeout fails this test instead of hanging.
    release = threading.Timer(5.0, writer.rollback)
    release.start()
    try:
        started = time.monotonic()
        result = ping(deferred, timeout=0.5)
        elapsed = time.monotonic() - started
    finally:
        release.cancel()
        release.join()
    if writer.info.transaction_status != IDLE:
        writer.rollback()
    assert not result.ok
    assert result.classification is not None and result.classification.kind is Kind.TIMEOUT
    assert result.classification.sqlstate == "57014"
    assert 0.4 < elapsed < 4
    assert deferred.info.transaction_status == IDLE
    assert _setting(deferred, "statement_timeout") == "0"
    assert ping(deferred).ok


def test_p08_timeout_control_without_the_writer(connect: Callable[..., Any]) -> None:
    """Control for P08: the same deferred connection answers at once when no serializable writer is open."""
    deferred = connect(
        autocommit=True,
        options="-c default_transaction_isolation=serializable -c default_transaction_read_only=on"
        " -c default_transaction_deferrable=on",
    )
    assert ping(deferred, timeout=2.0).ok


def test_p08_inside_a_failed_transaction(connect: Callable[..., Any]) -> None:
    """A caller's aborted transaction: ok=False, and the transaction stays as the caller left it (T-NOTX)."""
    conn = connect()
    with pytest.raises(psycopg.errors.DivisionByZero):
        conn.execute("SELECT 1 / 0")
    assert conn.info.transaction_status == pq.TransactionStatus.INERROR
    result = ping(conn)
    assert not result.ok
    assert conn.info.transaction_status == pq.TransactionStatus.INERROR
    conn.rollback()


def test_p09_pool_source(admin_url: str) -> None:
    with psycopg_pool.ConnectionPool(admin_url, min_size=1, max_size=1, open=True) as pool:
        result = ping(pool, timeout=2.0)
        assert result.ok and result.server_version is not None
        stats = pool.get_stats()
        assert stats.get("requests_num", 1) >= 1
        with pool.connection() as conn:
            assert conn.info.transaction_status == IDLE


def test_p09_pool_exhausted(admin_url: str) -> None:
    """No connection free within ``timeout``: ok=False (PoolTimeout is a CONNECTION error, nothing sent)."""
    with psycopg_pool.ConnectionPool(admin_url, min_size=1, max_size=1, open=True) as pool:
        holding = threading.Event()
        release = threading.Event()

        def hold() -> None:
            with pool.connection():
                holding.set()
                release.wait(10)

        thread = threading.Thread(target=hold)
        thread.start()
        holding.wait(10)
        try:
            result = ping(pool, timeout=0.3)
        finally:
            release.set()
            thread.join(10)
    assert not result.ok
    assert result.classification is not None and result.classification.kind is Kind.CONNECTION
    assert result.classification.outcome_unknown is False


def test_p09_pool_like_object(connect: Callable[..., Any]) -> None:
    """Any object with ``connection(timeout=...)`` (Nodestro's SharedPool) is a pool source."""
    from contextlib import contextmanager

    conn = connect(autocommit=True)
    asked: list[float | None] = []

    class SharedPool:
        @contextmanager
        def connection(self, timeout: float | None = None) -> Iterator[Any]:
            asked.append(timeout)
            yield conn

    assert ping(SharedPool(), timeout=3.0).ok
    assert asked == [3.0]

    class Broken:
        @contextmanager
        def connection(self, timeout: float | None = None) -> Iterator[Any]:
            raise RuntimeError("The database is closed")
            yield conn  # pragma: no cover

    result = ping(Broken())
    assert not result.ok and result.classification is not None and result.classification.kind is Kind.UNKNOWN

    class LendsSomethingElse:
        @contextmanager
        def connection(self, timeout: float | None = None) -> Iterator[Any]:
            yield object()

    with pytest.raises(TypeError):
        ping(LendsSomethingElse())


# ---------------------------------------------------------------------------------------------------------------------
# diagnose
# ---------------------------------------------------------------------------------------------------------------------


def test_p10_superuser(connect: Callable[..., Any], schema: str) -> None:
    conn = connect(autocommit=True, options=f"-c search_path={schema} -c statement_timeout=90s -c lock_timeout=2min")
    d = diagnose(conn)
    assert d.server_version == conn.info.server_version
    assert d.server_version_text == _setting(conn, "server_version")
    assert d.database == conn.info.dbname
    assert d.user == conn.info.user
    assert d.schema == schema
    assert d.search_path == schema
    assert d.in_recovery is False
    assert d.statement_timeout_ms == 90_000
    assert d.lock_timeout_ms == 120_000
    with psycopg.Cursor(conn, row_factory=tuple_row) as cur:
        cur.execute("SELECT setting::int FROM pg_settings WHERE name = 'idle_in_transaction_session_timeout'")
        assert d.idle_in_transaction_timeout_ms == cur.fetchone()[0]
    assert d.archive_mode in ("on", "off", "always")
    assert d.wal_level in ("minimal", "replica", "logical")
    assert d.ssl in (True, False)
    assert d.role.superuser is True and d.role.role == conn.info.user
    assert d.role.table_owner is True  # the schema has a table, and a superuser owns everything
    assert d.host_is_local is True
    assert conn.info.transaction_status == IDLE


def test_p10_application_name_and_no_schema(connect: Callable[..., Any]) -> None:
    conn = connect(options="-c search_path=t_does_not_exist -c application_name=tabrion-p10")
    d = diagnose(conn)
    assert d.schema is None
    assert d.application_name == "tabrion-p10"
    assert d.role.table_owner is False
    assert conn.info.transaction_status == IDLE


def test_p11_without_read_privileges(
    connect: Callable[..., Any], scratch_database: str, make_role: Callable[..., Role]
) -> None:
    """Fields without read privilege are None (pg_stat_ssl, pg_settings revoked in a throwaway database); the
    timeouts then come from current_setting in every unit PostgreSQL prints."""
    with psycopg.connect(scratch_database, autocommit=True) as owner:
        owner.execute("REVOKE SELECT ON pg_catalog.pg_settings FROM PUBLIC")
        owner.execute("REVOKE SELECT ON pg_catalog.pg_stat_ssl FROM PUBLIC")
    role = make_role(dbname=psycopg.conninfo.conninfo_to_dict(scratch_database)["dbname"])
    conn = connect(
        role.url,
        options="-c statement_timeout=90s -c lock_timeout=2min -c idle_in_transaction_session_timeout=1500ms",
    )
    d = diagnose(conn)
    assert (d.ssl, d.archive_mode, d.wal_level) == (None, None, None)
    assert (d.statement_timeout_ms, d.lock_timeout_ms, d.idle_in_transaction_timeout_ms) == (90_000, 120_000, 1_500)
    assert d.role.superuser is False and d.role.bypass_rls is False and d.role.restricted
    assert d.user == role.name
    assert conn.info.transaction_status == IDLE


def test_p11_runtime_role_with_privileges(connect: Callable[..., Any], make_role: Callable[..., Role]) -> None:
    role = make_role()
    conn = connect(role.url, autocommit=True, options="-c statement_timeout=1h")
    d = diagnose(conn)
    assert d.statement_timeout_ms == 3_600_000
    assert d.wal_level is not None and d.ssl is not None
    assert d.role == type(d.role)(role=role.name, superuser=False, bypass_rls=False, table_owner=False)


@pytest.mark.parametrize("sslmode", ["disable", "prefer"])
def test_p12_ssl_and_local_host(connect: Callable[..., Any], admin_url: str, sslmode: str) -> None:
    conn = connect(with_params(admin_url, sslmode=sslmode), autocommit=True)
    d = diagnose(conn)
    with psycopg.Cursor(conn, row_factory=tuple_row) as cur:
        cur.execute("SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()")
        assert d.ssl is cur.fetchone()[0]
    if sslmode == "disable":
        assert d.ssl is False
    assert d.host_is_local is True  # 127.0.0.1 in every job


# ---------------------------------------------------------------------------------------------------------------------
# extension_status
# ---------------------------------------------------------------------------------------------------------------------

NODESTRO_PGVECTOR_STATUS = (
    # nodestro-runtime storage_postgres.py pgvector_status (master 40511eb), with the extension name and the type as
    # parameters instead of 'vector': the reference for TAB-T-P15.
    "SELECT to_regtype(%(type)s) IS NOT NULL, current_schema()::text,"
    " (SELECT e.extversion::text FROM pg_extension e WHERE e.extname = %(ext)s),"
    " (SELECT n.nspname::text FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace"
    "   WHERE e.extname = %(ext)s),"
    " (SELECT a.default_version::text FROM pg_available_extensions a WHERE a.name = %(ext)s)"
)


def _nodestro_state(conn: Any, ext: str, type_name: str) -> dict[str, Any]:
    with psycopg.Cursor(conn, row_factory=tuple_row) as cur:
        cur.execute(NODESTRO_PGVECTOR_STATUS, {"type": type_name, "ext": ext})
        usable, schema, version, extension_schema, available = cur.fetchone()
    if not conn.autocommit:
        conn.rollback()
    if usable:
        state = "active"
    elif version is not None:
        state = "other_schema"
    elif available is not None:
        state = "installable"
    else:
        state = "unavailable"
    return {
        "state": state,
        "installed_version": version,
        "available_version": available,
        "extension_schema": extension_schema,
        "schema": schema,
    }


def _tabrion_state(conn: Any, ext: str, type_name: str) -> dict[str, Any]:
    s = extension_status(conn, ext, probe_type=type_name)
    return {
        "state": s.state,
        "installed_version": s.installed_version,
        "available_version": s.available_version,
        "extension_schema": s.extension_schema,
        "schema": s.schema,
    }


@pytest.fixture
def scratch(scratch_database: str) -> Iterator[Any]:
    with psycopg.connect(scratch_database, autocommit=True) as conn:
        yield conn


def test_p13_active_installable_unavailable(scratch: Any, scratch_database: str) -> None:
    builtin = extension_status(scratch, "plpgsql")
    assert builtin.state == "active" and builtin.command is None
    assert builtin.extension_schema == "pg_catalog" and builtin.installed_version is not None
    missing = extension_status(scratch, "t_no_such_extension")
    assert missing.state == "unavailable"
    assert (missing.command, missing.installed_version, missing.available_version) == (None, None, None)
    trgm = extension_status(scratch, "pg_trgm", probe_type="gtrgm")
    assert trgm.state == "installable"
    assert trgm.command == 'CREATE EXTENSION "pg_trgm" SCHEMA "public";'
    assert trgm.available_version is not None and trgm.installed_version is None
    assert trgm.schema == "public"
    scratch.execute(trgm.command)
    now = extension_status(scratch, "pg_trgm", probe_type="gtrgm")
    assert now.state == "active" and now.command is None
    assert now.installed_version == trgm.available_version and now.extension_schema == "public"


def test_p13_probe_type_must_resolve(scratch: Any) -> None:
    """Installed and visible, but the probe type does not resolve: not active (Nodestro's to_regtype check)."""
    scratch.execute("CREATE EXTENSION pg_trgm")
    assert extension_status(scratch, "pg_trgm").state == "active"
    assert extension_status(scratch, "pg_trgm", probe_type="gtrgm").state == "active"
    assert extension_status(scratch, "pg_trgm", probe_type="t_no_such_type").state == "other_schema"


def test_p13_runtime_role_reads_the_state(
    connect: Callable[..., Any], scratch: Any, scratch_database: str, make_role: Callable[..., Role]
) -> None:
    scratch.execute("CREATE EXTENSION pg_trgm")
    role = make_role(dbname=psycopg.conninfo.conninfo_to_dict(scratch_database)["dbname"])
    conn = connect(role.url)
    status = extension_status(conn, "pg_trgm", probe_type="gtrgm")
    assert status.state == "active"
    assert conn.info.transaction_status == IDLE


def test_p14_other_schema(connect: Callable[..., Any], scratch: Any, scratch_database: str) -> None:
    other = new_name()
    scratch.execute(f"CREATE SCHEMA {other}")
    scratch.execute(f"CREATE EXTENSION pg_trgm SCHEMA {other}")
    conn = connect(scratch_database, autocommit=True, options="-c search_path=public")
    assert extension_status(conn, "pg_trgm").state == "other_schema"  # without a probe type, too
    status = extension_status(conn, "pg_trgm", probe_type="gtrgm")
    assert status.state == "other_schema"
    assert status.extension_schema == other and status.schema == "public"
    assert status.command == 'ALTER EXTENSION "pg_trgm" SET SCHEMA "public";'
    conn.execute(status.command)
    assert extension_status(conn, "pg_trgm", probe_type="gtrgm").state == "active"


def test_p14_no_current_schema_names_public(connect: Callable[..., Any], scratch_database: str) -> None:
    conn = connect(scratch_database, autocommit=True, options="-c search_path=t_does_not_exist")
    status = extension_status(conn, "pg_trgm")
    assert status.schema is None
    assert status.command == 'CREATE EXTENSION "pg_trgm" SCHEMA "public";'


def test_p14_command_quotes_the_schema(connect: Callable[..., Any], scratch: Any, scratch_database: str) -> None:
    odd = 'T "odd" ' + os.urandom(3).hex()
    scratch.execute(psycopg.sql.SQL("CREATE SCHEMA {}").format(psycopg.sql.Identifier(odd)))
    conn = connect(scratch_database, autocommit=True)
    conn.execute(psycopg.sql.SQL("SET search_path = {}").format(psycopg.sql.Identifier(odd)))
    status = extension_status(conn, "pg_trgm")
    assert status.command == 'CREATE EXTENSION "pg_trgm" SCHEMA "' + odd.replace('"', '""') + '";'
    conn.execute(status.command)
    assert extension_status(conn, "pg_trgm").state == "active"


def _p15_scenarios(conn: Any, scratch_database: str, connect: Callable[..., Any], ext: str, type_name: str) -> None:
    """Drive one extension through the four states and compare with Nodestro's semantics after each step."""
    seen: list[str] = []

    def compare(c: Any) -> None:
        mine = _tabrion_state(c, ext, type_name)
        assert mine == _nodestro_state(c, ext, type_name)
        seen.append(mine["state"])

    compare(conn)  # installable
    other = new_name()
    conn.execute(f"CREATE SCHEMA {other}")
    conn.execute(f"CREATE EXTENSION {ext} SCHEMA {other}")
    compare(connect(scratch_database, autocommit=True, options="-c search_path=public"))  # other_schema
    compare(connect(scratch_database, autocommit=False, options=f"-c search_path=public,{other}"))  # active
    assert seen == ["installable", "other_schema", "active"]
    missing = "t_no_such_extension"
    assert _tabrion_state(conn, missing, "t_no_such_type") == _nodestro_state(conn, missing, "t_no_such_type")
    assert _tabrion_state(conn, missing, "t_no_such_type")["state"] == "unavailable"


def test_p15_same_states_as_nodestro(connect: Callable[..., Any], scratch: Any, scratch_database: str) -> None:
    _p15_scenarios(scratch, scratch_database, connect, "pg_trgm", "gtrgm")


def test_p15_pgvector(connect: Callable[..., Any], scratch: Any, scratch_database: str) -> None:
    """The Nodestro call ``extension_status(conn, "vector", probe_type="vector")``: the pgvector job must run it."""
    available = scratch.execute("SELECT count(*) FROM pg_available_extensions WHERE name = 'vector'").fetchone()[0]
    if not available:
        if os.environ.get("TABRION_REQUIRE_PGVECTOR") == "1":
            pytest.fail("TABRION_REQUIRE_PGVECTOR=1 but the server has no pgvector")
        assert extension_status(scratch, "vector", probe_type="vector").state == "unavailable"
        assert _nodestro_state(scratch, "vector", "vector")["state"] == "unavailable"
        return
    _p15_scenarios(scratch, scratch_database, connect, "vector", "vector")
