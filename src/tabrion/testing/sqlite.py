"""SQLite fixtures of ``tabrion.testing``. Contract section 6; implemented in TAB-020."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
import sqlite3

import pytest

__all__ = ["tabrion_sqlite", "tabrion_sqlite_path"]


@pytest.fixture
def tabrion_sqlite_path(tmp_path: Path) -> Path:
    """A path in ``tmp_path`` for a database file (not created)."""
    raise NotImplementedError("tabrion.testing.tabrion_sqlite_path: implemented in TAB-020")


@pytest.fixture
def tabrion_sqlite(tabrion_sqlite_path: Path) -> Iterator[sqlite3.Connection]:
    """A connection from ``tabrion.sqlite.connect``; closed after the test."""
    raise NotImplementedError("tabrion.testing.tabrion_sqlite: implemented in TAB-020")
