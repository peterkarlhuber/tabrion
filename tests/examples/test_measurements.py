"""RA-B-1, measurements capture on SQLite (``examples/measurements``), planning document 09 acceptance:

1. a batch of 10 000 measurements in one ``write_transaction``;
2. ``migrate`` with mandatory backup;
3. the database file (and its side files and backups) ``0600``;
4. a process killed during the batch leaves no partial set (``SIGKILL`` in a subprocess: zero or all rows).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
import io
import math
import os
from pathlib import Path
import signal
import sqlite3
import stat
import subprocess
import sys

from measurements import SCHEMA_VERSION, Measurement, Store, open_database
from measurements.cli import main
from measurements.db import MIGRATIONS
from measurements.store import synthetic
import pytest

from tabrion import sqlite as tsqlite
from tabrion.errors import BackupFailed, BackupRequired

from .conftest import EXAMPLES

POSIX = pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes and SIGKILL")


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def user_version(path: Path) -> int:
    connection = sqlite3.connect(path)
    try:
        return int(connection.execute("PRAGMA user_version").fetchone()[0])
    finally:
        connection.close()


def test_batch_of_10000_in_one_write_transaction(tmp_path: Path) -> None:
    connection = open_database(tmp_path / "m.db", backup_dir=tmp_path / "backups")
    try:
        statements: list[str] = []
        connection.set_trace_callback(statements.append)
        store = Store(connection)
        assert store.capture(synthetic(10_000)) == 10_000
        connection.set_trace_callback(None)
        assert [s for s in statements if s.split()[0] in {"BEGIN", "COMMIT", "ROLLBACK"}] == [
            "BEGIN IMMEDIATE",
            "COMMIT",
        ]
        assert sum(1 for s in statements if s.startswith("INSERT")) == 10_000  # one executemany, one transaction
        assert store.count() == 10_000
        assert store.capture(synthetic(10_000)) == 0  # the natural key: captured twice, stored once
        assert store.count() == 10_000
    finally:
        connection.close()


def test_fresh_database_is_migrated_without_backup(tmp_path: Path) -> None:
    open_database(tmp_path / "m.db", backup_dir=tmp_path / "backups").close()
    assert user_version(tmp_path / "m.db") == SCHEMA_VERSION
    assert not (tmp_path / "backups").exists()  # an empty file has nothing to back up


def test_existing_database_is_backed_up_before_migrating(tmp_path: Path) -> None:
    path, backups = tmp_path / "m.db", tmp_path / "backups"
    connection = open_database(path, backup_dir=backups, target=1)
    Store(connection).capture(synthetic(50))
    connection.close()

    connection = open_database(path, backup_dir=backups)
    try:
        assert Store(connection).count() == 50
    finally:
        connection.close()
    assert user_version(path) == SCHEMA_VERSION
    (copy,) = backups.iterdir()
    assert copy.name.startswith(f"measurements-v1-to-v{SCHEMA_VERSION}-")
    assert user_version(copy) == 1
    with sqlite3.connect(copy) as check:
        assert check.execute("SELECT count(*) FROM measurement").fetchone()[0] == 50
    check.close()


def test_backup_is_mandatory(tmp_path: Path) -> None:
    """Without a backup Tabrion refuses to migrate a database that holds data; a failed backup changes nothing."""
    path = tmp_path / "m.db"
    connection = open_database(path, backup_dir=tmp_path / "backups", target=1)
    try:
        Store(connection).capture(synthetic(5))
        with pytest.raises(BackupRequired):
            tsqlite.migrate(connection, MIGRATIONS, target=SCHEMA_VERSION, backup=None)
    finally:
        connection.close()
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("")
    with pytest.raises(BackupFailed):
        open_database(path, backup_dir=blocker)
    assert user_version(path) == 1


@POSIX
def test_files_are_owner_only(tmp_path: Path) -> None:
    path, backups = tmp_path / "m.db", tmp_path / "backups"
    old = os.umask(0o022)  # a permissive umask must not decide the modes
    try:
        open_database(path, backup_dir=backups, target=1).close()
        connection = open_database(path, backup_dir=backups)
        Store(connection).capture(synthetic(10))
        side_files = [Path(f"{path}-wal"), Path(f"{path}-shm")]
        assert all(side.exists() for side in side_files)
        assert [mode(p) for p in [path, *side_files]] == [0o600, 0o600, 0o600]
        connection.close()
    finally:
        os.umask(old)
    assert mode(backups) == 0o700
    assert [mode(p) for p in backups.iterdir()] == [0o600]


KILLED_CHILD = """
import sys, time
from measurements import Store, open_database
from measurements.store import synthetic

connection = open_database(sys.argv[1], backup_dir=sys.argv[2])
before = connection.total_changes
calls = 0
when = sys.argv[3]


def progress():
    global calls
    calls += 1
    if when == "during" and calls == 20:
        # Inside the batch's transaction, with part of it written: report how much, then wait to be killed.
        print("inside", connection.in_transaction, connection.total_changes - before, flush=True)
        time.sleep(60)
    return 0


connection.set_progress_handler(progress, 1000)
stored = Store(connection).capture(synthetic(10_000))
print("committed", stored, flush=True)
time.sleep(60)
"""


@POSIX
@pytest.mark.parametrize("when", ["during", "after"])
def test_kill_during_the_batch_leaves_zero_or_all(tmp_path: Path, when: str) -> None:
    path = tmp_path / "m.db"
    open_database(path, backup_dir=tmp_path / "backups").close()
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(EXAMPLES / "measurements"), *sys.path])}
    child = subprocess.Popen(
        [sys.executable, "-c", KILLED_CHILD, str(path), str(tmp_path / "backups"), when],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        assert child.stdout is not None
        line = child.stdout.readline().split()
        if when == "during":
            assert line[:2] == ["inside", "True"], line
            assert 0 < int(line[2]) < 10_000  # part of the batch was written in the open transaction
        else:
            assert line == ["committed", "10000"], line
        child.send_signal(signal.SIGKILL)
        child.wait(timeout=30)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
        child.stdout and child.stdout.close()
        child.stderr and child.stderr.close()
    assert child.returncode == -signal.SIGKILL
    connection = open_database(path, backup_dir=tmp_path / "backups")
    try:
        assert Store(connection).count() == (0 if when == "during" else 10_000)
        assert tsqlite.integrity_check(connection).ok
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("value", math.nan, "value must be a finite number"),
        ("value", math.inf, "value must be a finite number"),
        ("value", True, "value must be a finite number"),
        ("ts_utc", datetime(2026, 1, 1), "ts_utc must be timezone-aware"),
        ("device_id", "", "device_id must be a str of 1 to 64 characters"),
        ("unit", "CANARY-" + "x" * 20, "unit must be a str of 1 to 16 characters"),
        ("quality", 256, "quality must be an int from 0 to 255"),
    ],
)
def test_invalid_measurement_writes_nothing(tmp_path: Path, field: str, value: object, message: str) -> None:
    connection = open_database(tmp_path / "m.db", backup_dir=tmp_path / "backups")
    try:
        good = list(synthetic(3))
        bad = Measurement(**{**{f: getattr(good[0], f) for f in Measurement.__slots__}, field: value})
        with pytest.raises(ValueError, match=r"^measurement 2: ") as info:
            Store(connection).capture([*good[:2], bad])
        assert str(info.value) == f"measurement 2: {message}"
        assert "CANARY" not in str(info.value) and info.value.__context__ is None
        assert not connection.in_transaction
        assert Store(connection).count() == 0
    finally:
        connection.close()


def test_timestamps_are_stored_in_utc_and_read_back_in_order(tmp_path: Path) -> None:
    connection = open_database(tmp_path / "m.db", backup_dir=tmp_path / "backups")
    try:
        store = Store(connection)
        vienna = timezone(timedelta(hours=1))
        later = Measurement("d", "c", datetime(2026, 1, 1, 13, 0, tzinfo=vienna), 2.5, "V")
        earlier = Measurement("d", "c", datetime(2026, 1, 1, 11, 30, tzinfo=UTC), 1.5, "V", quality=3)
        assert store.capture([later, earlier]) == 2
        stored = store.after(0)
        assert [s.seq for s in stored] == [1, 2]
        assert stored[0].measurement.ts_utc == datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        assert stored[1].measurement == earlier
        assert [s.seq for s in store.after(1)] == [2]
        raw = connection.execute("SELECT ts_utc FROM measurement ORDER BY ts_utc").fetchall()
        assert raw == [("2026-01-01T11:30:00.000000Z",), ("2026-01-01T12:00:00.000000Z",)]
    finally:
        connection.close()


def test_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    base = ["--db", str(tmp_path / "m.db"), "--backup-dir", str(tmp_path / "backups")]
    assert main([*base, "init"]) == 0
    assert main([*base, "demo", "100"]) == 0
    assert capsys.readouterr().out.startswith("100 stored in ")
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            "# device,channel,ts,value,unit\n"
            "d2,rh,2026-01-01T00:00:00Z,41.5,%\n"
            "\n"
            "d2,rh,2026-01-01T00:00:01+00:00,41.7,%,1\n"
        ),
    )
    assert main([*base, "capture"]) == 0
    assert capsys.readouterr().out == "2\n"
    assert main([*base, "count"]) == 0
    assert capsys.readouterr().out == "102\n"
