"""``Database`` and ``Transaction``: layer B over a SQLAlchemy engine. Private: use ``tabrion.Database``.

Contract: ``docs/api/contract.md`` sections 5.2, 5.3 and 5.5, implementation notes I-37..I-44 (TAB-018). The 0.2
methods (TAB-033) are staged in ``tabrion._sa._v02`` and are not part of these classes before 0.2 (G-T1 condition
C-6, ``docs/api/surface.json``).

``Statement``, ``Params`` and ``Isolation`` are documentation-only aliases: they name the types in the signatures and
are not exported from ``tabrion`` (G-T1 F-11, TODO TB-020).
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
from typing import TYPE_CHECKING, Any, Literal, NoReturn, TypeAlias, TypeVar
import warnings

import sqlalchemy
from sqlalchemy.pool import QueuePool, StaticPool

from tabrion import config as _config
from tabrion import sqlite as _sqlite
from tabrion._sa import events as _events
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

Statement: TypeAlias = str | sqlalchemy.sql.Executable
"""A ``str`` becomes ``sqlalchemy.text()`` with named parameters (``:name``)."""
Params: TypeAlias = Mapping[str, Any] | Sequence[Mapping[str, Any]] | None
"""A sequence of mappings runs ``executemany``."""
Isolation: TypeAlias = Literal["read_committed", "repeatable_read", "serializable"]

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
    return error_class(
        f"database.{classification.kind.value}",
        classification=classification,
        original=original,
        statement=_statement_text(exc, statement),
    )


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

    def __new__(cls, *args: object, **kwargs: object) -> Transaction:
        _no_constructor("Transaction", "Database.transaction() or Database.read()")

    @classmethod
    def _open(
        cls, connection: sqlalchemy.Connection, *, read_only: bool, dialect: str, timeout: float | None
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

    # The statement runner: timeout, execution and error translation in one place.

    def _run(
        self,
        statement: Statement,
        params: dict[str, Any] | list[dict[str, Any]] | None,
        timeout: float | None,
        consume: Callable[[sqlalchemy.CursorResult[Any]], _T],
    ) -> _T:
        self._check()
        executable = _statement(statement)
        seconds = _optional_seconds(timeout)
        if seconds is None:
            seconds = self._default_timeout
        connection = self._connection
        error: TabrionError
        try:
            if self._dialect == "postgresql":
                self._pg_statement_timeout(seconds)
                return consume(connection.execute(executable, params))
            return _with_deadline(connection, seconds, lambda: consume(connection.execute(executable, params)))
        except _SQLiteDeadline as expired:
            error = _timeout_error(expired.cause, statement)
        except Exception as exc:
            if not _is_database_error(exc):
                raise
            error = _translate(exc, statement)
        # Raised outside the except block: no __context__ that holds the SQLAlchemy error and its parameters (I-39).
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

    def execute(self, statement: Statement, params: Params = None, *, timeout: float | None = None) -> int:
        """Row count (``-1`` becomes ``0`` for DDL)."""
        values = _execute_params(params)
        if isinstance(values, list) and not values:
            self._check()
            _statement(statement)
            return 0  # executemany of nothing: SQLAlchemy would run the statement once without parameters

        def consume(result: sqlalchemy.CursorResult[Any]) -> int:
            count = result.rowcount
            result.close()
            return max(count, 0)

        return self._run(statement, values, timeout, consume)

    def fetch_one(
        self, statement: Statement, params: Mapping[str, Any] | None = None, *, timeout: float | None = None
    ) -> sqlalchemy.Row[Any] | None:
        """0 rows: ``None``; more than one: ``MultipleRowsError``."""
        return self._run(statement, _fetch_params(params), timeout, _one_row)

    def fetch_all(
        self, statement: Statement, params: Mapping[str, Any] | None = None, *, timeout: float | None = None
    ) -> list[sqlalchemy.Row[Any]]:
        return self._run(statement, _fetch_params(params), timeout, _all_rows)

    def fetch_scalar(
        self, statement: Statement, params: Mapping[str, Any] | None = None, *, timeout: float | None = None
    ) -> Any:
        """First column of the only row; 0 rows: ``None``; more than one: ``MultipleRowsError``."""
        row = self._run(statement, _fetch_params(params), timeout, _one_row)
        return None if row is None else row[0]


class _SQLiteDeadline(Exception):
    """Internal: the progress handler stopped a statement at its deadline (becomes ``StatementTimeout``)."""

    def __init__(self, cause: BaseException) -> None:
        super().__init__("deadline")
        self.cause = cause


def _with_deadline(connection: sqlalchemy.Connection, seconds: float | None, call: Callable[[], _T]) -> _T:
    """SQLite: run ``call`` under a deadline checked every 1 000 VM steps (``set_progress_handler``, I-40)."""
    if seconds is None:
        return call()
    dbapi = connection.connection.dbapi_connection
    if not isinstance(dbapi, sqlite3.Connection):
        return call()
    deadline = time.monotonic() + seconds
    expired = False

    def progress() -> int:
        nonlocal expired
        if time.monotonic() >= deadline:
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
    hosts = [url.host] if url.host else []
    hosts += [_host_part(entry) for entry in _query_values(params.get("host"))]
    addresses = _query_values(params.get("hostaddr"))
    if not hosts and not addresses:
        # libpq falls back to PGHOST/PGHOSTADDR, then to the Unix socket (I-41).
        hosts = [_host_part(entry) for entry in _query_values(os.environ.get("PGHOST"))]
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
    return _sqlite.connect(path, profile=profile.sqlite, read_only=profile.read_only, check_same_thread=False)


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


def _safe_url(url: sqlalchemy.URL) -> str:
    try:
        rendered = url.render_as_string(hide_password=True)
    except Exception:
        return "<unparseable url>"
    return _config.safe_url(rendered)


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
        parsed = _parse_url(url)
        profile = _profile(profile)
        options = _engine_options(engine_options)
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
        url = _config.url_from_env(name)
        if url is None:  # required=True raises instead; kept for the type checker
            raise ConfigurationError("config.missing", details={"name": name})
        return cls.from_url(url, profile=profile, engine_options=engine_options)

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
            transaction = Transaction._open(connection, read_only=read, dialect=self._dialect, timeout=timeout)
            failure: BaseException | None = None
            try:
                yield transaction
            except BaseException as exc:
                failure = exc
            transaction._active = False
            if failure is not None:
                _rollback(connection)
                if isinstance(failure, Exception) and _is_database_error(failure):
                    # The caller used transaction.connection directly: translated like Tabrion's own calls.
                    error = _translate(failure)
                    failure = None
                    raise error from None
                raise failure
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
        with self.transaction() as transaction:
            return transaction.execute(statement, params, timeout=timeout)

    def fetch_one(
        self, statement: Statement, params: Mapping[str, Any] | None = None, *, timeout: float | None = None
    ) -> sqlalchemy.Row[Any] | None:
        """In its own ``read()``."""
        with self.read() as transaction:
            return transaction.fetch_one(statement, params, timeout=timeout)

    def fetch_all(
        self, statement: Statement, params: Mapping[str, Any] | None = None, *, timeout: float | None = None
    ) -> list[sqlalchemy.Row[Any]]:
        """In its own ``read()``."""
        with self.read() as transaction:
            return transaction.fetch_all(statement, params, timeout=timeout)

    def fetch_scalar(
        self, statement: Statement, params: Mapping[str, Any] | None = None, *, timeout: float | None = None
    ) -> Any:
        """In its own ``read()``."""
        with self.read() as transaction:
            return transaction.fetch_scalar(statement, params, timeout=timeout)

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


def _rollback(connection: sqlalchemy.Connection) -> None:
    """Roll back; when that fails the connection is invalidated, so the pool never lends it again (I-39)."""
    try:
        connection.rollback()
    except Exception:
        with contextlib.suppress(Exception):  # the connection is gone either way
            connection.invalidate()


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
