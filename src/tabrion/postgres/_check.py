"""PostgreSQL checks of ``tabrion.check.check`` (TAB-PG-*; private, layer A, extra ``postgres``).

``tabrion.check`` loads this module by name when the target is a ``psycopg.Connection`` (G-T1 condition C-1, fix
B; contract section 7). It may import ``tabrion.check`` and the rest of ``tabrion.postgres``, never layer B.
Implementation notes I-52..I-55 (TAB-017). Every statement only reads, through ``tabrion.postgres._common.reading``
(T-NOTX, contract I-16), and qualifies every catalog object with ``pg_catalog``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from tabrion.check import Finding, Severity, _finding, _not_checked
from tabrion.postgres import diagnose
from tabrion.postgres._common import one_row, reading, require_connection, require_psycopg

if TYPE_CHECKING:
    import psycopg

    from tabrion.postgres import PostgresDiagnosis

__all__ = ["collect"]

_MIN_SERVER_VERSION = 160_000
_SECURE_SSLMODE = "verify-full"

_SESSION_SQL = (
    "SELECT pg_catalog.has_schema_privilege(pg_catalog.current_schema(), 'CREATE'),"
    " pg_catalog.current_setting('default_transaction_read_only')"
)
_EXTENSIONS_SQL = (
    "SELECT e.extname::pg_catalog.text, n.nspname::pg_catalog.text FROM pg_catalog.pg_extension e"
    " JOIN pg_catalog.pg_namespace n ON n.oid OPERATOR(pg_catalog.=) e.extnamespace"
    " WHERE n.nspname OPERATOR(pg_catalog.<>) ALL (pg_catalog.current_schemas(true))"
    " ORDER BY 1"
)


def _sslmode(connection: psycopg.Connection[Any]) -> str:
    """The ``sslmode`` libpq used for this connection (the URL, ``PGSSLMODE`` or libpq's default ``prefer``)."""
    for option in connection.pgconn.info:
        if option.keyword == b"sslmode":
            value = option.val if option.val is not None else option.compiled
            return value.decode() if isinstance(value, bytes) else "prefer"
    return "prefer"  # pragma: no cover - libpq always lists sslmode


def collect(
    target: psycopg.Connection[Any], *, expect: Literal["runtime", "owner", "any"] = "runtime"
) -> list[Finding]:
    """The TAB-PG-* findings for a psycopg connection, read only (uses ``diagnose`` and ``role_info``)."""
    require_psycopg()
    require_connection(target, "check")
    facts = diagnose(target)
    with reading(target, "check") as cursor:
        cursor.execute(_SESSION_SQL)
        can_create, default_read_only = one_row(cursor)
        cursor.execute(_EXTENSIONS_SQL)
        outside_path = [(str(name), str(schema)) for name, schema in cursor.fetchall()]
    findings = _role_findings(facts, expect, can_create)
    findings += _setting_findings(facts)
    findings += _tls_findings(target, facts)
    findings += _server_findings(facts, read_only=str(default_read_only) == "on")
    for name, schema in outside_path:
        findings.append(_finding(
            "TAB-PG-030", Severity.WARNING, "Extension schema not in the search path",
            f"The extension {name} is installed in schema {schema}, which is not in this session's search_path; "
            "its types and functions do not resolve unqualified.",
            f"Add {schema} to the search_path of the role or database, or qualify the extension's objects.",
        ))  # fmt: skip
    return findings


def _role_findings(facts: PostgresDiagnosis, expect: str, can_create: object) -> list[Finding]:
    findings: list[Finding] = []
    role = facts.role
    if role.superuser:
        findings.append(_finding(
            "TAB-PG-001", Severity.ERROR, "Role is a superuser",
            "The session's role is, or can become, a superuser: row-level security and every grant are bypassed.",
            "Connect with a runtime role without SUPERUSER (tabrion.postgres.grant_runtime_role).",
        ))  # fmt: skip
    if role.bypass_rls:
        findings.append(_finding(
            "TAB-PG-002", Severity.ERROR, "Role bypasses row-level security",
            "The session's role has, or can become a role with, BYPASSRLS.",
            "Connect with a role without BYPASSRLS.",
        ))  # fmt: skip
    if expect == "owner":
        return findings
    # The runtime-role findings: expect="any" keeps them as info, because the role may be either (I-53).
    severity = Severity.WARNING if expect == "runtime" else Severity.INFO
    if role.table_owner:
        findings.append(_finding(
            "TAB-PG-003", severity, "Role owns tables",
            "The session's role owns (or can become the owner of) tables in its current schema; an owner is not "
            "subject to row-level security unless FORCE ROW LEVEL SECURITY is set.",
            "Use an owner role for migrations and a separate runtime role for the application.",
        ))  # fmt: skip
    if can_create is True:
        findings.append(_finding(
            "TAB-PG-009", severity, "Role may create objects in the schema",
            "The session's role has CREATE on its current schema.",
            "REVOKE CREATE ON SCHEMA from the runtime role; only the owner role creates objects.",
        ))  # fmt: skip
    return findings


def _setting_findings(facts: PostgresDiagnosis) -> list[Finding]:
    findings: list[Finding] = []
    if facts.statement_timeout_ms == 0:
        findings.append(_finding(
            "TAB-PG-004", Severity.WARNING, "No statement timeout",
            "statement_timeout is 0: a runaway statement runs without limit.",
            "Set statement_timeout (SessionSettings uses 30 s).",
        ))  # fmt: skip
    if facts.lock_timeout_ms == 0:
        findings.append(_finding(
            "TAB-PG-005", Severity.INFO, "No lock timeout",
            "lock_timeout is 0: a statement waits for a lock until statement_timeout ends it.",
            "Set lock_timeout (SessionSettings uses 10 s).",
        ))  # fmt: skip
    if facts.idle_in_transaction_timeout_ms == 0:
        findings.append(_finding(
            "TAB-PG-006", Severity.WARNING, "No idle-in-transaction timeout",
            "idle_in_transaction_session_timeout is 0: a forgotten transaction holds its locks and snapshot.",
            "Set idle_in_transaction_session_timeout (SessionSettings uses 300 s).",
        ))  # fmt: skip
    if facts.application_name == "":
        findings.append(_finding(
            "TAB-PG-007", Severity.INFO, "Empty application name",
            "application_name is empty, so the session cannot be told apart in pg_stat_activity.",
            "Set application_name (SessionSettings uses 'tabrion').",
        ))  # fmt: skip
    return findings


def _tls_findings(connection: psycopg.Connection[Any], facts: PostgresDiagnosis) -> list[Finding]:
    if facts.host_is_local:
        return []
    # The client knows whether this connection is encrypted; pg_stat_ssl (facts.ssl) needs a privilege (I-54).
    encrypted = bool(connection.pgconn.ssl_in_use)
    if not encrypted:
        return [_finding(
            "TAB-PG-011", Severity.ERROR, "Remote connection without TLS",
            "The connection to a remote host is not encrypted; passwords and data travel in plain text.",
            "Connect with sslmode=verify-full (tabrion.TLSMode.VERIFY_FULL).",
        )]  # fmt: skip
    sslmode = _sslmode(connection)
    if sslmode != _SECURE_SSLMODE:
        return [_finding(
            "TAB-PG-010", Severity.WARNING, "Remote connection without verify-full",
            f"The connection is encrypted with sslmode={sslmode}, which does not verify the server's host name.",
            "Connect with sslmode=verify-full and a trusted root certificate (sslrootcert).",
        )]  # fmt: skip
    return []


def _server_findings(facts: PostgresDiagnosis, *, read_only: bool) -> list[Finding]:
    findings: list[Finding] = []
    if facts.server_version < _MIN_SERVER_VERSION:
        findings.append(_finding(
            "TAB-PG-008", Severity.WARNING, "Old PostgreSQL server",
            f"The server runs PostgreSQL {facts.server_version // 10_000}; Tabrion supports 16 and later.",
            "Upgrade the server to PostgreSQL 16 or later.",
        ))  # fmt: skip
    if facts.archive_mode is None:
        findings.append(_not_checked("TAB-PG-020", "archive_mode (pg_settings)"))
    elif facts.archive_mode == "off":
        findings.append(_finding(
            "TAB-PG-020", Severity.INFO, "WAL archiving is off",
            "archive_mode is off: point-in-time recovery is not possible.",
            "Enable archive_mode with an archive_command or a backup tool such as pgBackRest, if you need PITR.",
        ))  # fmt: skip
    if facts.in_recovery and not read_only:
        findings.append(_finding(
            "TAB-PG-021", Severity.WARNING, "Standby with a write session",
            "The server is a standby (in recovery), but the session is not read-only; every write will fail.",
            "Connect to the primary for writes, or use a read-only profile (default_transaction_read_only=on).",
        ))  # fmt: skip
    return findings
