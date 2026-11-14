# Troubleshooting

Look up the `code` of the `TabrionError` you got, or the `kind` of `classify(exc)`. The full lists are in the
[API contract](../api/contract.md); this page covers the ones people meet first.

## SQLite

| Code or kind | Cause | Fix |
|---|---|---|
| `sqlite.symlink` | the path or a `-wal`/`-shm`/`-journal` file is a symlink | open the real path; Tabrion never follows links |
| `sqlite.unsafe_owner`, `sqlite.unsafe_mode` | the existing file belongs to another user, or group/others may write it | `chmod 600` and `chown` the file and its side files; `allow_shared_file=True` only if you really share it |
| `sqlite.unsafe_path` | not a regular file (directory, FIFO, device), or the file changed while it was opened | check the path |
| `sqlite.nested_transaction` | `write_transaction`/`read_transaction`/`migrate` inside an open transaction | end the transaction first, or keep a reentrancy counter and open Tabrion's transaction only at the outermost level |
| `sqlite.not_autocommit` | a connection with driver-managed transactions (`isolation_level` not `None`) | open it with `tabrion.sqlite.connect`, or set `isolation_level = None` |
| `sqlite.transaction_open` | `apply_profile` or `backup` inside a transaction | call it between transactions |
| `sqlite.backup_required` | `migrate` on a non-empty database with `backup=None` | pass a backup callback |
| `sqlite.schema_too_new` | the file was migrated by a newer version of your application | upgrade the application; never downgrade a file in place |
| kind `locked` | another connection holds the write lock longer than the busy timeout | keep write transactions short; raise `busy_timeout`; it is retryable |
| kind `read_only` | a write in `read_transaction`/`read()`, or a file opened read-only | write in a write transaction |

`locked` is what a waiting writer gets when the lock does not come free in time:

```pycon
>>> import dataclasses
>>> import sqlite3
>>> from tabrion import sqlite
>>> from tabrion.errors import classify
>>> writer = sqlite.connect("busy.db")
>>> impatient = sqlite.connect("busy.db", profile=dataclasses.replace(sqlite.SQLiteProfile(), busy_timeout=0.0))
>>> with sqlite.write_transaction(writer):
...     try:
...         with sqlite.write_transaction(impatient):
...             pass
...     except sqlite3.OperationalError as exc:
...         c = classify(exc)
...         print(c.kind, c.retryable, c.sqlite_code)
locked True SQLITE_BUSY
>>> impatient.close()
>>> writer.close()
```

## PostgreSQL

| Code or kind | Cause | Fix |
|---|---|---|
| `config.tls` | a remote host with `sslmode=disable`/`allow`/`prefer`, or conflicting `sslmode` values | use TLS; `Profile(tls=TLSMode.INSECURE)` only for local development |
| `config.driver` | a URL for another driver (`postgresql+psycopg2://`, `mysql://`) | use `postgresql://` or `postgresql+psycopg://` with psycopg 3 |
| `postgres.role_unsafe` | `check_role` or `grant_runtime_role`: superuser, `BYPASSRLS` or table owner (`details["reasons"]`) | connect as the runtime role; run migrations as the owner |
| `postgres.role_missing` | `inspect_role`/`grant_runtime_role` for a role that does not exist | create the role first |
| `postgres.nested_transaction` | `grant_runtime_role` inside an open transaction | call it on an idle connection |
| `postgres.lock_in_transaction` | `wait_advisory_lock` inside a transaction (it would hold a snapshot) | wait on an idle connection |
| `postgres.invalid_setting` | a `SessionSettings` value out of range, a `search_path` that is not one plain schema name | fix the value |
| kind `timeout` | `statement_timeout` (30 s by default) | make the statement faster or pass a larger `timeout` for it |
| kind `locked` | `lock_timeout` (10 s by default) | find the blocking session in `pg_stat_activity`; retry the transaction |
| `outcome_unknown` | the connection broke during the statement or `COMMIT` | do not retry blindly: read back or use an idempotency key ([Transactions](transactions.md#unknown-outcomes)) |

## Layer B

| Code | Cause | Fix |
|---|---|---|
| `database.nested_transaction` | `transaction()`/`read()`/a short form inside an open transaction of the same `Database` in the same thread | pass the `Transaction` down |
| `database.read_only` | a write under `Profile.readonly()`, or read-only switched off inside `read()` | use a writable profile and `transaction()` |
| `database.closed` | the `Database` was closed | keep it open for the application's lifetime |
| `database.wrong_thread`, `database.transaction_ended` | a `Transaction` used from another thread or after its block | use it only inside its `with` block |
| `database.invalid_statement` | SQLAlchemy refused the statement before sending it (a missing parameter, a value its type rejects) | check the parameters |
| `database.multiple_rows` | `fetch_one`/`fetch_scalar` matched several rows | add a condition or use `fetch_all` |
| `config.engine_option` | `engine_options` tried to replace what Tabrion controls (`creator`, `poolclass`, `isolation_level`) | leave those to Tabrion |
| `migrations.not_current` | `require_current`: pending migrations, or a database newer than the code | run `alembic upgrade head`, or deploy the newer code |

```pycon
>>> from tabrion import Database
>>> with Database.from_url("sqlite://") as db:
...     db.fetch_scalar("SELECT :a + :b", {"a": 1})
Traceback (most recent call last):
  ...
tabrion.errors.UsageError: database.invalid_statement
```

## Imports

`ImportError: tabrion.Database needs the extra "sqlalchemy"` and the like: install the extra named in the message,
**in the Git-pin form** of [Installation](../getting-started/installation.md#extras)
(`pip install "tabrion[sqlalchemy] @ git+https://github.com/peterkarlhuber/tabrion@<commit-sha>"`), as the message
shows, with the full commit your project pins. Never drop the `@ git+...` part: Tabrion is not on PyPI, and a package
of that name there would not be ours (TODO TB-132). `import tabrion`, `tabrion.errors`, `tabrion.sql`,
`tabrion.config`, `tabrion.sqlite` and `tabrion.check` never need an extra.
