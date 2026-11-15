"""TAB-XW-7: ``tabrion.sqlite.delete_database_file`` (contract section 3.10, note I-171; ADR 0015 section 9 N-4).

The named tests of N-4 are ``test_swap_between_check_and_unlink_refused`` and
``test_side_file_symlink_unlinked_not_followed``. The platforms workflow runs this file on macOS and Windows as well:
there is no directory descriptor on Windows, so the function works by name there and Windows itself refuses to
delete a file another connection keeps open; ``test_windows_*`` force that path on POSIX, and
``test_idle_rollback_connection`` states what each platform does with an idle connection.
"""

from __future__ import annotations

import os
from pathlib import Path
import sqlite3

import pytest

from tabrion import sqlite as ts
from tabrion.errors import DatabaseInUse, UnsafePath, UsageError
from tests.sqlite.conftest import POSIX, WINDOWS, can_symlink, digest, files, make_database, plain, posix_only

SIDES = ("-wal", "-shm", "-journal")


def _private(path: Path, content: bytes = b"") -> None:
    ts.create_private_file(path)
    path.write_bytes(content)


def _rollback_database(path: Path, rows: int = 3) -> None:
    """A closed file in rollback-journal mode (no side files)."""
    make_database(path, rows=rows)
    connection = plain(path)
    connection.execute("PRAGMA journal_mode = DELETE").fetchall()
    connection.close()


@pytest.fixture
def symlinks(tmp_path: Path) -> None:
    if not can_symlink(tmp_path):
        pytest.skip("symbolic links cannot be created here")


@pytest.fixture(params=["directory descriptor", "by name"])
def by_name(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> bool:
    """Both ways: through the directory descriptor (POSIX) and by name (Windows, forced here on POSIX)."""
    forced = bool(request.param == "by name")
    if forced:
        monkeypatch.setattr(ts, "_DIR_FD", False)
    elif not ts._DIR_FD:
        pytest.skip("POSIX only")
    return forced


def test_deletes_the_file_and_its_side_files(tmp_path: Path, by_name: bool) -> None:
    database = tmp_path / "app.db"
    make_database(database)
    for suffix in SIDES:
        _private(Path(f"{database}{suffix}"))
    (tmp_path / "app.db.keep").write_text("not a side file")
    deleted = ts.delete_database_file(database)
    assert deleted == (database, *(Path(f"{database}{suffix}") for suffix in SIDES))
    assert files(tmp_path) == ["app.db.keep"]


def test_returns_only_what_existed(tmp_path: Path) -> None:
    _rollback_database(tmp_path / "app.db")
    assert ts.delete_database_file(str(tmp_path / "app.db")) == (tmp_path / "app.db",)
    assert files(tmp_path) == []


def test_relative_name_in_the_working_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _rollback_database(tmp_path / "app.db")
    assert ts.delete_database_file("app.db") == (Path("app.db"),)
    assert files(tmp_path) == []


def test_missing_file(tmp_path: Path, by_name: bool) -> None:
    with pytest.raises(FileNotFoundError) as caught:
        ts.delete_database_file(tmp_path / "missing.db")
    assert str(tmp_path) not in str(caught.value)
    assert ts.delete_database_file(tmp_path / "missing.db", missing_ok=True) == ()


@pytest.mark.parametrize("content", [b"", b"SQLite format 2\x00 is not this format", b"plain text " * 10])
def test_file_that_is_no_database_is_refused(tmp_path: Path, content: bytes, by_name: bool) -> None:
    _private(tmp_path / "notes.db", content)
    with pytest.raises(UsageError) as caught:
        ts.delete_database_file(tmp_path / "notes.db")
    assert caught.value.code == "sqlite.not_a_database"
    assert (tmp_path / "notes.db").read_bytes() == content


def test_file_whose_header_page_sqlite_refuses_is_refused(tmp_path: Path) -> None:
    """The header string alone is not enough: SQLite must read the file as a database (``SQLITE_NOTADB``)."""
    _private(tmp_path / "fake.db", b"SQLite format 3\x00" + b"\xff" * 4080)
    with pytest.raises(UsageError) as caught:
        ts.delete_database_file(tmp_path / "fake.db")
    assert caught.value.code == "sqlite.not_a_database"
    assert (tmp_path / "fake.db").exists()


@pytest.mark.parametrize("name", [":memory:", "", "dir/", "dir/..", "."])
def test_names_without_a_file_are_refused(name: str) -> None:
    with pytest.raises(UsageError) as caught:
        ts.delete_database_file(name)
    assert caught.value.code == "sqlite.invalid_target"


def test_directory_is_refused(tmp_path: Path, by_name: bool) -> None:
    (tmp_path / "dir.db").mkdir()
    with pytest.raises(UnsafePath) as caught:
        ts.delete_database_file(tmp_path / "dir.db")
    assert caught.value.code == "sqlite.unsafe_path"
    assert (tmp_path / "dir.db").is_dir()


def test_side_file_that_is_a_directory_is_refused_before_anything_is_deleted(tmp_path: Path, by_name: bool) -> None:
    _rollback_database(tmp_path / "app.db")
    (tmp_path / "app.db-wal").mkdir()
    with pytest.raises(UnsafePath) as caught:
        ts.delete_database_file(tmp_path / "app.db")
    assert caught.value.code == "sqlite.unsafe_path"
    assert files(tmp_path) == ["app.db", "app.db-wal"]


def test_open_wal_connection_is_in_use(tmp_path: Path, by_name: bool) -> None:
    """In WAL mode every open connection, also an idle one, blocks SQLite's exclusive lock."""
    make_database(tmp_path / "app.db")
    connection = ts.connect(tmp_path / "app.db")
    try:
        connection.execute("SELECT count(*) FROM parent").fetchall()
        before = digest(tmp_path / "app.db")
        with pytest.raises(DatabaseInUse) as caught:
            ts.delete_database_file(tmp_path / "app.db")
        assert caught.value.code == "sqlite.in_use"
        assert str(tmp_path) not in str(caught.value)
        assert digest(tmp_path / "app.db") == before
        assert connection.execute("SELECT count(*) FROM parent").fetchone() == (3,)
    finally:
        connection.close()
    assert ts.delete_database_file(tmp_path / "app.db")[0] == tmp_path / "app.db"


@pytest.mark.parametrize("lock", ["BEGIN", "BEGIN IMMEDIATE"])
def test_reader_or_writer_in_rollback_mode_is_in_use(tmp_path: Path, lock: str) -> None:
    _rollback_database(tmp_path / "app.db")
    connection = plain(tmp_path / "app.db")
    try:
        connection.execute(lock)
        connection.execute("SELECT count(*) FROM parent").fetchall()
        with pytest.raises(DatabaseInUse):
            ts.delete_database_file(tmp_path / "app.db")
        assert (tmp_path / "app.db").exists()
    finally:
        connection.close()


def test_idle_rollback_connection(tmp_path: Path) -> None:
    """An idle connection in rollback mode holds no lock, so SQLite's lock check cannot see it (documented, I-171).
    POSIX deletes the file anyway (the connection keeps the unlinked file); Windows refuses to delete a file another
    handle keeps open, which the function reports as in use."""
    _rollback_database(tmp_path / "app.db")
    connection = plain(tmp_path / "app.db")
    try:
        connection.execute("SELECT count(*) FROM parent").fetchall()
        if WINDOWS:
            with pytest.raises(DatabaseInUse):
                ts.delete_database_file(tmp_path / "app.db")
            assert (tmp_path / "app.db").exists()
        else:
            assert ts.delete_database_file(tmp_path / "app.db") == (tmp_path / "app.db",)
            assert files(tmp_path) == []
    finally:
        connection.close()


def test_windows_open_file_refusal_is_in_use(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """By name (Windows), a ``PermissionError`` of the unlink is the file another handle keeps open: ``DatabaseInUse``.
    Through the directory descriptor (POSIX), the same error is a permission problem and propagates unchanged."""
    _rollback_database(tmp_path / "app.db")
    real = os.unlink

    def refuse(path: str, *, dir_fd: int | None = None) -> None:
        if os.path.basename(path) == "app.db":
            raise PermissionError(13, "The process cannot access the file because it is being used")
        real(path, dir_fd=dir_fd)

    monkeypatch.setattr(os, "unlink", refuse)
    monkeypatch.setattr(ts, "_DIR_FD", False)
    with pytest.raises(DatabaseInUse) as caught:
        ts.delete_database_file(tmp_path / "app.db")
    assert caught.value.code == "sqlite.in_use"
    if POSIX and ts._O_NOFOLLOW:
        monkeypatch.setattr(ts, "_DIR_FD", True)
        with pytest.raises(PermissionError):
            ts.delete_database_file(tmp_path / "app.db")
    assert (tmp_path / "app.db").exists()


def test_windows_directory_link_is_removed_with_rmdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """By name, a side file that is a directory link or junction (Windows) cannot be unlinked; ``rmdir`` removes
    the link itself, never what it points to."""
    monkeypatch.setattr(ts, "_DIR_FD", False)
    entries = ts._Entries(str(tmp_path))
    (tmp_path / "junction").mkdir()

    def refuse(path: str, *, dir_fd: int | None = None) -> None:
        raise IsADirectoryError(21, "is a directory")

    monkeypatch.setattr(os, "unlink", refuse)
    entries.remove_link("junction")
    assert files(tmp_path) == []


@pytest.mark.security
@pytest.mark.usefixtures("symlinks")
def test_symbolic_link_is_refused(tmp_path: Path, by_name: bool) -> None:
    make_database(tmp_path / "real.db")
    (tmp_path / "link.db").symlink_to(tmp_path / "real.db")
    with pytest.raises(UnsafePath) as caught:
        ts.delete_database_file(tmp_path / "link.db")
    assert caught.value.code == "sqlite.symlink"
    assert str(tmp_path) not in str(caught.value)
    assert files(tmp_path) == ["link.db", "real.db"]


@pytest.mark.security
@pytest.mark.usefixtures("symlinks")
def test_side_file_symlink_unlinked_not_followed(tmp_path: Path, by_name: bool) -> None:
    """N-4 (ADR 0015 section 9): a side file that is a symbolic link is removed itself; its target stays as it was,
    and SQLite never opens it (the link is gone before SQLite opens the database for the lock check)."""
    _rollback_database(tmp_path / "app.db")
    victim = tmp_path / "victim.db"
    make_database(victim, rows=5)
    before = digest(victim)
    (tmp_path / "app.db-journal").symlink_to(victim)
    (tmp_path / "app.db-wal").symlink_to(tmp_path / "missing-target")
    deleted = ts.delete_database_file(tmp_path / "app.db")
    assert deleted == (tmp_path / "app.db", tmp_path / "app.db-wal", tmp_path / "app.db-journal")
    assert files(tmp_path) == ["victim.db"]
    assert digest(victim) == before


@posix_only
@pytest.mark.security
@pytest.mark.usefixtures("symlinks")
def test_directory_that_is_a_link_is_refused(tmp_path: Path) -> None:
    """The directory descriptor is opened with ``O_NOFOLLOW``: the database's own directory must not be a link."""
    (tmp_path / "real").mkdir()
    make_database(tmp_path / "real" / "app.db")
    (tmp_path / "linked").symlink_to(tmp_path / "real")
    with pytest.raises(UnsafePath) as caught:
        ts.delete_database_file(tmp_path / "linked" / "app.db")
    assert caught.value.code == "sqlite.symlink"
    assert (tmp_path / "real" / "app.db").exists()


@pytest.mark.security
def test_swap_between_check_and_unlink_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, by_name: bool) -> None:
    """N-4 (ADR 0015 section 9): device and inode of the checked file are compared again right before the unlink; a
    file swapped in after the check (here: right after the lock check) is refused and stays."""
    database = tmp_path / "app.db"
    _rollback_database(database)
    _rollback_database(tmp_path / "other.db", rows=9)
    swapped = digest(tmp_path / "other.db")
    real = ts._refuse_if_in_use

    def check_then_swap(name: str, expected: tuple[int, int]) -> None:
        real(name, expected)
        os.replace(database, tmp_path / "moved.db")  # the checked file stays, so its inode is not reused
        os.replace(tmp_path / "other.db", database)

    monkeypatch.setattr(ts, "_refuse_if_in_use", check_then_swap)
    if not os.stat(database).st_ino:
        pytest.skip("no file index on this file system")
    with pytest.raises(UnsafePath) as caught:
        ts.delete_database_file(database)
    assert caught.value.code == "sqlite.unsafe_path"
    assert digest(database) == swapped
    assert files(tmp_path) == ["app.db", "moved.db"]


@pytest.mark.security
def test_swap_before_the_lock_check_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, by_name: bool) -> None:
    """A file swapped in between the header check and SQLite's open is caught by the open check (contract I-22)."""
    database = tmp_path / "app.db"
    _rollback_database(database)
    _rollback_database(tmp_path / "other.db", rows=9)
    real = ts._open_checked

    def swap_then_open(target: str, **kwargs: object) -> sqlite3.Connection | None:
        os.replace(database, tmp_path / "moved.db")
        os.replace(tmp_path / "other.db", database)
        return real(target, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(ts, "_open_checked", swap_then_open)
    if not os.stat(database).st_ino:
        pytest.skip("no file index on this file system")
    with pytest.raises(UnsafePath):
        ts.delete_database_file(database)
    assert files(tmp_path) == ["app.db", "moved.db"]


def test_unverified_open_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When SQLite's open of the checked file cannot be proved (``_open_checked`` gives up), nothing is deleted."""
    _rollback_database(tmp_path / "app.db")
    monkeypatch.setattr(ts, "_open_checked", lambda *args, **kwargs: None)
    with pytest.raises(UnsafePath):
        ts.delete_database_file(tmp_path / "app.db")
    assert files(tmp_path) == ["app.db"]


def test_other_sqlite_error_of_the_lock_check_propagates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _rollback_database(tmp_path / "app.db")
    real = ts._rows

    def failing(connection: sqlite3.Connection, statement: str, limit: int | None = None) -> list[tuple[object, ...]]:
        if statement == "PRAGMA schema_version":
            raise sqlite3.OperationalError("disk I/O error")
        return real(connection, statement, limit)

    monkeypatch.setattr(ts, "_rows", failing)
    with pytest.raises(sqlite3.OperationalError):
        ts.delete_database_file(tmp_path / "app.db")
    assert files(tmp_path) == ["app.db"]


@posix_only
@pytest.mark.security
@pytest.mark.parametrize("side", ["", "-wal"])
def test_group_writable_files_are_refused(tmp_path: Path, side: str) -> None:
    """The owner and mode rules of ``connect`` (contract I-22 (b)): a file someone else could have written."""
    _rollback_database(tmp_path / "app.db")
    if side:
        _private(Path(f"{tmp_path / 'app.db'}{side}"))
    Path(f"{tmp_path / 'app.db'}{side}").chmod(0o664)
    with pytest.raises(UnsafePath) as caught:
        ts.delete_database_file(tmp_path / "app.db")
    assert caught.value.code == "sqlite.unsafe_mode"
    assert (tmp_path / "app.db").exists()


def test_unreadable_file_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, by_name: bool) -> None:
    """A file this user may not open (``EACCES``, ``EPERM``) is ``UnsafePath("sqlite.unsafe_path")``; another error of
    the open (no descriptors left) propagates as the ``OSError`` it is."""
    _rollback_database(tmp_path / "app.db")
    real = os.open

    for error in (13, 1, 24):

        def refuse(path: str, flags: int, mode: int = 0o777, *, dir_fd: int | None = None, error: int = error) -> int:
            if os.path.basename(path) == "app.db":
                raise OSError(error, os.strerror(error))
            return real(path, flags, mode, dir_fd=dir_fd)

        monkeypatch.setattr(os, "open", refuse)
        if error == 24:
            with pytest.raises(OSError) as raised:
                ts.delete_database_file(tmp_path / "app.db")
            assert raised.value.errno == 24
        else:
            with pytest.raises(UnsafePath) as caught:
                ts.delete_database_file(tmp_path / "app.db")
            assert caught.value.code == "sqlite.unsafe_path"
        monkeypatch.undo()
        monkeypatch.setattr(ts, "_DIR_FD", not by_name and ts._DIR_FD)
    assert files(tmp_path) == ["app.db"]
