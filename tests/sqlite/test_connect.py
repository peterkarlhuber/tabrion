"""TAB-013: ``SQLiteProfile``, ``create_private_file`` and ``connect`` (TAB-T-S01..S12; 07 TM-03, TM-04, DI-01).

Contract section 3.1 and 3.2, implementation notes I-21..I-23.
"""

from __future__ import annotations

from collections.abc import Callable
import logging
import os
from pathlib import Path
import sqlite3
import stat
from typing import Any

import pytest

from tabrion import sqlite as ts
from tabrion.errors import ConfigurationError, Kind, UnsafePath, classify
from tabrion.sqlite import JournalMode, SQLiteProfile, Synchronous
from tests.sqlite.conftest import POSIX, WINDOWS, digest, files, make_database, mode, plain, posix_only, umask

Opened = Callable[[sqlite3.Connection], sqlite3.Connection]

pytestmark = pytest.mark.security


def _pragmas(connection: sqlite3.Connection) -> dict[str, Any]:
    return {
        name: connection.execute(f"PRAGMA {name}").fetchone()[0]
        for name in ("journal_mode", "synchronous", "foreign_keys", "busy_timeout", "secure_delete", "trusted_schema")
    }


# --- SQLiteProfile ---------------------------------------------------------------------------------------------------


def test_profile_presets() -> None:
    assert SQLiteProfile.orkexa() == SQLiteProfile()
    assert SQLiteProfile.orkexa().secure_delete is True
    nodestro = SQLiteProfile.nodestro()
    assert nodestro.secure_delete is False
    assert (nodestro.journal_mode, nodestro.synchronous, nodestro.busy_timeout, nodestro.foreign_keys) == (
        JournalMode.WAL,
        Synchronous.FULL,
        30.0,
        True,
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("busy_timeout", -0.001),
        ("busy_timeout", float("nan")),
        ("busy_timeout", float("inf")),
        ("busy_timeout", True),
        ("busy_timeout", "30"),
        ("busy_timeout", 3_000_000.0),
        ("file_mode", 0o640),
        ("file_mode", 0o604),
        ("file_mode", 0o400),
        ("file_mode", 0o1600),
        ("file_mode", -1),
        ("file_mode", True),
        ("journal_mode", "wal; DROP TABLE x"),
        ("journal_mode", "memory"),
        ("synchronous", "full; x"),
        ("foreign_keys", 1),
        ("secure_delete", "yes"),
        ("trusted_schema", None),
        ("allow_shared_file", 1),
    ],
)
def test_profile_validation(field: str, value: Any) -> None:
    with pytest.raises(ConfigurationError) as info:
        SQLiteProfile(**{field: value})
    assert info.value.code == "sqlite.invalid_profile"
    assert dict(info.value.details) == {"field": field}


def test_profile_shared_file_needs_the_flag() -> None:
    assert SQLiteProfile(file_mode=0o640, allow_shared_file=True).file_mode == 0o640
    assert SQLiteProfile(busy_timeout=0).busy_timeout == 0


def test_profile_strings_become_enum_members() -> None:
    profile = SQLiteProfile(journal_mode="delete", synchronous="normal")  # type: ignore[arg-type]
    assert profile.journal_mode is JournalMode.DELETE
    assert profile.synchronous is Synchronous.NORMAL


# --- create_private_file ---------------------------------------------------------------------------------------------


@posix_only
@pytest.mark.parametrize("mask", [0o022, 0o000, 0o277])
def test_s01_create_private_file_is_0600_whatever_the_umask(tmp_path: Path, mask: int) -> None:
    """TAB-T-S01 / TAB-T-120: 0600 under umask 022, 000 (simulated) and 277 (would strip the owner's write bit)."""
    path = tmp_path / "a.db"
    with umask(mask):
        ts.create_private_file(path)
    assert mode(path) == 0o600
    assert path.stat().st_size == 0


@posix_only
def test_s02_create_private_file_leaves_an_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "a.db"
    path.write_bytes(b"content")
    path.chmod(0o644)
    ts.create_private_file(path)
    assert mode(path) == 0o644
    assert path.read_bytes() == b"content"


def test_create_private_file_ignores_memory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    ts.create_private_file(":memory:")
    assert files(tmp_path) == []


def test_create_private_file_never_follows_a_link(tmp_path: Path, symlinks: None) -> None:
    victim = tmp_path / "victim"
    victim.write_bytes(b"victim")
    link = tmp_path / "a.db"
    link.symlink_to(victim)
    dangling = tmp_path / "b.db"
    dangling.symlink_to(tmp_path / "nowhere")
    ts.create_private_file(link)
    ts.create_private_file(dangling)
    assert victim.read_bytes() == b"victim"
    assert not (tmp_path / "nowhere").exists()
    assert link.is_symlink() and dangling.is_symlink()


# --- connect: files and modes ----------------------------------------------------------------------------------------


@posix_only
@pytest.mark.parametrize("mask", [0o022, 0o000, 0o277])
def test_s01_new_database_and_side_files_are_0600(tmp_path: Path, opened: Opened, mask: int) -> None:
    """TAB-T-S01 and the B-06-5 test of MIG-NOD-6: the file, ``-wal`` and ``-shm`` are 0600 from the start."""
    path = tmp_path / "a.db"
    with umask(mask):
        connection = opened(ts.connect(path))
        connection.execute("CREATE TABLE t (x)")
        connection.execute("INSERT INTO t VALUES (1)")
    assert files(tmp_path) == ["a.db", "a.db-shm", "a.db-wal"]
    for name in files(tmp_path):
        assert mode(tmp_path / name) == 0o600, name


@posix_only
def test_s01_rollback_journal_is_0600(tmp_path: Path, opened: Opened) -> None:
    path = tmp_path / "a.db"
    with umask(0o000):
        connection = opened(ts.connect(path, profile=SQLiteProfile(journal_mode=JournalMode.DELETE)))
        connection.execute("CREATE TABLE t (x)")
        connection.execute("BEGIN")
        connection.execute("INSERT INTO t VALUES (1)")
        assert mode(tmp_path / "a.db-journal") == 0o600
        connection.execute("COMMIT")


@posix_only
def test_shared_file_mode_is_exact(tmp_path: Path, opened: Opened) -> None:
    with umask(0o077):
        opened(ts.connect(tmp_path / "a.db", profile=SQLiteProfile(file_mode=0o640, allow_shared_file=True)))
    assert mode(tmp_path / "a.db") == 0o640


@posix_only
def test_s02_existing_file_keeps_its_mode(tmp_path: Path, opened: Opened) -> None:
    path = tmp_path / "a.db"
    make_database(path)
    path.chmod(0o644)
    connection = opened(ts.connect(path))
    connection.execute("INSERT INTO parent (id) VALUES (100)")
    assert mode(path) == 0o644


def test_s09_memory(tmp_path: Path, opened: Opened, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    for name in (":memory:", ""):
        connection = opened(ts.connect(name))
        pragmas = _pragmas(connection)
        assert pragmas["journal_mode"] in ("memory", "delete")
        assert (pragmas["foreign_keys"], pragmas["secure_delete"], pragmas["synchronous"]) == (1, 1, 2)
        connection.execute("CREATE TABLE t (x)")
    assert files(tmp_path) == []


def test_s10_pragmas_after_connect(tmp_path: Path, opened: Opened) -> None:
    """TAB-T-S10 and DI-01 (``foreign_keys`` on every connection), TAB-T-024 (WAL for files)."""
    default = opened(ts.connect(tmp_path / "a.db"))
    assert _pragmas(default) == {
        "journal_mode": "wal",
        "synchronous": 2,
        "foreign_keys": 1,
        "busy_timeout": 30_000,
        "secure_delete": 1,
        "trusted_schema": 0,
    }
    assert default.isolation_level is None
    nodestro = opened(ts.connect(tmp_path / "b.db", profile=SQLiteProfile.nodestro()))
    assert _pragmas(nodestro)["secure_delete"] == 0
    custom = SQLiteProfile(
        journal_mode=JournalMode.TRUNCATE,
        synchronous=Synchronous.NORMAL,
        foreign_keys=False,
        busy_timeout=1.5,
        secure_delete=False,
        trusted_schema=True,
    )
    other = opened(ts.connect(tmp_path / "c.db", profile=custom))
    assert _pragmas(other) == {
        "journal_mode": "truncate",
        "synchronous": 1,
        "foreign_keys": 0,
        "busy_timeout": 1_500,
        "secure_delete": 0,
        "trusted_schema": 1,
    }


def test_s10_foreign_keys_are_enforced(tmp_path: Path, opened: Opened) -> None:
    """TAB-T-001 (probe S1): a child row with an unknown parent is a FOREIGN_KEY error."""
    path = tmp_path / "a.db"
    make_database(path)
    connection = opened(ts.connect(path))
    with pytest.raises(sqlite3.IntegrityError) as info:
        connection.execute("INSERT INTO child (id, parent_id) VALUES (1, 999)")
    assert classify(info.value).kind is Kind.FOREIGN_KEY


def test_check_same_thread_is_passed(tmp_path: Path, opened: Opened) -> None:
    import threading

    connection = opened(ts.connect(tmp_path / "a.db", check_same_thread=True))
    errors: list[BaseException] = []

    def use() -> None:
        try:
            connection.execute("SELECT 1")
        except sqlite3.ProgrammingError as exc:
            errors.append(exc)

    thread = threading.Thread(target=use)
    thread.start()
    thread.join()
    assert len(errors) == 1


# --- connect: read only and missing files ----------------------------------------------------------------------------


def test_s08_read_only_never_writes(tmp_path: Path, opened: Opened) -> None:
    """TAB-T-S08: ``mode=ro`` and ``query_only``; INSERT is READ_ONLY; the database file is unchanged."""
    path = tmp_path / "a.db"
    make_database(path)
    before = digest(path)
    connection = opened(ts.connect(path, read_only=True))
    assert connection.execute("PRAGMA query_only").fetchone()[0] == 1
    assert connection.execute("SELECT count(*) FROM parent").fetchone()[0] == 3
    with pytest.raises(sqlite3.OperationalError) as info:
        connection.execute("INSERT INTO parent (id) VALUES (10)")
    assert classify(info.value).kind is Kind.READ_ONLY
    # query_only off still cannot write: the file is opened read-only (mode=ro).
    connection.execute("PRAGMA query_only = OFF")
    with pytest.raises(sqlite3.OperationalError) as info:
        connection.execute("INSERT INTO parent (id) VALUES (10)")
    assert classify(info.value).kind is Kind.READ_ONLY
    connection.close()
    assert digest(path) == before
    if POSIX:
        # SQLite may create -wal/-shm for a WAL database; they get the main file's mode.
        for name in files(tmp_path):
            assert mode(tmp_path / name) == mode(path), name


def test_s08_read_only_keeps_the_journal_mode(tmp_path: Path, opened: Opened) -> None:
    path = tmp_path / "a.db"
    connection = plain(path)
    connection.execute("CREATE TABLE t (x)")
    connection.close()
    before = digest(path)
    reader = opened(ts.connect(path, read_only=True))
    assert reader.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    reader.close()
    assert digest(path) == before
    assert files(tmp_path) == ["a.db"]


@pytest.mark.parametrize("kwargs", [{"read_only": True}, {"create": False}, {"read_only": True, "create": False}])
def test_missing_file_is_not_created(tmp_path: Path, kwargs: dict[str, Any]) -> None:
    path = tmp_path / "a.db"
    with pytest.raises(FileNotFoundError) as info:
        ts.connect(path, **kwargs)
    assert str(path) not in str(info.value) and "a.db" not in str(info.value)
    assert files(tmp_path) == []


def test_read_only_path_with_uri_characters(tmp_path: Path, opened: Opened) -> None:
    # Windows allows no "?" in file names; "#", "%" and spaces must be quoted in the URI there too.
    directory = tmp_path / ("x#y %20" if WINDOWS else "x?mode=rwc#y %20")
    directory.mkdir()
    path = directory / ("a b#.db" if WINDOWS else "a b?.db")
    make_database(path)
    connection = opened(ts.connect(path, read_only=True))
    assert connection.execute("SELECT count(*) FROM parent").fetchone()[0] == 3
    assert path.name in files(directory)


def test_s11_broken_file_is_corrupt(tmp_path: Path) -> None:
    path = tmp_path / "a.db"
    path.write_bytes(b"not a database " * 300)
    for kwargs in ({}, {"read_only": True}):
        with pytest.raises(sqlite3.DatabaseError) as info:
            ts.connect(path, **kwargs)  # type: ignore[arg-type]
        assert classify(info.value).kind is Kind.CORRUPT
    assert path.read_bytes() == b"not a database " * 300


def test_s11_connection_is_closed_on_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[sqlite3.Connection] = []
    original = ts._open

    def spy(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        connection = original(*args, **kwargs)
        seen.append(connection)
        return connection

    monkeypatch.setattr(ts, "_open", spy)
    path = tmp_path / "a.db"
    path.write_bytes(b"x" * 4096)
    with pytest.raises(sqlite3.DatabaseError):
        ts.connect(path)
    with pytest.raises(sqlite3.ProgrammingError):
        seen[0].execute("SELECT 1")


@pytest.mark.skipif(WINDOWS, reason="a directory cannot be opened as a file on Windows either way")
def test_not_a_regular_file_is_refused(tmp_path: Path) -> None:
    directory = tmp_path / "dir.db"
    directory.mkdir()
    with pytest.raises(UnsafePath) as info:
        ts.connect(directory)
    assert info.value.code == "sqlite.unsafe_path"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no FIFOs on this platform")
def test_fifo_is_refused_without_blocking(tmp_path: Path) -> None:
    fifo = tmp_path / "a.db"
    os.mkfifo(fifo)
    with pytest.raises(UnsafePath) as info:
        ts.connect(fifo)
    assert info.value.code == "sqlite.unsafe_path"


# --- connect: symbolic links (TAB-T-S03..S07, TM-03, TM-04) ----------------------------------------------------------


@pytest.mark.parametrize("suffix", ["", "-wal", "-shm", "-journal"])
@pytest.mark.parametrize("kwargs", [{}, {"read_only": True}])
def test_s03_s06_symlinks_are_refused(
    tmp_path: Path, symlinks: None, suffix: str, kwargs: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    """TAB-T-S03..S06 / TAB-T-121: a link for the file or a side file is refused; the target stays unchanged."""
    path = tmp_path / "a.db"
    victim = tmp_path / "victim.db"
    make_database(victim)
    if suffix:
        make_database(path)
    before = digest(victim)
    (tmp_path / f"a.db{suffix}").symlink_to(victim)
    with caplog.at_level(logging.DEBUG, logger="tabrion.sqlite"), pytest.raises(UnsafePath) as info:
        ts.connect(path, **kwargs)
    assert info.value.code == "sqlite.symlink"
    assert str(tmp_path) not in str(info.value) and str(tmp_path) not in repr(info.value)
    assert "a.db" not in str(info.value)
    assert digest(victim) == before
    assert any(str(tmp_path) in record.getMessage() for record in caplog.records if record.levelno == logging.DEBUG)


def test_dangling_symlink_is_refused(tmp_path: Path, symlinks: None) -> None:
    path = tmp_path / "a.db"
    path.symlink_to(tmp_path / "nowhere.db")
    with pytest.raises(UnsafePath):
        ts.connect(path)
    assert not (tmp_path / "nowhere.db").exists()


def _victim(tmp_path: Path) -> tuple[Path, str, int]:
    victim = tmp_path / "victim.db"
    make_database(victim)
    if POSIX:
        victim.chmod(0o644)
    return victim, digest(victim), mode(victim)


def test_s07_link_planted_before_creation(tmp_path: Path, symlinks: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """TAB-T-S07: a link planted between the checks and ``open`` meets ``O_EXCL``/``O_NOFOLLOW`` and is refused."""
    path = tmp_path / "a.db"
    victim, before, victim_mode = _victim(tmp_path)
    real_create = ts._create

    def plant_then_create(candidate: str, file_mode: int) -> tuple[int, int] | None:
        path.symlink_to(victim)
        return real_create(candidate, file_mode)

    monkeypatch.setattr(ts, "_create", plant_then_create)
    with pytest.raises(UnsafePath) as info:
        ts.connect(path)
    assert info.value.code == "sqlite.symlink"
    assert digest(victim) == before
    assert mode(victim) == victim_mode


def test_s07_link_planted_after_the_check(tmp_path: Path, symlinks: None, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "a.db"
    victim, before, _ = _victim(tmp_path)
    real_check = ts.check_path

    def check_then_plant(candidate: Any) -> None:
        real_check(candidate)
        if not os.path.lexists(path):
            path.symlink_to(victim)

    monkeypatch.setattr(ts, "check_path", check_then_plant)
    with pytest.raises(UnsafePath):
        ts.connect(path)
    assert digest(victim) == before


def test_s07_existing_file_swapped_for_a_link(tmp_path: Path, symlinks: None, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "a.db"
    make_database(path)
    victim, before, _ = _victim(tmp_path)
    real_check = ts.check_path

    def check_then_swap(candidate: Any) -> None:
        real_check(candidate)
        if not path.is_symlink():
            path.unlink()
            path.symlink_to(victim)

    monkeypatch.setattr(ts, "check_path", check_then_swap)
    with pytest.raises(UnsafePath):
        ts.connect(path)
    assert digest(victim) == before


def test_s07_link_followed_while_opening_and_swapped_back(
    tmp_path: Path, symlinks: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The attacker swaps in a link only while SQLite opens the file and swaps the real file back afterwards."""
    path = tmp_path / "a.db"
    make_database(path)
    victim, _, _ = _victim(tmp_path)
    to_delete_mode = plain(victim)
    to_delete_mode.execute("PRAGMA journal_mode = DELETE")
    to_delete_mode.close()
    before = digest(victim)  # nothing may be written to it, not even the WAL flag of the profile
    (tmp_path / "link.db").symlink_to(victim)
    resolver = plain(tmp_path / "link.db")
    resolved = ts._main_file(resolver)
    resolver.close()
    stash = tmp_path / "stash.db"
    original = ts._open
    swapped: list[sqlite3.Connection] = []

    def open_through_link(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        path.rename(stash)
        path.symlink_to(victim)
        try:
            connection = original(*args, **kwargs)
            connection.execute("PRAGMA schema_version").fetchone()
        finally:
            path.unlink()
            stash.rename(path)
        swapped.append(connection)
        return connection

    monkeypatch.setattr(ts, "_open", open_through_link)
    link_name = os.path.normcase(os.path.abspath(tmp_path / "link.db"))
    if os.path.normcase(resolved) == link_name or os.path.realpath(resolved) != os.path.realpath(victim):
        # Windows (and old SQLite builds): the opened name is the link's own; the inode check remains (I-22).
        pytest.skip("this SQLite build does not resolve links in the name it opens")
    with pytest.raises(UnsafePath) as info:
        leaked = ts.connect(path)
        leaked.close()
    assert info.value.code == "sqlite.unsafe_path"
    assert digest(victim) == before
    with pytest.raises(sqlite3.ProgrammingError):
        swapped[0].execute("SELECT 1")  # closed


def test_s07_file_replaced_between_check_and_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Another regular file put in place after the check (a different inode) is refused too."""
    path = tmp_path / "a.db"
    make_database(path)
    other = tmp_path / "other.db"
    make_database(other)
    original = ts._open

    def replace_then_open(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        os.replace(other, path)
        return original(*args, **kwargs)

    monkeypatch.setattr(ts, "_open", replace_then_open)
    if WINDOWS and os.lstat(path).st_ino == 0:
        pytest.skip("no file index on this file system")
    with pytest.raises(UnsafePath) as info:
        ts.connect(path)
    assert info.value.code == "sqlite.unsafe_path"


def test_s12_o_nofollow_by_platform() -> None:
    """TAB-T-S12: Windows has no ``O_NOFOLLOW``; there the ``lstat`` check (reparse points included) is the guard."""
    if WINDOWS:
        assert ts._O_NOFOLLOW == 0
    else:
        assert ts._O_NOFOLLOW == os.O_NOFOLLOW != 0


def test_s12_windows_reparse_points_count_as_links(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The reparse-point attribute (a junction on Windows) is treated as a link on every platform."""
    path = tmp_path / "a.db"
    path.write_bytes(b"")
    real = os.lstat

    class Info:
        def __init__(self, info: os.stat_result) -> None:
            self.st_mode = info.st_mode
            self.st_file_attributes = ts._REPARSE_POINT

    monkeypatch.setattr(os, "lstat", lambda p: Info(real(p)) if os.fspath(p) == str(path) else real(p))
    with pytest.raises(UnsafePath):
        ts.check_path(path)
    assert stat.S_ISREG(real(path).st_mode)


def test_s12_without_o_nofollow_a_dangling_link_is_not_followed(
    tmp_path: Path, symlinks: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows' ``CREATE_NEW`` follows a dangling link; without ``O_NOFOLLOW`` the ``lstat`` check comes first."""
    monkeypatch.setattr(ts, "_O_NOFOLLOW", 0)
    link = tmp_path / "a.db"
    link.symlink_to(tmp_path / "nowhere")
    assert ts._create(str(link), 0o600) is None
    assert not (tmp_path / "nowhere").exists()
