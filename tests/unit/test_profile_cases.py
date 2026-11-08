"""``tabrion.testing.profiles`` on SQLite (TAB-021, contract section 6, implementation note I-58).

Every profile case (TAB-T-F01..F06) passes for ``tabrion.sqlite.connect`` and fails for a connect function that
breaks its rule; every portability case (TAB-T-D01..D09) passes on a SQLite ``Database``. The PostgreSQL side is
``tests/postgres/test_portability_postgres.py``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
import itertools
import json
import os
from pathlib import Path
import sqlite3
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from tabrion import Database
from tabrion.errors import UnsafePath
import tabrion.sqlite
from tabrion.sqlite import JournalMode, SQLiteProfile
from tabrion.testing import profiles
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


# Review M-1 and M-3: each rule fails for a connect function that breaks only that rule. Every refusal below is the
# UnsafePath("sqlite.symlink") of tabrion.sqlite.connect, so a case fails for the rule it is about, not for the
# kind of error.


def refuse() -> UnsafePath:
    return UnsafePath("sqlite.symlink")


def side_names(path: Path) -> list[Path]:
    return [Path(f"{path}{suffix}") for suffix in ("-wal", "-shm", "-journal")]


def private(path: Path) -> sqlite3.Connection:
    """The profile of tabrion.sqlite.connect without its path checks: follows every link."""
    if not path.exists():
        os.close(os.open(path, os.O_CREAT | os.O_WRONLY, 0o600))
    connection = sqlite3.connect(path, isolation_level=None)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
    except BaseException:
        connection.close()
        raise
    return connection


def dangling_only(path: Path) -> sqlite3.Connection:
    """Refuses a link only when its target is missing (an exists() test): follows links to existing files."""
    if any(p.is_symlink() and not p.exists() for p in [path, *side_names(path)]):
        raise refuse()
    return private(path)


def main_link_only(path: Path) -> sqlite3.Connection:
    """Refuses a linked main path, never a linked side file."""
    if path.is_symlink():
        raise refuse()
    return private(path)


def side_dangling_only(path: Path) -> sqlite3.Connection:
    """Refuses every linked main path, but a side link only when it dangles."""
    if path.is_symlink() or any(p.is_symlink() and not p.exists() for p in side_names(path)):
        raise refuse()
    return private(path)


def creates_target(path: Path) -> sqlite3.Connection:
    """Refuses links, but only after it opened the path once, which creates a missing link target."""
    if path.is_symlink():
        path.open("ab").close()
        raise refuse()
    return tabrion.sqlite.connect(path)


def creates_side_target(path: Path) -> sqlite3.Connection:
    """Like creates_target for the side files."""
    linked = [p for p in side_names(path) if p.is_symlink()]
    for side in linked:
        side.open("ab").close()
    if linked:
        raise refuse()
    return tabrion.sqlite.connect(path)


def refuses_existing(path: Path) -> sqlite3.Connection:
    """Refuses every existing name, so it 'refuses' every side link for the wrong reason."""
    if path.exists() or path.is_symlink():
        raise refuse()
    return tabrion.sqlite.connect(path)


def wrong_error(path: Path) -> sqlite3.Connection:
    """Refuses links, with an error that is not UnsafePath."""
    if any(p.is_symlink() for p in [path, *side_names(path)]):
        raise ValueError("a link")
    return tabrion.sqlite.connect(path)


def wrong_code(path: Path) -> sqlite3.Connection:
    if any(p.is_symlink() for p in [path, *side_names(path)]):
        raise UnsafePath("sqlite.unsafe_path")
    return tabrion.sqlite.connect(path)


F05_BROKEN: list[tuple[Callable[[Path], sqlite3.Connection], str]] = [
    (dangling_only, "accepted a symlink to an existing database file"),
    # sqlite3 itself may fail on these links (OperationalError), which is not a refusal either
    (main_link_only, "-wal symlink to a missing file"),
    (side_dangling_only, "-wal symlink to an existing file"),
    (creates_target, "created the symlink's target"),
    (creates_side_target, "created the target of the -wal symlink"),
    (refuses_existing, "cannot reopen an existing database file"),
    (wrong_error, "refused with ValueError, not UnsafePath"),
    (wrong_code, "refused with sqlite.unsafe_path, not sqlite.symlink"),
]


@pytest.mark.security
@pytest.mark.parametrize(("connect", "message"), F05_BROKEN, ids=[b[0].__name__ for b in F05_BROKEN])
def test_f05_fails_for_each_kind_of_link_a_connect_follows(
    connect: Callable[[Path], sqlite3.Connection], message: str, tmp_path: Path
) -> None:
    if not can_symlink(tmp_path):
        pytest.skip("symbolic links cannot be created here")
    with pytest.raises(AssertionError, match=message):
        case(sqlite_profile_cases(), "TAB-T-F05").check(connect, tmp_path)


@pytest.mark.security
def test_f05_a_dangling_only_connect_writes_through_a_link(tmp_path: Path) -> None:
    """Why F05 needs the existing-file links: dangling_only opens a link to a real file and writes into it."""
    if not can_symlink(tmp_path):
        pytest.skip("symbolic links cannot be created here")
    real = tmp_path / "real.db"
    tabrion.sqlite.connect(real).close()
    (tmp_path / "link.db").symlink_to(real)
    connection = dangling_only(tmp_path / "link.db")
    connection.execute("CREATE TABLE x (a)")
    connection.close()
    with pytest.raises(UnsafePath):
        tabrion.sqlite.connect(tmp_path / "link.db")
    check = sqlite3.connect(real)
    try:
        assert check.execute("SELECT name FROM sqlite_master").fetchall() == [("x",)]
    finally:
        check.close()


@pytest.mark.skipif(not POSIX, reason="POSIX modes")
@pytest.mark.security
@pytest.mark.parametrize("suffix", ["-wal", "-shm"])
def test_f04_fails_for_a_world_readable_side_file(suffix: str, tmp_path: Path) -> None:
    def readable_side(path: Path) -> sqlite3.Connection:
        connection = tabrion.sqlite.connect(path)
        connection.execute("CREATE TABLE IF NOT EXISTS tabrion_case (n INTEGER)")  # creates -wal and -shm 0600
        Path(f"{path}{suffix}").chmod(0o644)
        return connection

    with pytest.raises(AssertionError, match=rf"f04\.db{suffix} has mode 644"):
        case(sqlite_profile_cases(), "TAB-T-F04").check(readable_side, tmp_path)


@pytest.mark.skipif(not hasattr(sqlite3.Connection, "autocommit"), reason="sqlite3 autocommit needs Python 3.12")
def test_f06_fails_for_autocommit_false(tmp_path: Path) -> None:
    def autocommit_false(path: Path) -> sqlite3.Connection:
        options: dict[str, Any] = {"autocommit": False}  # Python 3.12+; mypy checks against 3.11
        connection: sqlite3.Connection = sqlite3.connect(path, isolation_level=None, **options)
        return connection

    with pytest.raises(AssertionError, match="autocommit=False"):
        case(sqlite_profile_cases(), "TAB-T-F06").check(autocommit_false, tmp_path)


def portability(case_id: str) -> PortabilityCase:
    return next(c for c in portability_cases() if c.id == case_id)


@pytest.fixture
def foreign_sqlite(tmp_path: Path) -> Iterator[Callable[..., Database]]:
    """A Database on a plain SQLAlchemy engine with the given PRAGMAs and engine options (no Tabrion listeners)."""
    import sqlalchemy

    engines: list[sqlalchemy.Engine] = []

    def make(*pragmas: str, **options: Any) -> Database:
        engine = sqlalchemy.create_engine(f"sqlite:///{tmp_path / f'e{len(engines)}.db'}", **options)

        @sqlalchemy.event.listens_for(engine, "connect")
        def on_connect(dbapi: sqlite3.Connection, record: object) -> None:
            for pragma in pragmas:
                dbapi.execute(pragma)

        engines.append(engine)
        return Database.from_engine(engine, install_listeners=False)

    yield make
    for engine in engines:
        engine.dispose()


@pytest.mark.parametrize(("case_id", "message"), [("TAB-T-D05", "2 as a boolean"), ("TAB-T-D06", "six characters")])
def test_check_cases_fail_under_ignore_check_constraints(
    case_id: str, message: str, foreign_sqlite: Callable[..., Database]
) -> None:
    database = foreign_sqlite("PRAGMA foreign_keys = ON", "PRAGMA ignore_check_constraints = ON")
    with database, pytest.raises(AssertionError, match=message):
        portability(case_id).check(database)


def test_d07_fails_for_a_json_deserializer_that_changes_values(foreign_sqlite: Callable[..., Database]) -> None:
    database = foreign_sqlite("PRAGMA foreign_keys = ON", json_deserializer=lambda s: json.loads(s, parse_int=str))
    with database, pytest.raises(AssertionError, match="JSON document changed"):
        portability("TAB-T-D07").check(database)


def broken_mappings() -> list[tuple[str, str, Callable[..., Any], str]]:
    """(case, mapping function, broken replacement, message) on SQLite: each breaks the rule of its row."""
    return [
        ("TAB-T-D01", "_d01_text", lambda value: value.isoformat(), r"2024-01-02T23:00:00-05:00"),
        ("TAB-T-D02", "_d02_type", lambda sa, sqlite: sa.Float(), "is not exact"),
        ("TAB-T-D03", "_d03_id", lambda sa: (sa.BigInteger(), sa.Identity()), "did not generate the ids"),
        # ids generated by the application, so only the rowid check sees that BIGINT is no alias
        (
            "TAB-T-D03",
            "_d03_id",
            lambda sa: (sa.BigInteger(), sa.ColumnDefault(itertools.count(10).__next__)),
            "alias of rowid",
        ),
        ("TAB-T-D04", "_d04_type", lambda sa, sqlite: sa.Uuid(as_uuid=False), "32 characters, not 36"),
        ("TAB-T-D04", "_d04_type", lambda sa, sqlite: uuid_object_text(sa), r"UUID\('12345678"),
        ("TAB-T-D09", "_d09_type", lambda sa: sa.Float(asdecimal=True), r"Decimal\("),
    ]


def uuid_object_text(sa: Any) -> Any:
    """36-character text in the database, but a uuid.UUID in Python: not the str of the SQLite mapping."""
    import uuid

    class UUIDText(sa.types.TypeDecorator):  # type: ignore[misc]
        impl = sa.String(36)
        cache_ok = True

        def process_result_value(self, value: str | None, dialect: object) -> uuid.UUID | None:
            return None if value is None else uuid.UUID(value)

    return UUIDText()


@pytest.mark.parametrize(("case_id", "name", "broken", "message"), broken_mappings(), ids=lambda v: str(v)[:12])
def test_portability_cases_fail_for_a_broken_mapping_on_sqlite(
    case_id: str,
    name: str,
    broken: Callable[..., Any],
    message: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(profiles, name, broken)
    with Database.from_url(f"sqlite:///{tmp_path / 'p.db'}") as database, pytest.raises(AssertionError, match=message):
        portability(case_id).check(database)


# Review M-2: the cases create fresh names, never reuse or drop a table they did not create.


def user_tables(database: Database) -> list[tuple[str, int]]:
    names = [r[0] for r in database.fetch_all("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]
    return [(n, database.fetch_scalar(f'SELECT count(*) FROM "{n}"')) for n in names]  # noqa: S608 (own names)


def test_portability_cases_leave_tables_with_their_base_names_alone(tmp_path: Path) -> None:
    with Database.from_url(f"sqlite:///{tmp_path / 'p.db'}") as database:
        for name in ("tabrion_d05", "tabrion_d08_child", "tabrion_d08_parent"):
            database.execute(f"CREATE TABLE {name} (id INTEGER PRIMARY KEY, note TEXT)")
            database.execute(f"INSERT INTO {name} (id, note) VALUES (1, 'user data')")  # noqa: S608
        before = user_tables(database)
        for portability_case in portability_cases():
            portability_case.check(database)
        assert user_tables(database) == before


@pytest.mark.parametrize(
    ("case_id", "existing", "absent"),
    [
        ("TAB-T-D05", "tabrion_d05_fixed", None),
        ("TAB-T-D08", "tabrion_d08_child_fixed", "tabrion_d08_parent_fixed"),
    ],
)
def test_a_case_fails_on_an_existing_table_and_leaves_it_alone(
    case_id: str, existing: str, absent: str | None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(profiles, "_suffix", lambda: "fixed")
    with Database.from_url(f"sqlite:///{tmp_path / 'p.db'}") as database:
        database.execute(f"CREATE TABLE {existing} (id INTEGER PRIMARY KEY, note TEXT)")
        database.execute(f"INSERT INTO {existing} (id, note) VALUES (7, 'user data')")  # noqa: S608
        with pytest.raises(AssertionError, match="empty or dedicated schema"):
            portability(case_id).check(database)
        assert user_tables(database) == [(existing, 1)]  # the other table of D08 was rolled back with the create
        assert database.fetch_one(f"SELECT id, note FROM {existing}") == (7, "user data")  # noqa: S608
        if absent is not None:
            assert absent not in [t for t, _ in user_tables(database)]


def test_two_calls_use_different_table_names(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: list[str] = []
    original = profiles._suffix

    def record() -> str:
        seen.append(original())
        return seen[-1]

    monkeypatch.setattr(profiles, "_suffix", record)
    with Database.from_url(f"sqlite:///{tmp_path / 'p.db'}") as database:
        portability("TAB-T-D05").check(database)
        portability("TAB-T-D05").check(database)
    assert len(seen) == 2 and seen[0] != seen[1] and all(len(s) == 8 for s in seen)


# Review L-1: the two SQLite pitfalls of specification section 6 that the SQLite mappings of D01 and D02 avoid. These
# pin the reason for those mappings (deviation D-63); if SQLAlchemy ever fixes them, the mappings can be revisited.


def test_sqlite_pitfall_datetime_with_timezone_comes_back_naive(tmp_path: Path) -> None:
    import datetime as dt

    import sqlalchemy as sa

    table = sa.Table("pitfall", sa.MetaData(), sa.Column("at", sa.DateTime(timezone=True)))
    aware = dt.datetime(2024, 1, 2, 3, 4, 5, tzinfo=dt.timezone(dt.timedelta(hours=2)))
    with Database.from_url(f"sqlite:///{tmp_path / 'p.db'}") as database:
        database.execute(sa.schema.CreateTable(table))
        database.execute(table.insert(), {"at": aware})
        back = database.fetch_scalar(sa.select(table.c.at))
        raw = database.fetch_scalar("SELECT at FROM pitfall")
    assert back.tzinfo is None  # the offset is gone, and the wall time is not converted to UTC either
    assert raw == "2024-01-02 03:04:05.000000"  # space, no Z: not the ISO 8601 UTC text of the rule


def test_sqlite_pitfall_numeric_goes_through_float(tmp_path: Path) -> None:
    from decimal import Decimal
    import warnings

    import sqlalchemy as sa

    table = sa.Table("pitfall", sa.MetaData(), sa.Column("amount", sa.Numeric(20, 2, asdecimal=True)))
    exact = Decimal("123456789012345678.91")
    with Database.from_url(f"sqlite:///{tmp_path / 'p.db'}") as database, warnings.catch_warnings():
        warnings.simplefilter("ignore")  # SQLAlchemy warns that SQLite does not support Decimal natively
        database.execute(sa.schema.CreateTable(table))
        database.execute(table.insert(), {"amount": exact})
        back = database.fetch_scalar(sa.select(table.c.amount))
    assert back != exact
