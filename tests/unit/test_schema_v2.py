"""Schema model v2 on SQLite: defaults, foreign keys, unique and check constraints, indexes (TABRION TAB-XW-2).

Contract section 5.10, I-110 and I-111; ADR 0015 D-4 (level 2 is opt-in, ``v1:`` fingerprints stay byte-identical).
The PostgreSQL side is ``tests/postgres/test_schema_postgres.py``.
"""

from __future__ import annotations

from collections.abc import Iterator
import dataclasses
import inspect
import json
from pathlib import Path
from typing import Any

import pytest

sqlalchemy = pytest.importorskip("sqlalchemy")

from tabrion import Database  # noqa: E402
from tabrion import schema as ts  # noqa: E402
from tabrion.errors import UsageError  # noqa: E402

SECRET = "SECRET-4711"
GOLDEN = Path(__file__).resolve().parents[1] / "golden" / "schema_v1.json"

DDL = (
    "CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, email TEXT, "
    "created TEXT DEFAULT CURRENT_TIMESTAMP, CONSTRAINT uq_email UNIQUE (email))",
    "CREATE TABLE invoices (id INTEGER PRIMARY KEY, "
    "customer INTEGER NOT NULL REFERENCES customers (id) ON DELETE CASCADE, other INTEGER, "
    "total_cents INTEGER NOT NULL DEFAULT 0 CHECK (total_cents >= 0), note VARCHAR(20) DEFAULT 'x', "
    "UNIQUE (customer, note), CONSTRAINT ck_note CHECK (length(note) < 30), "
    "CONSTRAINT fk_other FOREIGN KEY (other) REFERENCES customers (id) ON UPDATE SET NULL ON DELETE NO ACTION)",
    "CREATE INDEX invoices_note ON invoices (note)",
    "CREATE UNIQUE INDEX \"invoices(lower\" ON invoices (lower(note) DESC, total_cents, 'a,)' || note)",
)


def make(path: Path, statements: tuple[str, ...] = DDL) -> Database:
    database = Database.from_url(f"sqlite:///{path}")
    for statement in statements:
        database.execute(statement)
    return database


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    database = make(tmp_path / "v2.db")
    database.execute("INSERT INTO customers (name, email) VALUES (:n, :n)", {"n": SECRET})
    yield database
    database.close()


def take(db: Database, **options: Any) -> ts.SchemaSnapshot:
    with db.inspector() as inspector:
        return ts.snapshot(inspector, **options)


def test_default_level_is_1(db: Database) -> None:
    # ADR 0015 D-4: level 2 is opt-in in 0.3.
    assert inspect.signature(ts.snapshot).parameters["level"].default == 1
    assert ts.SchemaSnapshot("sqlite", ()).level == 1
    snap = take(db)
    assert snap.level == 1 and ts.fingerprint(snap).startswith("v1:sha256:")
    invoices = snap.table("main.invoices")
    assert invoices is not None
    assert all(column.default is None for column in invoices.columns)
    assert (invoices.foreign_keys, invoices.unique_constraints, invoices.check_constraints, invoices.indexes) == (
        (),
        (),
        (),
        (),
    )


def test_v1_fingerprint_unchanged_against_02_golden(tmp_path: Path) -> None:
    # The golden was recorded with the 0.2 code of tabrion.schema before TAB-XW-2 (tests/golden/schema_v1.json).
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    for recorded in golden["snapshots"]:
        snap = ts.SchemaSnapshot.from_json(recorded["json"])
        assert snap.level == 1
        assert snap.to_json() == recorded["json"]  # byte-identical canonical JSON
        assert ts.fingerprint(snap) == recorded["fingerprint"]
    database = make(tmp_path / "golden.db", tuple(golden["sqlite_ddl"]))
    try:
        reflected = take(database)  # the DDL has defaults, constraints and an index: level 1 leaves them out
        assert reflected.to_json() == golden["snapshots"][0]["json"]
        assert ts.fingerprint(reflected) == golden["snapshots"][0]["fingerprint"]
        assert ts.fingerprint(take(database, level=2)).startswith("v2:sha256:")
    finally:
        database.close()


def test_level2_sqlite_shapes(db: Database) -> None:
    snap = take(db, level=2)
    assert snap.level == 2 and snap.dialect == "sqlite"
    customers, invoices = snap.table("main.customers"), snap.table("main.invoices")
    assert customers is not None and invoices is not None
    assert [(c.name, c.default) for c in customers.columns] == [
        ("id", None),
        ("name", None),
        ("email", None),
        ("created", "CURRENT_TIMESTAMP"),
    ]
    assert customers.unique_constraints == (ts.UniqueShape("uq_email", ("email",)), ts.UniqueShape(None, ("name",)))
    assert customers.indexes == ()  # the indexes of the unique constraints are not indexes of the shape
    assert [(c.name, c.default) for c in invoices.columns][3:] == [("total_cents", "0"), ("note", "'x'")]
    assert invoices.foreign_keys == (
        # a column-level REFERENCES keeps its ON DELETE (read from the pragma, not from SQLAlchemy's text parse)
        ts.ForeignKeyShape(None, ("customer",), "main.customers", ("id",), on_delete="CASCADE"),
        # NO ACTION, written or not, is None
        ts.ForeignKeyShape("fk_other", ("other",), "main.customers", ("id",), on_update="SET NULL"),
    )
    assert invoices.unique_constraints == (ts.UniqueShape(None, ("customer", "note")),)
    assert invoices.check_constraints == (
        ts.CheckShape(None, "total_cents >= 0"),
        ts.CheckShape("ck_note", "length(note) < 30"),
    )
    assert invoices.indexes == (
        # an expression index keeps its expression texts (SQLAlchemy would leave it out with a warning)
        ts.IndexShape("invoices(lower", ("lower(note)", "total_cents", "'a,)' || note"), unique=True),
        ts.IndexShape("invoices_note", ("note",)),
    )
    assert SECRET not in snap.to_json()  # shape only, never data


def test_level2_fingerprint_ignores_creation_order(db: Database, tmp_path: Path) -> None:
    first = ts.fingerprint(take(db, level=2))
    assert first.startswith("v2:sha256:") and len(first) == len("v2:sha256:") + 64
    assert ts.fingerprint(take(db, level=2)) == first
    other = make(tmp_path / "other.db", (DDL[1], DDL[0], DDL[3], DDL[2]))
    try:
        assert ts.fingerprint(take(other, level=2)) == first
        other.execute("CREATE INDEX invoices_other ON invoices (other)")
        assert ts.fingerprint(take(other, level=2)) != first
        assert ts.fingerprint(take(other)) == ts.fingerprint(take(db))  # level 1 does not see an index
    finally:
        other.close()


def test_level2_constraint_order_does_not_count() -> None:
    a, b = ts.UniqueShape("a", ("x",)), ts.UniqueShape(None, ("y",))
    i, j = ts.IndexShape("i", ("x",)), ts.IndexShape("j", ("y",), unique=True)
    column = ts.ColumnShape("x", "INTEGER", True)

    def model(*tables: ts.TableShape) -> ts.SchemaSnapshot:
        return ts.SchemaSnapshot("sqlite", tables, level=2)

    one = model(ts.TableShape("main.t", (column,), (), unique_constraints=(a, b), indexes=(i, j)))
    two = model(ts.TableShape("main.t", (column,), (), unique_constraints=(b, a), indexes=(j, i)))
    assert one.to_json() == two.to_json() and ts.fingerprint(one) == ts.fingerprint(two)
    assert ts.compare(one, two) == ()
    assert ts.SchemaSnapshot.from_json(two.to_json()) == ts.SchemaSnapshot.from_json(one.to_json())


def _base() -> ts.TableShape:
    return ts.TableShape(
        "main.t",
        (ts.ColumnShape("id", "INTEGER", False), ts.ColumnShape("v", "TEXT", True, default="'a'")),
        ("id",),
        foreign_keys=(ts.ForeignKeyShape("fk", ("v",), "main.o", ("k",)),),
        unique_constraints=(ts.UniqueShape(None, ("v",)),),
        check_constraints=(ts.CheckShape("ck", "id > 0"),),
        indexes=(ts.IndexShape("ix", ("v", "id")),),
    )


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (
            {"columns": (ts.ColumnShape("id", "INTEGER", False), ts.ColumnShape("v", "TEXT", True))},
            [("column_default", "v", None, True)],
        ),
        ({"foreign_keys": ()}, [("foreign_key_missing", "v", "fk", True)]),
        (
            {"foreign_keys": (ts.ForeignKeyShape("fk", ("v",), "main.o", ("k",), on_delete="CASCADE"),)},
            [("foreign_key_missing", "v", "fk", True), ("foreign_key_added", "v", "fk", True)],
        ),
        ({"unique_constraints": ()}, [("unique_missing", "v", None, True)]),
        (
            {"unique_constraints": (ts.UniqueShape(None, ("v",)), ts.UniqueShape("u2", ("id", "v")))},
            [("unique_added", None, "u2", True)],
        ),
        ({"check_constraints": ()}, [("check_missing", None, "ck", True)]),
        (
            {"check_constraints": (ts.CheckShape("ck", "id > 0"), ts.CheckShape(None, "v <> ''"))},
            [("check_added", None, None, True)],
        ),
        ({"indexes": ()}, [("index_missing", None, "ix", True)]),
        (
            {"indexes": (ts.IndexShape("ix", ("v", "id")), ts.IndexShape("iy", ("id",), unique=True))},
            [("index_added", "id", "iy", False)],  # only an added index is not breaking
        ),
    ],
)
def test_level2_each_difference_kind(
    change: dict[str, Any], expected: list[tuple[str, str | None, str | None, bool]]
) -> None:
    before = ts.SchemaSnapshot("sqlite", (_base(),), level=2)
    after = ts.SchemaSnapshot("sqlite", (dataclasses.replace(_base(), **change),), level=2)
    differences = ts.compare(before, after)
    assert [(d.kind, d.column, d.name, d.breaking) for d in differences] == expected
    assert all(d.table == "main.t" for d in differences)
    assert ts.fingerprint(before) != ts.fingerprint(after)
    assert ts.compare(before, before) == ()


def test_level2_difference_text_names_the_constraint() -> None:
    assert str(ts.SchemaDifference("index_missing", "main.t", "v", "ix")) == "index_missing: main.t.v (ix)"
    assert str(ts.SchemaDifference("check_added", "main.t")) == "check_added: main.t"
    assert str(ts.SchemaDifference("column_type", "main.t", "v")) == "column_type: main.t.v"  # level 1 unchanged


def test_level2_reflected_drift(db: Database, tmp_path: Path) -> None:
    expected = take(db, level=2)
    changed = make(
        tmp_path / "changed.db",
        (
            DDL[0].replace("DEFAULT CURRENT_TIMESTAMP", "DEFAULT '2026'"),
            DDL[1].replace(" ON DELETE CASCADE", "").replace("CONSTRAINT ck_note CHECK (length(note) < 30), ", ""),
            DDL[3],
            "CREATE INDEX invoices_other ON invoices (other)",
        ),
    )
    try:
        actual = take(changed, level=2)
    finally:
        changed.close()
    assert [str(d) for d in ts.compare(expected, actual)] == [
        "column_default: main.customers.created",
        "foreign_key_missing: main.invoices.customer",
        "foreign_key_added: main.invoices.customer",
        "check_missing: main.invoices (ck_note)",
        "index_missing: main.invoices.note (invoices_note)",
        "index_added: main.invoices.other (invoices_other)",
    ]


def test_level_checks() -> None:
    with pytest.raises(UsageError) as info:
        ts.compare(ts.SchemaSnapshot("sqlite", ()), ts.SchemaSnapshot("sqlite", (), level=2))
    assert info.value.code == "schema.level_mismatch"
    for bad in (0, 3, True, "2", 2.0):
        with pytest.raises(UsageError) as info:
            ts.SchemaSnapshot("sqlite", (), level=bad)  # type: ignore[arg-type]
        assert info.value.code == "schema.invalid_level"
    # a level-1 snapshot holds no level-2 detail, so its v1 fingerprint never hides one
    for detail in (
        dataclasses.replace(_base(), columns=(ts.ColumnShape("id", "INTEGER", False, default="1"),)),
        dataclasses.replace(_base(), foreign_keys=(), unique_constraints=(), check_constraints=()),
    ):
        with pytest.raises(UsageError) as info:
            ts.SchemaSnapshot("sqlite", (detail,))
        assert info.value.code == "schema.level_mismatch"


def test_snapshot_refuses_an_invalid_level(db: Database) -> None:
    with pytest.raises(UsageError) as info:
        take(db, level=3)
    assert info.value.code == "schema.invalid_level"


def test_level2_json_round_trip(db: Database) -> None:
    snap = take(db, level=2)
    text = snap.to_json()
    assert json.loads(text)["format"] == 2
    assert ts.SchemaSnapshot.from_json(text) == snap
    assert ts.fingerprint(ts.SchemaSnapshot.from_json(text)) == ts.fingerprint(snap)
    model = ts.SchemaSnapshot("postgresql", (_base(),), level=2)  # a target model built by hand
    assert ts.SchemaSnapshot.from_json(model.to_json()) == model


def test_level2_from_json_refuses_loose_types() -> None:
    good = json.loads(ts.SchemaSnapshot("sqlite", (_base(),), level=2).to_json())

    def bad(path: tuple[Any, ...], value: Any) -> str:
        data = json.loads(json.dumps(good))
        target = data
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        return json.dumps(data)

    table = ("tables", 0)
    for text in (
        bad((*table, "columns", 1, 3), 5),
        bad((*table, "columns", 1, 2), "yes"),
        bad((*table, "foreign_keys", 0, "columns"), "v"),
        bad((*table, "foreign_keys", 0, "on_delete"), 1),
        bad((*table, "unique_constraints"), {}),
        bad((*table, "check_constraints", 0, "sqltext"), None),
        bad((*table, "indexes", 0, "unique"), 0),
        bad((*table, "indexes", 0, "name"), None),
        bad(("dialect",), None),
        f'{{"format": 2, "dialect": "{SECRET}"}}',
        '{"format": 3, "dialect": "sqlite", "tables": []}',
    ):
        with pytest.raises(UsageError) as info:
            ts.SchemaSnapshot.from_json(text)
        assert info.value.code == "schema.invalid_snapshot"
        assert info.value.__context__ is None and SECRET not in repr(info.value)


def test_level2_target_model_by_replace(db: Database) -> None:
    # Addendum A.1: a level-2 snapshot is the target model; a consumer edits it with dataclasses.replace.
    snap = take(db, level=2)
    customers = snap.table("main.customers")
    assert customers is not None
    edited = dataclasses.replace(customers, indexes=(ts.IndexShape("customers_email", ("email",)),))
    target = dataclasses.replace(snap, tables=tuple(edited if t is customers else t for t in snap.tables))
    assert [str(d) for d in ts.compare(snap, target)] == ["index_added: main.customers.email (customers_email)"]


def test_level2_with_an_engine_inspector(tmp_path: Path) -> None:
    # sqlalchemy.inspect(engine) works too: the pragma queries open their own connection.
    make(tmp_path / "engine.db").close()
    engine = sqlalchemy.create_engine(f"sqlite:///{tmp_path / 'engine.db'}")
    try:
        snap = ts.snapshot(sqlalchemy.inspect(engine), level=2)
    finally:
        engine.dispose()
    invoices = snap.table("main.invoices")
    assert invoices is not None and len(invoices.indexes) == 2 and invoices.foreign_keys[0].on_delete == "CASCADE"


@pytest.mark.parametrize(
    ("sql", "elements"),
    [
        ("CREATE INDEX i ON t (a, b)", ["a", "b"]),
        ('CREATE INDEX "i(" ON "t(" (lower("a)") DESC, b ASC)', ['lower("a)")', "b"]),
        ("CREATE INDEX [i(] ON t (a /* (, */, -- x(\n b) WHERE a > 0", ["a /* (, */", "-- x(\n b"]),
        (
            "CREATE INDEX i ON t (coalesce(a, 'it''s'), b collate nocase desc)",
            ["coalesce(a, 'it''s')", "b collate nocase"],
        ),
        ("CREATE INDEX i ON t", None),
        ("CREATE INDEX 'i ON t (a)", None),
    ],
)
def test_sqlite_index_elements(sql: str, elements: list[str] | None) -> None:
    assert ts._sqlite_index_elements(sql) == elements
