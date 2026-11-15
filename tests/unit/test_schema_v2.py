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
    # read from the pragmas: SQLite keeps no unique constraint name it can address (review of #85, F3)
    assert customers.unique_constraints == (ts.UniqueShape(None, ("email",)), ts.UniqueShape(None, ("name",)))
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
        ts.IndexShape(
            "invoices(lower",
            ("lower(note)", "total_cents", "'a,)' || note"),
            unique=True,
            ordering=("DESC", "", ""),
            expressions=(0, 2),
        ),
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
        dataclasses.replace(_base(), columns=(ts.ColumnShape("id", "INTEGER", False, identity="ALWAYS"),)),
        dataclasses.replace(_base(), columns=(ts.ColumnShape("id", "INTEGER", False, generated="STORED"),)),
        dataclasses.replace(_base(), columns=(ts.ColumnShape("id", "INTEGER", False, generation_expression="1"),)),
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
        bad((*table, "columns", 1, 4), 5),
        bad((*table, "columns", 1, 6), False),
        bad((*table, "columns", 1), ["v", "TEXT", True, None]),  # the four entries of a column before review M-2
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
    ("sql", "parts"),
    [
        ("CREATE INDEX i ON t (a, b)", (["a", "b"], "")),
        ('CREATE INDEX "i(" ON "t(" (lower("a)") DESC, b ASC)', (['lower("a)") DESC', "b ASC"], "")),
        ("CREATE INDEX [i(] ON t (a /* (, */, -- x(\n b) WHERE a > 0", (["a /* (, */", "-- x(\n b"], " WHERE a > 0")),
        (
            "CREATE INDEX i ON t (coalesce(a, 'it''s'), b collate nocase desc)",
            (["coalesce(a, 'it''s')", "b collate nocase desc"], ""),
        ),
        ("CREATE INDEX i ON t", None),
        ("CREATE INDEX 'i ON t (a)", None),
    ],
)
def test_sqlite_index_parts(sql: str, parts: tuple[list[str], str] | None) -> None:
    assert ts._sqlite_index_parts(sql) == parts


# --- detail added after the review of the draft (coordinator, open question 1): a change only in one attribute
# changes the v2 fingerprint and is named by compare; level 1 does not see it.


def _detail_table(**index: Any) -> ts.TableShape:
    return ts.TableShape(
        "main.t",
        (ts.ColumnShape("a", "INTEGER", True), ts.ColumnShape("b", "TEXT", True)),
        (),
        foreign_keys=(ts.ForeignKeyShape("fk", ("a",), "main.o", ("k",)),),
        unique_constraints=(ts.UniqueShape("u", ("a", "b")),),
        check_constraints=(ts.CheckShape("ck", "a > 0"),),
        indexes=(ts.IndexShape("ix", ("a", "b"), **index),),
    )


@pytest.mark.parametrize(
    ("part", "change", "kinds"),
    [
        ("index", {"ordering": ("DESC", "")}, ["index_missing", "index_added"]),
        ("index", {"ordering": ("", "NULLS LAST")}, ["index_missing", "index_added"]),
        ("index", {"where": "a > 0"}, ["index_missing", "index_added"]),
        ("index", {"include": ("b",)}, ["index_missing", "index_added"]),
        ("index", {"nulls_not_distinct": True}, ["index_missing", "index_added"]),
        ("unique", {"include": ("b",)}, ["unique_missing", "unique_added"]),
        ("unique", {"nulls_not_distinct": True}, ["unique_missing", "unique_added"]),
        ("unique", {"deferrable": True}, ["unique_missing", "unique_added"]),
        ("unique", {"deferrable": True, "initially_deferred": True}, ["unique_missing", "unique_added"]),
        ("fk", {"deferrable": True}, ["foreign_key_missing", "foreign_key_added"]),
        ("fk", {"deferrable": True, "initially_deferred": True}, ["foreign_key_missing", "foreign_key_added"]),
        # review of #85, F2 and F4
        ("index", {"expressions": (1,)}, ["index_missing", "index_added"]),
        ("index", {"collations": ("", "NOCASE")}, ["index_missing", "index_added"]),
        ("index", {"opclasses": ("", "text_pattern_ops")}, ["index_missing", "index_added"]),
        ("index", {"method": "hash"}, ["index_missing", "index_added"]),
        ("index", {"valid": False}, ["index_missing", "index_added"]),
        ("unique", {"collations": ("", "NOCASE")}, ["unique_missing", "unique_added"]),
        ("fk", {"match": "FULL"}, ["foreign_key_missing", "foreign_key_added"]),
        ("fk", {"validated": False}, ["foreign_key_missing", "foreign_key_added"]),
        ("check", {"validated": False}, ["check_missing", "check_added"]),
        ("exclusion", {}, ["exclusion_added"]),
    ],
)
def test_level2_detail_changes_the_v2_fingerprint(part: str, change: dict[str, Any], kinds: list[str]) -> None:
    base = _detail_table()
    if part == "index":
        changed = dataclasses.replace(base, indexes=(dataclasses.replace(base.indexes[0], **change),))
    elif part == "unique":
        changed = dataclasses.replace(
            base, unique_constraints=(dataclasses.replace(base.unique_constraints[0], **change),)
        )
    elif part == "check":
        changed = dataclasses.replace(
            base, check_constraints=(dataclasses.replace(base.check_constraints[0], **change),)
        )
    elif part == "exclusion":
        changed = dataclasses.replace(
            base, exclusion_constraints=(ts.ExclusionShape("ex", "EXCLUDE USING gist (r WITH &&)"),)
        )
    else:
        changed = dataclasses.replace(base, foreign_keys=(dataclasses.replace(base.foreign_keys[0], **change),))
    before, after = (ts.SchemaSnapshot("sqlite", (t,), level=2) for t in (base, changed))
    assert ts.fingerprint(before) != ts.fingerprint(after)
    assert [d.kind for d in ts.compare(before, after)] == kinds
    assert ts.SchemaSnapshot.from_json(after.to_json()) == after


def test_level2_default_ordering_is_canonical() -> None:
    # ("", "") and () both mean "every element in the default order": same JSON, same fingerprint, no difference.
    one = ts.SchemaSnapshot("sqlite", (_detail_table(ordering=("", "")),), level=2)
    two = ts.SchemaSnapshot("sqlite", (_detail_table(),), level=2)
    assert one.to_json() == two.to_json() and ts.compare(one, two) == ()


SQLITE_DETAIL = (
    "CREATE TABLE p (id INTEGER PRIMARY KEY)",
    "CREATE TABLE t (a INTEGER, b TEXT, CONSTRAINT fk FOREIGN KEY (a) REFERENCES p (id))",
    "CREATE INDEX ix ON t (a, b)",
)


@pytest.mark.parametrize(
    ("statements", "expected"),
    [
        (
            ("CREATE INDEX ix ON t (a DESC, b)",),
            ts.IndexShape("ix", ("a", "b"), ordering=("DESC", "")),
        ),
        (
            ("CREATE INDEX ix ON t (a, b) WHERE a > 0 AND b IS NOT NULL",),
            ts.IndexShape("ix", ("a", "b"), where="a > 0 AND b IS NOT NULL"),
        ),
    ],
)
def test_sqlite_index_detail(tmp_path: Path, statements: tuple[str, ...], expected: ts.IndexShape) -> None:
    before_db = make(tmp_path / "before.db", SQLITE_DETAIL)
    after_db = make(tmp_path / "after.db", (*SQLITE_DETAIL[:2], *statements))
    try:
        before, after = take(before_db, level=2), take(after_db, level=2)
        assert ts.fingerprint(take(before_db)) == ts.fingerprint(take(after_db))  # v1 unchanged
    finally:
        before_db.close()
        after_db.close()
    table = after.table("main.t")
    assert table is not None and table.indexes == (expected,)
    assert ts.fingerprint(before) != ts.fingerprint(after)
    assert [str(d) for d in ts.compare(before, after)] == ["index_missing: main.t (ix)", "index_added: main.t (ix)"]


@pytest.mark.parametrize(
    ("clause", "deferrable", "initially_deferred"),
    [(" DEFERRABLE", True, False), (" DEFERRABLE INITIALLY DEFERRED", True, True)],
)
def test_sqlite_foreign_key_deferrable(tmp_path: Path, clause: str, deferrable: bool, initially_deferred: bool) -> None:
    # A table-level FOREIGN KEY clause; a column-level REFERENCES ... DEFERRABLE is a documented gap (I-111 d).
    before_db = make(tmp_path / "before.db", SQLITE_DETAIL)
    after_db = make(tmp_path / "after.db", (SQLITE_DETAIL[0], SQLITE_DETAIL[1].replace("(id))", f"(id){clause})")))
    try:
        before, after = take(before_db, level=2), take(after_db, level=2)
        assert ts.fingerprint(take(before_db)) == ts.fingerprint(take(after_db))
    finally:
        before_db.close()
        after_db.close()
    table = after.table("main.t")
    assert table is not None
    assert table.foreign_keys == (
        ts.ForeignKeyShape(
            "fk", ("a",), "main.p", ("id",), deferrable=deferrable, initially_deferred=initially_deferred
        ),
    )
    assert ts.fingerprint(before) != ts.fingerprint(after)


def test_from_json_refuses_a_format_that_is_not_an_int() -> None:
    # review of #85, F6: True == 1 and 2.0 == 2 in Python, but neither is a format.
    for form in ("true", "1.0", "2.0"):
        with pytest.raises(UsageError) as info:
            ts.SchemaSnapshot.from_json(f'{{"format": {form}, "dialect": "sqlite", "tables": []}}')
        assert info.value.code == "schema.invalid_snapshot"


def test_from_json_refuses_an_expression_position_out_of_range() -> None:
    data = json.loads(ts.SchemaSnapshot("sqlite", (_detail_table(),), level=2).to_json())
    data["tables"][0]["indexes"][0]["expressions"] = [2]
    with pytest.raises(UsageError):
        ts.SchemaSnapshot.from_json(json.dumps(data))
    data["tables"][0]["indexes"][0]["expressions"] = [True]
    with pytest.raises(UsageError):
        ts.SchemaSnapshot.from_json(json.dumps(data))


def _index_of(tmp_path: Path, *statements: str) -> ts.IndexShape:
    database = make(tmp_path / "ix.db", ("CREATE TABLE t (x INTEGER, y INTEGER)", *statements))
    try:
        table = take(database, level=2).table("main.t")
    finally:
        database.close()
    assert table is not None
    (index,) = table.indexes
    return index


def test_sqlite_long_whitespace_is_parsed_in_linear_time(tmp_path: Path) -> None:
    # review of #85, F1: _ORDER_SUFFIX and the WHERE pattern were quadratic on a long run of blanks; a hostile file
    # with 32 000 blanks took seconds. Now plain string methods: 5 runs of 15 000 blanks (under the limit) are parsed.
    blanks = " " * 15_000
    index = _index_of(tmp_path, f"CREATE INDEX i ON t (x +{blanks}y{blanks}DESC{blanks}) WHERE{blanks}x > 0{blanks}")
    assert index.columns == (f"x +{blanks}y",) and index.expressions == (0,)
    assert index.ordering == ("DESC",) and index.where == "x > 0"


def test_sqlite_index_text_over_the_limit_stands_whole(tmp_path: Path) -> None:
    # Above _MAX_INDEX_SQL (100 000 characters) the text is not split: the whole statement stands for the expression
    # and the predicate, so the fingerprint still follows every change of it.
    statement = f"CREATE INDEX i ON t (x +{' ' * 1_000_000}y) WHERE x > 0"
    index = _index_of(tmp_path, statement)
    assert index.columns == (statement,) and index.where == statement and index.expressions == (0,)


def test_sqlite_column_and_expression_of_the_same_text_differ(tmp_path: Path) -> None:
    # review of #85, F4: a column named "lower(a)" and the expression lower(a) give different v2 fingerprints.
    def snap(index: str) -> ts.SchemaSnapshot:
        database = make(tmp_path / f"{len(index)}.db", ('CREATE TABLE t (a TEXT, "lower(a)" TEXT)', index))
        try:
            return take(database, level=2)
        finally:
            database.close()

    column, expression = snap('CREATE INDEX i ON t ("lower(a)")'), snap("CREATE INDEX i ON t (lower(a) )")
    assert column.tables[0].indexes[0].columns == expression.tables[0].indexes[0].columns == ("lower(a)",)
    assert ts.fingerprint(column) != ts.fingerprint(expression)
    assert [d.kind for d in ts.compare(column, expression)] == ["index_missing", "index_added"]


def test_sqlite_collations_of_unique_index_and_constraint(tmp_path: Path) -> None:
    # review of #85, F2: a unique index or constraint that turns case-insensitive changes the v2 fingerprint.
    def snap(name: str, *statements: str) -> ts.SchemaSnapshot:
        database = make(tmp_path / name, statements)
        try:
            assert ts.fingerprint(take(database)).startswith("v1:")
            return take(database, level=2)
        finally:
            database.close()

    plain = snap("a.db", "CREATE TABLE t (a TEXT, b TEXT, UNIQUE (a))", "CREATE UNIQUE INDEX i ON t (b)")
    nocase = snap(
        "b.db",
        "CREATE TABLE t (a TEXT, b TEXT, UNIQUE (a COLLATE NOCASE))",
        "CREATE UNIQUE INDEX i ON t (b COLLATE NOCASE)",
    )
    assert plain.tables[0].unique_constraints == (ts.UniqueShape(None, ("a",)),)
    assert nocase.tables[0].unique_constraints == (ts.UniqueShape(None, ("a",), collations=("NOCASE",)),)
    assert nocase.tables[0].indexes == (ts.IndexShape("i", ("b",), unique=True, collations=("NOCASE",)),)
    assert ts.fingerprint(plain) != ts.fingerprint(nocase)
    assert [d.kind for d in ts.compare(plain, nocase)] == [
        "unique_missing",
        "unique_added",
        "index_missing",
        "index_added",
    ]


def test_sqlite_duplicate_foreign_keys_keep_their_actions(tmp_path: Path) -> None:
    # review of #85, F5: two keys on the same columns; SQLAlchemy reports only one (with a warning), the pragma both.
    database = make(
        tmp_path / "dup.db",
        (
            "CREATE TABLE p (a INTEGER PRIMARY KEY)",
            "CREATE TABLE t (a INTEGER, CONSTRAINT f1 FOREIGN KEY (a) REFERENCES p (a) ON DELETE CASCADE, "
            "CONSTRAINT f2 FOREIGN KEY (a) REFERENCES p (a))",
        ),
    )
    try:
        with pytest.warns(sqlalchemy.exc.SAWarning):
            table = take(database, level=2).table("main.t")
    finally:
        database.close()
    assert table is not None
    # ambiguous: no name is guessed, and each key keeps its own action
    assert table.foreign_keys == (
        ts.ForeignKeyShape(None, ("a",), "main.p", ("a",), on_delete="CASCADE"),
        ts.ForeignKeyShape(None, ("a",), "main.p", ("a",)),
    )


def test_level2_snapshot_leaves_the_warning_filters_alone(db: Database) -> None:
    # review of #85, F3: no catch_warnings (process-wide state); the fixture has an expression index.
    import warnings

    before = list(warnings.filters)
    take(db, level=2)
    assert warnings.filters == before


# SQLite CHECK constraints are read with Tabrion's own tokenizer: SQLAlchemy 2.0.30 (the floor of the extra) reads
# them with a greedy regular expression, so two checks became one text (CI job "lowest supported dependency
# versions" on #85). These tests run on every SQLAlchemy version.
CHECK_TABLE = (
    'CREATE TABLE "t(" (\n'
    "  a INTEGER CHECK (a > (0 + (1))) CONSTRAINT [ck)a] CHECK (a < 100),\n"
    "  b TEXT DEFAULT ')' CHECK (b <> ')' AND b <> 'it''s)'),\n"
    '  "check)" INTEGER /* CHECK (no) ) */ CHECK ("check)" >= 0), -- CHECK (nor this) )\n'
    "  c INTEGER,\n"
    '  CONSTRAINT "ck ""c""" CHECK (c IN (1, 2, 3)),\n'
    "  CHECK (c IS NULL OR a IS NOT NULL),\n"
    "  CONSTRAINT `ck_b` CHECK (length(b) < 30)\n"
    ")"
)
CHECKS = [
    (None, "a > (0 + (1))"),
    ("ck)a", "a < 100"),
    (None, "b <> ')' AND b <> 'it''s)'"),
    (None, '"check)" >= 0'),
    ('ck "c"', "c IN (1, 2, 3)"),
    (None, "c IS NULL OR a IS NOT NULL"),
    ("ck_b", "length(b) < 30"),
]


def test_sqlite_check_constraints_parsed() -> None:
    assert ts._sqlite_check_constraints(CHECK_TABLE) == CHECKS
    assert ts._sqlite_check_constraints("CREATE TABLE t (a INTEGER)") == []
    for bad in ("CREATE TABLE t (a CHECK (a > 0)", "CREATE TABLE t (a TEXT DEFAULT 'x)", "CREATE TABLE t (a))"):
        assert ts._sqlite_check_constraints(bad) is None


def test_sqlite_check_constraints_reflected(tmp_path: Path) -> None:
    database = make(tmp_path / "checks.db", (CHECK_TABLE,))
    try:
        table = take(database, level=2).table("main.t(")
    finally:
        database.close()
    assert table is not None
    expected = tuple(sorted((ts.CheckShape(n, t) for n, t in CHECKS), key=lambda c: (c.name or "", c.sqltext)))
    assert table.check_constraints == expected


def test_sqlite_check_text_is_linear_and_bounded(tmp_path: Path) -> None:
    # As F1: long blank runs are read in linear time; a CREATE TABLE text over 1 000 000 characters stands whole.
    blanks = " " * 200_000
    assert ts._sqlite_check_constraints(f"CREATE TABLE t (a INTEGER CHECK ({blanks}a > 0{blanks}))") == [
        (None, "a > 0")
    ]
    statement = f"CREATE TABLE t (a INTEGER CHECK (a >{' ' * 1_000_000}0))"
    assert ts._sqlite_check_constraints(statement) is None
    database = make(tmp_path / "long.db", (statement,))
    try:
        table = take(database, level=2).table("main.t")
    finally:
        database.close()
    assert table is not None and table.check_constraints == (ts.CheckShape(None, statement),)


# --- review of #87: names with a dot (M-1), identity and generated columns (M-2) -----------------------------------


def test_level2_refuses_a_name_with_a_dot_sqlite(tmp_path: Path) -> None:
    """The reviewer's probe on SQLite: table ``x.y`` of schema ``s`` and table ``y`` of the attached schema ``s.x``
    are both ``s.x.y``. Level 2 refuses the name instead of letting one table hide the other; level 1 keeps the 0.2
    behavior (I-111 g), where one of them is left out."""
    engine = sqlalchemy.create_engine(f"sqlite:///{tmp_path / 'main.db'}")
    try:
        with engine.connect() as connection:
            for name, file in (("s", "s.db"), ("s.x", "sx.db")):
                connection.exec_driver_sql(f"ATTACH DATABASE '{tmp_path / file}' AS \"{name}\"")
            connection.exec_driver_sql('CREATE TABLE s."x.y" (a INTEGER)')
            connection.exec_driver_sql('CREATE TABLE "s.x".y (b INTEGER)')
            inspector = sqlalchemy.inspect(connection)
            assert [t.name for t in ts.snapshot(inspector, schemas=["s", "s.x"]).tables] == ["s.x.y"]
            for options in ({"schemas": ["s"]}, {"schemas": ["s.x"]}, {"tables": ["s.x.y"]}):
                with pytest.raises(UsageError) as info:
                    ts.snapshot(inspector, level=2, **options)
                assert info.value.code == "schema.unsupported_name"
    finally:
        engine.dispose()


def test_level2_model_refuses_an_ambiguous_name() -> None:
    table = dataclasses.replace(_base(), name="s.x.y")
    referring = dataclasses.replace(_base(), foreign_keys=(ts.ForeignKeyShape("fk", ("v",), "s.x.o", ("k",)),))
    for shape in (table, referring):
        with pytest.raises(UsageError) as info:
            ts.SchemaSnapshot("postgresql", (shape,), level=2)
        assert info.value.code == "schema.unsupported_name"
    text = ts.SchemaSnapshot("postgresql", (_base(),), level=2).to_json().replace('"main.t"', '"s.x.y"')
    with pytest.raises(UsageError) as info:
        ts.SchemaSnapshot.from_json(text)
    assert info.value.code == "schema.invalid_snapshot"
    # level 1 keeps the 0.2 behavior: a name is any text (D-4)
    level1 = ts.TableShape("s.x.y", (ts.ColumnShape("id", "INTEGER", False),), ("id",))
    assert ts.SchemaSnapshot("postgresql", (level1,)).tables == (level1,)


GENERATED = (
    "CREATE TABLE g (id INTEGER PRIMARY KEY, a INTEGER, b INTEGER GENERATED ALWAYS AS (a * 2) STORED, "
    "\"V, v\" INTEGER AS (a + (1)) VIRTUAL, c TEXT DEFAULT 'AS (x)', CHECK (a > 0))"
)


def test_sqlite_generated_columns(tmp_path: Path) -> None:
    """Generated columns are in the level-2 shape (``pragma_table_xinfo`` hidden 3 and 2, the expression as
    written), so the ``v2:`` fingerprint and :func:`compare` see them (review of #87, M-2)."""
    database = make(tmp_path / "g.db", (GENERATED,))
    try:
        snap = take(database, level=2)
        assert take(database).tables[0].columns[2] == ts.ColumnShape("b", "INTEGER", True)  # level 1 unchanged
    finally:
        database.close()
    table = snap.tables[0]
    assert [(c.name, c.generated, c.generation_expression, c.identity) for c in table.columns] == [
        ("id", None, None, None),
        ("a", None, None, None),
        ("b", "STORED", "a * 2", None),
        ("V, v", "VIRTUAL", "a + (1)", None),
        ("c", None, None, None),
    ]
    assert table.columns[4].default == "'AS (x)'"
    assert table.check_constraints == (ts.CheckShape(None, "a > 0"),)
    plain = dataclasses.replace(
        table,
        columns=tuple(dataclasses.replace(c, generated=None, generation_expression=None) for c in table.columns),
    )
    other = ts.SchemaSnapshot("sqlite", (plain,), level=2)
    assert ts.fingerprint(other) != ts.fingerprint(snap)
    assert [str(d) for d in ts.compare(snap, other)] == [
        "column_generated: main.g.b",
        "column_generated: main.g.V, v",
    ]
    assert ts.SchemaSnapshot.from_json(snap.to_json()) == snap


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("CREATE TABLE t (a INT, b INT AS (a+1))", {"b": "a+1"}),
        ('CREATE TABLE t ("x y" INT GENERATED ALWAYS AS ( f(a, (b)) ) STORED, c INT)', {"x y": "f(a, (b))"}),
        ("CREATE TABLE t (a INT CHECK (CAST(a AS INT) > 0), b TEXT DEFAULT ('AS (1)'))", {}),
        ("CREATE TABLE t (a INT AS (a", None),
        ("CREATE TABLE t (a INT AS (')", None),
        ("CREATE TABLE t (a INT)) AS (1)", None),
    ],
)
def test_sqlite_generated_expressions(sql: str, expected: dict[str, str] | None) -> None:
    from tabrion.schema import _sqlite_generated_expressions

    assert _sqlite_generated_expressions(sql) == expected


@pytest.mark.parametrize(
    ("change", "kind"),
    [
        ({"identity": "ALWAYS"}, "column_identity"),
        ({"identity": "BY DEFAULT"}, "column_identity"),
        ({"generated": "STORED", "generation_expression": "id * 2"}, "column_generated"),
        ({"generated": "VIRTUAL", "generation_expression": "id * 2"}, "column_generated"),
    ],
)
def test_level2_identity_and_generated_change_the_v2_fingerprint(change: dict[str, Any], kind: str) -> None:
    base = _base()
    changed = dataclasses.replace(base, columns=(dataclasses.replace(base.columns[0], **change), base.columns[1]))
    before, after = (ts.SchemaSnapshot("postgresql", (t,), level=2) for t in (base, changed))
    assert ts.fingerprint(before) != ts.fingerprint(after)
    assert [(d.kind, d.column, d.breaking) for d in ts.compare(before, after)] == [(kind, "id", True)]
    assert ts.SchemaSnapshot.from_json(after.to_json()) == after


# --- TAB-XW-4, ADR 0015 D-5: triggers, policies, functions, features (contract I-111 i) -------------------------------


def test_level2_base_fingerprint_unchanged_by_the_d5_fields() -> None:
    # The D-5 keys are written only when not empty: a table without them keeps the v2 fingerprint it had before
    # TAB-XW-4 (values computed with the schema module of master 948fc80).
    assert ts.fingerprint(ts.SchemaSnapshot("sqlite", (_base(),), level=2)) == (
        "v2:sha256:c08a2fb4b81e5457b66a8aaf67d916f821e0fae6f8ff48c39606908e5f56df73"
    )
    assert ts.fingerprint(ts.SchemaSnapshot("postgresql", (_base(),), level=2)) == (
        "v2:sha256:5eb8ba77f73606ffa9b6cc840f64ebf51e5e24a09ebb7dfd4c79f148938a46ca"
    )
    text = ts.SchemaSnapshot("sqlite", (_base(),), level=2).to_json()
    for key in ("triggers", "policies", "functions", "row_security", "features"):
        assert f'"{key}"' not in text


def test_sqlite_triggers_reflected(tmp_path: Path) -> None:
    trigger = "CREATE TRIGGER t_log AFTER INSERT ON t BEGIN UPDATE t SET v = v WHERE id = new.id; END"
    database = make(tmp_path / "trg.db", ("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)", trigger))
    try:
        snapshot = take(database, level=2)
        assert snapshot.tables[0].triggers == (ts.TriggerShape("t_log", trigger),)
        assert snapshot.tables[0].features == ()
        assert take(database).tables[0].triggers == ()  # level 1 never reads them
        database.execute("DROP TRIGGER t_log")
        after = take(database, level=2)
        assert [(d.kind, d.name) for d in ts.compare(snapshot, after)] == [("trigger_missing", "t_log")]
        assert ts.fingerprint(snapshot) != ts.fingerprint(after)
    finally:
        database.close()


@pytest.mark.parametrize(
    ("sql", "features"),
    [
        ("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)", ()),
        ("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT) STRICT", ("strict",)),
        ("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT) WITHOUT ROWID", ("without_rowid",)),
        ("CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, v TEXT)", ("autoincrement",)),
        ("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT NOT NULL ON CONFLICT REPLACE)", ("on_conflict",)),
        ("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT COLLATE NOCASE)", ("collation",)),
        ("CREATE TABLE t (id INTEGER, v TEXT, PRIMARY KEY (v COLLATE NOCASE))", ("collation",)),
        ("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT, UNIQUE (v COLLATE NOCASE))", ()),
        ("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT CHECK (v COLLATE NOCASE <> 'x'))", ()),
        (
            "CREATE TABLE t (id INTEGER PRIMARY KEY, v INTEGER REFERENCES t (id) DEFERRABLE INITIALLY DEFERRED)",
            ("deferrable_reference",),
        ),
        (
            "CREATE TABLE t (id INTEGER PRIMARY KEY, v INTEGER, "
            "FOREIGN KEY (v) REFERENCES t (id) DEFERRABLE INITIALLY DEFERRED)",
            (),
        ),
        ("CREATE TABLE t (id INTEGER PRIMARY KEY, v INTEGER REFERENCES t (id) NOT DEFERRABLE)", ()),
    ],
)
def test_sqlite_table_features_reflected(tmp_path: Path, sql: str, features: tuple[str, ...]) -> None:
    database = make(tmp_path / "f.db", (sql,))
    try:
        assert take(database, level=2).tables[0].features == features
        assert take(database).tables[0].features == ()
    finally:
        database.close()


def test_sqlite_virtual_table_feature(tmp_path: Path) -> None:
    database = make(tmp_path / "v.db", ())
    try:
        try:
            database.execute("CREATE VIRTUAL TABLE vt USING fts5(body)")
        except Exception:  # not every SQLite build has FTS5; fall back to the R*Tree module
            database.execute("CREATE VIRTUAL TABLE vt USING rtree(id, x0, x1)")
        tables = {t.name: t for t in take(database, level=2).tables}
        assert "virtual_table" in tables["main.vt"].features
    finally:
        database.close()


@pytest.mark.parametrize("sql", ["", "CREATE TABLE t (id INTEGER", "x" * 70_000])
def test_sqlite_unparsed_table_sql(sql: str) -> None:
    assert ts._sqlite_features(sql, 0) == ("unparsed_table_sql",)


def _d5_table(**change: Any) -> ts.TableShape:
    return dataclasses.replace(_base(), **change)


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"triggers": (ts.TriggerShape("tr", "CREATE TRIGGER tr ..."),)}, [("trigger_added", "tr")]),
        ({"policies": (ts.PolicyShape("p", "ALL", True, ("PUBLIC",)),)}, [("policy_added", "p")]),
        (
            {"functions": (ts.FunctionShape("public.f()", "plpgsql", "CREATE FUNCTION ..."),)},
            [("function_added", "public.f()")],
        ),
        ({"row_security": True}, [("row_security", None)]),
        ({"force_row_security": True}, [("row_security", None)]),
        ({"features": ("partitioned",)}, [("feature_added", "partitioned")]),
    ],
)
def test_level2_d5_difference_kinds(change: dict[str, Any], expected: list[tuple[str, str | None]]) -> None:
    before = ts.SchemaSnapshot("postgresql", (_base(),), level=2)
    after = ts.SchemaSnapshot("postgresql", (_d5_table(**change),), level=2)
    differences = ts.compare(before, after)
    assert [(d.kind, d.name) for d in differences] == expected
    assert all(d.breaking and d.table == "main.t" for d in differences)
    assert [(d.kind.replace("_added", "_missing"), d.name) for d in ts.compare(after, before)] == [
        (k.replace("_added", "_missing"), n) for k, n in expected
    ]
    assert ts.fingerprint(before) != ts.fingerprint(after)
    assert ts.SchemaSnapshot.from_json(after.to_json()) == after


def test_level2_changed_trigger_or_function_body_is_a_difference() -> None:
    one = _d5_table(
        triggers=(ts.TriggerShape("tr", "CREATE TRIGGER tr A"),),
        functions=(ts.FunctionShape("public.f()", "plpgsql", "BODY 1"),),
    )
    two = _d5_table(
        triggers=(ts.TriggerShape("tr", "CREATE TRIGGER tr A", enabled="DISABLED"),),
        functions=(ts.FunctionShape("public.f()", "plpgsql", "BODY 2"),),
    )
    a, b = (ts.SchemaSnapshot("postgresql", (t,), level=2) for t in (one, two))
    assert [(d.kind, d.name) for d in ts.compare(a, b)] == [
        ("trigger_missing", "tr"),
        ("trigger_added", "tr"),
        ("function_missing", "public.f()"),
        ("function_added", "public.f()"),
    ]
    assert ts.fingerprint(a) != ts.fingerprint(b)


def test_level2_database_features() -> None:
    plain = ts.SchemaSnapshot("postgresql", (_base(),), level=2)
    hooked = ts.SchemaSnapshot("postgresql", (_base(),), level=2, features=("event_trigger:audit",))
    assert [(d.kind, d.table, d.name) for d in ts.compare(plain, hooked)] == [
        ("feature_added", "", "event_trigger:audit")
    ]
    assert [(d.kind, d.table, d.name) for d in ts.compare(hooked, plain)] == [
        ("feature_missing", "", "event_trigger:audit")
    ]
    assert ts.fingerprint(plain) != ts.fingerprint(hooked)
    assert ts.SchemaSnapshot.from_json(hooked.to_json()) == hooked


def test_level2_d5_parts_round_trip_through_json() -> None:
    table = _d5_table(
        triggers=(ts.TriggerShape("tr", "CREATE TRIGGER tr ...", enabled="ALWAYS"),),
        policies=(ts.PolicyShape("p", "SELECT", False, ("alice", "bob"), using="(owner = CURRENT_USER)", check=None),),
        functions=(ts.FunctionShape("public.f(integer)", "sql", "CREATE FUNCTION public.f(integer) ..."),),
        row_security=True,
        force_row_security=True,
        features=("rule:r", "unlogged"),
    )
    snapshot = ts.SchemaSnapshot("postgresql", (table,), level=2, features=("event_trigger:e",))
    assert ts.SchemaSnapshot.from_json(snapshot.to_json()) == snapshot
    assert ts.SchemaSnapshot.from_json(snapshot.to_json()).to_json() == snapshot.to_json()


@pytest.mark.parametrize(
    "change",
    [
        {"triggers": (ts.TriggerShape("tr", "x"),)},
        {"policies": (ts.PolicyShape("p", "ALL", True, ()),)},
        {"functions": (ts.FunctionShape("f()", "sql", ""),)},
        {"row_security": True},
        {"force_row_security": True},
        {"features": ("strict",)},
    ],
)
def test_level1_refuses_the_d5_parts(change: dict[str, Any]) -> None:
    table = ts.TableShape("main.t", (ts.ColumnShape("id", "INTEGER", False),), ("id",), **change)
    with pytest.raises(UsageError) as caught:
        ts.SchemaSnapshot("sqlite", (table,))
    assert caught.value.code == "schema.level_mismatch"


def test_level1_refuses_database_features() -> None:
    with pytest.raises(UsageError) as caught:
        ts.SchemaSnapshot("postgresql", (), features=("event_trigger:e",))
    assert caught.value.code == "schema.level_mismatch"


@pytest.mark.parametrize(
    "patch",
    [
        {"row_security": [True]},
        {"row_security": ["yes", False]},
        {"triggers": [{"name": 1, "definition": "x"}]},
        {"features": "strict"},
        {"features": [1]},
        {"policies": [{"name": "p", "command": "ALL", "permissive": "yes", "roles": []}]},
        {"functions": [{"signature": "f()", "language": None, "definition": ""}]},
    ],
)
def test_from_json_refuses_loose_d5_parts(patch: dict[str, Any]) -> None:
    data = json.loads(ts.SchemaSnapshot("sqlite", (_base(),), level=2).to_json())
    data["tables"][0].update(patch)
    with pytest.raises(UsageError) as info:
        ts.SchemaSnapshot.from_json(json.dumps(data))
    assert info.value.code == "schema.invalid_snapshot"
