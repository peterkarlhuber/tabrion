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

import pytest
import sqlalchemy as sa

from . import measurements_upstream as support
from . import measurements_variants as variants
from .measurements_upstream import count, snapshot
from .measurements_variants import Upstream, Variant

app = variants.app
upstream_url = support.upstream_url
upstream = support.upstream
store = support.store
URL_ENV = "MEASUREMENTS_UPSTREAM_URL"


def capture_2500(app: Variant, store: Any) -> None:
    store.capture(app.synthetic(1500, device_id="dev-1"))
    store.capture(app.synthetic(1000, device_id="dev-2", channel="rh"))


def test_transfer_sends_every_row_once_in_batches(app: Variant, store: Any, upstream: Upstream) -> None:
    capture_2500(app, store)
    last = store.after(0, limit=5000)[-1].seq
    report = app.transfer(store, upstream.handle, batch_size=1000)
    assert report == app.Report(batches=3, sent=2500, inserted=2500, mark=last)
    assert store.mark() == last and count(upstream) == 2500
    assert app.transfer(store, upstream.handle) == app.Report(batches=0, sent=0, inserted=0, mark=last)  # nothing new

    store.capture(app.synthetic(10, device_id="dev-3"))  # capture goes on; the next run sends only the new rows
    report = app.transfer(store, upstream.handle, batch_size=1000)
    assert (report.batches, report.sent, report.inserted) == (1, 10, 10)
    assert count(upstream) == 2510


def test_a_repeated_transfer_changes_nothing(tmp_path: Path, app: Variant, store: Any, upstream: Upstream) -> None:
    capture_2500(app, store)
    app.transfer(store, upstream.handle, batch_size=700)
    before = snapshot(upstream)
    assert len(before) == 2500

    with sqlite3.connect(tmp_path / "local.db") as raw:  # the mark is lost: everything goes again
        raw.execute("DELETE FROM transfer_mark")
    raw.close()
    assert store.mark() == 0
    report = app.transfer(store, upstream.handle, batch_size=700)
    assert (report.batches, report.sent, report.inserted) == (4, 2500, 0)
    assert snapshot(upstream) == before  # the first copy stays, source_seq included

    # A second device file with the same readings (a restored backup, a replaced device): nothing new upstream.
    other = app.open(tmp_path / "other.db")
    try:
        capture_2500(app, other.store)
        assert app.transfer(other.store, upstream.handle, batch_size=4000).inserted == 0
    finally:
        other.close()
    assert snapshot(upstream) == before


def test_the_mark_moves_only_after_the_upstream_commit(
    app: Variant, store: Any, upstream: Upstream, upstream_url: sa.URL, monkeypatch: pytest.MonkeyPatch
) -> None:
    """At the moment ``advance`` runs, another connection already sees the batch upstream: it is committed."""
    capture_2500(app, store)
    seen: list[tuple[int, int, int]] = []
    advance = app.Store.advance

    def watching(self: Any, target: str, seq: int) -> None:
        with app.upstream(upstream_url) as observer:
            seen.append((self.mark(target), seq, count(observer)))
        advance(self, target, seq)

    monkeypatch.setattr(app.Store, "advance", watching)
    app.transfer(store, upstream.handle, batch_size=1000)
    seqs = [stored.seq for stored in store.after(0, limit=5000)]
    assert seen == [(0, seqs[999], 1000), (seqs[999], seqs[1999], 2000), (seqs[1999], seqs[2499], 2500)]


def test_a_failed_batch_keeps_the_mark_and_is_sent_again(
    app: Variant, store: Any, upstream: Upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The upstream refuses the second batch: it is rolled back, the mark stays after the first one."""
    capture_2500(app, store)
    calls = 0

    def failing(dialect: str, rows: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 2:
            return sa.text("INSERT INTO no_such_table VALUES (1)")
        return insert_new(dialect, rows)

    insert_new = app.insert_new
    monkeypatch.setattr(f"{app.package}.transfer.insert_new", failing)
    with pytest.raises(app.UpstreamError):
        app.transfer(store, upstream.handle, batch_size=1000)
    seqs = [stored.seq for stored in store.after(0, limit=5000)]
    assert store.mark() == seqs[999] and count(upstream) == 1000

    monkeypatch.undo()
    report = app.transfer(store, upstream.handle, batch_size=1000)
    assert (report.sent, report.inserted, report.mark) == (1500, 1500, seqs[-1])
    assert count(upstream) == 2500


def test_a_crash_between_commit_and_mark_sends_the_batch_again_without_duplicates(
    app: Variant, store: Any, upstream: Upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    capture_2500(app, store)
    calls = 0
    advance = app.Store.advance

    def crashing(self: Any, target: str, seq: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("the process dies after the upstream COMMIT")
        advance(self, target, seq)

    monkeypatch.setattr(app.Store, "advance", crashing)
    with pytest.raises(RuntimeError):
        app.transfer(store, upstream.handle, batch_size=1000)
    assert count(upstream) == 2000  # the second batch is committed upstream, its mark is not
    monkeypatch.undo()
    report = app.transfer(store, upstream.handle, batch_size=1000)
    assert (report.batches, report.sent, report.inserted) == (2, 1500, 500)
    assert count(upstream) == 2500


def test_values_arrive_unchanged_and_the_key_is_the_instant(
    tmp_path: Path, app: Variant, store: Any, upstream: Upstream
) -> None:
    moment = datetime(2026, 3, 29, 1, 30, 15, 123456, tzinfo=UTC)
    store.capture(
        [
            app.Measurement("dev-1", "temp", moment, 21.123456789012345, "degC", 3),
            app.Measurement("dev-1", "temp", moment + timedelta(microseconds=1), -0.0, "degC"),
            app.Measurement("dev-1", "rh", moment, 1e-300, "%", 255),
        ]
    )
    app.transfer(store, upstream.handle)
    assert snapshot(upstream) == [
        ("dev-1", "rh", moment, 1e-300, "%", 255, 3),
        ("dev-1", "temp", moment, 21.123456789012345, "degC", 3, 1),
        ("dev-1", "temp", moment + timedelta(microseconds=1), -0.0, "degC", 0, 2),
    ]
    # The same instant with another offset is the same natural key, also upstream: nothing new.
    same = moment.astimezone(timezone(timedelta(hours=2)))
    other = app.open(tmp_path / "other.db")
    try:
        other.store.capture([app.Measurement("dev-1", "temp", same, 99.0, "degC")])
        assert app.transfer(other.store, upstream.handle).inserted == 0
    finally:
        other.close()


def test_the_mark_never_moves_back(app: Variant, store: Any) -> None:
    store.advance("upstream", 10)
    store.advance("upstream", 4)
    store.advance("archive", 2)
    assert (store.mark(), store.mark("archive"), store.mark("unknown")) == (10, 2, 0)
    for seq in (-1, True, 1.5):
        with pytest.raises(ValueError, match=r"^seq must be"):
            store.advance("upstream", seq)
    with pytest.raises(ValueError, match=r"^target must be"):
        store.advance("", 1)


@pytest.mark.parametrize("batch_size", [0, 4001, True])
def test_the_batch_size_is_bounded(app: Variant, store: Any, tmp_path: Path, batch_size: int) -> None:
    with app.upstream(f"sqlite:///{tmp_path / 'up.db'}") as opened, pytest.raises(ValueError, match="batch_size"):
        app.transfer(store, opened.handle, batch_size=batch_size)


def test_cli_transfer(
    app: Variant,
    tmp_path: Path,
    upstream_url: sa.URL,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = ["--db", str(tmp_path / "cli.db"), "--backup-dir", str(tmp_path / "backups")]
    secret = tmp_path / "upstream-url"
    secret.write_text(upstream_url.render_as_string(hide_password=False) + "\n")
    monkeypatch.setenv(f"{URL_ENV}_FILE", str(secret))  # the URL never appears on the command line
    assert app.main([*base, "init-upstream"]) == 0
    assert capsys.readouterr().out == "upstream ready\n"
    assert app.main([*base, "demo", "2500"]) == 0
    capsys.readouterr()
    assert app.main([*base, "transfer", "--batch-size", "1000"]) == 0
    assert capsys.readouterr().out == "2500 sent in 3 batches, 2500 new upstream, mark 2500\n"
    assert app.main([*base, "transfer"]) == 0
    assert capsys.readouterr().out == "0 sent in 0 batches, 0 new upstream, mark 2500\n"


def test_transfer_from_start_fills_a_switched_upstream(
    app: Variant,
    tmp_path: Path,
    upstream_url: sa.URL,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review L-1 of #23: the mark says what was sent, not where. Pointed at a fresh upstream, a plain run sends
    nothing; ``--from-start`` sends everything again, and a repeat against the old upstream adds nothing."""
    base = ["--db", str(tmp_path / "cli.db"), "--backup-dir", str(tmp_path / "backups")]
    monkeypatch.delenv(f"{URL_ENV}_FILE", raising=False)
    fresh = sa.make_url(f"sqlite:///{tmp_path / 'fresh.db'}")
    with app.upstream(fresh) as opened:
        app.create_upstream(opened.handle)
    assert app.main([*base, "demo", "100"]) == 0
    for url in (upstream_url, fresh):
        monkeypatch.setenv(URL_ENV, url.render_as_string(hide_password=False))
        capsys.readouterr()
        assert app.main([*base, "transfer"]) == 0
    assert capsys.readouterr().out == "0 sent in 0 batches, 0 new upstream, mark 100\n"  # the silent case
    assert app.main([*base, "transfer", "--from-start"]) == 0
    assert capsys.readouterr().out == "100 sent in 1 batches, 100 new upstream, mark 100\n"
    monkeypatch.setenv(URL_ENV, upstream_url.render_as_string(hide_password=False))
    assert app.main([*base, "transfer", "--from-start"]) == 0
    assert capsys.readouterr().out == "100 sent in 1 batches, 0 new upstream, mark 100\n"


def test_cli_transfer_errors_name_no_values(
    app: Variant, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """MW-4: an upstream that fails the batch's ``INSERT`` (its table is missing) and one that refuses the
    connection: the CLI's message names neither a value of the batch nor the URL's user, password or database."""
    pytest.importorskip("psycopg")
    base = ["--db", str(tmp_path / "cli.db"), "--backup-dir", str(tmp_path / "backups")]
    monkeypatch.delenv(f"{URL_ENV}_FILE", raising=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO("CANARY-device,c,2026-01-01T00:00:00Z,1.0,V\n"))
    assert app.main([*base, "capture"]) == 0
    for url in (
        f"sqlite:///{tmp_path / 'no-table.db'}",
        "postgresql://CANARY-user:CANARY-password@127.0.0.1:1/CANARY-db",
    ):
        monkeypatch.setenv(URL_ENV, url)
        capsys.readouterr()
        assert app.main([*base, "transfer"]) == 2
        captured = capsys.readouterr()
        assert captured.err.startswith("measurements: transfer stopped: ")
        if app.name == "tabrion":  # Tabrion's stable code (review L-6 of #28)
            assert captured.err.startswith("measurements: transfer stopped: database.")
        assert captured.err.endswith("; the mark stays at 0\n")
        assert "CANARY" not in captured.err + captured.out


def test_an_upstream_error_is_classified(
    app: Variant, tmp_path: Path, store: Any, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """MW-2 (error model): the transfer's error says what kind of failure it was, so the caller can tell an
    unreachable upstream from a refused statement; the CLI prints the stable code."""
    pytest.importorskip("psycopg")
    capture_2500(app, store)
    unreachable = app.upstream("postgresql://measurements@127.0.0.1:1/measurements")
    with unreachable as opened, pytest.raises(app.UpstreamError) as info:
        app.transfer(store, opened.handle)
    assert app.classified_as_connection(info.value)
    base = ["--db", str(tmp_path / "cli.db"), "--backup-dir", str(tmp_path / "backups")]
    monkeypatch.delenv(URL_ENV, raising=False)
    monkeypatch.delenv(f"{URL_ENV}_FILE", raising=False)
    assert app.main([*base, "transfer"]) == 2
    assert capsys.readouterr().err == (
        f"measurements: transfer stopped: config.missing (name={URL_ENV}); the mark stays at 0\n"
    )


def test_csv_errors_carry_no_context(app: Variant) -> None:
    """Review R-1 of the RA-B-1 re-review: the ``line N:`` error is raised outside the ``except`` block, so neither
    ``__context__`` nor ``__cause__`` holds the parser's error, which quotes the input."""
    for text in ("d,c,NOT-A-DATE-CANARY,1.0,V\n", "d,c,2026-01-01T00:00:00Z,CANARY,V\n", "d,c\n"):
        with pytest.raises(ValueError, match=r"^line 1: ") as info:
            list(app._read_csv(io.StringIO(text)))
        assert info.value.__context__ is None and info.value.__cause__ is None
        assert "CANARY" not in repr(info.value)


def test_capture_runs_without_the_transfer_modules(app: Variant) -> None:
    """Capture is layer A only: importing the package and its CLI loads neither SQLAlchemy nor the transfer."""
    import subprocess

    name = app.package
    code = (
        f"import sys; import {name}, {name}.cli; "
        f"print(sorted(m for m in ('sqlalchemy', '{name}.transfer', '{name}.upstream') if m in sys.modules))"
    )
    root = Path(__file__).resolve().parents[2] / "examples" / app.directory
    result = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, check=True)
    assert result.stdout == "[]\n"
