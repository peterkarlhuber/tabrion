"""TAB-014: ``migrate`` (TAB-T-S22..S30; 07 DI-06, TAB-T-010/011). Contract section 3.5, implementation note I-19.

Every error path checks that the database file is byte-identical afterwards (SHA-256 of the closed file).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

import pytest

from tabrion import sqlite as ts
from tabrion.errors import (
    BackupFailed,
    BackupRequired,
    MigrationFailed,
    MigrationMissing,
    SchemaTooNew,
    UsageError,
)
from tabrion.sqlite import Migration, SQLiteProfile
from tests.conftest import SRC
from tests.sqlite.conftest import CANARY, digest, make_database

Opened = Callable[[sqlite3.Connection], sqlite3.Connection]


def _create(name: str) -> Migration:
    def step(connection: sqlite3.Connection) -> None:
        connection.execute(f"CREATE TABLE {name} (x)")

    return step


def _fail(connection: sqlite3.Connection) -> None:
    connection.execute("CREATE TABLE half_done (x)")
    raise RuntimeError(f"step failed with {CANARY}")


STEPS: dict[int, Migration] = {1: _create("t1"), 2: _create("t2"), 3: _create("t3"), 4: _create("t4")}


@pytest.fixture
def path(tmp_path: Path) -> Path:
    """A database with data (not fresh) at schema version 1."""
    path = tmp_path / "a.db"
    make_database(path)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA user_version = 1")
    connection.close()
    return path


@pytest.fixture
def unchanged(path: Path) -> Iterator[Callable[[], sqlite3.Connection]]:
    """Open the database; after the test, close it and compare the file with its bytes before the test."""
    before = digest(path)
    connections: list[sqlite3.Connection] = []

    def open_it() -> sqlite3.Connection:
        connection = ts.connect(path)
        connections.append(connection)
        return connection

    yield open_it
    for connection in connections:
        connection.close()
    assert digest(path) == before


class Backups:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []

    def __call__(self, current: int, target: int) -> None:
        self.calls.append((current, target))


def test_s22_current_version_writes_nothing(unchanged: Callable[[], sqlite3.Connection]) -> None:
    connection = unchanged()
    backups = Backups()
    assert ts.migrate(connection, {}, target=1, backup=backups) == ts.MigrationResult(1, 1, False)
    assert backups.calls == []


def test_s23_schema_too_new(unchanged: Callable[[], sqlite3.Connection]) -> None:
    """TAB-T-S23 / TAB-T-011: the database is newer than the code; nothing is written, no backup."""
    backups = Backups()
    with pytest.raises(SchemaTooNew) as info:
        ts.migrate(unchanged(), STEPS, target=0, backup=backups)
    assert info.value.code == "sqlite.schema_too_new"
    assert dict(info.value.details) == {"found": 1, "supported": 0}
    assert backups.calls == []


def test_s24_missing_step(unchanged: Callable[[], sqlite3.Connection]) -> None:
    backups = Backups()
    with pytest.raises(MigrationMissing) as info:
        ts.migrate(unchanged(), {2: STEPS[2], 4: STEPS[4]}, target=4, backup=backups)
    assert info.value.code == "sqlite.migration_missing"
    assert dict(info.value.details) == {"found": 1, "supported": 4, "missing": 3}
    assert backups.calls == []


def test_s25_fresh_database_needs_no_backup(tmp_path: Path, opened: Opened) -> None:
    connection = opened(ts.connect(tmp_path / "new.db"))
    result = ts.migrate(connection, STEPS, target=4, backup=None)
    assert result == ts.MigrationResult(0, 4, False)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 4
    backups = Backups()
    other = opened(ts.connect(tmp_path / "other.db"))
    assert ts.migrate(other, STEPS, target=2, backup=backups).backed_up is False
    assert backups.calls == []


def test_s25_is_fresh_hook(tmp_path: Path, opened: Opened) -> None:
    """Nodestro's own empty documents table makes a database "fresh" (``_is_empty``)."""
    connection = opened(ts.connect(tmp_path / "a.db"))
    connection.execute("CREATE TABLE nodestro_documents (id TEXT)")
    with pytest.raises(BackupRequired):
        ts.migrate(connection, STEPS, target=1, backup=None)
    hooked = ts.migrate(
        connection,
        STEPS,
        target=1,
        backup=None,
        is_fresh=lambda c: c.execute("SELECT count(*) FROM nodestro_documents").fetchone()[0] == 0,
    )
    assert hooked == ts.MigrationResult(0, 1, False)


def test_s25_sqlite_tables_do_not_count(tmp_path: Path, opened: Opened) -> None:
    connection = opened(ts.connect(tmp_path / "a.db"))
    connection.execute("CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT)")  # creates sqlite_sequence
    connection.execute("DROP TABLE t")
    assert connection.execute("SELECT count(*) FROM sqlite_master WHERE name = 'sqlite_sequence'").fetchone()[0]
    assert ts.migrate(connection, STEPS, target=1, backup=None).backed_up is False
    lookalike = opened(ts.connect(tmp_path / "b.db"))
    lookalike.execute("CREATE TABLE sqlitex (x)")  # "sqlite_%" with an unescaped "_" would match it
    with pytest.raises(BackupRequired):
        ts.migrate(lookalike, STEPS, target=1, backup=None)


def test_s26_backup_required(unchanged: Callable[[], sqlite3.Connection]) -> None:
    with pytest.raises(BackupRequired) as info:
        ts.migrate(unchanged(), STEPS, target=4, backup=None)
    assert info.value.code == "sqlite.backup_required"
    assert dict(info.value.details) == {"found": 1, "supported": 4}


def test_s27_backup_failed(unchanged: Callable[[], sqlite3.Connection]) -> None:
    """The backup error is ``original``, never ``__cause__``/``__context__``, and its text is not in the message."""
    problem = OSError(f"disk full at /secret/{CANARY}")

    def failing(current: int, target: int) -> None:
        raise problem

    with pytest.raises(BackupFailed) as info:
        ts.migrate(unchanged(), STEPS, target=4, backup=failing)
    error = info.value
    assert error.code == "sqlite.backup_failed"
    assert dict(error.details) == {"found": 1, "supported": 4}
    assert error.original is problem
    assert error.__cause__ is None and error.__context__ is None
    assert CANARY not in str(error) and CANARY not in repr(error)


def test_s27_backup_interrupt_propagates(unchanged: Callable[[], sqlite3.Connection]) -> None:
    def interrupted(current: int, target: int) -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        ts.migrate(unchanged(), STEPS, target=4, backup=interrupted)


def test_s28_failing_step_leaves_the_file_unchanged(path: Path, unchanged: Callable[[], sqlite3.Connection]) -> None:
    """TAB-T-S28 / TAB-T-010: step 3 of 4 fails; SHA-256 before = after; the backup was made first."""
    backups = Backups()
    connection = unchanged()
    with pytest.raises(MigrationFailed) as info:
        ts.migrate(connection, {**STEPS, 3: _fail}, target=4, backup=backups)
    error = info.value
    assert error.code == "sqlite.migration_failed"
    assert dict(error.details) == {"found": 1, "supported": 4, "step": 3}
    assert isinstance(error.original, RuntimeError)
    assert error.__cause__ is None and error.__context__ is None
    assert CANARY not in str(error) and CANARY not in repr(error)
    assert backups.calls == [(1, 4)]
    assert not connection.in_transaction
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
    tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert tables == {"parent", "child"}


def test_s28_failing_version_write(unchanged: Callable[[], sqlite3.Connection]) -> None:
    """A step that ends the transaction itself is a failure (it may have committed, contract I-19)."""

    def commits(connection: sqlite3.Connection) -> None:
        connection.execute("ROLLBACK")

    with pytest.raises(MigrationFailed) as info:
        ts.migrate(unchanged(), {**STEPS, 2: commits}, target=4, backup=Backups())
    assert info.value.details["step"] == 2
    assert isinstance(info.value.original, UsageError)
    assert info.value.original.code == "sqlite.transaction_ended"


def test_s29_order_of_backup_and_steps(path: Path, opened: Opened) -> None:
    events: list[str] = []
    connection = opened(ts.connect(path))

    def record(n: int) -> Migration:
        def step(db: sqlite3.Connection) -> None:
            assert db is connection and db.in_transaction
            events.append(f"step {n}")

        return step

    def backup(current: int, target: int) -> None:
        assert not connection.in_transaction
        events.append(f"backup {current}->{target}")

    result = ts.migrate(connection, {n: record(n) for n in range(1, 5)}, target=4, backup=backup)
    assert events == ["backup 1->4", "step 2", "step 3", "step 4"]
    assert result == ts.MigrationResult(1, 4, True)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 4
    assert not connection.in_transaction


def test_s29_backup_with_tabrion_backup(path: Path, tmp_path: Path, opened: Opened) -> None:
    """The intended pairing (Nodestro ``workspace.py:221-235``): ``backup`` writes the pre-migration copy."""
    connection = opened(ts.connect(path))
    copy = tmp_path / "before-v4.db"

    def before_migration(current: int, target: int) -> None:
        ts.backup(connection, copy)

    ts.migrate(connection, STEPS, target=4, backup=before_migration)
    restored = opened(ts.connect(copy, read_only=True))
    assert restored.execute("PRAGMA user_version").fetchone()[0] == 1


def test_usage_errors(path: Path, opened: Opened) -> None:
    connection = opened(ts.connect(path))
    connection.execute("BEGIN")
    with pytest.raises(UsageError) as info:
        ts.migrate(connection, STEPS, target=4, backup=Backups())
    assert info.value.code == "sqlite.nested_transaction"
    assert connection.in_transaction
    connection.execute("ROLLBACK")
    for target in (-1, True, 1.0, 2**31):
        with pytest.raises(UsageError) as info:
            ts.migrate(connection, STEPS, target=target, backup=Backups())  # type: ignore[arg-type]
        assert info.value.code == "sqlite.invalid_version"


def test_busy_begin_passes_the_driver_error(path: Path, opened: Opened) -> None:
    """``BEGIN IMMEDIATE`` that cannot get the write lock: no step ran, the driver error passes unchanged."""
    holder = opened(ts.connect(path))
    connection = opened(ts.connect(path, profile=SQLiteProfile(busy_timeout=0)))
    holder.execute("BEGIN IMMEDIATE")
    with pytest.raises(sqlite3.OperationalError):
        ts.migrate(connection, STEPS, target=4, backup=Backups())
    holder.execute("ROLLBACK")
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 1


def test_s30_a_concurrent_migration_is_seen(path: Path, opened: Opened) -> None:
    """Contract I-19: another connection migrated between the first read and ``BEGIN IMMEDIATE``."""
    connection = opened(ts.connect(path))
    other = opened(ts.connect(path))

    def backup_while_other_migrates(current: int, target: int) -> None:
        ts.migrate(other, STEPS, target=3, backup=lambda c, t: None)

    result = ts.migrate(connection, STEPS, target=4, backup=backup_while_other_migrates)
    assert result == ts.MigrationResult(1, 4, True)
    tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"t2", "t3", "t4"} <= tables  # t2 and t3 once (by the other connection), then t4

    newer_path = path.parent / "b.db"
    make_database(newer_path)
    late = opened(ts.connect(newer_path))
    third = opened(ts.connect(newer_path))

    def newer(current: int, target: int) -> None:
        ts.migrate(third, STEPS, target=3, backup=lambda c, t: None)

    with pytest.raises(SchemaTooNew) as info:
        ts.migrate(late, STEPS, target=2, backup=newer)
    assert dict(info.value.details) == {"found": 3, "supported": 2}
    assert not late.in_transaction
    assert late.execute("PRAGMA user_version").fetchone()[0] == 3


_PROCESS = """
import json, sys, time
from pathlib import Path
from tabrion import sqlite as ts

path, marker, name = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]

def step2(connection):
    connection.execute("INSERT INTO applied VALUES (2, ?)", (name,))
    marker.write_text("in step 2")
    time.sleep(1.0)

connection = ts.connect(path)
try:
    result = ts.migrate(connection, {2: step2}, target=2, backup=lambda c, t: None)
finally:
    connection.close()
print(json.dumps([result.previous, result.current, result.backed_up]))
"""


def test_s30_two_processes(tmp_path: Path, path: Path) -> None:
    """Two processes migrate at once: the second waits, sees version 2 and does nothing (each step runs once)."""
    setup = ts.connect(path)
    setup.execute("CREATE TABLE applied (step INTEGER, by TEXT)")
    setup.close()
    marker = tmp_path / "marker"
    script = tmp_path / "migrate_once.py"
    script.write_text(_PROCESS, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    env["PYTHONPATH"] = str(SRC)

    def start(name: str) -> subprocess.Popen[str]:
        return subprocess.Popen(
            [sys.executable, str(script), str(path), str(marker), name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )

    first = start("first")
    deadline = time.monotonic() + 60
    while not marker.exists():
        assert first.poll() is None, first.communicate()
        assert time.monotonic() < deadline
        time.sleep(0.02)
    second = start("second")
    outputs = [p.communicate(timeout=120) for p in (first, second)]
    assert [p.returncode for p in (first, second)] == [0, 0], outputs
    assert json.loads(outputs[0][0]) == [1, 2, True]
    assert json.loads(outputs[1][0]) == [2, 2, True]
    check = ts.connect(path)
    try:
        assert check.execute("SELECT step, by FROM applied").fetchall() == [(2, "first")]
        assert check.execute("PRAGMA user_version").fetchone()[0] == 2
    finally:
        check.close()
