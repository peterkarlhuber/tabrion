"""``tabrion.check.check`` on SQLite connections (TAB-017): TAB-T-K01..K09 and the SQLite part of K27..K30.

Each code has a positive and a negative case (contract section 7); the test numbers map to the codes as in contract
implementation note I-50. Standard library and pytest only (the platforms workflow runs this directory too).
"""

from __future__ import annotations

from collections.abc import Iterator
import os
from pathlib import Path
import sqlite3

import pytest

import tabrion.check
from tabrion.check import Finding, Severity, check
import tabrion.sqlite
from tabrion.sqlite import JournalMode, SQLiteProfile
from tests.sqlite.conftest import posix_only


@pytest.fixture
def path(tmp_path: Path) -> Path:
    return tmp_path / "findings.db"


@pytest.fixture
def good(path: Path) -> Iterator[sqlite3.Connection]:
    """A connection under the default profile: no finding at all."""
    connection = tabrion.sqlite.connect(path)
    connection.execute("CREATE TABLE t (a INTEGER)")
    try:
        yield connection
    finally:
        connection.close()


def codes(findings: list[Finding]) -> list[str]:
    return [finding.code for finding in findings]


def only(findings: list[Finding], code: str) -> Finding:
    matching = [finding for finding in findings if finding.code == code]
    assert len(matching) == 1, findings
    return matching[0]


def test_default_profile_has_no_findings(good: sqlite3.Connection) -> None:
    assert check(good) == []


def test_k01_foreign_keys_off(good: sqlite3.Connection) -> None:
    good.execute("PRAGMA foreign_keys = OFF")
    finding = only(check(good), "TAB-SQ-001")
    assert finding.severity is Severity.WARNING
    good.execute("PRAGMA foreign_keys = ON")
    assert "TAB-SQ-001" not in codes(check(good))


def test_k02_journal_mode_not_wal(path: Path) -> None:
    profile = SQLiteProfile(journal_mode=JournalMode.DELETE)
    connection = tabrion.sqlite.connect(path, profile=profile)
    try:
        assert only(check(connection), "TAB-SQ-002").severity is Severity.INFO
    finally:
        connection.close()
    memory = tabrion.sqlite.connect(":memory:")
    try:
        assert check(memory) == []  # no file: no journal-mode or file findings
    finally:
        memory.close()


@posix_only
def test_k03_file_readable_by_others(good: sqlite3.Connection, path: Path) -> None:
    os.chmod(path, 0o644)
    finding = only(check(good), "TAB-SQ-003")
    assert finding.severity is Severity.ERROR
    assert str(path) not in finding.detail and path.name not in finding.detail
    os.chmod(path, 0o600)
    assert "TAB-SQ-003" not in codes(check(good))
    wal = Path(str(path) + "-wal")
    assert wal.exists()
    os.chmod(wal, 0o640)
    assert "the -wal file" in only(check(good), "TAB-SQ-003").detail


@pytest.mark.security
def test_k04_symlinked_side_file(good: sqlite3.Connection, path: Path, symlinks: None) -> None:
    assert "TAB-SQ-004" not in codes(check(good))
    journal = Path(str(path) + "-journal")
    journal.symlink_to(path.parent / "elsewhere")
    finding = only(check(good), "TAB-SQ-004")
    assert finding.severity is Severity.ERROR
    assert "-journal" in finding.detail and str(path.parent) not in finding.detail


def test_k05_busy_timeout(good: sqlite3.Connection) -> None:
    good.execute("PRAGMA busy_timeout = 999")
    assert only(check(good), "TAB-SQ-005").severity is Severity.WARNING
    good.execute("PRAGMA busy_timeout = 1000")
    assert "TAB-SQ-005" not in codes(check(good))


def test_k06_quick_check(tmp_path: Path) -> None:
    path = tmp_path / "damaged.db"
    raw = sqlite3.connect(path)
    raw.execute("CREATE TABLE t (a TEXT)")
    raw.execute("CREATE INDEX t_a ON t (a)")
    raw.executemany("INSERT INTO t VALUES (?)", [("x" * 100 + str(i),) for i in range(2000)])
    raw.commit()
    assert "TAB-SQ-006" not in codes(check(raw))
    raw.close()
    with path.open("r+b") as handle:
        handle.seek(4096 * 20 + 100)
        handle.write(b"\xff" * 2000)
    damaged = sqlite3.connect(path)
    try:
        assert only(check(damaged), "TAB-SQ-006").severity is Severity.ERROR
    finally:
        damaged.close()


def test_k07_sqlite_version(good: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    assert sqlite3.sqlite_version_info >= (3, 37, 0)
    assert "TAB-SQ-007" not in codes(check(good))
    monkeypatch.setattr(tabrion.check, "_sqlite_version", lambda: (3, 36, 9))
    finding = only(check(good), "TAB-SQ-007")
    assert finding.severity is Severity.WARNING and "3.36.9" in finding.detail


def test_k08_large_wal(good: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    good.execute("PRAGMA wal_autocheckpoint = 0")
    good.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(5000)])
    assert "TAB-SQ-008" not in codes(check(good))
    monkeypatch.setattr(tabrion.check, "_MAX_WAL_BYTES", 4096)
    assert only(check(good), "TAB-SQ-008").severity is Severity.WARNING


def test_k09_trusted_schema(good: sqlite3.Connection) -> None:
    good.execute("PRAGMA trusted_schema = ON")
    assert only(check(good), "TAB-SQ-009").severity is Severity.INFO
    good.execute("PRAGMA trusted_schema = OFF")
    assert "TAB-SQ-009" not in codes(check(good))


def test_k27_expect_does_not_change_sqlite_findings(good: sqlite3.Connection) -> None:
    good.execute("PRAGMA foreign_keys = OFF")
    assert codes(check(good, expect="owner")) == codes(check(good, expect="any")) == ["TAB-SQ-001"]


def test_k28_unreadable_file_is_not_checked(good: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    real = os.lstat

    def denied(name: str, *args: object, **kwargs: object) -> os.stat_result:
        if str(name).endswith("findings.db"):
            raise PermissionError(13, "denied")
        return real(name)

    monkeypatch.setattr(os, "lstat", denied)
    finding = only(check(good), "TAB-SQ-003")
    assert finding.severity is Severity.INFO and finding.title == "Not checked"


def test_k29_dispatch_and_arguments(good: sqlite3.Connection) -> None:
    with pytest.raises(TypeError, match=r"expects a sqlite3\.Connection"):
        check(object())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="expect"):
        check(good, expect="admin")  # type: ignore[arg-type]


@pytest.mark.parametrize("legacy", [False, True])
def test_k30_read_only_and_t_notx(path: Path, legacy: bool) -> None:
    """The check writes nothing, ends no transaction, and leaves the row factory as it was."""
    setup = tabrion.sqlite.connect(path)
    setup.execute("CREATE TABLE w (a INTEGER)")
    setup.close()
    connection = sqlite3.connect(path, isolation_level="DEFERRED" if legacy else None)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = OFF")
        if not legacy:
            connection.execute("BEGIN")
        connection.execute("INSERT INTO w VALUES (1)")
        before = connection.in_transaction
        assert before
        check(connection)
        assert connection.in_transaction is before
        assert connection.row_factory is sqlite3.Row
        connection.rollback()
        assert connection.execute("SELECT count(*) FROM w").fetchone()[0] == 0
        check(connection)
        assert not connection.in_transaction
    finally:
        connection.close()
    read_only = tabrion.sqlite.connect(path, read_only=True)
    try:
        assert "TAB-SQ-006" not in codes(check(read_only))
    finally:
        read_only.close()
