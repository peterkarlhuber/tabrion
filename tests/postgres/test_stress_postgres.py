"""QA-4: concurrency and stress suite for PostgreSQL (TAB-T-006, TAB-T-007, B07; plan 07 K6, DI-03, DI-04).

Each test repeats its scenario ``TABRION_STRESS_REPEAT`` times (5 on pull requests, 200 nightly; ``tests/stress.py``)
and checks its invariant after every repetition: mutual exclusion of ``SessionLock`` and the advisory helpers, the
release of a lock when its session dies, no lost update in layer-B transactions (``FOR UPDATE`` and
``pg_advisory_xact_lock``), a bounded pool (``ConnectionFailed`` with kind ``RESOURCE`` when it is exhausted, every
connection back afterwards) and serializable transfers with an application retry on ``retryable`` errors. Threads are
joined with a deadline; :func:`tests.stress.hang_guard` ends a hanging run with every thread's traceback.
"""

# The SQL is built from names this file generates (the plugin's schema) and fixed text, never from outside input.
# ruff: noqa: S608

from __future__ import annotations

from collections.abc import Callable, Iterator
import functools
import threading
import time
from typing import Any
import uuid

import pytest

from tabrion.errors import Kind, TabrionError, classify
from tabrion.testing import PostgresSchema
from tests.stress import hang_guard, repeat, run_threads

from .conftest import Role

psycopg = pytest.importorskip("psycopg")
sqlalchemy = pytest.importorskip("sqlalchemy")

from tabrion import Database, Profile  # noqa: E402
from tabrion.errors import ConnectionFailed  # noqa: E402
from tabrion.postgres import SessionLock, advisory_unlock, lock_key, wait_advisory_lock  # noqa: E402

pytestmark = [pytest.mark.postgres, pytest.mark.stress, pytest.mark.slow]

REPEAT = repeat()
THREADS = 5


@pytest.fixture(autouse=True)
def bounded(request: pytest.FixtureRequest) -> Iterator[None]:
    with hang_guard(request.config, 180 + 5 * REPEAT):
        yield


@pytest.fixture
def key() -> int:
    return lock_key(f"tabrion-stress-{uuid.uuid4().hex}")


@pytest.fixture
def s(admin_url: str, tabrion_postgres_schema: PostgresSchema) -> str:
    """The test schema with ``counter`` (one row, 0) and ten accounts of 1 000 each."""
    name = tabrion_postgres_schema.name
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(
            f"CREATE TABLE {name}.counter (id int PRIMARY KEY, n int NOT NULL);"
            f"INSERT INTO {name}.counter VALUES (1, 0);"
            f"CREATE TABLE {name}.account (id int PRIMARY KEY, balance int NOT NULL);"
            f"INSERT INTO {name}.account SELECT g, 1000 FROM generate_series(1, 10) g"
        )
    return name


def _sa_url(conninfo: str) -> str:
    from urllib.parse import urlencode

    return "postgresql+psycopg:///?" + urlencode(psycopg.conninfo.conninfo_to_dict(conninfo))


def _counter(admin_url: str, schema: str) -> int:
    with psycopg.connect(admin_url, autocommit=True) as conn:
        row = conn.execute(f"SELECT n FROM {schema}.counter WHERE id = 1").fetchone()
    assert row is not None
    return int(row[0])


class Section:
    """A critical section that records overlapping entries (mutual exclusion is broken when ``overlaps > 0``)."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self.inside = 0
        self.overlaps = 0
        self.entries = 0

    def run(self, seconds: float = 0.002) -> None:
        with self._guard:
            self.inside += 1
            self.entries += 1
            if self.inside > 1:
                self.overlaps += 1
        time.sleep(seconds)
        with self._guard:
            self.inside -= 1


# --- TAB-T-006: SessionLock -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("as_role", [False, True], ids=["superuser", "runtime role"])
def test_t006_session_locks_exclude_each_other(
    admin_url: str, make_role: Callable[..., Role], key: int, as_role: bool
) -> None:
    """5 threads, each with its own ``SessionLock`` (its own session), take one key with ``acquire`` and enter a
    section 4 times: no two are ever inside together, and every entry happens."""
    url = make_role().url if as_role else admin_url
    for repetition in range(REPEAT):
        section = Section()
        locks = [SessionLock(url) for _ in range(THREADS)]

        def worker(lock: SessionLock) -> None:
            for _ in range(4):
                assert lock.acquire(key, timeout=30, poll_interval=0.001)
                try:
                    section.run()
                finally:
                    lock.release(key)

        try:
            errors = run_threads([functools.partial(worker, lock) for lock in locks], timeout=90)
        finally:
            for lock in locks:
                lock.close()
        assert errors == [], (repetition, errors)
        assert (section.overlaps, section.entries) == (0, 4 * THREADS), repetition


def test_t006_second_holder_fails_or_waits(admin_url: str, key: int) -> None:
    """The second ``SessionLock`` fails ``try_acquire`` and times out in ``acquire`` (bounded) while the first holds;
    it takes the key as soon as the first releases it."""
    for _ in range(REPEAT):
        with SessionLock(admin_url) as first, SessionLock(admin_url) as second:
            assert first.try_acquire(key)
            assert not second.try_acquire(key)
            started = time.monotonic()
            assert not second.acquire(key, timeout=0.1, poll_interval=0.01)
            assert 0.1 <= time.monotonic() - started < 5
            threading.Timer(0.05, first.release, args=(key,)).start()
            assert second.acquire(key, timeout=10, poll_interval=0.01)
            assert second.holds(key) and not first.holds(key)


def test_t006_connection_loss_releases_the_lock(admin_url: str, admin: Any, key: int) -> None:
    """``pg_terminate_backend`` on the holder's session frees the key for a waiter at once; the holder reports
    ``holds() == False`` and refuses a silent re-take (``postgres.lock_lost``) until it acknowledges the loss."""
    for _ in range(REPEAT):
        with SessionLock(admin_url) as holder, SessionLock(admin_url) as waiter:
            assert holder.try_acquire(key)
            pid = admin.execute(
                "SELECT pid FROM pg_locks WHERE locktype = 'advisory' AND granted"
                " AND ((classid::bigint << 32) | objid::bigint) = (%s::bigint & x'ffffffffffffffff'::bigint)",
                (key,),
            ).fetchone()[0]
            got: list[bool] = []
            thread = threading.Thread(target=lambda: got.append(waiter.acquire(key, timeout=30, poll_interval=0.01)))
            thread.start()
            admin.execute("SELECT pg_terminate_backend(%s)", (pid,))
            thread.join(40)
            assert got == [True]
            with pytest.raises(TabrionError) as info:
                holder.try_acquire(key)
            assert info.value.code == "postgres.lock_lost"
            assert not holder.holds(key)  # acknowledges the loss
            assert not holder.try_acquire(key)  # the waiter holds it now
            waiter.release(key)


# --- Advisory helpers on given connections: no lost update without row locks ---------------------------------------


def test_wait_advisory_lock_serializes_read_modify_write(admin_url: str, s: str, key: int) -> None:
    """5 connections increment a counter with plain ``SELECT`` + ``UPDATE`` (no row lock) under
    ``wait_advisory_lock``: the result is exact."""
    for repetition in range(REPEAT):

        def worker() -> None:
            with psycopg.connect(admin_url, autocommit=True) as conn:
                for _ in range(3):
                    assert wait_advisory_lock(conn, key, poll_interval=0.001, timeout=30)
                    try:
                        row = conn.execute(f"SELECT n FROM {s}.counter WHERE id = 1").fetchone()
                        conn.execute(f"UPDATE {s}.counter SET n = %s WHERE id = 1", (row[0] + 1,))
                    finally:
                        assert advisory_unlock(conn, key)

        errors = run_threads([worker] * THREADS, timeout=90)
        assert errors == [], (repetition, errors)
        assert _counter(admin_url, s) == 3 * THREADS * (repetition + 1)


# --- Layer B: transactions, pool -----------------------------------------------------------------------------------


@pytest.mark.parametrize("lock", ["for_update", "advisory_xact"])
def test_b07_layer_b_no_lost_update(admin_url: str, s: str, key: int, lock: str) -> None:
    """B07 repeated: 5 threads on one ``Database`` (pool of 5) read and write in ``transaction()``, with
    ``SELECT ... FOR UPDATE`` or ``pg_advisory_xact_lock``; the result is exact."""
    profile = Profile(pool_size=THREADS, max_overflow=0, pool_timeout=30)
    with Database.from_url(_sa_url(admin_url), profile=profile) as db:

        def increment() -> None:
            with db.transaction() as tx:
                if lock == "advisory_xact":
                    tx.execute("SELECT pg_advisory_xact_lock(:k)", {"k": key})
                    n = tx.fetch_scalar(f"SELECT n FROM {s}.counter WHERE id = 1")
                else:
                    n = tx.fetch_scalar(f"SELECT n FROM {s}.counter WHERE id = 1 FOR UPDATE")
                tx.execute(f"UPDATE {s}.counter SET n = :n WHERE id = 1", {"n": n + 1})

        for repetition in range(REPEAT):
            errors = run_threads([increment] * THREADS, timeout=90)
            assert errors == [], (repetition, errors)
            assert db.fetch_scalar(f"SELECT n FROM {s}.counter WHERE id = 1") == THREADS * (repetition + 1)
        assert db.engine.pool.checkedout() == 0  # type: ignore[attr-defined]


def test_pool_exhaustion_is_resource_and_recovers(admin_url: str, s: str) -> None:
    """A pool of 2 (no overflow): 8 threads queue for it and all succeed; while 2 transactions are held open, a third
    gets ``ConnectionFailed`` with kind ``RESOURCE``, retryable, outcome known, after ``pool_timeout`` (bounded).
    Every connection is back in the pool afterwards."""
    profile = Profile(pool_size=2, max_overflow=0, pool_timeout=0.2)
    with Database.from_url(_sa_url(admin_url), profile=profile) as db:
        pool: Any = db.engine.pool
        for repetition in range(REPEAT):

            def short() -> None:
                for _ in range(3):
                    for attempt in range(50):
                        try:
                            db.execute(f"UPDATE {s}.counter SET n = n + 1 WHERE id = 1")
                            break
                        except ConnectionFailed as exc:
                            assert exc.kind is Kind.RESOURCE and exc.classification is not None
                            assert exc.classification.retryable and not exc.classification.outcome_unknown
                            assert attempt < 49

            errors = run_threads([short] * 8, timeout=90)
            assert errors == [], (repetition, errors)
            assert db.fetch_scalar(f"SELECT n FROM {s}.counter WHERE id = 1") == 24 * (repetition + 1)

            entered, leave = threading.Barrier(3), threading.Event()

            def hold() -> None:
                with db.transaction():
                    entered.wait(10)
                    leave.wait(10)

            holders = [threading.Thread(target=hold, daemon=True) for _ in range(2)]
            for thread in holders:
                thread.start()
            entered.wait(10)
            try:
                started = time.monotonic()
                with pytest.raises(ConnectionFailed) as info:
                    db.fetch_scalar("SELECT 1")
                assert 0.15 <= time.monotonic() - started < 5
                assert info.value.kind is Kind.RESOURCE
            finally:
                leave.set()
                for thread in holders:
                    thread.join(30)
            assert pool.checkedout() == 0


def test_t007_serializable_transfers_with_application_retry(admin_url: str, s: str) -> None:
    """DI-04 (no automatic retry in Tabrion): 5 threads move money in ``serializable`` transactions and retry the
    whole transaction when the error is ``retryable`` (``SERIALIZATION``/``DEADLOCK``). The total stays 10 000, no
    error other than a retryable one occurs, and the first transfers of every repetition conflict (a barrier after
    their reads)."""
    profile = Profile(pool_size=THREADS, max_overflow=0, pool_timeout=30)
    conflicts = 0
    guard = threading.Lock()
    with Database.from_url(_sa_url(admin_url), profile=profile) as db:
        for repetition in range(REPEAT):
            all_read = threading.Barrier(THREADS)

            def transfer(seed: int) -> None:
                nonlocal conflicts
                for i in range(4):
                    a, b = (seed + i) % 3 + 1, (seed + 2 * i + 1) % 3 + 1  # three hot accounts
                    for attempt in range(100):
                        try:
                            with db.transaction(isolation="serializable") as tx:
                                balance = tx.fetch_scalar(f"SELECT balance FROM {s}.account WHERE id = :a", {"a": a})
                                if i == 0 and attempt == 0:
                                    all_read.wait(30)  # every thread has read before any writes: a conflict is certain
                                tx.execute(
                                    f"UPDATE {s}.account SET balance = :v WHERE id = :a", {"v": balance - 5, "a": a}
                                )
                                tx.execute(f"UPDATE {s}.account SET balance = balance + 5 WHERE id = :b", {"b": b})
                            break
                        except TabrionError as exc:
                            assert exc.classification is not None and exc.classification.retryable, exc
                            assert classify(exc).kind in (Kind.SERIALIZATION, Kind.DEADLOCK), exc
                            with guard:
                                conflicts += 1
                            time.sleep(0.002 * (attempt + 1) * (seed + 1))  # back off, staggered per thread
                    else:
                        raise AssertionError("100 attempts without success")

            errors = run_threads([functools.partial(transfer, seed) for seed in range(THREADS)], timeout=120)
            assert errors == [], (repetition, errors)
            assert db.fetch_scalar(f"SELECT sum(balance) FROM {s}.account") == 10_000
    assert conflicts >= REPEAT
