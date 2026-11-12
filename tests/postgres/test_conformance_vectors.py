"""TAB-T-V05 (PostgreSQL part): Tabrion passes every ``postgres`` vector of the Levuvo Database Profile (TAB-050).

The actions follow SPEC.md appendix A. Every role, schema and lock key is new per vector (the server may be shared),
and everything is created with the prefix of this run and dropped by the fixtures of ``conftest.py``.
"""

# The SQL is built from names this file generates (uuid hex) and fixed text, never from outside input.

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
import dataclasses
import threading
import time
from typing import Any
import uuid

import pytest

from tabrion.errors import TabrionError
from tabrion.postgres import SessionLock, SessionSettings, configure, extension_status, grant_runtime_role, role_info
from tabrion.testing import PostgresSchema
from tests.support.conformance import Vector, load_all

from .conftest import Role

psycopg = pytest.importorskip("psycopg")
from psycopg import sql  # noqa: E402

pytestmark = [pytest.mark.postgres, pytest.mark.conformance]

VECTORS = [v for v in load_all() if v.engine == "postgres"]
PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE")

Observed = dict[str, Any]


@dataclasses.dataclass
class Context:
    admin_url: str
    schema: PostgresSchema
    make_role: Callable[..., Role]
    connect: Callable[..., Any]


def _setting(conn: Any, name: str) -> str | None:
    row = conn.execute("SELECT current_setting(%s, true)", (name,)).fetchone()
    return None if row is None else row[0]


def session_settings(vector: Vector, ctx: Context) -> Observed:
    conn = ctx.connect(autocommit=True)
    for statement in vector.setup:
        conn.execute(statement)
    names = vector.expect.get("unchanged", [])
    before = {name: _setting(conn, name) for name in names}
    settings: SessionSettings
    extra = vector.args.get("extra_field")
    if extra is not None:
        cls = dataclasses.make_dataclass(
            "LDPSettings",
            [(extra, "str | None", dataclasses.field(default="off"))],
            bases=(SessionSettings,),
            frozen=True,
        )
        settings = cls(**vector.args["settings"])
    else:
        settings = SessionSettings(**vector.args["settings"])
    try:
        configure(conn, settings)
    except TabrionError as exc:
        return {"error": exc.code}
    observed: Observed = {"error": None}
    observed["show"] = {name: _setting(conn, name) for name in vector.expect.get("show", {})}
    observed["unchanged"] = [name for name in names if _setting(conn, name) == before[name]]
    positive = []
    for name in vector.expect.get("positive", []):
        (value,) = conn.execute("SELECT setting::bigint FROM pg_settings WHERE name = %s", (name,)).fetchone()
        if value > 0:
            positive.append(name)
    observed["positive"] = positive
    return observed


def role_report(vector: Vector, ctx: Context) -> Observed:
    attributes = vector.args["attributes"]
    schema = ctx.schema.name
    if attributes == "SUPERUSER":
        url = ctx.admin_url  # the suite's own role is a superuser (conftest.py)
    else:
        role = ctx.make_role(attributes)
        url = role.url
        admin = ctx.connect(autocommit=True)
        admin.execute(
            sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(sql.Identifier(schema), sql.Identifier(role.name))
        )
        if vector.args["owns_rls_table"]:
            table = sql.Identifier(schema, "ldp_rls")
            admin.execute(sql.SQL("CREATE TABLE {} (id int)").format(table))
            admin.execute(sql.SQL("ALTER TABLE {} ENABLE ROW LEVEL SECURITY").format(table))
            admin.execute(sql.SQL("ALTER TABLE {} OWNER TO {}").format(table, sql.Identifier(role.name)))
    conn = ctx.connect(url, autocommit=True, options=f"-c search_path={schema}")
    info = role_info(conn)
    return {
        "restricted": info.restricted,
        "superuser": info.superuser,
        "bypass_rls": info.bypass_rls,
        "table_owner": info.table_owner,
    }


def grant_runtime(vector: Vector, ctx: Context) -> Observed:
    schema = ctx.schema.name
    admin = ctx.connect(autocommit=True)
    for table in vector.args["tables"]:
        admin.execute(sql.SQL("CREATE TABLE {} (id int)").format(sql.Identifier(schema, table)))
    role = ctx.make_role()
    observed: Observed = {"error": None}
    if vector.args["in_transaction"]:
        conn = ctx.connect()
        conn.execute("SELECT 1")
        try:
            grant_runtime_role(conn, role.name, schema=schema, protected_tables=vector.args["protected_tables"])
        except TabrionError as exc:
            observed["error"] = exc.code
        observed["transaction_status"] = conn.info.transaction_status.name
        conn.rollback()
        return observed
    grant_runtime_role(admin, role.name, schema=schema, protected_tables=vector.args["protected_tables"])
    privileges: dict[str, list[str]] = {}
    for table in vector.args["tables"]:
        held = [
            p
            for p in PRIVILEGES
            if admin.execute("SELECT has_table_privilege(%s, %s, %s)", (role.name, f"{schema}.{table}", p)).fetchone()[
                0
            ]
        ]
        privileges[table] = sorted(held)
    observed["privileges"] = privileges
    return observed


def _activity(admin: Any, application_name: str) -> tuple[int, str, bool]:
    row = admin.execute(
        "SELECT pid, state, backend_xmin IS NOT NULL FROM pg_stat_activity WHERE application_name = %s",
        (application_name,),
    ).fetchone()
    assert row is not None, "the lock session is not connected"
    return int(row[0]), str(row[1]), bool(row[2])


def _lock(ctx: Context, application_name: str) -> SessionLock:
    """A ``SessionLock`` whose backend ``pg_stat_activity`` finds by its ``application_name``."""
    settings = SessionSettings(application_name=application_name)
    return SessionLock(ctx.admin_url, configure=lambda conn: configure(conn, settings))


WAITER_SAMPLES = 10
IN_FLIGHT_LIMIT = 1.0
"""Seconds one statement of the waiter may stay in flight before its sample counts as busy (a poll takes ms)."""
IN_FLIGHT_WAITS = (None, "Client", "LWLock")
"""``wait_event_type`` of a running poll statement: on the CPU, reading the rest of the client's message, or an internal
latch. A blocking lock wait (``Lock``), a server-side sleep (``Timeout``) and every other wait count as busy at once."""


def _waiter_row(admin: Any, application_name: str) -> tuple[str, bool, str | None, Any]:
    row = admin.execute(
        "SELECT state, backend_xmin IS NOT NULL, wait_event_type, query_start"
        " FROM pg_stat_activity WHERE application_name = %s",
        (application_name,),
    ).fetchone()
    assert row is not None, "the lock session is not connected"
    return str(row[0]), bool(row[1]), row[2], row[3]


def _waiter_sample(admin: Any, application_name: str) -> str:
    """One sample of the waiting backend between two polls: ``idle``, ``snapshot`` or the other ``state``.

    A sample that meets a poll statement in flight (``active``, waiting for nothing but the CPU, the client or an
    internal latch) is taken again once that statement has ended, so the samples never depend on where the sampler
    falls in the poll cycle (the CI flake of LDP-PGL-005: sampler and poller both slept 0.1 s and fell into step). A
    statement still in flight after ``IN_FLIGHT_LIMIT`` seconds counts as busy.
    """
    deadline = time.monotonic() + IN_FLIGHT_LIMIT
    while True:
        state, snapshot, wait_type, _ = _waiter_row(admin, application_name)
        if state == "active" and wait_type in IN_FLIGHT_WAITS and time.monotonic() < deadline:
            time.sleep(0.002)
            continue
        if snapshot:
            return "snapshot"
        return state


def _waiter_state(admin: Any, lock: SessionLock, application_name: str, key: int, holders: list[SessionLock]) -> str:
    """SPEC appendix A ``waiter_state``: sample the backend of ``lock`` while it waits for ``key``, which a holder has.

    Deterministic (bounded waits only): the samples start once the waiter's first poll has reached the server, each
    sample is taken between two polls (``_waiter_sample``), and the waiter is still waiting at the last sample. It
    waits without a timeout of its own that the samples could outlast; afterwards the holder hands the key over to end
    the wait and takes it back, so the sessions hold what they held before the step.
    """
    lock.try_acquire(key + 1)  # open the session's connection before it waits
    lock.release(key + 1)
    _, _, _, before = _waiter_row(admin, application_name)
    outcome: list[Any] = []

    def wait() -> None:
        try:
            outcome.append(lock.acquire(key, timeout=30.0, poll_interval=0.1))
        except BaseException as exc:  # reported below, never lost in the thread
            outcome.append(exc)

    waiter = threading.Thread(target=wait, daemon=True)
    waiter.start()
    states: Counter[str] = Counter()
    try:
        deadline = time.monotonic() + 10
        while _waiter_row(admin, application_name)[3] == before:  # the first poll has not reached the server yet
            assert waiter.is_alive() and time.monotonic() < deadline, f"the waiter did not poll: {outcome}"
            time.sleep(0.01)
        for _ in range(WAITER_SAMPLES):
            time.sleep(0.1)
            states[_waiter_sample(admin, application_name)] += 1
        still_waiting = waiter.is_alive()
    finally:
        # End the wait: the holder releases, the waiter takes the key and gives it back, the holder takes it again.
        held = [h for h in holders if h.holds(key)]
        for holder in held:
            holder.release(key)
        waiter.join(timeout=10)
        if outcome == [True]:
            lock.release(key)
        for holder in held:
            assert holder.try_acquire(key), "the holder could not take the key back"
    assert not waiter.is_alive(), "the waiter did not end"
    if not still_waiting:
        return f"the wait ended before the last sample: {outcome}"
    # Every sample must be idle without a snapshot. A blocking wait, an open transaction or snapshot, or a statement
    # that stays open between polls shows in every sample.
    busy = sum(count for state, count in states.items() if state != "idle")
    return "idle" if busy == 0 else f"open in {busy} of {sum(states.values())} samples: {dict(states)}"


def lock_acquire(vector: Vector, ctx: Context) -> Observed:
    key = uuid.uuid4().int % (2**62)
    names = {s: f"ldp-{s}-{uuid.uuid4().hex[:12]}" for s in ("a", "b")}
    sessions = {s: _lock(ctx, n) for s, n in names.items()}
    admin = ctx.connect(autocommit=True)
    results: list[Any] = []
    try:
        for session, operation in vector.args["steps"]:
            lock = sessions[session]
            if operation == "try_acquire":
                results.append(lock.try_acquire(key))
            elif operation == "acquire_timeout_0.3":
                results.append(lock.acquire(key, timeout=0.3, poll_interval=0.05))
            elif operation == "release":
                lock.release(key)
                results.append(None)
            elif operation == "holds":
                results.append(lock.holds(key))
            elif operation == "waiter_state":
                holders = [lock_ for s, lock_ in sessions.items() if s != session]
                results.append(_waiter_state(admin, lock, names[session], key, holders))
            else:
                raise AssertionError(f"{vector.id}: unknown lock operation {operation!r}")
    finally:
        for lock in sessions.values():
            lock.close()
    return {"results": results}


def lock_connection_lost(vector: Vector, ctx: Context) -> Observed:
    key = uuid.uuid4().int % (2**62)
    name = f"ldp-lost-{uuid.uuid4().hex[:12]}"
    admin = ctx.connect(autocommit=True)
    with _lock(ctx, name) as lock:
        assert lock.try_acquire(key)
        pid, _, _ = _activity(admin, name)
        admin.execute("SELECT pg_terminate_backend(%s)", (pid,))
        deadline = time.monotonic() + 10
        while admin.execute("SELECT count(*) FROM pg_stat_activity WHERE pid = %s", (pid,)).fetchone()[0]:
            assert time.monotonic() < deadline, "the backend did not end"
            time.sleep(0.05)
        return {"holds_after_loss": lock.holds(key)}


def extension(vector: Vector, ctx: Context) -> Observed:
    status = extension_status(ctx.connect(autocommit=True), vector.args["name"])
    return {
        "state": status.state,
        "command": status.command,
        "has_installed_version": status.installed_version is not None,
    }


HANDLERS: dict[str, Callable[[Vector, Context], Observed]] = {
    "session_settings": session_settings,
    "role_report": role_report,
    "grant_runtime_role": grant_runtime,
    "lock_acquire": lock_acquire,
    "lock_connection_lost": lock_connection_lost,
    "extension_status": extension,
}


@pytest.mark.parametrize("vector", VECTORS, ids=[v.id for v in VECTORS])
def test_vector(
    vector: Vector,
    admin_url: str,
    tabrion_postgres_schema: PostgresSchema,
    make_role: Callable[..., Role],
    connect: Callable[..., Any],
    postgres_server_version: int,
) -> None:
    if vector.min_server_version is not None and postgres_server_version < vector.min_server_version:
        pytest.skip(f"{vector.id} needs server version {vector.min_server_version}")
    observed = HANDLERS[vector.action](vector, Context(admin_url, tabrion_postgres_schema, make_role, connect))
    for key, expected in vector.expect.items():
        assert key in observed, f"{vector.id}: the action reports no {key!r}"
        assert observed[key] == expected, f"{vector.id} ({vector.rule}): {key}"
