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
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
import random
import signal
import subprocess
import sys
import time
from typing import Any

import pytest
import sqlalchemy as sa

from tabrion.testing import PostgresSchema
from tabrion.testing._faults import FaultProxy, wait_backend_gone

from . import measurements_upstream as support
from . import measurements_variants as variants
from .conftest import EXAMPLES
from .measurements_upstream import count, keys, postgres_url
from .measurements_variants import Upstream, Variant

app = variants.app
upstream_url = support.upstream_url
upstream = support.upstream
store = support.store

POSIX = pytest.mark.skipif(sys.platform == "win32", reason="SIGKILL")
INSERT = b"INSERT INTO measurement"
TOTAL = 2500


def tally(store: Any, observer: Upstream) -> tuple[int, int]:
    """``(losses, duplicates)``: local readings missing upstream, upstream rows beyond one per natural key."""
    local = {
        (s.measurement.device_id, s.measurement.channel, s.measurement.ts_utc) for s in store.after(0, limit=100_000)
    }
    upstream_keys = keys(observer)
    return len(local - set(upstream_keys)), len(upstream_keys) - len(set(upstream_keys))


def capture(app: Variant, store: Any) -> list[int]:
    store.capture(app.synthetic(1500, device_id="dev-1"))
    store.capture(app.synthetic(1000, device_id="dev-2", channel="rh"))
    return [stored.seq for stored in store.after(0, limit=100_000)]


# ---------------------------------------------------------------------------------------------------------------------
# PostgreSQL behind the fault proxy
# ---------------------------------------------------------------------------------------------------------------------


@pytest.fixture
def schema(app: Variant, tabrion_postgres_schema: PostgresSchema) -> PostgresSchema:
    from psycopg.conninfo import conninfo_to_dict

    params = conninfo_to_dict(tabrion_postgres_schema.owner_url)
    if str(params.get("host") or "").startswith("/"):
        pytest.skip("the fault proxy needs a TCP server address")
    with app.upstream(postgres_url(tabrion_postgres_schema.owner_url)) as opened:
        app.create_upstream(opened.handle)
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
def observer(app: Variant, schema: PostgresSchema) -> Iterator[Upstream]:
    """A direct connection to the upstream, past the proxy: what the server really holds."""
    with app.upstream(postgres_url(schema.owner_url)) as opened:
        yield opened


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
    app: Variant,
    store: Any,
    proxy: FaultProxy,
    through: sa.URL,
    observer: Upstream,
    schema: PostgresSchema,
    fault: str,
    statement: bytes,
    committed: int,
    run: int,
) -> None:
    seqs = capture(app, store)
    with app.upstream(through) as opened:
        upstream_db = opened.handle
        assert app.transfer(store, upstream_db, batch_size=1000, max_batches=1).inserted == 1000
        if statement == b"COMMIT":
            after_the_insert(proxy, fault)
        else:
            proxy.arm(statement, fault=fault)
        with pytest.raises(app.UpstreamError):
            app.transfer(store, upstream_db, batch_size=1000)
        assert store.mark() == seqs[999]  # the failed batch did not move the mark
        backend = proxy.fired_backend
        assert backend is not None and wait_backend_gone(schema.owner_url, backend)
        assert count(observer) == 1000 + committed  # the truth on the server

        report = app.transfer(store, upstream_db, batch_size=1000)  # the same handle: the pool replaces the connection
    assert (report.sent, report.inserted, report.mark) == (1500, 1500 - committed, seqs[-1])
    assert tally(store, observer) == (0, 0)  # MW-6
    assert count(observer) == TOTAL


@pytest.mark.postgres
@pytest.mark.slow
def test_a_lost_commit_reply_is_reported_as_outcome_unknown(
    app: Variant, store: Any, proxy: FaultProxy, through: sa.URL, observer: Upstream
) -> None:
    """MW-2 (error model): the error of a lost ``COMMIT`` answer says that the outcome is unknown (here: committed),
    so a caller knows that only an idempotent retry is safe (the lesson of F-02)."""
    capture(app, store)
    with app.upstream(through) as opened:
        app.transfer(store, opened.handle, batch_size=1000, max_batches=1)
        after_the_insert(proxy, "reply")
        with pytest.raises(app.UpstreamError) as info:
            app.transfer(store, opened.handle, batch_size=1000)
    assert app.classified_as_connection(info.value) and app.outcome_unknown(info.value)


def run_cli(app: Variant, local: Path, url: sa.URL, *args: str) -> subprocess.Popen[str]:
    env = {**os.environ, "MEASUREMENTS_UPSTREAM_URL": url.render_as_string(hide_password=False)}
    env.pop("MEASUREMENTS_UPSTREAM_URL_FILE", None)
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            app.package,
            "--db",
            str(local),
            "--backup-dir",
            str(local.parent / "backups"),
            "transfer",
            *args,
        ],
        cwd=EXAMPLES / app.directory,
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
    app: Variant,
    tmp_path: Path,
    proxy: FaultProxy,
    through: sa.URL,
    observer: Upstream,
    schema: PostgresSchema,
    where: str,
) -> None:
    local = tmp_path / "local.db"
    opened = app.open(local)
    store = opened.store
    try:
        seqs = capture(app, store)
        with app.upstream(through) as upstream_db:
            app.transfer(store, upstream_db.handle, batch_size=500, max_batches=1)
        children: list[subprocess.Popen[str]] = []

        def kill() -> None:
            os.kill(children[0].pid, signal.SIGKILL)

        if where == "after-commit":
            after_the_insert(proxy, "delay", then=kill)  # the server ran the COMMIT; the child never sees it
        else:
            proxy.arm(INSERT, fault="delay", then=kill)
        children.append(run_cli(app, local, through, "--batch-size", "500"))
        code, _out, _err = finish(children[0])
        assert code == -signal.SIGKILL
        backend = proxy.fired_backend
        assert backend is not None and wait_backend_gone(schema.owner_url, backend)
        committed = 500 if where == "after-commit" else 0
        assert store.mark() == seqs[499]  # killed before the mark moved
        assert count(observer) == 500 + committed

        code, out, err = finish(run_cli(app, local, through, "--batch-size", "500"))
        assert (code, err) == (0, "")
        assert out == f"2000 sent in 4 batches, {2000 - committed} new upstream, mark {seqs[-1]}\n"
        assert tally(store, observer) == (0, 0)  # MW-6
        assert count(observer) == TOTAL
    finally:
        opened.close()


@POSIX
@pytest.mark.postgres
@pytest.mark.slow
def test_kills_at_arbitrary_moments_lose_and_duplicate_nothing(
    app: Variant, tmp_path: Path, schema: PostgresSchema, observer: Upstream
) -> None:
    """Five ``SIGKILL`` of a running transfer at moments drawn with a fixed seed (anywhere: reading, sending,
    committing, moving the mark), then one run to the end."""
    local = tmp_path / "local.db"
    opened = app.open(local)
    store = opened.store
    url = postgres_url(schema.owner_url)
    draw = random.Random(4711)  # noqa: S311  # reproducible kill moments, not cryptography
    try:
        seqs = capture(app, store)
        kills: list[int] = []
        for _ in range(5):
            before = store.mark()
            child = run_cli(app, local, url, "--batch-size", "20")
            deadline = time.monotonic() + 60
            while store.mark() == before and child.poll() is None and time.monotonic() < deadline:
                time.sleep(0.005)  # the child is under way once its first batch moved the mark
            time.sleep(draw.uniform(0.0, 0.05))
            if child.poll() is None:
                child.send_signal(signal.SIGKILL)
                kills.append(store.mark())
            child.communicate(timeout=120)
        assert len(kills) == 5 and all(0 < mark < seqs[-1] for mark in kills)  # every kill hit a running transfer
        code, out, err = finish(run_cli(app, local, url, "--batch-size", "20"))
        assert (code, err) == (0, "") and out.endswith(f" mark {seqs[-1]}\n")
        assert tally(store, observer) == (0, 0)  # MW-6
        assert count(observer) == TOTAL and store.mark() == seqs[-1]
    finally:
        opened.close()


# ---------------------------------------------------------------------------------------------------------------------
# 24 hours offline, simulated (SQLite and PostgreSQL upstream)
# ---------------------------------------------------------------------------------------------------------------------


@pytest.fixture
def unreachable(app: Variant) -> Iterator[Upstream]:
    """An upstream that refuses every connection (nothing listens on port 1): the device is offline."""
    pytest.importorskip("psycopg")
    with app.upstream("postgresql://measurements@127.0.0.1:1/measurements") as opened:
        yield opened


@pytest.mark.skipif(sys.platform == "win32", reason="Windows takes about 2 s to refuse each loopback connection")
def test_24_hours_offline_capture_goes_on_and_the_transfer_catches_up(
    app: Variant, store: Any, upstream: Upstream, unreachable: Upstream
) -> None:
    """A simulated day: two readings a minute, a transfer attempt every 15 minutes, the upstream unreachable from
    02:00 to 22:00 and again for a short flap at 23:00. Capture never waits; nothing is lost or duplicated."""
    start = datetime(2026, 1, 1, tzinfo=UTC)
    failures = 0
    lags: list[int] = []
    for minute in range(0, 24 * 60, 5):  # one capture batch every 5 minutes: 5 readings per device
        now = start + timedelta(minutes=minute)
        batch = [
            app.Measurement(device, channel, now + timedelta(minutes=step), 20.0 + step, unit)
            for device, channel, unit in (("dev-1", "temp", "degC"), ("dev-2", "rh", "%"))
            for step in range(5)
        ]
        assert store.capture(batch) == 10  # capture never depends on the upstream
        if minute % 15:
            continue
        offline = 120 <= minute < 1320 or 1380 <= minute < 1395
        mark = store.mark()
        if offline:
            with pytest.raises(app.UpstreamError):
                app.transfer(store, unreachable.handle, batch_size=500)
            assert store.mark() == mark
            failures += 1
        else:
            app.transfer(store, upstream.handle, batch_size=500)
        lags.append(len(store.after(store.mark(), limit=100_000)))
    app.transfer(store, upstream.handle, batch_size=500)  # the last attempt of the day
    assert failures == 81 and max(lags) == 20 * 60 * 2  # 20 hours of readings waited locally
    assert store.count() == 24 * 60 * 2 and not store.after(store.mark())
    assert tally(store, upstream) == (0, 0)  # MW-6
    assert count(upstream) == 24 * 60 * 2
