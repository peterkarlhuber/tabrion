"""The 0.2 methods of ``Database`` and ``Transaction`` (TAB-033), staged for the API freeze. Private.

G-T1 condition C-6: the 0.1.0 surface must not export a 0.2 name. These mixins hold the frozen 0.2 signatures and
their implementation, so ``scripts/api_surface.py`` can compare them with ``docs/api/surface.json``, but ``Database``
and ``Transaction`` inherit from them only when the package version reaches 0.2 (TODO TB-013). Until then only the
private classes of ``tabrion._sa._staged`` combine them, for Tabrion's own tests. Contract sections 5.3 to 5.6,
implementation notes I-100..I-106.

This module imports ``tabrion._sa.database`` as a module and reads its helpers when a method runs, and ``Transaction``
for the type checker only, so ``database`` can import the mixins at its top in 0.2 without an import cycle that fails
(G-T1 condition check CC-2, TODO TB-024).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
import contextlib
from contextlib import contextmanager
import math
import random
import re
import threading
import time
from typing import TYPE_CHECKING, Any, Literal, TypeVar, cast

import sqlalchemy
from sqlalchemy.schema import ExecutableDDLElement
from sqlalchemy.sql.dml import UpdateBase
from sqlalchemy.sql.elements import TextClause
from sqlalchemy.sql.selectable import Selectable

from tabrion._sa import database as _db
from tabrion._sa._types import Isolation, Statement
from tabrion._sa.result import Column, Result
from tabrion.errors import TabrionError, UsageError

if TYPE_CHECKING:
    from tabrion._sa.database import Database, Transaction

__all__: list[str] = []

T = TypeVar("T")

StatementKind = Literal["select", "dml", "ddl", "other"]

_FIRST_WORD = re.compile(r"[A-Za-z_]+")
_SELECT_WORDS = frozenset({"select", "with", "values", "table"})
_DML_WORDS = frozenset({"insert", "update", "delete", "merge", "upsert", "replace"})
_DDL_WORDS = frozenset({"create", "alter", "drop", "truncate", "comment", "grant", "revoke", "rename"})
_ENDING_WORDS = frozenset({"commit", "end", "rollback", "abort"})
"""First keywords of a statement that ends the transaction, ``AND CHAIN`` included (I-104, re-review N-1 of #32)."""

_MAX_BACKOFF = 2.0
"""``run_transaction``: the longest wait between two attempts, in seconds, unless ``backoff`` is longer (I-105)."""

_FIRST_NORMAL_OID = 16_384
"""PostgreSQL's ``FirstNormalObjectId``: OIDs below it are built-in types, whose names never change (I-101)."""
_TYPE_NAMES: dict[int, str] = {}
"""Built-in PostgreSQL type OID to ``format_type`` name, shared by every ``Database`` of the process."""
_TYPE_NAMES_SQL = "SELECT t.oid, pg_catalog.format_type(t.oid, NULL) FROM pg_catalog.pg_type t WHERE t.oid = ANY(%s)"


def _tx(obj: object) -> Transaction:
    return cast("Transaction", obj)


def _database(obj: object) -> Database:
    return cast("Database", obj)


def _max_rows(value: object, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("max_rows must be an int")
    if value < 1:
        raise ValueError("max_rows must be at least 1")
    return value


def _batch_size(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("batch_size must be an int")
    if value < 1:
        raise ValueError("batch_size must be at least 1")
    return value


def _skip_leading(text: str, position: int, *, postgres: bool) -> int:
    """The position after whitespace, ``(``, ``;`` and comments, as the server's lexer reads them.

    PostgreSQL ends a ``--`` comment at ``\\n`` or ``\\r`` and nests ``/* */`` comments; SQLite ends a ``--`` comment
    at ``\\n`` only and does not nest.
    """
    length = len(text)
    while position < length:
        char = text[position]
        if char.isspace() or char in "(;":
            position += 1
        elif text.startswith("--", position):
            position += 2
            while position < length and text[position] != "\n" and not (postgres and text[position] == "\r"):
                position += 1
        elif text.startswith("/*", position):
            depth, position = 1, position + 2
            while depth and position < length:
                if text.startswith("*/", position):
                    depth, position = depth - 1, position + 2
                elif postgres and text.startswith("/*", position):
                    depth, position = depth + 1, position + 2
                else:
                    position += 1
        else:
            break
    return position


def _keywords(text: str, count: int, *, postgres: bool = False) -> list[str]:
    """The first ``count`` keywords of a SQL text, lower case, after whitespace, ``(``, ``;`` and comments."""
    words: list[str] = []
    position = 0
    while len(words) < count:
        word = _FIRST_WORD.match(text, _skip_leading(text, position, postgres=postgres))
        if word is None:
            break
        words.append(word.group(0).lower())
        position = word.end()
    return words


def statement_kind(executable: sqlalchemy.sql.Executable) -> StatementKind:
    """``Result.statement_kind``: from the statement class, for text from its first keyword (I-100)."""
    if isinstance(executable, Selectable) and not isinstance(executable, TextClause):
        return "select"
    if isinstance(executable, UpdateBase):
        return "dml"
    if isinstance(executable, ExecutableDDLElement):
        return "ddl"
    if isinstance(executable, TextClause):
        words = _keywords(executable.text, 1)
        first = words[0] if words else ""
        if first in _SELECT_WORDS:
            return "select"
        if first in _DML_WORDS:
            return "dml"
        if first in _DDL_WORDS:
            return "ddl"
    return "other"


@contextmanager
def _extended_protocol(transaction: Transaction) -> Iterator[None]:
    """PostgreSQL: the statement goes through the extended query protocol as a prepared statement (I-104).

    psycopg sends a statement without parameters as a simple query, in which the server runs several commands
    (``COMMIT; BEGIN READ WRITE; INSERT ...``); a prepared statement holds exactly one command, and the server refuses
    more (42601). ``prepare_threshold = 0`` on the connection for the statement does that through SQLAlchemy.
    """
    dbapi = transaction._dbapi
    if transaction._dialect != "postgresql" or dbapi is None:
        yield
        return
    previous = dbapi.prepare_threshold
    dbapi.prepare_threshold = 0
    try:
        yield
    finally:
        dbapi.prepare_threshold = previous


def _refuse_transaction_control(transaction: Transaction, executable: sqlalchemy.sql.Executable) -> None:
    """Before a user statement: a statement that ends the transaction is never sent (I-104, re-review N-1 of #32).

    ``COMMIT``, ``END``, ``ROLLBACK`` and ``ABORT`` (with or without ``AND CHAIN``, ``ROLLBACK TO SAVEPOINT`` too) and
    ``PREPARE TRANSACTION``. The server allows one command per call, so the first keyword decides (after whitespace,
    ``(``, ``;`` and comments, read both as PostgreSQL does and as SQLite does; see :func:`_skip_leading`). ``... AND
    CHAIN`` opens a new transaction at once, which the status check after the statement cannot tell from the old one.
    The transaction is marked broken, as by :func:`_still_in_transaction`, which stays as the second line.
    """
    if not isinstance(executable, TextClause):
        return
    for postgres in (False, True):  # both lexers: a text must not pass by reading its comments the other way
        words = _keywords(executable.text, 2, postgres=postgres)
        if words and (words[0] in _ENDING_WORDS or words == ["prepare", "transaction"]):
            with transaction._state:
                transaction._broken = True
            raise UsageError("database.transaction_ended")


def _still_in_transaction(transaction: Transaction, close: Callable[[], None] | None = None) -> None:
    """After a user statement: the caller's SQL must not have ended Tabrion's transaction (I-104, review L-1 of #32).

    ``COMMIT`` or ``ROLLBACK`` as a statement of its own passes the one-command rule; what follows it would run outside
    the transaction (on PostgreSQL in a new one the caller can open ``READ WRITE``). The transaction is marked
    broken: this call raises ``UsageError("database.transaction_ended")``, every later statement too, and the end of
    the block rolls back and keeps the connection out of the pool.
    """
    dbapi = transaction._dbapi
    try:
        if transaction._dialect == "postgresql":
            from psycopg import pq

            inside = dbapi.info.transaction_status in (pq.TransactionStatus.INTRANS, pq.TransactionStatus.INERROR)
        else:
            inside = bool(dbapi.in_transaction)
    except Exception:
        inside = False  # cannot tell: treat it as ended
    if inside:
        return
    if close is not None:
        with contextlib.suppress(Exception):
            close()
    with transaction._state:
        transaction._broken = True
    raise UsageError("database.transaction_ended")


def _columns(transaction: Transaction, description: Sequence[Any] | None) -> tuple[Column, ...]:
    if not description:
        return ()
    names = [str(item[0]) for item in description]
    if transaction._dialect != "postgresql":
        # Python's sqlite3 does not expose the declared type of a result column (sqlite3_column_decltype).
        return tuple(Column(name=name, type_name=None, nullable=None) for name in names)
    oids = [item[1] if isinstance(item[1], int) else None for item in description]
    missing = sorted({oid for oid in oids if oid is not None and oid not in _TYPE_NAMES})
    found: dict[int, str] = {}
    if missing:
        found = _lookup_types(transaction, missing)
        for oid, name in found.items():
            if oid < _FIRST_NORMAL_OID:
                _TYPE_NAMES[oid] = name
    return tuple(
        Column(name=name, type_name=None if oid is None else _TYPE_NAMES.get(oid) or found.get(oid), nullable=None)
        for name, oid in zip(names, oids, strict=True)
    )


def _lookup_types(transaction: Transaction, oids: list[int]) -> dict[int, str]:
    """``format_type`` of each OID, on Tabrion's own ``tuple_row`` cursor inside the transaction (I-101)."""
    import psycopg
    from psycopg.rows import tuple_row

    with psycopg.Cursor(transaction._dbapi, row_factory=tuple_row) as cursor:
        cursor.execute(_TYPE_NAMES_SQL, (oids,))
        return {int(oid): str(name) for oid, name in cursor.fetchall()}


class _TransactionV02:
    """Mixed into ``Transaction`` in 0.2."""

    def query(
        self,
        statement: Statement,
        params: Mapping[str, Any] | None = None,
        *,
        max_rows: int | None = None,
        timeout: float | None = None,
    ) -> Result:
        """Since 0.2. At most ``max_rows`` rows (default ``Profile.max_rows``), materialized; ``truncated`` if more."""
        transaction = _tx(self)
        try:
            transaction._check()
            executable = _db._statement(statement)
        except BaseException:
            del statement, params  # no values or SQL text in the frames of the traceback (I-76)
            raise
        values = None
        rows: list[sqlalchemy.Row[Any]] = []
        try:
            values = _db._fetch_params(params)
            limit = _max_rows(max_rows, transaction._max_rows)
            kind = statement_kind(executable)
            _refuse_transaction_control(transaction, executable)
            connection = transaction._connection

            def run() -> tuple[Sequence[Any] | None, list[sqlalchemy.Row[Any]], bool, float]:
                started = time.perf_counter()
                with _extended_protocol(transaction):
                    result = connection.execute(executable, values)
                try:
                    if not result.returns_rows:
                        return None, [], False, started
                    description = result.cursor.description if result.cursor is not None else None
                    fetched = list(result.fetchmany(limit + 1))
                finally:
                    result.close()
                return description, fetched[:limit], len(fetched) > limit, started

            description, rows, truncated, started = transaction._step(statement, timeout, run)
            _still_in_transaction(transaction)
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            columns = transaction._step(statement, timeout, lambda: _columns(transaction, description))
        except BaseException:
            # ``values`` and ``executable`` are cells of ``run``: deleting them empties the cells; ``rows`` holds the
            # fetched values (I-76).
            del statement, params, values, executable, rows
            raise
        return Result(
            columns=columns,
            rows=tuple(tuple(row) for row in rows),
            row_count=len(rows),
            truncated=truncated,
            elapsed_ms=elapsed_ms,
            statement_kind=kind,
        )

    def stream(
        self,
        statement: Statement,
        params: Mapping[str, Any] | None = None,
        *,
        batch_size: int = 500,
        timeout: float | None = None,
    ) -> Iterator[Sequence[sqlalchemy.Row[Any]]]:
        """Since 0.2. PostgreSQL: server-side cursor; SQLite: ``fetchmany``."""
        transaction = _tx(self)
        try:
            transaction._check()
            executable = _db._statement(statement)
        except BaseException:
            del statement, params  # no values or SQL text in the frames of the traceback (I-76)
            raise
        values = None
        try:
            values = _db._fetch_params(params)
            size = _batch_size(batch_size)
            _db._optional_seconds(timeout)  # checked now, not at the first batch
            _refuse_transaction_control(transaction, executable)
        except BaseException:
            del statement, params, values, executable  # I-76
            raise
        return _stream(transaction, statement, executable, values, size, timeout)

    def cancel(self) -> None:
        """Since 0.2. Callable from another thread."""
        _tx(self)._request_cancel()

    @contextmanager
    def savepoint(self) -> Iterator[None]:
        """Since 0.2, experimental."""
        transaction = _tx(self)
        nested = transaction._step(None, None, transaction._connection.begin_nested)
        try:
            yield
        except BaseException:
            if nested.is_active:
                with contextlib.suppress(TabrionError):  # the caller's error wins; the transaction rolls back anyway
                    transaction._step(None, None, nested.rollback)
            raise
        transaction._step(None, None, nested.commit)


def _stream(
    transaction: Transaction,
    statement: Statement,
    executable: sqlalchemy.sql.Executable,
    values: dict[str, Any] | None,
    size: int,
    timeout: float | None,
) -> Iterator[Sequence[sqlalchemy.Row[Any]]]:
    """The batches of ``stream`` (I-103). Every round trip runs through ``Transaction._step``."""
    connection = transaction._connection
    options: dict[str, Any] = {}
    if transaction._dialect == "postgresql":
        # A named (server-side) cursor: DECLARE ... CURSOR, then FETCH FORWARD per batch. psycopg sends the DECLARE
        # through the extended protocol, so the server refuses a text with several commands there as well.
        options = {"stream_results": True, "max_row_buffer": size}
    rows: list[sqlalchemy.Row[Any]] = []
    try:
        result = transaction._step(
            statement, timeout, lambda: connection.execute(executable, values, execution_options=options)
        )
        _still_in_transaction(transaction, result.close)
    except BaseException:
        # ``executable`` and ``values`` are cells of the lambda: deleting them empties the cells (I-76).
        del statement, executable, values
        raise
    closed = threading.Event()

    def close() -> None:
        if not closed.is_set():
            closed.set()
            result.close()

    transaction._cleanups.append(close)
    try:
        if not result.returns_rows:
            raise UsageError("database.no_rows_returned")
        while True:
            rows = transaction._step(statement, timeout, lambda: list(result.fetchmany(size)))
            if rows:
                yield rows
            if len(rows) < size:
                return
    except BaseException:
        # No SQL text, parameters or rows of an earlier batch in the frame of the traceback (I-76).
        del statement, executable, values, rows
        raise
    finally:
        # Only in the owner thread of a running transaction: after its end the transaction's clean-up closed the
        # result, and the connection may belong to someone else (a generator collected later must not touch it).
        if transaction._active and threading.get_ident() == transaction._thread and not closed.is_set():
            with contextlib.suppress(ValueError):
                transaction._cleanups.remove(close)
            # Never raises from here (a generator may be closed by the garbage collector); a failed CLOSE leaves the
            # PostgreSQL transaction aborted, and its next statement or COMMIT reports that.
            with contextlib.suppress(Exception):
                close()


class _DatabaseV02:
    """Mixed into ``Database`` in 0.2."""

    def query(
        self,
        statement: Statement,
        params: Mapping[str, Any] | None = None,
        *,
        max_rows: int | None = None,
        timeout: float | None = None,
    ) -> Result:
        """Since 0.2. In its own ``read()``."""
        database = _database(self)
        try:
            with database.read() as transaction:
                staged = cast("_TransactionV02", transaction)  # in 0.2 every Transaction is one
                return staged.query(statement, params, max_rows=max_rows, timeout=timeout)
        except BaseException:
            del statement, params  # I-76
            raise

    @contextmanager
    def inspector(self) -> Iterator[sqlalchemy.Inspector]:
        """Since 0.2. On a read-only connection."""
        database = _database(self)
        with database.read() as transaction:
            yield sqlalchemy.inspect(transaction.connection)

    def on_begin(self, callback: Callable[[sqlalchemy.Connection, bool], None]) -> Callable[[], None]:
        """Since 0.2, experimental. ``bool`` is ``read_only``; returns the function that unregisters it."""
        database = _database(self)
        if not callable(callback):
            raise TypeError("callback must be callable")
        database._check_open()
        token = object()
        with database._callbacks_lock:
            database._begin_callbacks = [*database._begin_callbacks, (token, callback)]

        def unregister() -> None:
            with database._callbacks_lock:
                database._begin_callbacks = [entry for entry in database._begin_callbacks if entry[0] is not token]

        return unregister

    def run_transaction(
        self,
        fn: Callable[[Transaction], T],
        *,
        attempts: int = 3,
        isolation: Isolation | None = None,
        backoff: float = 0.05,
    ) -> T:
        """Since 0.2, experimental. Retries only ``retryable and not outcome_unknown``."""
        database = _database(self)
        if not callable(fn):
            raise TypeError("fn must be callable")
        if isinstance(attempts, bool) or not isinstance(attempts, int):
            raise TypeError("attempts must be an int")
        if attempts < 1:
            raise ValueError("attempts must be at least 1")
        if isinstance(backoff, bool) or not isinstance(backoff, int | float):
            raise TypeError("backoff must be a number of seconds")
        if not math.isfinite(backoff) or backoff < 0:
            raise ValueError("backoff must be a finite number of seconds, at least 0")
        attempt = 1
        while True:
            try:
                with database.transaction(isolation=isolation) as transaction:
                    return fn(transaction)
            except TabrionError as exc:
                classification = exc.classification
                if (
                    attempt >= attempts
                    or classification is None
                    or not classification.retryable
                    or classification.outcome_unknown
                ):
                    raise
            # Exponential backoff with full jitter: 0..backoff, 0..2*backoff, ..., each at most _MAX_BACKOFF (or
            # backoff itself when that is larger); random has no security purpose here.
            delay = min(backoff * 2 ** (attempt - 1), max(backoff, _MAX_BACKOFF))
            time.sleep(delay * random.random())  # noqa: S311
            attempt += 1
