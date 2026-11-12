"""TAB-013: ``SQLiteProfile``, ``create_private_file`` and ``connect`` (TAB-T-S01..S12; 07 TM-03, TM-04, DI-01).

Contract section 3.1 and 3.2, implementation notes I-21..I-23.
"""

from __future__ import annotations

from collections.abc import Callable
import errno
import logging
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import threading
from typing import Any
import warnings

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


@posix_only
def test_m1_foreign_owner_is_refused(tmp_path: Path, opened: Opened, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review M-1 (TM-03): an existing file owned by another user is never opened, unless ``allow_shared_file``."""
    path = tmp_path / "a.db"
    make_database(path)
    before = digest(path)
    me = os.geteuid()
    monkeypatch.setattr(os, "geteuid", lambda: me + 1)  # the file now belongs to "another user"
    for read_only in (False, True):
        with pytest.raises(UnsafePath) as info:
            ts.connect(path, read_only=read_only)
        assert info.value.code == "sqlite.unsafe_owner"
        assert str(tmp_path) not in str(info.value) and str(tmp_path) not in repr(info.value)
    assert digest(path) == before
    shared = SQLiteProfile(allow_shared_file=True)
    opened(ts.connect(path, profile=shared)).execute("SELECT count(*) FROM parent").fetchone()


@posix_only
@pytest.mark.parametrize("bits", [0o620, 0o602, 0o664, 0o666])
def test_m1_group_or_world_writable_file_is_refused(tmp_path: Path, opened: Opened, bits: int) -> None:
    """Review M-1: a group- or world-writable file is refused; ``allow_shared_file`` accepts it as it is."""
    path = tmp_path / "a.db"
    make_database(path)
    path.chmod(bits)
    before = digest(path)
    with pytest.raises(UnsafePath) as info:
        ts.connect(path)
    assert info.value.code == "sqlite.unsafe_mode"
    assert digest(path) == before and mode(path) == bits
    opened(ts.connect(path, profile=SQLiteProfile(allow_shared_file=True)))
    assert mode(path) == bits


@posix_only
@pytest.mark.parametrize("bits", [0o640, 0o644, 0o604])
def test_m1_readable_but_not_writable_file_is_accepted(tmp_path: Path, opened: Opened, bits: int) -> None:
    """Existing Nodestro files are 0644 (B-06-5): readable by others is accepted and reported by ``diagnose``."""
    path = tmp_path / "a.db"
    make_database(path)
    path.chmod(bits)
    assert ts.diagnose(opened(ts.connect(path))).file_mode == bits


@posix_only
@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_m1_side_file_owner_and_mode(
    tmp_path: Path, opened: Opened, monkeypatch: pytest.MonkeyPatch, suffix: str
) -> None:
    """An existing side file follows the same rules: a writable or foreign one is refused before SQLite opens it."""
    path = tmp_path / "a.db"
    make_database(path)
    side = tmp_path / f"a.db{suffix}"
    side.write_bytes(b"")
    side.chmod(0o666)
    with pytest.raises(UnsafePath) as info:
        ts.connect(path)
    assert info.value.code == "sqlite.unsafe_mode"
    side.chmod(0o600)
    real = os.lstat

    def foreign(candidate: Any, *args: Any, **kwargs: Any) -> os.stat_result:
        info = real(candidate, *args, **kwargs)
        if os.fspath(candidate).endswith(suffix):
            values = list(info)
            values[stat.ST_UID] = os.geteuid() + 1
            return os.stat_result(values)
        return info

    monkeypatch.setattr(os, "lstat", foreign)
    with pytest.raises(UnsafePath) as info:
        ts.connect(path)
    assert info.value.code == "sqlite.unsafe_owner"
    monkeypatch.undo()
    side.unlink()
    opened(ts.connect(path, profile=SQLiteProfile(allow_shared_file=True)))


@posix_only
def test_m1_new_file_is_not_checked_twice(tmp_path: Path, opened: Opened) -> None:
    """A file Tabrion creates with a shared ``file_mode`` is fine (``allow_shared_file`` is required for it)."""
    profile = SQLiteProfile(file_mode=0o664, allow_shared_file=True)
    with umask(0):
        opened(ts.connect(tmp_path / "a.db", profile=profile))
    assert mode(tmp_path / "a.db") == 0o664


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


@pytest.mark.parametrize("descriptors", [True, False], ids=["descriptors", "names-only"])
def test_s07_link_followed_while_opening_and_swapped_back(
    tmp_path: Path, symlinks: None, monkeypatch: pytest.MonkeyPatch, descriptors: bool
) -> None:
    """The attacker swaps in a link only while SQLite opens the file and swaps the real file back afterwards.
    ``names-only``: without the descriptor check (Windows, TB-045), the name SQLite reports still catches it."""
    if not descriptors:
        monkeypatch.setattr(ts, "_open_files", lambda: None)
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


@pytest.mark.parametrize("descriptors", [True, False], ids=["descriptors", "names-only"])
def test_s07_file_replaced_between_check_and_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, descriptors: bool
) -> None:
    """Another regular file put in place after the check (a different inode) is refused too. ``names-only``: the
    inode check of the name still catches it without the descriptor check (Windows, TB-045)."""
    if not descriptors:
        monkeypatch.setattr(ts, "_open_files", lambda: None)
    path = tmp_path / "a.db"
    make_database(path)
    other = tmp_path / "other.db"
    make_database(other)
    original = ts._open

    def replace_then_open(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        if other.exists():  # once: connect retries an open it could not verify
            os.replace(other, path)
        return original(*args, **kwargs)

    monkeypatch.setattr(ts, "_open", replace_then_open)
    if WINDOWS and os.lstat(path).st_ino == 0:
        pytest.skip("no file index on this file system")
    with pytest.raises(UnsafePath) as info:
        ts.connect(path)
    assert info.value.code == "sqlite.unsafe_path"


# --- connect: the descriptor SQLite opened is the checked file (contract I-22, TB-045, TM-04) ------------------------


def _open_other(other: Path, path: Path, monkeypatch: pytest.MonkeyPatch, times: int) -> list[sqlite3.Connection]:
    """A seam for the window inside SQLite's own open: the first ``times`` opens get ``other`` while SQLite still
    reports the checked name (as when the name changes between SQLite's name resolution and its ``open``)."""
    original_open, original_main_file = ts._open, ts._main_file
    redirected: list[sqlite3.Connection] = []

    def open_other(target: str, **kwargs: Any) -> sqlite3.Connection:
        if len(redirected) < times:
            redirected.append(original_open(str(other), **kwargs))
            return redirected[-1]
        return original_open(target, **kwargs)

    def main_file(connection: sqlite3.Connection) -> str:
        return os.path.abspath(path) if connection in redirected else original_main_file(connection)

    monkeypatch.setattr(ts, "_open", open_other)
    monkeypatch.setattr(ts, "_main_file", main_file)
    return redirected


@posix_only
@pytest.mark.parametrize("held", [False, True], ids=["alone", "file-held-elsewhere"])
@pytest.mark.parametrize("kwargs", [{}, {"read_only": True}])
def test_tb045_another_file_opened_by_sqlite_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kwargs: dict[str, Any], held: bool
) -> None:
    """TAB-T-123 / TB-045, deterministic: every name check passes (no link, the same inode under the name, the
    reported name is the real path), but SQLite holds another file. The descriptor check refuses it, after its
    retries, and nothing is written to the other file. ``file-held-elsewhere``: another descriptor of the process
    is the checked file, which must not count (only a reused descriptor, with nothing new opened, may)."""
    path = tmp_path / "a.db"
    make_database(path)
    holder = plain(path) if held else None
    other = tmp_path / "other.db"
    make_database(other)
    to_delete_mode = plain(other)
    to_delete_mode.execute("PRAGMA journal_mode = DELETE").fetchone()
    to_delete_mode.close()
    before = digest(other)  # nothing may be written to it, not even the WAL flag of the profile
    redirected = _open_other(other, path, monkeypatch, times=ts._OPEN_ATTEMPTS)
    with pytest.raises(UnsafePath) as info:
        ts.connect(path, **kwargs).close()
    assert info.value.code == "sqlite.unsafe_path"
    assert len(redirected) == ts._OPEN_ATTEMPTS
    for connection in redirected:
        with pytest.raises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")  # closed
    assert digest(other) == before
    if holder is not None:
        holder.close()


@posix_only
def test_tb045_names_alone_cannot_tell(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Why the descriptor check exists: without it (as on Windows) the same open is accepted (documented, I-22)."""
    path = tmp_path / "a.db"
    make_database(path)
    other = tmp_path / "other.db"
    make_database(other)
    _open_other(other, path, monkeypatch, times=1)
    monkeypatch.setattr(ts, "_open_files", lambda: None)
    connection = ts.connect(path)
    try:
        assert connection.execute("SELECT count(*) FROM parent").fetchone() is not None
        assert os.path.samefile(connection.execute("PRAGMA database_list").fetchone()[2], other)
    finally:
        connection.close()


def test_tb045_the_last_check_does_not_follow_the_name(
    tmp_path: Path, symlinks: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Names only (Windows, or no descriptor listing): the comparison with the name SQLite opened resolves the
    directory, not the name itself, so a link put under the name after its checks cannot make both sides agree."""
    path = tmp_path / "a.db"
    make_database(path)
    other = tmp_path / "other.db"
    make_database(other)
    _open_other(other, path, monkeypatch, times=ts._OPEN_ATTEMPTS)
    monkeypatch.setattr(ts, "_main_file", lambda connection: str(other))  # SQLite reports what it opened
    monkeypatch.setattr(ts, "_open_files", lambda: None)
    real_realpath = os.path.realpath
    stash = tmp_path / "stash.db"

    def realpath(candidate: Any, *args: Any, **kwargs: Any) -> str:
        if os.fspath(candidate) == str(path) and not stash.exists():
            path.rename(stash)
            path.symlink_to(other)
        return str(real_realpath(candidate, *args, **kwargs))

    monkeypatch.setattr(os.path, "realpath", realpath)
    with pytest.raises(UnsafePath) as info:
        ts.connect(path).close()
    assert info.value.code == "sqlite.unsafe_path"


def test_tb045_descriptor_listing_by_platform(tmp_path: Path) -> None:
    """The descriptor check exists on POSIX only (I-22 (e)). On Linux and macOS the process's open regular
    files are listed with their identity; on Windows nothing is listed and ``connect`` relies on the name checks of
    I-22 (d), which still open a normal database."""
    path = tmp_path / "a.db"
    make_database(path)
    with open(path, "rb") as handle:
        listed = ts._open_files()
        if POSIX:
            assert listed is not None
            info = os.fstat(handle.fileno())
            assert (handle.fileno(), info.st_dev, info.st_ino) in listed
        else:
            assert listed is None
    connection = ts.connect(path)
    try:
        assert connection.execute("SELECT count(*) FROM parent").fetchone() == (3,)
    finally:
        connection.close()


@posix_only
def test_tb045_no_descriptor_listing_falls_back_to_the_names(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A POSIX system without ``/proc/self/fd`` and ``/dev/fd`` behaves like Windows: one open, the name checks."""
    path = tmp_path / "a.db"
    make_database(path)
    monkeypatch.setattr(ts, "_DESCRIPTOR_DIRECTORIES", (str(tmp_path / "missing"),))
    assert ts._open_files() is None
    opens: list[str] = []
    original = ts._open

    def counting_open(target: str, **kwargs: Any) -> sqlite3.Connection:
        opens.append(target)
        return original(target, **kwargs)

    monkeypatch.setattr(ts, "_open", counting_open)
    connection = ts.connect(path)
    try:
        assert connection.execute("SELECT count(*) FROM parent").fetchone() == (3,)
    finally:
        connection.close()
    assert len(opens) == 1


@posix_only
def test_tb045_a_single_mismatch_is_retried(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "a.db"
    make_database(path)
    other = tmp_path / "other.db"
    make_database(other)
    redirected = _open_other(other, path, monkeypatch, times=1)
    connection = ts.connect(path)
    try:
        assert os.path.samefile(connection.execute("PRAGMA database_list").fetchone()[2], path)
    finally:
        connection.close()
    with pytest.raises(sqlite3.ProgrammingError):
        redirected[0].execute("SELECT 1")


@posix_only
def test_tb045_a_descriptor_sqlite_reuses_is_accepted(tmp_path: Path) -> None:
    """SQLite keeps the descriptor of a closed connection while another connection of the process holds a lock on
    the file (WAL keeps a shared lock), and the next open takes it: no new descriptor, still the checked file."""
    path = tmp_path / "a.db"
    make_database(path)
    holder = ts.connect(path)
    holder.execute("SELECT count(*) FROM parent").fetchone()
    try:
        closed = ts.connect(path)
        closed.execute("SELECT count(*) FROM parent").fetchone()
        closed.close()
        before = ts._open_files()
        reused = ts.connect(path)
        after = ts._open_files()
        assert before is not None and after is not None
        identity = (os.stat(path).st_dev, os.stat(path).st_ino)
        assert not {entry for entry in after - before if entry[1:] == identity}  # the reuse case really ran
        assert reused.execute("SELECT count(*) FROM parent").fetchone() == (3,)
        reused.close()
    finally:
        holder.close()


def test_tb045_side_files_opened_meanwhile_do_not_hide_a_reused_descriptor() -> None:
    """While SQLite reuses a descriptor of the checked file, other connections of the database may open its ``-shm``
    (outside the lock; seen on macOS CI): that does not count as new. Any other new file still does."""
    checked, shm, other = (1, 10), (1, 11), (1, 12)
    before = frozenset({(3, *checked)})
    with_shm = before | {(4, *shm)}
    with_other = before | {(4, *other)}
    assert ts._opened_checked_file(before, with_shm, checked, frozenset({shm}))
    assert not ts._opened_checked_file(before, with_shm, checked, frozenset())
    assert not ts._opened_checked_file(before, with_other, checked, frozenset({shm}))
    assert not ts._opened_checked_file(frozenset(), frozenset({(4, *shm)}), checked, frozenset({shm}))  # not held
    assert ts._opened_checked_file(frozenset(), frozenset({(5, *checked)}), checked)


@posix_only
def test_tb045_side_file_identities(tmp_path: Path) -> None:
    path = tmp_path / "a.db"
    make_database(path)
    holder = ts.connect(path)
    holder.execute("SELECT count(*) FROM parent").fetchone()  # opens -wal and -shm
    try:
        shm = os.stat(f"{path}-shm")
        assert os.path.exists(f"{path}-wal")  # present, but never exempt (review F10)
        assert ts._side_file_identities(str(path)) == {(shm.st_dev, shm.st_ino)}
        assert ts._side_file_identities(str(path), allow_shared_file=True) == frozenset()
        assert ts._side_file_identities(str(tmp_path / "missing.db")) == frozenset()
    finally:
        holder.close()


@posix_only
def test_tb045_side_file_identities_need_one_link_and_the_owners_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review F2: only what SQLite creates next to a checked database counts as a side file. A file linked under the
    side file's name (it has a second name), one that others may write, or one of another user does not."""
    path = tmp_path / "a.db"
    make_database(path)
    shm = Path(f"{path}-shm")
    shm.write_bytes(b"x")
    shm.chmod(0o600)
    identity = {(shm.stat().st_dev, shm.stat().st_ino)}
    assert ts._side_file_identities(str(path)) == identity
    with monkeypatch.context() as patched:
        patched.setattr(os, "geteuid", lambda: shm.stat().st_uid + 1)
        assert ts._side_file_identities(str(path)) == frozenset()
    shm.chmod(0o620)
    assert ts._side_file_identities(str(path)) == frozenset()
    shm.chmod(0o600)
    os.link(shm, tmp_path / "second-name")
    assert ts._side_file_identities(str(path)) == frozenset()


def _reuse_the_holder(
    holder: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, side_effect: Callable[[], None]
) -> None:
    """A seam for SQLite reusing a descriptor of the checked file: ``_open`` opens no new main file (it returns the
    holder) while ``side_effect`` opens something else in the window."""

    def reuse(target: str, **kwargs: Any) -> sqlite3.Connection:
        side_effect()
        return holder

    monkeypatch.setattr(ts, "_open", reuse)


@posix_only
def test_tb045_a_side_file_that_appeared_during_the_open_is_not_exempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review F2: a side file counts only when it was the same before and after the open; one put under the name of
    the ``-shm`` meanwhile is an unexplained new file."""
    path = tmp_path / "a.db"
    make_database(path)
    holder = plain(path)
    holder.execute("SELECT count(*) FROM parent").fetchone()
    descriptors: list[int] = []

    def new_shm() -> None:
        fresh = tmp_path / f"fresh{len(descriptors)}"
        descriptors.append(os.open(fresh, os.O_CREAT | os.O_RDWR, 0o600))
        os.replace(fresh, f"{path}-shm")

    _reuse_the_holder(holder, monkeypatch, new_shm)
    try:
        with pytest.raises(UnsafePath) as info:
            ts.connect(path)
        assert info.value.code == "sqlite.unsafe_path"
        assert len(descriptors) == ts._OPEN_ATTEMPTS
    finally:
        for descriptor in descriptors:
            os.close(descriptor)
        holder.close()


@posix_only
@pytest.mark.parametrize("suffix", ["-wal", "-journal"])
def test_tb045_a_new_descriptor_on_the_wal_or_journal_is_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, suffix: str
) -> None:
    """Review F10: while SQLite reuses a descriptor of the checked file, a new descriptor on the database's own
    ``-wal`` or ``-journal`` is not explained (SQLite could have opened that file under the database's name): the
    open is retried and, when it keeps happening, refused."""
    path = tmp_path / "a.db"
    make_database(path)
    holder = plain(path)
    holder.execute("SELECT count(*) FROM parent").fetchone()
    side = Path(f"{path}{suffix}")
    if not side.exists():
        side.write_bytes(b"")
    side.chmod(0o600)
    descriptors: list[int] = []
    # O_CREAT: closing the refused holder after the first attempt may delete the -wal
    _reuse_the_holder(holder, monkeypatch, lambda: descriptors.append(os.open(side, os.O_RDONLY | os.O_CREAT, 0o600)))
    try:
        with pytest.raises(UnsafePath) as info:
            ts.connect(path)
        assert info.value.code == "sqlite.unsafe_path"
        assert len(descriptors) == ts._OPEN_ATTEMPTS
    finally:
        for descriptor in descriptors:
            os.close(descriptor)
        holder.close()


@posix_only
def test_tb045_a_pipe_opened_meanwhile_does_not_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review F6: only regular files are compared; a pipe or socket opened by another thread while SQLite reuses a
    descriptor of the checked file does not refuse the open."""
    path = tmp_path / "a.db"
    make_database(path)
    holder = plain(path)
    holder.execute("SELECT count(*) FROM parent").fetchone()
    pipes: list[int] = []
    _reuse_the_holder(holder, monkeypatch, lambda: pipes.extend(os.pipe()))
    try:
        connection = ts.connect(path)
        assert connection is holder
        assert len(pipes) == 2  # accepted at the first attempt
    finally:
        for descriptor in pipes:
            os.close(descriptor)
        holder.close()


@posix_only
def test_tb045_connects_do_not_interleave_between_the_snapshots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review F6: while one connect is between its snapshots, another connect cannot open (``_OPEN_LOCK``)."""
    first, second = tmp_path / "a.db", tmp_path / "b.db"
    make_database(first)
    make_database(second)
    original = ts._open
    first_inside, release, second_inside = threading.Event(), threading.Event(), threading.Event()

    def gated(target: str, **kwargs: Any) -> sqlite3.Connection:
        if target == str(first):
            first_inside.set()
            assert release.wait(30)
        else:
            second_inside.set()
        return original(target, **kwargs)

    monkeypatch.setattr(ts, "_open", gated)
    results: list[sqlite3.Connection | BaseException] = []

    def run(path: Path) -> None:
        try:
            results.append(ts.connect(path))
        except BaseException as exc:  # reported below
            results.append(exc)

    threads = [threading.Thread(target=run, args=(first,))]
    threads[0].start()
    try:
        assert first_inside.wait(30)
        threads.append(threading.Thread(target=run, args=(second,)))
        threads[1].start()
        assert not second_inside.wait(0.5)
    finally:
        release.set()
        for thread in threads:
            thread.join()
    assert second_inside.is_set()
    for result in results:
        assert isinstance(result, sqlite3.Connection), result
        result.close()


@posix_only
def test_tb045_the_identity_check_runs_under_the_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review F5/F13: the check of (b) and (c) holds ``_OPEN_LOCK``, so its own descriptor cannot fall between
    another connect's snapshots: while one connect checks its file, another cannot check or open."""
    first, second = tmp_path / "a.db", tmp_path / "b.db"
    make_database(first)
    make_database(second)
    original = ts._identity
    first_inside, release, second_inside = threading.Event(), threading.Event(), threading.Event()

    def gated(candidate: str, **kwargs: Any) -> tuple[int, int]:
        if candidate == str(first):
            first_inside.set()
            assert release.wait(30)
        else:
            second_inside.set()
        return original(candidate, **kwargs)

    monkeypatch.setattr(ts, "_identity", gated)
    results: list[sqlite3.Connection | BaseException] = []

    def run(path: Path) -> None:
        try:
            results.append(ts.connect(path))
        except BaseException as exc:  # reported below
            results.append(exc)

    threads = [threading.Thread(target=run, args=(first,))]
    threads[0].start()
    try:
        assert first_inside.wait(30)
        threads.append(threading.Thread(target=run, args=(second,)))
        threads[1].start()
        assert not second_inside.wait(0.5)
    finally:
        release.set()
        for thread in threads:
            thread.join()
    assert second_inside.is_set()
    for result in results:
        assert isinstance(result, sqlite3.Connection), result
        result.close()


def _fixed_listing(monkeypatch: pytest.MonkeyPatch, directory: Path, numbers: range) -> None:
    """``directory`` lists ``numbers`` whatever is open (a listing of fixed entries)."""
    real_listdir = os.listdir

    def listdir(candidate: Any = ".") -> list[str]:
        if os.fspath(candidate) == str(directory):
            return [str(number) for number in numbers]
        return real_listdir(candidate)

    monkeypatch.setattr(os, "listdir", listdir)
    monkeypatch.setattr(ts, "_DESCRIPTOR_DIRECTORIES", (str(directory),))
    monkeypatch.setattr(ts, "_LISTINGS", {})


@posix_only
def test_tb045_a_listing_of_fixed_entries_is_never_used(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review F12/F15/F17: a directory that lists every number (also those a daemon may have freed, 0..2) whatever
    is open keeps listing the probe's copy after it is closed. That is undecided, so the probe refuses: the open is
    never made without the check, and nothing is cached (the next connect probes again)."""
    _fixed_listing(monkeypatch, tmp_path / "fd", range(1_000_000))
    with pytest.raises(UnsafePath) as info:
        ts._listing_directory()
    assert info.value.code == "sqlite.unsafe_path"
    assert ts._LISTINGS == {}
    path = tmp_path / "a.db"
    make_database(path)
    with pytest.raises(UnsafePath):
        ts.connect(path)


@posix_only
def test_tb045_a_listing_without_the_open_copy_is_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review F12/F17: the copy is numbered above every listed number, so fixed entries cannot list it while open."""
    _fixed_listing(monkeypatch, tmp_path / "fd", range(64))
    assert ts._listing_directory() is None


@posix_only
def test_tb045_descriptors_opened_during_the_probe_do_not_hide_the_listing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review F17: other threads open descriptors while the probe runs (sockets, logs); they get the lowest free
    numbers, never the copy's, which lies above every listed number. Simulated by opening descriptors in each
    listing; the listing is still found, not cached as absent."""
    monkeypatch.setattr(ts, "_LISTINGS", {})
    real_listdir = os.listdir
    opened: list[int] = []

    def busy_listdir(candidate: Any = ".") -> list[str]:
        if os.fspath(candidate) in ts._DESCRIPTOR_DIRECTORIES:
            for _ in range(4):
                opened.append(os.open(os.devnull, os.O_RDONLY))
        return real_listdir(candidate)

    monkeypatch.setattr(os, "listdir", busy_listdir)
    try:
        assert ts._listing_directory() in ts._DESCRIPTOR_DIRECTORIES
    finally:
        for descriptor in opened:
            os.close(descriptor)
    assert opened


@posix_only
def test_tb045_a_listing_without_links_is_proved_by_open_and_close(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review F15/F18: macOS ``/dev/fd`` entries are no links and their ``stat`` does not report the open file; the
    proof that the listing follows the descriptors does not depend on it (simulated: no links, and ``stat`` of an
    entry reports another file)."""
    monkeypatch.setattr(ts, "_LISTINGS", {})
    monkeypatch.setattr(os.path, "islink", lambda entry: False)
    real_stat = os.stat
    other = real_stat(__file__)

    def stat_elsewhere(candidate: Any, *args: Any, **kwargs: Any) -> os.stat_result:
        if any(os.fspath(candidate).startswith(directory + os.sep) for directory in ts._DESCRIPTOR_DIRECTORIES):
            return other
        return real_stat(candidate, *args, **kwargs)

    monkeypatch.setattr(os, "stat", stat_elsewhere)
    assert ts._listing_directory() in ts._DESCRIPTOR_DIRECTORIES


def _links_listing() -> str:
    directory = ts._DESCRIPTOR_DIRECTORIES[0]
    if not os.path.islink(os.path.join(directory, "0")):
        pytest.skip("the descriptor entries are no links here (macOS)")
    return directory


@posix_only
def test_tb045_a_link_entry_must_lead_to_the_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Where the entries are links (Linux), the copy's entry must lead to the probe's file (defence in depth)."""
    directory = _links_listing()
    monkeypatch.setattr(ts, "_DESCRIPTOR_DIRECTORIES", (directory,))
    monkeypatch.setattr(ts, "_LISTINGS", {})
    real_stat = os.stat
    other = real_stat(__file__)

    def stat_elsewhere(candidate: Any, *args: Any, **kwargs: Any) -> os.stat_result:
        if os.fspath(candidate).startswith(directory + os.sep):
            return other
        return real_stat(candidate, *args, **kwargs)

    monkeypatch.setattr(os, "stat", stat_elsewhere)
    assert ts._listing_directory() is None


@posix_only
def test_tb045_exhaustion_while_following_a_link_is_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review F16: running out of memory while the copy's link entry is followed is raised, not cached."""
    directory = _links_listing()
    monkeypatch.setattr(ts, "_DESCRIPTOR_DIRECTORIES", (directory,))
    monkeypatch.setattr(ts, "_LISTINGS", {})
    real_stat = os.stat

    def stat_exhausted(candidate: Any, *args: Any, **kwargs: Any) -> os.stat_result:
        if os.fspath(candidate).startswith(directory + os.sep):
            raise OSError(errno.ENOMEM, os.strerror(errno.ENOMEM))
        return real_stat(candidate, *args, **kwargs)

    monkeypatch.setattr(os, "stat", stat_exhausted)
    with pytest.raises(OSError) as info:
        ts._listing_directory()
    assert info.value.errno == errno.ENOMEM
    assert ts._LISTINGS == {}


@posix_only
def test_tb045_the_probe_tries_again_when_the_number_is_taken_again(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review F15/F17: a round that cannot decide (the closed copy still listed: its number taken again) is
    repeated; one such round does not make the process give up the listing. Only the platform's real listing is a
    candidate here (``/proc/self/fd`` on Linux, ``/dev/fd`` on macOS), so the rounds counted are its own."""
    monkeypatch.setattr(ts, "_LISTINGS", {})
    listing = ts._listing_directory()
    assert listing is not None
    monkeypatch.setattr(ts, "_DESCRIPTOR_DIRECTORIES", (listing,))
    monkeypatch.setattr(ts, "_LISTINGS", {})
    original = ts._probe_once
    rounds: list[bool | None] = []

    def taken_once(directory: str, probe: int) -> bool | None:
        rounds.append(None if not rounds else original(directory, probe))
        return rounds[-1]

    monkeypatch.setattr(ts, "_probe_once", taken_once)
    assert ts._listing_directory() == listing
    assert rounds == [None, True]


@posix_only
def test_tb045_an_incomplete_listing_is_not_used(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review F4: a ``/dev/fd`` that lists only 0, 1 and 2 (a BSD without ``fdescfs``) would refuse every open; it
    is detected once and ``connect`` checks the names only, as without a listing."""
    fake = tmp_path / "fd"
    fake.mkdir()
    for number in ("0", "1", "2"):
        (fake / number).touch()
    monkeypatch.setattr(ts, "_DESCRIPTOR_DIRECTORIES", (str(fake),))
    monkeypatch.setattr(ts, "_LISTINGS", {})
    assert ts._listing_directory() is None
    assert ts._open_files() is None
    path = tmp_path / "a.db"
    make_database(path)
    ts.connect(path).close()
    monkeypatch.setattr(ts, "_DESCRIPTOR_DIRECTORIES", (str(fake), "/proc/self/fd", "/dev/fd"))
    assert ts._listing_directory() in ("/proc/self/fd", "/dev/fd")  # the next candidate is complete


def test_tb045_a_name_gone_while_checked_is_checked_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review F1: a name that is missing for a moment while its identity is taken is never opened unchecked: the
    file put there instead goes through every check (here its mode is refused)."""
    path = tmp_path / "a.db"
    make_database(path)
    replacement = tmp_path / "replacement.db"
    make_database(replacement)
    if POSIX:
        replacement.chmod(0o666)
    stash = tmp_path / "stash.db"
    original = ts._identity

    def vanish_once(candidate: str, **kwargs: Any) -> tuple[int, int]:
        if not stash.exists():
            path.rename(stash)
            try:
                return original(candidate, **kwargs)
            finally:
                os.replace(replacement, path)
        return original(candidate, **kwargs)

    monkeypatch.setattr(ts, "_identity", vanish_once)
    if POSIX:
        with pytest.raises(UnsafePath) as info:
            ts.connect(path)
        assert info.value.code == "sqlite.unsafe_mode"
    else:
        connection = ts.connect(path)
        connection.close()


def test_tb045_a_name_that_stays_gone_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "a.db"
    make_database(path)
    calls: list[str] = []

    def gone(candidate: str, **kwargs: Any) -> tuple[int, int]:
        calls.append(candidate)
        raise FileNotFoundError(2, "gone")

    monkeypatch.setattr(ts, "_identity", gone)
    with pytest.raises(UnsafePath) as info:
        ts.connect(path)
    assert info.value.code == "sqlite.unsafe_path"
    assert len(calls) == ts._OPEN_ATTEMPTS


@posix_only
@pytest.mark.parametrize("number", [errno.EACCES, errno.EPERM], ids=["EACCES", "EPERM"])
def test_tb045_an_unreadable_file_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, number: int) -> None:
    """Review F1: an existing file whose identity cannot be taken is refused, not opened unchecked."""
    path = tmp_path / "a.db"
    make_database(path)
    real_open = os.open

    def denied(candidate: Any, *args: Any, **kwargs: Any) -> int:
        if os.fspath(candidate) == str(path):
            raise PermissionError(number, os.strerror(number))
        return real_open(candidate, *args, **kwargs)

    monkeypatch.setattr(os, "open", denied)
    monkeypatch.setattr(ts, "_open", _never_opened)
    with pytest.raises(UnsafePath) as info:
        ts.connect(path)
    assert info.value.code == "sqlite.unsafe_path"


def _never_opened(target: str, **kwargs: Any) -> sqlite3.Connection:
    raise AssertionError("SQLite must not open a file whose identity was not taken")


@posix_only
@pytest.mark.parametrize("where", ["temporary-file", "listing"])
def test_tb045_a_probe_that_cannot_run_is_not_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, where: str
) -> None:
    """Review F16: when the listing probe itself runs out of descriptors, the ``OSError`` reaches the caller and no
    verdict is cached; caching "no listing" would turn the descriptor check off for the rest of the process. The
    next connect probes again and uses the listing."""
    path = tmp_path / "a.db"
    make_database(path)
    monkeypatch.setattr(ts, "_LISTINGS", {})
    failures: list[str] = []
    exhausted = OSError(errno.EMFILE, os.strerror(errno.EMFILE))
    if where == "temporary-file":
        real_temporary_file = tempfile.TemporaryFile

        def temporary_file(*args: Any, **kwargs: Any) -> Any:
            if not failures:
                failures.append(where)
                raise exhausted
            return real_temporary_file(*args, **kwargs)

        monkeypatch.setattr(tempfile, "TemporaryFile", temporary_file)
    else:
        real_listdir = os.listdir

        def listdir(candidate: Any = ".") -> list[str]:
            if not failures and os.fspath(candidate) in ts._DESCRIPTOR_DIRECTORIES:
                failures.append(where)
                raise exhausted
            return real_listdir(candidate)

        monkeypatch.setattr(os, "listdir", listdir)
    with pytest.raises(OSError) as info:
        ts.connect(path).close()
    assert not isinstance(info.value, UnsafePath)
    assert info.value.errno == errno.EMFILE
    assert failures == [where]
    assert ts._LISTINGS == {}
    ts.connect(path).close()
    assert list(ts._LISTINGS.values()) == [ts._listing_directory()]
    assert ts._listing_directory() is not None


@posix_only
@pytest.mark.parametrize("failing", [1, 2], ids=["first-listing", "second-listing"])
def test_tb045_a_failing_listing_is_raised(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failing: int) -> None:
    """Review F14: once the listing was proved complete, a listing that fails (here: no descriptors left) raises its
    ``OSError``; it is not taken as "no listing", which would open without the descriptor check. A connection SQLite
    opened before the second listing failed is closed."""
    path = tmp_path / "a.db"
    make_database(path)
    directory = ts._listing_directory()
    assert directory is not None
    real_listdir, real_open = os.listdir, ts._open
    calls: list[str] = []
    opened: list[sqlite3.Connection] = []

    def listdir(candidate: Any = ".") -> list[str]:
        if os.fspath(candidate) == directory:
            calls.append(directory)
            if len(calls) == failing:
                raise OSError(errno.EMFILE, os.strerror(errno.EMFILE))
        return real_listdir(candidate)

    def recording_open(target: str, **kwargs: Any) -> sqlite3.Connection:
        opened.append(real_open(target, **kwargs))
        return opened[-1]

    monkeypatch.setattr(os, "listdir", listdir)
    monkeypatch.setattr(ts, "_open", recording_open)
    with pytest.raises(OSError) as info:
        ts.connect(path).close()
    assert not isinstance(info.value, UnsafePath)
    assert info.value.errno == errno.EMFILE
    assert len(opened) == failing - 1
    for connection in opened:
        with pytest.raises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")  # closed


@posix_only
@pytest.mark.parametrize(
    "number", [errno.EMFILE, errno.ENFILE, errno.ENOMEM, errno.EIO], ids=["EMFILE", "ENFILE", "ENOMEM", "EIO"]
)
def test_tb045_resource_errors_are_not_refusals(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, number: int) -> None:
    """Review F11: running out of descriptors or memory, or an I/O error, while the identity is taken is an
    operational problem: the ``OSError`` is raised as it is, not ``UnsafePath``."""
    path = tmp_path / "a.db"
    make_database(path)
    real_open = os.open

    def failing(candidate: Any, *args: Any, **kwargs: Any) -> int:
        if os.fspath(candidate) == str(path):
            raise OSError(number, os.strerror(number))
        return real_open(candidate, *args, **kwargs)

    monkeypatch.setattr(os, "open", failing)
    monkeypatch.setattr(ts, "_open", _never_opened)
    with pytest.raises(OSError) as info:
        ts.connect(path)
    assert not isinstance(info.value, UnsafePath)
    assert info.value.errno == number


@posix_only
def test_tb045_the_lock_is_free_in_a_forked_child() -> None:
    """Review F8: a child forked while another thread holds ``_OPEN_LOCK`` gets a free lock."""
    with ts._OPEN_LOCK:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)  # forking a process with threads
            child = os.fork()
        if child == 0:  # pragma: no cover - runs in the child
            os._exit(0 if ts._OPEN_LOCK.acquire(blocking=False) else 1)
    _, status = os.waitpid(child, 0)
    assert os.waitstatus_to_exitcode(status) == 0


@posix_only
def test_tb045_concurrent_connects_are_not_refused(tmp_path: Path) -> None:
    """Pools open and close connections from many threads; the snapshots must not refuse a correct open."""
    path = tmp_path / "a.db"
    make_database(path)
    errors: list[BaseException] = []

    def churn() -> None:
        try:
            for _ in range(25):
                connection = ts.connect(path)
                connection.execute("SELECT count(*) FROM parent").fetchone()
                connection.close()
        except BaseException as exc:  # reported below
            errors.append(exc)

    threads = [threading.Thread(target=churn) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []


def test_l3_link_planted_while_the_profile_is_applied(
    tmp_path: Path, symlinks: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review L-3: the path is checked again after ``_apply`` (WAL opens the side files then); a link planted
    while the profile is applied is refused and the connection is closed (defence in depth on Linux, whose VFS
    refuses linked side files itself)."""
    path = tmp_path / "a.db"
    make_database(path)
    victim = tmp_path / "victim"
    victim.write_bytes(b"victim")
    original = ts._apply
    seen: list[sqlite3.Connection] = []

    def apply_then_plant(connection: sqlite3.Connection, *args: Any, **kwargs: Any) -> None:
        original(connection, *args, **kwargs)
        seen.append(connection)
        (tmp_path / "a.db-journal").symlink_to(victim)

    monkeypatch.setattr(ts, "_apply", apply_then_plant)
    with pytest.raises(UnsafePath) as info:
        ts.connect(path)
    assert info.value.code == "sqlite.symlink"
    assert victim.read_bytes() == b"victim"
    with pytest.raises(sqlite3.ProgrammingError):
        seen[0].execute("SELECT 1")  # closed


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
