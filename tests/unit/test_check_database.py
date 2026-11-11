"""``tabrion.check.check`` for a ``tabrion.Database`` on SQLite (TODO TB-060, contract I-55).

TAB-T-K25, K26 and the layer-B part of K27..K30; the PostgreSQL part (K21 and the same codes there) is
``tests/postgres/test_check_layer_b.py``.
"""

from __future__ import annotations

from collections.abc import Iterator
import hashlib
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

pytest.importorskip("sqlalchemy")

from tabrion import Database, Profile
import tabrion._sa._check as sa_check
from tabrion.check import Finding, Severity, check
from tabrion.errors import UsageError
from tabrion.postgres import SessionSettings


def codes(findings: list[Finding]) -> list[str]:
    return sorted(finding.code for finding in findings)


@pytest.fixture
def path(tmp_path: Path) -> Path:
    return tmp_path / "app.db"


@pytest.fixture
def db(path: Path) -> Iterator[Database]:
    database = Database.from_url(f"sqlite:///{path}")
    try:
        yield database
    finally:
        database.close()


def test_clean_database_has_no_findings(db: Database) -> None:
    assert db.check() == [] and check(db) == []


def test_dialect_findings_come_from_the_raw_connection(db: Database, path: Path) -> None:
    """The SQLite checks run on the pooled ``sqlite3.Connection``: the same findings as a raw ``check``."""
    db.execute("CREATE TABLE t (x)")
    with db.read() as transaction:
        transaction.connection.exec_driver_sql("PRAGMA trusted_schema = ON")  # session state, read on that connection
        raw = check(transaction.connection.connection.dbapi_connection)  # type: ignore[arg-type]
    assert "TAB-SQ-009" in codes(raw)
    assert codes(db.check()) == codes(raw)  # the pool hands out the same idle connection again


def test_k25_pool_without_pre_ping(path: Path) -> None:
    with Database.from_url(f"sqlite:///{path}", profile=Profile(pool_pre_ping=False)) as database:
        (finding,) = database.check()
    assert finding.code == "TAB-B-001" and finding.severity is Severity.INFO
    assert "pre_ping" in finding.title


def test_k25_negative_default_and_in_memory(path: Path) -> None:
    with Database.from_url(f"sqlite:///{path}") as database:
        assert "TAB-B-001" not in codes(database.check())
    with Database.from_url("sqlite://", profile=Profile(pool_pre_ping=False)) as memory:
        assert memory.check() == []  # one shared connection that is never stale


def test_k26_version_table_is_not_checked(db: Database) -> None:
    db.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)")
    (finding,) = db.check()
    assert finding.code == "TAB-B-002" and finding.severity is Severity.INFO and finding.title == "Not checked"
    assert "require_current" in finding.remedy


def test_k26_negative_without_version_table(db: Database) -> None:
    db.execute("CREATE TABLE app_version (version_num VARCHAR(32))")
    assert "TAB-B-002" not in codes(db.check())


def test_k27_expect_is_passed_through(db: Database) -> None:
    assert db.check() == check(db, expect="owner") == check(db, expect="any")
    with pytest.raises(ValueError):
        check(db, expect="admin")  # type: ignore[arg-type]


@pytest.mark.security
def test_k30_read_only_and_works_under_readonly_profile(path: Path) -> None:
    with Database.from_url(f"sqlite:///{path}") as database:
        database.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    with Database.from_url(f"sqlite:///{path}", profile=Profile.readonly()) as database:
        assert codes(database.check()) == ["TAB-B-002"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    raw = sqlite3.connect(path)
    try:
        assert raw.execute("SELECT count(*) FROM alembic_version").fetchone() == (0,)
    finally:
        raw.close()


def test_k29_closed_database_raises(path: Path) -> None:
    database = Database.from_url(f"sqlite:///{path}")
    database.close()
    with pytest.raises(UsageError):
        database.check()


@pytest.mark.parametrize(
    ("wanted", "server_version", "reported"),
    [
        (60_000, 160_009, True),
        (60_000, 169_999, True),
        (60_000, 170_000, False),  # PostgreSQL 17 applies transaction_timeout: nothing to report
        (60_000, 180_001, False),
        (None, 160_009, False),
        (0, 160_009, False),
    ],
)
def test_k21_version_gate(wanted: int | None, server_version: int, reported: bool) -> None:
    """TAB-PG-012's gate on a stub connection: CI runs PostgreSQL 16 only (review L-1 of #17, mutant C4)."""
    target = SimpleNamespace(_profile=Profile(postgres=SessionSettings(transaction_timeout_ms=wanted)))
    dbapi = SimpleNamespace(info=SimpleNamespace(server_version=server_version))
    findings = sa_check._transaction_timeout(target, dbapi)  # type: ignore[arg-type]
    assert [finding.code for finding in findings] == (["TAB-PG-012"] if reported else [])
