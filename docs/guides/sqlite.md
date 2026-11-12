# SQLite

`tabrion.sqlite` is the SQLite part of the driver core (layer A). It needs only the standard library and works on
plain `sqlite3.Connection` objects: no wrapper class, no pool, no thread. Every example on this page runs in the test
suite. The contract is [section 3 of the API contract](../api/contract.md#3-tabrionsqlite-standard-library); the
reasons are in [ADR 0005](../adr/0005-sqlite-profile.md).

## Opening a database

```pycon
>>> from tabrion import sqlite
>>> conn = sqlite.connect("shop.db")
>>> sqlite.diagnose(conn).journal_mode, sqlite.diagnose(conn).foreign_keys
('wal', True)
```

`connect` does, in this order:

1. refuses a symlink for the file and for its `-wal`, `-shm` and `-journal` files (`UnsafePath`, code
   `sqlite.symlink`); the error names no path;
2. creates a missing file with `O_CREAT|O_EXCL|O_NOFOLLOW` and mode `0600`, whatever the umask, **before** SQLite
   opens it (SQLite itself would create `0644`);
3. checks that an existing file is a regular file and, on POSIX, that it and its side files belong to the process
   user and are not writable by group or others;
4. opens it with `isolation_level=None` (no transactions behind your back) and applies the profile.

<!-- docs-test: posix -->
```pycon
>>> import os, stat
>>> oct(stat.S_IMODE(os.stat("shop.db").st_mode))
'0o600'
>>> os.symlink("shop.db", "link.db")
>>> sqlite.connect("link.db")
Traceback (most recent call last):
  ...
tabrion.errors.UnsafePath: sqlite.symlink
```

`":memory:"` (and `""`) skip every file step. `read_only=True` opens the file with `mode=ro` and `query_only=ON` and
never creates it; `create=False` raises `FileNotFoundError` for a missing file.

```pycon
>>> sqlite.connect("missing.db", create=False)
Traceback (most recent call last):
  ...
FileNotFoundError: ...
```

## The profile

`SQLiteProfile` holds the settings `connect` and `apply_profile` set. The defaults:

| Field | Default | Why |
|---|---|---|
| `journal_mode` | `WAL` | readers do not block the writer (files only) |
| `synchronous` | `FULL` | durability before throughput |
| `foreign_keys` | `True` | SQLite's own default is off |
| `busy_timeout` | `30.0` seconds | wait for the write lock instead of failing at once |
| `secure_delete` | `True` | deleted content is overwritten |
| `trusted_schema` | `False` | views and triggers cannot call unsafe functions |
| `file_mode` | `0o600` | for new files |
| `allow_shared_file` | `False` | `True` permits a `file_mode` with any group or other bit (even `0o644`, which is only readable) and foreign owners (not recommended) |

Profiles are frozen dataclasses; derive your own with `dataclasses.replace`. Invalid values raise
`ConfigurationError` when the profile is built, not when it is used:

```pycon
>>> import dataclasses
>>> from tabrion.sqlite import SQLiteProfile, Synchronous
>>> bulk = dataclasses.replace(SQLiteProfile(), synchronous=Synchronous.NORMAL, busy_timeout=5.0)
>>> bulk.synchronous
<Synchronous.NORMAL: 'normal'>
>>> SQLiteProfile(file_mode=0o644)
Traceback (most recent call last):
  ...
tabrion.errors.ConfigurationError: sqlite.invalid_profile (field=file_mode)
>>> SQLiteProfile.nodestro().secure_delete, SQLiteProfile.orkexa().secure_delete
(False, True)
```

`SQLiteProfile.orkexa()` and `SQLiteProfile.nodestro()` reproduce the settings of those platforms (plus
`foreign_keys=ON`). For a connection you opened yourself, `apply_profile(conn, profile)` sets the same pragmas; it
refuses to run inside an open transaction, because `foreign_keys` would silently not apply there.

## Transactions

Connections from `connect` never begin a transaction on their own. Use the two context managers:

```pycon
>>> with sqlite.write_transaction(conn):
...     _ = conn.execute("CREATE TABLE parts (id INTEGER PRIMARY KEY, name TEXT NOT NULL, stock INTEGER NOT NULL)")
...     _ = conn.executemany("INSERT INTO parts (name, stock) VALUES (?, ?)", [("bolt", 10), ("nut", 25)])
>>> with sqlite.read_transaction(conn):
...     conn.execute("SELECT name, stock FROM parts ORDER BY id").fetchall()
[('bolt', 10), ('nut', 25)]
```

- `write_transaction` sends `BEGIN IMMEDIATE`: the write lock is taken at the start, so the busy timeout applies and
  a read-modify-write cannot lose an update. It commits at the end of the block and rolls back on any exception,
  `KeyboardInterrupt` included.
- `read_transaction` sends a deferred `BEGIN` (one consistent snapshot) and, with `enforce=True` (the default), sets
  `query_only=ON` for the block, so a write fails:

```pycon
>>> import sqlite3
>>> from tabrion.errors import classify
>>> try:
...     with sqlite.read_transaction(conn):
...         conn.execute("DELETE FROM parts")
... except sqlite3.OperationalError as exc:
...     classify(exc).kind
<Kind.READ_ONLY: 'read_only'>
```

Neither nests: a second `write_transaction` inside the first raises `UsageError("sqlite.nested_transaction")`. If
your code needs reentrancy, keep a counter of your own and call `write_transaction` at the outermost level only.

```pycon
>>> with sqlite.write_transaction(conn):
...     with sqlite.write_transaction(conn):
...         pass
Traceback (most recent call last):
  ...
tabrion.errors.UsageError: sqlite.nested_transaction
```

## Schema migrations with `user_version`

`migrate` runs numbered Python steps and records the version in `PRAGMA user_version`. All pending steps and the
version update run in **one** `BEGIN IMMEDIATE` transaction: either all of them apply or none.

```pycon
>>> def add_price(conn):
...     conn.execute("ALTER TABLE parts ADD COLUMN price_cents INTEGER NOT NULL DEFAULT 0")
>>> def add_index(conn):
...     conn.execute("CREATE INDEX parts_name ON parts (name)")
>>> steps = {1: add_price, 2: add_index}
>>> backups = []
>>> sqlite.migrate(conn, steps, target=2, backup=lambda found, target: backups.append((found, target)))
MigrationResult(previous=0, current=2, backed_up=True)
>>> backups
[(0, 2)]
>>> sqlite.migrate(conn, steps, target=2, backup=None)
MigrationResult(previous=2, current=2, backed_up=False)
```

The rules:

- A database that is not empty needs a backup callback: `backup=None` raises `BackupRequired` unless the database is
  fresh (no tables, or your `is_fresh` hook says so). Pass `sqlite.backup` wrapped in a function, as below.
- A database newer than `target` raises `SchemaTooNew` and stays unchanged; a gap in the steps raises
  `MigrationMissing`. A failing step raises `MigrationFailed` with `details["step"]`, and the database is unchanged.
- Steps use `conn.execute`, one statement each. Never `commit`, `rollback` or `executescript` in a step:
  `executescript` commits first and breaks the all-or-nothing rule. `PRAGMA foreign_keys` has no effect inside a step.
- A step that **rebuilds a table** (create a new table, copy the rows, `DROP TABLE` the old one, rename) deletes the
  rows of child tables through `ON DELETE CASCADE` while foreign keys are on, because SQLite deletes the rows of a
  dropped table first ([Migrations](migrations.md#an-alembic-project-on-tabriondatabase) shows it). Since
  `PRAGMA foreign_keys` has no effect inside the migration transaction, migrate on a **dedicated** connection opened
  with `SQLiteProfile(foreign_keys=False)` and close it afterwards; the application's connections keep foreign keys
  on. Check the references **inside** the migration: `migrate` commits before it returns, so a
  `foreign_key_check` after it would only find a violation that is already committed. Wrap the steps so that each
  one ends with `sqlite.foreign_key_check(conn)` and raises when the list is not empty; `migrate` then rolls back
  (example below).
- Two processes that migrate at the same time apply each step once; the second sees the new version.

```pycon
>>> sqlite.migrate(conn, steps, target=1, backup=None)
Traceback (most recent call last):
  ...
tabrion.errors.SchemaTooNew: sqlite.schema_too_new (found=2, supported=1)
>>> def broken(conn):
...     conn.execute("ALTER TABLE parts ADD COLUMN note TEXT")
...     raise RuntimeError("step failed")
>>> sqlite.migrate(conn, {**steps, 3: broken}, target=3, backup=lambda found, target: None)
Traceback (most recent call last):
  ...
tabrion.errors.MigrationFailed: sqlite.migration_failed (found=2, supported=3, step=3)
>>> [row[1] for row in conn.execute("PRAGMA table_info(parts)")]
['id', 'name', 'stock', 'price_cents']
```

A migration with foreign keys off, checked before the commit:

```pycon
>>> def checked(step):
...     def run(conn):
...         step(conn)
...         if sqlite.foreign_key_check(conn):
...             raise RuntimeError("foreign key violations")
...
...     return run
>>> def create_orders(conn):
...     conn.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY)")
...     conn.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers (id))")
...     conn.execute("INSERT INTO customers VALUES (1)")
...     conn.execute("INSERT INTO orders VALUES (10, 1)")
>>> def drop_customers(conn):
...     conn.execute("DELETE FROM customers")
>>> order_steps = {1: checked(create_orders), 2: checked(drop_customers)}
>>> migration_conn = sqlite.connect("orders.db", profile=sqlite.SQLiteProfile(foreign_keys=False))
>>> sqlite.migrate(migration_conn, order_steps, target=1, backup=None)
MigrationResult(previous=0, current=1, backed_up=False)
>>> sqlite.migrate(migration_conn, order_steps, target=2, backup=lambda found, target: None)
Traceback (most recent call last):
  ...
tabrion.errors.MigrationFailed: sqlite.migration_failed (found=1, supported=2, step=2)
>>> migration_conn.execute("SELECT count(*) FROM customers").fetchone()
(1,)
>>> migration_conn.close()
```

## Backups

`backup` copies a live database with SQLite's online backup API into a `0600` temporary file next to the target,
checks the copy with `quick_check`, and renames it into place atomically. It refuses an existing target unless
`overwrite=True`, and it refuses to run inside an open transaction (the copy would contain uncommitted data).

```pycon
>>> result = sqlite.backup(conn, "shop-backup.db")
>>> result.quick_check, result.path.name
('ok', 'shop-backup.db')
>>> sqlite.backup(conn, "shop-backup.db")
Traceback (most recent call last):
  ...
FileExistsError: ...
```

A migration with a backup before it:

```python
from pathlib import Path


def backup_before_migration(found: int, target: int) -> None:
    sqlite.backup(conn, Path(f"shop-v{found}.db"), overwrite=True)
```

```pycon
>>> def add_note(conn):
...     conn.execute("ALTER TABLE parts ADD COLUMN note TEXT")
>>> sqlite.migrate(conn, {**steps, 3: add_note}, target=3, backup=backup_before_migration)
MigrationResult(previous=2, current=3, backed_up=True)
>>> backup_conn = sqlite.connect("shop-v2.db", read_only=True)
>>> backup_conn.execute("PRAGMA user_version").fetchone()
(2,)
>>> backup_conn.close()
```

There is no `restore()`: replacing an open database is product specific. Close every connection, move the backup
file into place, and open again.

## Integrity checks and diagnosis

```pycon
>>> sqlite.quick_check(conn)
IntegrityReport(ok=True, messages=(), truncated=False)
>>> sqlite.integrity_check(conn).ok
True
>>> sqlite.foreign_key_check(conn)
[]
>>> diagnosis = sqlite.diagnose(conn)
>>> diagnosis.user_version, diagnosis.busy_timeout_ms, diagnosis.trusted_schema
(3, 30000, False)
```

The checks only read. A damaged file gives `ok=False` with SQLite's messages (page and table names, never row data)
rather than an exception. `diagnose` reports the pragmas, the file mode and the WAL size; `tabrion.check` turns it
into findings ([Diagnosis](check.md)).

## Extensions

Extension loading is off. Enable it only for the load itself:

```python
try:
    with sqlite.loading_extensions(conn):
        pass  # conn.load_extension("...") goes here
except Exception as exc:  # builds without extension support raise UsageError("sqlite.extensions_unavailable")
    assert getattr(exc, "code", None) == "sqlite.extensions_unavailable", exc
```

The context manager turns loading off again on every exit.

## Threads

A connection has no lock of its own (`check_same_thread=False`). Serialize its use yourself, or give each thread its
own connection; with `BEGIN IMMEDIATE` and the busy timeout, several connections to one file write safely.

```pycon
>>> conn.close()
```
