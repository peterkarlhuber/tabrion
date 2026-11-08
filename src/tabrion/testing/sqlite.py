"""SQLite fixtures of ``tabrion.testing``. Contract section 6, implemented in TAB-020 (contract I-36)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
import sqlite3

import pytest

__all__ = ["tabrion_sqlite", "tabrion_sqlite_path"]


@pytest.fixture
def tabrion_sqlite_path(tmp_path: Path) -> Path:
    """A path in ``tmp_path`` for a database file (not created): ``tmp_path / "tabrion-test.sqlite3"``."""
    return tmp_path / "tabrion-test.sqlite3"


@pytest.fixture
def tabrion_sqlite(tabrion_sqlite_path: Path) -> Iterator[sqlite3.Connection]:
    """A connection from ``tabrion.sqlite.connect`` (default profile, new file 0600); closed after the test."""
    from tabrion.sqlite import connect

    connection = connect(tabrion_sqlite_path)
    try:
        yield connection
    finally:
        connection.close()
