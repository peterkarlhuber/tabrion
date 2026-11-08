"""PostgreSQL helpers on psycopg connections (layer A, extra ``postgres``).

Contract: ``docs/api/contract.md`` section 4. Stub for the API freeze G-T1; the implementation follows in TAB-015
(configure, ping, diagnose, extension_status), TAB-016/TAB-031 (roles) and TAB-030 (locks).

psycopg is imported for type checking only in this stub: ``tabrion.Profile`` (layer B, extra ``sqlalchemy``)
holds a ``SessionSettings``, and a SQLite-only application installs SQLAlchemy without psycopg.
Rules T-NOTX and R-SET (``docs/api/contract.md``, preamble) apply to every function here.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol

from tabrion.errors import Classification
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
        raise NotImplementedError("tabrion.postgres.SessionSettings.unchanged: implemented in TAB-015")


def configure(connection: psycopg.Connection[Any], settings: SessionSettings = SessionSettings()) -> None:  # noqa: B008
    """Apply ``settings`` in one ``SELECT set_config(...)`` round trip. Never commits.

    Without autocommit the ``SELECT`` opens an implicit transaction that the caller commits.
    """
    raise NotImplementedError("tabrion.postgres.configure: implemented in TAB-015")


def session_configurator(settings: SessionSettings) -> Callable[[psycopg.Connection[Any]], None]:
    """A ``psycopg_pool.ConnectionPool(configure=...)`` callback; validates ``settings`` once, at creation.

    It commits after ``configure`` exactly when the connection was IDLE without autocommit before.
    """
    raise NotImplementedError("tabrion.postgres.session_configurator: implemented in TAB-015")


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


def ping(source: psycopg.Connection[Any] | _PoolLike, *, timeout: float = 5.0) -> PingResult:
    """Check a connection or pool. Never raises for database errors (``ok=False``), only ``TypeError``."""
    raise NotImplementedError("tabrion.postgres.ping: implemented in TAB-015")


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


def diagnose(connection: psycopg.Connection[Any]) -> PostgresDiagnosis:
    """Read-only diagnosis; fields without read privilege are ``None``."""
    raise NotImplementedError("tabrion.postgres.diagnose: implemented in TAB-015")


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


def extension_status(
    connection: psycopg.Connection[Any], name: str, *, probe_type: str | None = None
) -> ExtensionStatus:
    """The state of an extension; with ``probe_type`` also ``to_regtype(probe_type) IS NOT NULL``."""
    raise NotImplementedError("tabrion.postgres.extension_status: implemented in TAB-015")
