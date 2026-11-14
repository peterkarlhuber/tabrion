"""``tabrion.rowedit`` on SQLite (TAB-XW-6, contract section 5.11, I-121; ADR 0015 section 9 N-5).

Planned for 0.3 and staged until then (``tabrion._since``): the functions are tested at their staged location
``tabrion._sa._rowedit`` and run through the private ``tabrion._sa._staged`` classes
(``tests/unit/test_v03_gate.py`` proves that ``tabrion.rowedit`` re-exports exactly these objects from 0.3 on). The
PostgreSQL side is ``tests/postgres/test_rowedit_postgres.py``.
"""

from __future__ import annotations

from collections.abc import Iterator
import json
from pathlib import Path
from typing import Any

import pytest

sqlalchemy = pytest.importorskip("sqlalchemy")
from sqlalchemy import Table  # noqa: E402
from sqlalchemy.dialects.postgresql.psycopg import PGDialect_psycopg  # noqa: E402
from sqlalchemy.dialects.sqlite.pysqlite import SQLiteDialect_pysqlite  # noqa: E402

from tabrion import Database  # noqa: E402
from tabrion._sa._rowedit import delete_row, insert_row, update_row  # noqa: E402
from tabrion._sa._staged import _StagedDatabase, _StagedTransaction  # noqa: E402
from tabrion.errors import UsageError, _RowCountMismatch  # noqa: E402
from tests.canary import assert_clean, new_canary  # noqa: E402

metadata = sqlalchemy.MetaData()
ITEMS = sqlalchemy.Table(
    "items",
    metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True),
    sqlalchemy.Column("label", sqlalchemy.String),
    sqlalchemy.Column("code", sqlalchemy.String(collation="NOCASE")),
    sqlalchemy.Column("price", sqlalchemy.Float),
    sqlalchemy.Column("doc", sqlalchemy.JSON),
    sqlalchemy.Column("flag", sqlalchemy.Boolean),
)
ODD = sqlalchemy.Table(
    'we"ird table',
    metadata,
    sqlalchemy.Column("select", sqlalchemy.Integer, primary_key=True),
    sqlalchemy.Column("from where", sqlalchemy.String),
)
PAIR = sqlalchemy.Table(
    "pair",
    metadata,
    sqlalchemy.Column("a", sqlalchemy.Integer, primary_key=True),
    sqlalchemy.Column("b", sqlalchemy.String, primary_key=True),
    sqlalchemy.Column("v", sqlalchemy.String),
)
NO_KEY = Table("no_key", metadata, sqlalchemy.Column("v", sqlalchemy.String))

SQLITE: Any = SQLiteDialect_pysqlite()
POSTGRESQL: Any = PGDialect_psycopg()  # type: ignore[no-untyped-call]
"""Compilers only; neither connects."""

ROW: dict[str, Any] = {"id": 1, "label": "one", "code": "abc", "price": 2.5, "doc": {"a": 1}, "flag": True}


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    database = _StagedDatabase.from_url(f"sqlite:///{tmp_path / 'rowedit.db'}")
    with database.transaction() as tx:
        metadata.create_all(tx.connection)
        run(tx, insert_row(ITEMS, ROW))
        run(tx, insert_row(ITEMS, {"id": 2, "label": None}))
    yield database
    database.close()


def run(tx: Any, statement: Any) -> int:
    assert isinstance(tx, _StagedTransaction)
    return tx.execute_checked(statement, expect_rowcount=1)


def row(db: Database, key: int = 1) -> dict[str, Any]:
    found = db.fetch_one(sqlalchemy.select(ITEMS).where(ITEMS.c.id == key))
    assert found is not None
    return dict(found._mapping)


def compiled(statement: Any, dialect: Any) -> Any:
    return statement.compile(dialect=dialect)


# --- Statements: identifiers quoted by SQLAlchemy, values bound -------------------------------------------------------


@pytest.mark.parametrize("dialect", [SQLITE, POSTGRESQL], ids=["sqlite", "postgresql"])
def test_values_are_bound_never_in_the_sql_text(dialect: Any) -> None:
    canary = new_canary("bind")
    statements = [
        insert_row(ODD, {"select": 7, "from where": canary}),
        update_row(ODD, {"select": 7}, {"from where": canary}, {"from where": canary + "2"}),
        delete_row(ODD, {"select": 7}, {"from where": canary}),
    ]
    for statement in statements:
        result = compiled(statement, dialect)
        assert canary not in str(result)
        assert canary in "".join(str(value) for value in result.params.values())
        assert '"we""ird table"' in str(result)  # the dialect's preparer quotes and escapes the names
        assert '"select"' in str(result) and '"from where"' in str(result)


def test_an_sql_expression_as_a_value_is_bound_not_compiled(db: Database) -> None:
    """A value is always a bound parameter: a SQL construct passed as a value is never compiled into the text."""
    expression = sqlalchemy.literal_column("label || 'x'")
    statement = update_row(ITEMS, {"id": 1}, {"label": "one"}, {"label": expression})
    assert "label || 'x'" not in str(compiled(statement, SQLITE))


def test_update_predicate_on_sqlite_and_postgresql() -> None:
    statement = update_row(ITEMS, {"id": 1}, {"label": None, "code": "abc", "price": 1.5, "doc": {}}, {"label": "x"})
    lite = str(compiled(statement, SQLITE))
    assert "items.id = ?" in lite
    assert '(items.label COLLATE "BINARY") IS NULL' in lite
    assert '(items.code COLLATE "BINARY") IS ?' in lite
    assert "items.price IS ?" in lite and "items.doc IS ?" in lite
    assert "DISTINCT" not in lite and "= 1" not in lite
    pg = str(compiled(statement, POSTGRESQL))
    assert '(items.label COLLATE "C") IS NOT DISTINCT FROM NULL' in pg
    assert '(items.code COLLATE "C") IS NOT DISTINCT FROM %(param_' in pg
    assert "items.price IS NOT DISTINCT FROM CAST(%(param_" in pg
    assert "CAST(items.doc AS JSONB) IS NOT DISTINCT FROM CAST(%(param_" in pg


# --- Primary key ------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("table", "key"),
    [
        (NO_KEY, {"v": "x"}),
        (NO_KEY, {}),
        (ITEMS, {}),
        (ITEMS, {"label": "one"}),
        (ITEMS, {"id": 1, "label": "one"}),
        (PAIR, {"a": 1}),
        (PAIR, {"a": 1, "b": "x", "v": "y"}),
    ],
)
def test_key_must_be_exactly_the_primary_key(table: Table, key: dict[str, Any]) -> None:
    for build in (lambda: update_row(table, key, {}, {"v": "x"}), lambda: delete_row(table, key, {})):
        with pytest.raises(UsageError) as caught:
            build()
        assert caught.value.code == "rowedit.primary_key_required"


def test_composite_primary_key(db: Database) -> None:
    with db.transaction() as tx:
        run(tx, insert_row(PAIR, {"a": 1, "b": "x", "v": "old"}))
        run(tx, update_row(PAIR, {"a": 1, "b": "x"}, {"v": "old"}, {"v": "new"}))
    assert db.fetch_scalar("SELECT v FROM pair") == "new"


@pytest.mark.parametrize(
    "build",
    [
        lambda: update_row(ITEMS, {"id": 1}, {}, {"label": "x"}),
        lambda: delete_row(ITEMS, {"id": 1}, {}),
        lambda: update_row(PAIR, {"a": 1, "b": "x"}, {}, {"v": "x"}),
    ],
    ids=["update", "delete", "composite-key"],
)
def test_empty_old_is_refused(build: Any) -> None:
    """Review L-1 of #78: without an old value the statement would win over every concurrent change."""
    with pytest.raises(UsageError) as caught:
        build()
    assert caught.value.code == "rowedit.old_required"


def test_null_key_value_is_refused() -> None:
    with pytest.raises(ValueError, match="must not be None"):
        update_row(ITEMS, {"id": None}, {}, {"label": "x"})


@pytest.mark.parametrize(
    ("build", "error"),
    [
        (lambda: insert_row("items", {"id": 1}), TypeError),  # type: ignore[arg-type]
        (lambda: insert_row(ITEMS, [("id", 1)]), TypeError),  # type: ignore[arg-type]
        (lambda: insert_row(ITEMS, {1: 1}), TypeError),  # type: ignore[dict-item]
        (lambda: update_row(ITEMS, [1], {}, {"label": "x"}), TypeError),  # type: ignore[arg-type]
        (lambda: update_row(ITEMS, {"id": 1}, {"label": "one"}, {}), ValueError),
        (lambda: update_row(ITEMS, {"id": 1}, {"nope": 1}, {"label": "x"}), ValueError),
        (lambda: delete_row(ITEMS, {"id": 1}, None), TypeError),  # type: ignore[arg-type]
    ],
)
def test_invalid_arguments(build: Any, error: type) -> None:
    with pytest.raises(error):
        build()


def test_unknown_column_message_names_no_key_or_value() -> None:
    canary = new_canary("column")
    for build in (
        lambda: insert_row(ITEMS, {canary: canary}),
        lambda: update_row(ITEMS, {"id": 1}, {canary: canary}, {"label": "x"}),
        lambda: update_row(ITEMS, {"id": 1}, {"label": "one"}, {canary: canary}),
    ):
        with pytest.raises(ValueError) as caught:
            build()
        assert canary not in str(caught.value)


# --- Running the statements -------------------------------------------------------------------------------------------


def test_insert_update_delete_round_trip(db: Database) -> None:
    current = row(db)
    assert current == ROW
    with db.transaction() as tx:
        assert run(tx, update_row(ITEMS, {"id": 1}, current, {"label": "uno", "price": 3.0})) == 1
    assert row(db)["label"] == "uno"
    seen = row(db)
    with db.transaction() as tx:
        assert run(tx, delete_row(ITEMS, {"id": 1}, seen)) == 1
    assert db.fetch_scalar("SELECT count(*) FROM items WHERE id = 1") == 0


def test_insert_with_defaults_and_none_as_sql_null(db: Database) -> None:
    with db.transaction() as tx:
        run(tx, insert_row(ITEMS, {"id": 3, "doc": None}))
    # None is SQL NULL, also for a JSON column (not the JSON text 'null').
    assert db.fetch_scalar("SELECT doc IS NULL FROM items WHERE id = 3") == 1


def test_concurrent_change_conflicts_and_leaves_the_row(db: Database) -> None:
    seen = row(db)
    with db.transaction() as tx:  # another writer changes the row after the client read it
        tx.execute("UPDATE items SET label = 'theirs' WHERE id = 1")
    for statement in (
        update_row(ITEMS, {"id": 1}, seen, {"label": "mine"}),
        delete_row(ITEMS, {"id": 1}, seen),
    ):
        with pytest.raises(_RowCountMismatch) as caught, db.transaction() as tx:
            run(tx, statement)
        assert dict(caught.value.details) == {"expected": 1, "actual": 0}
        assert row(db)["label"] == "theirs"


def test_deleted_row_conflicts(db: Database) -> None:
    seen = row(db)
    db.execute("DELETE FROM items WHERE id = 1")
    with pytest.raises(_RowCountMismatch), db.transaction() as tx:
        run(tx, update_row(ITEMS, {"id": 1}, seen, {"label": "mine"}))
    assert db.fetch_scalar("SELECT count(*) FROM items") == 1


def test_null_in_old_is_compared_null_safe(db: Database) -> None:
    """``IS`` on SQLite: an old ``None`` matches a NULL column and only that; ``=`` would match nothing."""
    with db.transaction() as tx:
        assert run(tx, update_row(ITEMS, {"id": 2}, {"label": None, "doc": None}, {"label": "set"})) == 1
    with pytest.raises(_RowCountMismatch), db.transaction() as tx:  # now no longer NULL: a conflict
        run(tx, update_row(ITEMS, {"id": 2}, {"label": None}, {"label": "again"}))
    with pytest.raises(_RowCountMismatch), db.transaction() as tx:  # a value where the column is NULL
        run(tx, update_row(ITEMS, {"id": 2}, {"price": 0.0}, {"label": "again"}))
    assert row(db, 2)["label"] == "set"


def test_case_insensitive_collation_cannot_hide_a_change(db: Database) -> None:
    """A NOCASE column: a concurrent change of only the case is still a conflict (binary comparison)."""
    seen = row(db)
    db.execute("UPDATE items SET code = 'ABC' WHERE id = 1")
    assert db.fetch_scalar("SELECT code = 'abc' FROM items WHERE id = 1") == 1  # the column's own collation
    with pytest.raises(_RowCountMismatch), db.transaction() as tx:
        run(tx, update_row(ITEMS, {"id": 1}, seen, {"label": "mine"}))
    assert row(db)["code"] == "ABC"


def test_float_and_json_old_values_may_conflict(db: Database) -> None:
    """N-5 (ADR 0015 section 9): floating-point and JSON values compare exactly as stored. A value that went through
    rounding (0.1 + 0.2 is not 0.3) or a JSON text written differently (blanks, key order) reports a conflict: the safe
    side, nothing is written. The exact value as read matches."""
    with db.transaction() as tx:
        tx.execute("UPDATE items SET price = 0.1 + 0.2, doc = :doc WHERE id = 1", {"doc": '{"b":1,  "a":2}'})
    stored = row(db)
    assert stored["price"] != 0.3 and stored["doc"] == {"b": 1, "a": 2}
    for old in ({"price": 0.3}, {"doc": {"a": 2, "b": 1}}, {"doc": json.loads('{"b":1,  "a":2}')}):
        with pytest.raises(_RowCountMismatch) as caught, db.transaction() as tx:
            run(tx, update_row(ITEMS, {"id": 1}, old, {"label": "mine"}))
        assert dict(caught.value.details) == {"expected": 1, "actual": 0}
    assert row(db) == stored  # nothing written
    with db.transaction() as tx:  # the stored float as read, and the JSON text exactly as stored, match
        run(tx, update_row(ITEMS, {"id": 1}, {"price": stored["price"]}, {"label": "mine"}))
        tx.execute("UPDATE items SET doc = :doc WHERE id = 1", {"doc": json.dumps({"a": 2})})
        run(tx, update_row(ITEMS, {"id": 1}, {"doc": {"a": 2}}, {"label": "mine2"}))
    assert row(db)["label"] == "mine2"


def test_canary_old_values_never_reach_the_conflict(db: Database) -> None:
    canary = new_canary("old")
    with pytest.raises(_RowCountMismatch) as caught, db.transaction() as tx:
        run(tx, update_row(ITEMS, {"id": 1}, {"label": canary}, {"label": canary + "-new"}))
    assert_clean(caught.value, canary)
