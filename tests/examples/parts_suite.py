"""The parts test suite shared by both variants (RA-A-3): ``examples/parts`` (Tabrion) and ``examples/parts-baseline``
(plain SQLAlchemy).

``PartsSuite`` holds the tests that use only what both variants offer: the migrations and the start-up gate, the
schema, the portable types, and the catalog functions. ``test_parts.py`` and ``test_parts_baseline.py`` each
subclass it with an ``app`` fixture, an ``App`` that says how to open, read and write in that variant. Every test
runs on SQLite and on PostgreSQL (marker ``postgres``, a schema ``t_<hex>`` of ``tabrion.testing``'s
``tabrion_postgres_schema``).

Where a variant behaves differently, ``App.deviations`` names the test and why: the test then runs as a strict
``xfail``, so the deviation stays a measured fact for RA-EVAL (planning document 09 section 7) and is reported
again the day it disappears.
"""

# The SQL is built from names this file generates and fixed text, never from outside input.

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
import contextlib
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal, localcontext
import itertools
from pathlib import Path
import shutil
import threading
import time
from types import ModuleType
from typing import Any

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
import pytest
import sqlalchemy as sa

NOW = datetime(2026, 11, 8, 12, 30, 0, 123456, tzinfo=UTC)


@dataclass(frozen=True)
class App:
    """One variant of the parts application, as the suite drives it."""

    name: str
    directory: Path
    """``examples/<name>``, with ``alembic.ini``."""
    db: ModuleType
    """Holds ``upgrade``, ``downgrade``, ``status``, ``require_current`` and ``SCRIPT_LOCATION``."""
    catalog: ModuleType
    errors: ModuleType
    schema: ModuleType
    open: Callable[[Any, str | None], Any]
    """``open(url, search_path)``: the application's handle (a ``Database`` or an ``Engine``)."""
    close: Callable[[Any], None]
    engine: Callable[[Any], sa.Engine]
    write: Callable[[Any], AbstractContextManager[sa.Connection]]
    read: Callable[[Any], AbstractContextManager[sa.Connection]]
    not_current: type[Exception]
    integrity_error: type[Exception]
    """What a violated constraint raises."""
    refused_value: Callable[[BaseException], bool]
    """Whether an error is the variant's refusal of a value the column type does not accept."""
    constraint_name: Callable[[BaseException], str | None]
    """The name of the constraint an ``integrity_error`` names, if the variant tells it."""
    deviations: Mapping[str, str] = field(default_factory=dict)
    """Test name (``test_x`` or ``test_x[sqlite]``) to the reason it fails for this variant."""


def describe_metadata(metadata: sa.MetaData) -> dict[str, Any]:
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


def describe_database(engine: sa.Engine) -> dict[str, Any]:
    inspector = sa.inspect(engine)
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


def _identity_on_sqlite(engine: sa.Engine, diff: Any) -> bool:
    """Alembic 1.13 reports ``Identity`` as a changed default on SQLite, which has no identity columns (the
    ``id`` is the ``rowid`` alias there, TAB-T-D03); newer Alembic reports nothing. Only that entry is skipped."""
    if engine.dialect.name != "sqlite" or not isinstance(diff, list) or len(diff) != 1:
        return False
    kind, _, _table, column, _, old, new = diff[0]
    return bool(kind == "modify_default" and column == "id" and old is None and isinstance(new, sa.Identity))


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


def _counter_threads(step: Callable[[], None], workers: int = 5) -> None:
    errors: list[BaseException] = []

    def run() -> None:
        try:
            step()
        except BaseException as exc:  # reported below, so a failing thread fails the test
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not errors, errors
    assert not any(thread.is_alive() for thread in threads)


def _pause_after_the_cycle_check(app: App, monkeypatch: pytest.MonkeyPatch, parties: int) -> None:
    """Every ``add_component`` waits (up to 1 s) after its cycle check until ``parties`` of them got there, so
    without serialization all of them check before any inserts. A serialized one times out and goes on."""
    barrier = threading.Barrier(parties)
    descendants = app.catalog._descendants

    def paused(connection: sa.Connection, part_id: int) -> set[int]:
        found: set[int] = descendants(connection, part_id)
        with contextlib.suppress(threading.BrokenBarrierError):
            barrier.wait(timeout=1)
        return found

    monkeypatch.setattr(app.catalog, "_descendants", paused)


def _chain(app: App, db: Any, numbers: list[str], quantity: Decimal) -> None:
    """Parts ``numbers[0] -> numbers[1] -> ...``, written in one transaction (the test's setup, not the API)."""
    with app.write(db) as connection:
        material_id = connection.execute(sa.select(app.schema.material.c.id)).scalar_one()
        ids = []
        for number in numbers:
            ids.append(
                connection.execute(
                    sa.insert(app.schema.part)
                    .values(part_number=number, name=number, material_id=material_id, created_at=NOW, updated_at=NOW)
                    .returning(app.schema.part.c.id)
                ).scalar_one()
            )
        connection.execute(
            sa.insert(app.schema.part_relation),
            [{"assembly_id": a, "component_id": c, "quantity": quantity} for a, c in itertools.pairwise(ids)],
        )


def _insert_part(app: App, database: Any, number: str = "P-1", status: str | None = "draft") -> tuple[int, int]:
    """A material and a part; ``status=None`` leaves the status to the server default."""
    extra = {} if status is None else {"status": status}
    with app.write(database) as connection:
        material_id = connection.execute(
            sa.insert(app.schema.material)
            .values(code=f"M-{number}", name="Steel", created_at=NOW)
            .returning(app.schema.material.c.id)
        ).scalar_one()
        part_id = connection.execute(
            sa.insert(app.schema.part)
            .values(
                part_number=number,
                name="Bracket",
                material_id=material_id,
                **extra,
                created_at=NOW,
                updated_at=NOW,
            )
            .returning(app.schema.part.c.id)
        ).scalar_one()
    return int(material_id), int(part_id)


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


class PartsSuite:
    """The shared tests; a subclass named ``Test...`` provides the ``app`` fixture."""

    # --- fixtures -----------------------------------------------------------------------------------------------

    @pytest.fixture(autouse=True)
    def _deviation(self, request: pytest.FixtureRequest, app: App) -> None:
        reason = app.deviations.get(request.node.name) or app.deviations.get(request.node.originalname)
        if reason:
            request.applymarker(pytest.mark.xfail(strict=True, reason=f"{app.name}: {reason}"))

    @pytest.fixture(params=["sqlite", pytest.param("postgresql", marks=pytest.mark.postgres)])
    def target(self, request: pytest.FixtureRequest, tmp_path: Path) -> tuple[Any, str | None]:
        """``(url, search_path)``: SQLite in a temporary file, or PostgreSQL in a fresh schema of
        ``tabrion.testing``'s ``tabrion_postgres_schema`` (dropped afterwards)."""
        if request.param == "sqlite":
            return f"sqlite:///{tmp_path / 'parts.db'}", None
        pytest.importorskip("psycopg")
        from tests.postgres.test_database import sa_url

        postgres_schema = request.getfixturevalue("tabrion_postgres_schema")
        return sa_url(request.getfixturevalue("tabrion_postgres_url")), str(postgres_schema.name)

    @pytest.fixture
    def db(self, app: App, target: tuple[Any, str | None]) -> Iterator[Any]:
        handle = app.open(*target)
        try:
            yield handle
        finally:
            app.close(handle)

    @pytest.fixture
    def catalog_db(self, app: App, db: Any) -> Any:
        app.db.upgrade(db)
        app.catalog.create_material(db, "STEEL", "Steel")
        return db

    # --- migrations and the start-up gate (RA-A-1) ----------------------------------------------------------------

    def test_fresh_database_is_not_current_until_upgraded(self, app: App, db: Any) -> None:
        before = app.db.status(db)
        assert before.current == () and before.pending == ("0001",) and not before.is_current
        with pytest.raises(app.not_current):
            app.db.require_current(db)
        app.db.upgrade(db)
        after = app.db.require_current(db)
        assert after.current == after.heads == ("0001",) and after.pending == () and after.is_current

    def test_migrations_create_exactly_the_declared_schema(self, app: App, db: Any) -> None:
        """The same schema on both engines: the reflected tables, columns, keys, constraints and indexes are what
        the schema declares, and Alembic's own comparison (types included) finds no difference."""
        app.db.upgrade(db)
        engine = app.engine(db)
        assert describe_database(engine) == describe_metadata(app.schema.metadata)
        with engine.connect() as connection:
            context = MigrationContext.configure(connection, opts={"compare_type": True})
            diffs = compare_metadata(context, app.schema.metadata)
            assert [diff for diff in diffs if not _identity_on_sqlite(engine, diff)] == []

    def test_downgrade_and_upgrade_again(self, app: App, db: Any) -> None:
        app.db.upgrade(db)
        app.db.downgrade(db, "base")
        assert describe_database(app.engine(db)) == {}
        assert app.db.status(db).current == ()
        with pytest.raises(app.not_current):
            app.db.require_current(db)
        app.db.upgrade(db)
        assert app.db.require_current(db).is_current

    def test_alembic_command_line_configuration(self, app: App, tmp_path: Path) -> None:
        """``alembic -c examples/<app>/alembic.ini upgrade head`` runs ``env.py`` without a passed connection."""
        config = Config(str(app.directory / "alembic.ini"))
        url = f"sqlite:///{tmp_path / 'cli.db'}"
        config.set_main_option("sqlalchemy.url", url)
        assert Path(config.get_main_option("script_location") or "").resolve() == app.db.SCRIPT_LOCATION
        command.upgrade(config, "head")
        handle = app.open(url, None)
        try:
            assert app.db.require_current(handle).is_current
        finally:
            app.close(handle)

    def test_a_failing_upgrade_leaves_nothing_behind(
        self, app: App, db: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Review L-1 of #22: ``upgrade`` from base with a failing second revision is one transaction: no table, no
        version row, and the pool's connection is idle afterwards (T-NOTX)."""
        scripts = tmp_path / "migrations"
        shutil.copytree(app.db.SCRIPT_LOCATION, scripts, ignore=shutil.ignore_patterns("__pycache__"))
        (scripts / "versions" / "0002_fails.py").write_text(_FAILING_REVISION, encoding="utf-8")
        monkeypatch.setattr(app.db, "SCRIPT_LOCATION", scripts)
        with pytest.raises(RuntimeError, match="0002 fails"):
            app.db.upgrade(db)
        engine = app.engine(db)
        assert sa.inspect(engine).get_table_names() == []
        raw = engine.raw_connection()
        try:
            driver = raw.driver_connection
            if engine.dialect.name == "sqlite":
                assert driver.in_transaction is False  # type: ignore[union-attr]
            else:
                import psycopg

                assert driver.info.transaction_status == psycopg.pq.TransactionStatus.IDLE  # type: ignore[union-attr]
        finally:
            raw.close()

    @pytest.mark.parametrize("operation", ["upgrade", "downgrade", "command line"])
    def test_migrations_wait_for_a_concurrent_writer_on_sqlite(self, app: App, tmp_path: Path, operation: str) -> None:
        """Review M-2 and N-3 of #22: ``upgrade``, ``downgrade`` and the ``env.py`` command line branch each wait
        for a writer that holds the SQLite database instead of failing on a stale read snapshot (Tabrion:
        ``BEGIN IMMEDIATE``; the baseline reads outside a transaction, see its README)."""
        url = f"sqlite:///{tmp_path / 'busy.db'}"
        other, database = app.open(url, None), app.open(url, None)
        try:
            with app.write(other) as connection:
                connection.exec_driver_sql("CREATE TABLE t_other (x INTEGER)")
            if operation == "downgrade":
                app.db.upgrade(database)
            holding = threading.Event()

            def hold() -> None:
                with app.write(other) as connection:
                    connection.exec_driver_sql("INSERT INTO t_other VALUES (1)")
                    holding.set()
                    time.sleep(0.5)

            writer = threading.Thread(target=hold)
            writer.start()
            try:
                assert holding.wait(5)
                if operation == "upgrade":
                    app.db.upgrade(database)
                elif operation == "downgrade":
                    app.db.downgrade(database, "base")
                else:
                    config = Config(str(app.directory / "alembic.ini"))
                    config.set_main_option("sqlalchemy.url", url)
                    command.upgrade(config, "head")
            finally:
                writer.join()
            assert app.db.status(database).current == (() if operation == "downgrade" else ("0001",))
        finally:
            app.close(other)
            app.close(database)

    # --- the portable types (RA-A-1) ------------------------------------------------------------------------------

    def test_decimals_order_and_compare_alike_on_both_engines(self, app: App, catalog_db: Any) -> None:
        """Review M-1 of #22: ``ORDER BY``, a comparison with a bound ``Decimal``, ``min``, ``max`` and ``sum`` give
        the PostgreSQL answer on SQLite too (a scaled integer there, not text)."""
        db = catalog_db
        numbers = [Decimal(9), Decimal(10), Decimal(-1), Decimal("100.5"), Decimal("0.25")]
        for index, number in enumerate(numbers):
            app.catalog.create_part(db, f"P-{index}", "Part", "STEEL")
            app.catalog.set_property(db, f"P-{index}", "k", number, "u")
        value = app.schema.property_.c.value
        with app.read(db) as connection:
            ordered = connection.execute(sa.select(value).order_by(value)).scalars().all()
            above = connection.execute(sa.select(value).where(value > Decimal(5)).order_by(value)).scalars().all()
            low, high, total = connection.execute(
                sa.select(sa.func.min(value), sa.func.max(value), sa.func.sum(value))
            ).one()
        assert ordered == sorted(numbers)
        assert above == [Decimal(9), Decimal(10), Decimal("100.5")]
        assert (low, high, total) == (Decimal(-1), Decimal("100.5"), sum(numbers))
        assert all(isinstance(number, Decimal) for number in (*ordered, low, high, total))

    def test_timestamps_order_and_round_trip_before_year_1000(self, app: App, db: Any) -> None:
        """Review L-4 of #22: the year has four digits on SQLite (``0999``), so the value reads back and orders."""
        app.db.upgrade(db)
        early = datetime(999, 1, 2, 3, 4, 5, 6, tzinfo=UTC)
        earliest = datetime(1, 1, 1, tzinfo=UTC)
        material = app.schema.material
        with app.write(db) as connection:
            for code, moment in (("A", NOW), ("B", early), ("C", earliest)):
                connection.execute(sa.insert(material).values(code=code, name="x", created_at=moment))
        created = material.c.created_at
        with app.read(db) as connection:
            ordered = connection.execute(sa.select(created).order_by(created)).scalars().all()
            before = connection.execute(sa.select(material.c.code).where(created < NOW)).scalars().all()
        assert ordered == [earliest, early, NOW]
        assert sorted(before) == ["B", "C"]

    # --- the shared domain code: schema and types (RA-A-1) ------------------------------------------------------

    def test_values_round_trip_exactly(self, app: App, db: Any) -> None:
        app.db.upgrade(db)
        _, part_id = _insert_part(app, db, status=None)
        vienna = datetime(2026, 11, 8, 13, 30, 0, 123456, tzinfo=timezone(timedelta(hours=1)))
        with app.write(db) as connection:
            connection.execute(
                sa.insert(app.schema.property_).values(
                    part_id=part_id, key="mass", value=Decimal("0.100001"), unit="kg"
                )
            )
            connection.execute(sa.update(app.schema.part).values(updated_at=vienna))
        with app.read(db) as connection:
            value = connection.execute(sa.select(app.schema.property_.c.value)).scalar_one()
            row = connection.execute(
                sa.select(app.schema.part.c.status, app.schema.part.c.version, app.schema.part.c.updated_at)
            ).one()
        assert value == Decimal("0.100001") and isinstance(value, Decimal)
        assert row.status == "draft" and row.version == 1  # the server defaults
        assert (
            row.updated_at == NOW and row.updated_at.tzinfo is not None and row.updated_at.utcoffset() == timedelta(0)
        )

    def test_every_check_has_a_case(self, app: App) -> None:
        declared = {
            c.name
            for t in app.schema.metadata.tables.values()
            for c in t.constraints
            if isinstance(c, sa.CheckConstraint)
        }
        assert declared == set(CHECKS) | {"ck_part_relation_quantity", "ck_part_relation_not_self"}

    @pytest.mark.parametrize("check", sorted(CHECKS))
    def test_constraints_hold_on_both_engines(self, app: App, db: Any, check: str) -> None:
        app.db.upgrade(db)
        _, part_id = _insert_part(app, db)
        with app.write(db) as connection:
            connection.execute(
                sa.insert(app.schema.property_).values(part_id=part_id, key="mass", value=Decimal(1), unit="kg")
            )
        table, values = CHECKS[check]
        with pytest.raises(app.integrity_error), app.write(db) as connection:
            connection.execute(sa.update(app.schema.metadata.tables[table]).values(**values))

    @pytest.mark.parametrize("check", sorted(CHECKS))
    def test_violations_name_their_constraint(self, app: App, db: Any, check: str) -> None:
        app.db.upgrade(db)
        _, part_id = _insert_part(app, db)
        with app.write(db) as connection:
            connection.execute(
                sa.insert(app.schema.property_).values(part_id=part_id, key="mass", value=Decimal(1), unit="kg")
            )
        table, values = CHECKS[check]
        with pytest.raises(app.integrity_error) as raised, app.write(db) as connection:
            connection.execute(sa.update(app.schema.metadata.tables[table]).values(**values))
        assert app.constraint_name(raised.value) == check

    def test_decimals_are_stored_scaled_on_sqlite_and_numeric_on_postgresql(self, app: App, db: Any) -> None:
        app.db.upgrade(db)
        _, assembly = _insert_part(app, db, "A-1")
        _, component = _insert_part(app, db, "C-1")
        with app.write(db) as connection:
            connection.execute(
                sa.insert(app.schema.part_relation).values(
                    assembly_id=assembly, component_id=component, quantity=Decimal("2.5")
                )
            )
        with app.read(db) as connection:
            if app.engine(db).dialect.name == "sqlite":
                raw = connection.exec_driver_sql("SELECT typeof(quantity), quantity FROM part_relation").one()
                assert tuple(raw) == ("integer", 2500)
            else:
                assert connection.exec_driver_sql("SELECT quantity FROM part_relation").scalar_one() == Decimal("2.500")

    def test_relation_rules(self, app: App, db: Any) -> None:
        app.db.upgrade(db)
        _, assembly = _insert_part(app, db, "A-1")
        _, component = _insert_part(app, db, "C-1")
        relation = app.schema.part_relation
        with app.write(db) as connection:
            connection.execute(
                sa.insert(relation).values(assembly_id=assembly, component_id=component, quantity=Decimal("2.5"))
            )
        for bad in (
            {"assembly_id": assembly, "component_id": assembly, "quantity": Decimal(1)},  # a part in itself
            {"assembly_id": component, "component_id": assembly, "quantity": Decimal(0)},  # no quantity
            {"assembly_id": assembly, "component_id": 999_999, "quantity": Decimal(1)},  # no such part
        ):
            with pytest.raises(app.integrity_error), app.write(db) as connection:
                connection.execute(sa.insert(relation).values(**bad))
        with app.read(db) as connection:
            assert connection.execute(sa.select(relation.c.quantity)).scalar_one() == Decimal("2.500")

    def test_types_refuse_floats_and_naive_timestamps(self, app: App, db: Any) -> None:
        app.db.upgrade(db)
        _, part_id = _insert_part(app, db)
        for column, value in (
            ("value", 0.5),  # exact in binary, refused for being a float
            ("value", 2.0),
            ("value", Decimal("0.0000001")),  # more digits after the point than the scale
            ("value", Decimal("1" * 13)),  # more digits before the point than numeric(18, 6) holds
        ):
            with pytest.raises(Exception) as raised, app.write(db) as connection:
                connection.execute(
                    sa.insert(app.schema.property_).values(part_id=part_id, key="k", unit="u", **{column: value})
                )
            assert app.refused_value(raised.value), raised.value
        with pytest.raises(Exception) as raised, app.write(db) as connection:
            connection.execute(sa.update(app.schema.part).values(updated_at=datetime(2026, 1, 1)))
        assert app.refused_value(raised.value), raised.value

    # --- the catalog functions (RA-A-2) ---------------------------------------------------------------------------

    def test_crud(self, app: App, catalog_db: Any) -> None:
        db, catalog, errors = catalog_db, app.catalog, app.errors
        created = catalog.create_part(db, "P-100", "Bracket", "STEEL")
        assert (created.status, created.version, created.material_code) == ("draft", 1, "STEEL")
        assert created.created_at.tzinfo is not None
        with pytest.raises(errors.AlreadyExists):
            catalog.create_part(db, "P-100", "Again", "STEEL")
        with pytest.raises(errors.NotFound):
            catalog.create_part(db, "P-101", "No material", "WOOD")
        with pytest.raises(errors.NotFound):
            catalog.get_part(db, "P-999")
        assert catalog.rename_part(db, "P-100", "Angle bracket", expected_version=1) == 2
        assert catalog.get_part(db, "P-100").name == "Angle bracket"
        with pytest.raises(errors.StaleVersion):
            catalog.rename_part(db, "P-100", "Lost", expected_version=1)
        with pytest.raises(errors.NotFound):
            catalog.rename_part(db, "P-999", "x", expected_version=1)
        catalog.create_part(db, "P-050", "Plate", "STEEL")
        assert [p.part_number for p in catalog.list_parts(db)] == ["P-050", "P-100"]
        with pytest.raises(errors.InUse):
            catalog.delete_material(db, "STEEL")
        with pytest.raises(errors.StaleVersion):
            catalog.delete_part(db, "P-100", expected_version=1)
        catalog.delete_part(db, "P-100", expected_version=2)
        assert [p.part_number for p in catalog.list_parts(db)] == ["P-050"]

    def test_status_life_cycle(self, app: App, catalog_db: Any) -> None:
        db, catalog, errors = catalog_db, app.catalog, app.errors
        catalog.create_part(db, "P-1", "Bolt", "STEEL")
        version = 1
        for wanted in ("released", "blocked", "released"):
            version = catalog.change_status(db, "P-1", wanted, expected_version=version)
        assert catalog.list_parts(db, status="released")[0].version == version == 4
        with pytest.raises(errors.InvalidTransition):
            catalog.change_status(db, "P-1", "draft", expected_version=version)
        with pytest.raises(errors.StaleVersion):
            catalog.change_status(db, "P-1", "obsolete", expected_version=version - 1)
        version = catalog.change_status(db, "P-1", "obsolete", expected_version=version)
        for wanted in app.schema.STATUSES:
            with pytest.raises(errors.InvalidTransition):
                catalog.change_status(db, "P-1", wanted, expected_version=version)  # obsolete is final
        with pytest.raises(ValueError, match="unknown status"):
            catalog.change_status(db, "P-1", "archived", expected_version=version)
        catalog.create_part(db, "P-2", "Nut", "STEEL")
        with pytest.raises(errors.InvalidTransition):
            catalog.change_status(db, "P-2", "blocked", expected_version=1)  # a draft is never blocked
        assert catalog.list_parts(db, status="draft")[0].part_number == "P-2"

    def test_properties_are_exact(self, app: App, catalog_db: Any) -> None:
        db, catalog = catalog_db, app.catalog
        catalog.create_part(db, "P-1", "Bolt", "STEEL")
        catalog.set_property(db, "P-1", "mass", Decimal("0.100001"), "kg")
        catalog.set_property(db, "P-1", "length", Decimal("12.5"), "mm")
        catalog.set_property(db, "P-1", "mass", Decimal("0.100002"), "kg")  # replaced
        assert catalog.properties(db, "P-1") == {
            "length": (Decimal("12.5"), "mm"),
            "mass": (Decimal("0.100002"), "kg"),
        }

    def test_relations_and_bill_of_materials(self, app: App, catalog_db: Any) -> None:
        db, catalog, errors = catalog_db, app.catalog, app.errors
        component = catalog.Component
        for number in ("A", "B", "C", "D"):
            catalog.create_part(db, number, f"Part {number}", "STEEL")
        catalog.add_component(db, "A", "B", Decimal(2))
        catalog.add_component(db, "A", "C", Decimal(3))
        catalog.add_component(db, "B", "C", Decimal("0.5"))
        catalog.add_component(db, "C", "D", Decimal(4))
        assert catalog.components(db, "A") == [component("B", Decimal(2)), component("C", Decimal(3))]
        # C: 3 directly plus 2 * 0.5 through B = 4; D: 4 * 4 = 16, at depth 2 (A -> C -> D) and 3 (A -> B -> C -> D).
        assert catalog.bill_of_materials(db, "A") == [
            component("B", Decimal(2), 1),
            component("C", Decimal("4.0"), 1),
            component("D", Decimal("16.0"), 2),
        ]
        assert catalog.where_used(db, "D") == ["A", "B", "C"]
        assert catalog.where_used(db, "A") == []
        with pytest.raises(errors.CycleRefused):
            catalog.add_component(db, "D", "A", Decimal(1))  # A -> C -> D -> A
        with pytest.raises(errors.CycleRefused):
            catalog.add_component(db, "B", "B", Decimal(1))
        with pytest.raises(errors.AlreadyExists):
            catalog.add_component(db, "A", "B", Decimal(1))
        with pytest.raises(errors.InUse):
            catalog.delete_part(db, "D", expected_version=1)  # a component of C
        catalog.delete_part(db, "A", expected_version=1)  # an assembly: its component rows go with it
        assert catalog.where_used(db, "B") == []
        assert catalog.where_used(db, "D") == ["B", "C"]

    def test_lost_update_is_impossible(self, app: App, catalog_db: Any) -> None:
        """MW-3 (planning document 09 section 7): five concurrent read-then-write changes of one part, all reading
        before any writes. With the optimistic lock four of them get ``StaleVersion`` and retry: the end value is 5
        and the version 6, with no lock error, on both engines."""
        db, catalog = catalog_db, app.catalog
        catalog.create_part(db, "P-1", "0", "STEEL")
        barrier = threading.Barrier(5)
        conflicts: list[int] = []

        def increment() -> None:
            first = True
            while True:
                current = catalog.get_part(db, "P-1")
                if first:
                    barrier.wait(timeout=30)  # everyone has read the same version
                    first = False
                try:
                    catalog.rename_part(db, "P-1", str(int(current.name) + 1), expected_version=current.version)
                    return
                except app.errors.StaleVersion:
                    conflicts.append(1)

        _counter_threads(increment)
        final = catalog.get_part(db, "P-1")
        assert (final.name, final.version) == ("5", 6)
        assert len(conflicts) >= 4

    def test_lost_update_without_the_version_check(self, app: App, catalog_db: Any) -> None:
        """The contrast: the same five changes without the version check lose four of them (the end value is 1)."""
        db, catalog = catalog_db, app.catalog
        catalog.create_part(db, "P-1", "0", "STEEL")
        barrier = threading.Barrier(5)
        part = app.schema.part

        def increment_blindly() -> None:
            current = catalog.get_part(db, "P-1")
            barrier.wait(timeout=30)
            with app.write(db) as connection:
                connection.execute(
                    sa.update(part).where(part.c.part_number == "P-1").values(name=str(int(current.name) + 1))
                )

        _counter_threads(increment_blindly)
        assert catalog.get_part(db, "P-1").name == "1"

    # --- the review of #29 (RA-A-2) ------------------------------------------------------------------------------

    def test_concurrent_additions_cannot_close_a_cycle(
        self, app: App, catalog_db: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Review M-1 of #29: with B -> C and D -> A stored, ``A -> B`` and ``C -> D`` at the same time would close the
        cycle A -> B -> C -> D -> A, each passing its own check. Serialized (``BEGIN IMMEDIATE``, the advisory lock on
        PostgreSQL), the second one sees the first and is refused."""
        db = catalog_db
        for number in ("A", "B", "C", "D"):
            app.catalog.create_part(db, number, number, "STEEL")
        app.catalog.add_component(db, "B", "C", Decimal(1))
        app.catalog.add_component(db, "D", "A", Decimal(1))
        _pause_after_the_cycle_check(app, monkeypatch, 2)
        outcomes: list[str] = []

        def add(assembly: str, component: str) -> None:
            try:
                app.catalog.add_component(db, assembly, component, Decimal(1))
                outcomes.append("added")
            except app.errors.CycleRefused:
                outcomes.append("refused")

        threads = [threading.Thread(target=add, args=pair) for pair in (("A", "B"), ("C", "D"))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        assert sorted(outcomes) == ["added", "refused"]
        assert "A" not in app.catalog.where_used(db, "A")

    def test_a_stored_cycle_is_reported_not_recursed(self, app: App, catalog_db: Any) -> None:
        """Review M-1 of #29: relations written past ``add_component`` with a cycle give ``app.errors.CycleFound``."""
        db = catalog_db
        ids = {number: app.catalog.create_part(db, number, number, "STEEL").id for number in ("A", "B", "C")}
        with app.write(db) as connection:
            for assembly, component in (("A", "B"), ("B", "C"), ("C", "B")):
                connection.execute(
                    sa.insert(app.schema.part_relation).values(
                        assembly_id=ids[assembly], component_id=ids[component], quantity=Decimal(1)
                    )
                )
        with pytest.raises(app.errors.CycleFound):
            app.catalog.bill_of_materials(db, "A")
        with pytest.raises(app.errors.CycleFound):
            app.catalog.bill_of_materials(db, "B")  # the root itself is in the cycle
        assert app.catalog.where_used(db, "C") == ["A", "B", "C"]

    def test_a_deep_chain_and_many_diamonds_are_linear(self, app: App, catalog_db: Any) -> None:
        """Review L-1 of #29: 1500 levels (beyond Python's recursion limit) and 22 diamond levels (2**22 paths)."""
        db = catalog_db
        numbers = [f"L{index:05}" for index in range(1501)]
        _chain(app, db, numbers, Decimal(1))
        deep = app.catalog.bill_of_materials(db, numbers[0])
        assert len(deep) == 1500 and deep[-1] == app.catalog.Component(numbers[-1], Decimal(1), 1500)
        # Diamonds: level i -> left i, right i (quantity 1 each) -> level i + 1.
        with app.write(db) as connection:
            material_id = connection.execute(sa.select(app.schema.material.c.id)).scalar_one()
            names = ["D0"] + [f"{side}{i}" for i in range(22) for side in ("X", "Y")] + [f"D{i + 1}" for i in range(22)]
            ids = {
                name: connection.execute(
                    sa.insert(app.schema.part)
                    .values(part_number=name, name=name, material_id=material_id, created_at=NOW, updated_at=NOW)
                    .returning(app.schema.part.c.id)
                ).scalar_one()
                for name in names
            }
            rows = []
            for i in range(22):
                for side in ("X", "Y"):
                    rows.append(
                        {"assembly_id": ids[f"D{i}"], "component_id": ids[f"{side}{i}"], "quantity": Decimal(1)}
                    )
                    rows.append(
                        {"assembly_id": ids[f"{side}{i}"], "component_id": ids[f"D{i + 1}"], "quantity": Decimal(1)}
                    )
            connection.execute(sa.insert(app.schema.part_relation), rows)
        started = time.monotonic()
        diamonds = {c.part_number: c for c in app.catalog.bill_of_materials(db, "D0")}
        assert time.monotonic() - started < 5
        assert diamonds["D22"] == app.catalog.Component("D22", Decimal(2**22), 44)

    def test_bill_of_materials_is_exact_then_rounded_once(self, app: App, catalog_db: Any) -> None:
        """Review L-3 of #29: the default context (28 digits) would round 999999999.999**4 in its integer part; the
        catalog computes exactly and rounds once to ``BOM_SCALE`` places."""
        db = catalog_db
        quantity = Decimal("999999999.999")
        _chain(app, db, ["Q0", "Q1", "Q2", "Q3", "Q4"], quantity)
        with localcontext(prec=100):
            exact = quantity**4
            expected = exact.quantize(Decimal(1).scaleb(-app.catalog.BOM_SCALE))
        assert quantity**4 != exact  # the default context rounds it
        assert app.catalog.bill_of_materials(db, "Q0")[-1] == app.catalog.Component("Q4", expected, 4)
        _chain(app, db, [f"R{i}" for i in range(13)], Decimal("1.001"))
        with localcontext(prec=100):
            twelve = (Decimal("1.001") ** 12).quantize(Decimal(1).scaleb(-app.catalog.BOM_SCALE))
        last = {c.part_number: c for c in app.catalog.bill_of_materials(db, "R0")}["R12"]
        assert (
            last == app.catalog.Component("R12", twelve, 12)
            and last.quantity.as_tuple().exponent == -app.catalog.BOM_SCALE
        )

    def test_the_smallest_depth_wins(self, app: App, catalog_db: Any) -> None:
        """Review L-5 (M4) of #29: C is reached through A -> C (depth 1) and A -> B -> C (depth 2); the longer path is
        processed last, so a depth that is overwritten instead of minimized shows."""
        db = catalog_db
        for number in ("A", "B", "C"):
            app.catalog.create_part(db, number, number, "STEEL")
        app.catalog.add_component(db, "A", "C", Decimal(1))
        app.catalog.add_component(db, "A", "B", Decimal(1))
        app.catalog.add_component(db, "B", "C", Decimal(1))
        assert app.catalog.bill_of_materials(db, "A") == [
            app.catalog.Component("B", Decimal(1), 1),
            app.catalog.Component("C", Decimal(2), 1),
        ]

    def test_changes_move_updated_at(self, app: App, catalog_db: Any) -> None:
        """Review L-5 (M11) of #29."""
        db = catalog_db
        created = app.catalog.create_part(db, "P-1", "Bolt", "STEEL")
        assert created.updated_at == created.created_at
        app.catalog.rename_part(db, "P-1", "Screw", expected_version=1)
        renamed = app.catalog.get_part(db, "P-1")
        assert renamed.updated_at > created.updated_at and renamed.created_at == created.created_at
        app.catalog.change_status(db, "P-1", "released", expected_version=2)
        assert app.catalog.get_part(db, "P-1").updated_at > renamed.updated_at

    def test_not_found_and_invalid_arguments(self, app: App, catalog_db: Any) -> None:
        """Review L-5 of #29: every function that names a part or material refuses an unknown one."""
        db = catalog_db
        app.catalog.create_part(db, "P-1", "Bolt", "STEEL")
        calls: list[Callable[[], object]] = [
            lambda: app.catalog.set_property(db, "P-9", "mass", Decimal(1), "kg"),
            lambda: app.catalog.properties(db, "P-9"),
            lambda: app.catalog.components(db, "P-9"),
            lambda: app.catalog.where_used(db, "P-9"),
            lambda: app.catalog.bill_of_materials(db, "P-9"),
            lambda: app.catalog.add_component(db, "P-1", "P-9", Decimal(1)),
            lambda: app.catalog.change_status(db, "P-9", "released", expected_version=1),
            lambda: app.catalog.delete_part(db, "P-9", expected_version=1),
            lambda: app.catalog.delete_material(db, "WOOD"),
        ]
        for call in calls:
            with pytest.raises(app.errors.NotFound):
                call()
        with pytest.raises(ValueError, match="unknown status"):
            app.catalog.list_parts(db, status="bogus")

    def test_check_violations_are_application_errors_without_a_chain(self, app: App, catalog_db: Any) -> None:
        """Review L-4 and L-5 of #29: a ``CHECK`` violation is ``InvalidValue``; no application error chains the
        driver's error (its message may quote a value)."""
        db = catalog_db
        app.catalog.create_part(db, "A", "A", "STEEL")
        app.catalog.create_part(db, "B", "B", "STEEL")
        raised: list[BaseException] = []
        cases: list[tuple[Callable[[], object], type[Exception]]] = [
            (lambda: app.catalog.create_part(db, "P-2", "", "STEEL"), app.errors.InvalidValue),
            (lambda: app.catalog.create_part(db, "P" * 41, "x", "STEEL"), app.errors.InvalidValue),
            (lambda: app.catalog.add_component(db, "A", "B", Decimal(0)), app.errors.InvalidValue),
            (lambda: app.catalog.set_property(db, "A", "", Decimal(1), "kg"), app.errors.InvalidValue),
            (lambda: app.catalog.create_material(db, "STEEL", "again"), app.errors.AlreadyExists),
            (lambda: app.catalog.delete_material(db, "STEEL"), app.errors.InUse),
        ]
        for call, error in cases:
            with pytest.raises(error) as info:
                call()
            raised.append(info.value)
        assert all(e.__cause__ is None and e.__context__ is None for e in raised)

    def test_concurrent_first_set_property(self, app: App, catalog_db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        """Review L-4 of #29: two first ``set_property`` calls of one key at once; on PostgreSQL the second insert hits
        the ``UNIQUE`` constraint and runs again as an update. One value wins, no error."""
        db = catalog_db
        app.catalog.create_part(db, "P-1", "Bolt", "STEEL")
        barrier = threading.Barrier(2)
        part_id = app.catalog._part_id

        def paused(connection: sa.Connection, part_number: str) -> int:
            found: int = part_id(connection, part_number)
            with contextlib.suppress(threading.BrokenBarrierError):
                barrier.wait(timeout=1)
            return found

        monkeypatch.setattr(app.catalog, "_part_id", paused)
        errors: list[BaseException] = []

        def put(value: int) -> None:
            try:
                app.catalog.set_property(db, "P-1", "mass", Decimal(value), "kg")
            except BaseException as exc:
                errors.append(exc)

        threads = [threading.Thread(target=put, args=(value,)) for value in (1, 2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        assert errors == []
        assert app.catalog.properties(db, "P-1")["mass"][0] in (Decimal(1), Decimal(2))

    # --- the delta review of #29 (RA-A-2) -------------------------------------------------------------------------

    def test_values_that_do_not_fit_are_invalid_values(self, app: App, catalog_db: Any) -> None:
        """Review L-6 of #29: a quantity or property value the column cannot hold (too large, too many decimals,
        ``NaN``, a float) is ``InvalidValue`` before any SQL, not the database layer's usage error."""
        db, catalog = catalog_db, app.catalog
        catalog.create_part(db, "A", "A", "STEEL")
        catalog.create_part(db, "B", "B", "STEEL")
        for bad in (Decimal("1e12"), Decimal("0.0001"), Decimal("NaN"), 0.5):
            with pytest.raises(app.errors.InvalidValue):
                catalog.add_component(db, "A", "B", bad)
            with pytest.raises(app.errors.InvalidValue):
                catalog.set_property(db, "A", "mass", bad if bad != Decimal("0.0001") else Decimal("1e-7"), "kg")
        assert catalog.components(db, "A") == [] and catalog.properties(db, "A") == {}

    def test_a_positive_total_never_rounds_to_zero(self, app: App, catalog_db: Any) -> None:
        """Review L-7 of #29: three levels of ``0.001`` give an exact total of ``1e-9``, below ``BOM_SCALE``; the
        bill of materials refuses rather than listing the part as needed 0 times."""
        db = catalog_db
        _chain(app, db, ["Z0", "Z1", "Z2", "Z3"], Decimal("0.001"))
        assert app.catalog.bill_of_materials(db, "Z1")[-1] == app.catalog.Component("Z3", Decimal("0.000001"), 2)
        with pytest.raises(app.errors.InvalidValue):
            app.catalog.bill_of_materials(db, "Z0")
