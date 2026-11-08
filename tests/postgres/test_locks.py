"""TAB-030 against a real PostgreSQL: ``SessionLock`` (TAB-T-L01..L10) and the advisory helpers on a given
connection (L11..L15), as the superuser and as a role without superuser.

Other processes are real processes (``subprocess``), so the locks compete across server sessions as they do between
an application's workers. Every test uses keys of its own (``lock_key`` of a random name).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
import contextlib
import os
import signal
import subprocess
import sys
import textwrap
import threading
import time
from typing import Any
import uuid

import pytest

from tabrion.errors import UsageError
from tabrion.postgres import (
    SessionLock,
    SessionSettings,
    advisory_unlock,
    configure,
    lock_key,
    try_advisory_lock,
    wait_advisory_lock,
)

from .conftest import Role, with_params

psycopg = pytest.importorskip("psycopg")
from psycopg import pq  # noqa: E402

pytestmark = pytest.mark.postgres

IDLE = pq.TransactionStatus.IDLE
INTRANS = pq.TransactionStatus.INTRANS


@pytest.fixture
def key() -> int:
    return lock_key(f"tabrion-test-{uuid.uuid4().hex}")


@pytest.fixture(params=["superuser", "runtime role"])
def url(request: pytest.FixtureRequest, admin_url: str, make_role: Callable[..., Role]) -> str:
    """Advisory locks need no privilege: every test runs as the superuser and as a role without superuser."""
    return admin_url if request.param == "superuser" else make_role().url


@pytest.fixture
def locks(url: str) -> Iterator[Callable[..., SessionLock]]:
    made: list[SessionLock] = []

    def factory(**kwargs: Any) -> SessionLock:
        lock = SessionLock(url, **kwargs)
        made.append(lock)
        return lock

    yield factory
    for lock in made:
        lock.close()


def _held_by_others(admin: Any, key: int) -> int:
    """Sessions that hold the advisory lock ``key`` (one-key form: classid/objid are its high/low 32 bits)."""
    row = admin.execute(
        "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND granted AND objsubid = 1"
        " AND ((classid::bigint << 32) | objid::bigint) = (%s::bigint & x'ffffffffffffffff'::bigint)",
        (key,),
    ).fetchone()
    return int(row[0])


def _free(admin_url: str, key: int) -> bool:
    """Whether another session can take ``key`` right now (it releases it again at once)."""
    with psycopg.connect(admin_url, autocommit=True) as other:
        taken = other.execute("SELECT pg_try_advisory_lock(%s)", (key,)).fetchone()[0]
        if taken:
            other.execute("SELECT pg_advisory_unlock(%s)", (key,))
        return bool(taken)


HOLDER = textwrap.dedent(
    """
    import sys
    from tabrion.postgres import SessionLock
    lock = SessionLock(sys.argv[1])
    print("held" if lock.try_acquire(int(sys.argv[2])) else "busy", flush=True)
    sys.stdin.readline()
    lock.release(int(sys.argv[2]))
    print("released", flush=True)
    sys.stdin.readline()
    lock.close()
    """
)


class Holder:
    """Another process with a SessionLock on ``key``."""

    def __init__(self, url: str, key: int) -> None:
        self.process = subprocess.Popen(
            [sys.executable, "-c", HOLDER, url, str(key)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        assert self.process.stdout is not None
        self.first = self.process.stdout.readline().strip()

    def step(self) -> str:
        assert self.process.stdin is not None and self.process.stdout is not None
        self.process.stdin.write("\n")
        self.process.stdin.flush()
        return self.process.stdout.readline().strip()

    def stop(self) -> None:
        for pipe in (self.process.stdin, self.process.stdout):
            if pipe is not None:
                with contextlib.suppress(OSError):
                    pipe.close()
        try:
            self.process.wait(10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(10)


# ---------------------------------------------------------------------------------------------------------------------
# lock_key
# ---------------------------------------------------------------------------------------------------------------------


def test_lock_key_fits_postgres(admin: Any) -> None:
    """Keys are accepted by pg_try_advisory_lock(bigint) at both ends of the range."""
    for key in (lock_key("a"), -(2**63), 2**63 - 1, 0x6E6F646573747230, 0x617865_6E747261):
        assert try_advisory_lock(admin, key) is True
        assert advisory_unlock(admin, key) is True


# ---------------------------------------------------------------------------------------------------------------------
# SessionLock
# ---------------------------------------------------------------------------------------------------------------------


def test_l01_two_processes_compete(locks: Callable[..., SessionLock], url: str, admin: Any, key: int) -> None:
    holder = Holder(url, key)
    try:
        assert holder.first == "held"
        lock = locks()
        assert lock.try_acquire(key) is False
        assert lock.holds(key) is False
        assert holder.step() == "released"
        assert lock.try_acquire(key) is True
        assert _held_by_others(admin, key) == 1
        other = Holder(url, key)
        try:
            assert other.first == "busy"  # now this process holds it
        finally:
            other.stop()
    finally:
        holder.stop()


def test_l02_counting(locks: Callable[..., SessionLock], admin_url: str, key: int) -> None:
    lock = locks()
    assert lock.try_acquire(key) and lock.try_acquire(key) and lock.acquire(key, timeout=0)
    lock.release(key)
    lock.release(key)
    assert lock.holds(key) and not _free(admin_url, key), "unlocked before the last release"
    lock.release(key)
    assert not lock.holds(key)
    assert _free(admin_url, key)


def test_l03_release_and_held_keys(locks: Callable[..., SessionLock], admin_url: str, key: int) -> None:
    lock = locks()
    other = key ^ 1
    assert lock.held_keys() == frozenset()
    lock.release(key)  # not held: nothing happens, no connection is opened
    assert lock._connection is None
    assert lock.try_acquire(key) and lock.try_acquire(other)
    assert lock.held_keys() == frozenset({key, other})
    lock.release(other)
    assert lock.held_keys() == frozenset({key})
    assert _free(admin_url, other) and not _free(admin_url, key)


def test_l04_connection_loss_clears_every_count(
    locks: Callable[..., SessionLock], admin: Any, admin_url: str, key: int
) -> None:
    lock = locks()
    assert lock.try_acquire(key) and lock.try_acquire(key) and lock.try_acquire(key ^ 2)
    assert lock._connection is not None
    admin.execute("SELECT pg_terminate_backend(%s)", (lock._connection.info.backend_pid,))
    time.sleep(0.2)
    assert lock.holds(key) is False
    assert lock.held_keys() == frozenset()
    assert _free(admin_url, key), "the server freed the locks with the session"
    lock.release(key)  # counts are gone: no error, nothing to do
    assert lock.try_acquire(key) is True, "a new connection replaced the lost one"
    assert lock.holds(key)


def test_l04_held_keys_probes_the_connection(locks: Callable[..., SessionLock], admin: Any, key: int) -> None:
    """Review L-4 (#6): ``held_keys`` alone notices a lost connection (no ``holds`` call before it)."""
    lock = locks()
    assert lock.try_acquire(key) and lock.try_acquire(key ^ 2)
    admin.execute("SELECT pg_terminate_backend(%s)", (lock._connection.info.backend_pid,))  # type: ignore[union-attr]
    time.sleep(0.2)
    assert lock.held_keys() == frozenset()


def test_l04_reentrant_acquire_after_a_loss_is_refused(
    locks: Callable[..., SessionLock], admin: Any, admin_url: str, key: int
) -> None:
    """Review L-3 (#6): a nested section must not take a fresh hold of a key that the outer section lost; its
    release would free the lock while the outer section still relies on it."""
    lock = locks()
    other = key ^ 64
    assert lock.try_acquire(key)  # the outer section
    admin.execute("SELECT pg_terminate_backend(%s)", (lock._connection.info.backend_pid,))  # type: ignore[union-attr]
    time.sleep(0.2)
    with pytest.raises(UsageError) as lost:
        lock.try_acquire(key)  # the nested section
    assert lost.value.code == "postgres.lock_lost"
    started = time.monotonic()
    with pytest.raises(UsageError) as lost_again:
        lock.acquire(key, timeout=30)
    assert lost_again.value.code == "postgres.lock_lost"
    assert time.monotonic() - started < 5, "acquire raises at once on a lost key, it does not poll"
    assert lock.try_acquire(other) is True  # a key that was not held is not affected
    assert _free(admin_url, key), "nothing took the lost key again"
    # Acknowledged by holds() (the outer section checks and sees the loss): the key can be taken again.
    assert lock.holds(key) is False
    assert lock.try_acquire(key) is True
    lock.release(key)
    lock.release(other)
    # Acknowledged by release() (the outer section ends): the same.
    assert lock.try_acquire(key)
    admin.execute("SELECT pg_terminate_backend(%s)", (lock._connection.info.backend_pid,))  # type: ignore[union-attr]
    time.sleep(0.2)
    lock.release(key)
    assert lock.try_acquire(key) is True
    lock.close()
    with pytest.raises(UsageError) as closed:
        lock.try_acquire(key)
    assert closed.value.code == "postgres.lock_closed"


def test_l04_loss_while_unlocking(locks: Callable[..., SessionLock], admin: Any, admin_url: str, key: int) -> None:
    """``release`` finds the connection dead at its probe: the lock is gone already, no error."""
    lock = locks()
    assert lock.try_acquire(key)
    pid = lock._connection.info.backend_pid  # type: ignore[union-attr]
    admin.execute("SELECT pg_terminate_backend(%s)", (pid,))
    time.sleep(0.2)
    lock.release(key)
    assert lock.held_keys() == frozenset() and _free(admin_url, key)


def test_l05_polling_timeout(locks: Callable[..., SessionLock], url: str, key: int) -> None:
    holder = Holder(url, key)
    try:
        assert holder.first == "held"
        lock = locks()
        started = time.monotonic()
        assert lock.acquire(key, timeout=0.6, poll_interval=0.1) is False
        assert 0.55 <= time.monotonic() - started < 3
        assert lock.acquire(key, timeout=0) is False  # one attempt
        released = threading.Timer(0.5, holder.step)
        released.start()
        started = time.monotonic()
        assert lock.acquire(key, timeout=10, poll_interval=0.05) is True
        assert time.monotonic() - started < 5
        released.join()
    finally:
        holder.stop()


def test_l06_no_statement_and_no_snapshot_while_waiting(
    locks: Callable[..., SessionLock], admin: Any, url: str, key: int
) -> None:
    holder = Holder(url, key)
    try:
        lock = locks(configure=lambda c: configure(c, SessionSettings(application_name="tabrion-l06")))
        assert lock.try_acquire(key ^ 4)  # opens the connection
        pid = lock._connection.info.backend_pid  # type: ignore[union-attr]
        done = threading.Event()

        def wait() -> None:
            lock.acquire(key, timeout=3, poll_interval=0.2)
            done.set()

        waiter = threading.Thread(target=wait)
        waiter.start()
        samples = []
        for _ in range(10):
            samples.append(
                admin.execute(
                    "SELECT state, backend_xmin IS NULL, backend_xid IS NULL, application_name"
                    " FROM pg_stat_activity WHERE pid = %s",
                    (pid,),
                ).fetchone()
            )
            time.sleep(0.11)
        waiter.join(10)
        assert done.is_set()
        idle = [s for s in samples if s[0] == "idle"]
        assert len(idle) >= 8, samples  # a sample may meet an attempt in flight
        assert all(s[1] and s[2] for s in idle), samples
        assert {s[3] for s in samples} == {"tabrion-l06"}
    finally:
        holder.stop()


def test_l07_close(locks: Callable[..., SessionLock], admin_url: str, key: int) -> None:
    lock = locks()
    assert lock.try_acquire(key)
    lock.close()
    lock.close()  # harmless
    assert _free(admin_url, key), "closing ends the session and every lock"
    for call in (
        lambda: lock.try_acquire(key),
        lambda: lock.acquire(key, timeout=0),
        lambda: lock.holds(key),
        lambda: lock.release(key),
        lambda: lock.held_keys(),
        lambda: lock.__enter__(),
    ):
        with pytest.raises(UsageError) as info:
            call()
        assert info.value.code == "postgres.lock_closed"


def test_l07_context_manager(url: str, admin_url: str, key: int) -> None:
    with SessionLock(url) as lock:
        assert lock.try_acquire(key)
    assert _free(admin_url, key)
    with pytest.raises(UsageError):
        lock.holds(key)


def test_l08_configure(locks: Callable[..., SessionLock], admin: Any, url: str, key: int) -> None:
    default = locks()
    assert default.try_acquire(key)
    pid = default._connection.info.backend_pid  # type: ignore[union-attr]
    row = admin.execute(
        "SELECT application_name FROM pg_stat_activity WHERE pid = %s",
        (pid,),
    ).fetchone()
    assert row == ("tabrion-lock",)
    seen: list[Any] = []
    custom = locks(configure=seen.append, connect_kwargs={"application_name": "tabrion-l08", "connect_timeout": 3})
    assert custom.try_acquire(key ^ 8)
    assert len(seen) == 1 and seen[0].autocommit is True
    assert seen[0].execute("SHOW application_name").fetchone() == ("tabrion-l08",)

    def failing(conn: Any) -> None:
        seen.append(conn)
        raise RuntimeError("configure failed")

    broken = locks(configure=failing)
    with pytest.raises(RuntimeError, match="configure failed"):
        broken.try_acquire(key ^ 16)
    assert seen[-1].closed, "the connection of a failed configure is closed"
    assert broken.held_keys() == frozenset()


def test_l08_connect_errors_pass_through(admin_url: str, key: int) -> None:
    password = "SECRET-4711"
    lock = SessionLock(with_params(admin_url, password=password, port="1"), connect_kwargs={"connect_timeout": 2})
    with pytest.raises(psycopg.OperationalError) as info:
        lock.try_acquire(key)
    assert password not in repr(lock)
    assert info.value is not None
    lock.close()


def test_l09_threads(locks: Callable[..., SessionLock], admin_url: str, key: int) -> None:
    """One SessionLock from many threads: the counts stay consistent, and the key is free at the end."""
    lock = locks()
    errors: list[BaseException] = []

    def work() -> None:
        try:
            for _ in range(25):
                assert lock.try_acquire(key)
                assert lock.holds(key)
                lock.release(key)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)
    assert not errors
    assert lock.held_keys() == frozenset()
    assert _free(admin_url, key)


def test_l09_threads_acquire_on_a_fresh_lock(locks: Callable[..., SessionLock], admin: Any, admin_url: str) -> None:
    """Review L-4 (#6): many threads ``acquire`` at once on a SessionLock that has no connection yet, with keys that
    are busy for a moment: one connection, consistent counts (``acquire`` polls under the internal lock)."""
    lock = locks()
    keys = [lock_key(f"tabrion-l09-{uuid.uuid4().hex}") for _ in range(4)]
    blocker = psycopg.connect(admin_url, autocommit=True)
    try:
        for k in keys:
            assert try_advisory_lock(blocker, k)
        start = threading.Barrier(16)
        errors: list[BaseException] = []

        def work(k: int) -> None:
            try:
                start.wait(10)
                assert lock.acquire(k, timeout=20, poll_interval=0.01)
            except BaseException as exc:
                errors.append(exc)

        threads = [threading.Thread(target=work, args=(keys[i % 4],)) for i in range(16)]
        for thread in threads:
            thread.start()
        time.sleep(0.3)
        blocker.close()  # frees the keys at once
        for thread in threads:
            thread.join(30)
        assert not errors, errors
        assert lock.held_keys() == frozenset(keys)
        assert lock._held == dict.fromkeys(keys, 4)
        backends = admin.execute(
            "SELECT count(DISTINCT pid) FROM pg_locks WHERE locktype = 'advisory' AND granted AND objsubid = 1"
            " AND ((classid::bigint << 32) | objid::bigint)"
            "   = ANY (SELECT k & x'ffffffffffffffff'::bigint FROM unnest(%s::bigint[]) AS k)",
            (keys,),
        ).fetchone()[0]
        assert backends == 1, "every thread used the one connection"
        for k in keys:
            for _ in range(4):
                lock.release(k)
        assert lock.held_keys() == frozenset()
        assert all(_free(admin_url, k) for k in keys)
    finally:
        blocker.close()


@pytest.mark.skipif(not hasattr(os, "register_at_fork"), reason="needs os.register_at_fork")
def test_l10_fork_while_the_internal_lock_is_held(locks: Callable[..., SessionLock], key: int) -> None:
    """Review L-2 (#6): a fork while another thread is inside a method (simulated by holding the internal lock): the
    child gets a fresh internal lock and does not hang."""
    lock = locks()
    assert lock.try_acquire(key)
    read, write = os.pipe()
    with lock._guard:  # as if another thread were inside try_acquire at the fork
        child = os.fork()
        if child == 0:  # pragma: no cover - runs in the child
            code = 1
            try:
                signal.alarm(5)  # a hang would end the child, not the test run
                os.write(write, str(lock.holds(key)).encode())
                code = 0
            finally:
                os._exit(code)
    os.close(write)
    _, status = os.waitpid(child, 0)
    answer = os.read(read, 20).decode()
    os.close(read)
    assert os.waitstatus_to_exitcode(status) == 0, "the child hung on the inherited internal lock"
    assert answer == "False"
    assert lock.holds(key)


@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs os.fork")
def test_l10_fork(locks: Callable[..., SessionLock], admin: Any, admin_url: str, key: int) -> None:
    """A forked child opens its own connection and holds nothing; the parent's session and locks survive it."""
    lock = locks()
    assert lock.try_acquire(key)
    parent_pid = lock._connection.info.backend_pid  # type: ignore[union-attr]
    read, write = os.pipe()
    child = os.fork()
    if child == 0:  # pragma: no cover - runs in the child
        code = 1
        try:
            result = (lock.holds(key), lock.try_acquire(key), lock.try_acquire(key ^ 32))
            child_pid = lock._connection.info.backend_pid  # type: ignore[union-attr]
            lock.release(key ^ 32)
            lock.close()
            os.write(write, f"{result[0]},{result[1]},{result[2]},{child_pid}".encode())
            code = 0
        finally:
            os._exit(code)
    os.close(write)
    _, status = os.waitpid(child, 0)
    answer = os.read(read, 200).decode()
    os.close(read)
    assert os.waitstatus_to_exitcode(status) == 0
    holds, retaken, other, child_backend = answer.split(",")
    assert (holds, retaken, other) == ("False", "False", "True")
    assert int(child_backend) != parent_pid
    assert lock.holds(key), "the child must not end the parent's session"
    assert lock._connection.info.backend_pid == parent_pid  # type: ignore[union-attr]
    assert not _free(admin_url, key)


# ---------------------------------------------------------------------------------------------------------------------
# Advisory helpers on a given connection
# ---------------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("autocommit", [True, False])
def test_l11_two_processes_one_waits(connect: Callable[..., Any], url: str, key: int, autocommit: bool) -> None:
    holder = Holder(url, key)
    try:
        assert holder.first == "held"
        conn = connect(url, autocommit=autocommit)
        assert try_advisory_lock(conn, key) is False
        assert conn.info.transaction_status == IDLE
        release = threading.Timer(0.4, holder.step)
        release.start()
        assert wait_advisory_lock(conn, key, poll_interval=0.05, timeout=10) is True
        release.join()
        assert conn.info.transaction_status == IDLE
        assert advisory_unlock(conn, key) is True
        assert advisory_unlock(conn, key) is False  # not held any more
        assert conn.info.transaction_status == IDLE
    finally:
        holder.stop()


@pytest.mark.parametrize("autocommit", [True, False])
def test_l12_idle_and_no_snapshot_while_waiting(
    connect: Callable[..., Any], admin: Any, url: str, key: int, autocommit: bool
) -> None:
    holder = Holder(url, key)
    try:
        conn = connect(url, autocommit=autocommit)
        pid = conn.info.backend_pid
        waiter = threading.Thread(target=wait_advisory_lock, args=(conn, key), kwargs={"timeout": 2.5})
        waiter.start()
        samples = []
        for _ in range(10):
            samples.append(
                admin.execute(
                    "SELECT state, backend_xmin IS NULL FROM pg_stat_activity WHERE pid = %s", (pid,)
                ).fetchone()
            )
            time.sleep(0.1)
        waiter.join(10)
        # Between attempts the session is idle and holds no snapshot. Without autocommit a sample may meet the moment
        # between an attempt and its rollback (idle in transaction, with the catalog snapshot); L13 shows that such
        # moments do not hold up CREATE INDEX CONCURRENTLY.
        assert all(s[1] for s in samples if s[0] == "idle"), samples
        assert sum(s[0] == "active" for s in samples) <= 2, samples
        assert sum(s[0] == "idle" for s in samples) >= 6, samples
    finally:
        holder.stop()


def test_l13_create_index_concurrently_while_one_holds_and_one_waits(
    connect: Callable[..., Any], admin_url: str, schema: str, key: int
) -> None:
    """The Axentra case: the holder runs CREATE INDEX CONCURRENTLY while a second session waits for the lock. With
    polling it completes; with a blocking pg_advisory_lock (control) it waits for the waiter's snapshot."""
    holder = connect(admin_url, autocommit=True)
    assert try_advisory_lock(holder, key)
    waiter_conn = connect(admin_url, autocommit=True)
    waiter = threading.Thread(target=wait_advisory_lock, args=(waiter_conn, key), kwargs={"timeout": 30})
    waiter.start()
    time.sleep(0.3)
    holder.execute("SET statement_timeout = '10s'")
    started = time.monotonic()
    holder.execute(f"CREATE INDEX CONCURRENTLY parts_label_l13 ON {schema}.parts (label)")
    assert time.monotonic() - started < 10
    assert advisory_unlock(holder, key)
    waiter.join(10)
    assert not waiter.is_alive()
    assert advisory_unlock(waiter_conn, key)

    # Control: a blocking wait is a transaction the index build must wait for, while that wait needs the holder's
    # lock: a deadlock, which the server resolves by failing one of the two (or the holder's statement times out).
    assert try_advisory_lock(holder, key)
    blocking = connect(admin_url, autocommit=True)
    failures: list[BaseException] = []

    def block() -> None:
        try:
            blocking.execute("SELECT pg_advisory_lock(%s)", (key,))
            blocking.execute("SELECT pg_advisory_unlock(%s)", (key,))
        except psycopg.Error as exc:
            failures.append(exc)

    blocked = threading.Thread(target=block, name="blocking-advisory-lock")
    blocked.start()
    time.sleep(0.3)
    holder.execute("SET statement_timeout = '5s'")
    try:
        holder.execute(f"CREATE INDEX CONCURRENTLY parts_id_l13 ON {schema}.parts (id)")
    except (psycopg.errors.QueryCanceled, psycopg.errors.DeadlockDetected) as exc:
        failures.append(exc)
    assert advisory_unlock(holder, key)
    blocked.join(10)
    assert not blocked.is_alive()
    assert failures, "the blocking control ran without a deadlock or a timeout"
    assert all(isinstance(f, psycopg.errors.QueryCanceled | psycopg.errors.DeadlockDetected) for f in failures)


def test_l14_timeout(connect: Callable[..., Any], url: str, key: int) -> None:
    holder = Holder(url, key)
    try:
        conn = connect(url)
        started = time.monotonic()
        assert wait_advisory_lock(conn, key, poll_interval=0.1, timeout=0.5) is False
        assert 0.45 <= time.monotonic() - started < 3
        assert wait_advisory_lock(conn, key, timeout=0) is False
        assert conn.info.transaction_status == IDLE
    finally:
        holder.stop()


def test_l15_inside_a_transaction(connect: Callable[..., Any], url: str, key: int) -> None:
    conn = connect(url)
    conn.execute("SELECT 1")
    assert conn.info.transaction_status == INTRANS
    with pytest.raises(UsageError) as info:
        wait_advisory_lock(conn, key, timeout=1)
    assert info.value.code == "postgres.lock_in_transaction"
    assert conn.info.transaction_status == INTRANS
    # try_advisory_lock and advisory_unlock run inside the caller's transaction and end nothing.
    assert try_advisory_lock(conn, key) is True
    assert conn.info.transaction_status == INTRANS
    assert advisory_unlock(conn, key) is True
    conn.rollback()


def test_l15_failed_transaction_and_closed_connection(connect: Callable[..., Any], url: str, key: int) -> None:
    conn = connect(url)
    with pytest.raises(psycopg.errors.DivisionByZero):
        conn.execute("SELECT 1 / 0")
    with pytest.raises(UsageError):
        wait_advisory_lock(conn, key)
    with pytest.raises(psycopg.errors.InFailedSqlTransaction):
        try_advisory_lock(conn, key)
    assert conn.info.transaction_status == pq.TransactionStatus.INERROR
    conn.close()
    with pytest.raises(psycopg.OperationalError):
        wait_advisory_lock(conn, key, timeout=0)


@pytest.mark.parametrize("state", ["idle-autocommit", "idle", "intrans"])
def test_g01_advisory_helpers_keep_the_state(connect: Callable[..., Any], url: str, key: int, state: str) -> None:
    """T-NOTX: the helpers end no transaction and change no factory; the session lock outlives a rollback."""
    from psycopg.rows import dict_row

    conn = connect(url, autocommit=state == "idle-autocommit", row_factory=dict_row)
    if state == "intrans":
        conn.execute("SELECT pg_catalog.set_config('app.tenant_id', 't', true)")
    before = (conn.info.transaction_status, conn.autocommit, conn.row_factory)
    assert try_advisory_lock(conn, key) is True
    assert (conn.info.transaction_status, conn.autocommit, conn.row_factory) == before
    if state != "intrans":
        assert wait_advisory_lock(conn, key, timeout=0) is True  # re-entrant on the same session
        assert advisory_unlock(conn, key) is True
    assert advisory_unlock(conn, key) is True
    assert (conn.info.transaction_status, conn.autocommit, conn.row_factory) == before
    if state == "intrans":
        assert conn.execute("SELECT current_setting('app.tenant_id', true) AS t").fetchone() == {"t": "t"}
        conn.rollback()


def test_session_lock_on_its_own_database(scratch_database: str, key: int) -> None:
    """Keys are per database: the same key in another database is another lock."""
    with SessionLock(scratch_database) as here:
        assert here.try_acquire(key)
        with psycopg.connect(with_params(scratch_database, dbname="postgres"), autocommit=True) as elsewhere:
            assert try_advisory_lock(elsewhere, key) is True
            assert advisory_unlock(elsewhere, key) is True


def test_l12_pipeline_autocommit_holds_no_snapshot_while_waiting(
    connect: Callable[..., Any], admin: Any, url: str, key: int
) -> None:
    """Review L-1 (#6): in an autocommit pipeline each attempt is synced before the sleep, so the waiting session is
    idle without a snapshot, as outside pipeline mode."""
    holder = Holder(url, key)
    try:
        conn = connect(url, autocommit=True)
        pid = conn.info.backend_pid
        result: list[bool] = []

        def wait() -> None:
            with conn.pipeline():
                result.append(wait_advisory_lock(conn, key, timeout=2.0, poll_interval=0.2))

        waiter = threading.Thread(target=wait)
        waiter.start()
        samples = []
        for _ in range(12):
            time.sleep(0.1)
            samples.append(
                admin.execute(
                    "SELECT state, backend_xmin IS NULL FROM pg_stat_activity WHERE pid = %s", (pid,)
                ).fetchone()
            )
        waiter.join(10)
        assert result == [False]
        assert sum(s == ("idle", True) for s in samples) >= 9, samples
    finally:
        holder.stop()


@pytest.mark.parametrize("autocommit", [True, False])
def test_l15_pipeline_with_queued_statements(connect: Callable[..., Any], url: str, key: int, autocommit: bool) -> None:
    """Review L-4 (#6): in pipeline mode the status is ACTIVE until a sync; ``wait_advisory_lock`` syncs first. With
    autocommit the queued statement has ended and the wait proceeds; in a transaction it is refused."""
    conn = connect(url, autocommit=autocommit)
    with conn.pipeline():
        conn.execute("SELECT 1")  # queued, not yet synced
        if autocommit:
            assert wait_advisory_lock(conn, key, timeout=1) is True
            assert advisory_unlock(conn, key) is True
        else:
            with pytest.raises(UsageError) as refused:
                wait_advisory_lock(conn, key, timeout=1)
            assert refused.value.code == "postgres.lock_in_transaction"
    if not autocommit:
        conn.rollback()


def test_l04_release_acknowledges_a_loss_seen_elsewhere(
    locks: Callable[..., SessionLock], admin: Any, key: int
) -> None:
    """Delta review DL-1 (#6), mutant 3: another call noticed the loss first (``held_keys``); ``release(key)`` of the
    lost hold (count 0 by then) still acknowledges it, and the key can be taken again."""
    lock = locks()
    assert lock.try_acquire(key)
    admin.execute("SELECT pg_terminate_backend(%s)", (lock._connection.info.backend_pid,))  # type: ignore[union-attr]
    time.sleep(0.2)
    assert lock.held_keys() == frozenset()  # noticed here; held_keys does not acknowledge
    with pytest.raises(UsageError):
        lock.try_acquire(key)
    lock.release(key)  # the outer section ends
    assert lock.try_acquire(key) is True


@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs os.fork")
def test_l10_fork_after_a_loss(locks: Callable[..., SessionLock], admin: Any, key: int) -> None:
    """Delta review DL-1 (#6), mutant 5: a child never held the parent's lost key, so it may take it."""
    lock = locks()
    assert lock.try_acquire(key)
    admin.execute("SELECT pg_terminate_backend(%s)", (lock._connection.info.backend_pid,))  # type: ignore[union-attr]
    time.sleep(0.2)
    assert lock.held_keys() == frozenset()  # the parent has noticed the loss and not acknowledged it
    read, write = os.pipe()
    child = os.fork()
    if child == 0:  # pragma: no cover - runs in the child
        code = 1
        try:
            answer = "True" if lock.try_acquire(key) else "False"
            lock.close()
            os.write(write, answer.encode())
            code = 0
        except UsageError as exc:
            os.write(write, exc.code.encode())
        finally:
            os._exit(code)
    os.close(write)
    _, status = os.waitpid(child, 0)
    answer = os.read(read, 50).decode()
    os.close(read)
    assert answer == "True", answer
    assert os.waitstatus_to_exitcode(status) == 0
    with pytest.raises(UsageError):
        lock.try_acquire(key)  # the parent's loss is still unacknowledged
