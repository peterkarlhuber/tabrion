"""Schema fingerprint and comparison on SQLite (TABRION TAB-CR-2, contract section 5.8, I-108).

The PostgreSQL side is ``tests/postgres/test_schema_postgres.py``.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")

from tabrion import Database
from tabrion import schema as ts
from tabrion.errors import ReadOnlyViolation, UsageError

SECRET = "SECRET-4711"


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    database = Database.from_url(f"sqlite:///{tmp_path / 'schema.db'}")
    database.execute("CREATE TABLE invoices (id INTEGER PRIMARY KEY, total_cents INTEGER NOT NULL, note VARCHAR(20))")
    database.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
    database.execute("CREATE VIEW big AS SELECT * FROM invoices")
    database.execute("INSERT INTO customers (name) VALUES (:n)", {"n": SECRET})
    yield database
    database.close()


def take(db: Database, **options: object) -> ts.SchemaSnapshot:
    with db.inspector() as inspector:
        return ts.snapshot(inspector, **options)  # type: ignore[arg-type]


def test_cr2_snapshot_shape(db: Database) -> None:
    snap = take(db)
    assert snap.dialect == "sqlite"
    assert [t.name for t in snap.tables] == ["main.customers", "main.invoices"]  # sorted, no view
    invoices = snap.table("main.invoices")
    assert invoices is not None
    assert invoices.columns == (
        ts.ColumnShape("id", "INTEGER", True),
        ts.ColumnShape("total_cents", "INTEGER", False),
        ts.ColumnShape("note", "VARCHAR(20)", True),
    )
    assert invoices.primary_key == ("id",)
    assert snap.table("main.nothing") is None
    assert SECRET not in snap.to_json()  # shape only, never data


def test_cr2_fingerprint_is_stable_and_sensitive(db: Database, tmp_path: Path) -> None:
    first = ts.fingerprint(take(db))
    assert first.startswith("v1:sha256:") and len(first) == len("v1:sha256:") + 64
    assert ts.fingerprint(take(db)) == first
    db.execute("INSERT INTO invoices (total_cents) VALUES (5)")  # data does not count
    assert ts.fingerprint(take(db)) == first
    other = Database.from_url(f"sqlite:///{tmp_path / 'other.db'}")
    other.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
    other.execute("CREATE TABLE invoices (id INTEGER PRIMARY KEY, total_cents INTEGER NOT NULL, note VARCHAR(20))")
    assert ts.fingerprint(take(other)) == first  # creation order does not count
    other.execute("ALTER TABLE invoices ADD COLUMN paid INTEGER")
    assert ts.fingerprint(take(other)) != first
    other.close()


def test_cr2_tables_selects_and_reports_missing(db: Database) -> None:
    snap = take(db, tables=["invoices", "main.customers", "main.absent"])
    assert [t.name for t in snap.tables] == ["main.customers", "main.invoices"]
    only = take(db, tables=["invoices"])
    assert [t.name for t in only.tables] == ["main.invoices"]
    expected = take(db, tables=["invoices", "customers"])
    assert ts.compare(expected, only) == (ts.SchemaDifference("table_missing", "main.customers"),)


def test_cr2_compare_names_each_difference(db: Database, tmp_path: Path) -> None:
    expected = take(db)
    db.execute("DROP TABLE customers")
    db.execute("CREATE TABLE extra (x INTEGER)")
    db.execute("ALTER TABLE invoices DROP COLUMN total_cents")
    db.execute("ALTER TABLE invoices ADD COLUMN paid INTEGER")
    differences = ts.compare(expected, take(db))
    assert [str(d) for d in differences] == [
        "table_missing: main.customers",
        "table_added: main.extra",
        "column_missing: main.invoices.total_cents",
        "column_added: main.invoices.paid",
    ]
    assert [d.breaking for d in differences] == [True, False, True, False]
    assert ts.compare(expected, expected) == ()


def test_cr2_type_nullable_and_primary_key_changes() -> None:
    def table(*columns: ts.ColumnShape, pk: tuple[str, ...] = ("id",)) -> ts.SchemaSnapshot:
        return ts.SchemaSnapshot("sqlite", (ts.TableShape("main.t", columns, pk),))

    base = table(ts.ColumnShape("id", "INTEGER", False), ts.ColumnShape("v", "TEXT", True))
    retyped = table(ts.ColumnShape("id", "INTEGER", False), ts.ColumnShape("v", "INTEGER", True))
    narrowed = table(ts.ColumnShape("id", "INTEGER", False), ts.ColumnShape("v", "TEXT", False))
    rekeyed = table(ts.ColumnShape("id", "INTEGER", False), ts.ColumnShape("v", "TEXT", True), pk=("v",))
    assert ts.compare(base, retyped) == (ts.SchemaDifference("column_type", "main.t", "v"),)
    assert ts.compare(base, narrowed) == (ts.SchemaDifference("column_nullable", "main.t", "v"),)
    assert ts.compare(base, rekeyed) == (ts.SchemaDifference("primary_key", "main.t"),)
    assert all(d.breaking for d in ts.compare(base, retyped) + ts.compare(base, narrowed) + ts.compare(base, rekeyed))
    with pytest.raises(UsageError) as info:
        ts.compare(base, ts.SchemaSnapshot("postgresql", base.tables))
    assert info.value.code == "schema.dialect_mismatch"


def test_cr2_json_round_trip(db: Database) -> None:
    snap = take(db)
    assert ts.SchemaSnapshot.from_json(snap.to_json()) == snap
    assert ts.fingerprint(ts.SchemaSnapshot.from_json(snap.to_json())) == ts.fingerprint(snap)
    for bad in ("", "[]", '{"format": 2}', '{"format": 1, "dialect": "sqlite", "tables": [{"name": 1}]}', SECRET):
        with pytest.raises(UsageError) as info:
            ts.SchemaSnapshot.from_json(bad)
        assert info.value.code == "schema.invalid_snapshot"
        assert info.value.__context__ is None and SECRET not in repr(info.value)


def test_cr2_inspector_stays_read_only(db: Database) -> None:
    with pytest.raises(ReadOnlyViolation), db.inspector() as inspector:
        ts.snapshot(inspector)
        inspector.bind.exec_driver_sql("DELETE FROM customers")  # type: ignore[union-attr]


def test_cr2_too_many_tables(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ts, "_MAX_TABLES", 1)
    with pytest.raises(UsageError) as info:
        take(db)
    assert info.value.code == "schema.too_many_tables"
