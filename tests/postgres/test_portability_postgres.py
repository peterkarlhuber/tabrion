"""``tabrion.testing.profiles.portability_cases()`` on PostgreSQL (TAB-021, TAB-T-D01..D09 on the second engine).

Each case runs on a ``Database`` whose ``search_path`` is the ``schema`` fixture (``t_<hex>``, dropped afterwards),
so its tables never meet another run's.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest

pytestmark = pytest.mark.postgres

pytest.importorskip("sqlalchemy")
pytest.importorskip("psycopg")

from tabrion import Database, Profile  # noqa: E402
from tabrion.postgres import SessionSettings  # noqa: E402
from tabrion.testing import profiles  # noqa: E402
from tabrion.testing.profiles import PortabilityCase, portability_cases  # noqa: E402
from tests.postgres.test_database import sa_url  # noqa: E402


@pytest.fixture
def db(admin_url: str, schema: str) -> Iterator[Database]:
    profile = Profile(pool_size=1, postgres=SessionSettings(search_path=schema))
    database = Database.from_url(sa_url(admin_url), profile=profile)
    try:
        yield database
    finally:
        database.close()


@pytest.mark.parametrize("portability_case", portability_cases(), ids=lambda c: c.id)
def test_portability_cases_pass_on_postgres(portability_case: PortabilityCase, db: Database, schema: str) -> None:
    portability_case.check(db)
    left = db.fetch_scalar(
        "SELECT count(*) FROM pg_catalog.pg_tables WHERE schemaname = :s AND tablename LIKE 'tabrion_d%'", {"s": schema}
    )
    assert left == 0  # each case drops its tables


def portability(case_id: str) -> PortabilityCase:
    return next(c for c in portability_cases() if c.id == case_id)


@pytest.fixture
def foreign(admin_url: str, schema: str) -> Iterator[Callable[[str], Database]]:
    """A Database on a plain engine whose sessions run with the given server options (no Tabrion listeners)."""
    import sqlalchemy

    engines: list[sqlalchemy.Engine] = []

    def make(options: str) -> Database:
        url = sa_url(admin_url).set(drivername="postgresql+psycopg")
        engine = sqlalchemy.create_engine(url, connect_args={"options": f"-c search_path={schema} {options}"})
        engines.append(engine)
        return Database.from_engine(engine, install_listeners=False)

    yield make
    for engine in engines:
        engine.dispose()


# Review M-3: each PostgreSQL check fails for a misconfigured server session or a broken mapping.


@pytest.mark.parametrize(
    ("case_id", "options", "message"),
    [
        ("TAB-T-D01", "-c TimeZone=Europe/Vienna", "TimeZone is not UTC"),
        ("TAB-T-D01", "-c TimeZone=America/New_York", "TimeZone is not UTC"),
        ("TAB-T-D09", "-c extra_float_digits=-3", r"0\.1"),
    ],
)
def test_portability_cases_fail_for_a_misconfigured_session(
    case_id: str, options: str, message: str, foreign: Callable[[str], Database]
) -> None:
    with foreign(options) as database, pytest.raises(AssertionError, match=message):
        portability(case_id).check(database)


def test_a_correct_foreign_session_passes(foreign: Callable[[str], Database]) -> None:
    with foreign("-c TimeZone=UTC") as database:
        for portability_case in portability_cases():
            portability_case.check(database)


def broken_mappings() -> list[tuple[str, str, Callable[..., Any], str]]:
    from sqlalchemy.dialects import postgresql

    return [
        ("TAB-T-D02", "_d02_type", lambda sa, sqlite: sa.Double(), "is not exactly 0.30"),
        ("TAB-T-D03", "_d03_id", lambda sa: (sa.Integer(), sa.Identity(always=True)), "integer, not bigint"),
        ("TAB-T-D03", "_d03_id", lambda sa: (sa.BigInteger(), sa.Identity()), "not GENERATED ALWAYS"),
        ("TAB-T-D04", "_d04_type", lambda sa, sqlite: sa.Uuid(as_uuid=True, native_uuid=False), "not uuid"),
        ("TAB-T-D07", "_d07_type", lambda sa: postgresql.JSON(), "json, not jsonb"),
        # numeric read back as float: the largest double comes back as inf
        ("TAB-T-D09", "_d09_type", lambda sa: sa.Numeric(asdecimal=False), r"1e-300, inf\]"),
    ]


@pytest.mark.parametrize(("case_id", "name", "broken", "message"), broken_mappings(), ids=lambda v: str(v)[:12])
def test_portability_cases_fail_for_a_broken_mapping_on_postgres(
    case_id: str, name: str, broken: Callable[..., Any], message: str, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(profiles, name, broken)
    with pytest.raises(AssertionError, match=message):
        portability(case_id).check(db)


# Review M-2: tables the cases did not create survive, also when their name collides.


def tables(db: Database, schema: str) -> list[tuple[str, int]]:
    names = [
        r[0]
        for r in db.fetch_all(
            "SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname = :s ORDER BY tablename", {"s": schema}
        )
    ]
    return [(n, db.fetch_scalar(f'SELECT count(*) FROM "{n}"')) for n in names]  # noqa: S608 (own names)


def test_tables_with_the_base_names_survive(db: Database, schema: str) -> None:
    for name in ("tabrion_d05", "tabrion_d08_child", "tabrion_d08_parent"):
        db.execute(f"CREATE TABLE {name} (id int PRIMARY KEY, note text)")
        db.execute(f"INSERT INTO {name} VALUES (1, 'user data')")  # noqa: S608
    before = tables(db, schema)
    for portability_case in portability_cases():
        portability_case.check(db)
    assert tables(db, schema) == before


@pytest.mark.parametrize(
    ("case_id", "existing"), [("TAB-T-D05", "tabrion_d05_fixed"), ("TAB-T-D08", "tabrion_d08_child_fixed")]
)
def test_a_case_fails_on_an_existing_table_and_leaves_it_alone(
    case_id: str, existing: str, db: Database, schema: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(profiles, "_suffix", lambda: "fixed")
    db.execute(f"CREATE TABLE {existing} (id int PRIMARY KEY, note text)")
    db.execute(f"INSERT INTO {existing} VALUES (7, 'user data')")  # noqa: S608
    before = tables(db, schema)
    with pytest.raises(AssertionError, match="empty or dedicated schema"):
        portability(case_id).check(db)
    assert tables(db, schema) == before  # D08's parent table was rolled back with the failed create
