"""QA-4: concurrency and stress suite for SQLite, both layers (TAB-T-002, TAB-T-003, TAB-T-005; plan 07 K6, DI-02,
DI-03).

Each test repeats its scenario ``TABRION_STRESS_REPEAT`` times (5 on pull requests, 200 in the nightly ``stress``
job, ``tests/stress.py``) and checks the invariant after every repetition: no lost update, no unexpected
``database is locked``, a consistent snapshot for readers and for ``backup``. Threads and processes start behind a
barrier and are joined with a deadline; :func:`tests.stress.hang_guard` ends the run with every thread's traceback if
a test still hangs. Marked ``stress`` and ``slow``: ``pytest -m "not stress"`` leaves them out locally.
"""

from __future__ import annotations

from collections.abc import Iterator
import functools
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time

import pytest

from tabrion import sqlite as ts
from tabrion.errors import Kind, classify
from tabrion.sqlite import SQLiteProfile
from tests.conftest import SRC
from tests.stress import hang_guard, repeat, run_threads

pytestmark = [pytest.mark.stress, pytest.mark.slow]

REPEAT = repeat()
THREADS = 5


@pytest.fixture(autouse=True)
def bounded(request: pytest.FixtureRequest) -> Iterator[None]:
    with hang_guard(request.config, 120 + 5 * REPEAT):
        yield


def _connect(path: Path, busy_timeout: float) -> sqlite3.Connection:
    return ts.connect(path, profile=SQLiteProfile(busy_timeout=busy_timeout))


@pytest.fixture
def path(tmp_path: Path) -> Path:
    """A WAL database: ``counter`` (one row, 0) and ten accounts of 1 000 each (total 10 000)."""
    name = tmp_path / "stress.db"
    conn = ts.connect(name)
    try:
        conn.execute("CREATE TABLE counter (id INTEGER PRIMARY KEY, n INTEGER NOT NULL)")
        conn.execute("INSERT INTO counter VALUES (1, 0)")
        conn.execute("CREATE TABLE account (id INTEGER PRIMARY KEY, balance INTEGER NOT NULL)")
        conn.executemany("INSERT INTO account VALUES (?, 1000)", [(i,) for i in range(1, 11)])
    finally:
        conn.close()
    return name


def _counter(path: Path) -> int:
    conn = ts.connect(path, read_only=True)
    try:
        return int(conn.execute("SELECT n FROM counter WHERE id = 1").fetchone()[0])
    finally:
        conn.close()


# --- TAB-T-002 / TAB-T-003: read-then-write in threads, no lost update, no lock error ---------------------------------


def test_t002_t003_read_then_write_threads(path: Path) -> None:
    """The q.py/q2.py probes, repeated: 5 threads read, wait and write in ``write_transaction`` (``BEGIN
    IMMEDIATE``, ``busy_timeout`` 30 s): every repetition adds exactly 5, and no thread sees ``database is locked``."""

    def increment() -> None:
        conn = ts.connect(path)
        try:
            with ts.write_transaction(conn):
                n = conn.execute("SELECT n FROM counter WHERE id = 1").fetchone()[0]
                time.sleep(0.002)  # widen the window between the read and the write
                conn.execute("UPDATE counter SET n = ? WHERE id = 1", (n + 1,))
        finally:
            conn.close()

    for repetition in range(REPEAT):
        errors = run_threads([increment] * THREADS, timeout=60)
        assert errors == [], (repetition, errors)
        assert _counter(path) == THREADS * (repetition + 1), repetition


def test_t003_deferred_writes_fail_without_tabrion(path: Path) -> None:
    """Negative control: the same read-then-write with a plain DEFERRED transaction (what q2.py did) fails every time,
    so the positive test above can fail. All threads read before any writes (barrier); the writers whose snapshot is
    then stale get ``SQLITE_BUSY`` (``Kind.LOCKED``) at once, which no busy timeout retries."""
    for repetition in range(REPEAT):
        read_done = threading.Barrier(THREADS)

        def deferred() -> None:
            conn = sqlite3.connect(str(path), isolation_level=None, timeout=5)
            try:
                conn.execute("BEGIN")
                n = conn.execute("SELECT n FROM counter WHERE id = 1").fetchone()[0]
                read_done.wait(10)
                conn.execute("UPDATE counter SET n = ? WHERE id = 1", (n + 1,))
                conn.execute("COMMIT")
            finally:
                conn.close()

        before = _counter(path)
        errors = run_threads([deferred] * THREADS, timeout=60)
        assert errors, repetition
        assert all(isinstance(e, sqlite3.OperationalError) and classify(e).kind is Kind.LOCKED for e in errors), errors
        assert _counter(path) - before == THREADS - len(errors) < THREADS


# --- Readers see a consistent snapshot while writers run (WAL) -----------------------------------------------------


def test_wal_readers_never_block_and_see_consistent_sums(path: Path) -> None:
    """Writers move money between accounts; readers in ``read_transaction`` always see the total of 10 000 and never
    wait for a writer (WAL), also while ``wal_checkpoint`` runs."""
    for _ in range(REPEAT):
        stop = threading.Event()
        sums: list[int] = []

        def writer(seed: int) -> None:
            conn = ts.connect(path)
            try:
                for i in range(20):
                    a, b = (seed + i) % 10 + 1, (seed + 3 * i + 1) % 10 + 1
                    with ts.write_transaction(conn):
                        conn.execute("UPDATE account SET balance = balance - 7 WHERE id = ?", (a,))
                        conn.execute("UPDATE account SET balance = balance + 7 WHERE id = ?", (b,))
                    if i % 10 == 0:
                        conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
            finally:
                conn.close()

        def reader() -> None:
            conn = ts.connect(path, read_only=True)
            try:
                while not stop.is_set():
                    with ts.read_transaction(conn):
                        sums.append(int(conn.execute("SELECT sum(balance) FROM account").fetchone()[0]))
            finally:
                conn.close()

        readers = [threading.Thread(target=reader, daemon=True) for _ in range(2)]
        for thread in readers:
            thread.start()
        errors = run_threads([functools.partial(writer, seed) for seed in range(3)], timeout=60)
        stop.set()
        for thread in readers:
            thread.join(30)
        assert errors == []
        assert sums and set(sums) == {10_000}, set(sums)


# --- Busy timeout: bounded waits ----------------------------------------------------------------------------------


def test_busy_timeout_waits_and_gives_up_in_bounded_time(path: Path) -> None:
    """A holder keeps the write lock for 0.3 s: a writer with ``busy_timeout`` 5 s waits and succeeds; one with
    0.05 s gives up with ``Kind.LOCKED`` well before the holder ends. Deterministic through events, not sleeps."""
    for _ in range(REPEAT):
        holding, release = threading.Event(), threading.Event()

        def holder() -> None:
            conn = ts.connect(path)
            try:
                with ts.write_transaction(conn):
                    conn.execute("UPDATE counter SET n = n + 1 WHERE id = 1")
                    holding.set()
                    release.wait(10)
            finally:
                conn.close()

        thread = threading.Thread(target=holder, daemon=True)
        thread.start()
        assert holding.wait(10)
        impatient = _connect(path, busy_timeout=0.05)
        patient = _connect(path, busy_timeout=5.0)
        try:
            started = time.monotonic()
            with pytest.raises(sqlite3.OperationalError) as info, ts.write_transaction(impatient):
                pass
            assert classify(info.value).kind is Kind.LOCKED and classify(info.value).retryable
            assert time.monotonic() - started < 2.0
            threading.Timer(0.3, release.set).start()
            started = time.monotonic()
            with ts.write_transaction(patient):
                patient.execute("UPDATE counter SET n = n + 1 WHERE id = 1")
            assert 0.2 <= time.monotonic() - started < 5.0
        finally:
            release.set()
            thread.join(10)
            impatient.close()
            patient.close()
    assert _counter(path) == 2 * REPEAT


# --- Backup during writes ------------------------------------------------------------------------------------------


def test_backup_during_writes_is_a_consistent_snapshot(path: Path, tmp_path: Path) -> None:
    """``backup`` (stepwise, 1 page per step, so writers interleave) while two writers transfer money: every copy passes
    ``integrity_check``, holds the total of 10 000 and a counter value between the values before and after."""
    for repetition in range(REPEAT):
        stop, writing = threading.Event(), threading.Event()

        def writer(seed: int) -> None:
            conn = ts.connect(path)
            try:
                i = 0
                while not stop.is_set() and i < 500:
                    with ts.write_transaction(conn):
                        conn.execute("UPDATE account SET balance = balance - 1 WHERE id = ?", ((seed + i) % 10 + 1,))
                        conn.execute(
                            "UPDATE account SET balance = balance + 1 WHERE id = ?", ((seed + 2 * i) % 10 + 1,)
                        )
                        conn.execute("UPDATE counter SET n = n + 1 WHERE id = 1")
                    writing.set()
                    i += 1
            finally:
                conn.close()

        threads = [threading.Thread(target=writer, args=(s,), daemon=True) for s in range(2)]
        before = _counter(path)
        for thread in threads:
            thread.start()
        assert writing.wait(10)  # the copy starts while the writers run
        source = ts.connect(path)
        target = tmp_path / f"copy{repetition}.db"
        try:
            result = ts.backup(source, target, pages_per_step=1)
        finally:
            stop.set()
            for thread in threads:
                thread.join(30)
            source.close()
        after = _counter(path)
        assert result.quick_check == "ok"
        copy = ts.connect(target, read_only=True)
        try:
            assert ts.integrity_check(copy).ok
            assert copy.execute("SELECT sum(balance) FROM account").fetchone()[0] == 10_000
            copied = copy.execute("SELECT n FROM counter").fetchone()[0]
        finally:
            copy.close()
        assert before <= copied <= after
        os.unlink(target)


# --- TAB-T-005: two processes x 500 write transactions -------------------------------------------------------------

_WRITER = r"""
import json, sqlite3, sys, time
from pathlib import Path
from tabrion import sqlite as ts
from tabrion.errors import Kind, classify

path, go, name, count = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], int(sys.argv[4])
conn = ts.connect(path)
deadline = time.monotonic() + 60
while not go.exists():
    if time.monotonic() > deadline:
        sys.exit("no start signal")
    time.sleep(0.005)
locked = 0
for i in range(count):
    try:
        with ts.write_transaction(conn):
            n = conn.execute("SELECT n FROM counter WHERE id = 1").fetchone()[0]
            conn.execute("UPDATE counter SET n = ? WHERE id = 1", (n + 1,))
            conn.execute("INSERT INTO log (process, i) VALUES (?, ?)", (name, i))
    except sqlite3.OperationalError as exc:
        if classify(exc).kind is not Kind.LOCKED:
            raise
        locked += 1
conn.close()
print(json.dumps({"name": name, "locked": locked}))
"""


def test_t005_two_processes_500_writes_each(path: Path, tmp_path: Path) -> None:
    """TAB-T-005 (07 DI-03): two processes run 500 read-then-write transactions each on one WAL file; 0 lost updates,
    0 lock errors, every row of both logs present. Repeated ``max(1, REPEAT // 5)`` times (once on pull requests)."""
    conn = ts.connect(path)
    conn.execute("CREATE TABLE log (process TEXT NOT NULL, i INTEGER NOT NULL, PRIMARY KEY (process, i))")
    conn.close()
    script = tmp_path / "writer.py"
    script.write_text(_WRITER, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    env["PYTHONPATH"] = str(SRC)
    count = 500
    for repetition in range(max(1, REPEAT // 5)):
        go = tmp_path / f"go{repetition}"
        names = [f"p{repetition}a", f"p{repetition}b"]
        processes = [
            subprocess.Popen(
                [sys.executable, str(script), str(path), str(go), name, str(count)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )
            for name in names
        ]
        go.touch()
        outputs = []
        try:
            outputs = [p.communicate(timeout=120) for p in processes]
        finally:
            for p in processes:
                if p.poll() is None:
                    p.kill()
        assert [p.returncode for p in processes] == [0, 0], outputs
        assert [json.loads(out)["locked"] for out, _ in outputs] == [0, 0]
        assert _counter(path) == 2 * count * (repetition + 1)
        check = ts.connect(path, read_only=True)
        try:
            rows = dict(check.execute("SELECT process, count(*) FROM log GROUP BY process").fetchall())
        finally:
            check.close()
        assert all(rows[name] == count for name in names), rows


# --- Layer B: threads on one Database ------------------------------------------------------------------------------

sqlalchemy = pytest.importorskip("sqlalchemy")
from tabrion import Database  # noqa: E402


def test_t004_layer_b_threads_no_lost_update(path: Path) -> None:
    """TAB-T-004/B07 repeated: 5 threads share one ``Database``, read and write in ``transaction()``."""
    with Database.from_url(f"sqlite:///{path}") as db:

        def increment() -> None:
            with db.transaction() as tx:
                n = tx.fetch_scalar("SELECT n FROM counter WHERE id = 1")
                time.sleep(0.002)
                tx.execute("UPDATE counter SET n = :n WHERE id = 1", {"n": n + 1})

        def read() -> None:
            for _ in range(5):
                assert db.fetch_scalar("SELECT sum(balance) FROM account") == 10_000

        for repetition in range(REPEAT):
            errors = run_threads([increment] * THREADS + [read], timeout=60)
            assert errors == [], (repetition, errors)
            assert db.fetch_scalar("SELECT n FROM counter WHERE id = 1") == THREADS * (repetition + 1)


# --- The time bound itself -----------------------------------------------------------------------------------------

_HANGING = """
import sys, threading, time
sys.path.insert(0, {root!r})
from tests.stress import hang_guard

def test_hangs(request):
    with hang_guard(request.config, 1):
        threading.Event().wait(60)  # a deadlock stand-in
"""


def test_hang_guard_ends_a_hanging_run(pytester: pytest.Pytester) -> None:
    """A test that hangs ends the whole run within the guard's time, with every thread's traceback on stderr."""
    from tests.conftest import ROOT

    pytester.makepyfile(_HANGING.format(root=str(ROOT)))
    started = time.monotonic()
    result = pytester.runpytest_subprocess("-p", "no:cacheprovider", timeout=60)
    assert time.monotonic() - started < 30
    assert result.ret != 0
    output = result.stdout.str() + result.stderr.str()
    assert "Timeout" in output and "test_hangs" in output, output
