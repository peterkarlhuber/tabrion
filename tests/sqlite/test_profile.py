"""TAB-013: ``apply_profile``, ``check_path`` and ``loading_extensions`` (TAB-T-S13..S15). Contract section 3.3."""

from __future__ import annotations

from collections.abc import Callable
import logging
from pathlib import Path
import sqlite3
from typing import Any, cast

import pytest

from tabrion import sqlite as ts
from tabrion.errors import UnsafePath, UsageError
from tabrion.sqlite import JournalMode, SQLiteProfile
from tests.sqlite.conftest import make_database, plain

Opened = Callable[[sqlite3.Connection], sqlite3.Connection]


def _pragmas(connection: sqlite3.Connection) -> tuple[Any, ...]:
    names = ("journal_mode", "synchronous", "foreign_keys", "busy_timeout", "secure_delete", "trusted_schema")
    return tuple(connection.execute(f"PRAGMA {name}").fetchone()[0] for name in names)


def test_s13_apply_profile_on_a_foreign_connection(tmp_path: Path, opened: Opened) -> None:
    """Layer B calls it in the ``connect`` event on a connection it did not open."""
    connection = opened(plain(tmp_path / "a.db"))
    assert _pragmas(connection)[0] == "delete"  # the other defaults vary by SQLite build
    ts.apply_profile(connection)
    assert _pragmas(connection) == ("wal", 2, 1, 30_000, 1, 0)


def test_s13_apply_profile_is_idempotent(tmp_path: Path, opened: Opened) -> None:
    connection = opened(ts.connect(tmp_path / "a.db"))
    statements: list[str] = []
    connection.set_trace_callback(statements.append)
    ts.apply_profile(connection)
    ts.apply_profile(connection)
    connection.set_trace_callback(None)
    assert _pragmas(connection) == ("wal", 2, 1, 30_000, 1, 0)
    # The journal mode is read, and written only when it differs.
    assert not [s for s in statements if s.upper().startswith("PRAGMA JOURNAL_MODE =")]


def test_s13_file_backed_detection(tmp_path: Path, opened: Opened) -> None:
    memory = opened(plain(":memory:"))
    ts.apply_profile(memory)
    assert memory.execute("PRAGMA journal_mode").fetchone()[0] == "memory"
    ts.apply_profile(memory, file_backed=False)
    file = opened(plain(tmp_path / "a.db"))
    ts.apply_profile(file, SQLiteProfile(journal_mode=JournalMode.TRUNCATE), file_backed=None)
    assert file.execute("PRAGMA journal_mode").fetchone()[0] == "truncate"
    other = opened(plain(tmp_path / "b.db"))
    ts.apply_profile(other, file_backed=False)
    assert other.execute("PRAGMA journal_mode").fetchone()[0] == "delete"


def test_s13_apply_profile_refuses_an_open_transaction(tmp_path: Path, opened: Opened) -> None:
    connection = opened(plain(tmp_path / "a.db"))
    connection.execute("BEGIN")
    with pytest.raises(UsageError) as info:
        ts.apply_profile(connection)
    assert info.value.code == "sqlite.transaction_open"
    assert connection.in_transaction  # T-NOTX: the caller's transaction is untouched
    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 0


def test_s14_check_path_accepts(tmp_path: Path) -> None:
    for path in (":memory:", "", tmp_path / "missing.db"):
        ts.check_path(path)
    make_database(tmp_path / "a.db")
    ts.check_path(tmp_path / "a.db")
    ts.check_path(str(tmp_path / "a.db"))


@pytest.mark.security
@pytest.mark.parametrize("suffix", ["", "-wal", "-shm", "-journal"])
def test_s14_check_path_refuses_each_link(
    tmp_path: Path, symlinks: None, suffix: str, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / f"a.db{suffix}").symlink_to(tmp_path / "elsewhere")
    with caplog.at_level(logging.DEBUG, logger="tabrion.sqlite"), pytest.raises(UnsafePath) as info:
        ts.check_path(tmp_path / "a.db")
    assert info.value.code == "sqlite.symlink"
    assert str(info.value) == "sqlite.symlink"
    assert info.value.__context__ is None and info.value.__cause__ is None
    assert [r.levelno for r in caplog.records] == [logging.DEBUG]


@pytest.mark.security
def test_s14_check_path_ignores_links_in_parent_directories(tmp_path: Path, symlinks: None) -> None:
    """Only the file and its side files are checked (Orkexa's rule); a linked parent directory is allowed."""
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "linked").symlink_to(real, target_is_directory=True)
    ts.check_path(tmp_path / "linked" / "a.db")


def _load_error(connection: sqlite3.Connection) -> str:
    try:
        connection.execute("SELECT load_extension('tabrion-no-such-extension')")
    except sqlite3.OperationalError as exc:
        return str(exc)
    raise AssertionError("load_extension succeeded")


@pytest.mark.security
def test_s15_loading_extensions_only_inside_the_block(tmp_path: Path, opened: Opened) -> None:
    connection = opened(ts.connect(tmp_path / "a.db"))
    if not hasattr(connection, "enable_load_extension"):
        with pytest.raises(UsageError) as info, ts.loading_extensions(connection):
            pass
        assert info.value.code == "sqlite.extensions_unavailable"
        return
    assert "not authorized" in _load_error(connection)
    with ts.loading_extensions(connection):
        assert "not authorized" not in _load_error(connection)
    assert "not authorized" in _load_error(connection)
    with pytest.raises(RuntimeError), ts.loading_extensions(connection):
        raise RuntimeError("boom")
    assert "not authorized" in _load_error(connection)


def test_s15_extensions_unavailable() -> None:
    """A Python build without ``enable_load_extension`` (some distributions) never enters the block."""

    class WithoutExtensions:
        pass

    entered = False
    with pytest.raises(UsageError) as info, ts.loading_extensions(cast(sqlite3.Connection, WithoutExtensions())):
        entered = True
    assert info.value.code == "sqlite.extensions_unavailable"
    assert not entered


def test_s15_disabled_after_the_block_even_when_disable_was_called_inside() -> None:
    calls: list[bool] = []

    class Recorder:
        def enable_load_extension(self, value: bool) -> None:
            calls.append(value)

    with ts.loading_extensions(cast(sqlite3.Connection, Recorder())):
        assert calls == [True]
    assert calls == [True, False]
