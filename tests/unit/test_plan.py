"""Schema change plans on SQLite and from hand-made models (TABRION TAB-XW-4, CR-DBX).

Contract section 5.13, I-124; ADR 0015 sections 3, 4, 9 (N-2) and 10 (D-5). The SQLite round trips run every statement
of a plan alone (one ``execute`` each, as the executor of TAB-XW-5 will) and compare the reflected level-2 snapshot
with the target model. The PostgreSQL round trips and the D-5 catalog probes are in
``tests/postgres/test_plan_postgres.py``; the SQLite rebuild (K-1) in ``tests/unit/test_plan_sqlite_rebuild.py``; the
revision text (K-3) in ``tests/unit/test_plan_revision_text.py``.
"""

from __future__ import annotations

from collections.abc import Iterator
import dataclasses
import json
from pathlib import Path
import sqlite3
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("alembic")

from tabrion import Database
from tabrion import plan as tp
from tabrion import schema as ts
from tabrion.errors import PlanError, PlanMismatch, PlanRefused, UsageError
from tabrion.plan import RiskClass

SECRET = "SECRET-4711"
VERSION = sqlite3.sqlite_version_info

SOURCE = (
    "CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT NOT NULL, email VARCHAR(80))",
    "CREATE TABLE invoices (id INTEGER PRIMARY KEY, customer INTEGER NOT NULL REFERENCES customers (id), "
    "note VARCHAR(20), total INTEGER DEFAULT 0, old TEXT)",
    "CREATE INDEX invoices_note ON invoices (note)",
    "INSERT INTO customers VALUES (1, 'a', 'a@example.org'), (2, 'b', NULL)",
    "INSERT INTO invoices VALUES (1, 1, 'x', 5, 'o'), (2, 2, 'y', 7, NULL)",
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
def db(tmp_path: Path) -> Iterator[Database]:
    database = make(tmp_path / "plan.db", SOURCE)
    yield database
    database.close()


def column(name: str, type_: str = "INTEGER", nullable: bool = True, default: str | None = None) -> ts.ColumnShape:
    return ts.ColumnShape(name, type_, nullable, default)


def replace_table(model: ts.SchemaSnapshot, name: str, **changes: Any) -> ts.SchemaSnapshot:
    tables = tuple(dataclasses.replace(t, **changes) if t.name == name else t for t in model.tables)
    return dataclasses.replace(model, tables=tables)


def table_of(model: ts.SchemaSnapshot, name: str) -> ts.TableShape:
    found = model.table(name)
    assert found is not None
    return found


def execute(path: Path, plan: tp.MigrationPlan) -> ts.SchemaSnapshot:
    """Run each statement alone on a plain ``sqlite3`` connection, in one transaction, foreign keys off and checked
    before the commit (what the executor of TAB-XW-5 will do), then reflect the file."""
    connection = sqlite3.connect(path, isolation_level=None)
    try:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("BEGIN IMMEDIATE")
        for step in plan.steps:
            for statement in step.statements:
                connection.execute(statement.sql)  # one statement per call: sqlite3 refuses a second one
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        connection.execute("COMMIT")
    finally:
        connection.close()
    database = Database.from_url(f"sqlite:///{path}")
    try:
        return take(database)
    finally:
        database.close()


def refused(reason: str, call: Any, **details: Any) -> PlanRefused:
    with pytest.raises(PlanRefused) as info:
        call()
    assert info.value.code == "plan.refused"
    assert info.value.details["reason"] == reason
    for key, value in details.items():
        assert info.value.details[key] == value
    assert info.value.__cause__ is None
    return info.value


def usage(code: str, call: Any, **details: Any) -> UsageError:
    with pytest.raises(UsageError) as info:
        call()
    assert info.value.code == code
    for key, value in details.items():
        assert info.value.details[key] == value
    return info.value


# --- the plan object ------------------------------------------------------------------------------------------------


def test_xw4_empty_plan_for_equal_snapshots(db: Database) -> None:
    current = take(db)
    plan = tp.plan(current, current)
    assert plan.steps == () and plan.risks == ()
    assert plan.dialect == "sqlite"
    assert plan.source_fingerprint == plan.target_fingerprint == ts.fingerprint(current)
    assert plan.plan_hash.startswith("sha256:") and len(plan.plan_hash) == 71


def test_xw4_plan_is_deterministic(db: Database) -> None:
    """The same snapshots and server version give the same plan and hash, also from JSON copies of the snapshots."""
    current = take(db)
    target = replace_table(current, "main.invoices", columns=(*table_of(current, "main.invoices").columns, column("x")))
    target = dataclasses.replace(target, tables=(*target.tables, ts.TableShape("main.new", (column("id"),), ("id",))))
    first = tp.plan(current, target, server_version=VERSION)
    again = tp.plan(
        ts.SchemaSnapshot.from_json(current.to_json()),
        ts.SchemaSnapshot.from_json(target.to_json()),
        server_version=VERSION,
    )
    assert first == again and first.plan_hash == again.plan_hash
    assert first.to_json() == again.to_json()
    assert tp.plan(current, target, server_version=(3, 30, 0)).plan_hash == first.plan_hash  # no drop: same plan


def test_xw4_json_round_trip_and_canonical_form(db: Database) -> None:
    current = take(db)
    target = replace_table(current, "main.invoices", indexes=())
    plan = tp.plan(current, target)
    text = plan.to_json()
    assert json.loads(text)["plan_hash"] == plan.plan_hash
    assert tp.MigrationPlan.from_json(text) == plan
    assert text == json.dumps(json.loads(text), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def edited(text: str, change: Any) -> str:
    data = json.loads(text)
    change(data)
    return json.dumps(data)


def test_xw4_from_json_refuses_an_edited_plan(db: Database) -> None:
    """An edited statement, risk, step or fingerprint no longer matches the hash: ``plan.invalid``."""
    current = take(db)
    plan = tp.plan(current, replace_table(current, "main.invoices", indexes=()))
    text = plan.to_json()
    edits = [
        lambda d: d["steps"][0]["statements"][0].update(sql="DROP TABLE customers"),
        lambda d: d["steps"][0].update(risks=["additive"]),
        lambda d: d["steps"][0].update(target="main.customers"),
        lambda d: d.update(source_fingerprint="v2:sha256:" + "0" * 64),
        lambda d: d.update(dialect="postgresql"),
        lambda d: d["steps"].append(dict(d["steps"][0], index=1)),
        lambda d: d.update(plan_hash="sha256:" + "0" * 64),
    ]
    for change in edits:
        with pytest.raises(PlanMismatch) as info:
            tp.MigrationPlan.from_json(edited(text, change))
        assert info.value.code == "plan.invalid"
        assert info.value.__cause__ is None and info.value.__context__ is None
    # an edit with a recomputed hash is a valid plan: from_json proves integrity, not authenticity (N-2)
    data = json.loads(text)
    data["steps"][0]["statements"][0]["sql"] = "DROP TABLE customers"
    del data["plan_hash"]
    data["plan_hash"] = (
        "sha256:"
        + __import__("hashlib")
        .sha256(json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode())
        .hexdigest()
    )
    assert tp.MigrationPlan.from_json(json.dumps(data)).steps[0].statements[0].sql == "DROP TABLE customers"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "[]",
        "{}",
        f'{{"dialect": "{SECRET}"}}',
        '{"format": 1}',
        "x" * 10,
        '{"format": true, "dialect": "sqlite", "source_fingerprint": "", "target_fingerprint": "", "steps": [], '
        '"plan_hash": ""}',
        "[" * 100_000,
    ],
)
def test_xw4_from_json_refuses_what_is_not_a_plan(text: str) -> None:
    with pytest.raises(PlanMismatch) as info:
        tp.MigrationPlan.from_json(text)
    assert info.value.code == "plan.invalid"
    assert SECRET not in str(info.value) and SECRET not in repr(info.value)
    assert isinstance(info.value, PlanError)


def test_xw4_from_json_refuses_loose_types(db: Database) -> None:
    current = take(db)
    text = tp.plan(current, replace_table(current, "main.invoices", indexes=())).to_json()
    for change in (
        lambda d: d["steps"][0].update(index=True),
        lambda d: d["steps"][0].update(transactional=1),
        lambda d: d["steps"][0].update(risks=["destructive", "destructive"]),
        lambda d: d["steps"][0].update(risks=[]),
        lambda d: d["steps"][0].update(risks=["unknown"]),
        lambda d: d["steps"][0].update(operation="vacuum"),
        lambda d: d["steps"][0].update(statements=[]),
        lambda d: d["steps"][0].update(extra=1),
        lambda d: d.update(format=2),
    ):
        with pytest.raises(PlanMismatch):
            tp.MigrationPlan.from_json(edited(text, change))


def test_from_json_docstring_states_integrity_only() -> None:
    """N-2 (ADR 0015 section 9): ``from_json`` proves integrity, not authenticity, and says so."""
    doc = " ".join((tp.MigrationPlan.from_json.__doc__ or "").split())
    assert "integrity, not authenticity" in doc
    assert "Never take a plan from an untrusted source" in doc


def test_xw4_constructor_checks_the_hash(db: Database) -> None:
    current = take(db)
    plan = tp.plan(current, replace_table(current, "main.invoices", indexes=()))
    with pytest.raises(PlanMismatch):
        dataclasses.replace(plan, plan_hash="sha256:" + "0" * 64)
    step = dataclasses.replace(plan.steps[0], risks=(RiskClass.ADDITIVE,))
    with pytest.raises(PlanMismatch):
        dataclasses.replace(plan, steps=(step,))
    with pytest.raises(PlanMismatch):
        dataclasses.replace(plan.steps[0], risks=(RiskClass.LOCKING, RiskClass.ADDITIVE))  # not sorted
    with pytest.raises(PlanMismatch):
        dataclasses.replace(plan.steps[0], transactional=False)


def test_xw4_dataclasses_frozen_slotted() -> None:
    for cls in (tp.PlanStep, tp.MigrationPlan):
        assert getattr(cls, "__dataclass_params__").frozen  # noqa: B009 - not in the type stubs
        assert "__slots__" in cls.__dict__
    assert [r.value for r in RiskClass] == [
        "additive",
        "data_changing",
        "destructive",
        "rebuild",
        "locking",
        "non_transactional",
    ]


# --- arguments and refusals ------------------------------------------------------------------------------------------


def test_xw4_level_dialect_and_engine(db: Database) -> None:
    current = take(db)
    level1 = ts.SchemaSnapshot("sqlite", ())
    usage("plan.level_required", lambda: tp.plan(level1, current))
    usage("plan.level_required", lambda: tp.plan(current, level1))
    refused("dialect_mismatch", lambda: tp.plan(current, ts.SchemaSnapshot("postgresql", (), level=2)))
    other = ts.SchemaSnapshot("mysql", (), level=2)
    refused("engine_unsupported", lambda: tp.plan(other, other))
    with pytest.raises(TypeError):
        tp.plan(current, current.to_json())  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        tp.plan(current, current, server_version="3.45")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        tp.plan(current, current, server_version=(3, True))
    with pytest.raises(TypeError):
        tp.plan(current, current, concurrent_indexes=1)  # type: ignore[arg-type]
    usage(
        "plan.unsupported_option",
        lambda: tp.plan(current, current, concurrent_indexes=True),
        option="concurrent_indexes",
    )


def test_xw4_target_referring_to_a_dropped_table(db: Database) -> None:
    current = take(db)
    target = dataclasses.replace(current, tables=(table_of(current, "main.invoices"),))
    usage("plan.invalid_target", lambda: tp.plan(current, target), table="main.invoices", field="foreign_keys")


PG_T = ts.TableShape(
    "public.t",
    (column("id", nullable=False), column("note", "TEXT")),
    ("id",),
)


def pg(*tables: ts.TableShape, features: tuple[str, ...] = ()) -> ts.SchemaSnapshot:
    return ts.SchemaSnapshot("postgresql", tables, level=2, features=features)


@pytest.mark.parametrize(
    "feature",
    [
        "partitioned",
        "partition",
        "inheritance",
        "foreign_table",
        "rule:r",
        "domain:note",
        "unlogged",
        "typed_table",
        "storage_parameters",
        "replica_identity:f",
        "access_method:columnar",
        "no_inherit:ck",
        "relkind:x",
    ],
)
def test_xw4_postgres_table_feature_refuses_every_change(feature: str) -> None:
    """ADR 0015 D-5.2, D-5.3: a table with a feature marker is never changed, dropped or altered."""
    current = pg(dataclasses.replace(PG_T, features=(feature,)))
    added = pg(dataclasses.replace(PG_T, features=(feature,), columns=(*PG_T.columns, column("x"))))
    error = refused("unsupported_feature", lambda: tp.plan(current, added), table="public.t", feature=feature)
    assert feature in str(error)
    refused("unsupported_feature", lambda: tp.plan(current, pg()), table="public.t", feature=feature)
    assert tp.plan(current, current).steps == ()  # nothing to change: nothing refused
    other = ts.TableShape("public.o", (column("id"),), ())
    assert [s.operation for s in tp.plan(current, pg(current.tables[0], other)).steps] == ["create_table"]


def test_xw4_event_trigger_refuses_every_change() -> None:
    current = pg(PG_T, features=("event_trigger:audit",))
    target = pg(PG_T, ts.TableShape("public.o", (column("id"),), ()), features=("event_trigger:audit",))
    refused("unsupported_feature", lambda: tp.plan(current, target), table="", feature="event_trigger:audit")
    assert tp.plan(current, current).steps == ()


TRIGGER = ts.TriggerShape(
    "t_audit", "CREATE TRIGGER t_audit AFTER INSERT ON public.t FOR EACH ROW EXECUTE FUNCTION f()"
)
FUNCTION = ts.FunctionShape("public.f()", "plpgsql", "CREATE OR REPLACE FUNCTION public.f() ...")


@pytest.mark.parametrize(
    ("language", "definition"),
    [("c", "CREATE FUNCTION ... AS 'lib', 'f'"), ("internal", "x"), ("plpython3u", "x"), ("plpgsql", "")],
)
def test_xw4_trigger_function_language_refused(language: str, definition: str) -> None:
    """D-5.1: a table whose trigger or policy function is in C or a language Tabrion does not know is refused."""
    function = ts.FunctionShape("public.f()", language, definition)
    table = dataclasses.replace(PG_T, triggers=(TRIGGER,), functions=(function,))
    target = dataclasses.replace(table, columns=(*table.columns, column("x")))
    refused("unsupported_feature", lambda: tp.plan(pg(table), pg(target)), feature="function_language")


def test_xw4_plpgsql_trigger_and_policy_allow_changes() -> None:
    """D-5.1: triggers and policies whose functions are PL/pgSQL or SQL are in the fingerprint; the table can change."""
    policy = ts.PolicyShape("p", "ALL", True, ("PUBLIC",), using="public.ok(id)")
    function = ts.FunctionShape("public.ok(integer)", "sql", "CREATE OR REPLACE FUNCTION public.ok(integer) ...")
    table = dataclasses.replace(
        PG_T, triggers=(TRIGGER,), policies=(policy,), functions=(FUNCTION, function), row_security=True
    )
    target = dataclasses.replace(table, columns=(*table.columns, column("x")))
    plan = tp.plan(pg(table), pg(target))
    assert [(s.operation, s.statements[0].sql) for s in plan.steps] == [
        ("add_column", "ALTER TABLE public.t ADD COLUMN x INTEGER")
    ]
    assert plan.source_fingerprint != tp.plan(pg(PG_T), pg(PG_T)).source_fingerprint


@pytest.mark.parametrize(
    ("change", "feature"),
    [
        ({"triggers": (TRIGGER,), "functions": (FUNCTION,)}, "trigger"),
        ({"policies": (ts.PolicyShape("p", "ALL", True, ("PUBLIC",)),)}, "policy"),
        ({"row_security": True}, "row_security"),
        ({"force_row_security": True}, "row_security"),
        ({"features": ("unlogged",)}, "table_feature"),
    ],
)
def test_xw4_plan_never_creates_or_changes_triggers_policies_or_features(change: dict[str, Any], feature: str) -> None:
    """The plan makes no trigger, policy, function, row-level security or feature change (contract I-124 e)."""
    refused("unsupported_feature", lambda: tp.plan(pg(PG_T), pg(dataclasses.replace(PG_T, **change))), feature=feature)
    new = dataclasses.replace(PG_T, name="public.n", **change)
    refused("unsupported_feature", lambda: tp.plan(pg(), pg(new)), table="public.n", feature=feature)


def test_xw4_postgres_unsupported_changes() -> None:
    usage(
        "plan.unsupported_change",
        lambda: tp.plan(pg(PG_T), pg(dataclasses.replace(PG_T, primary_key=("id", "note")))),
        change="primary_key",
    )
    reordered = dataclasses.replace(PG_T, columns=(PG_T.columns[0], column("x"), PG_T.columns[1]))
    usage("plan.unsupported_change", lambda: tp.plan(pg(PG_T), pg(reordered)), change="column_order")
    exclusion = dataclasses.replace(
        PG_T, exclusion_constraints=(ts.ExclusionShape("ex", "EXCLUDE USING gist (id WITH =)"),)
    )
    usage("ddl.unsupported_feature", lambda: tp.plan(pg(PG_T), pg(exclusion)), feature="exclusion_constraint")
    assert [s.operation for s in tp.plan(pg(exclusion), pg(PG_T)).steps] == ["drop_constraint"]


# --- risk classes ---------------------------------------------------------------------------------------------------

PARENT = ts.TableShape("public.p", (column("id", nullable=False), column("code", "VARCHAR(10)")), ("id",))
CHILD = ts.TableShape(
    "public.c",
    (column("id", nullable=False), column("p_id"), column("qty", "SMALLINT"), column("note", "VARCHAR(20)")),
    ("id",),
    foreign_keys=(ts.ForeignKeyShape("c_p", ("p_id",), "public.p", ("id",)),),
    check_constraints=(ts.CheckShape("c_qty", "qty >= 0"),),
    indexes=(ts.IndexShape("c_note", ("note",)),),
)


def steps(plan: tp.MigrationPlan) -> list[tuple[str, str, list[str]]]:
    return [(s.operation, s.target, [r.value for r in s.risks]) for s in plan.steps]


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"columns": (*CHILD.columns, column("extra"))}, [("add_column", ["additive", "locking"])]),
        (
            {"columns": (*CHILD.columns, column("flag", "BOOLEAN", False, "false"))},
            [("add_column", ["additive", "data_changing", "locking"])],
        ),
        ({"columns": CHILD.columns[:3]}, [("drop_column", ["destructive", "locking"])]),
        (
            {"columns": (*CHILD.columns[:2], column("qty", "BIGINT"), CHILD.columns[3])},
            [("alter_column", ["data_changing", "locking"])],
        ),
        (
            {"columns": (*CHILD.columns[:3], column("note", "VARCHAR(5)"))},
            [("alter_column", ["data_changing", "destructive", "locking"])],
        ),
        (
            {"columns": (*CHILD.columns[:2], column("qty", "SMALLINT", False), CHILD.columns[3])},
            [("alter_column", ["data_changing", "locking"])],
        ),
        (
            {"columns": (*CHILD.columns[:2], column("qty", "SMALLINT", True, "0"), CHILD.columns[3])},
            [("alter_column", ["data_changing", "locking"])],
        ),
        ({"check_constraints": ()}, [("drop_constraint", ["destructive", "locking"])]),
        (
            {"unique_constraints": (ts.UniqueShape("c_u", ("note",)),)},
            [("add_constraint", ["additive", "locking"])],
        ),
        ({"indexes": ()}, [("drop_index", ["destructive", "locking"])]),
        (
            {"indexes": (*CHILD.indexes, ts.IndexShape("c_qty_ix", ("qty",)))},
            [("create_index", ["additive", "locking"])],
        ),
    ],
)
def test_xw4_postgres_risk_classes(change: dict[str, Any], expected: list[tuple[str, list[str]]]) -> None:
    current = pg(PARENT, CHILD)
    target = pg(PARENT, dataclasses.replace(CHILD, **change))
    plan = tp.plan(current, target)
    assert [(op, risks) for op, _, risks in steps(plan)] == expected
    assert all(target_ == "public.c" for _, target_, _ in steps(plan))
    assert plan.risks == tuple(sorted({RiskClass(r) for _, _, risks in steps(plan) for r in risks}))


def test_xw4_every_risk_class() -> None:
    """Each class occurs: additive and locking (above), data changing, destructive, rebuild (SQLite) and non
    transactional (``concurrent_indexes``)."""
    concurrent = tp.plan(
        pg(PARENT, CHILD),
        pg(PARENT, dataclasses.replace(CHILD, indexes=(ts.IndexShape("c_qty_ix", ("qty",)),))),
        concurrent_indexes=True,
    )
    assert steps(concurrent) == [
        ("drop_index", "public.c", ["destructive", "non_transactional"]),
        ("create_index", "public.c", ["additive", "non_transactional"]),
    ]
    assert [s.transactional for s in concurrent.steps] == [False, False]
    assert [s.statements[0].sql for s in concurrent.steps] == [
        "DROP INDEX CONCURRENTLY public.c_note",
        "CREATE INDEX CONCURRENTLY c_qty_ix ON public.c (qty)",
    ]
    sqlite_current = ts.SchemaSnapshot("sqlite", (dataclasses.replace(CHILD, name="main.c", foreign_keys=()),), level=2)
    narrowed = replace_table(sqlite_current, "main.c", columns=(*CHILD.columns[:3], column("note", "VARCHAR(5)")))
    rebuild = tp.plan(sqlite_current, narrowed)
    assert steps(rebuild) == [("rebuild_table", "main.c", ["data_changing", "destructive", "rebuild"])]
    seen = {r for p in (concurrent, rebuild) for r in p.risks}
    seen |= set(tp.plan(pg(PARENT, CHILD), pg(PARENT, dataclasses.replace(CHILD, check_constraints=()))).risks)
    seen |= set(tp.plan(pg(PARENT), pg(PARENT, CHILD)).risks)
    assert seen == set(RiskClass)


@pytest.mark.parametrize(
    ("old", "new", "widens"),
    [
        ("VARCHAR(10)", "VARCHAR(20)", True),
        ("VARCHAR(20)", "VARCHAR(10)", False),
        ("VARCHAR(10)", "TEXT", True),
        ("CHARACTER VARYING(10)", "VARCHAR", True),
        ("TEXT", "VARCHAR(10)", False),
        ("TEXT", "VARCHAR", False),
        ("SMALLINT", "INTEGER", True),
        ("BIGINT", "INTEGER", False),
        ("INTEGER", "NUMERIC", True),
        ("INTEGER", "NUMERIC(5, 0)", False),
        ("NUMERIC(10, 2)", "NUMERIC(12, 2)", True),
        ("NUMERIC(10, 2)", "NUMERIC(10, 3)", False),
        ("NUMERIC(10, 2)", "NUMERIC(11, 3)", True),
        ("NUMERIC(10, 2)", "NUMERIC", True),
        ("NUMERIC", "NUMERIC(10, 2)", False),
        ("REAL", "DOUBLE PRECISION", True),
        ("DOUBLE PRECISION", "REAL", False),
        ("TEXT", 'TEXT COLLATE "C"', False),
        ("INTEGER", "TEXT", False),
        ("DATE", "TIMESTAMP", False),
    ],
)
def test_xw4_type_widening(old: str, new: str, widens: bool) -> None:
    from tabrion.plan import _widens

    assert _widens(old, new) is widens


# --- order ----------------------------------------------------------------------------------------------------------


def test_xw4_postgres_order_holds_dependencies() -> None:
    """Foreign keys are dropped first and added last; referencing tables are dropped before the tables they refer
    to; a new table's key to another new table is added after both exist."""
    a = ts.TableShape(
        "public.a",
        (column("id", nullable=False), column("b_id")),
        ("id",),
        foreign_keys=(ts.ForeignKeyShape("a_b", ("b_id",), "public.b", ("id",)),),
    )
    b = ts.TableShape(
        "public.b",
        (column("id", nullable=False), column("a_id")),
        ("id",),
        foreign_keys=(ts.ForeignKeyShape("b_a", ("a_id",), "public.a", ("id",)),),
    )
    x = ts.TableShape(
        "public.x",
        (column("id", nullable=False), column("c_id")),
        ("id",),
        foreign_keys=(ts.ForeignKeyShape("x_c", ("c_id",), "public.c", ("id",)),),
    )
    new_child = dataclasses.replace(CHILD, foreign_keys=(ts.ForeignKeyShape("c_p", ("p_id",), "public.q", ("id",)),))
    q = dataclasses.replace(PARENT, name="public.q")
    y = dataclasses.replace(
        x, name="public.y", foreign_keys=(ts.ForeignKeyShape("y_p", ("c_id",), "public.p", ("id",)),)
    )
    current = pg(a, b, PARENT, CHILD, x, y)
    target = pg(new_child, q)
    plan = tp.plan(current, target)
    assert steps(plan) == [
        ("drop_constraint", "public.c", ["destructive", "locking"]),  # c_p into p
        ("drop_constraint", "public.a", ["destructive", "locking"]),  # the a-b cycle
        ("drop_constraint", "public.b", ["destructive", "locking"]),
        ("drop_table", "public.x", ["destructive", "locking"]),  # no dropped table refers to x or y
        ("drop_table", "public.y", ["destructive", "locking"]),
        ("drop_table", "public.p", ["destructive", "locking"]),  # after y, which refers to it
        ("drop_table", "public.a", ["destructive", "locking"]),
        ("drop_table", "public.b", ["destructive", "locking"]),
        ("create_table", "public.q", ["additive"]),
        ("add_constraint", "public.c", ["additive", "locking"]),  # c_p into q
    ]
    # two new tables that refer to each other: both created, then the keys
    plan = tp.plan(pg(), pg(a, b))
    assert steps(plan) == [
        ("create_table", "public.a", ["additive"]),
        ("create_table", "public.b", ["additive"]),
        ("add_constraint", "public.a", ["additive"]),
        ("add_constraint", "public.b", ["additive"]),
    ]
    assert "REFERENCES" not in plan.steps[0].statements[0].sql
    # a new table referring to an existing one keeps the key inline and locks the existing table
    assert steps(tp.plan(pg(PARENT), pg(PARENT, CHILD))) == [("create_table", "public.c", ["additive", "locking"])]


def test_xw4_drop_and_add_of_a_changed_constraint_and_index() -> None:
    changed = dataclasses.replace(
        CHILD,
        check_constraints=(ts.CheckShape("c_qty", "qty > 0"),),
        indexes=(ts.IndexShape("c_note", ("note",), unique=True),),
    )
    plan = tp.plan(pg(PARENT, CHILD), pg(PARENT, changed))
    assert [(s.operation, s.statements[0].sql) for s in plan.steps] == [
        ("drop_constraint", "ALTER TABLE public.c DROP CONSTRAINT c_qty"),
        ("drop_index", "DROP INDEX public.c_note"),
        ("add_constraint", "ALTER TABLE public.c ADD CONSTRAINT c_qty CHECK (qty > 0)"),
        ("create_index", "CREATE UNIQUE INDEX c_note ON public.c (note)"),
    ]


def test_xw4_identity_column_does_not_block_a_constraint() -> None:
    """A constraint or index statement only names columns: an identity column elsewhere in the table is no reason to
    refuse it (tabrion.ddl refuses identity columns only where it writes a column)."""
    identity = dataclasses.replace(
        PG_T, columns=(ts.ColumnShape("id", "INTEGER", False, identity="ALWAYS"), PG_T.columns[1])
    )
    target = dataclasses.replace(identity, indexes=(ts.IndexShape("t_note", ("note",)),))
    assert [s.operation for s in tp.plan(pg(identity), pg(target)).steps] == ["create_index"]


# --- SQLite in place ------------------------------------------------------------------------------------------------


def test_xw4_sqlite_in_place_round_trip(db: Database, tmp_path: Path) -> None:
    """Add a nullable column and one with a constant default, drop a column (3.35 and later), swap an index; create
    and drop a table: no rebuild, and the result is the target."""
    current = take(db)
    invoices = table_of(current, "main.invoices")
    target = replace_table(
        current,
        "main.invoices",
        columns=(*invoices.columns[:4], column("extra", "TEXT"), column("flag", "INTEGER", False, "1")),
        indexes=(ts.IndexShape("invoices_total", ("total",)),),
    )
    target = dataclasses.replace(
        target, tables=(*target.tables, ts.TableShape("main.notes", (column("id", nullable=False),), ("id",)))
    )
    plan = tp.plan(current, target, server_version=VERSION)
    assert steps(plan) == [
        ("drop_index", "main.invoices", ["destructive"]),
        ("drop_column", "main.invoices", ["destructive"]),
        ("add_column", "main.invoices", ["additive"]),
        ("add_column", "main.invoices", ["additive", "data_changing"]),
        ("create_index", "main.invoices", ["additive"]),
        ("create_table", "main.notes", ["additive"]),
    ]
    db.close()
    after = execute(tmp_path / "plan.db", plan)
    assert ts.compare(target, after) == ()
    assert ts.fingerprint(after) == plan.target_fingerprint
    reopened = Database.from_url(f"sqlite:///{tmp_path / 'plan.db'}")
    try:
        assert [
            tuple(r) for r in reopened.fetch_all("SELECT id, note, total, extra, flag FROM invoices ORDER BY id")
        ] == [
            (1, "x", 5, None, 1),
            (2, "y", 7, None, 1),
        ]
    finally:
        reopened.close()


def test_xw4_sqlite_drop_column_needs_335(db: Database) -> None:
    current = take(db)
    target = replace_table(current, "main.invoices", columns=table_of(current, "main.invoices").columns[:4])
    assert [s.operation for s in tp.plan(current, target, server_version=(3, 35, 0)).steps] == ["drop_column"]
    assert [s.operation for s in tp.plan(current, target, server_version=(3, 34, 1)).steps] == ["rebuild_table"]
    assert [s.operation for s in tp.plan(current, target).steps] == ["rebuild_table"]


@pytest.mark.parametrize(
    ("added", "in_place"),
    [
        (column("a", "TEXT"), True),
        (column("a", "INTEGER", False, "0"), True),
        (column("a", "TEXT", True, "'x'"), True),
        (column("a", "REAL", True, "-1.5e3"), True),
        (column("a", "INTEGER", False), False),  # NOT NULL without a default
        (column("a", "INTEGER", False, "NULL"), False),
        (column("a", "TEXT", True, "CURRENT_TIMESTAMP"), False),  # not a constant
        (column("a", "INTEGER", True, "(1 + 1)"), False),
    ],
)
def test_xw4_sqlite_add_column_in_place_or_rebuild(
    db: Database, tmp_path: Path, added: ts.ColumnShape, in_place: bool
) -> None:
    current = take(db)
    customers = table_of(current, "main.customers")
    target = replace_table(current, "main.customers", columns=(*customers.columns, added))
    plan = tp.plan(current, target, server_version=VERSION)
    assert [s.operation for s in plan.steps] == (["add_column"] if in_place else ["rebuild_table"])
    db.close()
    if not in_place and not added.nullable and added.default in (None, "NULL"):
        with pytest.raises(sqlite3.IntegrityError):  # existing rows have no value: rolled back, loud
            execute(tmp_path / "plan.db", plan)
        return
    assert ts.compare(target, execute(tmp_path / "plan.db", plan)) == ()


def test_xw4_sqlite_column_order_change_rebuilds(db: Database, tmp_path: Path) -> None:
    current = take(db)
    customers = table_of(current, "main.customers")
    target = replace_table(
        current, "main.customers", columns=(customers.columns[0], column("x", "TEXT"), *customers.columns[1:])
    )
    plan = tp.plan(current, target, server_version=VERSION)
    assert [s.operation for s in plan.steps] == ["rebuild_table"]
    db.close()
    after = execute(tmp_path / "plan.db", plan)
    assert ts.compare(target, after) == ()


# --- migration markers ----------------------------------------------------------------------------------------------


def test_xw4_migration_markers_sqlite(tmp_path: Path) -> None:
    """ADR 0015 section 3: an Alembic version table, the Nodestro and Axentra bookkeeping tables (any case, also in
    an attached database) and a ``user_version`` above 0; nothing on a plain file."""
    database = make(tmp_path / "m.db", ["CREATE TABLE t (id INTEGER)"])
    try:
        assert tp.migration_markers(database) == ()
        database.execute("CREATE TABLE Alembic_Version (version_num VARCHAR(32) NOT NULL)")
        assert tp.migration_markers(database) == ("alembic_version",)
        database.execute("PRAGMA user_version = 3")
        database.execute("CREATE VIEW axentra_schema_migrations AS SELECT 1 AS x")
        assert tp.migration_markers(database) == ("alembic_version", "axentra_schema_migrations", "sqlite_user_version")
    finally:
        database.close()
    other = tmp_path / "other.db"
    with sqlite3.connect(other) as connection:
        connection.execute("CREATE TABLE nodestro_schema_migrations (v INTEGER)")
    connection.close()
    database = Database.from_url("sqlite://")  # one shared connection (StaticPool): the ATTACH stays on it
    try:
        assert tp.migration_markers(database) == ()
        with database.engine.connect() as raw:
            raw.exec_driver_sql(f"ATTACH DATABASE '{other}' AS side")
        assert tp.migration_markers(database) == ("nodestro_schema_migrations",)
    finally:
        database.close()


def test_xw4_migration_markers_reads_only(tmp_path: Path) -> None:
    database = make(tmp_path / "r.db", ["CREATE TABLE t (id INTEGER)"])
    try:
        before = database.fetch_scalar("PRAGMA data_version")
        assert tp.migration_markers(database) == ()
        assert database.fetch_scalar("PRAGMA data_version") == before
        with pytest.raises(TypeError):
            tp.migration_markers(object())  # type: ignore[arg-type]
    finally:
        database.close()
