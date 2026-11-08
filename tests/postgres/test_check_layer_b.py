"""``tabrion.check.check`` for a PostgreSQL ``tabrion.Database`` (TODO TB-060, contract I-55): TAB-T-K21 (TAB-PG-012),
K25 and K26 on PostgreSQL, and the dialect findings through the pooled psycopg connection.
"""

# The SQL is built from the schema fixture's generated name and fixed text, never from outside input.

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

pytestmark = pytest.mark.postgres

pytest.importorskip("sqlalchemy")
pytest.importorskip("psycopg")

from tabrion import Database, Profile  # noqa: E402
import tabrion._sa._check as sa_check  # noqa: E402
import tabrion.check  # noqa: E402
from tabrion.check import Finding, Severity  # noqa: E402
from tabrion.postgres import SessionSettings  # noqa: E402
from tests.postgres.test_database import sa_url  # noqa: E402

LAYER_B = {"TAB-PG-012", "TAB-B-001", "TAB-B-002"}


def layer_b(findings: list[Finding]) -> list[str]:
    return sorted(finding.code for finding in findings if finding.code in LAYER_B)


@pytest.fixture
def make_db(admin_url: str, schema: str) -> Iterator[Any]:
    opened: list[Database] = []

    def make(**fields: Any) -> Database:
        settings = fields.pop("postgres", SessionSettings(search_path=schema))
        database = Database.from_url(sa_url(admin_url), profile=Profile(postgres=settings, pool_size=1, **fields))
        opened.append(database)
        return database

    yield make
    for database in opened:
        database.close()


def test_dialect_findings_come_through(make_db: Any) -> None:
    findings = make_db().check()
    assert "TAB-PG-001" in {finding.code for finding in findings}  # the admin is a superuser
    assert layer_b(findings) == []


def test_k21_transaction_timeout_on_an_old_server(make_db: Any, schema: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sa_check, "_POSTGRES_17", 10**9)  # every server counts as older than 17
    database = make_db(postgres=SessionSettings(search_path=schema, transaction_timeout_ms=60_000))
    (finding,) = [f for f in database.check() if f.code == "TAB-PG-012"]
    assert finding.severity is Severity.INFO and "60000 ms" in finding.detail


def test_k21_negative(make_db: Any, schema: str) -> None:
    database = make_db()  # no transaction_timeout wanted
    assert layer_b(database.check()) == []
    wanted = make_db(postgres=SessionSettings(search_path=schema, transaction_timeout_ms=60_000))
    with wanted.read() as transaction:
        version = transaction.connection.connection.dbapi_connection.info.server_version
    expected = ["TAB-PG-012"] if version < 170000 else []
    assert layer_b(wanted.check()) == expected


def test_k25_pool_without_pre_ping(make_db: Any) -> None:
    assert layer_b(make_db(pool_pre_ping=False).check()) == ["TAB-B-001"]
    assert layer_b(make_db().check()) == []


def test_k26_version_table_in_the_search_path(make_db: Any, schema: str, admin: Any) -> None:
    database = make_db()
    assert layer_b(database.check()) == []
    admin.execute(f"CREATE TABLE {schema}.alembic_version (version_num varchar(32) PRIMARY KEY)")
    (finding,) = [f for f in database.check() if f.code == "TAB-B-002"]
    assert finding.title == "Not checked" and finding.severity is Severity.INFO


@pytest.mark.security
def test_k30_check_runs_in_a_read_only_transaction(make_db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    database = make_db()
    seen: list[str] = []
    real = tabrion.check.check

    def spy(target: Any, **kwargs: Any) -> list[Finding]:
        seen.append(target.execute("SHOW transaction_read_only").fetchone()[0])
        return real(target, **kwargs)

    monkeypatch.setattr(sa_check, "check", spy)
    database.check()
    assert seen == ["on"]
