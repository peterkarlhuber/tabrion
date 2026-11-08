"""The 0.2 Studio APIs of layer B on SQLite (TAB-033): ``query``/``Result``, ``stream``, ``cancel``, ``inspector``,
``on_begin``, ``savepoint`` and ``run_transaction`` (TAB-T-B15, B26..B40; contract I-100..I-106).

They are staged until 0.2 (G-T1 condition C-6): ``tabrion.Database`` does not have them yet, so these tests use the
private ``tabrion._sa._staged`` classes, which combine the mixins with the 0.1 classes exactly as 0.2 will. The
PostgreSQL side is ``tests/postgres/test_v02_postgres.py``.
"""

# ruff: noqa: S608  (the SQL is fixed text and generated table names)

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import replace
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time
import types
from typing import Any

import pytest

sqlalchemy = pytest.importorskip("sqlalchemy")
from sqlalchemy import Connection  # noqa: E402

pytest.importorskip("psycopg")

from tabrion import Database, Profile, Transaction  # noqa: E402
from tabrion._sa._staged import _StagedDatabase, _StagedTransaction  # noqa: E402
import tabrion._sa._v02  # noqa: E402
from tabrion._sa._v02 import statement_kind  # noqa: E402
import tabrion._sa.database  # noqa: E402
from tabrion._sa.result import Column, Result  # noqa: E402
from tabrion.errors import (  # noqa: E402
    Canceled,
    Classification,
    IntegrityViolation,
    Kind,
    ReadOnlyViolation,
    StatementTimeout,
    TabrionError,
    TransientError,
    UnknownDatabaseError,
    UsageError,
)
from tabrion.sqlite import SQLiteProfile  # noqa: E402
from tests.conftest import ROOT  # noqa: E402
from tests.unit.test_mutation_probes import _copy  # noqa: E402

SECRET = "SECRET-4711"
COUNT_FOREVER = "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT count(*) FROM c"
EVERY_NUMBER = "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT x FROM c"


def staged(transaction: Transaction) -> _StagedTransaction:
    """The transaction as the 0.2 type (``tabrion.Transaction`` has these methods from 0.2 on)."""
    assert isinstance(transaction, _StagedTransaction)
    return transaction


@pytest.fixture
def path(tmp_path: Path) -> Path:
    return tmp_path / "v02.db"


@pytest.fixture
def open_db(path: Path) -> Iterator[Callable[..., _StagedDatabase]]:
    opened: list[Database] = []

    def factory(profile: Profile | None = None) -> _StagedDatabase:
        database = _StagedDatabase.from_url(f"sqlite:///{path}", profile=profile)
        opened.append(database)
        assert isinstance(database, _StagedDatabase)
        return database

    yield factory
    for database in opened:
        database.close()


@pytest.fixture
def db(open_db: Callable[..., _StagedDatabase]) -> _StagedDatabase:
    database = open_db()
    database.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, n INTEGER NOT NULL, label TEXT)")
    database.execute(
        "INSERT INTO items (n, label) VALUES (:n, :label)", [{"n": n, "label": f"r{n}"} for n in range(25)]
    )
    return database


def cancel_later(transaction: Transaction, delay: float) -> tuple[threading.Thread, list[float]]:
    """Cancel ``transaction`` from a second thread after ``delay`` seconds; the list gets the time of the call."""
    called: list[float] = []

    def run() -> None:
        time.sleep(delay)
        called.append(time.monotonic())
        staged(transaction).cancel()

    thread = threading.Thread(target=run)
    thread.start()
    return thread, called


# ---------------------------------------------------------------------------------------------------------------------
# Staging (G-T1 condition C-6)
# ---------------------------------------------------------------------------------------------------------------------


def test_the_public_classes_stay_0_1(db: _StagedDatabase) -> None:
    """The staged classes are subclasses; ``tabrion.Database`` and ``tabrion.Transaction`` gain nothing in 0.1."""
    assert isinstance(db, Database)
    for name in ("query", "inspector", "on_begin", "run_transaction"):
        assert not hasattr(Database, name)
    for name in ("query", "stream", "cancel", "savepoint"):
        assert not hasattr(Transaction, name)
    with Database.from_url("sqlite://") as plain, plain.read() as transaction:
        assert type(transaction) is Transaction


def test_tb024_mixing_in_at_the_top_has_no_import_cycle(tmp_path: Path) -> None:
    """TB-024 (CC-2): ``database`` can import the mixins at its top, as 0.2 will, and the 0.2 methods then work."""
    copy = _copy(tmp_path)
    target = copy / "tabrion" / "_sa" / "database.py"
    text = target.read_text(encoding="utf-8")
    for anchor, replacement in (
        (
            "from tabrion._sa._types import",
            "from tabrion._sa._v02 import _DatabaseV02, _TransactionV02\nfrom tabrion._sa._types import",
        ),
        ("class Transaction:", "class Transaction(_TransactionV02):"),
        ("class Database:", "class Database(_DatabaseV02):"),
    ):
        assert text.count(anchor) == 1
        text = text.replace(anchor, replacement)
    target.write_text(text, encoding="utf-8")
    program = (
        "import tabrion\n"
        "with tabrion.Database.from_url('sqlite://') as db:\n"
        "    result = db.query('SELECT 1 AS one')\n"
        "    with db.read() as tx:\n"
        "        batches = list(tx.stream('SELECT 2'))\n"
        "print(result.rows, len(batches))\n"
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    env["PYTHONPATH"] = os.pathsep.join([str(copy), str(ROOT)])
    done = subprocess.run(
        [sys.executable, "-c", program],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "((1,),) 1"


# ---------------------------------------------------------------------------------------------------------------------
# query and Result (TAB-T-B26..B30)
# ---------------------------------------------------------------------------------------------------------------------


def test_b26_query_returns_a_result(db: _StagedDatabase) -> None:
    result = db.query("SELECT id, n, label, NULL AS empty FROM items WHERE n < :limit ORDER BY n", {"limit": 3})
    assert isinstance(result, Result)
    assert result.columns == (
        Column("id", None, None),
        Column("n", None, None),
        Column("label", None, None),
        Column("empty", None, None),
    )
    assert result.rows == ((1, 0, "r0", None), (2, 1, "r1", None), (3, 2, "r2", None))
    assert (result.row_count, result.truncated, result.statement_kind) == (3, False, "select")
    assert result.elapsed_ms > 0
    assert all(type(row) is tuple for row in result.rows)
    with pytest.raises(AttributeError):
        result.row_count = 0  # type: ignore[misc]  # frozen


def test_b26_query_in_a_transaction_and_no_rows(db: _StagedDatabase) -> None:
    with db.transaction() as transaction:
        written = staged(transaction).query("INSERT INTO items (n) VALUES (100)")
        assert (written.columns, written.rows, written.row_count, written.statement_kind) == ((), (), 0, "dml")
        returned = staged(transaction).query("UPDATE items SET label = 'x' WHERE n = 100 RETURNING id, label")
        assert returned.rows == ((26, "x"),)
        assert returned.statement_kind == "dml"
    assert db.query("SELECT label FROM items WHERE n = 100").rows == (("x",),)
    empty = db.query("SELECT * FROM items WHERE n < 0")
    assert (empty.rows, empty.row_count, empty.truncated, len(empty.columns)) == ((), 0, False, 3)


def test_b27_max_rows_is_enforced(open_db: Callable[..., _StagedDatabase], db: _StagedDatabase) -> None:
    result = db.query("SELECT n FROM items ORDER BY n", max_rows=10)
    assert (result.row_count, result.truncated) == (10, True)
    assert result.rows == tuple((n,) for n in range(10))
    exact = db.query("SELECT n FROM items", max_rows=25)
    assert (exact.row_count, exact.truncated) == (25, False)
    # The default is Profile.max_rows; an endless statement stops at max_rows + 1.
    small = open_db(Profile(max_rows=7))
    endless = small.query(EVERY_NUMBER)
    assert (endless.row_count, endless.truncated, endless.rows[-1]) == (7, True, (7,))
    for bad, error in ((0, ValueError), (-1, ValueError), (True, TypeError), (1.5, TypeError), ("3", TypeError)):
        with pytest.raises(error):
            db.query("SELECT 1", max_rows=bad)  # type: ignore[arg-type]


def test_b28_statement_kind() -> None:
    metadata = sqlalchemy.MetaData()
    table = sqlalchemy.Table("t", metadata, sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True))
    text = sqlalchemy.text
    cases: list[tuple[Any, str]] = [
        (text("SELECT 1"), "select"),
        (text("  -- comment\n /* block */ (SELECT 1)"), "select"),
        (text("WITH a AS (SELECT 1) SELECT * FROM a"), "select"),
        (text("VALUES (1)"), "select"),
        (text("table t"), "select"),
        (text("insert into t values (1)"), "dml"),
        (text("UPDATE t SET id = 2"), "dml"),
        (text("DELETE FROM t"), "dml"),
        (text("MERGE INTO t USING s ON true WHEN MATCHED THEN DELETE"), "dml"),
        (text("CREATE TABLE x (id int)"), "ddl"),
        (text("ALTER TABLE t ADD c int"), "ddl"),
        (text("DROP TABLE t"), "ddl"),
        (text("TRUNCATE t"), "ddl"),
        (text("EXPLAIN SELECT 1"), "other"),
        (text("PRAGMA table_info(t)"), "other"),
        (text("SHOW search_path"), "other"),
        (text(""), "other"),
        (sqlalchemy.select(table), "select"),
        (sqlalchemy.select(table).union(sqlalchemy.select(table)), "select"),
        (text("SELECT id FROM t").columns(table.c.id), "select"),
        (sqlalchemy.insert(table), "dml"),
        (sqlalchemy.update(table), "dml"),
        (sqlalchemy.delete(table), "dml"),
        (sqlalchemy.schema.CreateTable(table), "ddl"),
        (sqlalchemy.schema.DropTable(table), "ddl"),
    ]
    for executable, kind in cases:
        assert statement_kind(executable) == kind, executable


def test_b28_core_expressions(db: _StagedDatabase) -> None:
    metadata = sqlalchemy.MetaData()
    items = sqlalchemy.Table("items", metadata, autoload_with=db.engine)
    result = db.query(sqlalchemy.select(items.c.n).where(items.c.n >= 20).order_by(items.c.n))
    assert result.rows == ((20,), (21,), (22,), (23,), (24,))
    assert result.columns == (Column("n", None, None),)
    assert result.statement_kind == "select"


def test_b29_database_query_is_read_only(db: _StagedDatabase) -> None:
    with pytest.raises(ReadOnlyViolation):
        db.query("INSERT INTO items (n) VALUES (1)")
    with pytest.raises(ReadOnlyViolation):
        db.query(sqlalchemy.text("DELETE FROM items"))
    assert db.query("SELECT count(*) FROM items").rows == ((25,),)
    with db.read() as transaction:
        with pytest.raises(UsageError) as nested:
            db.query("SELECT 1")
        assert nested.value.code == "database.nested_transaction"
        assert staged(transaction).query("SELECT 1").rows == ((1,),)


def test_b29_errors_are_translated_without_values(db: _StagedDatabase) -> None:
    with pytest.raises(TabrionError) as info:
        db.query("SELECT * FROM missing WHERE label = :v", {"v": SECRET})
    assert info.value.code == "database.undefined_object"
    assert SECRET not in str(info.value) + repr(info.value)
    assert info.value.__cause__ is None and info.value.__context__ is None
    with db.transaction() as transaction, pytest.raises(IntegrityViolation):
        staged(transaction).query("INSERT INTO items (id, n) VALUES (1, :v)", {"v": SECRET})
    with pytest.raises(TypeError):
        db.query("SELECT 1", [{"a": 1}])  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        db.query(42)  # type: ignore[arg-type]


def test_b30_query_timeout(db: _StagedDatabase) -> None:
    started = time.monotonic()
    with pytest.raises(StatementTimeout) as info:
        db.query(COUNT_FOREVER, timeout=0.2)
    assert time.monotonic() - started < 2
    assert info.value.classification is not None
    assert info.value.classification.kind is Kind.TIMEOUT
    with pytest.raises(ValueError):
        db.query("SELECT 1", timeout=0)
    # The default timeout of read(timeout=...) applies to query as well.
    with db.read(timeout=0.2) as transaction, pytest.raises(StatementTimeout):
        staged(transaction).query(COUNT_FOREVER)


# ---------------------------------------------------------------------------------------------------------------------
# run_transaction (TAB-T-B31, B32; B33 with the fault proxy is in tests/postgres)
# ---------------------------------------------------------------------------------------------------------------------


def _classification(**changes: Any) -> Classification:
    base = Classification(
        kind=Kind.LOCKED,
        retryable=True,
        outcome_unknown=False,
        connection_usable=True,
        driver="sqlite3",
        sqlstate=None,
        sqlite_code="SQLITE_BUSY",
        constraint=None,
        table=None,
        column=None,
    )
    return replace(base, **changes)


def test_b31_run_transaction_retries_retryable_errors(db: _StagedDatabase) -> None:
    calls: list[int] = []

    def work(transaction: Transaction) -> int:
        calls.append(1)
        transaction.execute("INSERT INTO items (n) VALUES (:n)", {"n": 1000 + len(calls)})
        if len(calls) < 3:
            raise TransientError("database.locked", classification=_classification())
        return len(calls)

    assert db.run_transaction(work, attempts=3, backoff=0.001) == 3
    # Only the last attempt committed.
    assert db.query("SELECT n FROM items WHERE n >= 1000").rows == ((1003,),)


def test_b31_run_transaction_with_a_real_lock(path: Path, open_db: Callable[..., _StagedDatabase]) -> None:
    """A writer holds the lock past the busy timeout: BEGIN IMMEDIATE fails (LOCKED) until it lets go."""
    db = open_db(Profile(sqlite=replace(SQLiteProfile(), busy_timeout=0.1)))
    db.execute("CREATE TABLE t (n INTEGER)")
    blocker = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    blocker.execute("BEGIN IMMEDIATE")
    with pytest.raises(TransientError):
        db.execute("INSERT INTO t VALUES (0)")  # the lock is real
    release = threading.Timer(0.5, blocker.execute, ["COMMIT"])
    calls: list[int] = []

    def work(transaction: Transaction) -> None:
        calls.append(1)
        transaction.execute("INSERT INTO t VALUES (1)")

    started = time.monotonic()
    release.start()
    try:
        db.run_transaction(work, attempts=8, backoff=0.05)
    finally:
        release.join()
        blocker.close()
    assert time.monotonic() - started >= 0.4
    assert calls == [1]  # the failed attempts ended at BEGIN, before fn
    assert db.fetch_scalar("SELECT count(*) FROM t") == 1


def test_b32_run_transaction_does_not_retry_the_rest(db: _StagedDatabase) -> None:
    calls: list[int] = []

    def unique(transaction: Transaction) -> None:
        calls.append(1)
        transaction.execute("INSERT INTO items (id, n) VALUES (1, 1)")

    with pytest.raises(IntegrityViolation):
        db.run_transaction(unique, backoff=0)
    assert len(calls) == 1

    def unknown(transaction: Transaction) -> None:
        calls.append(1)
        raise TransientError("database.connection", classification=_classification(outcome_unknown=True))

    calls.clear()
    with pytest.raises(TransientError):
        db.run_transaction(unknown, backoff=0)
    assert len(calls) == 1

    def always(transaction: Transaction) -> None:
        calls.append(1)
        raise TransientError("database.locked", classification=_classification())

    calls.clear()
    with pytest.raises(TransientError):
        db.run_transaction(always, attempts=4, backoff=0)
    assert len(calls) == 4

    def own_error(transaction: Transaction) -> None:
        calls.append(1)
        raise KeyError("mine")

    calls.clear()
    with pytest.raises(KeyError):
        db.run_transaction(own_error, backoff=0)
    assert len(calls) == 1


def test_b32_run_transaction_arguments(db: _StagedDatabase) -> None:
    def work(transaction: Transaction) -> str:
        return "ok"

    assert db.run_transaction(work, isolation="serializable") == "ok"
    for kwargs, error in (
        ({"attempts": 0}, ValueError),
        ({"attempts": True}, TypeError),
        ({"attempts": 2.0}, TypeError),
        ({"backoff": -1}, ValueError),
        ({"backoff": float("nan")}, ValueError),
        ({"backoff": "1"}, TypeError),
        ({"isolation": "chaos"}, ValueError),
    ):
        with pytest.raises(error):
            db.run_transaction(work, **kwargs)
    with pytest.raises(TypeError):
        db.run_transaction("not callable")  # type: ignore[arg-type]
    with db.read(), pytest.raises(UsageError):
        db.run_transaction(work)


# ---------------------------------------------------------------------------------------------------------------------
# stream (TAB-T-B34, B35)
# ---------------------------------------------------------------------------------------------------------------------


def test_b34_stream_in_batches(db: _StagedDatabase) -> None:
    with db.read() as transaction:
        batches = list(staged(transaction).stream("SELECT n FROM items ORDER BY n", batch_size=10))
        assert [len(batch) for batch in batches] == [10, 10, 5]
        assert [row[0] for batch in batches for row in batch] == list(range(25))
        assert list(staged(transaction).stream("SELECT n FROM items", batch_size=25)) != []
        assert [len(b) for b in staged(transaction).stream("SELECT n FROM items", batch_size=5)] == [5] * 5
        assert list(staged(transaction).stream("SELECT n FROM items WHERE n < 0")) == []
        rows = next(iter(staged(transaction).stream("SELECT n, label FROM items WHERE n = :n", {"n": 3})))
        assert rows[0]._mapping == {"n": 3, "label": "r3"}


def test_b34_stream_is_lazy_and_bounded(db: _StagedDatabase) -> None:
    """An endless statement: the first batches arrive; nothing is materialized beyond the batch (fetchmany)."""
    with db.read() as transaction:
        started = time.monotonic()
        stream = staged(transaction).stream(EVERY_NUMBER, batch_size=1000)
        first = next(stream)
        second = next(stream)
        assert time.monotonic() - started < 1
        assert (first[0][0], first[-1][0], second[-1][0]) == (1, 1000, 2000)
        assert isinstance(stream, types.GeneratorType)
        stream.close()
        # The transaction goes on after the stream.
        assert staged(transaction).query("SELECT count(*) FROM items").rows == ((25,),)


def test_b34_stream_ends_with_its_transaction(db: _StagedDatabase) -> None:
    with db.read() as transaction:
        stream = staged(transaction).stream(EVERY_NUMBER, batch_size=10)
        next(stream)
    with pytest.raises(UsageError) as info:
        next(stream)
    assert info.value.code == "database.transaction_ended"
    assert isinstance(stream, types.GeneratorType)
    stream.close()
    # Not run at all: still checked when it starts.
    with db.read() as transaction:
        pending = staged(transaction).stream("SELECT 1")
    with pytest.raises(UsageError):
        next(pending)
    assert db.query("SELECT 1").rows == ((1,),)


def test_b34_stream_arguments_and_errors(db: _StagedDatabase) -> None:
    with db.read() as transaction:
        tx = staged(transaction)
        for bad, error in ((0, ValueError), (True, TypeError), (2.5, TypeError)):
            with pytest.raises(error):
                tx.stream("SELECT 1", batch_size=bad)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            tx.stream("SELECT 1", timeout=-1)
        with pytest.raises(TypeError):
            tx.stream(1)  # type: ignore[arg-type]
        with pytest.raises(TabrionError) as missing:
            next(tx.stream("SELECT * FROM missing WHERE n = :v", {"v": SECRET}))
        assert missing.value.code == "database.undefined_object"
        assert SECRET not in str(missing.value) + repr(missing.value)
    with db.transaction() as transaction, pytest.raises(UsageError) as norows:
        next(staged(transaction).stream("UPDATE items SET n = n"))
    assert norows.value.code == "database.no_rows_returned"


def test_b34_stream_from_another_thread_is_refused(db: _StagedDatabase) -> None:
    with db.read() as transaction:
        stream = staged(transaction).stream("SELECT n FROM items", batch_size=5)
        next(stream)
        errors: list[BaseException] = []

        def other() -> None:
            try:
                next(stream)
            except BaseException as exc:
                errors.append(exc)

        thread = threading.Thread(target=other)
        thread.start()
        thread.join()
        assert isinstance(errors[0], UsageError)
        assert errors[0].code == "database.wrong_thread"


def test_b35_stream_timeout_per_batch(db: _StagedDatabase) -> None:
    slow = "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT x FROM c WHERE x % 5000000 = 0"
    with db.read() as transaction:
        stream = staged(transaction).stream(slow, batch_size=1, timeout=0.2)
        with pytest.raises(StatementTimeout):
            next(stream)


def test_b35_cancel_a_stream(db: _StagedDatabase) -> None:
    with pytest.raises(Canceled), db.read() as transaction:
        stream = staged(transaction).stream(EVERY_NUMBER, batch_size=100)
        next(stream)
        staged(transaction).cancel()  # between batches: the next one is refused without running
        next(stream)


# ---------------------------------------------------------------------------------------------------------------------
# cancel (TAB-T-B15, B40)
# ---------------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("method", ["query", "fetch_scalar", "stream"])
def test_b15_cancel_from_a_second_thread(db: _StagedDatabase, method: str) -> None:
    """The statement stops within 1 s of cancel() (TAB-033 acceptance); the transaction rolls back."""
    with pytest.raises(Canceled) as info, db.transaction() as transaction:
        transaction.execute("INSERT INTO items (n) VALUES (500)")
        thread, called = cancel_later(transaction, 0.3)
        try:
            if method == "query":
                staged(transaction).query(COUNT_FOREVER)
            elif method == "stream":
                next(staged(transaction).stream("SELECT x FROM (" + EVERY_NUMBER + ") WHERE x < 0"))
            else:
                transaction.fetch_scalar(COUNT_FOREVER)
        finally:
            stopped = time.monotonic()
            thread.join()
    assert stopped - called[0] < 1.0
    assert info.value.classification is not None
    assert info.value.classification.kind is Kind.CANCELED
    assert db.query("SELECT count(*) FROM items WHERE n = 500").rows == ((0,),)
    assert db.query("SELECT 1").rows == ((1,),)  # the pooled connection works afterwards


def test_b40_a_canceled_transaction_is_never_committed(db: _StagedDatabase) -> None:
    with pytest.raises(Canceled), db.transaction() as transaction:
        transaction.execute("INSERT INTO items (n) VALUES (600)")
        staged(transaction).cancel()  # nothing runs: the transaction is marked
        with pytest.raises(Canceled):
            transaction.execute("INSERT INTO items (n) VALUES (601)")
        # The caller swallows the error; the end of the block still refuses to commit.
    assert db.query("SELECT count(*) FROM items WHERE n >= 600").rows == ((0,),)


def test_b40_cancel_after_the_end_is_a_no_op(open_db: Callable[..., _StagedDatabase]) -> None:
    """A late cancel() cannot reach the next transaction on the same pooled connection."""
    db = open_db(Profile(pool_size=1))
    with db.read() as first:
        assert staged(first).query("SELECT 1").rows == ((1,),)
    with db.read() as second:
        staged(first).cancel()
        assert staged(second).query("SELECT count(*) FROM (" + EVERY_NUMBER + " LIMIT 300000)").rows == ((300000,),)
    staged(first).cancel()  # idempotent, never raises


def test_b40_cancel_from_many_threads(db: _StagedDatabase) -> None:
    with pytest.raises(Canceled), db.read() as transaction:
        threads = [threading.Thread(target=staged(transaction).cancel) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        staged(transaction).query("SELECT 1")


# ---------------------------------------------------------------------------------------------------------------------
# inspector (TAB-T-B36)
# ---------------------------------------------------------------------------------------------------------------------


def test_b36_inspector(db: _StagedDatabase) -> None:
    db.execute("CREATE INDEX items_n ON items (n)")
    db.execute("CREATE VIEW small AS SELECT n FROM items WHERE n < 5")
    with db.inspector() as inspector:
        assert isinstance(inspector, sqlalchemy.Inspector)
        assert inspector.get_table_names() == ["items"]
        assert inspector.get_view_names() == ["small"]
        columns = inspector.get_columns("items")
        assert [(c["name"], str(c["type"]), c["nullable"]) for c in columns] == [
            ("id", "INTEGER", True),
            ("n", "INTEGER", False),
            ("label", "TEXT", True),
        ]
        assert inspector.get_pk_constraint("items")["constrained_columns"] == ["id"]
        assert [i["name"] for i in inspector.get_indexes("items")] == ["items_n"]


def test_b36_inspector_is_read_only(db: _StagedDatabase) -> None:
    with pytest.raises(ReadOnlyViolation), db.inspector() as inspector:
        connection = inspector.bind
        assert isinstance(connection, Connection)
        assert connection.exec_driver_sql("PRAGMA query_only").scalar() == 1
        connection.exec_driver_sql("DELETE FROM items")
    assert db.query("SELECT count(*) FROM items").rows == ((25,),)
    with db.inspector() as inspector, pytest.raises(UsageError):
        db.query("SELECT 1")  # it holds a read() of this thread


def test_b36_inspector_catalog_of_500_tables(db: _StagedDatabase) -> None:
    """STU-010 lists the catalog of 500 tables in under 1 s; the bound here leaves room for slow CI runners."""
    with db.transaction() as transaction:
        for i in range(500):
            transaction.execute(f"CREATE TABLE t_{i:03} (id INTEGER PRIMARY KEY, a TEXT NOT NULL, b REAL)")
    started = time.monotonic()
    with db.inspector() as inspector:
        names = inspector.get_table_names()
        columns = inspector.get_multi_columns()
    elapsed = time.monotonic() - started
    assert len(names) == 501 and len(columns) == 501
    assert elapsed < 3.0, elapsed


# ---------------------------------------------------------------------------------------------------------------------
# on_begin, savepoint (TAB-T-B37, B38; experimental)
# ---------------------------------------------------------------------------------------------------------------------


def test_b37_on_begin(db: _StagedDatabase) -> None:
    seen: list[tuple[bool, bool]] = []

    def callback(connection: Connection, read_only: bool) -> None:
        seen.append((read_only, connection.in_transaction()))

    unregister = db.on_begin(callback)
    db.query("SELECT 1")
    db.execute("INSERT INTO items (n) VALUES (1)")
    with db.inspector():
        pass
    assert seen == [(True, True), (False, True), (True, True)]
    unregister()
    unregister()  # idempotent
    db.query("SELECT 1")
    assert len(seen) == 3
    # The same callback twice: two registrations, unregistered one by one.
    first = db.on_begin(callback)
    db.on_begin(callback)
    db.query("SELECT 1")
    assert len(seen) == 5
    first()
    db.query("SELECT 1")
    assert len(seen) == 6
    with pytest.raises(TypeError):
        db.on_begin("no")  # type: ignore[arg-type]


def test_b37_on_begin_errors_roll_back(db: _StagedDatabase) -> None:
    def fails(connection: Connection, read_only: bool) -> None:
        connection.exec_driver_sql("INSERT INTO items (n) VALUES (700)")
        raise RuntimeError("callback")

    unregister = db.on_begin(fails)
    with pytest.raises(RuntimeError), db.transaction():
        pytest.fail("the block must not run")
    unregister()

    def database_error(connection: Connection, read_only: bool) -> None:
        connection.exec_driver_sql("SELECT * FROM missing")

    unregister = db.on_begin(database_error)
    with pytest.raises(TabrionError) as info, db.read():
        pass
    assert info.value.code == "database.undefined_object"
    unregister()
    assert db.query("SELECT count(*) FROM items WHERE n = 700").rows == ((0,),)
    db.close()
    with pytest.raises(UsageError):
        db.on_begin(lambda connection, read_only: None)


def test_b38_savepoint(db: _StagedDatabase) -> None:
    with db.transaction() as transaction:
        tx = staged(transaction)
        tx.execute("INSERT INTO items (n) VALUES (800)")
        with pytest.raises(IntegrityViolation), tx.savepoint():
            tx.execute("INSERT INTO items (n) VALUES (801)")
            tx.execute("INSERT INTO items (id, n) VALUES (1, 1)")
        with tx.savepoint():
            tx.execute("INSERT INTO items (n) VALUES (802)")
        with pytest.raises(KeyError), tx.savepoint():
            tx.execute("INSERT INTO items (n) VALUES (803)")
            raise KeyError("mine")
    assert db.query("SELECT n FROM items WHERE n >= 800 ORDER BY n").rows == ((800,), (802,))
    with db.read() as transaction, staged(transaction).savepoint():
        assert staged(transaction).query("SELECT 1").rows == ((1,),)
    with db.read() as transaction:
        pass
    with pytest.raises(UsageError), staged(transaction).savepoint():
        pass


# ---------------------------------------------------------------------------------------------------------------------
# One statement per call, read-only (TAB-T-B39)
# ---------------------------------------------------------------------------------------------------------------------


def test_b39_one_statement_per_query(db: _StagedDatabase) -> None:
    """sqlite3 runs one statement per execute: a second one is refused before anything runs."""
    for text in (
        "SELECT 1; DELETE FROM items",
        "SELECT 1; PRAGMA query_only = OFF; DELETE FROM items",
    ):
        with pytest.raises(UnknownDatabaseError):
            db.query(text)
        with pytest.raises(UnknownDatabaseError), db.read() as transaction:
            next(staged(transaction).stream(text))
    with pytest.raises(UsageError):  # refused before sending (N-1 of the re-review of #32)
        db.query("COMMIT; DELETE FROM items")
    assert db.query("SELECT count(*) FROM items").rows == ((25,),)


def test_b39_lifting_read_only_in_one_statement_is_refused(db: _StagedDatabase) -> None:
    """``PRAGMA query_only = OFF`` alone writes nothing, and the read-back before COMMIT refuses it (I-56)."""
    with pytest.raises(UsageError) as info:
        db.query("PRAGMA query_only = OFF")
    assert info.value.code == "database.read_only"
    with pytest.raises(UsageError), db.read() as transaction:
        staged(transaction).query("PRAGMA query_only = OFF")
        staged(transaction).query("DELETE FROM items")
    assert db.query("SELECT count(*) FROM items").rows == ((25,),)


def test_b39_read_only_profile(path: Path, db: _StagedDatabase, open_db: Callable[..., _StagedDatabase]) -> None:
    readonly = open_db(Profile.readonly())
    assert readonly.query("SELECT count(*) FROM items").rows == ((25,),)
    with pytest.raises(ReadOnlyViolation):
        readonly.query("DELETE FROM items")
    with pytest.raises(UsageError):
        readonly.run_transaction(lambda transaction: None)
    with readonly.inspector() as inspector:
        assert inspector.get_table_names() == ["items"]


# ---------------------------------------------------------------------------------------------------------------------
# Review fixes of #32 (L-1, L-3)
# ---------------------------------------------------------------------------------------------------------------------


def test_l1_the_callers_sql_cannot_end_the_transaction(db: _StagedDatabase) -> None:
    """``COMMIT`` alone would end the ``read()``; a ``PRAGMA query_only = OFF`` and an autocommitted write could
    follow. The call raises, every later statement is refused, and the block does not end quietly."""
    with pytest.raises(UsageError) as at_end, db.read() as transaction:
        with pytest.raises(UsageError) as info:
            staged(transaction).query("COMMIT")
        assert info.value.code == "database.transaction_ended"
        for text in ("PRAGMA query_only = OFF", "DELETE FROM items"):
            with pytest.raises(UsageError):
                staged(transaction).query(text)
        with pytest.raises(UsageError):
            next(staged(transaction).stream("SELECT 1"))
    assert at_end.value.code == "database.transaction_ended"
    assert db.query("SELECT count(*) FROM items").rows == ((25,),)
    with db.transaction() as transaction:  # the pool hands out a working connection again
        transaction.execute("INSERT INTO items (n) VALUES (1)")


REFUSED = [
    "COMMIT",
    "commit and chain",
    "  ;END TRANSACTION",
    "/* x */ ROLLBACK",
    "-- x\nABORT",
    "/* a /* b */ c */ COMMIT",
    "/* a /* b */ COMMIT",
    "(COMMIT",
    "ROLLBACK TO SAVEPOINT s",
    "PREPARE TRANSACTION 'x'",
]


@pytest.mark.parametrize("method", ["query", "stream"])
@pytest.mark.parametrize("text", REFUSED)
def test_n1_transaction_control_is_refused_before_sending(db: _StagedDatabase, text: str, method: str) -> None:
    """N-1 of the re-review of #32: a statement that would end the transaction is never sent, whatever comes
    first (whitespace, ``;``, ``(``, comments read nested or not). The write before it is rolled back."""
    with pytest.raises(UsageError) as at_end, db.transaction() as transaction:
        transaction.execute("INSERT INTO items (n) VALUES (300)")
        with pytest.raises(UsageError) as info:
            getattr(staged(transaction), method)(text)
        assert info.value.code == "database.transaction_ended"
        assert transaction._dbapi.in_transaction  # not sent
        with pytest.raises(UsageError):
            transaction.execute("INSERT INTO items (n) VALUES (301)")
    assert at_end.value.code == "database.transaction_ended"
    assert db.query("SELECT count(*) FROM items WHERE n >= 300").rows == ((0,),)


@pytest.mark.parametrize(
    "text",
    ["SELECT 'commit'", "/* commit */ SELECT 1", "-- rollback\nSELECT 1", "SELECT 1 AS endless", "VALUES (2)"],
)
def test_n1_other_statements_pass(db: _StagedDatabase, text: str) -> None:
    with db.read() as transaction:
        assert len(staged(transaction).query(text).rows) == 1


@pytest.mark.parametrize("method", ["query", "stream"])
@pytest.mark.parametrize("text", ["COMMIT", "ROLLBACK", "END"])
def test_n2_status_check_after_the_statement(
    db: _StagedDatabase, monkeypatch: pytest.MonkeyPatch, text: str, method: str
) -> None:
    """N-2 of the re-review of #32: the second line on its own (the refusal before sending switched off). After
    ``stream("ROLLBACK")`` or ``query("ROLLBACK")`` the next write would autocommit; it is refused instead."""
    monkeypatch.setattr(tabrion._sa._v02, "_refuse_transaction_control", lambda transaction, executable: None)
    with pytest.raises(UsageError) as at_end, db.transaction() as transaction:
        staged(transaction).query("INSERT INTO items (n) VALUES (200)")
        with pytest.raises(UsageError) as info:
            if method == "stream":
                next(staged(transaction).stream(text))
            else:
                staged(transaction).query(text)
        assert info.value.code == "database.transaction_ended"
        with pytest.raises(UsageError):
            staged(transaction).query("INSERT INTO items (n) VALUES (201)")
    assert at_end.value.code == "database.transaction_ended"
    assert db.query("SELECT count(*) FROM items WHERE n = 201").rows == ((0,),)
    if text == "ROLLBACK":
        assert db.query("SELECT count(*) FROM items WHERE n = 200").rows == ((0,),)


def test_n3_the_end_of_the_block_waits_for_a_cancel_being_sent(
    db: _StagedDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N-3 of the re-review of #32: the end of the block does not overtake a cancel request that is being sent
    (the connection could be handed out while the request is still on its way)."""
    events: list[str] = []
    original = tabrion._sa.database._send_cancel

    def slow(dialect: str, dbapi: Any) -> None:
        time.sleep(0.5)
        events.append("sent")
        original(dialect, dbapi)

    monkeypatch.setattr(tabrion._sa.database, "_send_cancel", slow)
    with pytest.raises(Canceled), db.read() as transaction:
        canceler, _ = cancel_later(transaction, 0.2)
        staged(transaction).query(COUNT_FOREVER)
    events.append("block ended")
    canceler.join()
    assert events[0] == "sent"


def test_l3_cancel_sends_only_while_a_statement_runs(db: _StagedDatabase, monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[str] = []
    original = tabrion._sa.database._send_cancel

    def counting(dialect: str, dbapi: Any) -> None:
        sent.append(dialect)
        original(dialect, dbapi)

    monkeypatch.setattr(tabrion._sa.database, "_send_cancel", counting)
    with pytest.raises(Canceled), db.read() as transaction:
        staged(transaction).cancel()
        assert sent == []
    with pytest.raises(Canceled), db.read() as transaction:
        threading.Timer(0.2, staged(transaction).cancel).start()
        staged(transaction).query(COUNT_FOREVER)
    assert 1 <= len(sent) <= 4
