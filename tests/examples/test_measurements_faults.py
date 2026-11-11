"""RA-B-3, offline and failure tests of the measurements transfer (``examples/measurements``).

Planning document 09 acceptance: fault injection (a reply lost after the commit, a network cut, a process kill)
gives 0 losses and 0 duplicates; 24 hours of offline operation simulated. These counts are metric MW-6.

The network faults come from Tabrion's internal fault proxy (``tabrion.testing._faults.FaultProxy``, TAB-034). It
sits between the transfer and PostgreSQL and cuts the connection at a point the protocol defines (the ``INSERT``
or the ``COMMIT`` of a batch), never by a timer. After each fault the test reads the truth on the server through a
connection of its own, lets the transfer run again through the same ``tabrion.Database`` until it is done, and
counts:

- **losses**: captured readings that are not upstream;
- **duplicates**: upstream rows beyond one per natural key (``count(*)`` minus the distinct keys).

The process kill is a ``SIGKILL`` of the CLI (``python -m measurements ... transfer``) in a subprocess, placed by
the proxy as well: after the server ran the batch's ``COMMIT`` and before the child saw the answer (the worst case:
committed upstream, the mark not moved), and right after the server ran its ``INSERT`` (uncommitted).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
import random
import signal
import subprocess
import sys
import time

from measurements import Measurement, Store, open_database
from measurements.store import synthetic
from measurements.transfer import transfer
from measurements.upstream import PROFILE, UPSTREAM, create_upstream, open_upstream
import pytest
import sqlalchemy as sa

from tabrion import Database
from tabrion.errors import Kind, TabrionError
from tabrion.testing import PostgresSchema
from tabrion.testing._faults import FaultProxy, wait_backend_gone

from . import measurements_upstream as support
from .conftest import EXAMPLES
from .measurements_upstream import postgres_url

upstream_url = support.upstream_url
upstream = support.upstream
store = support.store

POSIX = pytest.mark.skipif(sys.platform == "win32", reason="SIGKILL")
INSERT = b"INSERT INTO measurement"
TOTAL = 2500


def tally(store: Store, observer: Database) -> tuple[int, int]:
    """``(losses, duplicates)``: local readings missing upstream, upstream rows beyond one per natural key."""
    local = {
        (s.measurement.device_id, s.measurement.channel, s.measurement.ts_utc) for s in store.after(0, limit=100_000)
    }
    rows = observer.fetch_all(sa.select(UPSTREAM.c.device_id, UPSTREAM.c.channel, UPSTREAM.c.ts_utc))
    keys = [
        (r.device_id, r.channel, r.ts_utc if r.ts_utc.tzinfo is not None else r.ts_utc.replace(tzinfo=UTC))
        for r in rows
    ]
    return len(local - set(keys)), len(keys) - len(set(keys))


def count(observer: Database) -> int:
    return int(observer.fetch_scalar(sa.select(sa.func.count()).select_from(UPSTREAM)))


def capture(store: Store) -> list[int]:
    store.capture(synthetic(1500, device_id="dev-1"))
    store.capture(synthetic(1000, device_id="dev-2", channel="rh"))
    return [stored.seq for stored in store.after(0, limit=100_000)]


# ---------------------------------------------------------------------------------------------------------------------
# PostgreSQL behind the fault proxy
# ---------------------------------------------------------------------------------------------------------------------


@pytest.fixture
def schema(tabrion_postgres_schema: PostgresSchema) -> PostgresSchema:
    from psycopg.conninfo import conninfo_to_dict

    params = conninfo_to_dict(tabrion_postgres_schema.owner_url)
    if str(params.get("host") or "").startswith("/"):
        pytest.skip("the fault proxy needs a TCP server address")
    with open_upstream(postgres_url(tabrion_postgres_schema.owner_url)) as database:
        create_upstream(database)
    return tabrion_postgres_schema


@pytest.fixture
def proxy(schema: PostgresSchema) -> Iterator[FaultProxy]:
    with FaultProxy.in_front_of(schema.owner_url) as faults:
        yield faults
    faults.check()


@pytest.fixture
def through(proxy: FaultProxy, schema: PostgresSchema) -> sa.URL:
    """The upstream URL with the proxy as its server."""
    return postgres_url(proxy.dsn(schema.owner_url))


@pytest.fixture
def observer(schema: PostgresSchema) -> Iterator[Database]:
    """A direct connection to the upstream, past the proxy: what the server really holds."""
    with open_upstream(postgres_url(schema.owner_url)) as database:
        yield database


def after_the_insert(proxy: FaultProxy, fault: str, then: Callable[[], None] | None = None) -> None:
    """Arm ``fault`` on the ``COMMIT`` that follows the next batch's ``INSERT`` (not one of the connection setup)."""
    proxy.arm(
        INSERT,
        fault="delay",
        then=lambda: proxy.arm(b"COMMIT", fault=fault, then=then),
    )


# (fault, statement, rows committed upstream by the failed attempt besides the first batch)
NETWORK_FAULTS = [
    pytest.param("reply", b"COMMIT", 1000, id="reply-lost-after-commit"),
    pytest.param("inflight", b"COMMIT", 1000, id="cut-while-committing"),
    pytest.param("request", b"COMMIT", 0, id="commit-never-sent"),
    pytest.param("reply", INSERT, 0, id="reply-lost-after-insert"),
    pytest.param("inflight", INSERT, 0, id="cut-while-inserting"),
    pytest.param("request", INSERT, 0, id="insert-never-sent"),
]


@pytest.mark.postgres
@pytest.mark.slow
@pytest.mark.parametrize("run", range(3))
@pytest.mark.parametrize(("fault", "statement", "committed"), NETWORK_FAULTS)
def test_a_network_fault_loses_and_duplicates_nothing(
    store: Store,
    proxy: FaultProxy,
    through: sa.URL,
    observer: Database,
    schema: PostgresSchema,
    fault: str,
    statement: bytes,
    committed: int,
    run: int,
) -> None:
    seqs = capture(store)
    with open_upstream(through) as upstream_db:
        assert transfer(store, upstream_db, batch_size=1000, max_batches=1).inserted == 1000
        if statement == b"COMMIT":
            after_the_insert(proxy, fault)
        else:
            proxy.arm(statement, fault=fault)
        with pytest.raises(TabrionError) as info:
            transfer(store, upstream_db, batch_size=1000)
        assert info.value.classification is not None and info.value.classification.kind is Kind.CONNECTION
        if statement == b"COMMIT":
            # Whether the COMMIT ran or not, the client cannot know (review L-3 of #26): never retried blindly.
            assert info.value.classification.outcome_unknown
        assert store.mark() == seqs[999]  # the failed batch did not move the mark
        backend = proxy.fired_backend
        assert backend is not None and wait_backend_gone(schema.owner_url, backend)
        assert count(observer) == 1000 + committed  # the truth on the server

        report = transfer(store, upstream_db, batch_size=1000)  # the same Database: the pool replaced the connection
    assert (report.sent, report.inserted, report.mark) == (1500, 1500 - committed, seqs[-1])
    assert tally(store, observer) == (0, 0)  # MW-6
    assert count(observer) == TOTAL


CHILD = [sys.executable, "-m", "measurements"]


def run_cli(local: Path, url: sa.URL, *args: str) -> subprocess.Popen[str]:
    env = {**os.environ, "MEASUREMENTS_UPSTREAM_URL": url.render_as_string(hide_password=False)}
    env.pop("MEASUREMENTS_UPSTREAM_URL_FILE", None)
    return subprocess.Popen(
        [*CHILD, "--db", str(local), "--backup-dir", str(local.parent / "backups"), "transfer", *args],
        cwd=EXAMPLES / "measurements",
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def finish(child: subprocess.Popen[str]) -> tuple[int, str, str]:
    out, err = child.communicate(timeout=120)
    return child.returncode, out, err


@POSIX
@pytest.mark.postgres
@pytest.mark.slow
@pytest.mark.parametrize("where", ["after-commit", "after-insert"])
def test_a_killed_transfer_loses_and_duplicates_nothing(
    tmp_path: Path, proxy: FaultProxy, through: sa.URL, observer: Database, schema: PostgresSchema, where: str
) -> None:
    local = tmp_path / "local.db"
    connection = open_database(local, backup_dir=tmp_path / "backups")
    store = Store(connection)
    try:
        seqs = capture(store)
        with open_upstream(through) as upstream_db:
            transfer(store, upstream_db, batch_size=500, max_batches=1)
        children: list[subprocess.Popen[str]] = []

        def kill() -> None:
            os.kill(children[0].pid, signal.SIGKILL)

        if where == "after-commit":
            after_the_insert(proxy, "delay", then=kill)  # the server ran the COMMIT; the child never sees it
        else:
            proxy.arm(INSERT, fault="delay", then=kill)
        children.append(run_cli(local, through, "--batch-size", "500"))
        code, _out, _err = finish(children[0])
        assert code == -signal.SIGKILL
        backend = proxy.fired_backend
        assert backend is not None and wait_backend_gone(schema.owner_url, backend)
        committed = 500 if where == "after-commit" else 0
        assert store.mark() == seqs[499]  # killed before the mark moved
        assert count(observer) == 500 + committed

        code, out, err = finish(run_cli(local, through, "--batch-size", "500"))
        assert (code, err) == (0, "")
        assert out == f"2000 sent in 4 batches, {2000 - committed} new upstream, mark {seqs[-1]}\n"
        assert tally(store, observer) == (0, 0)  # MW-6
        assert count(observer) == TOTAL
    finally:
        connection.close()


@POSIX
@pytest.mark.postgres
@pytest.mark.slow
def test_kills_at_arbitrary_moments_lose_and_duplicate_nothing(
    tmp_path: Path, schema: PostgresSchema, observer: Database
) -> None:
    """Five ``SIGKILL`` of a running transfer at moments drawn with a fixed seed (anywhere: reading, sending,
    committing, moving the mark), then one run to the end."""
    local = tmp_path / "local.db"
    connection = open_database(local, backup_dir=tmp_path / "backups")
    store = Store(connection)
    url = postgres_url(schema.owner_url)
    draw = random.Random(4711)  # noqa: S311  # reproducible kill moments, not cryptography
    try:
        seqs = capture(store)
        kills: list[int] = []
        for _ in range(5):
            before = store.mark()
            child = run_cli(local, url, "--batch-size", "20")
            deadline = time.monotonic() + 60
            while store.mark() == before and child.poll() is None and time.monotonic() < deadline:
                time.sleep(0.005)  # the child is under way once its first batch moved the mark
            time.sleep(draw.uniform(0.0, 0.05))
            if child.poll() is None:
                child.send_signal(signal.SIGKILL)
                kills.append(store.mark())
            child.communicate(timeout=120)
        assert len(kills) == 5 and all(0 < mark < seqs[-1] for mark in kills)  # every kill hit a running transfer
        code, out, err = finish(run_cli(local, url, "--batch-size", "20"))
        assert (code, err) == (0, "") and out.endswith(f" mark {seqs[-1]}\n")
        assert tally(store, observer) == (0, 0)  # MW-6
        assert count(observer) == TOTAL and store.mark() == seqs[-1]
    finally:
        connection.close()


# ---------------------------------------------------------------------------------------------------------------------
# 24 hours offline, simulated (SQLite and PostgreSQL upstream)
# ---------------------------------------------------------------------------------------------------------------------


@pytest.fixture
def unreachable() -> Iterator[Database]:
    """An upstream that refuses every connection (nothing listens on port 1): the device is offline."""
    pytest.importorskip("psycopg")
    profile = replace(PROFILE, connect_timeout=2.0)
    with open_upstream("postgresql://measurements@127.0.0.1:1/measurements", profile=profile) as database:
        yield database


@pytest.mark.skipif(sys.platform == "win32", reason="Windows takes about 2 s to refuse each loopback connection")
def test_24_hours_offline_capture_goes_on_and_the_transfer_catches_up(
    store: Store, upstream: Database, unreachable: Database
) -> None:
    """A simulated day: two readings a minute, a transfer attempt every 15 minutes, the upstream unreachable from
    02:00 to 22:00 and again for a short flap at 23:00. Capture never waits; nothing is lost or duplicated."""
    start = datetime(2026, 1, 1, tzinfo=UTC)
    failures = 0
    lags: list[int] = []
    for minute in range(0, 24 * 60, 5):  # one capture batch every 5 minutes: 5 readings per device
        now = start + timedelta(minutes=minute)
        batch = [
            Measurement(device, channel, now + timedelta(minutes=step), 20.0 + step, unit)
            for device, channel, unit in (("dev-1", "temp", "degC"), ("dev-2", "rh", "%"))
            for step in range(5)
        ]
        assert store.capture(batch) == 10  # capture never depends on the upstream
        if minute % 15:
            continue
        offline = 120 <= minute < 1320 or 1380 <= minute < 1395
        mark = store.mark()
        if offline:
            with pytest.raises(TabrionError) as info:
                transfer(store, unreachable, batch_size=500)
            assert info.value.classification is not None and info.value.classification.kind is Kind.CONNECTION
            assert store.mark() == mark
            failures += 1
        else:
            transfer(store, upstream, batch_size=500)
        lags.append(len(store.after(store.mark(), limit=100_000)))
    transfer(store, upstream, batch_size=500)  # the last attempt of the day
    assert failures == 81 and max(lags) == 20 * 60 * 2  # 20 hours of readings waited locally
    assert store.count() == 24 * 60 * 2 and not store.after(store.mark())
    assert tally(store, upstream) == (0, 0)  # MW-6
    assert count(upstream) == 24 * 60 * 2
