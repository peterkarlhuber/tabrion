"""TAB-022a: Tabrion replays the consumers' PostgreSQL golden files (contract section 8, category A/B rows).

Every test runs a scenario of ``tests/golden/consumers/scenarios.py`` with Tabrion's adapter, the call the contract
table names, and compares the result with what the consumer's own code did at the commit its golden file names.
Where the table lists a difference ("Differences / adjustment"), the test states it as an explicit change of the
recorded expectation, next to the row it comes from; everything else must be identical.

The scenarios create a scratch database and roles with this run's ``PREFIX`` and drop them by exact name, so the
leak check of ``conftest.py`` covers them.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import copy
from typing import Any

import pytest

from tabrion.errors import RoleUnsafe, safe_message
from tabrion.postgres import (
    SessionLock,
    SessionSettings,
    check_role,
    configure,
    extension_status,
    inspect_role,
    ping,
    role_info,
    session_configurator,
    wait_advisory_lock,
)
from tests.golden.consumers import scenarios
from tests.postgres.conftest import PREFIX

psycopg = pytest.importorskip("psycopg")
pytest.importorskip("psycopg_pool")

AXENTRA = scenarios.golden("axentra")
NODESTRO = scenarios.golden("nodestro")
ORKEXA = scenarios.golden("orkexa")
LOCK_KEY = 4711


@pytest.fixture
def scratch(tabrion_postgres_url: str) -> Iterator[scenarios.Scratch]:
    with scenarios.Scratch(tabrion_postgres_url, f"{PREFIX}g_") as created:
        yield created


def _subset(recorded: Any, replayed: Any) -> None:
    """Every recorded key has the recorded value in the replay (Tabrion may return more keys: a superset)."""
    if isinstance(recorded, dict):
        assert isinstance(replayed, dict), replayed
        for key, value in recorded.items():
            assert key in replayed, f"missing {key!r} in {replayed!r}"
            _subset(value, replayed[key])
    else:
        assert replayed == recorded


# --- 8.1 Axentra ------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("timeout", [5000, 0])
def test_axentra_configure(scratch: scenarios.Scratch, timeout: int) -> None:
    """``Database._configure``: identical GUCs, and the ``commit()`` leaves the connection IDLE."""
    settings = SessionSettings.unchanged()
    settings = SessionSettings(**{**_fields(settings), "statement_timeout_ms": timeout})
    replay = scenarios.configure_scenario(scratch, session_configurator(settings), autocommit=False)
    assert replay == AXENTRA[f"configure.statement_timeout_ms={timeout}"]


def test_axentra_ping(scratch: scenarios.Scratch) -> None:
    """``Database.ping``: ``ping(self.pool, timeout=5).ok``."""
    assert scenarios.ping_scenario(scratch, lambda pool: ping(pool, timeout=5).ok) == AXENTRA["ping"]


def test_axentra_role_info_and_check_role(scratch: scenarios.Scratch) -> None:
    """``Database.role_info``/``check_role``: ``owner_scope="rls"``; ``RoleUnsafe`` is the refusal. ``RoleInfo``
    also names the role (a superset of Axentra's dict)."""

    def info(url: str) -> dict[str, Any]:
        with psycopg.connect(url, autocommit=True) as conn:
            return _fields(role_info(conn, owner_scope="rls"))

    def check(url: str, allow_superuser: bool, allow_table_owner: bool) -> bool:
        with psycopg.connect(url, autocommit=True) as conn:
            try:
                check_role(
                    conn, allow_superuser=allow_superuser, allow_table_owner=allow_table_owner, owner_scope="rls"
                )
            except RoleUnsafe:
                return False
            return True

    _subset(AXENTRA["role_info.check_role"], scenarios.role_scenario(scratch, info, check))


def test_axentra_inspect_role(scratch: scenarios.Scratch) -> None:
    """``roles.inspect_role``: ``as_dict()`` is a superset (plus ``database``)."""
    replay = scenarios.inspect_scenario(scratch, _inspect)
    _subset(AXENTRA["inspect_role"], replay)
    assert all(case["database"] == "<database>" for case in replay.values())


def test_axentra_wait_for_lock(scratch: scenarios.Scratch) -> None:
    """``migrations._wait_for_lock``: ``wait_advisory_lock(conn, MIGRATION_LOCK)``, same polling semantics."""
    recorded = AXENTRA["wait_for_lock"]
    replay = scenarios.wait_lock_scenario(scratch, wait_advisory_lock, recorded["key"])
    assert replay == recorded["result"]


class _Leader:
    """Axentra's ``LeaderLock`` on a :class:`SessionLock`: ``acquire`` keeps the lead or takes it (contract row
    "Worker lock")."""

    def __init__(self, url: str, key: int) -> None:
        self.lock = SessionLock(url)
        self.key = key

    def acquire(self) -> bool:
        return self.lock.holds(self.key) or self.lock.try_acquire(self.key)

    def release(self) -> None:
        self.lock.release(self.key)


def test_axentra_leader_lock(scratch: scenarios.Scratch) -> None:
    leaders: list[_Leader] = []

    def make(url: str, key: int) -> _Leader:
        leaders.append(_Leader(url, key))
        return leaders[-1]

    try:
        assert scenarios.leader_lock_scenario(scratch, make, LOCK_KEY) == AXENTRA["leader_lock"]
    finally:
        for leader in leaders:
            leader.lock.close()


# Where Tabrion's error texts differ from Axentra's ``db_errors.safe_message`` (contract 8.1, D-74): the kind text
# of a few SQLSTATE classes, and the table and column names Tabrion adds. Same SQLSTATE and constraint, no value.
_KIND_TEXT = {
    "data error": "invalid data",
    "insufficient privilege": "permission denied",
    "query canceled": "timeout",  # a statement_timeout, which Tabrion tells apart from a cancel
    "syntax error or access rule violation": "undefined object",
}
_NAMES_ADDED = {
    "check_violation": ", table child",
    "foreign_key_violation": ", table child",
    "not_null_violation": ", table child, column needed",
    "unique_violation": ", table parent",
}


def _tabrion_text(label: str, axentra: str) -> str:
    kind, rest = axentra.split(" (", 1)
    return f"{_KIND_TEXT.get(kind, kind)} ({rest[:-1]}{_NAMES_ADDED.get(label, '')})"


def test_axentra_error_texts(scratch: scenarios.Scratch) -> None:
    """The ``except psycopg...`` sites: ``safe_message`` leaks no value (B-06-1), keeps the SQLSTATE and the
    constraint, and differs from Axentra's text only as ``_KIND_TEXT`` and ``_NAMES_ADDED`` say."""
    expected = copy.deepcopy(AXENTRA["safe_message"])
    for label, case in expected.items():
        assert case["leaks"] is False
        case["message"] = _tabrion_text(label, case["message"])
    assert scenarios.error_text_scenario(scratch, safe_message) == expected


# --- 8.2 Nodestro -----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("schema", [None, "nodestro"])
def test_nodestro_configure(scratch: scenarios.Scratch, schema: str | None) -> None:
    """``PostgresDatabase._configure``: identical GUCs (the schema quoted as an identifier), autocommit."""
    if schema:
        with scratch.admin() as conn:
            conn.execute(f"CREATE SCHEMA {schema}")
    settings = SessionSettings(
        statement_timeout_ms=30_000,
        idle_in_transaction_timeout_ms=300_000,
        lock_timeout_ms=None,
        application_name=None,
        timezone=None,
        search_path=schema,
    )
    replay = scenarios.configure_scenario(scratch, session_configurator(settings), autocommit=True)
    assert replay == NODESTRO[f"configure.schema={schema}"]


class _Counted:
    def __init__(self, url: str) -> None:
        self.lock = SessionLock(url)

    def try_(self, key: int) -> bool:
        return self.lock.try_acquire(key)

    def holds(self, key: int) -> bool:
        return self.lock.holds(key)

    def release(self, key: int) -> None:
        self.lock.release(key)

    def close(self) -> None:
        self.lock.close()


def test_nodestro_session_locks(scratch: scenarios.Scratch) -> None:
    """``try_/holds_/release_session_lock``: ``SessionLock`` counts holds and notices a lost connection."""
    assert scenarios.session_lock_scenario(scratch, _Counted, LOCK_KEY) == NODESTRO["session_lock"]


def test_nodestro_role_info(scratch: scenarios.Scratch) -> None:
    """``role_info``: same name, ``owner_scope="schema"`` (every table of ``current_schema()``)."""

    def info(url: str) -> dict[str, Any]:
        with psycopg.connect(url, autocommit=True) as conn:
            return _fields(role_info(conn, owner_scope="schema"))

    assert scenarios.role_scenario(scratch, info, None) == NODESTRO["role_info"]


def test_nodestro_inspect_role(scratch: scenarios.Scratch) -> None:
    """``inspect_role``: the dict wrapper is ``as_dict()``, identical keys."""
    assert scenarios.inspect_scenario(scratch, _inspect) == NODESTRO["inspect_role"]


def test_nodestro_pgvector_status(scratch: scenarios.Scratch) -> None:
    """``pgvector_status``: ``extension_status(conn, "vector", probe_type="vector")``; the message texts stay
    Nodestro's. The golden file was recorded on a server without pgvector, so only the ``unavailable`` case is
    compared there; a server with pgvector compares the states the contract lists (TODO TB-071)."""

    def status(url: str) -> dict[str, Any]:
        with psycopg.connect(url, autocommit=True) as conn:
            return _fields(extension_status(conn, "vector", probe_type="vector"))

    replay = scenarios.vector_scenario(scratch, status)
    recorded = NODESTRO["pgvector_status"]
    if replay["server_has_vector"] == recorded["server_has_vector"]:
        assert replay == recorded
        return
    assert recorded["server_has_vector"] is False
    assert replay["in_public"]["state"] == "other_schema"
    assert replay["in_app"] == {"state": "active", "command": None, "extension_schema": "app"}


# --- 8.3 Orkexa -------------------------------------------------------------------------------------------------------


class _ConfiguredSource:
    """Orkexa's ``PsycopgSource`` with Tabrion's ``configure`` on every new connection (contract row 8.3: it gets
    the timeouts it lacks); the rest (autocommit, ``BEGIN`` ... ``COMMIT`` for ``"transaction"``) is unchanged."""

    def __init__(self, url: str) -> None:
        self.url = url

    @contextmanager
    def transaction(self, mode: str = "transaction") -> Iterator[Any]:
        with psycopg.connect(self.url, autocommit=True) as conn:
            configure(conn)
            if mode == "transaction":
                with conn.transaction():
                    yield conn
            else:
                yield conn

    def close(self) -> None:
        pass


def test_orkexa_source_with_tabrion_configure(scratch: scenarios.Scratch) -> None:
    """``PsycopgSource``: the transaction behavior stays; the only change is the settings ``configure`` adds."""
    expected = copy.deepcopy(ORKEXA["source"])
    added = {
        "statement_timeout": "30s",
        "lock_timeout": "10s",
        "idle_in_transaction_session_timeout": "5min",
        "application_name": "tabrion",
        "TimeZone": "UTC",
    }
    for mode in ("read", "write", "transaction"):
        assert expected[mode]["changed"] == {}  # Orkexa sets nothing today (no timeouts)
        expected[mode]["changed"] = added
    replay = scenarios.source_scenario(scratch, _ConfiguredSource)
    for mode in ("read", "write", "transaction"):  # the server's own TimeZone may already be UTC
        if replay[mode]["changed"].get("TimeZone") is None:
            expected[mode]["changed"].pop("TimeZone")
    assert replay == expected


def test_orkexa_ping_is_new(scratch: scenarios.Scratch) -> None:
    """``PsycopgSource`` has no ping (contract row 8.3); Tabrion's works on a connection of the source."""
    with _ConfiguredSource(scratch.url).transaction("read") as conn:
        assert ping(conn).ok


# --- helpers ----------------------------------------------------------------------------------------------------------


def _fields(value: Any) -> dict[str, Any]:
    return {name: getattr(value, name) for name in value.__slots__}


def _inspect(owner_url: str, role: str, schema: str) -> dict[str, Any]:
    with psycopg.connect(owner_url, autocommit=True) as conn:
        return inspect_role(conn, role, schema=schema).as_dict()
