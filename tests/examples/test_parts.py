"""``examples/parts`` (RA-A-1, RA-A-2): the parts application on Tabrion, on SQLite and PostgreSQL.

``TestParts`` runs the shared suite (``parts_suite.PartsSuite``: migrations, the start-up gate, the schema, the
portable types, the catalog functions and the lost-update test) against this variant; ``test_parts_baseline.py``
runs the same suite against the plain SQLAlchemy baseline (RA-A-3). The module-level tests here use what only
Tabrion offers: the read-only profile and the PostgreSQL session settings.

Every test runs on both engines; the PostgreSQL case needs ``TABRION_TEST_POSTGRES`` (marker ``postgres``) and works
in a schema ``t_<hex>`` of its own (the ``search_path`` of the profile), dropped afterwards.
"""

# The SQL is built from names this file generates and fixed text, never from outside input.

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("parts", reason="examples/ is not in the sdist")
pytest.importorskip("alembic")

from parts import open_database, require_current, upgrade
import parts.catalog
import parts.db
import parts.errors
import parts.schema as schema
from parts.types import ExactDecimal
import sqlalchemy as sa

from tabrion import Database, Profile
from tabrion.errors import IntegrityViolation, SchemaNotCurrent, UsageError
from tabrion.postgres import SessionSettings

from .conftest import EXAMPLES
from .parts_suite import App, PartsSuite, _insert_part

# Alembic 1.13 itself passes an empty parameter sequence to execute(), which SQLAlchemy 2.1 deprecates; the extras
# allow that combination (as in tests/unit/test_migrations.py, re-review DL-2 of #14). Only that warning is ignored.
pytestmark = pytest.mark.filterwarnings(
    "ignore:Empty parameter sequence passed to execute:sqlalchemy.exc.SADeprecationWarning"
)

NOW = datetime(2026, 11, 8, 12, 30, 0, 123456, tzinfo=UTC)


@pytest.fixture(params=["sqlite", pytest.param("postgresql", marks=pytest.mark.postgres)])
def url_and_profile(request: pytest.FixtureRequest, tmp_path: Path) -> tuple[Any, Profile]:
    """SQLite in a temporary file, or PostgreSQL in a fresh schema of ``tabrion.testing``'s
    ``tabrion_postgres_schema`` (the profile's ``search_path``; dropped afterwards)."""
    if request.param == "sqlite":
        return f"sqlite:///{tmp_path / 'parts.db'}", Profile()
    pytest.importorskip("psycopg")
    from tests.postgres.test_database import sa_url

    postgres_schema = request.getfixturevalue("tabrion_postgres_schema")
    admin = request.getfixturevalue("tabrion_postgres_url")
    return sa_url(admin), Profile(postgres=SessionSettings(search_path=postgres_schema.name))


@pytest.fixture
def db(url_and_profile: tuple[Any, Profile]) -> Iterator[Database]:
    url, profile = url_and_profile
    database = open_database(url, profile=profile)
    try:
        yield database
    finally:
        database.close()


def test_require_current_under_a_read_only_profile(url_and_profile: tuple[Any, Profile]) -> None:
    url, profile = url_and_profile
    with open_database(url, profile=profile) as writable:
        upgrade(writable)
    readonly = Profile(read_only=True, postgres=profile.postgres)
    with open_database(url, profile=readonly) as database:
        assert require_current(database).is_current


def test_exact_decimal_precision_limit() -> None:
    with pytest.raises(ValueError, match="precision <= 18"):
        ExactDecimal(19, 2)


@pytest.mark.postgres
def test_postgresql_results_are_utc_under_another_session_timezone(request: pytest.FixtureRequest) -> None:
    """Review L-1 (T10) of #22: a session in Europe/Vienna still reads ``tzinfo=UTC`` values."""
    pytest.importorskip("psycopg")
    from tests.postgres.test_database import sa_url

    postgres_schema = request.getfixturevalue("tabrion_postgres_schema")
    url = sa_url(request.getfixturevalue("tabrion_postgres_url"))
    vienna = Profile(postgres=SessionSettings(search_path=postgres_schema.name, timezone="Europe/Vienna"))
    with open_database(url, profile=vienna) as database:
        upgrade(database)
        _insert_part(TABRION, database)
        with database.read() as tx:
            moment = tx.connection.execute(sa.select(schema.part.c.created_at)).scalar_one()
    assert moment == NOW and moment.tzinfo is UTC


# --- the shared suite -------------------------------------------------------------------------------------------


def _open(url: Any, search_path: str | None) -> Database:
    profile = Profile() if search_path is None else Profile(postgres=SessionSettings(search_path=search_path))
    return open_database(url, profile=profile)


@contextmanager
def _write(database: Database) -> Iterator[sa.Connection]:
    with database.transaction() as transaction:
        yield transaction.connection


@contextmanager
def _read(database: Database) -> Iterator[sa.Connection]:
    with database.read() as transaction:
        yield transaction.connection


def _constraint_name(error: BaseException) -> str | None:
    classification = getattr(error, "classification", None)
    return None if classification is None else classification.constraint


TABRION = App(
    name="parts (Tabrion)",
    directory=EXAMPLES / "parts",
    db=parts.db,
    catalog=parts.catalog,
    errors=parts.errors,
    schema=schema,
    open=_open,
    close=Database.close,
    engine=lambda database: database.engine,
    write=_write,
    read=_read,
    not_current=SchemaNotCurrent,
    integrity_error=IntegrityViolation,
    usage_error=UsageError,
    constraint_name=_constraint_name,
)


class TestParts(PartsSuite):
    @pytest.fixture
    def app(self) -> App:
        return TABRION
