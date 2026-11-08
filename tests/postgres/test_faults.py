"""TAB-034 / QA-3 against a real PostgreSQL: the internal fault proxy ``tabrion.testing._faults`` (TAB-T-170, E14).

Each fault is placed by the protocol (the statement's ``Query`` or ``Parse`` message, the server's
``ReadyForQuery``), never by a timer, and every scenario runs three times to show that it is reproducible. The
statements write a marker into a table; the count afterwards is the truth the client could not see:

- ``reply``: the statement ran and committed, the reply was lost: count 1, ``outcome_unknown``, not retryable;
- ``inflight``: the server received the statement and the connection was cut: count 1 once its process ended;
- ``request``: the statement never reached the server: count 0, although the client cannot tell it from ``reply``;
- ``delay``: the answer comes late, the connection stays usable.

Tabrion's own functions see the same faults: ``ping`` reports them, ``SessionLock`` and the advisory helpers raise
them (no retry) and keep no count of a lock they may not hold.
"""

# The SQL is built from schema names the fixtures generate (uuid hex) and fixed text, never from outside input.
# ruff: noqa: S608

from __future__ import annotations

from collections.abc import Iterator
import time
from typing import Any
import uuid

import pytest

from tabrion.errors import Kind, classify
from tabrion.postgres import SessionLock, lock_key, ping, try_advisory_lock, wait_advisory_lock
from tabrion.testing._faults import FaultProxy, wait_backend_gone

psycopg = pytest.importorskip("psycopg")

pytestmark = [pytest.mark.postgres, pytest.mark.slow]

RUNS = range(3)
"""Every scenario runs this often (QA-3: reproducible, no timer decides the outcome)."""


@pytest.fixture
def proxy(admin_url: str) -> Iterator[FaultProxy]:
    params = psycopg.conninfo.conninfo_to_dict(admin_url)
    if str(params.get("host") or "").startswith("/"):
        pytest.skip("the fault proxy needs a TCP server address")
    with FaultProxy.in_front_of(admin_url) as faults:
        yield faults
    faults.check()


@pytest.fixture
def through(proxy: FaultProxy, admin_url: str) -> str:
    """The admin conninfo with the proxy as its server."""
    return proxy.dsn(admin_url)


def _marker() -> str:
    return f"tabrion-fault-{uuid.uuid4().hex[:12]}"


def _count(admin: Any, schema: str, marker: str) -> int:
    return int(admin.execute(f"SELECT count(*) FROM {schema}.parts WHERE label = %s", (marker,)).fetchone()[0])


def _insert(schema: str) -> str:
    return f"INSERT INTO {schema}.parts (id, label) VALUES (%s, %s)"


# ---------------------------------------------------------------------------------------------------------------------
# TAB-T-170 / E14: the four faults on a write
# ---------------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("run", RUNS)
def test_t170_reply_lost_the_statement_ran_once(
    proxy: FaultProxy, through: str, admin: Any, admin_url: str, schema: str, run: int
) -> None:
    marker = _marker()
    seen: list[int] = []
    with psycopg.connect(through, autocommit=True, connect_timeout=10) as conn:
        backend = conn.info.backend_pid
        # ``then`` runs after the server ended the statement and before the client sees the cut.
        proxy.arm(b"INSERT INTO", fault="reply", then=lambda: seen.append(_count(admin, schema, marker)))
        with pytest.raises(psycopg.OperationalError) as info:
            conn.execute(_insert(schema), (run, marker))
        assert conn.closed or conn.broken
    assert (proxy.fired, proxy.replies_lost, proxy.fired_backend, proxy.armed) == (1, 1, backend, False)
    assert seen == [1], "the statement had committed before the client lost the connection"
    c = classify(info.value)
    assert (c.kind, c.outcome_unknown, c.retryable, c.connection_usable) == (Kind.CONNECTION, True, False, False)
    assert wait_backend_gone(admin_url, backend)
    assert _count(admin, schema, marker) == 1  # exactly once: a retry would have written it twice (R-NORETRY)


@pytest.mark.parametrize("run", RUNS)
def test_t170_reply_lost_during_commit(
    proxy: FaultProxy, through: str, admin: Any, admin_url: str, schema: str, run: int
) -> None:
    marker = _marker()
    with psycopg.connect(through, connect_timeout=10) as conn:
        backend = conn.info.backend_pid
        conn.execute(_insert(schema), (run, marker))
        proxy.arm(b"COMMIT", fault="reply")
        with pytest.raises(psycopg.OperationalError) as info:
            conn.commit()
    assert (proxy.fired, proxy.replies_lost) == (1, 1)
    c = classify(info.value, sent=False, during_commit=True)
    assert (c.kind, c.outcome_unknown, c.retryable) == (Kind.CONNECTION, True, False)
    assert wait_backend_gone(admin_url, backend)
    assert _count(admin, schema, marker) == 1  # the COMMIT ran


@pytest.mark.parametrize("run", RUNS)
def test_t170_cut_in_flight(proxy: FaultProxy, through: str, admin: Any, admin_url: str, schema: str, run: int) -> None:
    marker = _marker()
    with psycopg.connect(through, autocommit=True, connect_timeout=10) as conn:
        backend = conn.info.backend_pid
        proxy.arm(b"INSERT INTO", fault="inflight")
        with pytest.raises(psycopg.OperationalError) as info:
            conn.execute(_insert(schema), (run, marker))
    assert (proxy.fired, proxy.replies_lost, proxy.fired_backend) == (1, 0, backend)
    c = classify(info.value)
    assert (c.kind, c.outcome_unknown, c.retryable) == (Kind.CONNECTION, True, False)
    # The server runs what it received; count only once its process has ended.
    assert wait_backend_gone(admin_url, backend)
    assert _count(admin, schema, marker) == 1


@pytest.mark.parametrize("run", RUNS)
def test_t170_request_lost_the_statement_never_ran(
    proxy: FaultProxy, through: str, admin: Any, admin_url: str, schema: str, run: int
) -> None:
    marker = _marker()
    with psycopg.connect(through, autocommit=True, connect_timeout=10) as conn:
        backend = conn.info.backend_pid
        proxy.arm(b"INSERT INTO", fault="request")
        with pytest.raises(psycopg.OperationalError) as info:
            conn.execute(_insert(schema), (run, marker))
    assert proxy.fired == 1
    # The client cannot tell this from a lost reply: the outcome stays unknown, nothing is retried.
    c = classify(info.value)
    assert (c.kind, c.outcome_unknown, c.retryable) == (Kind.CONNECTION, True, False)
    assert wait_backend_gone(admin_url, backend)
    assert _count(admin, schema, marker) == 0


@pytest.mark.parametrize("run", RUNS)
def test_t170_delay_keeps_the_connection(proxy: FaultProxy, through: str, admin: Any, schema: str, run: int) -> None:
    marker = _marker()
    with psycopg.connect(through, autocommit=True, connect_timeout=10) as conn:
        proxy.arm(b"INSERT INTO", fault="delay", delay=0.4)
        started = time.monotonic()
        conn.execute(_insert(schema), (run, marker))
        assert time.monotonic() - started >= 0.4
        assert conn.execute("SELECT 1").fetchone() == (1,)
    assert (proxy.fired, proxy.replies_lost) == (1, 0)
    assert _count(admin, schema, marker) == 1


def test_only_the_armed_connection_and_statement(proxy: FaultProxy, through: str, admin: Any, schema: str) -> None:
    """Other connections and other statements pass unchanged; the fault fires once."""
    marker = _marker()
    with (
        psycopg.connect(through, autocommit=True, connect_timeout=10) as first,
        psycopg.connect(through, autocommit=True, connect_timeout=10) as second,
    ):
        proxy.arm(marker.encode(), fault="reply")
        assert first.execute("SELECT 1").fetchone() == (1,)  # not the armed statement
        assert proxy.armed
        with pytest.raises(psycopg.OperationalError):
            second.execute(f"SELECT '{marker}'")
        assert first.execute(f"SELECT '{marker}'").fetchone() == (marker,)  # disarmed after one fault
        assert second.closed or second.broken
    assert proxy.fired == 1


def test_then_errors_reach_the_test(admin_url: str) -> None:
    def fail() -> None:
        raise RuntimeError("then failed")

    with FaultProxy.in_front_of(admin_url) as faults:
        with psycopg.connect(faults.dsn(admin_url), autocommit=True, connect_timeout=10) as conn:
            faults.arm(b"SELECT 42", fault="delay", delay=0.0, then=fail)
            assert conn.execute("SELECT 42").fetchone() == (42,)
        with pytest.raises(RuntimeError, match="then failed"):
            faults.check()


# ---------------------------------------------------------------------------------------------------------------------
# Tabrion's functions under the same faults
# ---------------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("fault", ["reply", "inflight", "request"])
def test_ping_reports_a_lost_connection(proxy: FaultProxy, through: str, fault: str) -> None:
    with psycopg.connect(through, autocommit=True, connect_timeout=10) as conn:
        assert ping(conn).ok
        proxy.arm(b"pg_is_in_recovery", fault=fault)
        result = ping(conn, timeout=5)
        assert proxy.fired == 1
        assert result.ok is False and result.classification is not None
        assert result.classification.kind is Kind.CONNECTION and not result.classification.retryable
        assert ping(conn).ok is False  # the connection stays lost: nothing reconnects or retries behind the caller


def test_ping_latency_and_no_client_deadline(proxy: FaultProxy, through: str) -> None:
    """The delay shows in ``latency_ms``; the timeout bounds the server's work, not the network (TB-034)."""
    with psycopg.connect(through, autocommit=True, connect_timeout=10) as conn:
        proxy.arm(b"pg_is_in_recovery", fault="delay", delay=0.6)
        result = ping(conn, timeout=0.2)
        assert result.ok is True
        assert result.latency_ms is not None and result.latency_ms >= 600
        assert ping(conn).ok


@pytest.mark.parametrize("fault", ["reply", "inflight", "request"])
def test_session_lock_raises_and_forgets(proxy: FaultProxy, through: str, admin_url: str, fault: str) -> None:
    """A fault on the attempt propagates (no retry); the lock counts nothing it may not hold, and the server frees
    whatever the lost session held."""
    key = lock_key(_marker())
    with SessionLock(through) as lock:
        assert lock.try_acquire(key ^ 1)  # opens the connection
        proxy.arm(b"pg_try_advisory_lock", fault=fault)
        with pytest.raises(psycopg.OperationalError):
            lock.try_acquire(key)
        assert proxy.fired == 1
        backend = proxy.fired_backend
        assert backend is not None
        assert lock.holds(key) is False and lock.held_keys() == frozenset()
        assert wait_backend_gone(admin_url, backend)
        assert lock.try_acquire(key) is True  # a new session; the old one's locks are gone with it
        with psycopg.connect(admin_url, autocommit=True) as other:
            assert try_advisory_lock(other, key) is False
            assert try_advisory_lock(other, key ^ 1) is True  # the lost session's lock was freed


def test_wait_advisory_lock_does_not_retry(proxy: FaultProxy, through: str) -> None:
    key = lock_key(_marker())
    with psycopg.connect(through, autocommit=True, connect_timeout=10) as conn:
        proxy.arm(b"pg_try_advisory_lock", fault="request")
        with pytest.raises(psycopg.OperationalError):
            wait_advisory_lock(conn, key, timeout=5)
        assert proxy.fired == 1
        assert conn.closed or conn.broken
