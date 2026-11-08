"""TAB-014: ``backup`` (TAB-T-S31..S34; 07 TM-12, TAB-T-122, TAB-T-200/201). Contract 3.6, notes I-27, I-28."""

from __future__ import annotations

from collections.abc import Callable
import os
from pathlib import Path
import sqlite3
import threading
from typing import Any

import pytest

from tabrion import sqlite as ts
from tabrion.errors import TabrionError, UnsafePath, UsageError
from tests.sqlite.conftest import POSIX, digest, files, make_database, mode, posix_only, umask

Opened = Callable[[sqlite3.Connection], sqlite3.Connection]


@pytest.fixture
def source(tmp_path: Path, opened: Opened) -> sqlite3.Connection:
    path = tmp_path / "a.db"
    make_database(path, rows=200)
    return opened(ts.connect(path))


def _count(path: Path) -> int:
    connection = ts.connect(path, read_only=True)
    try:
        return int(connection.execute("SELECT count(*) FROM parent").fetchone()[0])
    finally:
        connection.close()


def test_backup_result_and_content(tmp_path: Path, source: sqlite3.Connection) -> None:
    target = tmp_path / "copy.db"
    result = ts.backup(source, target)
    assert result.path == target
    assert result.quick_check == "ok"
    assert result.bytes == target.stat().st_size > 0
    assert result.pages * 4096 == result.bytes
    assert _count(target) == 200
    assert sorted(files(tmp_path)) == ["a.db", "a.db-shm", "a.db-wal", "copy.db"]


def test_backup_is_one_self_contained_file(tmp_path: Path, source: sqlite3.Connection, opened: Opened) -> None:
    """Contract I-28: the copy is in rollback mode; ``connect`` switches it back to WAL when it is used."""
    target = tmp_path / "copy.db"
    ts.backup(source, target)
    raw = opened(sqlite3.connect(target))
    assert raw.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    raw.close()
    assert opened(ts.connect(target)).execute("PRAGMA journal_mode").fetchone()[0] == "wal"


@posix_only
@pytest.mark.security
@pytest.mark.parametrize("mask", [0o022, 0o000])
def test_s31_backup_is_0600(tmp_path: Path, source: sqlite3.Connection, mask: int) -> None:
    """TAB-T-S31 / TAB-T-122 and B-06-3 (Nodestro's snapshot was created with the umask's mode)."""
    target = tmp_path / "copy.db"
    with umask(mask):
        ts.backup(source, target)
        target.chmod(0o644)
        ts.backup(source, target, overwrite=True)
    assert mode(target) == 0o600


@posix_only
@pytest.mark.security
def test_s31_temporary_file_is_0600_while_copying(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[int] = []

    class Spy(sqlite3.Connection):
        def backup(self, target: sqlite3.Connection, **kwargs: Any) -> None:
            name = target.execute("PRAGMA database_list").fetchone()[2]
            seen.append(mode(Path(name)))
            super().backup(target, **kwargs)

    path = tmp_path / "a.db"
    make_database(path)
    connection = sqlite3.connect(path, isolation_level=None, factory=Spy)
    try:
        with umask(0o000):
            ts.backup(connection, tmp_path / "copy.db")
    finally:
        connection.close()
    assert seen == [0o600]


@pytest.mark.parametrize("overwrite", [False, True])
def test_s32_abort_leaves_no_half_target(tmp_path: Path, overwrite: bool) -> None:
    """TAB-T-S32 / TAB-T-201: an error in the middle of the copy leaves neither a target nor a temporary file."""

    class Broken(sqlite3.Connection):
        def backup(self, target: sqlite3.Connection, **kwargs: Any) -> None:
            def progress(status: int, remaining: int, total: int) -> None:
                raise KeyboardInterrupt  # after the first step

            super().backup(target, pages=1, progress=progress)

    path = tmp_path / "a.db"
    make_database(path, rows=500)
    target = tmp_path / "copy.db"
    if overwrite:
        target.write_bytes(b"previous backup")
    before = sorted(files(tmp_path))
    connection = sqlite3.connect(path, isolation_level=None, factory=Broken)
    try:
        with pytest.raises(KeyboardInterrupt):
            ts.backup(connection, target, overwrite=overwrite)
    finally:
        connection.close()
    assert sorted(files(tmp_path)) == before
    if overwrite:
        assert target.read_bytes() == b"previous backup"


def test_s33_failed_check_removes_the_copy(
    tmp_path: Path, source: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TAB-T-S33: a copy that fails ``quick_check`` is deleted and raises ``sqlite.backup_corrupt``."""
    monkeypatch.setattr(
        ts, "quick_check", lambda connection, max_errors=100: ts.IntegrityReport(False, ("page 2: bad",), False)
    )
    before = sorted(files(tmp_path))
    with pytest.raises(TabrionError) as info:
        ts.backup(source, tmp_path / "copy.db")
    assert info.value.code == "sqlite.backup_corrupt"
    assert "bad" not in str(info.value)
    assert sorted(files(tmp_path)) == before


def test_s33_verify_runs_on_a_separate_read_only_connection(
    tmp_path: Path, source: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    checked: list[tuple[str, bool]] = []
    real = ts.quick_check

    def spy(connection: sqlite3.Connection, max_errors: int = 100) -> ts.IntegrityReport:
        name = connection.execute("PRAGMA database_list").fetchone()[2]
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("CREATE TABLE x (y)")
        checked.append((Path(name).name, connection is source))
        return real(connection, max_errors=max_errors)

    monkeypatch.setattr(ts, "quick_check", spy)
    ts.backup(source, tmp_path / "copy.db")
    assert len(checked) == 1
    assert checked[0][0].startswith("copy.db.") and checked[0][0].endswith(".tmp")
    assert checked[0][1] is False


def test_s33_without_verify(tmp_path: Path, source: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: Any, **kwargs: Any) -> ts.IntegrityReport:
        raise AssertionError("quick_check must not run")

    monkeypatch.setattr(ts, "quick_check", fail)
    result = ts.backup(source, tmp_path / "copy.db", verify=False)
    assert result.quick_check == "not checked"


def test_s34_backup_while_another_connection_writes(tmp_path: Path, source: sqlite3.Connection) -> None:
    """TAB-T-S34 / TAB-T-200: other connections keep writing; the copy is consistent and ``integrity_check`` ok."""
    stop = threading.Event()
    written: list[int] = []
    errors: list[BaseException] = []

    def writer() -> None:
        connection = ts.connect(tmp_path / "a.db")
        try:
            i = 10_000
            while not stop.is_set() and i < 10_400:
                with ts.write_transaction(connection):
                    connection.execute("INSERT INTO parent (id, secret) VALUES (?, 'x')", (i,))
                written.append(i)
                i += 1
        except BaseException as exc:
            errors.append(exc)
        finally:
            connection.close()

    thread = threading.Thread(target=writer)
    thread.start()
    try:
        results = [ts.backup(source, tmp_path / f"copy{n}.db", pages_per_step=step) for n, step in enumerate([-1, 5])]
    finally:
        stop.set()
        thread.join()
    assert errors == []
    for n, result in enumerate(results):
        assert result.quick_check == "ok"
        copy = ts.connect(tmp_path / f"copy{n}.db", read_only=True)
        try:
            assert ts.integrity_check(copy).ok
            rows = copy.execute("SELECT count(*) FROM parent").fetchone()[0]
        finally:
            copy.close()
        assert 200 <= rows <= 200 + len(written)


def test_memory_source(tmp_path: Path, opened: Opened) -> None:
    memory = opened(ts.connect(":memory:"))
    memory.execute("CREATE TABLE t (x)")
    memory.execute("INSERT INTO t VALUES (1)")
    ts.backup(memory, tmp_path / "copy.db")
    copy = opened(ts.connect(tmp_path / "copy.db", read_only=True))
    assert copy.execute("SELECT x FROM t").fetchall() == [(1,)]


def test_existing_target(tmp_path: Path, source: sqlite3.Connection) -> None:
    target = tmp_path / "copy.db"
    target.write_bytes(b"keep")
    with pytest.raises(FileExistsError) as info:
        ts.backup(source, target)
    assert str(tmp_path) not in str(info.value)
    assert target.read_bytes() == b"keep"
    ts.backup(source, target, overwrite=True)
    assert _count(target) == 200


def test_target_appearing_during_the_copy_is_not_replaced(
    tmp_path: Path, source: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without ``overwrite`` the final step never replaces a file that appeared meanwhile (``os.link``)."""
    target = tmp_path / "copy.db"
    real = ts._verify

    def appear(path: str) -> str:
        target.write_bytes(b"someone else")
        return real(path)

    monkeypatch.setattr(ts, "_verify", appear)
    before = sorted(files(tmp_path))
    with pytest.raises(FileExistsError):
        ts.backup(source, target)
    assert target.read_bytes() == b"someone else"
    assert sorted(files(tmp_path)) == sorted([*before, "copy.db"])


def test_without_hard_links(tmp_path: Path, source: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_links(*args: Any, **kwargs: Any) -> None:
        raise PermissionError("no hard links on this file system")

    monkeypatch.setattr(os, "link", no_links)
    ts.backup(source, tmp_path / "copy.db")
    assert _count(tmp_path / "copy.db") == 200
    assert "copy.db" in files(tmp_path) and not [n for n in files(tmp_path) if n.endswith(".tmp")]


@pytest.mark.security
@pytest.mark.parametrize("suffix", ["", "-journal", "-wal"])
def test_link_target_is_refused(tmp_path: Path, source: sqlite3.Connection, symlinks: None, suffix: str) -> None:
    victim = tmp_path / "victim"
    victim.write_bytes(b"victim")
    target = tmp_path / "copy.db"
    (tmp_path / f"copy.db{suffix}").symlink_to(victim)
    with pytest.raises(UnsafePath):
        ts.backup(source, target, overwrite=True)
    assert victim.read_bytes() == b"victim"


def test_usage_errors(tmp_path: Path, source: sqlite3.Connection) -> None:
    source.execute("BEGIN IMMEDIATE")
    with pytest.raises(UsageError) as info:
        ts.backup(source, tmp_path / "copy.db")
    assert info.value.code == "sqlite.transaction_open"
    assert source.in_transaction  # T-NOTX
    source.execute("ROLLBACK")
    with pytest.raises(UsageError) as info:
        ts.backup(source, ":memory:")
    assert info.value.code == "sqlite.invalid_target"
    with pytest.raises(UsageError) as info:
        ts.backup(source, tmp_path / "copy.db", pages_per_step=True)
    assert info.value.code == "sqlite.invalid_pages"
    assert "copy.db" not in files(tmp_path)


def test_backup_does_not_change_the_source(tmp_path: Path) -> None:
    path = tmp_path / "a.db"
    make_database(path)
    before = digest(path)
    connection = ts.connect(path, read_only=True)
    try:
        ts.backup(connection, tmp_path / "copy.db")
    finally:
        connection.close()
    assert digest(path) == before
    if POSIX:
        assert mode(tmp_path / "copy.db") == 0o600
