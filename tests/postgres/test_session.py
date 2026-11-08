"""TAB-015 against a real PostgreSQL: ``SessionSettings``, ``configure`` and ``session_configurator``.

Contract section 4.1, tests TAB-T-P01..P05 and P16; R-SET (TAB-T-G03). Runs as the superuser of
``TABRION_TEST_POSTGRES`` and as a role without superuser (``make_role``).
"""

from __future__ import annotations

from collections.abc import Callable
import dataclasses
import os
from pathlib import Path
import re
import tempfile
import threading
from typing import Any

import pytest

from tabrion.errors import ConfigurationError
from tabrion.postgres import SessionSettings, configure, session_configurator

from .conftest import Role

psycopg = pytest.importorskip("psycopg")
psycopg_pool = pytest.importorskip("psycopg_pool")
from psycopg import pq  # noqa: E402
from psycopg.rows import dict_row, tuple_row  # noqa: E402

pytestmark = pytest.mark.postgres

IDLE = pq.TransactionStatus.IDLE
INTRANS = pq.TransactionStatus.INTRANS

SHOWN = (
    "statement_timeout",
    "lock_timeout",
    "idle_in_transaction_session_timeout",
    "application_name",
    "search_path",
    "TimeZone",
)
ALLOWED = {*SHOWN, "transaction_timeout"}


def _show(conn: Any, name: str) -> str:
    with psycopg.Cursor(conn, row_factory=tuple_row) as cur:
        cur.execute("SELECT pg_catalog.current_setting(%s)", (name,))
        row = cur.fetchone()
    return str(row[0])


def _ms(conn: Any, name: str) -> int:
    with psycopg.Cursor(conn, row_factory=tuple_row) as cur:
        cur.execute("SELECT setting::int FROM pg_catalog.pg_settings WHERE name = %s", (name,))
        row = cur.fetchone()
    return int(row[0])


@pytest.fixture(params=["superuser", "runtime role"])
def url(request: pytest.FixtureRequest, admin_url: str, make_role: Callable[..., Role]) -> str:
    """Every test of this module runs as the superuser and as a role without superuser."""
    return admin_url if request.param == "superuser" else make_role().url


def test_p01_values_through_show(connect: Callable[..., Any], url: str, schema: str, admin: Any) -> None:
    admin.execute(f"GRANT USAGE ON SCHEMA {schema} TO PUBLIC")  # current_schema() needs USAGE
    conn = connect(url, autocommit=True)
    configure(
        conn,
        SessionSettings(
            statement_timeout_ms=12_000,
            lock_timeout_ms=3_000,
            idle_in_transaction_timeout_ms=45_000,
            application_name="tabrion-p01",
            search_path=schema,
            timezone="Europe/Vienna",
        ),
    )
    assert _ms(conn, "statement_timeout") == 12_000
    assert _ms(conn, "lock_timeout") == 3_000
    assert _ms(conn, "idle_in_transaction_session_timeout") == 45_000
    assert _show(conn, "application_name") == "tabrion-p01"
    assert _show(conn, "search_path") == f'"{schema}"'  # quoted as an identifier (as Nodestro sets it)
    assert _show(conn, "TimeZone") == "Europe/Vienna"
    with psycopg.Cursor(conn, row_factory=tuple_row) as cur:
        cur.execute("SELECT pg_catalog.current_schema()")
        assert cur.fetchone() == (schema,)


def test_p01_defaults(connect: Callable[..., Any], url: str) -> None:
    conn = connect(url, autocommit=True)
    configure(conn)
    assert _ms(conn, "statement_timeout") == 30_000
    assert _ms(conn, "lock_timeout") == 10_000
    assert _ms(conn, "idle_in_transaction_session_timeout") == 300_000
    assert _show(conn, "application_name") == "tabrion"
    assert _show(conn, "TimeZone") == "UTC"


def test_p01_search_path_is_quoted(connect: Callable[..., Any], admin: Any) -> None:
    """A mixed-case schema name is an identifier, not folded to lower case (quoted as Nodestro does)."""
    name = "T_Mixed_" + os.urandom(4).hex()
    admin.execute(f'CREATE SCHEMA "{name}"')
    try:
        conn = connect(autocommit=True)
        configure(conn, dataclasses.replace(SessionSettings.unchanged(), search_path=name))
        with psycopg.Cursor(conn, row_factory=tuple_row) as cur:
            cur.execute("SELECT pg_catalog.current_schema()")
            assert cur.fetchone() == (name,)
    finally:
        admin.execute(f'DROP SCHEMA "{name}"')


def test_p01_unchanged_sets_nothing(connect: Callable[..., Any], url: str) -> None:
    conn = connect(url, autocommit=True)
    before = {name: _show(conn, name) for name in SHOWN}
    configure(conn, SessionSettings.unchanged())
    assert {name: _show(conn, name) for name in SHOWN} == before
    assert conn.info.transaction_status == IDLE


def test_p01_unchanged_without_autocommit_opens_no_transaction(connect: Callable[..., Any], url: str) -> None:
    conn = connect(url)
    configure(conn, SessionSettings.unchanged())
    assert conn.info.transaction_status == IDLE


def test_p01_transaction_timeout_by_server_version(connect: Callable[..., Any], url: str) -> None:
    """PostgreSQL 17 and later get ``transaction_timeout``; 16 skips it without an error (finding TAB-PG-012)."""
    conn = connect(url, autocommit=True)
    configure(conn, SessionSettings(transaction_timeout_ms=60_000))
    if conn.info.server_version >= 170000:
        assert _ms(conn, "transaction_timeout") == 60_000
    else:
        with psycopg.Cursor(conn, row_factory=tuple_row) as cur:
            cur.execute("SELECT count(*) FROM pg_catalog.pg_settings WHERE name = 'transaction_timeout'")
            assert cur.fetchone() == (0,)
    assert _ms(conn, "statement_timeout") == 30_000


def test_p02_configure_never_commits(connect: Callable[..., Any], url: str) -> None:
    """Without autocommit the SELECT opens a transaction that stays open; a rollback undoes the settings."""
    conn = connect(url)
    before = _ms(conn, "statement_timeout")
    conn.rollback()
    configure(conn, SessionSettings(statement_timeout_ms=7_000))
    assert conn.info.transaction_status == INTRANS
    assert _ms(conn, "statement_timeout") == 7_000
    conn.rollback()
    assert _ms(conn, "statement_timeout") == before
    conn.rollback()
    configure(conn, SessionSettings(statement_timeout_ms=7_000))
    conn.commit()  # the caller commits, as Axentra's _configure does
    assert _ms(conn, "statement_timeout") == 7_000


def test_p02_session_configurator_commits_and_leaves_idle(connect: Callable[..., Any], url: str) -> None:
    conn = connect(url)
    callback = session_configurator(SessionSettings(statement_timeout_ms=8_000))
    callback(conn)
    assert conn.info.transaction_status == IDLE
    conn.rollback()
    assert _ms(conn, "statement_timeout") == 8_000


def test_p02_session_configurator_with_autocommit(connect: Callable[..., Any], url: str) -> None:
    conn = connect(url, autocommit=True)
    session_configurator(SessionSettings(lock_timeout_ms=1_234))(conn)
    assert conn.info.transaction_status == IDLE
    assert _ms(conn, "lock_timeout") == 1_234


def test_p03_inside_an_open_transaction(connect: Callable[..., Any], url: str, schema: str) -> None:
    """In a running transaction: no commit, no rollback, the setting is active; the caller's work stays open."""
    conn = connect(url)
    with psycopg.Cursor(conn, row_factory=tuple_row) as cur:
        cur.execute("SELECT 1")
    assert conn.info.transaction_status == INTRANS
    configure(conn, SessionSettings(statement_timeout_ms=9_000))
    assert conn.info.transaction_status == INTRANS
    assert _ms(conn, "statement_timeout") == 9_000
    conn.rollback()
    assert _ms(conn, "statement_timeout") != 9_000


def test_p03_session_configurator_inside_a_transaction_does_not_commit(
    connect: Callable[..., Any], admin: Any, schema: str
) -> None:
    """The callback commits only a transaction it opened: a caller's open write stays uncommitted."""
    conn = connect()
    with psycopg.Cursor(conn, row_factory=tuple_row) as cur:
        cur.execute(f"INSERT INTO {schema}.parts VALUES (1, 'open')")  # noqa: S608
    session_configurator(SessionSettings(statement_timeout_ms=9_000))(conn)
    assert conn.info.transaction_status == INTRANS
    assert admin.execute(f"SELECT count(*) FROM {schema}.parts").fetchone() == (0,)  # noqa: S608
    conn.rollback()


def test_p04_validation_against_the_server(connect: Callable[..., Any]) -> None:
    """Invalid settings raise ConfigurationError before anything is sent; the server never sees them."""
    conn = connect(autocommit=True)
    for bad in (
        SessionSettings(statement_timeout_ms=-1),
        SessionSettings(search_path="x; RESET ALL"),
        SessionSettings(search_path='a"b'),
        SessionSettings(application_name="x" * 64),
        SessionSettings(application_name="café"),
    ):
        with pytest.raises(ConfigurationError) as info:
            configure(conn, bad)
        assert info.value.code == "postgres.invalid_setting"
    assert conn.info.transaction_status == IDLE


def test_p04_unknown_timezone_is_a_driver_error(connect: Callable[..., Any]) -> None:
    """The server rejects an unknown time zone; the driver error passes through (layer A)."""
    conn = connect(autocommit=True)
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        configure(conn, dataclasses.replace(SessionSettings.unchanged(), timezone="Mars/Olympus"))


def _round_trips(conn: Any, action: Callable[[], None]) -> int:
    """Client messages that end a round trip (``Sync`` or ``Query``), counted with libpq's protocol tracer."""
    with tempfile.TemporaryFile() as trace:
        conn.pgconn.trace(trace.fileno())
        conn.pgconn.set_trace_flags(pq.Trace.SUPPRESS_TIMESTAMPS | pq.Trace.REGRESS_MODE)
        try:
            action()
        finally:
            conn.pgconn.untrace()
        trace.seek(0)
        lines = trace.read().decode("utf-8", "replace").splitlines()
    return sum(1 for line in lines if line.startswith("F\t") and line.split("\t")[2].split(" ")[0] in ("Sync", "Query"))


def test_p05_one_round_trip(connect: Callable[..., Any], schema: str) -> None:
    conn = connect(autocommit=True)
    full = SessionSettings(
        statement_timeout_ms=1,
        lock_timeout_ms=2,
        idle_in_transaction_timeout_ms=3,
        transaction_timeout_ms=4,
        application_name="p05",
        search_path=schema,
        timezone="UTC",
    )
    assert _round_trips(conn, lambda: configure(conn, full)) == 1
    assert _round_trips(conn, lambda: configure(conn, SessionSettings.unchanged())) == 0
    assert _round_trips(conn, lambda: session_configurator(full)(conn)) == 1


def test_p05_tracer_counts_round_trips(connect: Callable[..., Any]) -> None:
    """Positive control for the counter: two statements are two round trips."""
    conn = connect(autocommit=True)

    def two() -> None:
        conn.execute("SELECT 1")
        conn.execute("SELECT 2")

    assert _round_trips(conn, two) == 2


def test_p16_pool_shared_by_two_consumers(admin_url: str, schema: str) -> None:
    """M10: the pool owner's ``session_configurator``; two consumers see the same GUCs, and a transaction-local
    ``set_config('app.tenant_id', ..., true)`` of one consumer is gone when the connection comes back."""
    settings = SessionSettings(statement_timeout_ms=15_000, application_name="tabrion-p16", search_path=schema)
    with psycopg_pool.ConnectionPool(
        admin_url,
        min_size=1,
        max_size=1,  # one connection: both consumers get the same backend
        kwargs={"row_factory": dict_row, "autocommit": False},
        configure=session_configurator(settings),
        open=True,
    ) as pool:
        seen: list[tuple[Any, ...]] = []
        lock = threading.Lock()

        def consumer(tenant: str) -> None:
            with pool.connection(timeout=10) as conn:
                with conn.transaction():
                    before = conn.execute("SELECT current_setting('app.tenant_id', true) AS t").fetchone()["t"]
                    conn.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant,))
                    row = conn.execute(
                        "SELECT pg_backend_pid() AS pid, current_setting('statement_timeout') AS st,"
                        " current_setting('application_name') AS app, current_schema() AS s,"
                        " current_setting('app.tenant_id', true) AS t"
                    ).fetchone()
                with lock:
                    seen.append((before or "", row["pid"], row["st"], row["app"], row["s"], row["t"]))

        for tenant in ("tenant-a", "tenant-b"):
            thread = threading.Thread(target=consumer, args=(tenant,))
            thread.start()
            thread.join(timeout=30)
        with pool.connection(timeout=10) as conn:
            after = conn.execute("SELECT current_setting('app.tenant_id', true) AS t").fetchone()["t"]
            conn.rollback()
    assert len(seen) == 2
    assert seen[0][1] == seen[1][1], "both consumers used the same backend"
    assert {s[2:5] for s in seen} == {("15s", "tabrion-p16", schema)}
    assert [s[0] for s in seen] == ["", ""], "a consumer saw the other one's tenant"
    assert [s[5] for s in seen] == ["tenant-a", "tenant-b"]
    assert (after or "") == ""


def test_g03_allowlist_r_set(connect: Callable[..., Any]) -> None:
    """R-SET: a field outside the allowlist is refused before anything is sent (subclass with an extra field)."""
    from dataclasses import dataclass

    @dataclass(frozen=True, slots=True)
    class Sneaky(SessionSettings):
        role: str | None = "postgres"

    conn = connect(autocommit=True)
    before = _show(conn, "role")
    with pytest.raises(ConfigurationError) as info:
        configure(conn, Sneaky())
    assert info.value.code == "postgres.setting_not_allowed"
    with pytest.raises(ConfigurationError):
        session_configurator(Sneaky())
    assert _show(conn, "role") == before
    # None in the extra field is not a setting: nothing outside the allowlist is sent.
    configure(conn, Sneaky(role=None))


def test_g03_only_allowlisted_names_reach_the_server(connect: Callable[..., Any], schema: str) -> None:
    """The one statement configure sends names exactly the allowlisted settings that are not None."""
    conn = connect(autocommit=True)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "trace"
        with path.open("wb") as trace:
            conn.pgconn.trace(trace.fileno())
            conn.pgconn.set_trace_flags(pq.Trace.SUPPRESS_TIMESTAMPS | pq.Trace.REGRESS_MODE)
            configure(conn, SessionSettings(transaction_timeout_ms=5, search_path=schema))
            conn.pgconn.untrace()
        binds = [line for line in path.read_text("utf-8", "replace").splitlines() if "\tBind\t" in line]
    assert len(binds) == 1
    params = re.findall(r"'([^']*)'", binds[0])
    names = set(params[0::2])
    expected = set(SHOWN) | ({"transaction_timeout"} if conn.info.server_version >= 170000 else set())
    assert names == expected
    assert names <= ALLOWED
