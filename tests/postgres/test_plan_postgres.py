"""Schema change plans on PostgreSQL (TABRION TAB-XW-4, contract section 5.13, I-124; ADR 0015 D-5).

A plan made from a reflected snapshot and run in one transaction reaches the target fingerprint; the D-5 parts
(triggers, policies, the functions they call, the table features of I-111 i, event triggers) change the v2
fingerprint and refuse a plan that would change the table. The revision text runs through Alembic's ``Operations``
on a connection the test opens, never through an ``env.py``.
"""

# The SQL is built from the schema fixture's generated name and fixed text, never from outside input.
# ruff: noqa: S608

from __future__ import annotations

from collections.abc import Iterator
import dataclasses
from typing import Any

import pytest

pytestmark = pytest.mark.postgres

sqlalchemy = pytest.importorskip("sqlalchemy")
psycopg = pytest.importorskip("psycopg")

from tabrion import Database  # noqa: E402
from tabrion import plan as tp  # noqa: E402
from tabrion import schema as ts  # noqa: E402
from tabrion.errors import PlanRefused  # noqa: E402
from tabrion.testing import PostgresSchema  # noqa: E402
from tests.postgres.conftest import PREFIX  # noqa: E402
from tests.postgres.test_database import sa_url  # noqa: E402


@pytest.fixture
def db(tabrion_postgres_url: str) -> Iterator[Database]:
    database = Database.from_url(sa_url(tabrion_postgres_url))
    yield database
    database.close()


def take(db: Database, **options: Any) -> ts.SchemaSnapshot:
    with db.inspector() as inspector:
        return ts.snapshot(inspector, level=2, **options)


def ddl(url: str, *statements: str) -> None:
    with psycopg.connect(url, autocommit=True) as conn:
        for statement in statements:
            conn.execute(statement)


def run_in_one_transaction(url: str, change: tp.MigrationPlan) -> None:
    assert all(step.transactional for step in change.steps)
    with psycopg.connect(url) as conn, conn.transaction():
        for step in change.steps:
            for statement in step.statements:
                conn.execute(statement.sql)


def column(name: str, type_: str = "INTEGER", nullable: bool = True, default: str | None = None) -> ts.ColumnShape:
    return ts.ColumnShape(name, type_, nullable, default=default)


def add_column(snapshot: ts.SchemaSnapshot, table: str, new: ts.ColumnShape) -> ts.SchemaSnapshot:
    tables = tuple(dataclasses.replace(t, columns=(*t.columns, new)) if t.name == table else t for t in snapshot.tables)
    return dataclasses.replace(snapshot, tables=tables)


TOUCH = (
    "CREATE OR REPLACE FUNCTION {s}.touch() RETURNS trigger LANGUAGE plpgsql AS "
    "$$ BEGIN NEW.code := upper(NEW.code); RETURN NEW; END $$"
)


@pytest.fixture
def shop(tabrion_postgres_url: str, tabrion_postgres_schema: PostgresSchema) -> str:
    s = tabrion_postgres_schema.name
    ddl(
        tabrion_postgres_url,
        f"CREATE TABLE {s}.parts (id bigint PRIMARY KEY, code varchar(10))",
        f"CREATE TABLE {s}.moves (id bigint PRIMARY KEY, part bigint, note text)",
        f"CREATE TABLE {s}.old (id bigint)",
        TOUCH.format(s=s),
        f"CREATE TRIGGER parts_touch BEFORE INSERT ON {s}.parts FOR EACH ROW EXECUTE FUNCTION {s}.touch()",
        f"INSERT INTO {s}.parts VALUES (1, 'a:b')",
        f"INSERT INTO {s}.moves VALUES (1, 1, 'x')",
    )
    return s


def test_xw4_postgres_plan_round_trip(db: Database, shop: str, tabrion_postgres_url: str) -> None:
    """Column widened, column with default added, foreign key and index added, a table created and one dropped:
    run in one transaction, the database has the target fingerprint and keeps its rows and its trigger."""
    s = shop
    current = take(db, schemas=[s])
    parts = current.table(f"{s}.parts")
    moves = current.table(f"{s}.moves")
    assert parts is not None and moves is not None
    assert [t.name for t in parts.triggers] == ["parts_touch"]
    assert [(f.signature, f.language) for f in parts.functions] == [(f"{s}.touch()", "plpgsql")]
    target = ts.SchemaSnapshot(
        "postgresql",
        (  # sorted by name, as a reflected snapshot is
            ts.TableShape(f"{s}.log", (column("id", "BIGINT", False), column("what", "TEXT")), ("id",)),
            dataclasses.replace(
                moves,
                columns=(*moves.columns, column("qty", nullable=False, default="0")),
                foreign_keys=(ts.ForeignKeyShape("moves_part", ("part",), f"{s}.parts", ("id",)),),
                indexes=(ts.IndexShape("moves_part_ix", ("part",)),),
            ),
            dataclasses.replace(parts, columns=(parts.columns[0], column("code", "VARCHAR(20)"))),
        ),
        level=2,
    )
    change = tp.plan(current, target)
    assert change.source_fingerprint == ts.fingerprint(current)
    assert change.target_fingerprint == ts.fingerprint(target)
    assert {r.value for r in change.risks} >= {"destructive", "locking"}
    assert tp.MigrationPlan.from_json(change.to_json()) == change
    run_in_one_transaction(tabrion_postgres_url, change)
    after = take(db, schemas=[s])
    assert ts.compare(target, after) == ()
    assert ts.fingerprint(after) == change.target_fingerprint
    with psycopg.connect(tabrion_postgres_url) as conn:
        assert conn.execute(f"SELECT id, part, note, qty FROM {s}.moves").fetchall() == [(1, 1, "x", 0)]
        conn.execute(f"INSERT INTO {s}.parts VALUES (2, 'lower')")
        assert conn.execute(f"SELECT code FROM {s}.parts WHERE id = 2").fetchone() == ("LOWER",)
    assert tp.plan(after, target).steps == ()


def test_xw4_function_body_changes_the_fingerprint(db: Database, shop: str, tabrion_postgres_url: str) -> None:
    before = take(db, schemas=[shop])
    ddl(
        tabrion_postgres_url,
        f"CREATE OR REPLACE FUNCTION {shop}.touch() RETURNS trigger LANGUAGE plpgsql AS "
        "$$ BEGIN NEW.code := lower(NEW.code); RETURN NEW; END $$",
    )
    after = take(db, schemas=[shop])
    assert ts.fingerprint(before) != ts.fingerprint(after)
    assert [(d.kind, d.name) for d in ts.compare(before, after)] == [
        ("function_missing", f"{shop}.touch()"),
        ("function_added", f"{shop}.touch()"),
    ]
    ddl(tabrion_postgres_url, f"ALTER TABLE {shop}.parts DISABLE TRIGGER parts_touch")
    disabled = take(db, schemas=[shop])
    assert [d.kind for d in ts.compare(after, disabled)] == ["trigger_missing", "trigger_added"]
    trigger = disabled.table(f"{shop}.parts")
    assert trigger is not None and trigger.triggers[0].enabled == "DISABLED"


def test_xw4_policies_and_row_security(db: Database, shop: str, tabrion_postgres_url: str) -> None:
    before = take(db, schemas=[shop])
    ddl(
        tabrion_postgres_url,
        f"ALTER TABLE {shop}.moves ENABLE ROW LEVEL SECURITY",
        f"CREATE POLICY own ON {shop}.moves AS RESTRICTIVE FOR SELECT USING (note <> 'hidden')",
    )
    after = take(db, schemas=[shop])
    moves = after.table(f"{shop}.moves")
    assert moves is not None and moves.row_security and not moves.force_row_security
    assert [(p.name, p.command, p.permissive, p.roles) for p in moves.policies] == [
        ("own", "SELECT", False, ("PUBLIC",))
    ]
    assert [(d.kind, d.name) for d in ts.compare(before, after)] == [("policy_added", "own"), ("row_security", None)]
    # A plan keeps a table with a SQL-only policy changeable, and never adds or drops a policy itself.
    change = tp.plan(after, add_column(after, f"{shop}.moves", column("qty")))
    assert [step.operation for step in change.steps] == ["add_column"]
    with pytest.raises(PlanRefused) as caught:
        tp.plan(before, after)
    assert caught.value.details == {"reason": "unsupported_feature", "table": f"{shop}.moves", "feature": "policy"}


def test_xw4_internal_function_refuses_the_plan(db: Database, shop: str, tabrion_postgres_url: str) -> None:
    ddl(
        tabrion_postgres_url,
        f"CREATE FUNCTION {shop}.size_of(text) RETURNS integer LANGUAGE internal IMMUTABLE STRICT AS 'textlen'",
        f"ALTER TABLE {shop}.moves ENABLE ROW LEVEL SECURITY",
        f"CREATE POLICY sized ON {shop}.moves USING ({shop}.size_of(note) > 0)",
    )
    current = take(db, schemas=[shop])
    moves = current.table(f"{shop}.moves")
    assert moves is not None
    assert [(f.signature, f.language) for f in moves.functions] == [(f"{shop}.size_of(text)", "internal")]
    with pytest.raises(PlanRefused) as caught:
        tp.plan(current, add_column(current, f"{shop}.moves", column("qty")))
    assert caught.value.details == {
        "reason": "unsupported_feature",
        "table": f"{shop}.moves",
        "feature": "function_language",
    }
    # The other tables stay changeable.
    assert tp.plan(current, add_column(current, f"{shop}.old", column("qty"))).steps


@pytest.mark.parametrize(
    ("statements", "feature"),
    [
        (["CREATE TABLE {s}.x (id int, v text) PARTITION BY RANGE (id)"], "partitioned"),
        (
            [
                "CREATE TABLE {s}.p (id int, v text) PARTITION BY RANGE (id)",
                "CREATE TABLE {s}.x PARTITION OF {s}.p FOR VALUES FROM (0) TO (10)",
            ],
            "partition",
        ),
        (["CREATE TABLE {s}.b (id int)", "CREATE TABLE {s}.x (v text) INHERITS ({s}.b)"], "inheritance"),
        (["CREATE TABLE {s}.x (id int, v text)", "CREATE RULE r AS ON INSERT TO {s}.x DO ALSO NOTHING"], "rule:r"),
        (["CREATE DOMAIN {s}.d AS int CHECK (VALUE > 0)", "CREATE TABLE {s}.x (id int, v {s}.d)"], "domain:v"),
        (["CREATE UNLOGGED TABLE {s}.x (id int, v text)"], "unlogged"),
        (["CREATE TABLE {s}.x (id int, v text) WITH (fillfactor = 70)"], "storage_parameters"),
        (["CREATE TABLE {s}.x (id int, v text)", "ALTER TABLE {s}.x REPLICA IDENTITY FULL"], "replica_identity:f"),
        (["CREATE TYPE {s}.ty AS (id int, v text)", "CREATE TABLE {s}.x OF {s}.ty"], "typed_table"),
        (["CREATE TABLE {s}.x (id int, v text, CONSTRAINT ck CHECK (id > 0) NO INHERIT)"], "no_inherit:ck"),
    ],
)
def test_xw4_table_feature_refuses_the_plan(
    db: Database,
    tabrion_postgres_url: str,
    tabrion_postgres_schema: PostgresSchema,
    statements: list[str],
    feature: str,
) -> None:
    s = tabrion_postgres_schema.name
    ddl(tabrion_postgres_url, *(statement.format(s=s) for statement in statements))
    current = take(db, tables=[f"{s}.x"])
    table = current.table(f"{s}.x")
    assert table is not None and feature in table.features
    plain = take(db, tables=[f"{s}.x"])
    assert ts.fingerprint(plain) == ts.fingerprint(current)
    stripped = dataclasses.replace(table, features=tuple(f for f in table.features if f != feature))
    assert ts.fingerprint(current) != ts.fingerprint(dataclasses.replace(current, tables=(stripped,)))
    with pytest.raises(PlanRefused) as caught:
        tp.plan(current, add_column(current, f"{s}.x", column("extra")))
    assert caught.value.details["reason"] == "unsupported_feature"
    assert caught.value.details["table"] == f"{s}.x"
    assert caught.value.details["feature"] in table.features
    with pytest.raises(PlanRefused):
        tp.plan(current, dataclasses.replace(current, tables=()))  # nor dropped
    assert tp.plan(current, current).steps == ()


def test_xw4_foreign_table_refuses_the_plan(
    db: Database, tabrion_postgres_url: str, tabrion_postgres_schema: PostgresSchema
) -> None:
    s = tabrion_postgres_schema.name
    wrapper, server = f"{PREFIX}fdw", f"{PREFIX}srv"
    ddl(
        tabrion_postgres_url,
        f"CREATE FOREIGN DATA WRAPPER {wrapper}",
        f"CREATE SERVER {server} FOREIGN DATA WRAPPER {wrapper}",
    )
    try:
        ddl(tabrion_postgres_url, f"CREATE FOREIGN TABLE {s}.x (id int, v text) SERVER {server}")
        current = take(db, tables=[f"{s}.x"])
        table = current.table(f"{s}.x")
        assert table is not None and "foreign_table" in table.features
        with pytest.raises(PlanRefused) as caught:
            tp.plan(current, add_column(current, f"{s}.x", column("extra")))
        assert caught.value.details["feature"] == "foreign_table"
    finally:
        ddl(
            tabrion_postgres_url,
            f"DROP FOREIGN TABLE IF EXISTS {s}.x",
            f"DROP SERVER IF EXISTS {server}",
            f"DROP FOREIGN DATA WRAPPER IF EXISTS {wrapper}",
        )


def test_xw4_event_trigger_refuses_every_change(scratch_database: str) -> None:
    ddl(
        scratch_database,
        "CREATE TABLE t (id int PRIMARY KEY)",
        "CREATE FUNCTION audit() RETURNS event_trigger LANGUAGE plpgsql AS $$ BEGIN END $$",
        "CREATE EVENT TRIGGER audit_ddl ON ddl_command_start EXECUTE FUNCTION audit()",
    )
    database = Database.from_url(sa_url(scratch_database))
    try:
        current = take(database)
        assert current.features == ("event_trigger:audit_ddl",)
        assert ts.compare(current, current) == ()
        assert tp.plan(current, current).steps == ()
        with pytest.raises(PlanRefused) as caught:
            tp.plan(current, add_column(current, "public.t", column("v")))
        assert caught.value.details == {
            "reason": "unsupported_feature",
            "table": "",
            "feature": "event_trigger:audit_ddl",
        }
        ddl(scratch_database, "ALTER EVENT TRIGGER audit_ddl DISABLE")
        assert take(database).features == ("event_trigger:audit_ddl",)  # disabled still counts
    finally:
        database.close()


def test_xw4_revision_runs_on_postgres_with_concurrent_indexes(
    db: Database, shop: str, tabrion_postgres_url: str
) -> None:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    current = take(db, schemas=[shop])
    moves = current.table(f"{shop}.moves")
    assert moves is not None
    target = dataclasses.replace(
        current,
        tables=tuple(
            dataclasses.replace(
                t,
                columns=(*t.columns, column("qty", nullable=False, default="0")),
                indexes=(ts.IndexShape("ix_note", ("note",)),),
            )
            if t is moves
            else t
            for t in current.tables
        ),
    )
    change = tp.plan(current, target, concurrent_indexes=True)
    assert any(not step.transactional for step in change.steps)
    text = tp.revision_text(change, revision="a1b2c3", down_revision=None, message="note: index")
    assert "autocommit_block" in text
    namespace: dict[str, Any] = {}
    exec(compile(text, "revision.py", "exec"), namespace)  # noqa: S102 - the text is the test's own output
    engine = sqlalchemy.create_engine(sa_url(tabrion_postgres_url))
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            with context.begin_transaction(), Operations.context(context):
                namespace["upgrade"]()
    finally:
        engine.dispose()
    after = take(db, schemas=[shop])
    assert ts.fingerprint(after) == change.target_fingerprint
    with pytest.raises(NotImplementedError):
        namespace["downgrade"]()


def test_xw4_migration_markers_on_postgres(scratch_database: str) -> None:
    database = Database.from_url(sa_url(scratch_database))
    try:
        assert tp.migration_markers(database) == ()
        ddl(
            scratch_database,
            "CREATE SCHEMA app",
            'CREATE TABLE app."Alembic_Version" (version_num varchar(32) PRIMARY KEY)',
        )
        assert tp.migration_markers(database) == ("alembic_version",)
        ddl(scratch_database, "CREATE VIEW axentra_schema_migrations AS SELECT 1 AS version")
        assert tp.migration_markers(database) == ("alembic_version", "axentra_schema_migrations")
    finally:
        database.close()
