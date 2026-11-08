# Measurements: local capture on SQLite, idempotent transfer to PostgreSQL

Reference application B (TABRION RA-B-1, RA-B-2). An edge device captures readings into a local SQLite file
(RA-B-1) and sends them to PostgreSQL in batches, as often as it likes, without losing or duplicating one (RA-B-2).
The capture uses **layer A only** (`tabrion.sqlite`), so it needs nothing but the standard library and Tabrion;
the transfer uses **layer B** (`tabrion.Database`, `tabrion.Profile`) and needs the extras `sqlalchemy` and
`postgres` (`pip install -e ".[sqlalchemy,postgres]"`).

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
$ export MEASUREMENTS_UPSTREAM_URL_FILE=upstream-url   # a file holding postgresql://user:password@host/db
$ python -m measurements --db data.db --backup-dir backups init-upstream
upstream ready
$ python -m measurements --db data.db --backup-dir backups transfer
10001 sent in 11 batches, 10001 new upstream, mark 10001
$ python -m measurements --db data.db --backup-dir backups transfer
0 sent in 0 batches, 0 new upstream, mark 10001
```

The mark records what was sent, not where: after switching the upstream to another server or database, or
restoring it from an older backup, run `transfer --from-start` once (`Store.reset_mark`). It sends everything
again, and the rows already upstream are skipped. One transfer process per device file is the intended
deployment; a second one is safe but wasteful.

The upstream URL comes from `MEASUREMENTS_UPSTREAM_URL` or, better, from a file named by
`MEASUREMENTS_UPSTREAM_URL_FILE` (`tabrion.config.url_from_env`), never from the command line. A `sqlite:///` URL
works too, with the same code.

From Python:

```python
from datetime import UTC, datetime
from measurements import Measurement, Store, open_database

connection = open_database("data.db", backup_dir="backups")
store = Store(connection)
store.capture([Measurement("dev-1", "temp", datetime.now(UTC), 21.5, "degC")])

from measurements.transfer import transfer
from measurements.upstream import create_upstream, open_upstream

with open_upstream("postgresql://user:password@localhost/measurements") as upstream:
    create_upstream(upstream)
    report = transfer(store, upstream)  # Report(batches=1, sent=1, inserted=1, mark=1)
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
| **No values in errors.** A validation error names the position and the field, never the value; the CLI names the CSV line and the field. A constraint violation of a writer that bypasses validation becomes a fixed `ValueError`. A transfer error prints Tabrion's code and classification, never the URL or a value. | `Measurement.row`, `cli.py` |

### The transfer (RA-B-2)

| Property | Where |
|---|---|
| **Batches by high-water mark.** The local table `transfer_mark` (migration 3) holds, per target, the largest `seq` that is committed upstream. A run sends the rows above it in capture order, `batch_size` (at most 4 000) per batch. | `measurements/transfer.py`, `Store.mark` |
| **One upstream transaction per batch, idempotent.** Each batch is one `Database.transaction()` with one `INSERT ... ON CONFLICT (device_id, channel, ts_utc) DO NOTHING RETURNING`. The natural key is the upstream primary key, so a batch sent again stores nothing new; the first copy of a reading wins. | `measurements/upstream.py` |
| **The mark moves only after the commit.** `Store.advance` runs after `transaction()` returned, in a local write transaction of its own, and never moves the mark back. A failure before the commit leaves the mark before the batch; a crash after the commit sends the batch again, and the key makes the copy a no-op. So a transfer can be repeated as often as needed: nothing is lost, nothing is stored twice. | `measurements/transfer.py` |
| **Offline-friendly.** The capture never waits for the upstream: the transfer reads the local file in short read transactions and holds no local lock during the upstream round trip. An upstream error (`tabrion.TabrionError`) stops the run; the next run picks up at the mark. | `transfer()` |
| **Layer B profile.** `tabrion.Profile(application_name="measurements-transfer", pool_size=1)`: Tabrion's TLS rule (a remote host gets `sslmode=require` at least), connect, statement, lock and idle timeouts, and value-free errors. | `measurements/upstream.py` |

The profile is `SQLiteProfile()`: WAL, `synchronous=FULL`. The specification allows `synchronous=NORMAL` for bulk
measurements; it is consistent in WAL mode, but a power loss can lose the last committed batch. Pass
`profile=SQLiteProfile(synchronous=Synchronous.NORMAL)` (from `tabrion.sqlite`) to `open_database` if the device
can afford that.

## Tests

`tests/examples/test_measurements.py` and `tests/examples/test_measurements_transfer.py` run inside the normal suite
(`pytest`). The transfer tests run against an upstream SQLite file and, with `TABRION_TEST_POSTGRES` set, against a
PostgreSQL schema of their own (marker `postgres`, fixture `tabrion_postgres_schema`). They cover the RA-B-2
acceptance criteria: every row once in batches; a repeated transfer (lost mark, a second device file with the same
readings) changes nothing; at the moment the mark moves, another connection already sees the batch upstream; a
failed batch keeps the mark and is sent again; a crash between the commit and the mark sends the batch again
without duplicates. The RA-B-1 criteria:
- 10 000 measurements in one write transaction, checked with a statement trace;
- migration with mandatory backup;
- `0600` files;
- `SIGKILL` of a subprocess inside the batch's open transaction, after part of the batch was written. On reopen
  there are zero rows. Killed after the commit, all 10 000 are there.

The file-mode and kill tests are POSIX only.

## Measurement notes for RA-EVAL (planning document 09 section 7)

- **MW-1** (database infrastructure code): `measurements/db.py` (local connection, profile, migrations, backup) and
  `measurements/upstream.py` (upstream database, profile, table, insert) hold all of it; `store.py`, `transfer.py`
  and `cli.py` are domain code. Count `db.py` and `upstream.py` with `cloc`, together with the plumbing lines of the
  tests (the fixtures `store`, `upstream_url` and `upstream`).
- **MW-4** (values in errors): `test_invalid_measurement_writes_nothing` checks that a canary value does not
  appear in the message and that the error has no `__context__`.
- **MW-7** (runtime overhead): `python -m measurements ... demo N` prints the time of one batch of `N` rows.
  Compare it with the baseline of RA-B-4 on the same machine.
- **MW-8** (engine switch): the transfer tests run the same code against SQLite and PostgreSQL; only the URL differs.
- **MW-2 and MW-6** come with RA-B-3 (the fault tests). MW-3 belongs to the parts application. MW-5 is measured
  from this README.
