"""RA-B-2, the idempotent transfer of the measurements application to PostgreSQL (``examples/measurements``).

Planning document 09 acceptance: the transfer to PostgreSQL is idempotent (natural key device + channel +
timestamp, ``ON CONFLICT DO NOTHING``, the local high-water mark moves only after the commit); a repeated transfer
changes nothing.

Every test runs against PostgreSQL (marker ``postgres``, a schema of its own from ``tabrion.testing``) and against
an upstream SQLite file, with the same code (metric MW-8). RA-B-3 adds real network faults.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
import io
from pathlib import Path
import sqlite3
import sys
from typing import Any

from measurements import Measurement, Store, open_database
from measurements.cli import _read_csv, main
from measurements.store import synthetic
from measurements.transfer import Report, transfer
from measurements.upstream import UPSTREAM, URL_ENV, create_upstream, insert_new, open_upstream
import pytest
import sqlalchemy as sa

from tabrion import Database
from tabrion.errors import TabrionError

from . import measurements_upstream as support

upstream_url = support.upstream_url
upstream = support.upstream
store = support.store


def capture_2500(store: Store) -> None:
    store.capture(synthetic(1500, device_id="dev-1"))
    store.capture(synthetic(1000, device_id="dev-2", channel="rh"))


def snapshot(database: Database) -> list[tuple[Any, ...]]:
    """Every upstream row, in key order, ``ts_utc`` as UTC (SQLite returns it naive)."""
    rows = database.fetch_all(sa.select(UPSTREAM).order_by(*UPSTREAM.primary_key.columns))
    return [
        (r.device_id, r.channel, r.ts_utc.replace(tzinfo=UTC) if r.ts_utc.tzinfo is None else r.ts_utc, *r[3:])
        for r in rows
    ]


def count(database: Database) -> int:
    return int(database.fetch_scalar(sa.select(sa.func.count()).select_from(UPSTREAM)))


def test_transfer_sends_every_row_once_in_batches(store: Store, upstream: Database) -> None:
    capture_2500(store)
    last = store.after(0, limit=5000)[-1].seq
    report = transfer(store, upstream, batch_size=1000)
    assert report == Report(batches=3, sent=2500, inserted=2500, mark=last)
    assert store.mark() == last and count(upstream) == 2500
    assert transfer(store, upstream) == Report(batches=0, sent=0, inserted=0, mark=last)  # nothing new

    store.capture(synthetic(10, device_id="dev-3"))  # capture goes on; the next run sends only the new rows
    report = transfer(store, upstream, batch_size=1000)
    assert (report.batches, report.sent, report.inserted) == (1, 10, 10)
    assert count(upstream) == 2510


def test_a_repeated_transfer_changes_nothing(tmp_path: Path, store: Store, upstream: Database) -> None:
    capture_2500(store)
    transfer(store, upstream, batch_size=700)
    before = snapshot(upstream)
    assert len(before) == 2500

    with sqlite3.connect(tmp_path / "local.db") as raw:  # the mark is lost: everything goes again
        raw.execute("DELETE FROM transfer_mark")
    raw.close()
    assert store.mark() == 0
    report = transfer(store, upstream, batch_size=700)
    assert (report.batches, report.sent, report.inserted) == (4, 2500, 0)
    assert snapshot(upstream) == before  # the first copy stays, source_seq included

    # A second device file with the same readings (a restored backup, a replaced device): nothing new upstream.
    other = open_database(tmp_path / "other.db", backup_dir=tmp_path / "backups")
    try:
        capture_2500(Store(other))
        assert transfer(Store(other), upstream, batch_size=4000).inserted == 0
    finally:
        other.close()
    assert snapshot(upstream) == before


def test_the_mark_moves_only_after_the_upstream_commit(
    store: Store, upstream: Database, upstream_url: sa.URL, monkeypatch: pytest.MonkeyPatch
) -> None:
    """At the moment ``advance`` runs, another connection already sees the batch upstream: it is committed."""
    capture_2500(store)
    seen: list[tuple[int, int, int]] = []
    advance = Store.advance

    def watching(self: Store, target: str, seq: int) -> None:
        with open_upstream(upstream_url) as observer:
            seen.append((self.mark(target), seq, count(observer)))
        advance(self, target, seq)

    monkeypatch.setattr(Store, "advance", watching)
    transfer(store, upstream, batch_size=1000)
    seqs = [stored.seq for stored in store.after(0, limit=5000)]
    assert seen == [(0, seqs[999], 1000), (seqs[999], seqs[1999], 2000), (seqs[1999], seqs[2499], 2500)]


def test_a_failed_batch_keeps_the_mark_and_is_sent_again(
    store: Store, upstream: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The upstream refuses the second batch: it is rolled back, the mark stays after the first one."""
    capture_2500(store)
    calls = 0

    def failing(dialect: str, rows: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 2:
            return sa.text("INSERT INTO no_such_table VALUES (1)")
        return insert_new(dialect, rows)

    monkeypatch.setattr("measurements.transfer.insert_new", failing)
    with pytest.raises(TabrionError):
        transfer(store, upstream, batch_size=1000)
    seqs = [stored.seq for stored in store.after(0, limit=5000)]
    assert store.mark() == seqs[999] and count(upstream) == 1000

    monkeypatch.undo()
    report = transfer(store, upstream, batch_size=1000)
    assert (report.sent, report.inserted, report.mark) == (1500, 1500, seqs[-1])
    assert count(upstream) == 2500


def test_a_crash_between_commit_and_mark_sends_the_batch_again_without_duplicates(
    store: Store, upstream: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    capture_2500(store)
    calls = 0
    advance = Store.advance

    def crashing(self: Store, target: str, seq: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("the process dies after the upstream COMMIT")
        advance(self, target, seq)

    monkeypatch.setattr(Store, "advance", crashing)
    with pytest.raises(RuntimeError):
        transfer(store, upstream, batch_size=1000)
    assert count(upstream) == 2000  # the second batch is committed upstream, its mark is not
    monkeypatch.undo()
    report = transfer(store, upstream, batch_size=1000)
    assert (report.batches, report.sent, report.inserted) == (2, 1500, 500)
    assert count(upstream) == 2500


def test_values_arrive_unchanged_and_the_key_is_the_instant(tmp_path: Path, store: Store, upstream: Database) -> None:
    moment = datetime(2026, 3, 29, 1, 30, 15, 123456, tzinfo=UTC)
    store.capture(
        [
            Measurement("dev-1", "temp", moment, 21.123456789012345, "degC", 3),
            Measurement("dev-1", "temp", moment + timedelta(microseconds=1), -0.0, "degC"),
            Measurement("dev-1", "rh", moment, 1e-300, "%", 255),
        ]
    )
    transfer(store, upstream)
    assert snapshot(upstream) == [
        ("dev-1", "rh", moment, 1e-300, "%", 255, 3),
        ("dev-1", "temp", moment, 21.123456789012345, "degC", 3, 1),
        ("dev-1", "temp", moment + timedelta(microseconds=1), -0.0, "degC", 0, 2),
    ]
    # The same instant with another offset is the same natural key, also upstream: nothing new.
    same = moment.astimezone(timezone(timedelta(hours=2)))
    other = open_database(tmp_path / "other.db", backup_dir=tmp_path / "backups")
    try:
        Store(other).capture([Measurement("dev-1", "temp", same, 99.0, "degC")])
        assert transfer(Store(other), upstream).inserted == 0
    finally:
        other.close()


def test_the_mark_never_moves_back(store: Store) -> None:
    store.advance("upstream", 10)
    store.advance("upstream", 4)
    store.advance("archive", 2)
    assert (store.mark(), store.mark("archive"), store.mark("unknown")) == (10, 2, 0)
    for seq in (-1, True, 1.5):
        with pytest.raises(ValueError, match=r"^seq must be"):
            store.advance("upstream", seq)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match=r"^target must be"):
        store.advance("", 1)


@pytest.mark.parametrize("batch_size", [0, 4001, True])
def test_the_batch_size_is_bounded(store: Store, tmp_path: Path, batch_size: int) -> None:
    with open_upstream(f"sqlite:///{tmp_path / 'up.db'}") as database, pytest.raises(ValueError, match="batch_size"):
        transfer(store, database, batch_size=batch_size)


def test_cli_transfer(
    tmp_path: Path, upstream_url: sa.URL, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    base = ["--db", str(tmp_path / "cli.db"), "--backup-dir", str(tmp_path / "backups")]
    secret = tmp_path / "upstream-url"
    secret.write_text(upstream_url.render_as_string(hide_password=False) + "\n")
    monkeypatch.setenv(f"{URL_ENV}_FILE", str(secret))  # the URL never appears on the command line
    assert main([*base, "init-upstream"]) == 0
    assert capsys.readouterr().out == "upstream ready\n"
    assert main([*base, "demo", "2500"]) == 0
    capsys.readouterr()
    assert main([*base, "transfer", "--batch-size", "1000"]) == 0
    assert capsys.readouterr().out == "2500 sent in 3 batches, 2500 new upstream, mark 2500\n"
    assert main([*base, "transfer"]) == 0
    assert capsys.readouterr().out == "0 sent in 0 batches, 0 new upstream, mark 2500\n"


def test_transfer_from_start_fills_a_switched_upstream(
    tmp_path: Path, upstream_url: sa.URL, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review L-1 of #23: the mark says what was sent, not where. Pointed at a fresh upstream, a plain run sends
    nothing; ``--from-start`` sends everything again, and a repeat against the old upstream adds nothing."""
    base = ["--db", str(tmp_path / "cli.db"), "--backup-dir", str(tmp_path / "backups")]
    monkeypatch.delenv(f"{URL_ENV}_FILE", raising=False)
    fresh = sa.make_url(f"sqlite:///{tmp_path / 'fresh.db'}")
    with open_upstream(fresh) as database:
        create_upstream(database)
    assert main([*base, "demo", "100"]) == 0
    for url in (upstream_url, fresh):
        monkeypatch.setenv(URL_ENV, url.render_as_string(hide_password=False))
        capsys.readouterr()
        assert main([*base, "transfer"]) == 0
    assert capsys.readouterr().out == "0 sent in 0 batches, 0 new upstream, mark 100\n"  # the silent case
    assert main([*base, "transfer", "--from-start"]) == 0
    assert capsys.readouterr().out == "100 sent in 1 batches, 100 new upstream, mark 100\n"
    monkeypatch.setenv(URL_ENV, upstream_url.render_as_string(hide_password=False))
    assert main([*base, "transfer", "--from-start"]) == 0
    assert capsys.readouterr().out == "100 sent in 1 batches, 0 new upstream, mark 100\n"


def test_cli_transfer_errors_name_no_values(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("psycopg")
    base = ["--db", str(tmp_path / "cli.db"), "--backup-dir", str(tmp_path / "backups")]
    monkeypatch.delenv(URL_ENV, raising=False)
    monkeypatch.delenv(f"{URL_ENV}_FILE", raising=False)
    assert main([*base, "transfer"]) == 2
    assert capsys.readouterr().err == (
        f"measurements: transfer stopped: config.missing (name={URL_ENV}); the mark stays at 0\n"
    )
    # A server that refuses the connection: the error names a code and a classification, not the URL or password.
    monkeypatch.setenv(URL_ENV, "postgresql://CANARY-user:CANARY-password@127.0.0.1:1/CANARY-db")
    assert main([*base, "demo", "3"]) == 0
    assert main([*base, "transfer"]) == 2
    captured = capsys.readouterr()
    assert captured.err.startswith("measurements: transfer stopped: database.")
    assert captured.err.endswith("; the mark stays at 0\n")
    assert "CANARY" not in captured.err + captured.out


def test_csv_errors_carry_no_context() -> None:
    """Review R-1 of the RA-B-1 re-review: the ``line N:`` error is raised outside the ``except`` block, so neither
    ``__context__`` nor ``__cause__`` holds the parser's error, which quotes the input."""
    for text in ("d,c,NOT-A-DATE-CANARY,1.0,V\n", "d,c,2026-01-01T00:00:00Z,CANARY,V\n", "d,c\n"):
        with pytest.raises(ValueError, match=r"^line 1: ") as info:
            list(_read_csv(io.StringIO(text)))
        assert info.value.__context__ is None and info.value.__cause__ is None
        assert "CANARY" not in repr(info.value)


def test_capture_runs_without_the_transfer_modules() -> None:
    """Capture is layer A only: importing the package and its CLI loads neither SQLAlchemy nor the transfer."""
    import subprocess

    code = (
        "import sys; import measurements, measurements.cli; "
        "print(sorted(m for m in ('sqlalchemy', 'measurements.transfer', 'measurements.upstream') if m in sys.modules))"
    )
    root = Path(__file__).resolve().parents[2] / "examples" / "measurements"
    result = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, check=True)
    assert result.stdout == "[]\n"
