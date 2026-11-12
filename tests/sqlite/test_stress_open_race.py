"""TAB-T-123 (plan 07 TM-04, QA-4, TODO TB-045): randomized repetition tests of ``connect``'s path checks.

The deterministic tests in ``test_connect.py`` place a link in each window between the check and the open through
seams. These tests do the same without seams: a thread keeps replacing the database name, at random intervals, with
either the checked database or a link to another file, while ``connect`` runs. Each repetition is one ``connect``;
the test repeats ``RACE_REPEAT`` times, that is ``5 * TABRION_STRESS_REPEAT``: 25 on pull requests, 1,000 in the
nightly ``stress`` job (``tests/stress.py``). The invariant: no ``connect`` ever returns a connection to the other
file, and the other file is never changed (0 unsafe opens). A control shows that a plain ``sqlite3.connect`` does
open the other file under the same load, so the race is real.

The second test is the other side (TODO TB-045, false refusals): under a realistic pool load (a few threads opening
and closing connections while another connection holds the database, and an occasional unrelated file open), a
correct file is never refused with ``UnsafePath``. Marked ``stress`` and ``slow``; POSIX only, because Windows has no
descriptor check (TAB-T-S12) and may not create links.
"""

from __future__ import annotations

from collections.abc import Iterator
import os
from pathlib import Path
import random
import sqlite3
import sys
import threading
import time

import pytest

from tabrion import sqlite as ts
from tabrion.errors import UnsafePath
from tests.sqlite.conftest import can_symlink, digest, posix_only
from tests.stress import hang_guard, repeat, run_threads

pytestmark = [pytest.mark.stress, pytest.mark.slow, posix_only]

RACE_REPEAT = 5 * repeat()
"""TAB-T-123 asks for 1,000 repetitions: 5 times the stress repetitions, so 1,000 at the nightly 200."""
POOL_THREADS = 3
CONNECTS_PER_THREAD = 5


@pytest.fixture(autouse=True)
def bounded(request: pytest.FixtureRequest) -> Iterator[None]:
    with hang_guard(request.config, 120 + RACE_REPEAT):
        yield


def _database(path: Path, tag: str, *, wal: bool) -> None:
    connection = sqlite3.connect(str(path), isolation_level=None)
    try:
        if wal:
            connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("CREATE TABLE tag (value TEXT NOT NULL)")
        connection.execute("INSERT INTO tag VALUES (?)", (tag,))
    finally:
        connection.close()


def _tag(connection: sqlite3.Connection) -> str:
    return str(connection.execute("SELECT value FROM tag").fetchone()[0])


class _Swapper:
    """Replaces ``name`` atomically, at random short intervals, by turns with a link to ``other`` and a hard link to
    ``right``.

    ``os.replace`` never leaves the name missing, so ``connect`` sees either the checked file or a link."""

    def __init__(self, name: Path, right: Path, other: Path) -> None:
        self._name = name
        self._right = right
        self._other = other
        self._stop = threading.Event()
        self._random = random.Random()  # noqa: S311 - timing jitter, not security
        self._thread = threading.Thread(target=self._run, daemon=True)
        self.swaps = 0
        self.error: BaseException | None = None

    def _run(self) -> None:
        staged = self._name.with_name(self._name.name + ".staged")
        try:
            while not self._stop.is_set():
                if self.swaps % 2:
                    os.link(self._right, staged)
                else:
                    os.symlink(self._other, staged)
                os.replace(staged, self._name)
                self.swaps += 1
                pause = self._random.choice((0.0, 0.00002, 0.0001, 0.0005))
                if pause:
                    time.sleep(pause)
        except BaseException as exc:  # reported by the test
            self.error = exc

    def next_swap(self) -> None:
        """Wait until the name changed once more, then a random moment, so each ``connect`` starts at another
        point of the swapping."""
        seen = self.swaps
        deadline = time.monotonic() + 10
        while self.swaps == seen and self.error is None and time.monotonic() < deadline:
            time.sleep(0)
        time.sleep(self._random.choice((0.0, 0.00001, 0.00005, 0.0002)))

    def __enter__(self) -> _Swapper:
        self._interval = sys.getswitchinterval()
        sys.setswitchinterval(0.00001)  # let the threads interleave densely
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join(10)
        sys.setswitchinterval(self._interval)
        assert not self._thread.is_alive(), "the swapping thread did not stop"


@pytest.fixture
def race(tmp_path: Path) -> tuple[Path, Path, Path]:
    """``(name, right, other)``: ``right`` is the checked database (WAL, 0600), ``other`` a database elsewhere that
    ``connect`` would accept by owner and mode (0644), so only the link checks stand between the two."""
    if not can_symlink(tmp_path):
        pytest.skip("symbolic links cannot be created here")
    store = tmp_path / "store"
    store.mkdir()
    right = store / "right.db"
    _database(right, "right", wal=True)
    right.chmod(0o600)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    other = elsewhere / "other.db"
    _database(other, "other", wal=False)
    other.chmod(0o644)
    work = tmp_path / "work"
    work.mkdir()
    name = work / "a.db"
    os.link(right, name)
    return name, right, other


def test_t123_randomized_link_swap_never_opens_the_other_file(race: tuple[Path, Path, Path]) -> None:
    """TAB-T-123: ``RACE_REPEAT`` connects while the name is swapped at random: 0 connections to the other file, the
    other file unchanged; every outcome is the checked database or a refusal (``UnsafePath``, or SQLite's own
    "unable to open" when the name changed while SQLite resolved it)."""
    name, right, other = race
    before, other_mode = digest(other), other.stat().st_mode & 0o777
    opened = refused = 0
    with _Swapper(name, right, other) as swapper:
        for repetition in range(RACE_REPEAT):
            swapper.next_swap()
            try:
                connection = ts.connect(name)
            except UnsafePath:
                refused += 1
                continue
            except sqlite3.OperationalError as exc:
                # SQLite itself can fail to open a name that changes while it resolves it: nothing was opened.
                assert "unable to open database file" in str(exc), repr(exc)
                refused += 1
                continue
            try:
                assert _tag(connection) == "right", repetition
            finally:
                connection.close()
            opened += 1
    assert swapper.error is None, swapper.error
    assert digest(other) == before
    assert other.stat().st_mode & 0o777 == other_mode
    assert opened + refused == RACE_REPEAT
    assert swapper.swaps > RACE_REPEAT  # the name changed before and while each connect ran
    assert refused > 0, (opened, refused, swapper.swaps)  # the links were seen, so the race was exercised
    assert opened > 0, (opened, refused, swapper.swaps)


def test_t123_control_plain_sqlite_opens_the_other_file(race: tuple[Path, Path, Path]) -> None:
    """Negative control: without the checks, the same swapping makes a plain read-only ``sqlite3.connect`` open the
    other file, so the test above can fail."""
    name, right, other = race
    seen = {"right": 0, "other": 0}
    with _Swapper(name, right, other) as swapper:
        for _ in range(max(RACE_REPEAT, 200)):
            swapper.next_swap()
            connection = sqlite3.connect(f"file:{name}?mode=ro", uri=True)
            try:
                seen[_tag(connection)] += 1
            except sqlite3.DatabaseError:
                pass  # a swap during the read: neither file decided
            finally:
                connection.close()
            if seen["other"] and seen["right"]:
                break
    assert swapper.error is None, swapper.error
    assert seen["other"] > 0, seen


def test_tb045_no_false_refusal_under_pool_load(tmp_path: Path) -> None:
    """TB-045: a realistic pool load never gets a false ``UnsafePath``. Per repetition, ``POOL_THREADS`` threads each
    open, read and close ``CONNECTS_PER_THREAD`` connections (each new connection opens the database's ``-wal`` and
    ``-shm``) while another connection holds the database, and a further thread opens an unrelated file now and then.
    ``RACE_REPEAT`` repetitions; 0 refusals."""
    path = tmp_path / "a.db"
    holder = ts.connect(path)
    unrelated = tmp_path / "unrelated.txt"
    unrelated.write_bytes(b"x" * 64)
    jitter = random.Random()  # noqa: S311 - timing jitter, not security
    try:
        holder.execute("CREATE TABLE tag (value TEXT NOT NULL)")
        holder.execute("INSERT INTO tag VALUES ('right')")
        for repetition in range(RACE_REPEAT):
            done = threading.Event()

            def pool() -> None:
                for _ in range(CONNECTS_PER_THREAD):
                    connection = ts.connect(path)
                    try:
                        assert _tag(connection) == "right"
                    finally:
                        connection.close()

            def other_files() -> None:
                while not done.is_set():
                    with unrelated.open("rb") as handle:
                        handle.read()
                    time.sleep(jitter.uniform(0.0005, 0.003))

            side = threading.Thread(target=other_files, daemon=True)
            side.start()
            try:
                errors = run_threads([pool] * POOL_THREADS, timeout=60)
            finally:
                done.set()
                side.join(10)
            assert errors == [], (repetition, errors)
            assert _tag(holder) == "right"
    finally:
        holder.close()
