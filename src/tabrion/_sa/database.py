"""``Database`` and ``Transaction``: layer B over a SQLAlchemy engine. Private: use ``tabrion.Database``.

Contract: ``docs/api/contract.md`` sections 5.2, 5.3 and 5.5, implementation notes I-37..I-44 and I-56 (TAB-018).
The 0.2 methods (TAB-033) are staged in ``tabrion._sa._v02`` and are not part of these classes before 0.2 (G-T1
condition C-6, ``docs/api/surface.json``).

``Statement``, ``Params`` and ``Isolation`` are documentation-only aliases (``tabrion._sa._types``): they name the types
in the signatures and are not exported from ``tabrion`` (G-T1 F-11, TODO TB-020).

The machinery the 0.2 methods need lives here, private, so their public part in ``tabrion._sa._v02`` stays small:
the cancel bookkeeping of every statement (``Transaction._step``, ``_request_cancel``), clean-up callbacks run when a
transaction ends (open streams), the ``on_begin`` callbacks and the transaction class a ``Database`` creates (contract
implementation notes I-100..I-106, TAB-033).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
import contextlib
from contextlib import contextmanager
from dataclasses import dataclass, replace
import functools
import math
import os
import re
import sqlite3
import threading
import time
from typing import TYPE_CHECKING, Any, ClassVar, Literal, NoReturn, TypeVar
import uuid
import warnings

import sqlalchemy
from sqlalchemy.pool import QueuePool, StaticPool

from tabrion import config as _config
from tabrion import sqlite as _sqlite
from tabrion._sa import events as _events
from tabrion._sa._types import Isolation, Params, Statement
from tabrion._sa.profile import Profile, TLSMode
from tabrion.errors import (
    Classification,
    ConfigurationError,
    Kind,
    MultipleRowsError,
    StatementTimeout,
    TabrionError,
    UsageError,
    _error_class,
    classify,
)
from tabrion.postgres import _host_is_local
from tabrion.postgres._common import require_psycopg

if TYPE_CHECKING:
    from tabrion.check import Finding

__all__ = ["Database", "Health", "Transaction"]

_T = TypeVar("_T")

_ISOLATION_LEVELS: dict[str, str] = {
    "read_committed": "READ COMMITTED",
    "repeatable_read": "REPEATABLE READ",
    "serializable": "SERIALIZABLE",
}
_RESERVED_OPTIONS = frozenset({"creator", "isolation_level", "pool", "poolclass"})
"""Engine options Tabrion sets itself (contract I-37): the creator applies layer A, the pool follows the profile."""
_WEAK_SSLMODES = frozenset({"disable", "allow", "prefer"})
_MAX_TIMEOUT_MS = 2_147_483_647
_PG_TIMEOUT_SETTING = re.compile(r"[0-9]{1,10}(?:us|ms|s|min|h|d)?")
_PROGRESS_STEPS = 1_000
"""``set_progress_handler`` calls the deadline check every 1 000 SQLite VM steps (contract section 5.3)."""

_CANCEL_ATTEMPTS = 4
"""``cancel()`` sends at most this many requests while the same statement keeps running (I-102)."""
_CANCEL_RETRY_SECONDS = 0.2
_CANCEL_TIMEOUT_SECONDS = 2.0
"""psycopg ``cancel_safe(timeout=...)``: how long one request to the server may take."""

_PARAMETER_CAST = re.compile(r"(?<![:\w\\]):\w+::")
"""``:name::type`` in the statement as sent (contract I-107, plan TAB-T-025). SQLAlchemy's ``text()`` binds ``:name``
only when neither ``:`` nor a word character follows it, so before ``::`` the name stays in the text, unbound:
PostgreSQL reports a syntax error, SQLite a parameter without a value. The look-behind is SQLAlchemy's own."""
_PARAMETER_CAST_KINDS = frozenset({Kind.SYNTAX, Kind.UNKNOWN})
_PARAMETER_CAST_HINT = "write CAST(:name AS type) instead of :name::type"
"""The fixed text of ``details["hint"]``: it names the pattern, never the parameter, its value or the statement."""

_NEW_MESSAGE = "tabrion.{name} is created by its factory, not called directly: {hint}"


def _no_constructor(name: str, hint: str) -> NoReturn:
    raise TypeError(_NEW_MESSAGE.format(name=name, hint=hint))


@dataclass(frozen=True, slots=True)
class Health:
    ok: bool
    dialect: str
    latency_ms: float | None
    server_version: str | None
    pool: Mapping[str, int] | None
    """``checked_out``, ``size``, ``overflow`` (SQLAlchemy pool status)."""
    classification: Classification | None


# ---------------------------------------------------------------------------------------------------------------------
# Argument checks and error translation
# ---------------------------------------------------------------------------------------------------------------------


def _seconds(timeout: object, name: str = "timeout") -> float:
    if isinstance(timeout, bool) or not isinstance(timeout, int | float):
        raise TypeError(f"{name} must be a number of seconds")
    seconds = float(timeout)
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError(f"{name} must be a positive, finite number of seconds")
    return seconds


def _optional_seconds(timeout: object) -> float | None:
    return None if timeout is None else _seconds(timeout)


def _statement(statement: object) -> sqlalchemy.sql.Executable:
    if isinstance(statement, str):
        return sqlalchemy.text(statement)
    if isinstance(statement, sqlalchemy.sql.Executable):
        return statement
    raise TypeError(f"statement must be a str or a SQLAlchemy executable, not {type(statement).__name__}")


def _mapping(params: object) -> dict[str, Any]:
    if not isinstance(params, Mapping):
        raise TypeError(f"parameters must be a mapping, not {type(params).__name__}")
    return dict(params)


def _execute_params(params: object) -> dict[str, Any] | list[dict[str, Any]] | None:
    if params is None:
        return None
    if isinstance(params, Mapping):
        return dict(params)
    if isinstance(params, Sequence) and not isinstance(params, str | bytes | bytearray):
        return [_mapping(item) for item in params]
    raise TypeError(f"parameters must be a mapping or a sequence of mappings, not {type(params).__name__}")


def _fetch_params(params: object) -> dict[str, Any] | None:
    return None if params is None else _mapping(params)


def _statement_text(exc: BaseException, statement: object) -> str | None:
    text = getattr(exc, "statement", None)
    if isinstance(text, str):
        return text
    return statement if isinstance(statement, str) else None


def _translate(
    exc: BaseException, statement: object = None, *, during_commit: bool = False, sent: bool = True
) -> TabrionError:
    """The ``TabrionError`` for a SQLAlchemy or driver error (contract section 5.3, I-39); raise it ``from None``."""
    if isinstance(exc, sqlalchemy.exc.TimeoutError):
        # The pool had no connection within pool_timeout: nothing was sent, so it is retryable (I-39).
        classification = replace(classify(exc, sent=False), kind=Kind.RESOURCE, retryable=True, connection_usable=False)
        return _error_class(Kind.RESOURCE)("database.resource", classification=classification, original=exc)
    if isinstance(exc, sqlalchemy.exc.StatementError) and not isinstance(exc, sqlalchemy.exc.DBAPIError):
        # SQLAlchemy refused the statement before the driver saw it (a missing bind parameter, a bad type).
        return UsageError("database.invalid_statement", original=exc, statement=_statement_text(exc, statement))
    classification = classify(exc, during_commit=during_commit, sent=sent)
    original = getattr(exc, "orig", None)
    if not isinstance(original, BaseException):
        original = exc
    error_class = _error_class(classification.kind)
    text = _statement_text(exc, statement)
    return error_class(
        f"database.{classification.kind.value}",
        classification=classification,
        original=original,
        statement=text,
        details=_parameter_cast_hint(classification.kind, text),
    )


def _parameter_cast_hint(kind: Kind, text: str | None) -> dict[str, str | int | None] | None:
    """``{"hint": ...}`` when a syntax or unknown error's statement holds ``:name::type`` (I-107), else ``None``.

    The match is textual, as SQLAlchemy's own bind rule is: string literals and comments count too, so a statement
    that failed for an unrelated reason can carry the hint when such text appears in a literal or a comment
    (review L-1 of #49). The hint is fixed text, so this costs no value, only a possibly misleading hint."""
    if kind in _PARAMETER_CAST_KINDS and text is not None and _PARAMETER_CAST.search(text) is not None:
        return {"hint": _PARAMETER_CAST_HINT}
    return None


def _is_database_error(exc: BaseException) -> bool:
    """An error ``_translate`` turns into a ``TabrionError`` (others, the caller's own, pass unchanged)."""
    return isinstance(exc, sqlalchemy.exc.StatementError | sqlalchemy.exc.TimeoutError | sqlite3.Error) or (
        type(exc).__module__.partition(".")[0] == "psycopg"
    )


# ---------------------------------------------------------------------------------------------------------------------
# Transaction
# ---------------------------------------------------------------------------------------------------------------------


class Transaction:
    """One Tabrion transaction; bound to the opening thread (only ``cancel`` may come from another thread)."""

    _connection: sqlalchemy.Connection
    _read_only: bool
    _dialect: str
    _thread: int
    _active: bool
    _default_timeout: float | None
    _pg_timeout: float | None
    _pg_saved: str | None
    _max_rows: int
    _dbapi: Any
    _state: threading.Condition
    _running: bool
    _canceled: bool
    _sending: bool
    _cancel_sent: bool
    _broken: bool
    _cleanups: list[Callable[[], None]]

    def __new__(cls, *args: object, **kwargs: object) -> Transaction:
        _no_constructor("Transaction", "Database.transaction() or Database.read()")

    @classmethod
    def _open(
        cls,
        connection: sqlalchemy.Connection,
        *,
        read_only: bool,
        dialect: str,
        timeout: float | None,
        max_rows: int = 10_000,
    ) -> Transaction:
        self = object.__new__(cls)
        self._connection = connection
        self._read_only = read_only
        self._dialect = dialect
        self._thread = threading.get_ident()
        self._active = True
        self._default_timeout = timeout
        self._pg_timeout = None
        self._pg_saved = None
        self._max_rows = max_rows
        # The DBAPI connection of this transaction, for cancel() from another thread (I-102).
        self._dbapi = connection.connection.dbapi_connection
        self._state = threading.Condition(threading.Lock())
        self._running = False
        self._canceled = False
        self._sending = False
        self._cancel_sent = False
        self._broken = False
        self._cleanups = []
        return self

    def __repr__(self) -> str:
        state = "active" if self._active else "ended"
        return f"<tabrion.Transaction {'read' if self._read_only else 'write'} {self._dialect} {state}>"

    def _check(self) -> None:
        if not self._active:
            raise UsageError("database.transaction_ended")
        if threading.get_ident() != self._thread:
            raise UsageError("database.wrong_thread")

    @property
    def read_only(self) -> bool:
        return self._read_only

    @property
    def connection(self) -> sqlalchemy.Connection:
        """Controlled access to the underlying SQLAlchemy connection."""
        self._check()
        return self._connection

    # The statement runner: timeout, cancel bookkeeping, execution and error translation in one place.

    def _run(
        self,
        statement: Statement,
        params: dict[str, Any] | list[dict[str, Any]] | None,
        timeout: float | None,
        consume: Callable[[sqlalchemy.CursorResult[Any]], _T],
    ) -> _T:
        try:
            self._check()
            executable = _statement(statement)
        except BaseException:
            del statement, params  # no values or SQL text in the frames of the traceback (I-76)
            raise
        connection = self._connection
        try:
            return self._step(statement, timeout, lambda: consume(connection.execute(executable, params)))
        except BaseException:
            # ``params`` and ``executable`` are cells of the lambda: deleting them empties the cells, so neither this
            # frame nor the lambda's frame shows them (I-76).
            del statement, params, executable
            raise

    def _step(self, statement: object, timeout: float | None, call: Callable[[], _T]) -> _T:
        """One round trip of a statement (its execution, or one fetch of a stream) under the transaction's rules.

        Checks the thread and the state, refuses to start after ``cancel()`` (``Canceled``), marks the statement as
        running for ``cancel()``, applies the timeout (PostgreSQL ``SET LOCAL statement_timeout``, SQLite the deadline
        of the progress handler, which also stops the statement once ``cancel()`` was called) and translates errors.
        """
        error: TabrionError
        try:
            self._check()
            seconds = _optional_seconds(timeout)
            if seconds is None:
                seconds = self._default_timeout
            connection = self._connection
            self._statement_started()
            try:
                if self._dialect == "postgresql":
                    self._pg_statement_timeout(seconds)
                    return call()
                return _with_deadline(connection, seconds, call, stop=self._cancel_requested)
            except _SQLiteDeadline as expired:
                error = _timeout_error(expired.cause, statement)
            except Exception as exc:
                if not _is_database_error(exc):
                    raise
                error = _translate(exc, statement)
            finally:
                self._statement_ended()
        except BaseException:
            # No SQL text in the frames of the traceback; ``call`` closes over the parameters (I-76).
            del statement, call
            raise
        # Raised outside the except block: no __context__ that holds the SQLAlchemy error and its parameters (I-39).
        # The frames it passes keep their locals for tools that capture them (Sentry, rich, ``pytest -l``): the
        # parameters and the SQL text (which can quote a value) are dropped first, here and in each frame on the way
        # out (I-76).
        del statement, call
        raise error from None

    def _pg_statement_timeout(self, seconds: float | None) -> None:
        """``SET LOCAL statement_timeout`` for a statement with a timeout; the previous value back for one without.

        ``SET`` takes no snapshot, so the limit covers the statement from its start (as in ``tabrion.postgres.ping``,
        contract I-14). The value in force before the first ``SET LOCAL`` (``SHOW``) is what comes back (I-40).
        """
        if seconds == self._pg_timeout:
            return
        connection = self._connection
        if seconds is not None:
            if self._pg_saved is None:
                shown = connection.exec_driver_sql("SHOW statement_timeout").scalar()
                self._pg_saved = shown if isinstance(shown, str) else "DEFAULT"
            milliseconds = min(_MAX_TIMEOUT_MS, max(1, math.ceil(seconds * 1000)))
            connection.exec_driver_sql(f"SET LOCAL statement_timeout = {milliseconds}")
        else:
            saved = self._pg_saved or "DEFAULT"
            value = f"'{saved}'" if _PG_TIMEOUT_SETTING.fullmatch(saved) else "DEFAULT"
            connection.exec_driver_sql(f"SET LOCAL statement_timeout = {value}")
        self._pg_timeout = seconds

    # Cancel bookkeeping (I-102). ``_state`` guards ``_running``, ``_canceled``, ``_sending`` and ``_active``. A request
    # is sent only while a statement of this transaction runs, never after the transaction's block ended; the block's
    # end waits for a request still being sent, and on PostgreSQL a transaction that sent one never returns its
    # connection to the pool (a request the server acts on late would hit the next user of that server session).

    def _cancel_requested(self) -> bool:
        return self._canceled

    def _statement_started(self) -> None:
        with self._state:
            if self._broken:
                raise UsageError("database.transaction_ended")
            if self._canceled:
                raise _canceled_error()
            self._running = True

    def _statement_ended(self) -> None:
        with self._state:
            self._running = False
            self._state.notify_all()

    def _request_cancel(self) -> None:
        """Mark the transaction canceled and stop the running statement; callable from any thread, never raises."""
        with self._state:
            if not self._active:
                return  # ended (or ending): the connection may already belong to someone else
            self._canceled = True
        for _attempt in range(_CANCEL_ATTEMPTS):
            with self._state:
                if not (self._active and self._running) or self._sending:
                    return
                self._sending = True
                if self._dialect == "postgresql":
                    self._cancel_sent = True  # set before sending: the end of the block must see it
            try:
                # Sent without the lock, so the owner's statement can end meanwhile (I-102, review L-2 of #32);
                # _finish waits for _sending, so the connection is not closed under the request.
                _send_cancel(self._dialect, self._dbapi)
            finally:
                with self._state:
                    self._sending = False
                    self._state.notify_all()
                    # A request that arrives before the server started the statement is ignored by the server; send
                    # it again while the statement still runs (wait releases the lock, so the statement can end).
                    if self._active and self._running:
                        self._state.wait(_CANCEL_RETRY_SECONDS)

    def _finish(self) -> None:
        """End of the transaction block: no cancel from now on; open streams are closed (I-103)."""
        with self._state:
            while self._sending:
                self._state.wait()
            self._active = False
            cleanups, self._cleanups = self._cleanups, []
        for cleanup in reversed(cleanups):
            with contextlib.suppress(Exception):  # the transaction ends either way; its rollback reports the state
                cleanup()

    def _must_not_return_to_pool(self) -> bool:
        """A PostgreSQL cancel request was sent (it may still reach the server session), or the caller's SQL ended the
        transaction (I-102, I-104; review H-1 and L-1 of #32)."""
        return self._cancel_sent or self._broken

    def execute(self, statement: Statement, params: Params = None, *, timeout: float | None = None) -> int:
        """Row count (``-1`` becomes ``0`` for DDL)."""

        def consume(result: sqlalchemy.CursorResult[Any]) -> int:
            count = result.rowcount
            result.close()
            return max(count, 0)

        values = None
        try:
            values = _execute_params(params)
            if isinstance(values, list) and not values:
                self._check()
                _statement(statement)
                return 0  # executemany of nothing: SQLAlchemy would run the statement once without parameters
            return self._run(statement, values, timeout, consume)
        except BaseException:
            del statement, params, values  # I-76
            raise

    def fetch_one(
        self, statement: Statement, params: Mapping[str, Any] | None = None, *, timeout: float | None = None
    ) -> sqlalchemy.Row[Any] | None:
        """0 rows: ``None``; more than one: ``MultipleRowsError``."""
        try:
            return self._run(statement, _fetch_params(params), timeout, _one_row)
        except BaseException:
            del statement, params  # I-76
            raise

    def fetch_all(
        self, statement: Statement, params: Mapping[str, Any] | None = None, *, timeout: float | None = None
    ) -> list[sqlalchemy.Row[Any]]:
        try:
            return self._run(statement, _fetch_params(params), timeout, _all_rows)
        except BaseException:
            del statement, params  # I-76
            raise

    def fetch_scalar(
        self, statement: Statement, params: Mapping[str, Any] | None = None, *, timeout: float | None = None
    ) -> Any:
        """First column of the only row; 0 rows: ``None``; more than one: ``MultipleRowsError``."""
        try:
            row = self._run(statement, _fetch_params(params), timeout, _one_row)
        except BaseException:
            del statement, params  # I-76
            raise
        return None if row is None else row[0]


class _SQLiteDeadline(Exception):
    """Internal: the progress handler stopped a statement at its deadline (becomes ``StatementTimeout``)."""

    def __init__(self, cause: BaseException) -> None:
        super().__init__("deadline")
        self.cause = cause


def _with_deadline(
    connection: sqlalchemy.Connection,
    seconds: float | None,
    call: Callable[[], _T],
    *,
    stop: Callable[[], bool] | None = None,
) -> _T:
    """SQLite: run ``call`` under a deadline checked every 1 000 VM steps (``set_progress_handler``, I-40).

    ``stop`` (a transaction's cancel flag) is checked by the same handler, so a ``cancel()`` that came before SQLite
    started the statement still stops it (``sqlite3.Connection.interrupt`` alone would be lost then, I-102).
    """
    if seconds is None and stop is None:
        return call()
    dbapi = connection.connection.dbapi_connection
    if not isinstance(dbapi, sqlite3.Connection):
        return call()
    deadline = None if seconds is None else time.monotonic() + seconds
    expired = False

    def progress() -> int:
        nonlocal expired
        if stop is not None and stop():
            return 1  # SQLITE_INTERRUPT, classified as CANCELED
        if deadline is not None and time.monotonic() >= deadline:
            expired = True
            return 1
        return 0

    dbapi.set_progress_handler(progress, _PROGRESS_STEPS)
    try:
        return call()
    except Exception as exc:
        if expired and _is_database_error(exc):
            raise _SQLiteDeadline(exc) from None
        raise
    finally:
        dbapi.set_progress_handler(None, _PROGRESS_STEPS)


def _canceled_error() -> TabrionError:
    """``Canceled`` for a statement refused after ``cancel()`` (I-102); nothing was sent."""
    classification = replace(classify(Exception()), kind=Kind.CANCELED, connection_usable=True)
    return _error_class(Kind.CANCELED)("database.canceled", classification=classification)


def _send_cancel(dialect: str, dbapi: Any) -> None:
    """``sqlite3.Connection.interrupt()`` or psycopg ``cancel_safe()``; best effort, never raises (I-102)."""
    with contextlib.suppress(Exception):
        if dialect == "postgresql":
            dbapi.cancel_safe(timeout=_CANCEL_TIMEOUT_SECONDS)
        else:
            dbapi.interrupt()


def _timeout_error(cause: BaseException, statement: object = None) -> StatementTimeout:
    """The interrupted statement as ``StatementTimeout`` (SQLite reports ``SQLITE_INTERRUPT``, I-40)."""
    original = getattr(cause, "orig", None)
    return StatementTimeout(
        "database.timeout",
        classification=replace(classify(cause), kind=Kind.TIMEOUT),
        original=original if isinstance(original, BaseException) else cause,
        statement=_statement_text(cause, statement),
    )


def _rows(result: sqlalchemy.CursorResult[Any], limit: int | None) -> list[sqlalchemy.Row[Any]]:
    if not result.returns_rows:
        result.close()
        raise UsageError("database.no_rows_returned")
    try:
        return list(result.fetchmany(limit) if limit is not None else result.fetchall())
    finally:
        result.close()


def _one_row(result: sqlalchemy.CursorResult[Any]) -> sqlalchemy.Row[Any] | None:
    rows = _rows(result, 2)
    if len(rows) > 1:
        del rows  # the fetched values stay out of the frame locals of the traceback (I-76)
        raise MultipleRowsError("database.multiple_rows")
    return rows[0] if rows else None


def _all_rows(result: sqlalchemy.CursorResult[Any]) -> list[sqlalchemy.Row[Any]]:
    return _rows(result, None)


# ---------------------------------------------------------------------------------------------------------------------
# URLs and engines
# ---------------------------------------------------------------------------------------------------------------------


def _parse_url(url: object) -> sqlalchemy.URL:
    if isinstance(url, sqlalchemy.URL):
        return url
    if not isinstance(url, str):
        raise TypeError(f"url must be a str or a sqlalchemy.URL, not {type(url).__name__}")
    try:
        return sqlalchemy.make_url(url)
    except Exception:
        # The message of a parse error can quote the URL, password included: none of it is kept (I-37).
        parsed = None
    if parsed is None:
        raise ConfigurationError("config.url")
    return parsed


def _engine_options(engine_options: object) -> dict[str, Any]:
    if engine_options is None:
        return {}
    if not isinstance(engine_options, Mapping):
        raise TypeError("engine_options must be a mapping")
    options = dict(engine_options)
    for name in sorted(options):
        if name in _RESERVED_OPTIONS:
            raise ConfigurationError("config.engine_option", details={"option": name})
    return options


def _query_values(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part for part in value.split(",") if part != ""] or [""]
    if isinstance(value, tuple | list):
        return [part for item in value for part in _query_values(item)]
    return [str(value)]


def _single(value: object) -> str | None:
    values = _query_values(value)
    if not values:
        return None
    if len(set(values)) > 1:
        raise ConfigurationError("config.tls")
    return values[0]


def _host_part(entry: str) -> str:
    """The host of a ``host`` query entry: ``h``, ``h:5432``, ``[::1]:5432``, ``::1`` or a socket directory."""
    if entry.startswith("["):
        end = entry.find("]")
        return entry[1:end] if end > 0 else entry
    if entry.count(":") == 1 and not entry.startswith(("/", "@")):
        return entry.partition(":")[0]
    return entry


def _hosts_are_local(url: sqlalchemy.URL, params: Mapping[str, Any]) -> bool:
    if _query_values(params.get("service")) or os.environ.get("PGSERVICE"):
        return False  # pg_service.conf decides the host, which Tabrion does not read (I-41, review M-3)
    hosts = [url.host] if url.host else []
    hosts += [_host_part(entry) for entry in _query_values(params.get("host"))]
    addresses = _query_values(params.get("hostaddr"))
    # libpq falls back to PGHOST and PGHOSTADDR for what the URL leaves out, then to the Unix socket (I-41).
    if not hosts:
        hosts = [_host_part(entry) for entry in _query_values(os.environ.get("PGHOST"))]
    if not addresses:
        addresses = _query_values(os.environ.get("PGHOSTADDR"))
    return all(_host_is_local(host) for host in hosts + addresses)


def _tls(url: sqlalchemy.URL, connect_args: Mapping[str, Any], mode: TLSMode) -> sqlalchemy.URL:
    """Apply the TLS rules of specification section 5.3 (``ConfigurationError("config.tls")``, I-41)."""
    if mode is TLSMode.INSECURE:
        return url
    params = {**dict(url.query), **dict(connect_args)}
    sslmode = _single(params.get("sslmode"))
    if mode is TLSMode.AUTO:
        if _hosts_are_local(url, params):
            return url
        wanted = "require"
    elif mode is TLSMode.REQUIRE:
        wanted = "require"
    else:
        wanted = "verify-full"
    if sslmode is None:
        return url.update_query_dict({"sslmode": wanted})
    if sslmode in _WEAK_SSLMODES or (wanted == "verify-full" and sslmode != "verify-full"):
        raise ConfigurationError("config.tls")
    return url


def _sqlite_creator(path: str, profile: Profile) -> sqlite3.Connection:
    connection = _sqlite.connect(path, profile=profile.sqlite, read_only=profile.read_only, check_same_thread=False)
    if profile.read_only:
        # mode=ro covers the main file only: an ATTACH of the same file (or any other) opens it read-write, so a
        # read-only profile allows no attached database at all (I-56, re-review N-2 of #12).
        connection.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
    return connection


def _sqlite_engine(url: sqlalchemy.URL, profile: Profile, options: dict[str, Any]) -> tuple[sqlalchemy.Engine, bool]:
    driver = url.drivername.partition("+")[2]
    if driver not in ("", "pysqlite"):
        raise ConfigurationError("config.driver")
    if url.query or url.host or url.port or url.username or url.password:
        # The creator opens the file itself; URL options would be ignored silently (I-37).
        raise ConfigurationError("config.url")
    if "connect_args" in options:
        raise ConfigurationError("config.engine_option", details={"option": "connect_args"})
    path = url.database or ":memory:"
    memory = path in (":memory:", "")
    if not memory:
        # check_path and the creation of a new file (file_mode, default 0600) before SQLAlchemy connects: one
        # connection of layer A, closed again (contract section 5.2, I-37). A missing file for a read-only profile
        # raises FileNotFoundError here.
        error: TabrionError | None
        try:
            _sqlite_creator(path, profile).close()
        except sqlite3.Error as exc:
            error = _translate(exc, sent=False)
        else:
            error = None
        if error is not None:
            raise error from None
    creator = functools.partial(_sqlite_creator, ":memory:" if memory else path, profile)
    kwargs: dict[str, Any]
    if memory:
        # One shared connection, so every thread sees the same database; Tabrion serializes its transactions on it.
        kwargs = {"poolclass": StaticPool}
    else:
        kwargs = {
            "poolclass": QueuePool,
            "pool_size": profile.pool_size,
            "max_overflow": profile.max_overflow,
            "pool_timeout": profile.pool_timeout,
            "pool_pre_ping": profile.pool_pre_ping,
            "pool_recycle": profile.pool_recycle,
        }
    kwargs.update(options)
    engine = sqlalchemy.create_engine(url.set(drivername="sqlite+pysqlite"), creator=creator, **kwargs)
    return engine, memory


def _postgres_engine(url: sqlalchemy.URL, profile: Profile, options: dict[str, Any]) -> sqlalchemy.Engine:
    driver = url.drivername.partition("+")[2]
    if driver not in ("", "psycopg"):
        raise ConfigurationError("config.driver")
    require_psycopg()  # the installation hint instead of SQLAlchemy's bare "No module named 'psycopg'"
    url = url.set(drivername="postgresql+psycopg")
    connect_args = options.pop("connect_args", None) or {}
    if not isinstance(connect_args, Mapping):
        raise ConfigurationError("config.engine_option", details={"option": "connect_args"})
    connect_args = dict(connect_args)
    url = _tls(url, connect_args, profile.tls)
    if "connect_timeout" not in url.query and "connect_timeout" not in connect_args:
        connect_args["connect_timeout"] = max(1, math.ceil(profile.connect_timeout))
    kwargs: dict[str, Any] = {
        "poolclass": QueuePool,
        "pool_size": profile.pool_size,
        "max_overflow": profile.max_overflow,
        "pool_timeout": profile.pool_timeout,
        "pool_pre_ping": profile.pool_pre_ping,
        "pool_recycle": profile.pool_recycle,
        "connect_args": connect_args,
    }
    kwargs.update(options)
    return sqlalchemy.create_engine(url, **kwargs)


def _printable_path(path: str) -> str:
    """The path as it is, except that a character that is not printable (a newline, ESC, BEL, DEL, a bidi override,
    a lone surrogate) is percent-encoded as its UTF-8 bytes, so a file name cannot forge a log line or recolour a
    terminal. ``\\``, ``:``, ``@``, ``%`` and spaces stay as they are."""
    return "".join(
        ch if ch.isprintable() else "".join(f"%{byte:02X}" for byte in ch.encode("utf-8", "surrogatepass"))
        for ch in path
    )


def _safe_url(url: sqlalchemy.URL) -> str:
    """The URL with its password masked. A SQLite file path is shown as it is (with :func:`_printable_path`):
    SQLAlchemy percent-encodes it when it renders the URL (``C%3A%5C...`` for ``C:\\...`` on Windows), which no
    reader of a log recognizes. The URL is masked with a placeholder for the path, and the path goes in only
    afterwards, so ``:``, ``@`` or ``?password=`` in a path are never read as credentials or a query."""
    try:
        database = url.database
        if url.get_backend_name() != "sqlite" or not database:
            return _config.safe_url(url.render_as_string(hide_password=True))
        slot = f"tabrion{uuid.uuid4().hex}"  # letters and digits only: masking leaves it alone
        masked = _config.safe_url(url.set(database=slot).render_as_string(hide_password=True))
        before, found, after = masked.partition(slot)
        if not found or slot in after:
            return _config.safe_url(url.render_as_string(hide_password=True))
        return before + _printable_path(database) + after
    except Exception:
        return "<unparseable url>"


def _pool_status(engine: sqlalchemy.Engine) -> dict[str, int] | None:
    pool = engine.pool
    if not isinstance(pool, QueuePool):
        return None
    try:
        return {"checked_out": pool.checkedout(), "size": pool.size(), "overflow": max(0, pool.overflow())}
    except Exception:
        return None


def _corrupt() -> Classification:
    return Classification(
        kind=Kind.CORRUPT,
        retryable=False,
        outcome_unknown=False,
        connection_usable=True,
        driver="sqlite3",
        sqlstate=None,
        sqlite_code=None,
        constraint=None,
        table=None,
        column=None,
    )


# ---------------------------------------------------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------------------------------------------------


class Database:
    """A thread-safe handle on a SQLAlchemy engine with Tabrion's profiles and transaction modes."""

    _engine: sqlalchemy.Engine
    _profile: Profile
    _dialect: Literal["sqlite", "postgresql"]
    _owned: bool
    _safe_url: str
    _closed: bool
    _close_lock: threading.Lock
    _listeners: list[_events.Listener]
    _local: threading.local
    _serial: threading.RLock | None
    _profile_applied: bool
    _begin_callbacks: list[tuple[object, Callable[[sqlalchemy.Connection, bool], None]]]
    """Replaced, never changed in place, under ``_callbacks_lock`` (copy on write; review Info I-1 of #32)."""
    _callbacks_lock: threading.Lock

    _transaction_class: ClassVar[type[Transaction]] = Transaction
    """What ``transaction()`` and ``read()`` yield (the staged 0.2 classes of ``tabrion._sa._staged`` set their own)."""

    def __new__(cls, *args: object, **kwargs: object) -> Database:
        _no_constructor("Database", "Database.from_url(), Database.from_env() or Database.from_engine()")

    @classmethod
    def _create(
        cls,
        engine: sqlalchemy.Engine,
        profile: Profile,
        *,
        owned: bool,
        install_listeners: bool,
        serial: bool,
    ) -> Database:
        self = object.__new__(cls)
        self._closed = True  # until set up: a failure below must not warn from __del__
        dialect = engine.dialect.name
        if dialect not in ("sqlite", "postgresql"):
            raise ConfigurationError("config.driver")
        self._engine = engine
        self._profile = profile
        self._dialect = "sqlite" if dialect == "sqlite" else "postgresql"
        self._owned = owned
        self._safe_url = _safe_url(engine.url)
        self._close_lock = threading.Lock()
        self._local = threading.local()
        self._serial = threading.RLock() if serial else None
        self._listeners = _events.install(engine, profile, owned=owned, apply=install_listeners)
        self._profile_applied = install_listeners
        self._begin_callbacks = []
        self._callbacks_lock = threading.Lock()
        self._closed = False
        return self

    @classmethod
    def from_url(
        cls,
        url: str | sqlalchemy.URL,
        *,
        profile: Profile | None = None,
        engine_options: Mapping[str, Any] | None = None,
    ) -> Database:
        """``sqlite:///path``, ``sqlite://`` (memory), ``postgresql://`` (rewritten to ``+psycopg``)."""
        try:
            return cls._from_url(url, profile, engine_options)
        except BaseException as exc:
            error = exc
        # Raised here without the frames of the helpers: their locals hold the URL, its password and the
        # ``connect_args``, which tools that capture locals would show (I-76). This holds for every exception, also
        # the ``TypeError`` of ``create_engine`` for an engine option it does not know (review C-1/L-A of #25).
        del url, engine_options
        raise error.with_traceback(None)

    @classmethod
    def _from_url(
        cls, url: str | sqlalchemy.URL, profile: Profile | None, engine_options: Mapping[str, Any] | None
    ) -> Database:
        parsed = _parse_url(url)
        profile = _profile(profile)
        options = _engine_options(engine_options)
        # SQLAlchemy leaves the values out of its ``sqlalchemy.engine`` log records and of the ``[parameters: ...]``
        # part of its error messages; ``engine_options={"hide_parameters": False}`` turns them back on (I-76).
        options.setdefault("hide_parameters", True)
        backend = parsed.drivername.partition("+")[0]
        if backend == "sqlite":
            engine, memory = _sqlite_engine(parsed, profile, options)
        elif backend == "postgresql":
            engine, memory = _postgres_engine(parsed, profile, options), False
        else:
            raise ConfigurationError("config.driver")
        try:
            return cls._create(engine, profile, owned=True, install_listeners=True, serial=memory)
        except BaseException:
            engine.dispose()
            raise

    @classmethod
    def from_env(
        cls,
        name: str = "DATABASE_URL",
        *,
        profile: Profile | None = None,
        engine_options: Mapping[str, Any] | None = None,
    ) -> Database:
        """Through ``tabrion.config.url_from_env`` (supports ``*_FILE``)."""
        url = None
        try:
            url = _config.url_from_env(name)
            if url is None:  # required=True raises instead; kept for the type checker
                raise ConfigurationError("config.missing", details={"name": name})
            return cls.from_url(url, profile=profile, engine_options=engine_options)
        except BaseException:
            del url, engine_options  # I-76
            raise

    @classmethod
    def from_engine(
        cls, engine: sqlalchemy.Engine, *, profile: Profile | None = None, install_listeners: bool = True
    ) -> Database:
        """A foreign engine; ``close()`` does not dispose it."""
        if not isinstance(engine, sqlalchemy.Engine):
            raise TypeError(f"engine must be a sqlalchemy.Engine, not {type(engine).__name__}")
        if not isinstance(install_listeners, bool):
            raise TypeError("install_listeners must be a bool")
        profile = _profile(profile)
        expected = {"sqlite": "pysqlite", "postgresql": "psycopg"}.get(engine.dialect.name)
        if expected is None or engine.dialect.driver != expected:
            raise ConfigurationError("config.driver")
        if profile.read_only and install_listeners:
            # The listener would make every connection of the host's pool read-only, also after close() (I-37,
            # T-NOTX rule 2, review M-2): a read-only profile needs an engine of its own.
            raise ConfigurationError("config.invalid_profile", details={"field": "read_only"})
        return cls._create(engine, profile, owned=False, install_listeners=install_listeners, serial=False)

    def __repr__(self) -> str:
        state = " closed" if getattr(self, "_closed", True) else ""
        return f"<tabrion.Database {getattr(self, '_safe_url', '?')}{state}>"

    def __del__(self) -> None:
        if getattr(self, "_closed", True):
            return
        try:
            warnings.warn(f"unclosed {self!r}", ResourceWarning, stacklevel=1, source=self)
        finally:
            with contextlib.suppress(Exception):  # nothing can be reported from a finalizer
                self.close()

    @property
    def dialect(self) -> Literal["sqlite", "postgresql"]:
        return self._dialect

    @property
    def engine(self) -> sqlalchemy.Engine:
        self._check_open()
        return self._engine

    @property
    def safe_url(self) -> str:
        return self._safe_url

    @property
    def closed(self) -> bool:
        return self._closed

    def _check_open(self) -> None:
        if self._closed:
            raise UsageError("database.closed")

    def close(self) -> None:
        """``engine.dispose()``; idempotent."""
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
            _events.remove(self._listeners)
            if self._owned:
                self._engine.dispose()

    def __enter__(self) -> Database:
        self._check_open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # Transactions

    def _checkout(self) -> sqlalchemy.Connection:
        try:
            return self._engine.connect()
        except Exception as exc:
            if not _is_database_error(exc):
                raise
            error = _translate(exc, sent=False)
        raise error from None

    @contextmanager
    def _begin(self, *, read: bool, timeout: float | None, isolation: str | None) -> Iterator[Transaction]:
        self._check_open()
        if getattr(self._local, "active", False):
            raise UsageError("database.nested_transaction")
        if not read and self._profile.read_only:
            raise UsageError("database.read_only")
        serial = self._serial
        if serial is not None:
            serial.acquire()
        self._local.active = True
        try:
            connection = self._checkout()
            try:
                yield from self._run_transaction(connection, read=read, timeout=timeout, isolation=isolation)
            finally:
                connection.close()
        finally:
            self._local.active = False
            if serial is not None:
                serial.release()

    def _run_transaction(
        self, connection: sqlalchemy.Connection, *, read: bool, timeout: float | None, isolation: str | None
    ) -> Iterator[Transaction]:
        postgres = self._dialect == "postgresql"
        options: dict[str, Any] = {_events.MODE: "read" if read else "write"}
        if postgres and read:
            options["postgresql_readonly"] = True
        if postgres and isolation is not None:
            options["isolation_level"] = _ISOLATION_LEVELS[isolation]
        error: TabrionError | None
        try:
            try:
                connection = connection.execution_options(**options)
                if postgres and read:
                    connection.connection.info[_events.READ_USED] = True
                connection.begin()
            except Exception as exc:
                if not _is_database_error(exc):
                    raise
                error = _translate(exc)
            else:
                error = None
            if error is not None:
                raise error from None
            transaction = self._transaction_class._open(
                connection, read_only=read, dialect=self._dialect, timeout=timeout, max_rows=self._profile.max_rows
            )
            failure: BaseException | None = None
            try:
                for _token, callback in self._begin_callbacks:
                    callback(connection, read)  # on_begin (I-105): inside the transaction, before the caller's block
                yield transaction
            except BaseException as exc:
                failure = exc
            transaction._finish()
            if failure is None and transaction._canceled:
                failure = _canceled_error()  # a canceled transaction is never committed (I-102)
            if failure is None and transaction._broken:
                failure = UsageError("database.transaction_ended")  # the caller's SQL ended it (I-104)
            if failure is not None:
                _rollback(connection)
                if transaction._must_not_return_to_pool() and (postgres or _sqlite_file_backed(connection)):
                    with contextlib.suppress(Exception):  # the pool opens a fresh connection instead
                        connection.invalidate()
                if isinstance(failure, Exception) and _is_database_error(failure):
                    # The caller used transaction.connection directly: translated like Tabrion's own calls.
                    error = _translate(failure)
                    failure = None
                    raise error from None
                raise failure
            if read and not self._read_only_held(connection):
                # The caller's SQL lifted read-only (SET TRANSACTION READ WRITE, PRAGMA query_only = OFF, ...):
                # nothing of it is committed (I-56). A guard against accidents, not a security boundary.
                _rollback(connection)
                if postgres or _sqlite_file_backed(connection):
                    # What the caller changed outside the transaction stays on this pooled connection: on
                    # PostgreSQL a session default switched off earlier and committed (TB-061), on SQLite an
                    # attached database or a raised attach limit, which no rollback undoes. The pool opens a fresh
                    # connection instead (re-review N-1 and R-2 of #12). The one shared connection of an in-memory
                    # database is the database itself and stays.
                    with contextlib.suppress(Exception):
                        connection.invalidate()
                raise UsageError("database.read_only") from None
            error = None
            try:
                connection.commit()
            except Exception as exc:
                if not _is_database_error(exc):
                    raise
                error = _translate(exc, during_commit=True)
            if error is not None:
                _rollback(connection)
                raise error from None
        finally:
            if read and not postgres and not self._profile.read_only:
                _query_only_off(connection)

    def _read_only_held(self, connection: sqlalchemy.Connection) -> bool:
        """Whether a ``read()`` transaction is still read only right before its ``COMMIT`` (I-56)."""
        if connection.closed or connection.invalidated:
            return True  # nothing left to commit; the commit below reports the state
        dbapi: Any = connection.connection.dbapi_connection
        if dbapi is None:  # pragma: no cover - a checked-out connection has its DBAPI connection
            return True
        try:
            if self._dialect == "postgresql":
                from psycopg import pq

                if dbapi.info.transaction_status != pq.TransactionStatus.INTRANS:
                    return True  # an aborted transaction commits nothing; an idle one has nothing to commit
                import psycopg
                from psycopg.rows import tuple_row

                # Its own cursor and row factory: whatever the caller set on the connection cannot answer for it.
                with psycopg.Cursor(dbapi, row_factory=tuple_row) as cursor:
                    cursor.execute(_READ_ONLY_STATE_SQL)
                    row = cursor.fetchone()
                if row is None:  # pragma: no cover - the statement always returns one row
                    return False
                # default_transaction_read_only is Tabrion's only where its listener set it (re-review N-3).
                session_ok = not (self._profile.read_only and self._profile_applied) or row[1] == "on"
                return bool(row[0] == "on" and session_ok)
            # The begin listener set query_only = ON for every read(), also under a read-only profile (I-38).
            cursor = dbapi.cursor()  # SQLite (the PostgreSQL branch returned above)
            try:
                cursor.row_factory = None
                value = cursor.execute(_QUERY_ONLY_SQL).fetchone()[0]
            finally:
                cursor.close()
            return bool(value == 1)
        except Exception:
            return False  # cannot tell: roll back rather than commit

    @contextmanager
    def transaction(self, *, timeout: float | None = None, isolation: Isolation | None = None) -> Iterator[Transaction]:
        """SQLite ``BEGIN IMMEDIATE``; PostgreSQL ``BEGIN [ISOLATION LEVEL ...]``. Nested: ``UsageError``."""
        seconds = _optional_seconds(timeout)
        if isolation is not None and isolation not in _ISOLATION_LEVELS:
            raise ValueError("isolation must be 'read_committed', 'repeatable_read' or 'serializable'")
        with self._begin(read=False, timeout=seconds, isolation=isolation) as transaction:
            yield transaction

    @contextmanager
    def read(self, *, timeout: float | None = None) -> Iterator[Transaction]:
        """SQLite ``query_only`` + ``BEGIN``; PostgreSQL ``BEGIN READ ONLY``."""
        seconds = _optional_seconds(timeout)
        with self._begin(read=True, timeout=seconds, isolation=None) as transaction:
            yield transaction

    def execute(self, statement: Statement, params: Params = None, *, timeout: float | None = None) -> int:
        """In its own ``transaction()``."""
        try:
            with self.transaction() as transaction:
                return transaction.execute(statement, params, timeout=timeout)
        except BaseException:
            del statement, params  # I-76
            raise

    def fetch_one(
        self, statement: Statement, params: Mapping[str, Any] | None = None, *, timeout: float | None = None
    ) -> sqlalchemy.Row[Any] | None:
        """In its own ``read()``."""
        try:
            with self.read() as transaction:
                return transaction.fetch_one(statement, params, timeout=timeout)
        except BaseException:
            del statement, params  # I-76
            raise

    def fetch_all(
        self, statement: Statement, params: Mapping[str, Any] | None = None, *, timeout: float | None = None
    ) -> list[sqlalchemy.Row[Any]]:
        """In its own ``read()``."""
        try:
            with self.read() as transaction:
                return transaction.fetch_all(statement, params, timeout=timeout)
        except BaseException:
            del statement, params  # I-76
            raise

    def fetch_scalar(
        self, statement: Statement, params: Mapping[str, Any] | None = None, *, timeout: float | None = None
    ) -> Any:
        """In its own ``read()``."""
        try:
            with self.read() as transaction:
                return transaction.fetch_scalar(statement, params, timeout=timeout)
        except BaseException:
            del statement, params  # I-76
            raise

    # Health and checks

    def health(self, *, timeout: float = 5.0, deep: bool = False) -> Health:
        """Never raises for database errors. ``deep=True`` adds ``PRAGMA quick_check(1)`` on SQLite (G-9)."""
        seconds = _seconds(timeout)
        if not isinstance(deep, bool):
            raise TypeError("deep must be a bool")
        self._check_open()
        if self._dialect == "postgresql":
            return self._postgres_health(seconds)
        return self._sqlite_health(seconds, deep=deep)

    def _health(
        self,
        *,
        ok: bool,
        latency_ms: float | None = None,
        server_version: str | None = None,
        classification: Classification | None = None,
    ) -> Health:
        return Health(
            ok=ok,
            dialect=self._dialect,
            latency_ms=latency_ms,
            server_version=server_version,
            pool=_pool_status(self._engine),
            classification=classification,
        )

    def _postgres_health(self, seconds: float) -> Health:
        from tabrion.postgres import ping

        try:
            raw = self._engine.raw_connection()
        except Exception as exc:
            return self._health(ok=False, classification=classify(exc, sent=False))
        try:
            result = ping(raw.driver_connection, timeout=seconds)  # type: ignore[arg-type]
        except Exception as exc:  # a pool that lent no psycopg connection (TypeError): still a failed check
            return self._health(ok=False, classification=classify(exc))
        finally:
            with contextlib.suppress(Exception):  # the ping result stands; the pool replaces a broken connection
                raw.close()
        version = None if result.server_version is None else str(result.server_version)
        return self._health(
            ok=result.ok, latency_ms=result.latency_ms, server_version=version, classification=result.classification
        )

    def _sqlite_health(self, seconds: float, *, deep: bool) -> Health:
        serial = self._serial
        if serial is not None and not serial.acquire(timeout=seconds):
            # The shared in-memory connection is busy for longer than the timeout.
            return self._health(ok=False, classification=replace(classify(TimeoutError()), kind=Kind.TIMEOUT))
        try:
            with self._engine.connect() as connection:
                started = time.perf_counter()
                _with_deadline(connection, seconds, lambda: connection.exec_driver_sql("SELECT 1").scalar())
                latency_ms = (time.perf_counter() - started) * 1000.0
                verdict: object = "ok"
                if deep:
                    verdict = _with_deadline(
                        connection, seconds, lambda: connection.exec_driver_sql("PRAGMA quick_check(1)").scalar()
                    )
                connection.rollback()
        except _SQLiteDeadline as expired:
            return self._health(ok=False, classification=_timeout_error(expired.cause).classification)
        except Exception as exc:  # a database error of any kind is a failed check, never an exception
            return self._health(ok=False, classification=classify(exc))
        finally:
            if serial is not None:
                serial.release()
        version = sqlite3.sqlite_version
        if verdict != "ok":
            return self._health(ok=False, latency_ms=latency_ms, server_version=version, classification=_corrupt())
        return self._health(ok=True, latency_ms=latency_ms, server_version=version)

    def check(self) -> list[Finding]:
        """``tabrion.check.check(self)``."""
        from tabrion.check import check

        self._check_open()
        return check(self)


def _profile(profile: object) -> Profile:
    if profile is None:
        return Profile()
    if not isinstance(profile, Profile):
        raise TypeError(f"profile must be a tabrion.Profile, not {type(profile).__name__}")
    return profile


_QUERY_ONLY_SQL = "PRAGMA query_only"

_READ_ONLY_STATE_SQL = (
    "SELECT pg_catalog.current_setting('transaction_read_only'),"
    " pg_catalog.current_setting('default_transaction_read_only')"
)


def _rollback(connection: sqlalchemy.Connection) -> None:
    """Roll back; when that fails the connection is invalidated, so the pool never lends it again (I-39)."""
    try:
        connection.rollback()
    except Exception:
        with contextlib.suppress(Exception):  # the connection is gone either way
            connection.invalidate()


def _sqlite_file_backed(connection: sqlalchemy.Connection) -> bool:
    """Whether the main database of this SQLite connection is a file (not ``:memory:`` or a temporary database)."""
    try:
        dbapi: Any = connection.connection.dbapi_connection
        cursor = dbapi.cursor()
        try:
            cursor.row_factory = None
            rows = cursor.execute("PRAGMA database_list").fetchall()
        finally:
            cursor.close()
    except Exception:
        return False  # unknown: keep the connection, as before
    return any(row[1] == "main" and row[2] for row in rows)


def _query_only_off(connection: sqlalchemy.Connection) -> None:
    """``PRAGMA query_only = OFF`` after ``read()``; a failure invalidates the connection (specification 3.1)."""
    if connection.closed or connection.invalidated:
        return
    try:
        dbapi = connection.connection.dbapi_connection
        if dbapi is not None:
            dbapi.execute("PRAGMA query_only = OFF")
    except Exception:
        with contextlib.suppress(Exception):  # the connection is gone either way
            connection.invalidate()
