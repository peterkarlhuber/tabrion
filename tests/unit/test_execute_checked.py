"""``Transaction.execute_checked`` on SQLite (TAB-XW-6, contract section 5.11, I-120; ADR 0015 section 9).

Public since 0.3 (``tabrion.Transaction`` inherits the mixin of ``tabrion._sa._v03``; before 0.3 these tests used the
private staged classes, ``tests/unit/test_v03_gate.py``). The PostgreSQL side is
``tests/postgres/test_rowedit_postgres.py``.
"""

# ruff: noqa: S608  (the SQL is fixed text)

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
import threading
from typing import Any

import pytest

sqlalchemy = pytest.importorskip("sqlalchemy")

from tabrion import Database, Profile, Transaction  # noqa: E402
from tabrion.errors import (  # noqa: E402
    ReadOnlyViolation,
    RowCountMismatch,
    StatementTimeout,
    TabrionError,
    UsageError,
)
from tests.canary import assert_clean, assert_logs_clean, capture_all_logs, new_canary, phase_records  # noqa: E402

COUNT_FOREVER = "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT count(*) FROM c"


@pytest.fixture
def open_db(tmp_path: Path) -> Iterator[Callable[..., Database]]:
    opened: list[Database] = []

    def factory(profile: Profile | None = None) -> Database:
        database = Database.from_url(f"sqlite:///{tmp_path / 'checked.db'}", profile=profile)
        opened.append(database)
        return database

    yield factory
    for database in opened:
        database.close()


@pytest.fixture
def db(open_db: Callable[..., Database]) -> Database:
    database = open_db()
    database.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, n INTEGER NOT NULL, label TEXT)")
    database.execute(
        "INSERT INTO items (id, n, label) VALUES (:id, :n, :label)",
        [{"id": i, "n": i, "label": f"r{i}"} for i in range(1, 6)],
    )
    return database


def checked(transaction: Any) -> Transaction:
    assert isinstance(transaction, Transaction)
    return transaction


def labels(db: Database) -> list[tuple[int, str | None]]:
    return [(int(row.id), row.label) for row in db.fetch_all("SELECT id, label FROM items ORDER BY id")]


BEFORE = [(1, "r1"), (2, "r2"), (3, "r3"), (4, "r4"), (5, "r5")]


def test_matching_rowcount_returns_it_and_commits(db: Database) -> None:
    with db.transaction() as tx:
        count = checked(tx).execute_checked(
            "UPDATE items SET label = :l WHERE id = :id", {"l": "x", "id": 2}, expect_rowcount=1
        )
    assert count == 1
    assert labels(db)[1] == (2, "x")


def test_insert_rowcount_is_counted(db: Database) -> None:
    """SQLAlchemy closes an INSERT's cursor early; ``preserve_rowcount`` keeps the count (I-120 c)."""
    with db.transaction() as tx:
        count = checked(tx).execute_checked(
            sqlalchemy.text("INSERT INTO items (id, n, label) VALUES (9, 9, 'r9')"), expect_rowcount=1
        )
    assert count == 1


def test_mismatch_raises_and_rolls_back(db: Database) -> None:
    with pytest.raises(RowCountMismatch) as caught, db.transaction() as tx:
        checked(tx).execute("UPDATE items SET label = 'before' WHERE id = 1")
        checked(tx).execute_checked("UPDATE items SET label = 'x' WHERE id > 3", expect_rowcount=1)
    assert caught.value.code == "transaction.rowcount_mismatch"
    assert dict(caught.value.details) == {"expected": 1, "actual": 2}
    assert type(caught.value).__name__ == "RowCountMismatch"
    assert repr(caught.value) == "RowCountMismatch('transaction.rowcount_mismatch')"
    assert labels(db) == BEFORE  # also the statement before it is rolled back


def test_optimistic_conflict_zero_rows(db: Database) -> None:
    with pytest.raises(RowCountMismatch) as caught, db.transaction() as tx:
        checked(tx).execute_checked("DELETE FROM items WHERE id = 99", expect_rowcount=1)
    assert dict(caught.value.details) == {"expected": 1, "actual": 0}


def test_caught_mismatch_still_rolls_back_and_fails_later_statements(db: Database) -> None:
    """The contract's rule: the transaction is failed even if the caller catches the error; every later statement
    raises ``UsageError("transaction.failed")``, and the block's end rolls back and raises the error again."""
    with pytest.raises(RowCountMismatch) as at_exit, db.transaction() as tx:
        tx.execute("UPDATE items SET label = 'changed' WHERE id = 1")
        with pytest.raises(RowCountMismatch):
            checked(tx).execute_checked("UPDATE items SET label = 'x'", expect_rowcount=1)
        for call in (
            lambda: tx.execute("UPDATE items SET label = 'y' WHERE id = 2"),
            lambda: tx.fetch_scalar("SELECT 1"),
            lambda: checked(tx).execute_checked("UPDATE items SET label = 'z' WHERE id = 2", expect_rowcount=1),
            lambda: checked(tx).query("SELECT 1"),
            lambda: tx.connection,
        ):
            with pytest.raises(UsageError) as later:
                call()
            assert later.value.code == "transaction.failed"
    assert at_exit.value.code == "transaction.rowcount_mismatch"
    assert dict(at_exit.value.details) == {"expected": 1, "actual": 5}
    assert at_exit.value.__context__ is None
    assert labels(db) == BEFORE


def test_max_affected_over_a_multi_row_statement(db: Database) -> None:
    with pytest.raises(RowCountMismatch) as caught, db.transaction() as tx:
        checked(tx).execute_checked("UPDATE items SET label = 'x' WHERE id <= 4", max_affected=3)
    assert caught.value.code == "transaction.too_many_rows"
    assert dict(caught.value.details) == {"limit": 3, "actual": 4}
    assert labels(db) == BEFORE


def test_max_affected_at_the_limit_and_below_passes(db: Database) -> None:
    with db.transaction() as tx:
        assert checked(tx).execute_checked("UPDATE items SET label = 'x' WHERE id <= 3", max_affected=3) == 3
        assert checked(tx).execute_checked("UPDATE items SET label = 'y' WHERE id = 99", max_affected=3) == 0
    assert labels(db)[:3] == [(1, "x"), (2, "x"), (3, "x")]


def test_both_limits_apply(db: Database) -> None:
    with pytest.raises(RowCountMismatch) as caught, db.transaction() as tx:
        checked(tx).execute_checked("UPDATE items SET label = 'x' WHERE id <= 2", expect_rowcount=2, max_affected=1)
    assert caught.value.code == "transaction.too_many_rows"
    with db.transaction() as tx:
        assert (
            checked(tx).execute_checked("UPDATE items SET n = n WHERE id <= 2", expect_rowcount=2, max_affected=2) == 2
        )


def test_a_limit_is_required(db: Database) -> None:
    with db.transaction() as tx:
        with pytest.raises(UsageError) as caught:
            checked(tx).execute_checked("UPDATE items SET label = 'x'")
        assert caught.value.code == "transaction.limit_required"
        tx.execute("UPDATE items SET label = 'kept' WHERE id = 1")  # nothing ran, the transaction is not failed
    assert labels(db)[0] == (1, "kept")


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"expect_rowcount": True}, TypeError),
        ({"expect_rowcount": 1.0}, TypeError),
        ({"expect_rowcount": "1"}, TypeError),
        ({"expect_rowcount": -1}, ValueError),
        ({"max_affected": -2}, ValueError),
        ({"max_affected": 1, "timeout": 0}, ValueError),
    ],
)
def test_invalid_limits_are_refused_before_anything_runs(db: Database, kwargs: dict[str, Any], error: type) -> None:
    with db.transaction() as tx:
        with pytest.raises(error):
            checked(tx).execute_checked("UPDATE items SET label = 'x'", **kwargs)
        tx.execute("UPDATE items SET label = 'kept' WHERE id = 1")
    assert labels(db)[0] == (1, "kept")


def test_params_must_be_one_mapping(db: Database) -> None:
    """``executemany`` is refused: its row count is a sum the limits cannot reason about."""
    with db.transaction() as tx, pytest.raises(TypeError):
        checked(tx).execute_checked("UPDATE items SET label = :l", [{"l": "a"}, {"l": "b"}], max_affected=10)  # type: ignore[arg-type]


def test_read_transaction_refuses_it(db: Database) -> None:
    with db.read() as tx:
        with pytest.raises(ReadOnlyViolation) as caught:
            checked(tx).execute_checked("UPDATE items SET label = 'x'", max_affected=10)
        assert caught.value.code == "database.read_only"
        assert tx.fetch_scalar("SELECT count(*) FROM items") == 5  # nothing sent, the transaction goes on
    assert labels(db) == BEFORE


def test_unknown_rowcount_is_a_mismatch(db: Database) -> None:
    """A statement whose driver reports no row count (``-1``, a SELECT on SQLite) never passes a limit."""
    with pytest.raises(RowCountMismatch) as caught, db.transaction() as tx:
        checked(tx).execute_checked("SELECT id FROM items", max_affected=100)
    assert dict(caught.value.details) == {"expected": None, "actual": -1}


def test_transaction_control_is_refused(db: Database) -> None:
    with pytest.raises(UsageError) as caught, db.transaction() as tx:
        checked(tx).execute_checked("COMMIT", max_affected=0)
    assert caught.value.code == "database.transaction_ended"


def test_timeout_stops_the_statement(db: Database) -> None:
    """SQLite: the deadline of the progress handler (contract section 5.3); the statement is rolled back."""
    statement = f"UPDATE items SET n = ({COUNT_FOREVER}) WHERE id = 1"
    with pytest.raises(StatementTimeout), db.transaction() as tx:
        checked(tx).execute_checked(statement, expect_rowcount=1, timeout=0.2)
    assert db.fetch_scalar("SELECT n FROM items WHERE id = 1") == 1


def test_transaction_timeout_applies_by_default(db: Database) -> None:
    with pytest.raises(StatementTimeout), db.transaction(timeout=0.2) as tx:
        checked(tx).execute_checked(f"UPDATE items SET n = ({COUNT_FOREVER}) WHERE id = 1", expect_rowcount=1)


def test_wrong_thread_is_refused(db: Database) -> None:
    errors: list[BaseException] = []
    with db.transaction() as tx:

        def other() -> None:
            try:
                checked(tx).execute_checked("UPDATE items SET label = 'x'", max_affected=10)
            except BaseException as exc:
                errors.append(exc)

        thread = threading.Thread(target=other)
        thread.start()
        thread.join()
    assert isinstance(errors[0], UsageError) and errors[0].code == "database.wrong_thread"
    assert labels(db) == BEFORE


def test_canary_values_never_reach_an_error_or_a_log(db: Database, caplog: pytest.LogCaptureFixture) -> None:
    """No parameter value in the mismatch, in the error at the block's end, in their frames or in a log record."""
    capture_all_logs(caplog)
    canary = new_canary("checked")
    raised: list[TabrionError] = []
    with pytest.raises(RowCountMismatch) as at_exit, db.transaction() as tx:
        try:
            checked(tx).execute_checked(
                "UPDATE items SET label = :label WHERE label <> :label", {"label": canary}, expect_rowcount=1
            )
        except RowCountMismatch as exc:
            raised.append(exc)
    assert_clean(raised[0], canary)
    assert_clean(at_exit.value, canary)
    with pytest.raises(StatementTimeout) as timeout, db.transaction() as tx:
        checked(tx).execute_checked(
            f"UPDATE items SET label = :label WHERE n = ({COUNT_FOREVER})",
            {"label": canary},
            max_affected=1,
            timeout=0.2,
        )
    assert_clean(timeout.value, canary)
    assert_logs_clean(phase_records(caplog), canary)


def test_trigger_changes_are_not_counted(db: Database) -> None:
    """Review I-1 of #78 (I-120 g): the count is the statement's own; rows a trigger changes are neither counted nor
    limited, and they commit with the statement."""
    db.execute("CREATE TABLE log (n INTEGER)")
    db.execute(
        "CREATE TRIGGER fan AFTER UPDATE ON items BEGIN"
        " INSERT INTO log SELECT n FROM items; DELETE FROM items WHERE id = 5; END"
    )
    with db.transaction() as tx:
        assert checked(tx).execute_checked("UPDATE items SET label = 'x' WHERE id = 1", max_affected=1) == 1
    assert db.fetch_scalar("SELECT count(*) FROM log") == 5
    assert db.fetch_scalar("SELECT count(*) FROM items") == 4


def test_max_affected_is_checked_after_the_work(db: Database) -> None:
    """Review I-2 of #78 (I-120 h): the statement runs to its end before the limit is checked, and the rollback undoes
    it; ``max_affected`` bounds what is committed, ``timeout`` bounds the work."""
    with pytest.raises(RowCountMismatch), db.transaction() as tx:
        checked(tx).execute_checked("UPDATE items SET n = n + 100", max_affected=1)
    assert db.fetch_scalar("SELECT max(n) FROM items") == 5
    with pytest.raises(StatementTimeout), db.transaction() as tx:
        checked(tx).execute_checked(f"UPDATE items SET n = ({COUNT_FOREVER})", max_affected=1, timeout=0.2)
