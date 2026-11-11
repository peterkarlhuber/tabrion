"""TAB-022a: Tabrion replays the consumers' SQLite, configuration and test-environment golden files (contract 8).

Like ``tests/postgres/test_golden_consumers.py``, without a server: each test runs a scenario of
``tests/golden/consumers/scenarios.py`` with the Tabrion call the contract table names and compares it with what the
consumer's own code did at the commit its golden file names. A difference the table lists is applied to the recorded
expectation explicitly, next to the test; everything else must be identical. ``test_every_category_a_b_row_has_a_
golden_record`` checks that every A/B row of contract section 8 has its records, each naming its commit.
"""

from __future__ import annotations

from collections.abc import Callable
import contextlib
import copy
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Any

import pytest

from tabrion.config import url_from_env
from tabrion.sqlite import (
    SQLiteProfile,
    backup,
    connect,
    diagnose,
    migrate,
    read_transaction,
    write_transaction,
)
from tests.golden.consumers import scenarios

ROOT = Path(__file__).resolve().parent.parent.parent
GOLDEN = ROOT / "tests" / "golden"
NODESTRO = scenarios.golden("nodestro")
NODESTRO_API = scenarios.golden("nodestro_api")
ORKEXA = scenarios.golden("orkexa")
POSIX = os.name == "posix"


def _opener(profile: SQLiteProfile) -> Callable[[Path], sqlite3.Connection]:
    return lambda path: connect(path, profile=profile)


def _tabrion_pragmas(recorded: dict[str, Any]) -> dict[str, Any]:
    """The documented changes of ``connect`` against both consumers (contract 8.2 and 8.3 rows ``SQLiteStore.__init__``
    and ``_storage.py``): ``foreign_keys`` on (F-01, plan 07 DI-01), ``trusted_schema`` off, new files 0600."""
    expected = dict(recorded)
    expected.update(foreign_keys=1, trusted_schema=0)
    expected["file_mode"] = "0o600" if POSIX else None
    return expected


# --- 8.2 Nodestro -----------------------------------------------------------------------------------------------------


def test_nodestro_open(tmp_path: Path) -> None:
    """``SQLiteStore.__init__``: ``connect(path, profile=SQLiteProfile.nodestro())`` (``secure_delete`` off)."""
    replay = scenarios.sqlite_open_scenario(tmp_path, _opener(SQLiteProfile.nodestro()))
    assert NODESTRO["sqlite.open"]["file_mode"] == "0o644"  # what Nodestro does today: umask, not 0600
    assert replay == _tabrion_pragmas(NODESTRO["sqlite.open"])


@pytest.mark.skipif(not POSIX, reason="symbolic links (POSIX)")
def test_nodestro_symlinks_are_refused_now(tmp_path: Path) -> None:
    """New for Nodestro (contract 8.2): a database path that is a link is refused, and the link's target stays as
    it was; Nodestro opened the link and wrote through it."""
    recorded = NODESTRO["sqlite.symlink"]
    assert recorded["database_is_link"] == "opened" and recorded["target_unchanged"] is False
    replay = scenarios.sqlite_symlink_scenario(tmp_path, _opener(SQLiteProfile.nodestro()))
    assert replay == {"database_is_link": "refused", "wal_is_link": "refused", "target_unchanged": True}


def test_nodestro_transaction(tmp_path: Path) -> None:
    """``SQLiteStore._transaction`` (outermost level): ``write_transaction``."""
    replay = scenarios.sqlite_transaction_scenario(tmp_path, _opener(SQLiteProfile.nodestro()), write_transaction)
    assert replay == NODESTRO["sqlite.transaction"]


def test_nodestro_migrate(tmp_path: Path) -> None:
    """``SQLiteStore.migrate``: ``tabrion.sqlite.migrate``; the codes are the same after the prefix (``storage.`` in
    Nodestro, ``sqlite.`` in Tabrion), the version, the backup calls and the rollback too."""

    def run(conn: sqlite3.Connection, migrations: Any, target: int, backup_: Any) -> int:
        return migrate(conn, migrations, target=target, backup=backup_).previous

    replay = scenarios.sqlite_migrate_scenario(tmp_path, _opener(SQLiteProfile.nodestro()), run)
    assert replay == NODESTRO["sqlite.migrate"]


def test_nodestro_snapshot(tmp_path: Path) -> None:
    """``SQLiteStore.snapshot``: ``backup(overwrite=True)`` (0600, atomic, ``quick_check``)."""
    replay = scenarios.sqlite_backup_scenario(
        tmp_path, _opener(SQLiteProfile.nodestro()), lambda conn, target: backup(conn, target, overwrite=True)
    )
    assert replay == NODESTRO["sqlite.snapshot"]


def test_nodestro_api_database_check(tmp_path: Path) -> None:
    """nodestro-api ``diagnostics._database``: ``diagnose(run_quick_check=True)``; the FTS5 check stays the API's.
    The codes map as the API maps them (a file that cannot be opened is ``unreadable``, a ``quick_check`` other
    than ``ok`` is ``damaged``), with one difference (D-75): the API's own ``quick_check`` raises on a damaged file,
    so it reports ``unreadable``; ``diagnose`` reports the failed check (contract I-29), so the file is ``damaged``,
    which is what it is."""

    def check(path: Path) -> dict[str, Any]:
        try:
            conn = connect(path, profile=SQLiteProfile.nodestro(), create=False)
            try:
                found = diagnose(conn, run_quick_check=True)
            finally:
                conn.close()
        except sqlite3.Error:
            return {"status": "error", "code": "unreadable"}
        if found.quick_check != "ok":
            return {"status": "error", "code": "damaged"}
        with contextlib.closing(sqlite3.connect(":memory:")) as probe:  # never left to the garbage collector
            options = {row[0] for row in probe.execute("PRAGMA compile_options")}
        if "ENABLE_FTS5" not in options:
            return {"status": "warning", "code": "no_fts"}
        return {"status": "ok", "code": "ok"}

    expected = copy.deepcopy(NODESTRO_API["diagnostics.database"])
    assert expected["damaged"] == {"status": "error", "code": "unreadable"}
    expected["damaged"]["code"] = "damaged"
    assert scenarios.sqlite_check_scenario(tmp_path, check) == expected


def test_nodestro_url_from_env(tmp_path: Path) -> None:
    """``server_settings.py`` (``*_FILE``): ``url_from_env``, the same rule (both forms refused, an unreadable or
    empty file refused, an empty value is missing) with one difference (deviation D-76): Nodestro strips white
    space around the value and the file's content, ``url_from_env`` removes only the file's trailing newline. A
    connection string with spaces around it still works for libpq, so the wrapper strips at adoption."""
    replay = scenarios.url_file_scenario(
        tmp_path, lambda environ: url_from_env("NODESTRO_DATABASE_URL", environ=environ), "NODESTRO_DATABASE_URL"
    )
    expected = dict(NODESTRO["url_from_env"])
    assert expected["direct"] == expected["file"] == "postgresql://u:pw@h/db"
    expected["direct"] = " postgresql://u:pw@h/db "  # the value as given
    expected["file"] = "  postgresql://u:pw@h/db"  # the file without its trailing newline
    assert replay == expected


# --- 8.3 Orkexa -------------------------------------------------------------------------------------------------------


def test_orkexa_open(tmp_path: Path) -> None:
    """``_storage.connect_private_database``: ``connect(path, profile=SQLiteProfile.orkexa())``."""
    replay = scenarios.sqlite_open_scenario(tmp_path, _opener(SQLiteProfile.orkexa()))
    # The golden file is recorded on Linux (scripts/record_golden.py in CI), so it holds Orkexa's POSIX mode on every
    # platform: Orkexa already makes the file private.
    assert ORKEXA["sqlite.open"]["file_mode"] == "0o600"
    # The replay runs here: 0600 on POSIX; Windows has no POSIX mode bits, and the scenario records None (issue #18).
    assert replay["file_mode"] == ("0o600" if POSIX else None)
    assert replay == _tabrion_pragmas(ORKEXA["sqlite.open"])


@pytest.mark.skipif(not POSIX, reason="symbolic links (POSIX)")
def test_orkexa_symlinks(tmp_path: Path) -> None:
    """``check_database_path``: ``check_path`` (inside ``connect``), the same refusals."""
    assert scenarios.sqlite_symlink_scenario(tmp_path, _opener(SQLiteProfile.orkexa())) == ORKEXA["sqlite.symlink"]


@pytest.mark.parametrize("store", ["files", "memory", "index"])
def test_orkexa_write_transactions(tmp_path: Path, store: str) -> None:
    """The three ``BEGIN IMMEDIATE`` helpers (``files.py``, ``memory.py``, ``index.py _write``):
    ``write_transaction``."""
    replay = scenarios.sqlite_transaction_scenario(tmp_path, _opener(SQLiteProfile.orkexa()), write_transaction)
    assert replay == ORKEXA[f"sqlite.transaction.{store}"]


def test_orkexa_read_transaction(tmp_path: Path) -> None:
    """``index.py _read``: ``read_transaction`` (one snapshot; ``query_only`` for the block, restored after)."""
    replay = scenarios.sqlite_read_scenario(tmp_path, _opener(SQLiteProfile.orkexa()), read_transaction)
    assert replay == ORKEXA["sqlite.read.index"]


# --- Test environments (8.2 and 8.3, category A rows) -----------------------------------------------------------------


def _plugin_names(prefix: str) -> dict[str, list[str]]:
    """The variables ``tabrion.testing.configure(env_prefix=prefix)`` reads, by role."""
    from tabrion.testing import _config, configure

    saved = _config.current
    try:
        configure(env_prefix=prefix)
        settings = _config.current
        return {
            "url": list(settings.postgres_env),
            "require": list(settings.require_env),
            "runtime": list(settings.runtime_role_env),
            "extensions": list(settings.require_extensions_env),
        }
    finally:
        _config.current = saved


@pytest.mark.parametrize(("consumer", "prefix"), [("nodestro", "NODESTRO"), ("orkexa", "ORKEXA")])
def test_testing_environment_names(consumer: str, prefix: str) -> None:
    """``configure(env_prefix=...)`` reads the consumer's URL, require and runtime-role variables. The pgvector
    switch differs (finding TB-072): both consumers have ``<PREFIX>_REQUIRE_PGVECTOR=1`` as a flag, Tabrion a list
    of required extensions (``<PREFIX>_REQUIRE_EXTENSIONS=vector``), so the switch is renamed at adoption."""
    recorded = scenarios.golden(consumer)["testing.environment"]
    names = _plugin_names(prefix)
    assert f"{prefix}_TEST_POSTGRES" in recorded["read"] and f"{prefix}_TEST_POSTGRES" in names["url"]
    for flag in (f"{prefix}_REQUIRE_POSTGRES", f"{prefix}_TEST_RUNTIME_ROLE"):
        assert flag in recorded["flag_equal_1"]
    assert f"{prefix}_REQUIRE_POSTGRES" in names["require"]
    assert f"{prefix}_TEST_RUNTIME_ROLE" in names["runtime"]
    assert f"{prefix}_REQUIRE_PGVECTOR" in recorded["flag_equal_1"]
    assert f"{prefix}_REQUIRE_PGVECTOR" not in [name for group in names.values() for name in group]
    assert names["extensions"] == [f"{prefix}_REQUIRE_EXTENSIONS"]
    assert set(recorded["read"]) == {
        f"{prefix}_TEST_POSTGRES",
        f"{prefix}_REQUIRE_POSTGRES",
        f"{prefix}_REQUIRE_PGVECTOR",
        f"{prefix}_TEST_RUNTIME_ROLE",
    }


# --- Coverage: every category A/B row of contract section 8 has a golden record ---------------------------------------

#: Each A/B row of contract section 8 (its first cell, verbatim) and where its recorded behavior lives:
#: ``(file, key)``; the key ``*`` is the whole file.
COVERAGE: dict[str, list[tuple[str, str]]] = {
    # 8.1 Axentra
    "`Database._configure` (`database.py:273-275`)": [
        ("axentra.json", "configure.statement_timeout_ms=5000"),
        ("axentra.json", "configure.statement_timeout_ms=0"),
    ],
    "`Database.ping` (`database.py:348-357`)": [("axentra.json", "ping")],
    "`Database.role_info`/`check_role` (`database.py:359-405`)": [("axentra.json", "role_info.check_role")],
    "`roles.inspect_role`, `RoleReport` (`roles.py:30-104`)": [("axentra.json", "inspect_role")],
    "`roles.grant_runtime_role` (`roles.py:107-190`)": [("role_grants.json", "axentra")],
    "`migrations.split_statements` (`migrations.py:125-171`)": [("axentra_splitter.json", "*")],
    "`migrations._wait_for_lock` (`migrations.py:309-318`)": [("axentra.json", "wait_for_lock")],
    "Worker lock (own connection, `events/worker.py`)": [("axentra.json", "leader_lock")],
    "16 typed and 4 generic `except psycopg...`": [("axentra.json", "safe_message")],
    "`tests/conftest.py` (throwaway database, roles without superuser, truncate)": [("axentra_conftest.json", "*")],
    # 8.2 Nodestro
    "`SQLiteStore.__init__` (`storage.py:271-286`)": [
        ("nodestro.json", "sqlite.open"),
        ("nodestro.json", "sqlite.symlink"),
    ],
    "`SQLiteStore._transaction` (`storage.py:313-332`)": [("nodestro.json", "sqlite.transaction")],
    "`SQLiteStore.migrate` (`storage.py:563-637`)": [("nodestro.json", "sqlite.migrate")],
    "`SQLiteStore.snapshot` (`storage.py:649-658`)": [("nodestro.json", "sqlite.snapshot")],
    "nodestro-api `diagnostics.py:110-133`": [("nodestro_api.json", "diagnostics.database")],
    "`PostgresDatabase._configure` (`storage_postgres.py:275-286`)": [
        ("nodestro.json", "configure.schema=None"),
        ("nodestro.json", "configure.schema=nodestro"),
    ],
    "`try_/holds_/release_session_lock` (`:418-481`)": [("nodestro.json", "session_lock")],
    "`role_info`, `inspect_role`, `grant_runtime_role` (`:679-689`, `:808-904`)": [
        ("nodestro.json", "role_info"),
        ("nodestro.json", "inspect_role"),
        ("role_grants.json", "nodestro"),
    ],
    "`pgvector_status` (`:907-968`)": [("nodestro.json", "pgvector_status")],
    "`server_settings.py:115-141` (`*_FILE`)": [("nodestro.json", "url_from_env")],
    "`tests/postgres_support.py` and 13 local fixtures": [("nodestro.json", "testing.environment")],
    # 8.3 Orkexa
    "`backends/postgres/connection.py` `PsycopgSource` (no timeouts, no ping)": [("orkexa.json", "source")],
    "`tests/backends/postgres/support.py`": [("orkexa.json", "testing.environment")],
    "`_storage.py:80-166`": [("orkexa.json", "sqlite.open"), ("orkexa.json", "sqlite.symlink")],
    "SQLite transactions x3 (`files.py:85-94`, `memory.py:84-92`, `index.py:473-500`)": [
        ("orkexa.json", "sqlite.transaction.files"),
        ("orkexa.json", "sqlite.transaction.memory"),
        ("orkexa.json", "sqlite.transaction.index"),
        ("orkexa.json", "sqlite.read.index"),
    ],
}

#: The keys that name the consumer commit of each golden file.
_COMMIT_KEYS = ("commit", "axentra_commit", "nodestro_commit")


def _ab_rows() -> list[str]:
    text = (ROOT / "docs" / "api" / "contract.md").read_text(encoding="utf-8")
    section = text[text.index("## 8. Compatibility table") : text.index("## 9. ")]
    rows = []
    for line in section.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) >= 2 and cells[1] in ("A", "B"):
            rows.append(cells[0])
    return rows


def test_every_category_a_b_row_has_a_golden_record() -> None:
    rows = _ab_rows()
    assert len(rows) == 25  # 10 Axentra, 11 Nodestro (with nodestro-api), 4 Orkexa
    assert sorted(rows) == sorted(COVERAGE), set(rows) ^ set(COVERAGE)
    for row, places in COVERAGE.items():
        for file, key in places:
            data = json.loads((GOLDEN / file).read_text(encoding="utf-8"))
            assert any(re.fullmatch(r"[0-9a-f]{40}", str(data.get(k, ""))) for k in _COMMIT_KEYS), (row, file)
            if key != "*":
                records = data.get("records", data)
                assert key in records, (row, file, key)


def test_consumer_golden_files_name_full_commits() -> None:
    """The files ``scripts/record_golden.py`` writes name the full SHA of the consumer and the versions used."""
    for consumer in ("axentra", "nodestro", "nodestro_api", "orkexa"):
        data = json.loads((GOLDEN / f"{consumer}.json").read_text(encoding="utf-8"))
        assert re.fullmatch(r"[0-9a-f]{40}", data["commit"]), consumer
        assert data["repository"].startswith("https://github.com/peterkarlhuber/")
        assert {"python", "sqlite"} <= set(data["recorded_with"])


def test_recorder_refuses_an_environment_with_tabrion(monkeypatch: pytest.MonkeyPatch) -> None:
    """The recorders run the consumer's code without Tabrion: in Tabrion's own environment they refuse."""
    from tests.golden.consumers._recorder import run

    monkeypatch.setenv("TABRION_GOLDEN_ADMIN_URL", "unused")
    monkeypatch.setenv("TABRION_GOLDEN_PREFIX", "unused")
    with pytest.raises(SystemExit, match="without Tabrion"):
        run(lambda *_: {})


def test_golden_files_hold_no_secret() -> None:
    """No password or generated name of the recording server is in a consumer golden file."""
    for consumer in ("axentra", "nodestro", "nodestro_api", "orkexa"):
        text = (GOLDEN / f"{consumer}.json").read_text(encoding="utf-8")
        assert not re.search(r"t_[0-9a-f]{6}_rec_", text), consumer
        assert "password=" not in text, consumer
