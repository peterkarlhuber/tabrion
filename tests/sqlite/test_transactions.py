"""TAB-014: ``write_transaction`` and ``read_transaction`` (TAB-T-S16..S21; 07 DI-02, TAB-T-002/003).

Contract section 3.4 and the T-NOTX rule; implementation notes I-17 and I-18.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import sqlite3
import sys
import threading
import time
from typing import Any

import pytest

from tabrion import sqlite as ts
from tabrion.errors import Kind, UsageError, classify
from tabrion.sqlite import SQLiteProfile
from tests.sqlite.conftest import plain

Opened = Callable[[sqlite3.Connection], sqlite3.Connection]


@pytest.fixture
def db(tmp_path: Path, opened: Opened) -> sqlite3.Connection:
    connection = opened(ts.connect(tmp_path / "a.db"))
    connection.execute("CREATE TABLE counter (id INTEGER PRIMARY KEY, n INTEGER NOT NULL)")
    connection.execute("INSERT INTO counter VALUES (1, 0)")
    return connection


def _n(connection: sqlite3.Connection) -> int:
    return int(connection.execute("SELECT n FROM counter WHERE id = 1").fetchone()[0])


def test_s16_lost_update_five_threads(tmp_path: Path, db: sqlite3.Connection) -> None:
    """TAB-T-S16 / TAB-T-002 / TAB-T-003 (probes q.py, q2.py): read, wait, write in 5 threads; result 5, 0 errors."""
    errors: list[BaseException] = []

    def worker() -> None:
        connection = ts.connect(tmp_path / "a.db")
        try:
            with ts.write_transaction(connection):
                n = _n(connection)
                time.sleep(0.05)  # widen the window between the read and the write
                connection.execute("UPDATE counter SET n = ? WHERE id = 1", (n + 1,))
        except BaseException as exc:
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert _n(db) == 5


def test_s17_exception_rolls_back(db: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="boom"), ts.write_transaction(db) as connection:
        assert connection is db and db.in_transaction
        db.execute("UPDATE counter SET n = 42")
        raise ValueError("boom")
    assert not db.in_transaction
    assert _n(db) == 0


def test_s17_commit_on_normal_exit(tmp_path: Path, db: sqlite3.Connection, opened: Opened) -> None:
    with ts.write_transaction(db):
        db.execute("UPDATE counter SET n = 7")
    assert not db.in_transaction
    other = opened(ts.connect(tmp_path / "a.db"))
    assert _n(other) == 7


def test_s17_begin_immediate_takes_the_write_lock(tmp_path: Path, db: sqlite3.Connection, opened: Opened) -> None:
    other = opened(ts.connect(tmp_path / "a.db", profile=SQLiteProfile(busy_timeout=0)))
    with ts.write_transaction(db):
        with pytest.raises(sqlite3.OperationalError) as info:
            other.execute("BEGIN IMMEDIATE")
        assert classify(info.value).kind is Kind.LOCKED
    assert not other.in_transaction


@pytest.mark.parametrize(
    ("outer", "inner"),
    [
        (ts.write_transaction, ts.write_transaction),
        (ts.write_transaction, ts.read_transaction),
        (ts.read_transaction, ts.write_transaction),
        (ts.read_transaction, ts.read_transaction),
    ],
)
def test_s18_nesting_is_a_usage_error(db: sqlite3.Connection, outer: object, inner: object) -> None:
    with outer(db):  # type: ignore[operator]
        with pytest.raises(UsageError) as info, inner(db):  # type: ignore[operator]
            pass
        assert info.value.code == "sqlite.nested_transaction"
        assert db.in_transaction  # the outer transaction is untouched
    assert not db.in_transaction


def test_s18_caller_transaction_is_untouched(db: sqlite3.Connection) -> None:
    """T-NOTX: a transaction the caller opened is neither committed nor rolled back."""
    db.execute("BEGIN")
    db.execute("UPDATE counter SET n = 3")
    for manager in (ts.write_transaction, ts.read_transaction):
        with pytest.raises(UsageError), manager(db):
            pass
        assert db.in_transaction
    assert _n(db) == 3
    db.execute("ROLLBACK")
    assert _n(db) == 0


def test_isolation_level_must_be_none(tmp_path: Path, opened: Opened) -> None:
    connection = opened(sqlite3.connect(tmp_path / "a.db"))  # isolation_level "" (implicit BEGIN)
    for manager in (ts.write_transaction, ts.read_transaction):
        with pytest.raises(UsageError) as info, manager(connection):
            pass
        assert info.value.code == "sqlite.not_autocommit"
    with pytest.raises(UsageError):
        ts.migrate(connection, {}, target=0, backup=None)


@pytest.mark.skipif(sys.version_info < (3, 12), reason="the autocommit attribute arrived in Python 3.12")
def test_python_312_autocommit_attribute(tmp_path: Path, opened: Opened) -> None:
    connect: Any = sqlite3.connect  # the stubs for Python 3.11 have no ``autocommit``
    on = opened(connect(tmp_path / "a.db", autocommit=True))
    with ts.write_transaction(on):
        on.execute("CREATE TABLE t (x)")
    assert not on.in_transaction
    off = opened(connect(tmp_path / "b.db", autocommit=False))
    with pytest.raises(UsageError) as info, ts.write_transaction(off):
        pass
    assert info.value.code == "sqlite.not_autocommit"


def test_s19_read_transaction_blocks_writes(db: sqlite3.Connection) -> None:
    with ts.read_transaction(db):
        assert db.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError) as info:
            db.execute("UPDATE counter SET n = 1")
        assert classify(info.value).kind is Kind.READ_ONLY
    assert _n(db) == 0


def test_s19_read_transaction_is_a_snapshot(tmp_path: Path, db: sqlite3.Connection, opened: Opened) -> None:
    writer = opened(ts.connect(tmp_path / "a.db"))
    with ts.read_transaction(db):
        assert _n(db) == 0
        with ts.write_transaction(writer):  # WAL: a reader does not block the writer
            writer.execute("UPDATE counter SET n = 9")
        assert _n(db) == 0
    assert _n(db) == 9


def test_s19_without_enforce_writes_are_kept(db: sqlite3.Connection) -> None:
    with ts.read_transaction(db, enforce=False):
        assert db.execute("PRAGMA query_only").fetchone()[0] == 0
        db.execute("UPDATE counter SET n = 2")
    assert _n(db) == 2


def test_s20_query_only_is_off_afterwards(db: sqlite3.Connection) -> None:
    with ts.read_transaction(db):
        pass
    assert db.execute("PRAGMA query_only").fetchone()[0] == 0
    with pytest.raises(ZeroDivisionError), ts.read_transaction(db):
        _ = 1 / 0
    assert db.execute("PRAGMA query_only").fetchone()[0] == 0
    assert not db.in_transaction
    db.execute("UPDATE counter SET n = 1")


def test_s20_query_only_of_a_read_only_connection_stays_on(tmp_path: Path, db: sqlite3.Connection) -> None:
    """Contract I-18: the previous value is restored, so ``connect(read_only=True)`` keeps ``query_only=ON``."""
    reader = ts.connect(tmp_path / "a.db", read_only=True)
    try:
        with ts.read_transaction(reader):
            pass
        assert reader.execute("PRAGMA query_only").fetchone()[0] == 1
    finally:
        reader.close()


def test_s21_keyboard_interrupt_rolls_back(db: sqlite3.Connection) -> None:
    for manager in (ts.write_transaction, ts.read_transaction):
        with pytest.raises(KeyboardInterrupt), manager(db):
            if manager is ts.write_transaction:
                db.execute("UPDATE counter SET n = 5")
            raise KeyboardInterrupt
        assert not db.in_transaction
        assert db.execute("PRAGMA query_only").fetchone()[0] == 0
    assert _n(db) == 0


def test_failing_commit_is_rolled_back(db: sqlite3.Connection) -> None:
    """Contract I-17: a COMMIT that fails (a deferred foreign key) leaves the connection idle."""
    db.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
    db.execute("CREATE TABLE child (p INTEGER REFERENCES parent (id) DEFERRABLE INITIALLY DEFERRED)")
    with pytest.raises(sqlite3.IntegrityError) as info, ts.write_transaction(db):
        db.execute("INSERT INTO child VALUES (99)")
    assert classify(info.value).kind is Kind.FOREIGN_KEY
    assert not db.in_transaction
    assert db.execute("SELECT count(*) FROM child").fetchone()[0] == 0


def test_block_that_ends_the_transaction(db: sqlite3.Connection) -> None:
    with pytest.raises(UsageError) as info, ts.write_transaction(db):
        db.execute("COMMIT")
    assert info.value.code == "sqlite.transaction_ended"
    assert not db.in_transaction
    with pytest.raises(UsageError), ts.read_transaction(db):
        db.execute("ROLLBACK")
    assert db.execute("PRAGMA query_only").fetchone()[0] == 0


def test_rollback_error_is_suppressed_only_without_a_transaction(db: sqlite3.Connection) -> None:
    """A block that rolled back itself and then failed: the original error, not "no transaction is active"."""
    with pytest.raises(ValueError, match="original"), ts.write_transaction(db):
        db.execute("ROLLBACK")
        raise ValueError("original")
    assert not db.in_transaction


def test_plain_connection_works(tmp_path: Path, opened: Opened) -> None:
    connection = opened(plain(tmp_path / "a.db"))
    with ts.write_transaction(connection):
        connection.execute("CREATE TABLE t (x)")
    with ts.read_transaction(connection):
        assert connection.execute("SELECT count(*) FROM t").fetchone()[0] == 0
