"""Layer-B checks of ``tabrion.check.check`` for a ``tabrion.Database`` (private, extra ``sqlalchemy``).

``tabrion.check`` loads this module by name when the target is a ``Database`` (G-T1 condition C-1, fix B; contract
section 7). It runs the dialect's checks (``tabrion.check`` for SQLite, ``tabrion.postgres._check`` for PostgreSQL,
through ``tabrion.check.check`` on the raw driver connection) inside one ``Database.read()``, so nothing is written,
and adds the layer-B findings (contract I-55, TODO TB-060):

- TAB-PG-012 (info): the ``Profile`` wants a ``transaction_timeout`` and the server is older than PostgreSQL 17,
  which skips it (``tabrion.postgres.configure``);
- TAB-B-001 (info): the engine's pool does not ``pre_ping`` (not for the single shared connection of an in-memory
  SQLite database, which is never stale);
- TAB-B-002: ``check`` has no script directory (the API is frozen), and layer B never imports Alembic, so it cannot
  compare the version table with the heads. When an Alembic version table ``alembic_version`` is visible it reports
  "Not checked" (info) and points at ``tabrion.migrations.require_current``, which raises when the schema is not
  current (deviation D-66).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy.pool import StaticPool

from tabrion.check import Finding, Severity, check

if TYPE_CHECKING:
    import sqlalchemy

    from tabrion._sa.database import Database

__all__ = ["collect"]

_POSTGRES_17 = 170000

_VERSION_TABLE_SQL = {
    "sqlite": "SELECT count(*) FROM sqlite_master WHERE type = 'table' AND name = 'alembic_version'",
    # to_regclass resolves through the search_path, as Alembic does for a version table without a schema.
    "postgresql": "SELECT pg_catalog.to_regclass('alembic_version') IS NOT NULL",
}


def _transaction_timeout(target: Database, dbapi: Any) -> list[Finding]:
    wanted = target._profile.postgres.transaction_timeout_ms
    version = int(dbapi.info.server_version)
    if not wanted or version >= _POSTGRES_17:
        return []
    return [
        Finding(
            code="TAB-PG-012",
            severity=Severity.INFO,
            title="transaction_timeout is not in effect",
            detail=(
                f"The profile sets transaction_timeout to {int(wanted)} ms, but the server version is {version}; "
                "PostgreSQL before 17 has no transaction_timeout, so it was skipped."
            ),
            remedy="Upgrade the server to PostgreSQL 17 or later, or bound transactions with statement_timeout.",
        )
    ]


def _pre_ping(target: Database) -> list[Finding]:
    pool = target.engine.pool
    if isinstance(pool, StaticPool) or getattr(pool, "_pre_ping", True):
        return []
    return [
        Finding(
            code="TAB-B-001",
            severity=Severity.INFO,
            title="Pool without pre_ping",
            detail="The connection pool does not test a connection before it hands it out (pool_pre_ping is off).",
            remedy="Keep Profile.pool_pre_ping=True (the default), so a dropped connection is replaced before use.",
        )
    ]


def _version_table(connection: sqlalchemy.Connection, dialect: str) -> list[Finding]:
    if not connection.exec_driver_sql(_VERSION_TABLE_SQL[dialect]).scalar():
        return []
    return [
        Finding(
            code="TAB-B-002",
            severity=Severity.INFO,
            title="Not checked",
            detail=(
                "An Alembic version table exists, but check() does not know the script directory, "
                "so TAB-B-002 was not checked."
            ),
            remedy="Call tabrion.migrations.require_current(database, script_location=...) at start-up.",
        )
    ]


def collect(target: Database, *, expect: Literal["runtime", "owner", "any"] = "runtime") -> list[Finding]:
    """The findings for a ``Database``: those of its dialect plus the layer-B ones, read only."""
    findings: list[Finding] = []
    with target.read() as transaction:
        connection = transaction.connection
        dbapi: Any = connection.connection.dbapi_connection
        findings += check(dbapi, expect=expect)
        if target.dialect == "postgresql":
            findings += _transaction_timeout(target, dbapi)
        findings += _version_table(connection, target.dialect)
    findings += _pre_ping(target)
    return findings
