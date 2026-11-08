"""``examples/parts`` (RA-A-1, RA-A-2): the parts application on Tabrion, on SQLite and PostgreSQL.

``TestParts`` runs the shared suite (``parts_suite.PartsSuite``: migrations, the start-up gate, the schema, the
portable types, the catalog functions and the lost-update test) against this variant; ``test_parts_baseline.py``
runs the same suite against the plain SQLAlchemy baseline (RA-A-3). The module-level tests here use what only
Tabrion offers: the read-only profile, the classified errors (``IntegrityViolation``, ``UsageError``) and the
PostgreSQL session settings.

Every test runs on both engines; the PostgreSQL case needs ``TABRION_TEST_POSTGRES`` (marker ``postgres``) and works
in a schema ``t_<hex>`` of its own (the ``search_path`` of the profile), dropped afterwards.
"""

# The SQL is built from names this file generates and fixed text, never from outside input.

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("parts", reason="examples/ is not in the sdist")
pytest.importorskip("alembic")

from parts import metadata, open_database, require_current, upgrade
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
from .parts_suite import App, PartsSuite

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


def _insert_part(database: Database, number: str = "P-1", status: str | None = "draft") -> tuple[int, int]:
    """A material and a part; ``status=None`` leaves the status to the server default."""
    extra = {} if status is None else {"status": status}
    with database.transaction() as tx:
        material_id = tx.connection.execute(
            sa.insert(schema.material)
            .values(code=f"M-{number}", name="Steel", created_at=NOW)
            .returning(schema.material.c.id)
        ).scalar_one()
        part_id = tx.connection.execute(
            sa.insert(schema.part)
            .values(
                part_number=number,
                name="Bracket",
                material_id=material_id,
                **extra,
                created_at=NOW,
                updated_at=NOW,
            )
            .returning(schema.part.c.id)
        ).scalar_one()
    return int(material_id), int(part_id)


def test_values_round_trip_exactly(db: Database) -> None:
    upgrade(db)
    _, part_id = _insert_part(db, status=None)
    vienna = datetime(2026, 11, 8, 13, 30, 0, 123456, tzinfo=timezone(timedelta(hours=1)))
    with db.transaction() as tx:
        tx.connection.execute(
            sa.insert(schema.property_).values(part_id=part_id, key="mass", value=Decimal("0.100001"), unit="kg")
        )
        tx.connection.execute(sa.update(schema.part).values(updated_at=vienna))
    with db.read() as tx:
        value = tx.connection.execute(sa.select(schema.property_.c.value)).scalar_one()
        row = tx.connection.execute(
            sa.select(schema.part.c.status, schema.part.c.version, schema.part.c.updated_at)
        ).one()
    assert value == Decimal("0.100001") and isinstance(value, Decimal)
    assert row.status == "draft" and row.version == 1  # the server defaults
    assert row.updated_at == NOW and row.updated_at.tzinfo is not None and row.updated_at.utcoffset() == timedelta(0)


CHECKS: dict[str, tuple[str, dict[str, Any]]] = {
    "ck_material_code": ("material", {"code": ""}),
    "ck_material_name": ("material", {"name": ""}),
    "ck_part_part_number": ("part", {"part_number": ""}),
    "ck_part_name": ("part", {"name": ""}),
    "ck_part_status": ("part", {"status": "archived"}),
    "ck_part_version": ("part", {"version": 0}),
    "ck_property_key": ("property", {"key": ""}),
    "ck_property_unit": ("property", {"unit": ""}),
}
"""A violating update per ``CHECK`` on material, part and property; ``test_relation_rules`` covers part_relation."""


def test_every_check_has_a_case() -> None:
    declared = {c.name for t in metadata.tables.values() for c in t.constraints if isinstance(c, sa.CheckConstraint)}
    assert declared == set(CHECKS) | {"ck_part_relation_quantity", "ck_part_relation_not_self"}


@pytest.mark.parametrize("check", sorted(CHECKS))
def test_constraints_hold_on_both_engines(db: Database, check: str) -> None:
    upgrade(db)
    _, part_id = _insert_part(db)
    with db.transaction() as tx:
        tx.connection.execute(
            sa.insert(schema.property_).values(part_id=part_id, key="mass", value=Decimal(1), unit="kg")
        )
    table, values = CHECKS[check]
    with pytest.raises(IntegrityViolation) as raised, db.transaction() as tx:
        tx.connection.execute(sa.update(metadata.tables[table]).values(**values))
    classification = raised.value.classification
    assert classification is not None and classification.constraint == check


def test_decimals_are_stored_scaled_on_sqlite_and_numeric_on_postgresql(db: Database) -> None:
    upgrade(db)
    _, assembly = _insert_part(db, "A-1")
    _, component = _insert_part(db, "C-1")
    with db.transaction() as tx:
        tx.connection.execute(
            sa.insert(schema.part_relation).values(
                assembly_id=assembly, component_id=component, quantity=Decimal("2.5")
            )
        )
    with db.read() as tx:
        if db.dialect == "sqlite":
            raw = tx.connection.exec_driver_sql("SELECT typeof(quantity), quantity FROM part_relation").one()
            assert tuple(raw) == ("integer", 2500)
        else:
            assert tx.connection.exec_driver_sql("SELECT quantity FROM part_relation").scalar_one() == Decimal("2.500")


def test_exact_decimal_precision_limit() -> None:
    with pytest.raises(ValueError, match="precision <= 18"):
        ExactDecimal(19, 2)


def test_relation_rules(db: Database) -> None:
    upgrade(db)
    _, assembly = _insert_part(db, "A-1")
    _, component = _insert_part(db, "C-1")
    relation = schema.part_relation
    with db.transaction() as tx:
        tx.connection.execute(
            sa.insert(relation).values(assembly_id=assembly, component_id=component, quantity=Decimal("2.5"))
        )
    for bad in (
        {"assembly_id": assembly, "component_id": assembly, "quantity": Decimal(1)},  # a part in itself
        {"assembly_id": component, "component_id": assembly, "quantity": Decimal(0)},  # no quantity
        {"assembly_id": assembly, "component_id": 999_999, "quantity": Decimal(1)},  # no such part
    ):
        with pytest.raises(IntegrityViolation), db.transaction() as tx:
            tx.connection.execute(sa.insert(relation).values(**bad))
    with db.read() as tx:
        assert tx.connection.execute(sa.select(relation.c.quantity)).scalar_one() == Decimal("2.500")


def test_types_refuse_floats_and_naive_timestamps(db: Database) -> None:
    upgrade(db)
    _, part_id = _insert_part(db)
    for column, value in (
        ("value", 0.5),  # exact in binary, refused for being a float
        ("value", 2.0),
        ("value", Decimal("0.0000001")),  # more digits after the point than the scale
        ("value", Decimal("1" * 13)),  # more digits before the point than numeric(18, 6) holds
    ):
        with pytest.raises(UsageError, match=r"database\.invalid_statement"), db.transaction() as tx:
            tx.connection.execute(
                sa.insert(schema.property_).values(part_id=part_id, key="k", unit="u", **{column: value})
            )
    with pytest.raises(UsageError, match=r"database\.invalid_statement"), db.transaction() as tx:
        tx.connection.execute(sa.update(schema.part).values(updated_at=datetime(2026, 1, 1)))


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
        _insert_part(database)
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
)


class TestParts(PartsSuite):
    @pytest.fixture
    def app(self) -> App:
        return TABRION
