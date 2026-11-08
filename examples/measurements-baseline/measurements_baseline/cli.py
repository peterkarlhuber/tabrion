"""``python -m measurements_baseline``: the same command line as ``python -m measurements`` (baseline, RA-B-4).

    python -m measurements_baseline --db data.db --backup-dir backups init
    python -m measurements_baseline --db data.db --backup-dir backups capture < readings.csv
    python -m measurements_baseline --db data.db --backup-dir backups demo 10000
    python -m measurements_baseline --db data.db --backup-dir backups count
    python -m measurements_baseline --db data.db --backup-dir backups init-upstream
    python -m measurements_baseline --db data.db --backup-dir backups transfer

``init-upstream`` and ``transfer`` read the upstream URL from ``MEASUREMENTS_UPSTREAM_URL`` (or a file named by
``MEASUREMENTS_UPSTREAM_URL_FILE``), never from the command line, and need ``psycopg`` for PostgreSQL.

``capture`` reads CSV rows ``device_id,channel,ts_utc,value,unit[,quality]`` (``ts_utc`` in ISO 8601 with an offset
or ``Z``) and stores them as one batch.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterator, Sequence
import csv
from datetime import datetime
import sys
import time
from typing import TextIO

from measurements_baseline.db import open_database
from measurements_baseline.store import Measurement, Store, synthetic


def _parse(row: list[str]) -> Measurement:
    """One CSV row; a ``ValueError`` names the field, never the value (metric MW-4, review L-2 of #16)."""
    if len(row) not in (5, 6):
        raise ValueError("expected 5 or 6 fields")
    invalid: str | None = None
    moment, value, quality = None, 0.0, 0
    for field in ("ts_utc", "value", "quality"):
        try:
            if field == "ts_utc":
                moment = datetime.fromisoformat(row[2])
            elif field == "value":
                value = float(row[3])
            elif len(row) == 6:
                quality = int(row[5])
        except ValueError:
            invalid = field
        if invalid is not None:
            raise ValueError(f"{invalid} is invalid")
    if moment is None:  # pragma: no cover - fromisoformat returns or raises
        raise ValueError("ts_utc is invalid")
    return Measurement(row[0], row[1], moment, value, row[4], quality)


def _read_csv(stream: TextIO) -> Iterator[Measurement]:
    for line, row in enumerate(csv.reader(stream), start=1):
        if not row or row[0].startswith("#"):
            continue
        problem = None
        try:
            measurement = _parse(row)
        except ValueError as exc:
            problem = f"line {line}: {exc}"
        if problem is not None:
            raise ValueError(problem)  # outside the except block: no context with the value
        yield measurement


def _upstream(store: Store, command: str, batch_size: int, *, from_start: bool = False) -> int:
    from measurements_baseline.transfer import UpstreamError, transfer
    from measurements_baseline.upstream import create_upstream, open_upstream

    try:
        upstream = open_upstream()
        try:
            if command == "init-upstream":
                create_upstream(upstream)
                print("upstream ready")
                return 0
            if from_start:
                store.reset_mark()  # a switched or restored upstream: send everything again (idempotent)
            report = transfer(store, upstream, batch_size=batch_size)
        finally:
            upstream.dispose()
    except (UpstreamError, LookupError) as exc:
        failure = str(exc)
    else:
        print(f"{report.sent} sent in {report.batches} batches, {report.inserted} new upstream, mark {report.mark}")
        return 0
    print(f"measurements: {command} stopped: {failure}; the mark stays at {store.mark()}", file=sys.stderr)
    return 2


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="measurements_baseline", description=__doc__.splitlines()[0] if __doc__ else None
    )
    parser.add_argument("--db", required=True, help="database file (created 0600)")
    parser.add_argument("--backup-dir", required=True, help="where migrations put their mandatory backups")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="create or migrate the database")
    commands.add_parser("capture", help="store CSV rows from stdin as one batch")
    demo = commands.add_parser("demo", help="store COUNT synthetic readings as one batch and time it")
    demo.add_argument("count", type=int)
    commands.add_parser("count", help="print the number of stored readings")
    commands.add_parser("init-upstream", help="create the upstream table if it is missing")
    send = commands.add_parser("transfer", help="send the readings above the high-water mark upstream")
    send.add_argument("--batch-size", type=int, default=1000)
    send.add_argument(
        "--from-start", action="store_true", help="forget the mark first: after switching or restoring the upstream"
    )
    args = parser.parse_args(argv)

    engine = open_database(args.db, backup_dir=args.backup_dir)
    try:
        store = Store(engine)
        if args.command == "capture":
            try:
                stored = store.capture(_read_csv(sys.stdin))
            except ValueError as exc:
                # Tabrion's and this application's own fixed texts: a line or position and a field, never a value.
                print(f"measurements: {exc}; nothing was stored", file=sys.stderr)
                return 1
            print(stored)
        elif args.command == "demo":
            started = time.perf_counter()
            stored = store.capture(synthetic(args.count))
            print(f"{stored} stored in {time.perf_counter() - started:.3f} s")
        elif args.command == "count":
            print(store.count())
        elif args.command in ("init-upstream", "transfer"):
            if args.command == "transfer":
                return _upstream(store, args.command, args.batch_size, from_start=args.from_start)
            return _upstream(store, args.command, 0)
    finally:
        engine.dispose()
    return 0
