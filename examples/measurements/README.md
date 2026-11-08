# Measurements: local capture on SQLite

Reference application B, part 1 (TABRION RA-B-1). An edge device captures readings into a local SQLite file.
It uses **layer A only** (`tabrion.sqlite`), so it needs nothing but the standard library and Tabrion. RA-B-2
adds the transfer to PostgreSQL through layer B.

## Run it

From a checkout with Tabrion installed (`pip install -e .`):

```console
$ cd examples/measurements
$ python -m measurements --db data.db --backup-dir backups init
$ python -m measurements --db data.db --backup-dir backups demo 10000
10000 stored in 0.041 s
$ printf 'd2,rh,2026-01-01T00:00:00Z,41.5,%%\n' | python -m measurements --db data.db --backup-dir backups capture
1
$ python -m measurements --db data.db --backup-dir backups count
10001
```

From Python:

```python
from datetime import UTC, datetime
from measurements import Measurement, Store, open_database

connection = open_database("data.db", backup_dir="backups")
store = Store(connection)
store.capture([Measurement("dev-1", "temp", datetime.now(UTC), 21.5, "degC")])
connection.close()
```

## What it shows

| Property | Where |
|---|---|
| **One transaction per batch.** `Store.capture` validates the whole batch first, then writes it in one `tabrion.sqlite.write_transaction` (`BEGIN IMMEDIATE`). It stores all of it or nothing, also when the process is killed in the middle. | `measurements/store.py` |
| **Natural key.** `(device_id, channel, ts_utc)` is unique, and `ON CONFLICT DO NOTHING` stores a reading captured twice only once. A re-sent reading with the same key and **another value is ignored** too: a correction needs a key of its own. `seq` is the local capture order, which the transfer of RA-B-2 uses as its high-water mark: `AUTOINCREMENT` makes it monotonic and never reused, also after transferred rows are purged. | `measurements/db.py` (migration 1) |
| **Migrations with mandatory backup.** `tabrion.sqlite.migrate` moves `PRAGMA user_version` in one write transaction. Before that, `open_database` copies an existing database with `tabrion.sqlite.backup` to `backups/measurements-v<from>-to-v<to>-<time>.db`. Without a backup Tabrion refuses (`BackupRequired`), and a failed backup changes nothing (`BackupFailed`). Only a fresh, empty file skips the backup. | `measurements/db.py` |
| **Owner-only files.** The database, its `-wal`/`-shm` files and every backup are `0600` whatever the umask; the backup directory is `0700`; symbolic links are refused. | `tabrion.sqlite.connect`, `backup` |
| **Portable values.** `ts_utc` is fixed-width UTC text (`2026-01-01T12:00:00.000000Z`), so text order is time order; a naive `datetime` is refused. `value` is a finite `REAL`: NaN and infinity are refused. The unit is a column of its own (specification section 7). | `measurements/store.py` |
| **No values in errors.** A validation error names the position and the field, never the value; the CLI names the CSV line and the field. A constraint violation of a writer that bypasses validation becomes a fixed `ValueError`. | `Measurement.row`, `cli.py` |

The profile is `SQLiteProfile()`: WAL, `synchronous=FULL`. The specification allows `synchronous=NORMAL` for bulk
measurements; it is consistent in WAL mode, but a power loss can lose the last committed batch. Pass
`profile=SQLiteProfile(synchronous=Synchronous.NORMAL)` (from `tabrion.sqlite`) to `open_database` if the device
can afford that.

## Tests

`tests/examples/test_measurements.py` runs inside the normal suite (`pytest`) and covers the four RA-B-1
acceptance criteria:
- 10 000 measurements in one write transaction, checked with a statement trace;
- migration with mandatory backup;
- `0600` files;
- `SIGKILL` of a subprocess inside the batch's open transaction, after part of the batch was written. On reopen
  there are zero rows. Killed after the commit, all 10 000 are there.

The file-mode and kill tests are POSIX only.

## Measurement notes for RA-EVAL (planning document 09 section 7)

- **MW-1** (database infrastructure code): `measurements/db.py` holds all of it (connection, profile, migrations,
  backup); `store.py` and `cli.py` are domain code. Count `db.py` with `cloc`, together with the plumbing lines of
  the tests (the fixtures-free `open_database` calls).
- **MW-4** (values in errors): `test_invalid_measurement_writes_nothing` checks that a canary value does not
  appear in the message and that the error has no `__context__`.
- **MW-7** (runtime overhead): `python -m measurements ... demo N` prints the time of one batch of `N` rows.
  Compare it with the baseline of RA-B-4 on the same machine.
- **MW-2, MW-6 and MW-8** come with RA-B-2 and RA-B-3 (the transfer and the fault tests). MW-3 belongs to the parts
  application. MW-5 is measured from this README.
