"""Rule T-NOTX for ``tabrion.sqlite`` (the SQLite counterpart of TAB-T-G01; ADR 0011).

Every function that takes a connection is called in two states, idle and inside a transaction the caller opened.
Afterwards the state is the same, and the caller's ``isolation_level``, ``row_factory``, ``text_factory`` and
``query_only`` are unchanged. Functions that need a transaction of their own refuse the caller's with ``UsageError``.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import sqlite3
from typing import Any

import pytest

from tabrion import sqlite as ts
from tabrion.errors import UsageError
from tests.sqlite.conftest import make_database

READERS: dict[str, Callable[[sqlite3.Connection], object]] = {
    "integrity_check": ts.integrity_check,
    "quick_check": ts.quick_check,
    "foreign_key_check": ts.foreign_key_check,
    "foreign_key_check(table)": lambda c: ts.foreign_key_check(c, table="child"),
    "diagnose": ts.diagnose,
    "diagnose(quick_check)": lambda c: ts.diagnose(c, run_quick_check=True),
}


def _loading(connection: sqlite3.Connection) -> None:
    if hasattr(connection, "enable_load_extension"):
        with ts.loading_extensions(connection):
            pass


OWN_TRANSACTION: dict[str, Callable[[sqlite3.Connection], object]] = {
    "write_transaction": lambda c: ts.write_transaction(c).__enter__(),
    "read_transaction": lambda c: ts.read_transaction(c).__enter__(),
    "migrate": lambda c: ts.migrate(c, {}, target=0, backup=None),
    "apply_profile": ts.apply_profile,
    "backup": lambda c: ts.backup(c, Path(ts._main_file(c)).with_name("copy.db")),
}


def _dict_rows(cursor: sqlite3.Cursor, row: tuple[Any, ...]) -> dict[str, Any]:
    return {column[0]: value for column, value in zip(cursor.description, row, strict=True)}


@pytest.fixture(params=[None, sqlite3.Row, _dict_rows], ids=["tuple", "Row", "dict"])
def connection(request: pytest.FixtureRequest, tmp_path: Path) -> Any:
    path = tmp_path / "a.db"
    make_database(path)
    connection = ts.connect(path)
    connection.row_factory = request.param
    yield connection
    connection.close()


def _count(connection: sqlite3.Connection) -> int:
    cursor = connection.cursor()
    cursor.row_factory = None
    return int(cursor.execute("SELECT count(*) FROM parent WHERE id = 500").fetchone()[0])


def _state(connection: sqlite3.Connection) -> tuple[object, ...]:
    cursor = connection.cursor()
    cursor.row_factory = None
    query_only = cursor.execute("PRAGMA query_only").fetchone()
    return (
        connection.in_transaction,
        connection.isolation_level,
        connection.row_factory,
        connection.text_factory,
        query_only,
    )


@pytest.mark.parametrize("name", READERS)
@pytest.mark.parametrize("caller_transaction", [False, True], ids=["idle", "intrans"])
def test_readers_keep_the_state(connection: sqlite3.Connection, name: str, caller_transaction: bool) -> None:
    if caller_transaction:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("INSERT INTO parent (id) VALUES (500)")
    before = _state(connection)
    changes = connection.total_changes
    READERS[name](connection)
    assert _state(connection) == before
    assert connection.total_changes == changes
    if caller_transaction:
        connection.execute("ROLLBACK")  # the caller's write is still there to roll back
        assert _count(connection) == 0


@pytest.mark.parametrize("name", OWN_TRANSACTION)
def test_own_transaction_refuses_the_callers(connection: sqlite3.Connection, name: str) -> None:
    connection.execute("BEGIN IMMEDIATE")
    connection.execute("INSERT INTO parent (id) VALUES (500)")
    before = _state(connection)
    with pytest.raises(UsageError):
        OWN_TRANSACTION[name](connection)
    assert _state(connection) == before
    connection.execute("COMMIT")
    assert _count(connection) == 1


def test_loading_extensions_keeps_the_callers_transaction(connection: sqlite3.Connection) -> None:
    connection.execute("BEGIN")
    before = _state(connection)
    _loading(connection)
    assert _state(connection) == before
    connection.execute("ROLLBACK")


def test_text_factory_bytes(tmp_path: Path) -> None:
    """A caller's ``text_factory=bytes`` does not change what the checks and ``diagnose`` report."""
    path = tmp_path / "a.db"
    make_database(path)
    connection = ts.connect(path)
    try:
        connection.text_factory = bytes
        assert ts.quick_check(connection).ok
        diagnosis = ts.diagnose(connection, run_quick_check=True)
        assert diagnosis.journal_mode == "wal" and diagnosis.quick_check == "ok"
        assert diagnosis.path is not None and diagnosis.path.endswith("a.db")
        assert connection.text_factory is bytes
    finally:
        connection.close()
