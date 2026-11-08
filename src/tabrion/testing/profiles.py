"""Profile and portability test cases for new applications (``tabrion.testing.profiles``).

Contract section 6; implemented in TAB-021. ``ProfileCase`` is not specified in doc 04 and is proposed here for the
G-T1 review (``docs/api/contract.md``, "Open points").
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import sqlite3

__all__ = ["ConnectionFactory", "ProfileCase", "portability_cases", "sqlite_profile_cases"]

ConnectionFactory = Callable[[], sqlite3.Connection]
"""Opens a fresh connection under the application's own settings."""


@dataclass(frozen=True, slots=True)
class ProfileCase:
    id: str
    """Test ID, e.g. ``TAB-T-D01``."""
    title: str
    check: Callable[[ConnectionFactory], None]
    """Raises ``AssertionError`` when the connection factory violates the rule."""


def sqlite_profile_cases() -> list[ProfileCase]:
    """FK, WAL, busy timeout, permissions, symlinks and IMMEDIATE for any connection factory."""
    raise NotImplementedError("tabrion.testing.profiles.sqlite_profile_cases: implemented in TAB-021")


def portability_cases() -> list[ProfileCase]:
    """The data type rules of the specification, section 6 (TAB-T-D01..D09)."""
    raise NotImplementedError("tabrion.testing.profiles.portability_cases: implemented in TAB-021")
