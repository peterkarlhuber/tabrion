"""The guided write transaction on PostgreSQL: ``Transaction.execute_checked`` and ``tabrion.rowedit`` (TAB-XW-6,
contract section 5.11, I-120 and I-121; ADR 0015 section 9 N-5).

Public since 0.3: the tests use ``tabrion.Database`` and ``tabrion.rowedit``, as on SQLite
(``tests/unit/test_execute_checked.py``, ``tests/unit/test_rowedit.py``); before 0.3 they used the staged classes.
Every table lives in the ``schema`` fixture; a second writer is a plain psycopg connection.
"""

# The SQL is built from the schema fixture's generated name and fixed text, never from outside input.
# ruff: noqa: S608

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

pytestmark = pytest.mark.postgres

sqlalchemy = pytest.importorskip("sqlalchemy")
psycopg = pytest.importorskip("psycopg")
from sqlalchemy import Table  # noqa: E402
from sqlalchemy.dialects import postgresql  # noqa: E402

from tabrion import Database, Transaction  # noqa: E402
from tabrion.errors import (  # noqa: E402
    IntegrityViolation,
    ReadOnlyViolation,
    RowCountMismatch,
    StatementError,
    StatementTimeout,
    UsageError,
)
from tabrion.rowedit import delete_row, insert_row, update_row  # noqa: E402
from tests.canary import assert_clean, new_canary  # noqa: E402
from tests.postgres.test_database import sa_url  # noqa: E402

ROW: dict[str, Any] = {
    "id": 1,
    "label": "one",
    "code": "abc",
    "price": 0.1,
    "amount": 0.1,
    "doc": {"b": 1, "a": [1, 2]},
    "docb": {"x": 1.5},
    "mood": "ok",
    "data": b"\x00\x01",
}


@pytest.fixture
def table(schema: str, admin: Any) -> Any:
    """``items`` in the fixture's schema (``tag`` unique): a nondeterministic (case-insensitive) collation,
    ``real``, ``json``, ``jsonb``, an enum and ``bytea``."""
    admin.execute(f"CREATE COLLATION {schema}.ci (provider = icu, locale = 'und-u-ks-level2', deterministic = false)")
    admin.execute(f"CREATE TYPE {schema}.mood AS ENUM ('ok', 'sad')")
    admin.execute(
        f"CREATE TABLE {schema}.items (id int PRIMARY KEY, label text, tag text UNIQUE, code text COLLATE {schema}.ci,"
        f" price real, amount double precision, doc json, docb jsonb, mood {schema}.mood, data bytea)"
    )
    metadata = sqlalchemy.MetaData()
    return sqlalchemy.Table(
        "items",
        metadata,
        sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True),
        sqlalchemy.Column("label", sqlalchemy.Text),
        sqlalchemy.Column("tag", sqlalchemy.Text),
        sqlalchemy.Column("code", sqlalchemy.Text),
        sqlalchemy.Column("price", postgresql.REAL),
        sqlalchemy.Column("amount", postgresql.DOUBLE_PRECISION),
        sqlalchemy.Column("doc", postgresql.JSON),
        sqlalchemy.Column("docb", postgresql.JSONB),
        sqlalchemy.Column("mood", postgresql.ENUM("ok", "sad", name="mood", schema=schema, create_type=False)),
        sqlalchemy.Column("data", sqlalchemy.LargeBinary),
        schema=schema,
    )


@pytest.fixture
def db(admin_url: str, table: Table) -> Iterator[Database]:
    database = Database.from_url(sa_url(admin_url))
    with database.transaction() as tx:
        run(tx, insert_row(table, ROW))
        run(tx, insert_row(table, {"id": 2}))
        run(tx, insert_row(table, {"id": 3, "label": "three"}))
    yield database
    database.close()


def run(tx: Any, statement: Any, **limits: Any) -> int:
    assert isinstance(tx, Transaction)
    return tx.execute_checked(statement, **(limits or {"expect_rowcount": 1}))


def read(db: Database, table: Table, key: int = 1) -> dict[str, Any]:
    found = db.fetch_one(sqlalchemy.select(table).where(table.c.id == key))
    assert found is not None
    return dict(found._mapping)


def test_round_trip_with_every_old_value_as_read(db: Database, table: Table) -> None:
    """No false conflict for the values as read: ``real`` (cast to the column's type), ``json`` (as ``jsonb``),
    ``jsonb``, an enum (no collation), a nondeterministic collation (``"C"``) and ``bytea``."""
    seen = read(db, table)
    assert seen["price"] == pytest.approx(0.1) and seen["doc"] == ROW["doc"]
    with db.transaction() as tx:
        assert run(tx, update_row(table, {"id": 1}, seen, {"label": "uno", "price": 0.2})) == 1
    seen = read(db, table)
    with db.transaction() as tx:
        assert run(tx, delete_row(table, {"id": 1}, seen)) == 1
    assert db.fetch_scalar(f"SELECT count(*) FROM {table.schema}.items WHERE id = 1") == 0


def test_concurrent_change_conflicts_and_leaves_the_row(db: Database, table: Table, admin: Any) -> None:
    seen = read(db, table)
    admin.execute(f"UPDATE {table.schema}.items SET label = 'theirs' WHERE id = 1")
    for statement in (update_row(table, {"id": 1}, seen, {"label": "mine"}), delete_row(table, {"id": 1}, seen)):
        with pytest.raises(RowCountMismatch) as caught, db.transaction() as tx:
            run(tx, statement)
        assert caught.value.code == "transaction.rowcount_mismatch"
        assert dict(caught.value.details) == {"expected": 1, "actual": 0}
    assert read(db, table)["label"] == "theirs"


def test_null_in_old_is_compared_not_distinct(db: Database, table: Table) -> None:
    with db.transaction() as tx:
        assert run(tx, update_row(table, {"id": 2}, {"label": None, "doc": None, "price": None}, {"label": "x"})) == 1
    with pytest.raises(RowCountMismatch), db.transaction() as tx:
        run(tx, update_row(table, {"id": 2}, {"label": None}, {"label": "y"}))
    with pytest.raises(RowCountMismatch), db.transaction() as tx:
        run(tx, update_row(table, {"id": 2}, {"mood": "ok"}, {"label": "y"}))
    assert read(db, table, 2)["label"] == "x"


def test_nondeterministic_collation_cannot_hide_a_change(db: Database, table: Table, admin: Any) -> None:
    seen = read(db, table)
    admin.execute(f"UPDATE {table.schema}.items SET code = 'ABC' WHERE id = 1")
    assert admin.execute(f"SELECT code = 'abc' FROM {table.schema}.items WHERE id = 1").fetchone()[0] is True
    with pytest.raises(RowCountMismatch), db.transaction() as tx:
        run(tx, update_row(table, {"id": 1}, seen, {"label": "mine"}))
    assert read(db, table)["code"] == "ABC"


def test_float_and_json_old_values_may_conflict(db: Database, table: Table) -> None:
    """N-5 on PostgreSQL: a rounded float conflicts (safe side); ``jsonb`` compares parsed values, so key order and
    blanks of a ``json`` column do not count, but a different number does."""
    with db.transaction() as tx:
        tx.execute(
            f"UPDATE {table.schema}.items SET amount = 0.1::float8 + 0.2::float8, doc = :doc WHERE id = 1",
            {"doc": '{"a": [1,2],  "b": 1}'},
        )
    for old in ({"amount": 0.3}, {"doc": {"a": [1, 2], "b": 2}}):
        with pytest.raises(RowCountMismatch), db.transaction() as tx:
            run(tx, update_row(table, {"id": 1}, old, {"label": "mine"}))
    with db.transaction() as tx:
        run(tx, update_row(table, {"id": 1}, {"amount": 0.1 + 0.2, "doc": {"b": 1, "a": [1, 2]}}, {"label": "mine"}))
    assert read(db, table)["label"] == "mine"


def test_max_affected_rolls_back_a_multi_row_statement(db: Database, table: Table) -> None:
    with pytest.raises(RowCountMismatch) as caught, db.transaction() as tx:
        run(tx, sqlalchemy.update(table).values(label="all"), max_affected=2)
    assert caught.value.code == "transaction.too_many_rows"
    assert dict(caught.value.details) == {"limit": 2, "actual": 3}
    assert read(db, table)["label"] == "one"


def test_caught_mismatch_still_rolls_back(db: Database, table: Table) -> None:
    with pytest.raises(RowCountMismatch), db.transaction() as tx:
        run(tx, update_row(table, {"id": 3}, {"label": "three"}, {"label": "changed"}))
        with pytest.raises(RowCountMismatch):
            run(tx, sqlalchemy.update(table).values(label="all"), expect_rowcount=1)
        with pytest.raises(UsageError) as later:
            tx.execute("SELECT 1")
        assert later.value.code == "transaction.failed"
    assert read(db, table, 3)["label"] == "three"


def test_several_commands_cannot_hide_the_count(db: Database, table: Table) -> None:
    """The statement goes through the extended protocol: one command, so the count is that command's (I-120 d)."""
    text = f"UPDATE {table.schema}.items SET label = 'a'; UPDATE {table.schema}.items SET label = 'b' WHERE id = 1"
    with pytest.raises(StatementError), db.transaction() as tx:
        run(tx, text)
    assert read(db, table)["label"] == "one"


def test_timeout_is_statement_timeout(db: Database, table: Table) -> None:
    """``SET LOCAL statement_timeout`` for the statement (contract section 5.3)."""
    sleepy = f"UPDATE {table.schema}.items SET label = 'late' WHERE pg_catalog.pg_sleep(5)::text = ''"
    with pytest.raises(StatementTimeout), db.transaction() as tx:
        run(tx, sleepy, max_affected=3, timeout=0.2)
    assert read(db, table)["label"] == "one"


def test_read_transaction_refuses_it(db: Database, table: Table) -> None:
    with db.read() as tx:
        with pytest.raises(ReadOnlyViolation):
            run(tx, sqlalchemy.update(table).values(label="x"), max_affected=10)
        assert tx.fetch_scalar(f"SELECT count(*) FROM {table.schema}.items") == 3


def test_canary_values_never_reach_an_error(db: Database, table: Table) -> None:
    """Neither a conflict nor a driver error of a row statement shows a value (PostgreSQL puts the duplicate key value
    into the error's DETAIL)."""
    canary = new_canary("pg")
    with pytest.raises(RowCountMismatch) as conflict, db.transaction() as tx:
        run(tx, update_row(table, {"id": 1}, {"label": canary}, {"label": canary}))
    assert_clean(conflict.value, canary)
    with db.transaction() as tx:
        run(tx, insert_row(table, {"id": 50, "tag": canary}))
    with pytest.raises(IntegrityViolation) as duplicate, db.transaction() as tx:
        run(tx, insert_row(table, {"id": 51, "tag": canary}))  # DETAIL: Key (tag)=(<canary>) already exists
    assert_clean(duplicate.value, canary)


def test_rules_and_triggers_are_not_counted(db: Database, table: Table, admin: Any) -> None:
    """Review I-1 of #78 (documents the behavior, I-120 g): the count is the statement's own. A rule (``DO ALSO``) and
    a trigger change other rows in the same transaction without being counted or limited, and the transaction
    commits; Studio's preview names them (TX-16)."""
    schema = table.schema
    admin.execute(f"CREATE TABLE {schema}.other (id int PRIMARY KEY)")
    admin.execute(f"INSERT INTO {schema}.other SELECT g FROM generate_series(1, 100) g")
    admin.execute(f"CREATE TABLE {schema}.audit (id int)")
    admin.execute(f"CREATE RULE wipe AS ON UPDATE TO {schema}.items DO ALSO DELETE FROM {schema}.other")
    admin.execute(
        f"CREATE FUNCTION {schema}.audit_fn() RETURNS trigger LANGUAGE plpgsql AS"
        f" $$BEGIN INSERT INTO {schema}.audit SELECT g FROM generate_series(1, 50) g; RETURN NEW; END$$"
    )
    admin.execute(
        f"CREATE TRIGGER audit AFTER UPDATE ON {schema}.items FOR EACH ROW EXECUTE FUNCTION {schema}.audit_fn()"
    )
    seen = read(db, table)
    with db.transaction() as tx:
        assert run(tx, update_row(table, {"id": 1}, seen, {"label": "uno"}), expect_rowcount=1, max_affected=1) == 1
    assert db.fetch_scalar(f"SELECT count(*) FROM {schema}.other") == 0  # the rule deleted 100 rows, uncounted
    assert db.fetch_scalar(f"SELECT count(*) FROM {schema}.audit") == 50  # the trigger inserted 50, uncounted
    assert read(db, table)["label"] == "uno"
