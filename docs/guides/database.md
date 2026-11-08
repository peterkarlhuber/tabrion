# Database and Profile (layer B)

Layer B is `tabrion.Database`: a thin wrapper around a SQLAlchemy `Engine` that applies the layer-A profile to every
connection of the pool and gives you transactions as context managers. It needs the extra `sqlalchemy`
(`tabrion[sqlalchemy]`), and `tabrion[postgres,sqlalchemy]` for PostgreSQL. Contract:
[section 5](../api/contract.md#5-layer-b-tabrion-extra-sqlalchemy); how the layers fit together:
[ADR 0002](../adr/0002-two-layers-one-distribution.md).

## Creating a database

```pycon
>>> from tabrion import Database, Profile
>>> db = Database.from_url("sqlite:///inventory.db")
>>> db.dialect
'sqlite'
>>> db
<tabrion.Database sqlite+pysqlite:///inventory.db>
```

The three factories:

- `Database.from_url(url, *, profile=None, engine_options=None)`: a SQLite or PostgreSQL URL. `sqlite:///path` (a
  file, created `0600`, symlinks refused), `sqlite://` (memory, one shared connection), `postgresql://...` or
  `postgresql+psycopg://...` (`postgresql://` means psycopg 3). Other drivers raise
  `ConfigurationError("config.driver")`.
- `Database.from_env(name="DATABASE_URL", ...)`: the URL from the environment or a `*_FILE` secret
  ([Connection URLs and secrets](config.md)).
- `Database.from_engine(engine, *, profile=None, install_listeners=True)`: an engine you already have. Tabrion adds its
  listeners to it, and `close()` removes them but does not dispose the engine.

```pycon
>>> Database.from_url("mysql://db.example.com/shop")
Traceback (most recent call last):
  ...
tabrion.errors.ConfigurationError: config.driver
>>> import os
>>> os.environ["INVENTORY_URL"] = "sqlite:///inventory.db"
>>> with Database.from_env("INVENTORY_URL") as other:
...     other.dialect
'sqlite'
```

A `Database` is thread-safe. Close it with `close()` or use it as a context manager; an unclosed one emits a
`ResourceWarning` when it is collected. After `close()` every method raises `UsageError("database.closed")`.

## Transactions

```pycon
>>> with db.transaction() as tx:
...     tx.execute("CREATE TABLE parts (id INTEGER PRIMARY KEY, name TEXT NOT NULL, stock INTEGER NOT NULL)")
...     tx.execute(
...         "INSERT INTO parts (name, stock) VALUES (:name, :stock)",
...         [{"name": "bolt", "stock": 10}, {"name": "nut", "stock": 25}],
...     )
0
2
>>> with db.read() as tx:
...     tx.fetch_scalar("SELECT sum(stock) FROM parts")
35
```

- `transaction()` commits at the end of the block and rolls back on any exception. On SQLite it begins with
  `BEGIN IMMEDIATE`; on PostgreSQL with `BEGIN`, optionally `isolation="repeatable_read"` or `"serializable"`.
- `read()` is a read-only transaction: `query_only` on SQLite, `BEGIN READ ONLY` on PostgreSQL. A write in it raises
  `ReadOnlyViolation`.
- Statements are SQL text with named parameters (`:name`), or any SQLAlchemy Core executable. Write
  `CAST(:x AS int)` rather than `:x::int` (the `::` cast collides with the parameter syntax).
- A sequence of parameter mappings runs the statement once per mapping (`executemany`).

The methods of a `Transaction`:

| Method | Returns |
|---|---|
| `execute(statement, params=None, *, timeout=None)` | the row count (`0` for DDL) |
| `fetch_one(statement, params=None, *, timeout=None)` | one `Row` or `None`; more than one row raises `MultipleRowsError` |
| `fetch_all(statement, params=None, *, timeout=None)` | a list of `Row` |
| `fetch_scalar(statement, params=None, *, timeout=None)` | the first column of the only row, or `None` |
| `connection` | the SQLAlchemy `Connection`, for what the methods above do not cover |

`Database` has the same four as short forms: `db.execute` runs in its own `transaction()`, the `fetch_*` methods in
their own `read()`.

```pycon
>>> row = db.fetch_one("SELECT id, name FROM parts WHERE name = :name", {"name": "nut"})
>>> row.id, row.name
(2, 'nut')
>>> db.fetch_one("SELECT id FROM parts WHERE name = :name", {"name": "washer"}) is None
True
>>> db.fetch_one("SELECT id FROM parts")
Traceback (most recent call last):
  ...
tabrion.errors.MultipleRowsError: database.multiple_rows
>>> with db.read() as tx:
...     tx.execute("DELETE FROM parts")
Traceback (most recent call last):
  ...
tabrion.errors.ReadOnlyViolation: database.read_only: ...
```

Transactions do not nest. Inside an open transaction of the same `Database`, in the same thread, another
`transaction()` or `read()` raises `UsageError("database.nested_transaction")`; pass the `Transaction` down instead.

```pycon
>>> with db.transaction() as tx:
...     db.fetch_scalar("SELECT count(*) FROM parts")
Traceback (most recent call last):
  ...
tabrion.errors.UsageError: database.nested_transaction
```

SQLAlchemy Core works too:

```pycon
>>> import sqlalchemy as sa
>>> parts = sa.table("parts", sa.column("id"), sa.column("name"), sa.column("stock"))
>>> [tuple(r) for r in db.fetch_all(sa.select(parts.c.name, parts.c.stock).order_by(parts.c.name))]
[('bolt', 10), ('nut', 25)]
```

## Timeouts

`timeout` (seconds) bounds a statement, or with `transaction(timeout=...)` / `read(timeout=...)` every statement of
the block. On PostgreSQL it is `SET LOCAL statement_timeout`; on SQLite a deadline checked while the statement runs.
Expiry raises `StatementTimeout`.

```pycon
>>> slow = "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r) SELECT count(*) FROM r"
>>> db.fetch_scalar(slow, timeout=0.05)
Traceback (most recent call last):
  ...
tabrion.errors.StatementTimeout: database.timeout: ...
```

Two waits are not bounded by `timeout`: on SQLite a wait for the write lock is bounded by the profile's busy timeout,
and a wait for a free pool connection by `Profile.pool_timeout` (also in `health(timeout=...)`).

## Errors

Driver errors become `DatabaseError` subclasses with a stable code (`database.<kind>`), the classification, and the
SQL text without parameters; the original driver error is in `.original` and never chained. See
[Errors](errors.md#errors-tabrion-raises).

```pycon
>>> from tabrion.errors import IntegrityViolation
>>> try:
...     db.execute("INSERT INTO parts (name, stock) VALUES (:name, NULL)", {"name": "washer"})
... except IntegrityViolation as exc:
...     exc.code, exc.classification.column
('database.not_null', 'stock')
```

## Profiles

`Profile` bundles the SQLite profile, the PostgreSQL session settings and the pool settings. The presets:

| Preset | What changes |
|---|---|
| `Profile.default()` | the defaults below |
| `Profile.readonly()` | `read_only=True`: SQLite opens `mode=ro` with `query_only`; PostgreSQL sets `default_transaction_read_only`; `transaction()` and `execute` raise `UsageError("database.read_only")` |
| `Profile.edge()` | SQLite `synchronous=NORMAL`, `secure_delete=False` (devices, bulk capture) |
| `Profile.testing()` | statement timeout 5 s, lock timeout 2 s, SQLite busy timeout 2 s, pool size 2 |

| Field | Default |
|---|---|
| `sqlite` | `SQLiteProfile()` ([SQLite](sqlite.md#the-profile)) |
| `postgres` | `SessionSettings()` ([PostgreSQL](postgresql.md#session-settings)) |
| `application_name` | `"tabrion"` (replaces the one in `postgres`) |
| `pool_size`, `max_overflow` | `5`, `0` |
| `pool_timeout`, `pool_pre_ping`, `pool_recycle` | `10.0` s, `True`, `1800.0` s |
| `connect_timeout` | `10.0` s |
| `tls` | `TLSMode.AUTO` |
| `max_rows` | `10_000` (for `query()` from 0.2) |
| `read_only` | `False` |

Derive your own with `dataclasses.replace`; invalid values raise `ConfigurationError("config.invalid_profile")`.

```pycon
>>> import dataclasses
>>> profile = dataclasses.replace(Profile.default(), application_name="inventory", pool_size=10)
>>> profile.application_name, profile.pool_size
('inventory', 10)
>>> Profile(pool_size=0)
Traceback (most recent call last):
  ...
tabrion.errors.ConfigurationError: config.invalid_profile (field=pool_size)
>>> with Database.from_url("sqlite:///inventory.db", profile=Profile.readonly()) as reader:
...     print(reader.fetch_scalar("SELECT count(*) FROM parts"))
...     reader.execute("DELETE FROM parts")
Traceback (most recent call last):
  ...
tabrion.errors.UsageError: database.read_only
```

`read()` and `Profile.readonly()` guard against accidents, not against hostile SQL: SQL you run can switch read-only
off. Tabrion reads the state back before the `COMMIT` and raises `UsageError("database.read_only")` (invalidating the
connection) when it was lifted, but SQL that lifts it, writes and restores it before the end is committed, and
`from_engine` gets no `mode=ro` and no `ATTACH` limit on SQLite ([Security](security.md#read-only-is-a-guard-not-a-boundary),
contract [I-56](../api/contract.md#57-implementation-notes-tab-018)). The hard boundary is a PostgreSQL role without write rights, or file permissions on SQLite.

## TLS for PostgreSQL

`Profile.tls` decides what `from_url` accepts for a remote host (not `localhost`, a loopback address or a Unix
socket):

| `tls` | Remote host |
|---|---|
| `auto` (default) | a missing `sslmode` becomes `require`; `disable`, `allow` or `prefer` raise `ConfigurationError("config.tls")` |
| `require` | the same for every host, local ones included |
| `verify-full` | `sslmode=verify-full`, set when missing; any other value is refused |
| `insecure` | nothing is checked (local development only; `tabrion.check` reports TAB-PG-011) |

```pycon
>>> remote = Database.from_url("postgresql://app@db.example.com/shop")
>>> remote.safe_url
'postgresql+psycopg://app@db.example.com/shop?sslmode=require'
>>> remote.close()
>>> Database.from_url("postgresql://app@db.example.com/shop?sslmode=disable")
Traceback (most recent call last):
  ...
tabrion.errors.ConfigurationError: config.tls
```

`from_url` connects to nothing; the first statement does.

## Health and diagnosis

```pycon
>>> health = db.health()
>>> health.ok, health.dialect
(True, 'sqlite')
>>> db.health(deep=True).ok
True
>>> [finding.code for finding in db.check()]
[]
```

`health(timeout=5.0, deep=False)` never raises for database errors: a failure is `ok=False` with a classification.
It uses a connection of the pool (PostgreSQL: `tabrion.postgres.ping`); `deep=True` adds `PRAGMA quick_check(1)` on
SQLite. `check()` is [`tabrion.check`](check.md) for this database.

## Using the engine directly

`db.engine` is the SQLAlchemy engine, for Alembic, ORM sessions or anything else. Its connections get the profile
(pragmas, session settings), but not Tabrion's transaction modes: on SQLite a write through `db.engine.connect()`
begins `DEFERRED`, not `IMMEDIATE`. Prefer `db.transaction()`; for Alembic on SQLite use `render_as_batch=True`
([Migrations](migrations.md)).

A `SET` without `LOCAL` inside a transaction stays on the pooled connection for every later checkout, because
Tabrion runs no `RESET`/`DISCARD`. Use `SET LOCAL` or `set_config(..., true)`.

```pycon
>>> db.close()
>>> db.closed
True
>>> db.fetch_scalar("SELECT 1")
Traceback (most recent call last):
  ...
tabrion.errors.UsageError: database.closed
```
