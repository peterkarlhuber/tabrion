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
import shutil
import threading
import time
from typing import Any

import pytest

pytest.importorskip("parts", reason="examples/ is not in the sdist")
pytest.importorskip("alembic")

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from parts import downgrade, metadata, open_database, require_current, status, upgrade
import parts.db
from parts.db import SCRIPT_LOCATION
import parts.schema as schema
from parts.types import ExactDecimal
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
            "primary_key": (table.primary_key.name, sorted(column.name for column in table.primary_key.columns)),
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
            "primary_key": (
                inspector.get_pk_constraint(name)["name"],
                sorted(inspector.get_pk_constraint(name)["constrained_columns"]),
            ),
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
    with pytest.raises(SchemaNotCurrent):
        require_current(db)
    upgrade(db)
    assert require_current(db).is_current


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


def test_decimals_order_and_compare_alike_on_both_engines(db: Database) -> None:
    """Review M-1 of #22: ``ORDER BY``, a comparison with a bound ``Decimal``, ``min``, ``max`` and ``sum`` give the
    PostgreSQL answer on SQLite too (a scaled integer there, not text)."""
    upgrade(db)
    numbers = [Decimal(9), Decimal(10), Decimal(-1), Decimal("100.5"), Decimal("0.25")]
    ids = [_insert_part(db, f"P-{index}")[1] for index in range(len(numbers))]
    value = schema.property_.c.value
    with db.transaction() as tx:
        for part_id, number in zip(ids, numbers, strict=True):
            tx.connection.execute(sa.insert(schema.property_).values(part_id=part_id, key="k", value=number, unit="u"))
    with db.read() as tx:
        ordered = tx.connection.execute(sa.select(value).order_by(value)).scalars().all()
        above = tx.connection.execute(sa.select(value).where(value > Decimal(5)).order_by(value)).scalars().all()
        low, high, total = tx.connection.execute(
            sa.select(sa.func.min(value), sa.func.max(value), sa.func.sum(value))
        ).one()
    assert ordered == sorted(numbers)
    assert above == [Decimal(9), Decimal(10), Decimal("100.5")]
    assert (low, high, total) == (Decimal(-1), Decimal("100.5"), sum(numbers))
    assert all(isinstance(number, Decimal) for number in (*ordered, low, high, total))


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


def test_alembic_command_line_configuration(tmp_path: Path) -> None:
    """``alembic -c examples/parts/alembic.ini upgrade head`` runs ``env.py`` without a passed connection."""
    config = Config(str(EXAMPLES / "parts" / "alembic.ini"))
    url = f"sqlite:///{tmp_path / 'cli.db'}"
    config.set_main_option("sqlalchemy.url", url)
    assert Path(config.get_main_option("script_location") or "").resolve() == SCRIPT_LOCATION
    command.upgrade(config, "head")
    with open_database(url) as database:
        assert require_current(database).is_current


def test_timestamps_order_and_round_trip_before_year_1000(db: Database) -> None:
    """Review L-4 of #22: the year has four digits on SQLite (``0999``), so the value reads back and orders."""
    upgrade(db)
    early = datetime(999, 1, 2, 3, 4, 5, 6, tzinfo=UTC)
    earliest = datetime(1, 1, 1, tzinfo=UTC)
    with db.transaction() as tx:
        for code, moment in (("A", NOW), ("B", early), ("C", earliest)):
            tx.connection.execute(sa.insert(schema.material).values(code=code, name="x", created_at=moment))
    created = schema.material.c.created_at
    with db.read() as tx:
        ordered = tx.connection.execute(sa.select(created).order_by(created)).scalars().all()
        before = tx.connection.execute(sa.select(schema.material.c.code).where(created < NOW)).scalars().all()
    assert ordered == [earliest, early, NOW]
    assert sorted(before) == ["B", "C"]


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


_FAILING_REVISION = """
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("t_partial", sa.Column("x", sa.Integer))
    raise RuntimeError("revision 0002 fails")


def downgrade() -> None:
    op.drop_table("t_partial")
"""


def test_a_failing_upgrade_leaves_nothing_behind(db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review L-1 of #22: ``upgrade`` from base with a failing second revision is one transaction: no table, no
    version row, and the pool's connection is idle afterwards (T-NOTX)."""
    scripts = tmp_path / "migrations"
    shutil.copytree(SCRIPT_LOCATION, scripts, ignore=shutil.ignore_patterns("__pycache__"))
    (scripts / "versions" / "0002_fails.py").write_text(_FAILING_REVISION, encoding="utf-8")
    monkeypatch.setattr(parts.db, "SCRIPT_LOCATION", scripts)
    with pytest.raises(RuntimeError, match="0002 fails"):
        upgrade(db)
    assert describe_database(db) == {}
    assert sa.inspect(db.engine).get_table_names() == []
    raw = db.engine.raw_connection()
    try:
        driver = raw.driver_connection
        if db.dialect == "sqlite":
            assert driver.in_transaction is False  # type: ignore[union-attr]
        else:
            import psycopg

            assert driver.info.transaction_status == psycopg.pq.TransactionStatus.IDLE  # type: ignore[union-attr]
    finally:
        raw.close()


def _cli_upgrade(url: str) -> None:
    config = Config(str(EXAMPLES / "parts" / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")


@pytest.mark.parametrize("operation", ["upgrade", "downgrade", "command line"])
def test_migrations_wait_for_a_concurrent_writer_on_sqlite(tmp_path: Path, operation: str) -> None:
    """Review M-2 and N-3 of #22: ``upgrade``, ``downgrade`` and the ``env.py`` command line branch run in
    ``Database.transaction()`` (``BEGIN IMMEDIATE``), so each waits for a writer that holds the database instead of
    failing on a stale read snapshot."""
    url = f"sqlite:///{tmp_path / 'busy.db'}"
    with open_database(url) as other, open_database(url) as database:
        with other.transaction() as tx:
            tx.connection.exec_driver_sql("CREATE TABLE t_other (x INTEGER)")
        if operation == "downgrade":
            upgrade(database)
        holding = threading.Event()

        def hold() -> None:
            with other.transaction() as tx:
                tx.connection.exec_driver_sql("INSERT INTO t_other VALUES (1)")
                holding.set()
                time.sleep(0.5)

        writer = threading.Thread(target=hold)
        writer.start()
        try:
            assert holding.wait(5)
            if operation == "upgrade":
                upgrade(database)
            elif operation == "downgrade":
                downgrade(database, "base")
            else:
                _cli_upgrade(url)
        finally:
            writer.join()
        assert status(database).current == (() if operation == "downgrade" else ("0001",))
