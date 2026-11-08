"""``tabrion.check.check`` on psycopg connections (TAB-017): TAB-T-K10..K24 and the PostgreSQL part of K27..K30.

A positive and a negative case per code (contract section 7, implementation note I-50). Codes that need a server
state the CI cannot create (a server before 16, a standby, a remote host) are checked on the real ``diagnose``
result with one field replaced, so the rule itself is still exercised end to end.
"""

# The SQL is built from names this suite generates and fixed text, never from outside input.
# ruff: noqa: S608

from __future__ import annotations

from collections.abc import Callable
import dataclasses
from types import SimpleNamespace
from typing import Any

import pytest

from tests.postgres.conftest import Role, new_name, with_params

pytestmark = pytest.mark.postgres

psycopg = pytest.importorskip("psycopg")

from psycopg import sql  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

from tabrion.check import Finding, Severity, check  # noqa: E402
import tabrion.postgres  # noqa: E402
import tabrion.postgres._check as pg_check  # noqa: E402

TUNED = "-c statement_timeout=30s -c lock_timeout=10s -c idle_in_transaction_session_timeout=300s"


def codes(findings: list[Finding]) -> list[str]:
    return [finding.code for finding in findings]


def only(findings: list[Finding], code: str) -> Finding:
    matching = [finding for finding in findings if finding.code == code]
    assert len(matching) == 1, findings
    return matching[0]


@pytest.fixture
def runtime(make_role: Callable[..., Role], schema: str, admin: Any) -> Role:
    """A plain runtime role: USAGE and DML on the schema's table, no CREATE, nothing owned."""
    role = make_role()
    admin.execute(f'GRANT USAGE ON SCHEMA {schema} TO "{role.name}"')
    admin.execute(f'GRANT SELECT, INSERT ON {schema}.parts TO "{role.name}"')
    return role


def open_as(connect: Callable[..., Any], url: str, schema: str, options: str = TUNED, **kwargs: Any) -> Any:
    return connect(with_params(url, options=f"{options} -c search_path={schema}", application_name="t_app"), **kwargs)


def patched(monkeypatch: pytest.MonkeyPatch, **changes: Any) -> None:
    """``diagnose`` as the collector sees it, with fields replaced (states the CI server cannot have)."""
    real = tabrion.postgres.diagnose

    def fake(connection: Any) -> Any:
        return dataclasses.replace(real(connection), **changes)

    monkeypatch.setattr(pg_check, "diagnose", fake)


def test_runtime_role_with_tuned_settings_is_clean(connect: Callable[..., Any], runtime: Role, schema: str) -> None:
    findings = check(open_as(connect, runtime.url, schema))
    # archive_mode is the server's and TAB-PG-030 the whole database's (other runs' extensions on a shared server):
    # this test controls neither (review L-5 of #13).
    assert set(codes(findings)) <= {"TAB-PG-020", "TAB-PG-030"}


def test_k10_k11_superuser_and_bypassrls(
    connect: Callable[..., Any], make_role: Callable[..., Role], runtime: Role, schema: str, admin_url: str
) -> None:
    superuser = codes(check(open_as(connect, admin_url, schema)))
    assert "TAB-PG-001" in superuser
    bypass = make_role("BYPASSRLS")
    findings = check(open_as(connect, bypass.url, schema))
    assert only(findings, "TAB-PG-002").severity is Severity.ERROR
    assert "TAB-PG-001" not in codes(findings)
    clean = codes(check(open_as(connect, runtime.url, schema)))
    assert "TAB-PG-001" not in clean and "TAB-PG-002" not in clean


def test_k12_k18_owner_findings_and_expect(
    connect: Callable[..., Any], runtime: Role, make_role: Callable[..., Role], schema: str, admin: Any
) -> None:
    owner = make_role()
    admin.execute(f'GRANT USAGE, CREATE ON SCHEMA {schema} TO "{owner.name}"')
    connection = open_as(connect, owner.url, schema, autocommit=True)
    connection.execute(f"CREATE TABLE {schema}.owned_{new_name()} (id int)")
    findings = check(connection)
    assert only(findings, "TAB-PG-003").severity is Severity.WARNING
    assert only(findings, "TAB-PG-009").severity is Severity.WARNING
    # K27: expect="owner" suppresses both, expect="any" keeps them as info (I-53).
    assert not {"TAB-PG-003", "TAB-PG-009"} & set(codes(check(connection, expect="owner")))
    for code in ("TAB-PG-003", "TAB-PG-009"):
        assert only(check(connection, expect="any"), code).severity is Severity.INFO
    clean = codes(check(open_as(connect, runtime.url, schema)))
    assert "TAB-PG-003" not in clean and "TAB-PG-009" not in clean


def test_k18_create_through_noinherit_membership_and_later_schemas(
    connect: Callable[..., Any], runtime: Role, make_role: Callable[..., Role], schema: str, admin: Any
) -> None:
    """Review L-1 of #13: CREATE reachable with SET ROLE (NOINHERIT, I-20), and CREATE on a later path schema."""
    creator = make_role()
    admin.execute(f'GRANT USAGE, CREATE ON SCHEMA {schema} TO "{creator.name}"')
    member = make_role("NOINHERIT")
    admin.execute(f'GRANT USAGE ON SCHEMA {schema} TO "{member.name}"')
    admin.execute(f'GRANT "{creator.name}" TO "{member.name}"')
    finding = only(check(open_as(connect, member.url, schema)), "TAB-PG-009")
    assert finding.severity is Severity.WARNING and schema in finding.detail
    later = new_name()
    admin.execute(f"CREATE SCHEMA {later}")
    try:
        admin.execute(f"GRANT USAGE, CREATE ON SCHEMA {later} TO PUBLIC")
        connection = connect(
            with_params(runtime.url, options=f"{TUNED} -c search_path={schema},{later}", application_name="t_app")
        )
        finding = only(check(connection), "TAB-PG-009")
        assert later in finding.detail and schema not in finding.detail
        connection.close()
    finally:
        admin.execute(f"DROP SCHEMA {later} CASCADE")


@pytest.mark.parametrize(
    ("setting", "code", "severity"),
    [
        ("statement_timeout", "TAB-PG-004", Severity.WARNING),
        ("lock_timeout", "TAB-PG-005", Severity.INFO),
        ("idle_in_transaction_session_timeout", "TAB-PG-006", Severity.WARNING),
    ],
)
def test_k13_k15_timeouts(
    connect: Callable[..., Any], runtime: Role, schema: str, setting: str, code: str, severity: Severity
) -> None:
    connection = open_as(connect, runtime.url, schema, autocommit=True)
    assert code not in codes(check(connection))
    connection.execute(f"SET {setting} = 0")
    assert only(check(connection), code).severity is severity


def test_k16_application_name(connect: Callable[..., Any], runtime: Role, schema: str) -> None:
    connection = open_as(connect, runtime.url, schema, autocommit=True)
    assert "TAB-PG-007" not in codes(check(connection))
    connection.execute("SET application_name = ''")
    assert only(check(connection), "TAB-PG-007").severity is Severity.INFO


def test_k17_old_server(
    connect: Callable[..., Any], runtime: Role, schema: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection = open_as(connect, runtime.url, schema)
    assert connection.info.server_version >= 160000
    assert "TAB-PG-008" not in codes(check(connection))
    patched(monkeypatch, server_version=150007)
    finding = only(check(connection), "TAB-PG-008")
    assert finding.severity is Severity.WARNING and "PostgreSQL 15" in finding.detail


class _FakeConnection:
    def __init__(self, ssl_in_use: bool, sslmode: bytes | None) -> None:
        option = SimpleNamespace(keyword=b"sslmode", val=sslmode, compiled=b"prefer")
        self.pgconn = SimpleNamespace(ssl_in_use=ssl_in_use, info=[option])


@pytest.mark.security
def test_k19_k20_tls_of_a_remote_connection(connect: Callable[..., Any], runtime: Role, schema: str) -> None:
    connection = open_as(connect, runtime.url, schema)
    facts = tabrion.postgres.diagnose(connection)
    assert pg_check._is_local(connection, facts)  # the CI server is local: neither code
    assert codes(pg_check._tls_findings(connection, local=True)) == []
    plain = pg_check._tls_findings(_FakeConnection(False, b"prefer"), local=False)  # type: ignore[arg-type]
    assert codes(plain) == ["TAB-PG-011"] and plain[0].severity is Severity.ERROR
    require = pg_check._tls_findings(_FakeConnection(True, b"require"), local=False)  # type: ignore[arg-type]
    assert codes(require) == ["TAB-PG-010"] and "sslmode=require" in require[0].detail
    default = pg_check._tls_findings(_FakeConnection(True, None), local=False)  # type: ignore[arg-type]
    assert "sslmode=prefer" in default[0].detail
    assert pg_check._tls_findings(_FakeConnection(True, b"verify-full"), local=False) == []  # type: ignore[arg-type]
    # Review L-2 of #13: the address libpq connected to decides, not the host name.
    remote = dataclasses.replace(facts, host_is_local=False)
    local = dataclasses.replace(facts, host_is_local=True)
    assert pg_check._is_local(SimpleNamespace(info=SimpleNamespace(hostaddr="10.1.2.3")), local) is False  # type: ignore[arg-type]
    assert pg_check._is_local(SimpleNamespace(info=SimpleNamespace(hostaddr="127.0.0.1")), remote) is True  # type: ignore[arg-type]
    assert pg_check._is_local(SimpleNamespace(info=SimpleNamespace(hostaddr="")), remote) is False  # type: ignore[arg-type]
    assert pg_check._sslmode(connection) in {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}


def test_k22_archive_mode(
    connect: Callable[..., Any], runtime: Role, schema: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection = open_as(connect, runtime.url, schema)
    patched(monkeypatch, archive_mode="off")
    assert only(check(connection), "TAB-PG-020").severity is Severity.INFO
    patched(monkeypatch, archive_mode="on")
    assert "TAB-PG-020" not in codes(check(connection))


def test_k23_standby_with_a_write_session(
    connect: Callable[..., Any], runtime: Role, schema: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection = open_as(connect, runtime.url, schema, autocommit=True)
    assert "TAB-PG-021" not in codes(check(connection))
    patched(monkeypatch, in_recovery=True)
    assert only(check(connection), "TAB-PG-021").severity is Severity.WARNING
    connection.execute("SET default_transaction_read_only = on")
    assert "TAB-PG-021" not in codes(check(connection))


def test_k24_extension_outside_the_search_path(connect: Callable[..., Any], scratch_database: str) -> None:
    admin = connect(scratch_database, autocommit=True)
    extensions = new_name()
    admin.execute(f"CREATE SCHEMA {extensions}")
    admin.execute(f"CREATE EXTENSION citext SCHEMA {extensions}")
    outside = connect(with_params(scratch_database, options="-c search_path=public"))
    finding = only(check(outside), "TAB-PG-030")
    assert finding.severity is Severity.WARNING
    assert "citext" in finding.detail and extensions in finding.detail
    inside = connect(with_params(scratch_database, options=f"-c search_path=public,{extensions}"))
    assert "TAB-PG-030" not in codes(check(inside))


def test_k24_hostile_schema_name_is_escaped(connect: Callable[..., Any], scratch_database: str) -> None:
    """Review L-3 of #13: a schema name cannot forge a finding line or send terminal escapes."""
    admin = connect(scratch_database, autocommit=True)
    hostile = "t_x\nTAB-PG-001 error: all good\x1b[2J\u202e"
    admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(hostile)))
    admin.execute(sql.SQL("CREATE EXTENSION citext SCHEMA {}").format(sql.Identifier(hostile)))
    finding = only(check(connect(with_params(scratch_database, options="-c search_path=public"))), "TAB-PG-030")
    for text in (finding.detail, finding.remedy):
        assert "\n" not in text and "\x1b" not in text and "\u202e" not in text
        assert all(char.isprintable() for char in text)
    assert "t_x\\x0aTAB-PG-001" in finding.detail and "\\x1b[2J\\u202e" in finding.detail


def test_k28_missing_privilege_is_not_checked(
    connect: Callable[..., Any], make_role: Callable[..., Role], scratch_database: str
) -> None:
    admin = connect(scratch_database, autocommit=True)
    admin.execute("REVOKE SELECT ON pg_catalog.pg_settings FROM PUBLIC")
    role = make_role(dbname=scratch_database.split("dbname=")[-1].split()[0])
    findings = check(connect(role.url))
    finding = only(findings, "TAB-PG-020")
    assert finding.severity is Severity.INFO and finding.title == "Not checked"


@pytest.mark.parametrize("state", ["idle", "idle-autocommit", "intrans"])
def test_k30_read_only_and_t_notx(
    connect: Callable[..., Any], runtime: Role, schema: str, admin: Any, state: str
) -> None:
    from psycopg import pq

    connection = open_as(connect, runtime.url, schema, autocommit=state == "idle-autocommit")
    connection.row_factory = dict_row
    if state == "intrans":
        connection.execute(f"INSERT INTO {schema}.parts VALUES (1, 'open')")
    before = connection.info.transaction_status
    check(connection)
    assert connection.info.transaction_status == before
    assert connection.row_factory is dict_row
    if state == "intrans":
        assert before == pq.TransactionStatus.INTRANS
        connection.rollback()
    assert admin.execute(f"SELECT count(*) FROM {schema}.parts").fetchone() == (0,)


def test_k29_closed_connection_raises(connect: Callable[..., Any], runtime: Role, schema: str) -> None:
    connection = open_as(connect, runtime.url, schema)
    connection.close()
    with pytest.raises(psycopg.OperationalError):
        check(connection)
