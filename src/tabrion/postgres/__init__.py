"""PostgreSQL helpers on psycopg connections (layer A, extra ``postgres``).

Contract: ``docs/api/contract.md`` section 4. ``configure``, ``session_configurator``, ``ping``, ``diagnose`` and
``extension_status`` are implemented (TAB-015), the reading role functions too (TAB-016, ``tabrion.postgres.roles``);
``grant_runtime_role`` (TAB-031) and the locks (TAB-030) are still stubs.

psycopg is imported inside the functions only: ``tabrion.Profile`` (layer B, extra ``sqlalchemy``) holds a
``SessionSettings``, and a SQLite-only application installs SQLAlchemy without psycopg. A call without psycopg
raises ``ImportError`` with the hint ``pip install "tabrion[postgres] @ git+https://github.com/peterkarlhuber/tabrion@<commit-sha>"``.

Rules T-NOTX and R-SET (``docs/api/contract.md``, preamble) apply to every function here: each one reads through
its own ``tuple_row`` cursor, never changes the row factory or ``autocommit``, never ends a transaction it did not
open, and only ``configure`` (and the ``session_configurator`` built from it) sets session settings, from a fixed
allowlist.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
import dataclasses
from dataclasses import dataclass
import ipaddress
import math
import re
import time
from typing import TYPE_CHECKING, Any, Literal, Protocol

from tabrion.errors import Classification, ConfigurationError, classify
from tabrion.postgres._common import (
    guarded,
    one_row,
    quote_ident,
    reading,
    require_connection,
    require_psycopg,
    settle,
)
from tabrion.postgres.locks import (
    SessionLock,
    advisory_unlock,
    lock_key,
    try_advisory_lock,
    wait_advisory_lock,
)
from tabrion.postgres.roles import (
    RoleInfo,
    RoleReport,
    _read_role_info,
    check_role,
    grant_runtime_role,
    inspect_role,
    role_info,
)

if TYPE_CHECKING:
    import psycopg

__all__ = [
    "ExtensionStatus",
    "PingResult",
    "PostgresDiagnosis",
    "RoleInfo",
    "RoleReport",
    "SessionLock",
    "SessionSettings",
    "advisory_unlock",
    "check_role",
    "configure",
    "diagnose",
    "extension_status",
    "grant_runtime_role",
    "inspect_role",
    "lock_key",
    "ping",
    "role_info",
    "session_configurator",
    "try_advisory_lock",
    "wait_advisory_lock",
]


@dataclass(frozen=True, slots=True)
class SessionSettings:
    """Session settings, set with ``set_config(..., false)`` in one round trip (02 section 4.2, TAB-ADR-0006).

    ``None`` leaves a setting unchanged. Milliseconds, because PostgreSQL's settings count in ms. Only these
    settings are ever set (R-SET allowlist).
    """

    statement_timeout_ms: int | None = 30_000
    lock_timeout_ms: int | None = 10_000
    idle_in_transaction_timeout_ms: int | None = 300_000
    transaction_timeout_ms: int | None = None
    """PostgreSQL 17 or later only; skipped on older servers (finding TAB-PG-012)."""
    application_name: str | None = "tabrion"
    search_path: str | None = None
    """One schema name, quoted as an identifier."""
    timezone: str | None = "UTC"

    @classmethod
    def unchanged(cls) -> SessionSettings:
        """All fields ``None``: the existing platform configures the session itself."""
        return cls(
            statement_timeout_ms=None,
            lock_timeout_ms=None,
            idle_in_transaction_timeout_ms=None,
            transaction_timeout_ms=None,
            application_name=None,
            search_path=None,
            timezone=None,
        )


# R-SET: the only session settings Tabrion ever sets, fixed in code (field of SessionSettings -> GUC).
_ALLOWED_SETTINGS: dict[str, str] = {
    "statement_timeout_ms": "statement_timeout",
    "lock_timeout_ms": "lock_timeout",
    "idle_in_transaction_timeout_ms": "idle_in_transaction_session_timeout",
    "transaction_timeout_ms": "transaction_timeout",
    "application_name": "application_name",
    "search_path": "search_path",
    "timezone": "TimeZone",
}
_TIMEOUT_FIELDS = frozenset(
    {"statement_timeout_ms", "lock_timeout_ms", "idle_in_transaction_timeout_ms", "transaction_timeout_ms"}
)
_MAX_TIMEOUT_MS = 2_147_483_647
"""The largest value PostgreSQL accepts for a timeout setting (``INT_MAX`` milliseconds)."""
_SCHEMA_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_$]{0,62}")
_PRINTABLE_ASCII = re.compile(r"[\x20-\x7e]{0,63}")
_TRANSACTION_TIMEOUT_SINCE = 170_000


def _invalid(field: str) -> ConfigurationError:
    # The field name only, never the value (an application name or schema is not secret, but a stray value in a
    # wrong field might be).
    return ConfigurationError("postgres.invalid_setting", details={"setting": field})


def _validated(settings: SessionSettings) -> tuple[tuple[str, str], ...]:
    """The ``(GUC, value)`` pairs of ``settings`` that are not ``None``, validated (contract section 4.1)."""
    if not isinstance(settings, SessionSettings):
        raise TypeError(f"settings must be a SessionSettings, not {type(settings).__name__}")
    pairs: list[tuple[str, str]] = []
    for field in dataclasses.fields(settings):
        value = getattr(settings, field.name)
        if value is None:
            continue
        guc = _ALLOWED_SETTINGS.get(field.name)
        if guc is None:
            # A subclass with an extra field: nothing outside the allowlist is ever set (R-SET, TAB-T-G03).
            raise ConfigurationError("postgres.setting_not_allowed", details={"setting": field.name})
        if field.name in _TIMEOUT_FIELDS:
            if type(value) is not int or not 0 <= value <= _MAX_TIMEOUT_MS:
                raise _invalid(field.name)
            pairs.append((guc, str(value)))
        elif field.name == "search_path":
            if type(value) is not str or not _SCHEMA_NAME.fullmatch(value):
                raise _invalid(field.name)
            pairs.append((guc, quote_ident(value)))
        elif field.name == "application_name":
            if type(value) is not str or not _PRINTABLE_ASCII.fullmatch(value):
                raise _invalid(field.name)
            pairs.append((guc, value))
        else:  # timezone: any name the server knows; it rejects the others
            if type(value) is not str or not value or "\x00" in value:
                raise _invalid(field.name)
            pairs.append((guc, value))
    return tuple(pairs)


def _apply(connection: psycopg.Connection[Any], pairs: tuple[tuple[str, str], ...]) -> None:
    """One ``SELECT set_config(%s, %s, false), ...`` round trip on the connection's own cursor (T-NOTX).

    Inside the caller's running transaction it runs under a savepoint (two more round trips, contract I-15).
    """
    require_connection(connection, "configure")
    import psycopg
    from psycopg.rows import tuple_row

    if connection.info.server_version < _TRANSACTION_TIMEOUT_SINCE:
        # transaction_timeout exists from PostgreSQL 17; older servers skip it (tabrion.check reports TAB-PG-012).
        pairs = tuple(pair for pair in pairs if pair[0] != "transaction_timeout")
    if not pairs:
        return
    sql = "SELECT " + ", ".join("pg_catalog.set_config(%s, %s, false)" for _ in pairs)
    params = [item for pair in pairs for item in pair]
    # In the caller's running transaction a savepoint: a value the server rejects (an unknown time zone) fails the
    # call but leaves the caller's transaction usable (review finding M-1).
    with guarded(connection), psycopg.Cursor(connection, row_factory=tuple_row) as cursor:
        cursor.execute(sql, params)


def configure(connection: psycopg.Connection[Any], settings: SessionSettings = SessionSettings()) -> None:
    """Apply ``settings`` in one ``SELECT set_config(...)`` round trip. Never commits.

    Without autocommit the ``SELECT`` opens an implicit transaction that the caller commits.
    """
    _apply(connection, _validated(settings))


def session_configurator(settings: SessionSettings) -> Callable[[psycopg.Connection[Any]], None]:
    """A ``psycopg_pool.ConnectionPool(configure=...)`` callback; validates ``settings`` once, at creation.

    It commits after ``configure`` exactly when the connection was IDLE without autocommit before.
    """
    require_psycopg()
    pairs = _validated(settings)

    def configure_session(connection: psycopg.Connection[Any]) -> None:
        require_connection(connection, "session_configurator")
        from psycopg import pq

        opened = not connection.autocommit and connection.info.transaction_status == pq.TransactionStatus.IDLE
        _apply(connection, pairs)
        if opened:
            # The transaction was opened by _apply itself; psycopg_pool needs an IDLE connection afterwards.
            connection.commit()

    return configure_session


@dataclass(frozen=True, slots=True)
class PingResult:
    ok: bool
    latency_ms: float | None
    server_version: int | None
    """``server_version_num``."""
    in_recovery: bool | None
    classification: Classification | None
    """Set when ``ok`` is ``False``."""


class _PoolLike(Protocol):
    def connection(self, timeout: float | None = ...) -> AbstractContextManager[psycopg.Connection[Any]]: ...


_PING_SQL = "SELECT pg_catalog.current_setting('server_version_num')::int, pg_catalog.pg_is_in_recovery()"


def _seconds(timeout: object) -> float:
    if isinstance(timeout, bool) or not isinstance(timeout, int | float):
        raise TypeError("timeout must be a number of seconds")
    seconds = float(timeout)
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("timeout must be a positive, finite number of seconds")
    return seconds


def _timeout_ms(seconds: float) -> int:
    return min(_MAX_TIMEOUT_MS, max(1, math.ceil(seconds * 1000)))


def _ping_connection(connection: psycopg.Connection[Any], seconds: float) -> PingResult:
    import psycopg
    from psycopg import pq, sql
    from psycopg.rows import tuple_row

    # Pipeline mode: the transaction status is known only after a sync (review DL-1). Outside the try: an error of a
    # statement the caller queued is the caller's and propagates; reported as a failed ping, psycopg would not raise
    # it again and the caller's COMMIT would end its aborted transaction silently (review M-1 of #6).
    settle(connection)
    try:
        if connection.info.transaction_status == pq.TransactionStatus.INERROR:
            # An aborted transaction takes no savepoint (psycopg would log the failed rollback of it, review I-1):
            # the SELECT fails there like any statement, with InFailedSqlTransaction, and ends nothing.
            with psycopg.Cursor(connection, row_factory=tuple_row) as cur:
                cur.execute(_PING_SQL)
        # A transaction of its own (BEGIN ... ROLLBACK), or a savepoint inside a running transaction that is rolled
        # back: the SET LOCAL ends with it, and a timeout leaves the caller's transaction usable (contract I-14).
        with connection.transaction(force_rollback=True), psycopg.Cursor(connection, row_factory=tuple_row) as cur:
            # SET takes no snapshot, so the limit covers the SELECT from its start (a wait for a safe snapshot, a
            # lock); the value is an int Tabrion computed, formatted as a literal (SET takes no parameters).
            cur.execute(sql.SQL("SET LOCAL statement_timeout = {}").format(sql.Literal(_timeout_ms(seconds))))
            started = time.perf_counter()
            cur.execute(_PING_SQL)
            row = one_row(cur)
            latency_ms = (time.perf_counter() - started) * 1000.0
    except Exception as exc:  # a database error of any kind is a failed ping, never an exception
        return PingResult(
            ok=False, latency_ms=None, server_version=None, in_recovery=None, classification=classify(exc)
        )
    return PingResult(
        ok=True, latency_ms=latency_ms, server_version=int(row[0]), in_recovery=bool(row[1]), classification=None
    )


def ping(source: psycopg.Connection[Any] | _PoolLike, *, timeout: float = 5.0) -> PingResult:
    """Check a connection or pool. Never raises for database errors (``ok=False``), only ``TypeError``.

    ``SELECT current_setting('server_version_num')::int, pg_is_in_recovery()`` under ``SET LOCAL
    statement_timeout`` = ``timeout``, in a transaction of its own that is rolled back (a savepoint when the caller's
    transaction is running). A pool lends its connection with ``timeout``; a failed checkout is classified with
    ``sent=False``. ``latency_ms`` is the round trip of that ``SELECT``. In pipeline mode the caller's pipeline is
    synced first, and an error of a statement the caller queued propagates: it is not the ping's (contract I-34).
    """
    require_psycopg()
    import psycopg

    seconds = _seconds(timeout)
    if isinstance(source, psycopg.Connection):
        return _ping_connection(source, seconds)
    if not callable(getattr(source, "connection", None)):
        raise TypeError(f"ping needs a psycopg.Connection or a pool, not {type(source).__name__}")
    try:
        with source.connection(timeout=seconds) as connection:
            if not isinstance(connection, psycopg.Connection):
                raise TypeError(f"the pool lent a {type(connection).__name__}, not a psycopg.Connection")
            return _ping_connection(connection, seconds)
    except TypeError:
        raise
    except Exception as exc:  # checkout (or return) failed; _ping_connection itself never raises
        return PingResult(
            ok=False, latency_ms=None, server_version=None, in_recovery=None, classification=classify(exc, sent=False)
        )


@dataclass(frozen=True, slots=True)
class PostgresDiagnosis:
    server_version: int
    server_version_text: str
    database: str
    user: str
    schema: str | None
    search_path: str
    in_recovery: bool
    ssl: bool | None
    """``pg_stat_ssl`` of the own backend; ``None`` without the privilege."""
    application_name: str
    statement_timeout_ms: int
    lock_timeout_ms: int
    idle_in_transaction_timeout_ms: int
    archive_mode: str | None
    wal_level: str | None
    role: RoleInfo
    host_is_local: bool


_TIME_UNITS_MS = {"": 1, "ms": 1, "s": 1_000, "min": 60_000, "h": 3_600_000, "d": 86_400_000}
_TIME_SETTING = re.compile(r"(-?\d+)\s*(ms|s|min|h|d)?")


def _milliseconds(value: str) -> int:
    """A time setting as ``current_setting`` shows it (``0``, ``500ms``, ``30s``, ``5min``) in milliseconds."""
    match = _TIME_SETTING.fullmatch(value.strip())
    if match is None:
        raise ValueError("unexpected time setting")
    return int(match.group(1)) * _TIME_UNITS_MS[match.group(2) or ""]


def _host_is_local(host: str) -> bool:
    """A Unix socket (a directory or an abstract ``@`` name), ``localhost`` or a loopback address."""
    if not host or host.startswith(("/", "@")) or host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.split("%", 1)[0]).is_loopback
    except ValueError:
        return False


def diagnose(connection: psycopg.Connection[Any]) -> PostgresDiagnosis:
    """Read-only diagnosis; fields without read privilege are ``None``.

    The timeouts come from ``pg_settings`` in milliseconds (or from ``current_setting`` when ``pg_settings`` is not
    readable); ``archive_mode`` and ``wal_level`` are ``None`` when the role may not see them, ``ssl`` when it may not
    read ``pg_stat_ssl``. ``role`` is ``role_info(connection)`` (``owner_scope="schema"``).
    """
    with reading(connection, "diagnose") as cur:
        cur.execute(
            "SELECT pg_catalog.current_setting('server_version_num')::int,"
            " pg_catalog.current_setting('server_version'), pg_catalog.current_database()::pg_catalog.text,"
            " CURRENT_USER::pg_catalog.text, pg_catalog.current_schema()::pg_catalog.text,"
            " pg_catalog.current_setting('search_path'),"
            " pg_catalog.pg_is_in_recovery(), pg_catalog.current_setting('application_name'),"
            " pg_catalog.current_setting('statement_timeout'), pg_catalog.current_setting('lock_timeout'),"
            " pg_catalog.current_setting('idle_in_transaction_session_timeout'),"
            " pg_catalog.has_table_privilege('pg_catalog.pg_settings', 'SELECT'),"
            " pg_catalog.has_table_privilege('pg_catalog.pg_stat_ssl', 'SELECT')"
        )
        (version, version_text, database, user, schema, search_path, in_recovery, application_name, statement_timeout,
         lock_timeout, idle_timeout, settings_readable, ssl_readable) = one_row(cur)  # fmt: skip
        timeouts = [_milliseconds(statement_timeout), _milliseconds(lock_timeout), _milliseconds(idle_timeout)]
        archive_mode: str | None = None
        wal_level: str | None = None
        if settings_readable:
            # pg_settings hides the rows a role may not see (GUC_SUPERUSER_ONLY); a missing row gives None.
            cur.execute(
                "SELECT s.name::pg_catalog.text, s.setting FROM pg_catalog.pg_settings s"
                " WHERE s.name::pg_catalog.text OPERATOR(pg_catalog.=) ANY (%s::pg_catalog.text[])",
                (["statement_timeout", "lock_timeout", "idle_in_transaction_session_timeout", "archive_mode",
                  "wal_level"],),
            )  # fmt: skip
            found = {str(name): str(value) for name, value in cur.fetchall()}
            for index, name in enumerate(("statement_timeout", "lock_timeout", "idle_in_transaction_session_timeout")):
                if name in found:
                    timeouts[index] = int(found[name])  # the unit of these settings is ms
            archive_mode = found.get("archive_mode")
            wal_level = found.get("wal_level")
        ssl: bool | None = None
        if ssl_readable:
            cur.execute(
                "SELECT s.ssl FROM pg_catalog.pg_stat_ssl s"
                " WHERE s.pid OPERATOR(pg_catalog.=) pg_catalog.pg_backend_pid()"
            )
            row = cur.fetchone()
            ssl = bool(row[0]) if row is not None and row[0] is not None else None
        role = _read_role_info(cur, "schema")
    return PostgresDiagnosis(
        server_version=int(version),
        server_version_text=str(version_text),
        database=str(database),
        user=str(user),
        schema=str(schema) if schema is not None else None,
        search_path=str(search_path),
        in_recovery=bool(in_recovery),
        ssl=ssl,
        application_name=str(application_name),
        statement_timeout_ms=timeouts[0],
        lock_timeout_ms=timeouts[1],
        idle_in_transaction_timeout_ms=timeouts[2],
        archive_mode=archive_mode,
        wal_level=wal_level,
        role=role,
        host_is_local=_host_is_local(str(connection.info.host or "")),
    )


_EXTENSION_NAME = re.compile(r"[a-z_][a-z0-9_]*")


@dataclass(frozen=True, slots=True)
class ExtensionStatus:
    name: str
    state: Literal["active", "other_schema", "installable", "unavailable"]
    installed_version: str | None
    available_version: str | None
    extension_schema: str | None
    schema: str | None
    """``current_schema()``."""
    command: str | None
    """SQL that fixes the state (superuser or owner); ``None`` when there is nothing to do."""


def _read_extension(connection: psycopg.Connection[Any], name: str, probe_type: str | None) -> tuple[Any, ...]:
    with reading(connection, "extension_status") as cur:
        cur.execute(
            "SELECT e.extversion::pg_catalog.text, n.nspname::pg_catalog.text,"
            " COALESCE(n.nspname OPERATOR(pg_catalog.=) ANY (pg_catalog.current_schemas(true)), false),"
            " (SELECT a.default_version::pg_catalog.text FROM pg_catalog.pg_available_extensions a"
            "   WHERE a.name::pg_catalog.text OPERATOR(pg_catalog.=) %(name)s::pg_catalog.text),"
            " pg_catalog.current_schema()::pg_catalog.text,"
            " pg_catalog.to_regtype(%(probe)s::pg_catalog.text) IS NOT NULL"
            " FROM (VALUES (1)) AS one (x)"
            " LEFT JOIN pg_catalog.pg_extension e"
            "   ON e.extname::pg_catalog.text OPERATOR(pg_catalog.=) %(name)s::pg_catalog.text"
            " LEFT JOIN pg_catalog.pg_namespace n ON n.oid OPERATOR(pg_catalog.=) e.extnamespace",
            {"name": name, "probe": probe_type},
        )
        return one_row(cur)


def extension_status(
    connection: psycopg.Connection[Any], name: str, *, probe_type: str | None = None
) -> ExtensionStatus:
    """The state of an extension; with ``probe_type`` also ``to_regtype(probe_type) IS NOT NULL``.

    * ``active``: installed, its schema is in ``current_schemas(true)`` and (with ``probe_type``) the type resolves;
    * ``other_schema``: installed but not usable here; ``command`` moves it to ``current_schema()``;
    * ``installable``: in ``pg_available_extensions``; ``command`` creates it in ``current_schema()``;
    * ``unavailable``: neither; the server needs the package first (``command`` is ``None``).

    The commands name ``"public"`` when the connection has no current schema (Nodestro ``pgvector_status``). A
    ``probe_type`` the server cannot parse raises ``ValueError`` (contract I-18).
    """
    if not isinstance(name, str):
        raise TypeError("name must be a str")
    if not _EXTENSION_NAME.fullmatch(name):
        raise ValueError("name must match [a-z_][a-z0-9_]*")
    if probe_type is not None and not isinstance(probe_type, str):
        raise TypeError("probe_type must be a str or None")
    try:
        row = _read_extension(connection, name, probe_type)
    except Exception as exc:
        # to_regtype raises (42601) on a type name it cannot parse, the only syntax error this statement can meet;
        # the reader's savepoint or rollback has already left the connection as it was (review finding M-1).
        if probe_type is not None and getattr(exc, "sqlstate", None) == "42601":
            raise ValueError("probe_type is not a type name the server can parse") from None
        raise
    installed, extension_schema, visible, available, schema, probe_ok = row
    target = quote_ident(str(schema) if schema is not None else "public")
    state: Literal["active", "other_schema", "installable", "unavailable"]
    command: str | None = None
    if installed is not None and visible and (probe_type is None or probe_ok):
        state = "active"
    elif installed is not None:
        state = "other_schema"
        command = f"ALTER EXTENSION {quote_ident(name)} SET SCHEMA {target};"
    elif available is not None:
        state = "installable"
        command = f"CREATE EXTENSION {quote_ident(name)} SCHEMA {target};"
    else:
        state = "unavailable"
    return ExtensionStatus(
        name=name,
        state=state,
        installed_version=str(installed) if installed is not None else None,
        available_version=str(available) if available is not None else None,
        extension_schema=str(extension_schema) if extension_schema is not None else None,
        schema=str(schema) if schema is not None else None,
        command=command,
    )
