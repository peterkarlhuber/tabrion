"""RA-A-1, the parts schema with Alembic (``examples/parts``), planning document 09 acceptance: the schema (part,
material, property, status, relation) migrates through Alembic identically on SQLite and PostgreSQL, and
``require_current`` is green afterwards.

Every test runs on both engines; the PostgreSQL case needs ``TABRION_TEST_POSTGRES`` (marker ``postgres``) and works
in a schema ``t_<hex>`` of its own (the ``search_path`` of the profile), dropped afterwards.
"""

# The SQL is built from names this file generates and fixed text, never from outside input.

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("parts", reason="examples/ is not in the sdist")
pytest.importorskip("alembic")

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from parts import downgrade, metadata, open_database, require_current, status, upgrade
from parts.db import SCRIPT_LOCATION
import parts.schema as schema
import sqlalchemy as sa

from tabrion import Database, Profile
from tabrion.errors import IntegrityViolation, SchemaNotCurrent, UsageError
from tabrion.postgres import SessionSettings

from .conftest import EXAMPLES

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


def describe_metadata() -> dict[str, Any]:
    """What the schema declares, in the shape ``describe_database`` reads back."""
    tables: dict[str, Any] = {}
    for table in metadata.sorted_tables:
        tables[table.name] = {
            "columns": [(column.name, column.nullable) for column in table.columns],
            "primary_key": sorted(column.name for column in table.primary_key.columns),
            "foreign_keys": sorted(
                (fk.name, tuple(fk.column_keys), fk.referred_table.name, tuple(e.column.name for e in fk.elements))
                for fk in table.foreign_key_constraints
            ),
            "unique": sorted(c.name for c in table.constraints if isinstance(c, sa.UniqueConstraint)),
            "checks": sorted(c.name for c in table.constraints if isinstance(c, sa.CheckConstraint)),
            "indexes": sorted((index.name, tuple(c.name for c in index.columns)) for index in table.indexes),
        }
    return tables


def describe_database(database: Database) -> dict[str, Any]:
    inspector = sa.inspect(database.engine)
    tables: dict[str, Any] = {}
    for name in sorted(inspector.get_table_names()):
        if name == "alembic_version":
            continue
        columns = inspector.get_columns(name)
        tables[name] = {
            "columns": [(column["name"], column["nullable"]) for column in columns],
            "primary_key": sorted(inspector.get_pk_constraint(name)["constrained_columns"]),
            "foreign_keys": sorted(
                (fk["name"], tuple(fk["constrained_columns"]), fk["referred_table"], tuple(fk["referred_columns"]))
                for fk in inspector.get_foreign_keys(name)
            ),
            "unique": sorted(str(c["name"]) for c in inspector.get_unique_constraints(name)),
            "checks": sorted(str(c["name"]) for c in inspector.get_check_constraints(name)),
            "indexes": sorted(
                (index["name"], tuple(str(c) for c in index["column_names"]))
                for index in inspector.get_indexes(name)
                if not index.get("duplicates_constraint")
            ),
        }
    return tables


def test_fresh_database_is_not_current_until_upgraded(db: Database) -> None:
    before = status(db)
    assert before.current == () and before.pending == ("0001",) and not before.is_current
    with pytest.raises(SchemaNotCurrent):
        require_current(db)
    upgrade(db)
    after = require_current(db)
    assert after.current == after.heads == ("0001",) and after.pending == () and after.is_current


def test_migrations_create_exactly_the_declared_schema(db: Database) -> None:
    """The same schema on both engines: the reflected tables, columns, keys, constraints and indexes are what
    ``parts.schema`` declares, and Alembic's own comparison (types included) finds no difference."""
    upgrade(db)
    assert describe_database(db) == describe_metadata()
    with db.engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        assert [diff for diff in compare_metadata(context, metadata) if not _identity_on_sqlite(db, diff)] == []


def _identity_on_sqlite(database: Database, diff: Any) -> bool:
    """Alembic 1.13 reports ``Identity`` as a changed default on SQLite, which has no identity columns (the
    ``id`` is the ``rowid`` alias there, TAB-T-D03); newer Alembic reports nothing. Only that entry is skipped."""
    if database.dialect != "sqlite" or not isinstance(diff, list) or len(diff) != 1:
        return False
    kind, _, _table, column, _, old, new = diff[0]
    return bool(kind == "modify_default" and column == "id" and old is None and isinstance(new, sa.Identity))


def test_downgrade_and_upgrade_again(db: Database) -> None:
    upgrade(db)
    downgrade(db, "base")
    assert describe_database(db) == {}
    assert status(db).current == ()
    upgrade(db)
    assert require_current(db).is_current


def test_require_current_under_a_read_only_profile(url_and_profile: tuple[Any, Profile]) -> None:
    url, profile = url_and_profile
    with open_database(url, profile=profile) as writable:
        upgrade(writable)
    readonly = Profile(read_only=True, postgres=profile.postgres)
    with open_database(url, profile=readonly) as database:
        assert require_current(database).is_current


def _insert_part(database: Database, number: str = "P-1", status: str = "draft") -> tuple[int, int]:
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
                status=status,
                created_at=NOW,
                updated_at=NOW,
            )
            .returning(schema.part.c.id)
        ).scalar_one()
    return int(material_id), int(part_id)


def test_values_round_trip_exactly(db: Database) -> None:
    upgrade(db)
    _, part_id = _insert_part(db)
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


@pytest.mark.parametrize(
    ("table", "values"),
    [
        ("part", {"status": "archived"}),
        ("part", {"part_number": ""}),
        ("part", {"version": 0}),
    ],
)
def test_constraints_hold_on_both_engines(db: Database, table: str, values: dict[str, Any]) -> None:
    upgrade(db)
    _insert_part(db)
    with pytest.raises(IntegrityViolation), db.transaction() as tx:
        tx.connection.execute(sa.update(schema.part).values(**values))


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
    for column, value in (("value", 0.1), ("value", Decimal("0.0000001"))):
        with pytest.raises(UsageError, match=r"database\.invalid_statement"), db.transaction() as tx:
            tx.connection.execute(
                sa.insert(schema.property_).values(part_id=part_id, key="k", unit="u", **{column: value})
            )
    with pytest.raises(UsageError, match=r"database\.invalid_statement"), db.transaction() as tx:
        tx.connection.execute(sa.update(schema.part).values(updated_at=datetime(2026, 1, 1)))


def test_alembic_command_line_configuration(tmp_path: Path) -> None:
    """``alembic -c examples/parts/alembic.ini upgrade head`` runs ``env.py`` without a passed connection."""
    config = Config(str(EXAMPLES / "parts" / "alembic.ini"))
    url = f"sqlite:///{tmp_path / 'cli.db'}"
    config.set_main_option("sqlalchemy.url", url)
    assert Path(config.get_main_option("script_location") or "").resolve() == SCRIPT_LOCATION
    command.upgrade(config, "head")
    with open_database(url) as database:
        assert require_current(database).is_current
