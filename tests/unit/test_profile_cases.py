"""``tabrion.testing.profiles`` on SQLite (TAB-021, contract section 6, implementation note I-58).

Every profile case (TAB-T-F01..F06) passes for ``tabrion.sqlite.connect`` and fails for a connect function that
breaks its rule; every portability case (TAB-T-D01..D09) passes on a SQLite ``Database``. The PostgreSQL side is
``tests/postgres/test_portability_postgres.py``.
"""

from __future__ import annotations

from collections.abc import Callable
import os
from pathlib import Path
import sqlite3

import pytest

pytest.importorskip("sqlalchemy")

from tabrion import Database
import tabrion.sqlite
from tabrion.sqlite import JournalMode, SQLiteProfile
from tabrion.testing.profiles import (
    PortabilityCase,
    ProfileCase,
    portability_cases,
    sqlite_profile_cases,
)
from tests.sqlite.conftest import can_symlink

POSIX = os.name == "posix"


def case(cases: list[ProfileCase], case_id: str) -> ProfileCase:
    return next(c for c in cases if c.id == case_id)


def test_case_lists() -> None:
    profile = sqlite_profile_cases()
    assert [c.id for c in profile] == [f"TAB-T-F0{n}" for n in range(1, 7)]
    portability = portability_cases()
    assert [c.id for c in portability] == [f"TAB-T-D0{n}" for n in range(1, 10)]
    assert all(isinstance(c, ProfileCase) and c.title for c in profile)
    assert all(isinstance(c, PortabilityCase) and c.title for c in portability)


@pytest.mark.parametrize("profile_case", sqlite_profile_cases(), ids=lambda c: c.id)
def test_profile_cases_pass_for_tabrion_sqlite_connect(profile_case: ProfileCase, tmp_path: Path) -> None:
    profile_case.check(tabrion.sqlite.connect, tmp_path)


def plain(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path)


def short_timeout(path: Path) -> sqlite3.Connection:
    connection = tabrion.sqlite.connect(path)
    connection.execute("PRAGMA busy_timeout = 500")
    return connection


def delete_journal(path: Path) -> sqlite3.Connection:
    return tabrion.sqlite.connect(path, profile=SQLiteProfile(journal_mode=JournalMode.DELETE))


def deferred(path: Path) -> sqlite3.Connection:
    connection = tabrion.sqlite.connect(path)
    connection.isolation_level = "DEFERRED"
    return connection


BROKEN: list[tuple[str, Callable[[Path], sqlite3.Connection], str]] = [
    ("TAB-T-F01", plain, "foreign_keys"),
    ("TAB-T-F02", delete_journal, "journal_mode"),
    ("TAB-T-F03", short_timeout, "busy_timeout"),
    ("TAB-T-F06", deferred, "DEFERRED"),
]


@pytest.mark.parametrize(("case_id", "connect", "message"), BROKEN, ids=[b[0] for b in BROKEN])
def test_profile_cases_fail_for_a_broken_connect(
    case_id: str, connect: Callable[[Path], sqlite3.Connection], message: str, tmp_path: Path
) -> None:
    with pytest.raises(AssertionError, match=message):
        case(sqlite_profile_cases(), case_id).check(connect, tmp_path)


@pytest.mark.skipif(not POSIX, reason="POSIX modes")
@pytest.mark.security
def test_f04_fails_for_a_world_readable_file(tmp_path: Path) -> None:
    with pytest.raises(AssertionError, match="not 0600"):
        case(sqlite_profile_cases(), "TAB-T-F04").check(plain, tmp_path)


@pytest.mark.security
def test_f05_fails_for_a_connect_that_follows_links(tmp_path: Path) -> None:
    if not can_symlink(tmp_path):
        pytest.skip("symbolic links cannot be created here")
    with pytest.raises(AssertionError, match="symlink"):
        case(sqlite_profile_cases(), "TAB-T-F05").check(plain, tmp_path)


def test_a_connect_function_must_return_a_connection(tmp_path: Path) -> None:
    with pytest.raises(AssertionError, match=r"sqlite3\.Connection"):
        case(sqlite_profile_cases(), "TAB-T-F01").check(lambda path: object(), tmp_path)  # type: ignore[arg-type,return-value]


@pytest.mark.parametrize("portability_case", portability_cases(), ids=lambda c: c.id)
@pytest.mark.parametrize("url", ["file", "memory"])
def test_portability_cases_pass_on_sqlite(portability_case: PortabilityCase, url: str, tmp_path: Path) -> None:
    location = f"sqlite:///{tmp_path / 'p.db'}" if url == "file" else "sqlite://"
    with Database.from_url(location) as database:
        portability_case.check(database)
        tables = database.fetch_all("SELECT name FROM sqlite_master WHERE name LIKE 'tabrion_d%'")
        assert tables == []  # each case drops its tables


def test_d08_fails_without_foreign_keys(tmp_path: Path) -> None:
    import sqlalchemy

    engine = sqlalchemy.create_engine(f"sqlite:///{tmp_path / 'nofk.db'}")
    try:
        with Database.from_engine(engine, install_listeners=False) as database:
            d08 = next(c for c in portability_cases() if c.id == "TAB-T-D08")
            with pytest.raises(AssertionError, match="foreign_keys"):
                d08.check(database)
    finally:
        engine.dispose()
