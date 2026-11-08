# Measurements baseline: the same application with plain SQLAlchemy

Reference application B, comparison variant (TABRION RA-B-4). It has the same features as
[`examples/measurements`](../measurements/README.md): capture on a local SQLite file, then an idempotent transfer
upstream. It is built **without Tabrion**, on SQLAlchemy alone (plus `psycopg` for PostgreSQL). RA-EVAL measures
Tabrion's value against it (planning document 09 section 7, metrics MW-1..MW-8).

## Run it

From a checkout with SQLAlchemy and psycopg installed (`pip install -e ".[dev]"` brings both):

```console
$ cd examples/measurements-baseline
$ python -m measurements_baseline --db data.db --backup-dir backups init
$ python -m measurements_baseline --db data.db --backup-dir backups demo 10000
10000 stored in 0.105 s
$ export MEASUREMENTS_UPSTREAM_URL_FILE=upstream-url   # a file holding postgresql://user:password@host/db
$ python -m measurements_baseline --db data.db --backup-dir backups init-upstream
upstream ready
$ python -m measurements_baseline --db data.db --backup-dir backups transfer
10000 sent in 10 batches, 10000 new upstream, mark 10000
```

## The rules of the comparison

- **The same layout.** Each file has the same name and the same job in both variants:
  - `db.py` and `upstream.py` are the database infrastructure, which MW-1 counts;
  - `store.py`, `transfer.py` and `cli.py` are the domain code.

  The domain code is the same in both, line for line, except where it touches the database handle. That is
  validation, the natural key, the high-water mark rule and the CLI.
- **The same features.** The baseline implements everything the application specifies, in the way the SQLAlchemy
  documentation shows it:
  - WAL, `synchronous=FULL` and foreign keys from a `connect` event;
  - `BEGIN IMMEDIATE` through the documented pysqlite recipe;
  - migrations by `user_version`, with a mandatory backup through `sqlite3`'s backup API;
  - an `INSERT ... ON CONFLICT DO NOTHING RETURNING` per batch;
  - the mark moved only after the commit;
  - the URL from an environment variable or a `_FILE`.
- **No extras.** The baseline adds none of the cross-cutting guarantees that Tabrion gives without extra code.
  Where it lacks one, the shared test suite shows it as a deviation.

## The same test suite

`tests/examples/test_measurements*.py` runs every test against both variants. The fixture `app` and the adapter in
`tests/examples/measurements_variants.py` provide this. The adapter reaches the database handle, which is a
`sqlite3.Connection` and a `tabrion.Database` in Tabrion and two `sqlalchemy.Engine` in the baseline.

The baseline passes every functional test, including the RA-B-3 fault tests (0 losses, 0 duplicates). It fails
the probes listed in `DEVIATIONS` in that module. They run as strict `xfail`, so a baseline that suddenly passes
one fails the run:

| Test | Metric | What the baseline lacks |
|---|---|---|
| `test_files_are_owner_only` | MW-2 (file modes) | Database, WAL files and backups get `0644` under umask `022`, and the backup directory `0755`. Tabrion gives `0600` and `0700`. |
| `test_cli_transfer_errors_name_no_values` | MW-4 (values in errors) | A failed batch prints SQLAlchemy's message, which quotes the statement's parameters, that is, the readings. |
| `test_an_upstream_error_is_classified` | MW-2 (error model) | A refused connection is an `OperationalError` without a classification; there is no stable error code. |
| `test_a_lost_commit_reply_is_reported_as_outcome_unknown` | MW-2 (error model) | A lost `COMMIT` answer cannot be told from any other connection error. |
| `test_capture_runs_without_the_transfer_modules` | edge suitability | The capture needs SQLAlchemy; Tabrion's capture needs the standard library only. |

The baseline also lacks the following, which no test of this suite probes; RA-EVAL's S1–S7 and P1–P7 probes do:

- the refusal of symbolic links;
- a verified, atomic backup;
- the TLS rule for remote hosts;
- server-side statement, lock and idle timeouts;
- `PRAGMA secure_delete`.

## Measurement notes for RA-EVAL

- **MW-1.** Code lines without blank lines, comments and docstrings, at the commit of RA-B-4:

  | Part | Tabrion | Baseline |
  |---|---|---|
  | `db.py` | 54 | 111 |
  | `upstream.py` | 36 | 55 |
  | **Infrastructure** | **90** | **166** |

  Tabrion's infrastructure is 46 % smaller. Even so, the baseline still lacks the guarantees listed above. RA-EVAL
  counts with `cloc` on the same files.
- **MW-6.** Both variants: 0 losses and 0 duplicates in every fault test. The rule (idempotent insert, mark after
  the commit) is the application's, not the library's.
- **MW-7.** `python -m <package> ... demo N` times one batch of `N` rows in each variant on the same machine.
- **MW-8.** Both variants run the transfer tests unchanged against SQLite and PostgreSQL.
