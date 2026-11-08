"""``python -m measurements``: a tiny command line over the capture API.

    python -m measurements --db data.db --backup-dir backups init
    python -m measurements --db data.db --backup-dir backups capture < readings.csv
    python -m measurements --db data.db --backup-dir backups demo 10000
    python -m measurements --db data.db --backup-dir backups count

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

from measurements.db import open_database
from measurements.store import Measurement, Store, synthetic


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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="measurements", description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--db", required=True, help="database file (created 0600)")
    parser.add_argument("--backup-dir", required=True, help="where migrations put their mandatory backups")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="create or migrate the database")
    commands.add_parser("capture", help="store CSV rows from stdin as one batch")
    demo = commands.add_parser("demo", help="store COUNT synthetic readings as one batch and time it")
    demo.add_argument("count", type=int)
    commands.add_parser("count", help="print the number of stored readings")
    args = parser.parse_args(argv)

    connection = open_database(args.db, backup_dir=args.backup_dir)
    try:
        store = Store(connection)
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
    finally:
        connection.close()
    return 0
