"""DDL from a level-2 schema model on PostgreSQL: round trips and in-place changes (TABRION TAB-XW-3, CR-DBX).

Contract section 5.12, I-123. Each round trip reflects a schema, drops its tables, runs the generated statements one
at a time (each text alone, as an executor would) and compares the new level-2 snapshot with the first one.
"""

# The SQL of the fixtures is built from the schema fixture's generated name and fixed text, never from outside input.

from __future__ import annotations

from collections.abc import Iterator
import dataclasses

import pytest

pytestmark = pytest.mark.postgres

pytest.importorskip("sqlalchemy")
psycopg = pytest.importorskip("psycopg")

from tabrion import Database, ddl  # noqa: E402
from tabrion import schema as ts  # noqa: E402
from tabrion.errors import UsageError  # noqa: E402
from tabrion.testing import PostgresSchema  # noqa: E402
from tests.postgres.test_database import sa_url  # noqa: E402

SOURCE = """
CREATE TABLE {s}.customers (
    id bigserial PRIMARY KEY, name text NOT NULL UNIQUE, email varchar(200),
    created timestamptz DEFAULT now(), "Mixed Case" int DEFAULT 1, note text DEFAULT 'a;b:c%d',
    CONSTRAINT uq_email UNIQUE (email) DEFERRABLE INITIALLY DEFERRED);
CREATE TABLE {s}.invoices (
    id serial PRIMARY KEY,
    customer bigint NOT NULL REFERENCES {s}.customers (id) ON DELETE CASCADE ON UPDATE RESTRICT,
    other bigint, total_cents integer NOT NULL DEFAULT 0 CHECK (total_cents >= 0), note varchar(20) DEFAULT 'x',
    tags text[], amount numeric(10,2), span interval day to second, at timestamp(3) with time zone,
    UNIQUE (customer, note), CONSTRAINT ck_note CHECK (length(note) < 30 AND note <> ')'),
    CONSTRAINT fk_other FOREIGN KEY (other) REFERENCES {s}.customers (id) MATCH FULL ON UPDATE SET NULL DEFERRABLE);
CREATE INDEX invoices_note ON {s}.invoices (note text_pattern_ops DESC NULLS LAST);
CREATE UNIQUE INDEX "invoices(lower" ON {s}.invoices (lower(note) COLLATE "C" DESC, total_cents) INCLUDE (amount)
    WHERE (total_cents > 0);
CREATE INDEX inv_hash ON {s}.invoices USING hash (note);
CREATE INDEX inv_gin ON {s}.invoices USING gin (tags);
CREATE INDEX inv_nnd ON {s}.invoices (other NULLS FIRST) NULLS NOT DISTINCT;
CREATE INDEX inv_expr ON {s}.invoices ((total_cents + 1));
CREATE TABLE {s}.a (id int PRIMARY KEY, b_id int);
CREATE TABLE {s}.b (id int PRIMARY KEY, a_id int REFERENCES {s}.a (id));
ALTER TABLE {s}.a ADD CONSTRAINT a_b FOREIGN KEY (b_id) REFERENCES {s}.b (id);
CREATE TABLE {s}."select" ("from" int PRIMARY KEY, "where" int REFERENCES {s}."select" ("from"), "größe" int);
"""
COLLATED = """
CREATE TABLE {s}.coll (id int PRIMARY KEY, code text COLLATE "C" UNIQUE, CONSTRAINT two UNIQUE (code, id));
"""
UNIQUE_OPTIONS = """
CREATE TABLE {s}.opts (id int PRIMARY KEY, email text, extra int,
    CONSTRAINT uq_opts UNIQUE NULLS NOT DISTINCT (email) INCLUDE (extra));
"""


@pytest.fixture
def db(tabrion_postgres_url: str) -> Iterator[Database]:
    database = Database.from_url(sa_url(tabrion_postgres_url))
    yield database
    database.close()


def execute(url: str, sql: str) -> None:
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(sql)


def take(db: Database, schema: str) -> ts.SchemaSnapshot:
    with db.inspector() as inspector:
        return ts.snapshot(inspector, schemas=[schema], level=2)


def drop_tables(url: str, model: ts.SchemaSnapshot) -> None:
    for table in model.tables:
        schema, _, name = table.name.rpartition(".")
        execute(url, f'DROP TABLE IF EXISTS "{schema}"."{name}" CASCADE')


def run(url: str, statements: tuple[ddl.DdlStatement, ...]) -> None:
    for statement in statements:
        if statement.transactional:
            with psycopg.connect(url) as conn:  # one statement per transaction, committed at the end of the block
                conn.execute(statement.sql)
        else:
            execute(url, statement.sql)


def round_trip(db: Database, url: str, schema: str) -> tuple[ts.SchemaSnapshot, tuple[ddl.DdlStatement, ...]]:
    model = take(db, schema)
    statements = ddl.create_statements(model, dialect="postgresql")
    drop_tables(url, model)
    assert take(db, schema).tables == ()
    run(url, statements)
    copy = take(db, schema)
    assert [str(d) for d in ts.compare(model, copy)] == []
    assert copy == model
    assert ts.fingerprint(copy) == ts.fingerprint(model)
    return model, statements


def test_xw3_postgres_round_trip(
    db: Database, tabrion_postgres_url: str, tabrion_postgres_schema: PostgresSchema
) -> None:
    """Shape -> DDL -> the empty schema -> level-2 snapshot: the same shape and the same ``v2:`` fingerprint, with
    serial columns, index collations and operator classes, a deferrable unique key, MATCH FULL, a cycle of foreign
    keys, every index detail and quoted names."""
    name = tabrion_postgres_schema.name
    execute(tabrion_postgres_url, SOURCE.format(s=name))
    model, statements = round_trip(db, tabrion_postgres_url, name)
    invoices = model.table(f"{name}.invoices")
    assert invoices is not None
    assert {i.name for i in invoices.indexes} == {
        "invoices_note",
        "invoices(lower",
        "inv_hash",
        "inv_gin",
        "inv_nnd",
        "inv_expr",
    }
    customers = model.table(f"{name}.customers")
    assert customers is not None and customers.columns[0].default is not None
    assert customers.columns[0].default.endswith(".customers_id_seq'::regclass)")
    # the serial column is created as one, so the sequence of the default exists again
    assert "id BIGSERIAL NOT NULL" in statements[[s.target for s in statements].index(f"{name}.customers")].sql


def test_xw3_postgres_unique_options_round_trip(
    db: Database, tabrion_postgres_url: str, tabrion_postgres_schema: PostgresSchema
) -> None:
    """``INCLUDE`` and ``NULLS NOT DISTINCT`` of a unique constraint round-trip where SQLAlchemy writes them and are
    refused where it does not (2.0.30, the floor)."""
    from tabrion.ddl import _unique_option_renders

    name = tabrion_postgres_schema.name
    execute(tabrion_postgres_url, UNIQUE_OPTIONS.format(s=name))
    if _unique_option_renders("postgresql_include", "INCLUDE") and _unique_option_renders(
        "postgresql_nulls_not_distinct", "NULLS NOT DISTINCT"
    ):
        round_trip(db, tabrion_postgres_url, name)
    else:
        with pytest.raises(UsageError) as info:
            ddl.create_statements(take(db, name), dialect="postgresql")
        assert info.value.code == "ddl.unsupported_feature"


def test_xw3_postgres_unique_collation_of_the_column(
    db: Database, tabrion_postgres_url: str, tabrion_postgres_schema: PostgresSchema
) -> None:
    """A unique constraint takes its collation from the column type. SQLAlchemy 2.1 reflects the column's collation
    into the type text (``TEXT COLLATE "C"``), so the table round-trips; 2.0.30 (the floor) does not, so the
    constraint's collation is not implied by the model's column and the table is refused, never created without it."""
    name = tabrion_postgres_schema.name
    execute(tabrion_postgres_url, COLLATED.format(s=name))
    table = take(db, name).table(f"{name}.coll")
    assert table is not None
    assert {u.collations for u in table.unique_constraints} == {("C",), ("C", "")}
    if "COLLATE" in table.columns[1].type:
        round_trip(db, tabrion_postgres_url, name)
    else:
        with pytest.raises(UsageError) as info:
            ddl.table_statements(table, dialect="postgresql")
        assert (info.value.code, info.value.details["feature"]) == ("ddl.unsupported_feature", "unique_collation")


def test_xw3_postgres_in_place_changes(
    db: Database, tabrion_postgres_url: str, tabrion_postgres_schema: PostgresSchema
) -> None:
    """Each in-place statement runs on PostgreSQL 16 and reflection shows its effect."""
    name = tabrion_postgres_schema.name
    url = tabrion_postgres_url
    execute(url, f"CREATE TABLE {name}.t (id int PRIMARY KEY, note varchar(20) DEFAULT 'x', n int)")

    def shape() -> ts.TableShape:
        found = take(db, name).table(f"{name}.t")
        assert found is not None
        return found

    t = shape()
    run(url, (ddl.add_column_statement(t, ts.ColumnShape("Big", "JSONB", False, "'{}'::jsonb"), dialect="postgresql"),))
    t = shape()
    assert t.columns[-1] == ts.ColumnShape("Big", "JSONB", False, "'{}'::jsonb")
    old = t.columns[1]
    new = ts.ColumnShape("memo", "TEXT", False, "'y'::text")
    run(url, ddl.alter_column_statements(t, old, new, dialect="postgresql"))
    t = shape()
    assert t.columns[1] == new
    unique = ts.UniqueShape("t_memo_key", ("memo",), deferrable=True)
    check = ts.CheckShape("t_n_check", "n > 0")
    fk = ts.ForeignKeyShape("t_n_fkey", ("n",), f"{name}.t", ("id",), on_delete="SET NULL")
    for constraint in (unique, check, fk):
        run(url, (ddl.add_constraint_statement(t, constraint, dialect="postgresql"),))
    t = shape()
    assert (t.unique_constraints, t.check_constraints, t.foreign_keys) == ((unique,), (check,), (fk,))
    index = ts.IndexShape("t_memo_lower", ("lower(memo)",), expressions=(0,), where="(n > 0)")
    create = ddl.create_index_statement(t, index, dialect="postgresql", concurrently=True)
    assert not create.transactional
    run(url, (create,))
    t = shape()
    assert t.indexes == (index,)
    for constraint_name in ("t_memo_key", "t_n_check", "t_n_fkey"):
        run(url, (ddl.drop_constraint_statement(t, constraint_name, dialect="postgresql"),))
    run(url, (ddl.drop_index_statement(t, "t_memo_lower", dialect="postgresql", concurrently=True),))
    run(url, (ddl.drop_column_statement(t, "Big", dialect="postgresql"),))
    t = shape()
    assert (t.unique_constraints, t.check_constraints, t.foreign_keys, t.indexes) == ((), (), (), ())
    assert [c.name for c in t.columns] == ["id", "memo", "n"]
    run(url, (ddl.drop_table_statement(t, dialect="postgresql"),))
    assert take(db, name).tables == ()


def test_xw3_postgres_literal_text_runs_unchanged(
    db: Database, tabrion_postgres_url: str, tabrion_postgres_schema: PostgresSchema
) -> None:
    """A default or check text with ``;``, ``:`` and ``%`` inside a literal is written once, unchanged (no doubled
    ``%``, no bound parameter), and the statement runs without parameters."""
    name = tabrion_postgres_schema.name
    table = ts.TableShape(
        f"{name}.lit",
        (ts.ColumnShape("a", "TEXT", True, "'x; DROP TABLE y; :z %s'::text"),),
        (),
        check_constraints=(ts.CheckShape("ck", "a <> ';'::text"),),
    )
    (create,) = ddl.table_statements(table, dialect="postgresql")
    assert "'x; DROP TABLE y; :z %s'::text" in create.sql
    execute(tabrion_postgres_url, create.sql)
    found = take(db, name).table(f"{name}.lit")
    assert found is not None
    assert dataclasses.replace(found, name=table.name) == table
