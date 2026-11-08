"""Helpers for the ``tabrion.sqlite`` suite (TAB-013, TAB-014). Standard library and pytest only.

The platforms workflow runs this directory on macOS and Windows as well: POSIX file modes are checked only where
they exist, and symbolic-link cases skip where the account may not create links.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import sqlite3
import sys

import pytest

POSIX = os.name == "posix"
posix_only = pytest.mark.skipif(not POSIX, reason="POSIX file modes and O_NOFOLLOW (Windows: TAB-T-S12)")
CANARY = "SECRET-4711"


def can_symlink(directory: Path) -> bool:
    """Whether this account may create symbolic links here (Windows needs a privilege or developer mode)."""
    probe = directory / ".symlink-probe"
    try:
        probe.symlink_to(directory / ".symlink-target")
    except (OSError, NotImplementedError):
        return False
    probe.unlink()
    return True


@pytest.fixture
def symlinks(tmp_path: Path) -> None:
    if not can_symlink(tmp_path):
        pytest.skip("symbolic links cannot be created here")


@contextmanager
def umask(value: int) -> Iterator[None]:
    previous = os.umask(value)
    try:
        yield
    finally:
        os.umask(previous)


def mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def files(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.iterdir())


@pytest.fixture
def opened() -> Iterator[Callable[[sqlite3.Connection], sqlite3.Connection]]:
    """Register connections so they are closed after the test (an unclosed one is a ResourceWarning, an error)."""
    connections: list[sqlite3.Connection] = []

    def register(connection: sqlite3.Connection) -> sqlite3.Connection:
        connections.append(connection)
        return connection

    yield register
    for connection in connections:
        connection.close()


def plain(path: Path | str, **kwargs: object) -> sqlite3.Connection:
    """A plain ``sqlite3`` connection in autocommit mode (a connection Tabrion did not open)."""
    return sqlite3.connect(str(path), isolation_level=None, **kwargs)  # type: ignore[call-overload, no-any-return]


def make_database(path: Path, rows: int = 3) -> None:
    """A small file in WAL mode with a parent and a child table, closed (and checkpointed) afterwards."""
    connection = plain(path)
    try:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY, secret TEXT)")
        connection.execute("CREATE TABLE child (id INTEGER PRIMARY KEY, parent_id INTEGER REFERENCES parent (id))")
        connection.executemany(
            "INSERT INTO parent (id, secret) VALUES (?, ?)", [(i, f"{CANARY}-{i}") for i in range(rows)]
        )
    finally:
        connection.close()


WINDOWS = sys.platform == "win32"
