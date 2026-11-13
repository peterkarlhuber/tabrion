"""Schema fingerprint and comparison on PostgreSQL (TABRION TAB-CR-2, contract section 5.8, I-108)."""

# The SQL is built from the schema fixture's generated name and fixed text, never from outside input.

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

pytestmark = pytest.mark.postgres

pytest.importorskip("sqlalchemy")
psycopg = pytest.importorskip("psycopg")

from tabrion import Database  # noqa: E402
from tabrion import schema as ts  # noqa: E402
from tabrion.testing import PostgresSchema  # noqa: E402
from tests.postgres.test_database import sa_url  # noqa: E402


@pytest.fixture
def names(tabrion_postgres_url: str, tabrion_postgres_schema: PostgresSchema) -> str:
    name = tabrion_postgres_schema.name
    with psycopg.connect(tabrion_postgres_url, autocommit=True) as conn:
        conn.execute(
            f"CREATE TABLE {name}.invoices (id bigint PRIMARY KEY, total_cents integer NOT NULL, "
            f"note varchar(20), tags text[], at timestamptz)"
        )
        conn.execute(f"CREATE TABLE {name}.lines (invoice bigint, n int, PRIMARY KEY (invoice, n))")
    return name


@pytest.fixture
def db(tabrion_postgres_url: str) -> Iterator[Database]:
    database = Database.from_url(sa_url(tabrion_postgres_url))
    yield database
    database.close()


def take(db: Database, **options: Any) -> ts.SchemaSnapshot:
    with db.inspector() as inspector:
        return ts.snapshot(inspector, **options)


def test_cr2_postgres_snapshot(db: Database, names: str) -> None:
    snap = take(db, schemas=[names])
    assert snap.dialect == "postgresql"
    assert [t.name for t in snap.tables] == [f"{names}.invoices", f"{names}.lines"]
    invoices = snap.table(f"{names}.invoices")
    assert invoices is not None
    assert [(c.name, c.type, c.nullable) for c in invoices.columns] == [
        ("id", "BIGINT", False),
        ("total_cents", "INTEGER", False),
        ("note", "VARCHAR(20)", True),
        ("tags", "TEXT[]", True),
        ("at", "TIMESTAMP WITH TIME ZONE", True),
    ]
    lines = snap.table(f"{names}.lines")
    assert lines is not None and lines.primary_key == ("invoice", "n")
    assert take(db, tables=[f"{names}.lines", f"{names}.invoices"]) == snap


def test_cr2_postgres_drift(db: Database, names: str, tabrion_postgres_url: str) -> None:
    expected = take(db, schemas=[names])
    print_before = ts.fingerprint(expected)
    with psycopg.connect(tabrion_postgres_url, autocommit=True) as conn:
        conn.execute(f"ALTER TABLE {names}.invoices DROP COLUMN total_cents")
        conn.execute(f"ALTER TABLE {names}.invoices ALTER COLUMN note TYPE varchar(40)")
        conn.execute(f"ALTER TABLE {names}.invoices ALTER COLUMN at SET NOT NULL")
    actual = take(db, schemas=[names])
    assert ts.fingerprint(actual) != print_before
    assert [str(d) for d in ts.compare(expected, actual)] == [
        f"column_missing: {names}.invoices.total_cents",
        f"column_type: {names}.invoices.note",
        f"column_nullable: {names}.invoices.at",
    ]
