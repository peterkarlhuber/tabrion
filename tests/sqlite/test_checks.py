"""TAB-014: ``integrity_check``, ``quick_check``, ``foreign_key_check`` (TAB-T-S35..S38) and ``diagnose``
(TAB-T-S39..S41). Contract sections 3.7 and 3.8, implementation notes I-29 and I-30.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
import sqlite3

import pytest

from tabrion import sqlite as ts
from tabrion.errors import UsageError
from tabrion.sqlite import SQLiteProfile
from tests.sqlite.conftest import CANARY, POSIX, make_database, plain

Opened = Callable[[sqlite3.Connection], sqlite3.Connection]


@pytest.fixture
def db(tmp_path: Path, opened: Opened) -> sqlite3.Connection:
    path = tmp_path / "a.db"
    make_database(path)
    return opened(ts.connect(path))


def _misindexed(path: Path, rows: int = 20) -> None:
    """A file whose index ``i`` disagrees with its table for every row (the schema text was rewritten)."""
    connection = plain(path)
    try:
        connection.execute("CREATE TABLE t (x TEXT, y TEXT)")
        connection.execute("CREATE INDEX i ON t (x)")
        values = [(f"{CANARY}-x{n}", f"{CANARY}-y{n}") for n in range(rows)]
        connection.executemany("INSERT INTO t VALUES (?, ?)", values)
        connection.execute("PRAGMA writable_schema = ON")
        connection.execute("UPDATE sqlite_master SET sql = 'CREATE INDEX i ON t (y)' WHERE name = 'i'")
        connection.execute("PRAGMA writable_schema = OFF")
    finally:
        connection.close()


def _damaged(path: Path) -> None:
    """A file with garbage in its table pages (the header and the schema page stay intact, so it opens)."""
    connection = plain(path)
    try:
        connection.execute("PRAGMA page_size = 4096")
        connection.execute("CREATE TABLE t (x TEXT)")
        connection.executemany("INSERT INTO t VALUES (?)", [(f"{CANARY}-{n}" * 20,) for n in range(400)])
    finally:
        connection.close()
    data = bytearray(path.read_bytes())
    for page in range(2, len(data) // 4096, 2):
        start = page * 4096 + 16
        data[start : start + 200] = b"\xa5" * 200
    path.write_bytes(bytes(data))


def test_s35_good_database(db: sqlite3.Connection) -> None:
    for check in (ts.integrity_check, ts.quick_check):
        assert check(db) == ts.IntegrityReport(ok=True, messages=(), truncated=False)
    assert ts.foreign_key_check(db) == []


def test_s35_checks_run_in_a_read_transaction(db: sqlite3.Connection) -> None:
    """Idle: a short read transaction of their own; afterwards idle, ``query_only`` off (T-NOTX)."""
    statements: list[str] = []
    db.set_trace_callback(statements.append)
    ts.integrity_check(db)
    ts.foreign_key_check(db)
    db.set_trace_callback(None)
    assert statements.count("BEGIN") == 2
    assert not db.in_transaction
    assert db.execute("PRAGMA query_only").fetchone()[0] == 0


def test_s35_checks_inside_the_callers_transaction(db: sqlite3.Connection) -> None:
    db.execute("BEGIN IMMEDIATE")
    db.execute("INSERT INTO child (id, parent_id) VALUES (1, 0)")
    assert ts.quick_check(db).ok
    assert ts.foreign_key_check(db) == []
    ts.diagnose(db, run_quick_check=True)
    assert db.in_transaction
    db.execute("ROLLBACK")


def test_s36_damaged_file_gives_ok_false(tmp_path: Path, opened: Opened) -> None:
    """TAB-T-S36: a damaged file that still opens gives ``ok=False``, not an exception; no row data in messages."""
    path = tmp_path / "damaged.db"
    _damaged(path)
    connection = opened(ts.connect(path, read_only=True))
    for check in (ts.integrity_check, ts.quick_check):
        report = check(connection)
        assert report.ok is False
        assert report.messages
        assert not any(CANARY in message for message in report.messages), report.messages


def _error(cls: type[sqlite3.Error], name: str) -> sqlite3.Error:
    error = cls(f"stand-in for {name} {CANARY}")
    error.sqlite_errorname = name
    return error


def test_s36_corrupt_error_becomes_a_report(db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    """Contract I-29: when the check itself fails with SQLITE_CORRUPT, the report carries its safe message."""
    raised: list[sqlite3.Error] = []

    @contextmanager
    def failing(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
        raise raised[0]
        yield connection  # pragma: no cover

    monkeypatch.setattr(ts, "_reading", failing)
    raised.append(_error(sqlite3.DatabaseError, "SQLITE_CORRUPT"))
    report = ts.quick_check(db)
    assert report.ok is False and report.truncated is False
    assert len(report.messages) == 1 and "SQLITE_CORRUPT" in report.messages[0]
    assert CANARY not in report.messages[0]
    raised[0] = _error(sqlite3.OperationalError, "SQLITE_BUSY")  # any other error passes through
    with pytest.raises(sqlite3.OperationalError):
        ts.integrity_check(db)


def test_s37_integrity_messages_and_truncation(tmp_path: Path, opened: Opened) -> None:
    """TAB-T-S37: ``max_errors`` bounds the messages and ``truncated`` says whether SQLite had more."""
    path = tmp_path / "a.db"
    _misindexed(path)
    connection = opened(ts.connect(path))
    full = ts.integrity_check(connection)
    assert full.ok is False and full.truncated is False
    assert len(full.messages) > 3
    assert not any(CANARY in message for message in full.messages)
    first = ts.integrity_check(connection, max_errors=3)
    assert first.messages == full.messages[:3] and first.truncated is True
    exact = ts.integrity_check(connection, max_errors=len(full.messages))
    assert exact.messages == full.messages and exact.truncated is False
    assert ts.quick_check(connection).ok  # quick_check does not compare index content


def test_s37_invalid_limits(db: sqlite3.Connection) -> None:
    for value in (0, -1, True, 1.5, 2**31):
        with pytest.raises(UsageError) as info:
            ts.quick_check(db, max_errors=value)  # type: ignore[arg-type]
        assert info.value.code == "sqlite.invalid_limit"
        with pytest.raises(UsageError):
            ts.foreign_key_check(db, limit=value)  # type: ignore[arg-type]


def test_s38_foreign_key_check(db: sqlite3.Connection) -> None:
    """TAB-T-S38 and DI-01 (adoption check on existing files): violations, ``limit``, ``table``."""
    db.execute("PRAGMA foreign_keys = OFF")
    db.executemany("INSERT INTO child (id, parent_id) VALUES (?, ?)", [(n, 900 + n) for n in range(5)])
    db.execute("PRAGMA foreign_keys = ON")
    violations = ts.foreign_key_check(db)
    assert len(violations) == 5
    assert violations[0] == ts.ForeignKeyViolation(table="child", rowid=0, parent="parent", fk_index=0)
    assert len(ts.foreign_key_check(db, limit=2)) == 2
    assert len(ts.foreign_key_check(db, table="child")) == 5
    assert ts.foreign_key_check(db, table="parent") == []


def test_s38_table_name_is_quoted(db: sqlite3.Connection) -> None:
    name = 'odd "name"; DROP TABLE parent; --'
    quoted = '"odd ""name""; DROP TABLE parent; --"'
    db.execute(f"CREATE TABLE {quoted} (p INTEGER REFERENCES parent (id))")
    db.execute("PRAGMA foreign_keys = OFF")
    db.execute(f"INSERT INTO {quoted} VALUES (77)")  # noqa: S608 - a fixed test name
    assert [v.table for v in ts.foreign_key_check(db, table=name)] == [name]
    assert db.execute("SELECT count(*) FROM parent").fetchone()[0] == 3
    with pytest.raises(sqlite3.OperationalError):
        ts.foreign_key_check(db, table="no_such_table")
    with pytest.raises(UsageError) as info:
        ts.foreign_key_check(db, table="a\x00b")
    assert info.value.code == "sqlite.invalid_name"


def test_s38_without_rowid_table(db: sqlite3.Connection) -> None:
    db.execute("CREATE TABLE w (k TEXT PRIMARY KEY, p INTEGER REFERENCES parent (id)) WITHOUT ROWID")
    db.execute("PRAGMA foreign_keys = OFF")
    db.execute("INSERT INTO w VALUES ('a', 404)")
    assert ts.foreign_key_check(db, table="w") == [
        ts.ForeignKeyViolation(table="w", rowid=None, parent="parent", fk_index=0)
    ]


# --- diagnose --------------------------------------------------------------------------------------------------------


def test_s39_diagnose_a_file(tmp_path: Path, db: sqlite3.Connection) -> None:
    d = ts.diagnose(db)
    assert d.sqlite_version == sqlite3.sqlite_version
    assert d.path is not None and Path(d.path).name == "a.db"
    assert d.file_mode == ((tmp_path / "a.db").stat().st_mode & 0o777 if POSIX else None)
    assert d.is_symlink is False
    assert (d.journal_mode, d.synchronous, d.foreign_keys, d.busy_timeout_ms) == ("wal", "full", True, 30_000)
    assert (d.secure_delete, d.trusted_schema, d.query_only, d.user_version) == (True, False, False, 0)
    assert d.page_size > 0 and d.page_count > 0 and d.freelist_count == 0
    assert d.wal_bytes is not None and d.wal_bytes >= 0
    assert all(option.partition("=")[0] in ts._COMPILE_OPTIONS for option in d.compile_options)
    assert any(option.startswith("THREADSAFE") for option in d.compile_options)
    assert d.quick_check is None
    assert ts.diagnose(db, run_quick_check=True).quick_check == "ok"


@pytest.mark.skipif(not POSIX, reason="POSIX file modes")
def test_s39_file_mode_of_a_new_and_a_shared_file(tmp_path: Path, opened: Opened) -> None:
    new = opened(ts.connect(tmp_path / "new.db"))
    assert ts.diagnose(new).file_mode == 0o600
    shared = opened(ts.connect(tmp_path / "s.db", profile=SQLiteProfile(file_mode=0o644, allow_shared_file=True)))
    assert ts.diagnose(shared).file_mode == 0o644  # TAB-SQ-003 in TAB-017


def test_s40_diagnose_memory(opened: Opened) -> None:
    d = ts.diagnose(opened(ts.connect(":memory:")))
    assert (d.path, d.file_mode, d.is_symlink, d.wal_bytes) == (None, None, False, None)
    assert d.journal_mode == "memory"
    plain_memory = ts.diagnose(opened(plain(":memory:")))
    assert plain_memory.foreign_keys is False and plain_memory.busy_timeout_ms == 5000


def test_s41_diagnose_reports_a_linked_side_file(tmp_path: Path, db: sqlite3.Connection, symlinks: None) -> None:
    """A link placed next to an open database (TAB-SQ-004); ``diagnose`` reads only."""
    journal = tmp_path / "a.db-journal"
    journal.symlink_to(tmp_path / "elsewhere")
    assert ts.diagnose(db).is_symlink is True
    assert not (tmp_path / "elsewhere").exists()


def test_s41_diagnose_has_no_side_effects(db: sqlite3.Connection) -> None:
    db.execute("PRAGMA query_only = ON")
    pragmas = ("journal_mode", "synchronous", "foreign_keys", "busy_timeout", "query_only", "user_version")
    before = [db.execute(f"PRAGMA {name}").fetchone()[0] for name in pragmas]
    statements: list[str] = []
    db.set_trace_callback(statements.append)
    ts.diagnose(db, run_quick_check=True)
    db.set_trace_callback(None)
    assert [db.execute(f"PRAGMA {name}").fetchone()[0] for name in pragmas] == before
    assert not db.in_transaction
    assert not [s for s in statements if "=" in s and s.upper().startswith("PRAGMA") and "QUERY_ONLY" not in s.upper()]
    db.execute("PRAGMA query_only = OFF")


def test_s41_read_only_connection(tmp_path: Path, opened: Opened) -> None:
    path = tmp_path / "a.db"
    make_database(path)
    d = ts.diagnose(opened(ts.connect(path, read_only=True)), run_quick_check=True)
    assert d.query_only is True and d.quick_check == "ok"


def test_s41_quick_check_message_of_a_damaged_file(tmp_path: Path, opened: Opened) -> None:
    path = tmp_path / "damaged.db"
    _damaged(path)
    d = ts.diagnose(opened(ts.connect(path, read_only=True)), run_quick_check=True)
    assert d.quick_check not in (None, "ok")
    assert CANARY not in str(d.quick_check)
