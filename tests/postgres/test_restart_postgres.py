"""TAB-T-009 (planning document 07 DI-05, K7, probe P3): the first request after a PostgreSQL restart succeeds.

The test restarts the server, so it runs only where that is allowed: the nightly job ``pg restart`` (TODO TB-136)
sets ``TABRION_PG_RESTART_COMMAND`` to ``docker restart <service container>``. Everywhere else (the ``pg`` jobs, a
shared local server) it is skipped. The command is split with :func:`shlex.split` and run without a shell.

One restart serves two layer-B databases that each hold a pooled connection from before it:

- with ``pool_pre_ping`` (the default ``Profile``), the first request after the restart succeeds on a new backend;
- without it, the first request fails with ``ConnectionFailed`` (``Kind.CONNECTION``) and the next one succeeds. This
  is the positive control: it shows the restart really dropped the pooled connections.
"""

# The SQL is built from the schema fixture's generated name and fixed text, never from outside input.
# ruff: noqa: S608

from __future__ import annotations

from collections.abc import Iterator
import os
import shlex
import subprocess
import time
from typing import Any

import pytest

pytestmark = [pytest.mark.postgres, pytest.mark.restart]

pytest.importorskip("sqlalchemy")
psycopg = pytest.importorskip("psycopg")

from tabrion import Database, Profile  # noqa: E402
from tabrion.errors import ConnectionFailed, Kind  # noqa: E402
from tabrion.postgres import SessionSettings  # noqa: E402
from tests.postgres.test_database import sa_url  # noqa: E402

RESTART_VARIABLE = "TABRION_PG_RESTART_COMMAND"
RESTART_TIMEOUT = 120.0
"""Seconds for the restart command and, separately, for the server to accept connections again."""


def restart_command() -> list[str]:
    command = shlex.split(os.environ.get(RESTART_VARIABLE, ""))
    if not command:
        pytest.skip(f"{RESTART_VARIABLE} is not set: the server restart runs in the nightly job 'pg restart' only")
    return command


def wait_until_ready(url: str, timeout: float = RESTART_TIMEOUT) -> None:
    """Connect until the server answers (a restarted container needs a moment after ``docker restart`` returns)."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            with psycopg.connect(url, autocommit=True, connect_timeout=2) as conn:
                conn.execute("SELECT 1")
                return
        except psycopg.OperationalError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.5)


def started_at(url: str) -> Any:
    with psycopg.connect(url, autocommit=True, connect_timeout=10) as conn:
        row = conn.execute("SELECT pg_postmaster_start_time()").fetchone()
    assert row is not None
    return row[0]


@pytest.fixture
def make_db(admin_url: str, schema: str) -> Iterator[Any]:
    opened: list[Database] = []

    def make(**fields: Any) -> Database:
        profile = Profile(postgres=SessionSettings(search_path=schema), pool_size=1, max_overflow=0, **fields)
        database = Database.from_url(sa_url(admin_url), profile=profile)
        opened.append(database)
        return database

    yield make
    for database in opened:
        database.close()


def backend(database: Database) -> int:
    with database.read() as tx:
        pid = tx.fetch_scalar("SELECT pg_backend_pid()")
    assert isinstance(pid, int)
    return pid


def test_t009_first_request_after_a_restart(make_db: Any, admin_url: str, schema: str) -> None:
    command = restart_command()
    pinged = make_db()
    unpinged = make_db(pool_pre_ping=False)
    with pinged.transaction() as tx:
        tx.execute(f"INSERT INTO {schema}.parts (id, label) VALUES (1, 'before the restart')")
    before = (backend(pinged), backend(unpinged), started_at(admin_url))

    subprocess.run(command, check=True, timeout=RESTART_TIMEOUT, stdout=subprocess.DEVNULL)
    wait_until_ready(admin_url)
    assert started_at(admin_url) > before[2], "the server did not restart"

    # pool_pre_ping: the stale pooled connection is replaced before use, so the very first request succeeds.
    with pinged.read() as tx:
        assert tx.fetch_scalar(f"SELECT label FROM {schema}.parts WHERE id = 1") == "before the restart"
    assert backend(pinged) != before[0]

    # Control: without pre_ping the first request meets the dropped connection; the pool then discards it.
    with pytest.raises(ConnectionFailed) as failed, unpinged.read() as tx:
        tx.fetch_scalar("SELECT 1")
    assert failed.value.kind is Kind.CONNECTION
    assert backend(unpinged) != before[1]
