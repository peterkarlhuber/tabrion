"""TAB-XW-7: ``restore`` (contract section 3.10, notes I-170, I-172; ADR 0015 section 6, planning document 15 9.4).

The round trip of the acceptance criterion (backup, restore into a new file, ``integrity_check`` ``ok``, schema
fingerprint equal), WAL mode, a corrupt source, the ``0600`` mode, a held lock and the refusals. Runs on macOS and
Windows too (platforms workflow); POSIX modes and symbolic links skip where they do not exist.
"""

from __future__ import annotations

from collections.abc import Callable
import os
from pathlib import Path
import sqlite3
from typing import Any

import pytest

from tabrion import sqlite as ts
from tabrion.errors import DatabaseInUse, TabrionError, UnsafePath, UsageError
from tests.sqlite.conftest import CANARY, digest, files, make_database, mode, plain, posix_only, umask

pytestmark = pytest.mark.usefixtures("hang_limit")  # TB-049: a hang fails the run within 120 s

Opened = Callable[[sqlite3.Connection], sqlite3.Connection]


def _rich_database(path: Path, rows: int = 50) -> None:
    """A WAL file with a foreign key, an index, a view and a trigger, closed afterwards."""
    make_database(path, rows=rows)
    connection = plain(path)
    try:
        connection.execute("CREATE INDEX child_parent ON child (parent_id)")
        connection.execute("CREATE VIEW named AS SELECT id, secret FROM parent")
        connection.execute("CREATE TABLE log (id INTEGER PRIMARY KEY, parent_id INTEGER)")
        connection.execute(
            "CREATE TRIGGER parent_log AFTER INSERT ON parent BEGIN INSERT INTO log (parent_id) VALUES (new.id); END"
        )
        connection.execute("INSERT INTO child (id, parent_id) VALUES (1, 1)")
    finally:
        connection.close()


def _rows(path: Path, table: str = "parent") -> list[tuple[Any, ...]]:
    connection = ts.connect(path, read_only=True)
    try:
        return connection.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()  # noqa: S608 - a fixed name
    finally:
        connection.close()


def _schema(path: Path) -> list[tuple[Any, ...]]:
    connection = ts.connect(path, read_only=True)
    try:
        return connection.execute("SELECT type, name, sql FROM sqlite_schema ORDER BY name").fetchall()
    finally:
        connection.close()


def _damaged(path: Path) -> None:
    """A file with garbage in its table pages; the header and the schema page stay intact, so it opens."""
    connection = plain(path)
    try:
        connection.execute("CREATE TABLE t (x TEXT)")
        connection.executemany("INSERT INTO t VALUES (?)", [(f"{CANARY}-{n}" * 20,) for n in range(400)])
    finally:
        connection.close()
    data = bytearray(path.read_bytes())
    for page in range(2, len(data) // 4096, 2):
        start = page * 4096 + 16
        data[start : start + 200] = b"\xa5" * 200
    path.write_bytes(bytes(data))


def test_round_trip_backup_restore_integrity_and_fingerprint(tmp_path: Path, opened: Opened) -> None:
    """Planning document 15 section 9.4 (TAB-XW-7), ADR 0015 Verification: backup, restore into a new file,
    ``integrity_check`` ``ok`` and the schema fingerprint equal."""
    sqlalchemy = pytest.importorskip("sqlalchemy")
    from tabrion import schema

    original = tmp_path / "live.db"
    _rich_database(original)
    live = opened(ts.connect(original))
    ts.backup(live, tmp_path / "backup.db")
    live.execute("INSERT INTO parent (id, secret) VALUES (1000, 'after the backup')")

    result = ts.restore(tmp_path / "backup.db", tmp_path / "restored.db")
    assert result == ts.RestoreResult(path=tmp_path / "restored.db", pages=result.pages, integrity_check="ok")
    assert result.pages > 0
    restored = opened(ts.connect(tmp_path / "restored.db"))
    assert ts.integrity_check(restored).ok
    assert ts.foreign_key_check(restored) == []
    assert _rows(tmp_path / "restored.db") == _rows(tmp_path / "backup.db")
    assert _schema(tmp_path / "restored.db") == _schema(original)

    def fingerprint(path: Path, level: Any) -> str:
        engine = sqlalchemy.create_engine(f"sqlite:///{path}")
        try:
            return schema.fingerprint(schema.snapshot(sqlalchemy.inspect(engine), level=level))
        finally:
            engine.dispose()

    for level in (1, 2):
        assert fingerprint(tmp_path / "restored.db", level) == fingerprint(original, level)


def test_restore_reads_uncheckpointed_wal_content(tmp_path: Path, opened: Opened) -> None:
    """A source in WAL mode whose last commits are only in its ``-wal`` file: they are restored too."""
    source = tmp_path / "source.db"
    make_database(source, rows=3)
    writer = opened(ts.connect(source))
    writer.execute("PRAGMA wal_autocheckpoint = 0")
    writer.execute("INSERT INTO parent (id, secret) VALUES (99, 'in the wal')")
    assert (tmp_path / "source.db-wal").stat().st_size > 0
    ts.restore(source, tmp_path / "target.db")
    assert (99, "in the wal") in _rows(tmp_path / "target.db")


def test_restore_into_a_live_wal_database_is_atomic_for_its_connections(tmp_path: Path, opened: Opened) -> None:
    """The copy runs in one step through SQLite's lock: another open connection sees the restored database."""
    _rich_database(tmp_path / "live.db", rows=5)
    live = opened(ts.connect(tmp_path / "live.db"))
    ts.backup(live, tmp_path / "backup.db")
    live.execute("DELETE FROM child")
    live.execute("DELETE FROM parent")
    other = opened(ts.connect(tmp_path / "live.db"))
    assert other.execute("SELECT count(*) FROM parent").fetchone() == (0,)
    result = ts.restore(tmp_path / "backup.db", tmp_path / "live.db", overwrite=True)
    assert result.integrity_check == "ok"
    assert other.execute("SELECT count(*) FROM parent").fetchone() == (5,)
    assert live.execute("SELECT count(*) FROM parent").fetchone() == (5,)
    assert live.execute("PRAGMA journal_mode").fetchone() == ("wal",)


def test_restore_changes_the_page_size_of_a_wal_target(tmp_path: Path, opened: Opened) -> None:
    """SQLite cannot change a WAL database's page size: the copy runs in rollback mode and the target is WAL again."""
    source = tmp_path / "small-pages.db"
    connection = plain(source)
    connection.execute("PRAGMA page_size = 1024")
    connection.execute("CREATE TABLE t (x)")
    connection.execute("INSERT INTO t VALUES (1)")
    connection.close()
    make_database(tmp_path / "target.db")
    ts.restore(source, tmp_path / "target.db", overwrite=True)
    target = opened(ts.connect(tmp_path / "target.db"))
    assert target.execute("PRAGMA page_size").fetchone() == (1024,)
    assert target.execute("PRAGMA journal_mode").fetchone() == ("wal",)
    assert target.execute("SELECT x FROM t").fetchall() == [(1,)]


def test_page_size_change_with_another_connection_is_refused(tmp_path: Path, opened: Opened) -> None:
    source = tmp_path / "small-pages.db"
    connection = plain(source)
    connection.execute("PRAGMA page_size = 1024")
    connection.execute("CREATE TABLE t (x)")
    connection.close()
    make_database(tmp_path / "target.db")
    other = opened(ts.connect(tmp_path / "target.db"))
    before = other.execute("SELECT count(*) FROM parent").fetchone()
    with pytest.raises(DatabaseInUse) as caught:
        ts.restore(source, tmp_path / "target.db", overwrite=True)
    assert caught.value.code == "sqlite.in_use"
    assert other.execute("SELECT count(*) FROM parent").fetchone() == before


def test_switch_back_to_wal_that_is_busy_keeps_the_restore(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A connection that opened the target between the copy and the switch back keeps it in rollback mode; the
    restore is committed and reported, and ``connect`` switches the file back to WAL."""
    source = tmp_path / "small-pages.db"
    connection = plain(source)
    connection.execute("PRAGMA page_size = 1024")
    connection.execute("CREATE TABLE t (x)")
    connection.close()
    real = ts._rows
    switches: list[str] = []

    def busy_switch(conn: sqlite3.Connection, statement: str, limit: int | None = None) -> list[tuple[Any, ...]]:
        if statement == "PRAGMA journal_mode = WAL":
            switches.append(statement)
            if len(switches) == 2:  # the first is connect's on the new, empty target; the second the switch back
                error = sqlite3.OperationalError("database is locked")
                error.sqlite_errorcode = sqlite3.SQLITE_BUSY
                raise error
        return real(conn, statement, limit)

    monkeypatch.setattr(ts, "_rows", busy_switch)
    assert ts.restore(source, tmp_path / "target.db").integrity_check == "ok"
    monkeypatch.undo()
    raw = plain(tmp_path / "target.db")
    assert raw.execute("PRAGMA journal_mode").fetchone() == ("delete",)
    raw.close()
    target = ts.connect(tmp_path / "target.db")
    assert target.execute("PRAGMA journal_mode").fetchone() == ("wal",)
    target.close()


def test_other_error_of_the_switch_back_propagates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "small-pages.db"
    connection = plain(source)
    connection.execute("PRAGMA page_size = 1024")
    connection.execute("CREATE TABLE t (x)")
    connection.close()
    real = ts._rows
    switches: list[str] = []

    def failing(conn: sqlite3.Connection, statement: str, limit: int | None = None) -> list[tuple[Any, ...]]:
        if statement == "PRAGMA journal_mode = WAL":
            switches.append(statement)
            if len(switches) == 2:
                raise sqlite3.OperationalError("disk I/O error")
        return real(conn, statement, limit)

    monkeypatch.setattr(ts, "_rows", failing)
    with pytest.raises(sqlite3.OperationalError):
        ts.restore(source, tmp_path / "target.db")
    assert not any(name.startswith("target.db") for name in files(tmp_path))  # a new target is removed again


def test_held_lock_on_the_target_is_refused(tmp_path: Path, opened: Opened, monkeypatch: pytest.MonkeyPatch) -> None:
    """A reader of a rollback-mode target holds its lock: after the busy timeout, ``DatabaseInUse``, nothing written."""
    monkeypatch.setattr(ts, "_RESTORE_PROFILE", ts.SQLiteProfile(busy_timeout=0.2))
    make_database(tmp_path / "source.db")
    target = tmp_path / "target.db"
    make_database(target, rows=7)
    switch = plain(target)
    switch.execute("PRAGMA journal_mode = DELETE").fetchall()
    switch.close()
    reader = opened(plain(target))
    reader.execute("BEGIN")
    reader.execute("SELECT count(*) FROM parent").fetchall()
    before = digest(target)
    with pytest.raises(DatabaseInUse) as caught:
        ts.restore(tmp_path / "source.db", target, overwrite=True)
    assert caught.value.code == "sqlite.in_use"
    assert caught.value.__cause__ is None
    reader.execute("ROLLBACK")
    assert digest(target) == before


def test_writer_on_a_wal_target_is_refused(tmp_path: Path, opened: Opened, monkeypatch: pytest.MonkeyPatch) -> None:
    """A WAL target with another connection's write transaction: the copy cannot take the write lock and stops after
    the busy timeout instead of retrying forever (``DatabaseInUse``); the writer's transaction is untouched."""
    monkeypatch.setattr(ts, "_RESTORE_PROFILE", ts.SQLiteProfile(busy_timeout=0.2))
    make_database(tmp_path / "source.db")
    make_database(tmp_path / "target.db", rows=7)
    writer = opened(ts.connect(tmp_path / "target.db"))
    writer.execute("BEGIN IMMEDIATE")
    writer.execute("INSERT INTO parent (id, secret) VALUES (70, 'pending')")
    with pytest.raises(DatabaseInUse) as caught:
        ts.restore(tmp_path / "source.db", tmp_path / "target.db", overwrite=True)
    assert caught.value.code == "sqlite.in_use"
    writer.execute("COMMIT")
    assert len(_rows(tmp_path / "target.db")) == 8


def test_existing_target_needs_overwrite(tmp_path: Path) -> None:
    make_database(tmp_path / "source.db")
    make_database(tmp_path / "target.db", rows=9)
    before = digest(tmp_path / "target.db")
    with pytest.raises(FileExistsError) as caught:
        ts.restore(tmp_path / "source.db", tmp_path / "target.db")
    assert str(tmp_path) not in str(caught.value)
    assert digest(tmp_path / "target.db") == before


def test_empty_existing_target_is_restored(tmp_path: Path) -> None:
    make_database(tmp_path / "source.db", rows=4)
    ts.create_private_file(tmp_path / "target.db")
    ts.restore(tmp_path / "source.db", tmp_path / "target.db", overwrite=True)
    assert len(_rows(tmp_path / "target.db")) == 4


def test_corrupt_source_is_refused_before_anything_is_written(tmp_path: Path) -> None:
    source = tmp_path / "damaged.db"
    _damaged(source)
    make_database(tmp_path / "target.db")
    before = digest(tmp_path / "target.db")
    for target, overwrite in ((tmp_path / "target.db", True), (tmp_path / "new.db", False)):
        with pytest.raises(TabrionError) as caught:
            ts.restore(source, target, overwrite=overwrite)
        assert caught.value.code == "sqlite.restore_source_corrupt"
        assert CANARY not in str(caught.value) and CANARY not in repr(caught.value)
    assert digest(tmp_path / "target.db") == before
    assert not (tmp_path / "new.db").exists()


@pytest.mark.parametrize("content", [b"", b"plain text, not a database file at all"])
def test_source_that_is_no_database_is_refused(tmp_path: Path, content: bytes) -> None:
    (tmp_path / "source.db").write_bytes(content)
    with pytest.raises(UsageError) as caught:
        ts.restore(tmp_path / "source.db", tmp_path / "target.db")
    assert caught.value.code == "sqlite.not_a_database"
    assert files(tmp_path) == ["source.db"]


def test_target_that_is_no_database_is_never_overwritten(tmp_path: Path) -> None:
    make_database(tmp_path / "source.db")
    (tmp_path / "notes.txt").write_text("my notes")
    with pytest.raises(UsageError) as caught:
        ts.restore(tmp_path / "source.db", tmp_path / "notes.txt", overwrite=True)
    assert caught.value.code == "sqlite.not_a_database"
    assert (tmp_path / "notes.txt").read_text() == "my notes"


def test_same_file_is_refused(tmp_path: Path) -> None:
    make_database(tmp_path / "a.db")
    for target in (tmp_path / "a.db", tmp_path / "." / "a.db"):
        with pytest.raises(UsageError) as caught:
            ts.restore(tmp_path / "a.db", target, overwrite=True)
        assert caught.value.code == "sqlite.same_file"
    try:
        os.link(tmp_path / "a.db", tmp_path / "hard.db")
    except OSError:
        return  # no hard links on this file system
    if not (tmp_path / "a.db").stat().st_ino:
        return  # no file index on this file system
    with pytest.raises(UsageError) as caught:
        ts.restore(tmp_path / "a.db", tmp_path / "hard.db", overwrite=True)
    assert caught.value.code == "sqlite.same_file"


def test_missing_source(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError) as caught:
        ts.restore(tmp_path / "missing.db", tmp_path / "target.db")
    assert str(tmp_path) not in str(caught.value)
    assert files(tmp_path) == []


@pytest.mark.parametrize(
    ("source", "target", "code"), [(":memory:", "t.db", "invalid_source"), ("s.db", "", "invalid_target")]
)
def test_memory_names_are_refused(tmp_path: Path, source: str, target: str, code: str) -> None:
    with pytest.raises(UsageError) as caught:
        ts.restore(source, target)
    assert caught.value.code == f"sqlite.{code}"


def test_verify_false_reports_not_checked(tmp_path: Path) -> None:
    make_database(tmp_path / "source.db")
    assert ts.restore(tmp_path / "source.db", tmp_path / "t.db", verify=False).integrity_check == "not checked"


def test_target_failing_the_check_is_reported_and_a_new_one_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_database(tmp_path / "source.db")
    real = ts.integrity_check
    calls: list[int] = []

    def second_fails(connection: sqlite3.Connection, *, max_errors: int = 100) -> ts.IntegrityReport:
        calls.append(1)
        if len(calls) == 2:
            return ts.IntegrityReport(ok=False, messages=("page 2 is never used",), truncated=False)
        return real(connection, max_errors=max_errors)

    monkeypatch.setattr(ts, "integrity_check", second_fails)
    with pytest.raises(TabrionError) as caught:
        ts.restore(tmp_path / "source.db", tmp_path / "new.db")
    assert caught.value.code == "sqlite.restore_corrupt"
    assert set(caught.value.details) == {"pages"}
    assert not any(name.startswith("new.db") for name in files(tmp_path))


def test_created_target_swapped_before_the_copy_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The target restore created must be the file SQLite opened; a file swapped in is refused and left alone."""
    make_database(tmp_path / "source.db")
    real = ts._create

    def create_then_swap(path: str, file_mode: int) -> tuple[int, int] | None:
        created = real(path, file_mode)
        if path.endswith("new.db"):
            os.rename(path, path + ".moved")  # kept, so its inode cannot be reused for the swapped-in file
            make_database(Path(path), rows=1)
        return created

    monkeypatch.setattr(ts, "_create", create_then_swap)
    with pytest.raises(UnsafePath):
        ts.restore(tmp_path / "source.db", tmp_path / "new.db")
    assert len(_rows(tmp_path / "new.db")) == 1  # the swapped-in file is not the restore's to remove


@posix_only
@pytest.mark.security
@pytest.mark.parametrize("mask", [0o022, 0o000])
def test_new_target_is_0600(tmp_path: Path, mask: int) -> None:
    make_database(tmp_path / "source.db")
    with umask(mask):
        ts.restore(tmp_path / "source.db", tmp_path / "target.db")
    assert mode(tmp_path / "target.db") == 0o600


@posix_only
@pytest.mark.security
def test_group_writable_target_is_refused(tmp_path: Path) -> None:
    make_database(tmp_path / "source.db")
    make_database(tmp_path / "target.db")
    (tmp_path / "target.db").chmod(0o664)
    with pytest.raises(UnsafePath) as caught:
        ts.restore(tmp_path / "source.db", tmp_path / "target.db", overwrite=True)
    assert caught.value.code == "sqlite.unsafe_mode"


@pytest.mark.security
@pytest.mark.usefixtures("symlinks")
@pytest.mark.parametrize("which", ["source", "target", "target-wal"])
def test_symbolic_links_are_refused(tmp_path: Path, which: str) -> None:
    make_database(tmp_path / "real.db")
    make_database(tmp_path / "victim.db", rows=2)
    before = digest(tmp_path / "victim.db")
    if which == "source":
        (tmp_path / "link.db").symlink_to(tmp_path / "real.db")
        source, target = tmp_path / "link.db", tmp_path / "victim.db"
    elif which == "target":
        (tmp_path / "link.db").symlink_to(tmp_path / "victim.db")
        source, target = tmp_path / "real.db", tmp_path / "link.db"
    else:
        (tmp_path / "victim.db-wal").symlink_to(tmp_path / "elsewhere")
        source, target = tmp_path / "real.db", tmp_path / "victim.db"
    with pytest.raises(UnsafePath) as caught:
        ts.restore(source, target, overwrite=True)
    assert caught.value.code == "sqlite.symlink"
    assert str(tmp_path) not in str(caught.value)
    assert digest(tmp_path / "victim.db") == before
    assert not (tmp_path / "elsewhere").exists()
