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


def _read_csv(stream: TextIO) -> Iterator[Measurement]:
    for row in csv.reader(stream):
        if not row or row[0].startswith("#"):
            continue
        quality = int(row[5]) if len(row) > 5 else 0
        yield Measurement(row[0], row[1], datetime.fromisoformat(row[2]), float(row[3]), row[4], quality)


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
            print(store.capture(_read_csv(sys.stdin)))
        elif args.command == "demo":
            started = time.perf_counter()
            stored = store.capture(synthetic(args.count))
            print(f"{stored} stored in {time.perf_counter() - started:.3f} s")
        elif args.command == "count":
            print(store.count())
    finally:
        connection.close()
    return 0
