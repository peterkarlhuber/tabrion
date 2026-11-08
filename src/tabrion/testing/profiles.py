"""Profile and portability test cases for new applications (``tabrion.testing.profiles``).

Contract section 6; implemented in TAB-021. The case types are not specified in doc 04; G-T1 rejected the first
proposal (a zero-argument connection factory, G-3) and condition C-2 fixed this shape (``docs/api/contract.md``,
"Open points"):

* ``sqlite_profile_cases()`` tests file mode and symlink refusal of *new* files, so each case chooses the path and
  calls the application's ``SQLiteConnect`` with it.
* ``portability_cases()`` must run on both engines (spec section 6), so each case takes a layer-B ``Database``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import sqlite3
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tabrion._sa.database import Database

__all__ = ["PortabilityCase", "ProfileCase", "SQLiteConnect", "portability_cases", "sqlite_profile_cases"]

SQLiteConnect = Callable[[Path], sqlite3.Connection]
"""Opens a connection to the given path under the application's own settings (e.g. its wrapper of
``tabrion.sqlite.connect``)."""


@dataclass(frozen=True, slots=True)
class ProfileCase:
    id: str
    """Test ID, e.g. ``TAB-T-S01``."""
    title: str
    check: Callable[[SQLiteConnect, Path], None]
    """Called with the application's connect function and a fresh, empty directory; the case picks the file path
    inside it. Raises ``AssertionError`` when the connect function violates the rule."""


@dataclass(frozen=True, slots=True)
class PortabilityCase:
    id: str
    """Test ID, e.g. ``TAB-T-D01``."""
    title: str
    check: Callable[[Database], None]
    """Called with an open ``Database`` on an empty schema, SQLite or PostgreSQL. Raises ``AssertionError`` when
    the engine breaks a data type rule."""


def sqlite_profile_cases() -> list[ProfileCase]:
    """FK, WAL, busy timeout, permissions, symlinks and IMMEDIATE for any SQLite connect function."""
    raise NotImplementedError("tabrion.testing.profiles.sqlite_profile_cases: implemented in TAB-021")


def portability_cases() -> list[PortabilityCase]:
    """The data type rules of the specification, section 6 (TAB-T-D01..D09), for SQLite and PostgreSQL."""
    raise NotImplementedError("tabrion.testing.profiles.portability_cases: implemented in TAB-021")
