"""The SQLite table rebuild of a schema change plan, and K-1 (TABRION TAB-XW-4, CR-DBX).

Contract I-124 f; ADR 0015 section 4 and K-1 (section 9): the rebuild recreates the table's triggers from the text
``sqlite_schema`` holds, which in a hostile file is attacker data. Each text is exactly one statement, one complete
statement with nothing after it, whose head is ``CREATE TRIGGER`` and the row's own name; otherwise ``plan`` refuses
(``PlanRefused``, reason ``unsafe_schema_sql``). ``test_rebuild_runs_under_authorizer_and_defensive`` is the check at
execution (TAB-XW-5, contract I-125): ``execute_plan`` runs each trigger text as one statement on the caller's
connection, under the caller's authorizer, with ``trusted_schema = OFF`` and defensive mode.
"""

from __future__ import annotations

from collections.abc import Iterator
import dataclasses
import inspect
from pathlib import Path
import sqlite3
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("alembic")

from tabrion import Database
from tabrion import plan as tp
from tabrion import schema as ts
from tabrion.errors import PlanFailed, PlanRefused

SECRET = "SECRET-4711"

SOURCE = (
    "CREATE TABLE parts (id INTEGER PRIMARY KEY, code VARCHAR(10) NOT NULL, qty INTEGER DEFAULT 0, "
    "CONSTRAINT ck_qty CHECK (qty >= 0))",
    "CREATE TABLE moves (id INTEGER PRIMARY KEY, part INTEGER NOT NULL REFERENCES parts (id) ON DELETE CASCADE)",
    "CREATE TABLE log (id INTEGER PRIMARY KEY, what TEXT)",
    "CREATE UNIQUE INDEX parts_code ON parts (code)",
    "CREATE INDEX parts_lower ON parts (lower(code))",
    "CREATE VIEW part_codes AS SELECT code FROM parts",
    "CREATE TRIGGER parts_log AFTER UPDATE ON parts BEGIN INSERT INTO log (what) VALUES ('u;' || new.code); END",
    'CREATE TRIGGER "parts ""ins""" AFTER INSERT ON parts BEGIN INSERT INTO log (what) VALUES (\'i\'); END',
    "CREATE TRIGGER moves_log AFTER INSERT ON moves BEGIN UPDATE parts SET qty = qty + 1 WHERE id = new.part; END",
    "INSERT INTO parts VALUES (1, 'a', 2), (2, 'b', 0)",
    "INSERT INTO moves VALUES (1, 1), (2, 1), (3, 2)",
)


def make(path: Path, statements: tuple[str, ...] | list[str]) -> Database:
    database = Database.from_url(f"sqlite:///{path}")
    for statement in statements:
        database.execute(statement)
    return database


def take(db: Database) -> ts.SchemaSnapshot:
    with db.inspector() as inspector:
        return ts.snapshot(inspector, level=2)


@pytest.fixture
def path(tmp_path: Path) -> Iterator[Path]:
    file = tmp_path / "rebuild.db"
    make(file, SOURCE).close()
    yield file


def current_of(path: Path) -> ts.SchemaSnapshot:
    database = Database.from_url(f"sqlite:///{path}")
    try:
        return take(database)
    finally:
        database.close()


def parts_of(model: ts.SchemaSnapshot) -> ts.TableShape:
    found = model.table("main.parts")
    assert found is not None
    return found


def with_parts(model: ts.SchemaSnapshot, **changes: Any) -> ts.SchemaSnapshot:
    return dataclasses.replace(
        model, tables=tuple(dataclasses.replace(t, **changes) if t.name == "main.parts" else t for t in model.tables)
    )


def narrowed(model: ts.SchemaSnapshot) -> ts.SchemaSnapshot:
    """``code`` from ``VARCHAR(10)`` to ``TEXT``, the check dropped and a column added: one rebuild."""
    parts = parts_of(model)
    columns = (
        parts.columns[0],
        dataclasses.replace(parts.columns[1], type="TEXT"),
        parts.columns[2],
        ts.ColumnShape("note", "TEXT", True),
    )
    return with_parts(model, columns=columns, check_constraints=())


def run(path: Path, plan: tp.MigrationPlan) -> None:
    """Each statement alone with ``execute`` (never ``executescript``), foreign keys off, checked before the commit."""
    connection = sqlite3.connect(path, isolation_level=None)
    try:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("BEGIN IMMEDIATE")
        for step in plan.steps:
            for statement in step.statements:
                connection.execute(statement.sql)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        connection.execute("COMMIT")
    finally:
        connection.close()


GOLDEN = [
    "CREATE TABLE _tabrion_old_parts (\n\tid BLOB, \n\tcode BLOB, \n\tqty BLOB\n)",
    "INSERT INTO _tabrion_old_parts (id, code, qty) SELECT parts.id, parts.code, parts.qty \nFROM parts",
    "DROP TABLE parts",
    "CREATE TABLE parts (\n\tid INTEGER, \n\tcode TEXT NOT NULL, \n\tqty INTEGER DEFAULT (0), \n\tnote TEXT, "
    "\n\tPRIMARY KEY (id)\n)",
    "INSERT INTO parts (id, code, qty) SELECT _tabrion_old_parts.id, _tabrion_old_parts.code, _tabrion_old_parts.qty "
    "\nFROM _tabrion_old_parts",
    "DROP TABLE _tabrion_old_parts",
    "CREATE UNIQUE INDEX parts_code ON parts (code)",
    "CREATE INDEX parts_lower ON parts (lower(code))",
    'CREATE TRIGGER "parts ""ins""" AFTER INSERT ON parts BEGIN INSERT INTO log (what) VALUES (\'i\'); END',
    "CREATE TRIGGER parts_log AFTER UPDATE ON parts BEGIN INSERT INTO log (what) VALUES ('u;' || new.code); END",
    'SELECT * FROM "part_codes" LIMIT 0',  # every view checked before the commit (review of #89, P89-02)
]


def test_xw4_rebuild_golden(path: Path) -> None:
    """R-X2: the rebuild copies the rows aside, recreates the table from the target, copies them back and recreates
    the indexes (from the target) and the triggers (from ``sqlite_schema``); views stay, as no table is renamed."""
    current = current_of(path)
    plan = tp.plan(current=current, target=narrowed(current))
    assert [(s.operation, s.target, [r.value for r in s.risks]) for s in plan.steps] == [
        ("rebuild_table", "main.parts", ["additive", "data_changing", "destructive", "rebuild"])
    ]
    assert [s.sql for s in plan.steps[0].statements] == GOLDEN
    assert plan.steps[0].transactional


def test_xw4_rebuild_round_trip_keeps_rows_triggers_views_and_children(path: Path) -> None:
    current = current_of(path)
    target = narrowed(current)
    plan = tp.plan(current=current, target=target)
    run(path, plan)
    database = Database.from_url(f"sqlite:///{path}")
    try:
        after = take(database)
        assert ts.compare(expected=target, actual=after) == ()
        assert ts.fingerprint(after) == plan.target_fingerprint
        assert [tuple(r) for r in database.fetch_all("SELECT id, code, qty, note FROM parts ORDER BY id")] == [
            (1, "a", 4, None),  # 2 + two moves (moves_log)
            (2, "b", 1, None),
        ]
        assert database.fetch_scalar("SELECT count(*) FROM moves") == 3  # ON DELETE CASCADE did not fire
        assert [r[0] for r in database.fetch_all("SELECT code FROM part_codes ORDER BY 1")] == ["a", "b"]
        database.execute("INSERT INTO moves VALUES (4, 2)")  # moves_log, on another table, still works
        database.execute("UPDATE parts SET code = 'c' WHERE id = 2")  # parts_log was recreated
        assert database.fetch_scalar("SELECT qty FROM parts WHERE id = 2") == 2
        assert database.fetch_scalar("SELECT what FROM log ORDER BY id DESC LIMIT 1") == "u;c"
        assert database.fetch_scalar("SELECT count(*) FROM sqlite_master WHERE name LIKE '_tabrion%'") == 0
    finally:
        database.close()


def test_xw4_rebuild_fails_closed_on_a_lost_value(path: Path) -> None:
    """A target the rows do not fit (``NOT NULL`` without a default) fails loudly and rolls back: nothing changed."""
    current = current_of(path)
    parts = parts_of(current)
    target = with_parts(current, columns=(*parts.columns, ts.ColumnShape("must", "INTEGER", False)))
    plan = tp.plan(current=current, target=target)
    with pytest.raises(sqlite3.IntegrityError):
        run(path, plan)
    assert ts.fingerprint(current_of(path)) == plan.source_fingerprint


def test_xw4_rebuild_holder_name_never_collides(path: Path) -> None:
    database = make(path, ["CREATE TABLE _tabrion_old_parts (x INTEGER)"])
    database.close()
    current = current_of(path)
    statements = tp.plan(current=current, target=narrowed(current)).steps[0].statements
    assert statements[0].sql.startswith("CREATE TABLE _tabrion_old_parts_ (")


@pytest.mark.parametrize(
    ("statement", "feature"),
    [
        ("CREATE TABLE s (id INTEGER PRIMARY KEY, v INTEGER) STRICT", "strict"),
        ("CREATE TABLE s (id INTEGER PRIMARY KEY, v INTEGER) WITHOUT ROWID", "without_rowid"),
        ("CREATE TABLE s (id INTEGER PRIMARY KEY AUTOINCREMENT, v INTEGER)", "autoincrement"),
        ("CREATE TABLE s (id INTEGER PRIMARY KEY, v INTEGER UNIQUE ON CONFLICT REPLACE)", "on_conflict"),
        ("CREATE TABLE s (id INTEGER PRIMARY KEY, v TEXT COLLATE NOCASE)", "collation"),
        ("CREATE TABLE s (id INTEGER, v TEXT, PRIMARY KEY (v COLLATE NOCASE))", "collation"),
        (
            "CREATE TABLE s (id INTEGER PRIMARY KEY, v INTEGER REFERENCES s (id) DEFERRABLE INITIALLY DEFERRED)",
            "deferrable_reference",
        ),
    ],
)
def test_xw4_rebuild_refuses_what_the_shape_would_lose(tmp_path: Path, statement: str, feature: str) -> None:
    """ADR 0015 D-5.3: a ``CREATE TABLE`` text with what the shape does not describe is marked (and in the ``v2:``
    fingerprint); a rebuild would lose it and is refused, an in-place change is not."""
    database = make(tmp_path / "f.db", [statement])
    try:
        current = take(database)
    finally:
        database.close()
    table = current.tables[0]
    assert feature in table.features
    rebuilt = dataclasses.replace(table, check_constraints=(ts.CheckShape(None, "id > 0"),))
    with pytest.raises(PlanRefused) as info:
        tp.plan(current=current, target=dataclasses.replace(current, tables=(rebuilt,)))
    assert info.value.details["reason"] == "unsupported_feature"
    assert info.value.details["feature"] == table.features[0]
    added = dataclasses.replace(table, columns=(*table.columns, ts.ColumnShape("extra", "TEXT", True)))
    plan = tp.plan(current=current, target=dataclasses.replace(current, tables=(added,)))
    assert [s.operation for s in plan.steps] == ["add_column"]


def test_xw4_virtual_table_is_marked(tmp_path: Path) -> None:
    database = make(tmp_path / "v.db", [])
    try:
        try:
            database.execute("CREATE VIRTUAL TABLE docs USING fts5(body)")
        except Exception:  # not every SQLite build has FTS5; R*Tree is the other module the standard builds carry
            database.execute("CREATE VIRTUAL TABLE docs USING rtree(id, x0, x1)")
        docs = take(database).table("main.docs")
        assert docs is not None and "virtual_table" in docs.features
    finally:
        database.close()


# --- K-1 ------------------------------------------------------------------------------------------------------------


def model_with_trigger(definition: str, name: str = "tr") -> tuple[ts.SchemaSnapshot, ts.SchemaSnapshot]:
    table = ts.TableShape(
        "main.t",
        (ts.ColumnShape("id", "INTEGER", False), ts.ColumnShape("v", "VARCHAR(10)", True)),
        ("id",),
        triggers=(ts.TriggerShape(name, definition),),
    )
    changed = dataclasses.replace(table, columns=(table.columns[0], ts.ColumnShape("v", "TEXT", True)))
    return ts.SchemaSnapshot("sqlite", (table,), level=2), ts.SchemaSnapshot("sqlite", (changed,), level=2)


def unsafe(definition: str, name: str = "tr") -> PlanRefused:
    current, target = model_with_trigger(definition, name)
    with pytest.raises(PlanRefused) as info:
        tp.plan(current=current, target=target)
    assert info.value.code == "plan.refused"
    assert info.value.details == {"reason": "unsafe_schema_sql", "table": "main.t", "object": "trigger"}
    assert SECRET not in str(info.value) and SECRET not in repr(info.value)
    assert info.value.__cause__ is None
    return info.value


GOOD = "CREATE TRIGGER tr AFTER INSERT ON t BEGIN SELECT 1; SELECT 2; END"


def test_xw4_k1_good_trigger_text_is_one_statement() -> None:
    current, target = model_with_trigger(GOOD)
    statements = tp.plan(current=current, target=target).steps[0].statements
    assert statements[-1].sql == GOOD
    assert sqlite3.complete_statement(GOOD + ";")


@pytest.mark.parametrize(
    "definition",
    [
        GOOD + "; DROP TABLE t",
        GOOD + ";",
        GOOD + "; SELECT '" + SECRET + "'",
        "CREATE TRIGGER tr AFTER INSERT ON t BEGIN SELECT 1; END; ATTACH DATABASE 'x' AS y",
        GOOD + " -- comment",
        GOOD + "\x00",
        "CREATE TRIGGER tr AFTER INSERT ON t BEGIN SELECT 1;",  # not complete
        "",
    ],
)
def test_rebuild_refuses_second_statement_in_schema_sql(definition: str) -> None:
    """K-1: a text with a second statement, anything after the first, or no complete statement is refused."""
    unsafe(definition)


@pytest.mark.parametrize(
    ("definition", "name"),
    [
        ("CREATE VIEW tr AS SELECT 1", "tr"),
        ("CREATE INDEX tr ON t (v)", "tr"),
        ("CREATE TABLE tr (x)", "tr"),
        ("CREATE TRIGGER other AFTER INSERT ON t BEGIN SELECT 1; END", "tr"),
        ('CREATE TRIGGER "tr2" AFTER INSERT ON t BEGIN SELECT 1; END', "tr"),
        ("CREATE TRIGGER main.tr AFTER INSERT ON t BEGIN SELECT 1; END", "tr"),
        ("CREATE TRIGGER IF NOT EXISTS tr AFTER INSERT ON t BEGIN SELECT 1; END", "tr"),
        ("CREATE TEMP TRIGGER tr AFTER INSERT ON t BEGIN SELECT 1; END", "tr"),
        ("CREATE /* x */ TRIGGER tr AFTER INSERT ON t BEGIN SELECT 1; END", "tr"),
        ("  CREATE TRIGGER tr AFTER INSERT ON t BEGIN SELECT 1; END", "tr"),
        ("DROP TABLE t", "tr"),
        ("CREATE TRIGGER trx AFTER INSERT ON t BEGIN SELECT 1; END", "tr"),
        ("CREATE TRIGGER tr(AFTER INSERT ON t BEGIN SELECT 1; END", "tr"),
    ],
)
def test_rebuild_refuses_type_or_name_mismatch(definition: str, name: str) -> None:
    """K-1: the head must be ``CREATE TRIGGER`` followed by the row's own name (quoted or not)."""
    unsafe(definition, name)


@pytest.mark.parametrize(
    ("definition", "name"),
    [
        ('CREATE TRIGGER "a ""b""" AFTER INSERT ON t BEGIN SELECT 1; END', 'a "b"'),
        ("CREATE TRIGGER [a b] AFTER INSERT ON t BEGIN SELECT 1; END", "a b"),
        ("CREATE TRIGGER `a``b` AFTER INSERT ON t BEGIN SELECT 1; END", "a`b"),
        ("create trigger größe\nAFTER INSERT ON t BEGIN SELECT 1; END", "größe"),
    ],
)
def test_xw4_k1_quoted_names_match(definition: str, name: str) -> None:
    current, target = model_with_trigger(definition, name)
    assert tp.plan(current=current, target=target).steps[0].statements[-1].sql == definition


def test_xw4_k1_hostile_file(tmp_path: Path) -> None:
    """K-1 on a real file: a ``sqlite_schema`` row rewritten through ``writable_schema`` to carry a second
    statement is read into the snapshot as it is and refused by the plan before anything runs."""
    file = tmp_path / "hostile.db"
    connection = sqlite3.connect(file, isolation_level=None)
    try:
        connection.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v VARCHAR(10))")
        connection.execute("CREATE TRIGGER tr AFTER INSERT ON t BEGIN SELECT 1; END")
        connection.execute("PRAGMA writable_schema = ON")
        connection.execute(
            "UPDATE sqlite_master SET sql = sql || '; DROP TABLE t' WHERE type = 'trigger' AND name = 'tr'"
        )
        connection.execute("PRAGMA writable_schema = OFF")
    finally:
        connection.close()
    database = Database.from_url(f"sqlite:///{file}")
    try:
        current = take(database)
    finally:
        database.close()
    table = current.tables[0]
    assert table.triggers[0].definition.endswith("; DROP TABLE t")
    target = dataclasses.replace(
        current, tables=(dataclasses.replace(table, columns=(table.columns[0], ts.ColumnShape("v", "TEXT", True))),)
    )
    with pytest.raises(PlanRefused) as info:
        tp.plan(current=current, target=target)
    assert info.value.details["reason"] == "unsafe_schema_sql"


def test_rebuild_never_uses_executescript(path: Path) -> None:
    """K-1: every rebuild text is one statement for ``execute``: the plan module never calls ``executescript``, and
    each statement of a rebuild runs through ``sqlite3.Connection.execute``, which refuses a second statement."""
    source = inspect.getsource(tp)
    assert "executescript" not in source.replace("never ``executescript``", "")
    current = current_of(path)
    plan = tp.plan(current=current, target=narrowed(current))
    connection = sqlite3.connect(path, isolation_level=None)
    try:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("BEGIN")
        for statement in plan.steps[0].statements:
            assert sqlite3.complete_statement(statement.sql + ";")
            connection.execute(statement.sql)
        connection.execute("ROLLBACK")
        with pytest.raises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1; SELECT 2")  # what execute does with a second statement
    finally:
        connection.close()


# --- review of #89, P89-02: views and triggers that name what a plan drops -------------------------------------------

DEPENDENTS = (
    "CREATE TABLE parent (id INTEGER PRIMARY KEY, a TEXT, b TEXT)",
    "CREATE TABLE child (id INTEGER PRIMARY KEY, pid INTEGER)",
    "INSERT INTO parent VALUES (1, 'x', 'y')",
)


def without_b(model: ts.SchemaSnapshot) -> ts.SchemaSnapshot:
    return dataclasses.replace(
        model,
        tables=tuple(
            dataclasses.replace(t, columns=t.columns[:2]) if t.name == "main.parent" else t for t in model.tables
        ),
    )


@pytest.mark.parametrize(
    ("dependent", "owner"),
    [
        ("CREATE VIEW v AS SELECT id, b FROM parent", "main.v"),  # the reviewer's view
        ('CREATE VIEW v AS SELECT "B" FROM "PARENT"', "main.v"),  # quoted, other case
        (
            "CREATE TRIGGER other_tr AFTER INSERT ON child BEGIN SELECT b FROM parent WHERE id = new.pid; END",
            "main.child",
        ),  # the reviewer's trigger on another table
        ("CREATE TRIGGER own_tr AFTER UPDATE ON parent BEGIN SELECT new.b; END", "main.parent"),
    ],
)
@pytest.mark.parametrize("server_version", [None, (3, 45, 0)])
def test_xw4_dropping_a_column_that_a_view_or_trigger_names_is_refused(
    tmp_path: Path, dependent: str, owner: str, server_version: tuple[int, ...] | None
) -> None:
    """P89-02: SQLite checks neither a view nor a trigger after a rebuild; the plan refuses to drop a column that the
    text of one names (with the table), in place or by a rebuild."""
    database = make(tmp_path / "dep.db", [*DEPENDENTS, dependent])
    try:
        current = take(database)
    finally:
        database.close()
    with pytest.raises(PlanRefused) as caught:
        tp.plan(current=current, target=without_b(current), server_version=server_version)
    assert caught.value.details == {"reason": "dependent_object", "table": "main.parent", "object": owner}


def test_xw4_a_view_without_the_dropped_column_allows_the_rebuild_and_is_checked(tmp_path: Path) -> None:
    """A view that names the table but not the dropped column does not refuse the plan; the rebuild ends with
    ``SELECT * FROM <view> LIMIT 0`` for every view, which runs inside the transaction."""
    file = tmp_path / "ok.db"
    make(file, [*DEPENDENTS, "CREATE VIEW v AS SELECT id, a FROM parent", "CREATE VIEW w AS SELECT 1 AS one"]).close()
    current = current_of(file)
    assert [v.name for v in current.views] == ["main.v", "main.w"]
    plan = tp.plan(current=current, target=without_b(current))
    assert [s.sql for s in plan.steps[0].statements][-2:] == [
        'SELECT * FROM "v" LIMIT 0',
        'SELECT * FROM "w" LIMIT 0',
    ]
    run(file, plan)
    assert ts.fingerprint(current_of(file)) == plan.target_fingerprint


def test_xw4_a_broken_view_fails_the_rebuild_transaction(tmp_path: Path) -> None:
    """The view check is the last line of defense: a view that the rebuild breaks although the snapshot's text did
    not name the column (a stale snapshot, changed behind the plan's back) fails the transaction, and nothing is
    committed."""
    file = tmp_path / "broken.db"
    make(file, [*DEPENDENTS, "CREATE VIEW v AS SELECT id, a FROM parent"]).close()
    current = current_of(file)
    plan = tp.plan(current=current, target=without_b(current))
    connection = sqlite3.connect(file, isolation_level=None)
    try:  # make the view depend on b behind the snapshot's back, as a stale snapshot would
        connection.execute("DROP VIEW v")
        connection.execute("CREATE VIEW v AS SELECT id, b FROM parent")
    finally:
        connection.close()
    with pytest.raises(sqlite3.OperationalError, match="no such column"):
        run(file, plan)
    assert [c.name for c in parts_of_named(current_of(file), "main.parent").columns] == ["id", "a", "b"]


def parts_of_named(model: ts.SchemaSnapshot, name: str) -> ts.TableShape:
    found = model.table(name)
    assert found is not None
    return found


@pytest.mark.parametrize(
    ("dependent", "owner"),
    [
        ("CREATE VIEW v AS SELECT * FROM parent", "main.v"),
        ("CREATE TRIGGER other_tr AFTER INSERT ON child BEGIN DELETE FROM parent; END", "main.child"),
    ],
)
def test_xw4_dropping_a_table_that_a_view_or_trigger_names_is_refused(
    tmp_path: Path, dependent: str, owner: str
) -> None:
    database = make(tmp_path / "drop.db", [*DEPENDENTS, dependent])
    try:
        current = take(database)
    finally:
        database.close()
    target = dataclasses.replace(current, tables=tuple(t for t in current.tables if t.name != "main.parent"))
    with pytest.raises(PlanRefused) as caught:
        tp.plan(current=current, target=target)
    assert caught.value.details == {"reason": "dependent_object", "table": "main.parent", "object": owner}


def test_xw4_a_dropped_tables_own_trigger_does_not_refuse(tmp_path: Path) -> None:
    file = tmp_path / "own.db"
    make(file, [*DEPENDENTS, "CREATE TRIGGER p_tr AFTER INSERT ON parent BEGIN SELECT new.b; END"]).close()
    current = current_of(file)
    target = dataclasses.replace(current, tables=tuple(t for t in current.tables if t.name != "main.parent"))
    assert [s.operation for s in tp.plan(current=current, target=target).steps] == ["drop_table"]


def test_xw4_a_plan_never_changes_a_view(tmp_path: Path) -> None:
    file = tmp_path / "views.db"
    make(file, [*DEPENDENTS, "CREATE VIEW v AS SELECT id FROM parent"]).close()
    current = current_of(file)
    for views in ((), (ts.ViewShape("main.v", "CREATE VIEW v AS SELECT a FROM parent"),)):
        with pytest.raises(PlanRefused) as caught:
            tp.plan(current=current, target=dataclasses.replace(without_b(current), views=views))
        assert caught.value.details == {"reason": "unsupported_feature", "table": "main.v", "feature": "view"}


def test_rebuild_runs_under_authorizer_and_defensive(path: Path) -> None:
    """K-1 at execution (TAB-XW-5): the caller's authorizer, installed on every connection of its engine, sees each
    ``CREATE TRIGGER`` of the rebuild, and defensive mode and ``trusted_schema = OFF`` are in force while it runs; an
    authorizer that denies the trigger makes the step fail and the transaction roll back. Without defensive mode
    (Python 3.11 has no ``Connection.setconfig``) the plan is refused before anything runs."""
    seen: list[tuple[str | None, bool | None]] = []
    deny: list[bool] = [False]
    flag = getattr(sqlite3, "SQLITE_DBCONFIG_DEFENSIVE", None)

    def watch(dbapi: Any, _record: object) -> None:
        def authorize(action: int, arg1: str | None, *_: object) -> int:
            if action == sqlite3.SQLITE_CREATE_TRIGGER:
                seen.append((arg1, bool(dbapi.getconfig(flag)) if flag is not None else None))
                if deny[0]:
                    return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        dbapi.set_authorizer(authorize)

    database = Database.from_url(f"sqlite:///{path}")
    import sqlalchemy

    sqlalchemy.event.listen(database.engine, "connect", watch)
    try:
        current = take(database)
        target = narrowed(current)
        preview = tp.plan(current=current, target=target)
        arguments: dict[str, Any] = {
            "plan_hash": preview.plan_hash,
            "expected_fingerprint": ts.fingerprint(current),
            "environment": "dev",
            "allow": preview.risks,
        }
        if flag is None:
            with pytest.raises(PlanRefused) as refused:
                tp.execute_plan(database, target, **arguments)
            assert refused.value.details == {"reason": "engine_unsupported", "feature": "sqlite_defensive"}
            assert seen == []
            return
        deny[0] = True
        with pytest.raises(PlanFailed) as caught:
            tp.execute_plan(database, target, **arguments)
        assert caught.value.details == {"step": 0, "completed": 0, "state": "rolled_back"}
        assert ts.fingerprint(take(database)) == ts.fingerprint(current)
        assert seen == [('parts "ins"', True)]  # the first trigger text was denied: nothing after it ran
        seen.clear()
        deny[0] = False
        result = tp.execute_plan(database, target, **arguments)
        assert result.steps_run == 1
        assert sorted(str(name) for name, _ in seen) == ['parts "ins"', "parts_log"]
        assert all(defensive is True for _, defensive in seen)
        assert ts.compare(expected=target, actual=take(database)) == ()
        with database.transaction() as transaction:  # defensive mode is off again on the pooled connection
            raw: Any = transaction.connection.connection.dbapi_connection
            assert raw.getconfig(flag) is False
            assert transaction.fetch_scalar("PRAGMA trusted_schema") == 0
    finally:
        database.close()
