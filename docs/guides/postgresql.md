# PostgreSQL

<!-- docs-test: postgres -->

`tabrion.postgres` is the PostgreSQL part of the driver core (layer A). It works on plain psycopg 3 connections that
you open, pool and close yourself, and needs the extra `postgres` (`tabrion[postgres]`, psycopg 3.2 or later; install
`psycopg[binary]` or a system libpq next to it). Contract:
[section 4](../api/contract.md#4-tabrionpostgres-extra-postgres-psycopg-32-or-later); the reasons:
[ADR 0006](../adr/0006-postgres-session-profile.md).

The examples on this page run against a test server in the pg CI jobs. `DATABASE_URL` is a `postgresql://` URL of a
role that owns an empty schema, which is first in its `search_path`; `APP_ROLE` and `APP_DATABASE_URL` are a second
LOGIN role without privileges and its URL.

```pycon
>>> import os
>>> import psycopg
>>> from tabrion import postgres
>>> conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)
```

## Two rules for every function

- **Tabrion never ends a transaction it did not open** (T-NOTX). On an idle connection a read-only function opens a
  short transaction and rolls it back; inside your transaction it runs in it and leaves it open.
- **Tabrion sets only session settings from a fixed list** (`statement_timeout`, `lock_timeout`,
  `idle_in_transaction_session_timeout`, `transaction_timeout`, `application_name`, `search_path`, `TimeZone`). It
  never sets `role`, `row_security` or `app.*` settings and never runs `RESET ALL` or `DISCARD`, so row-level security
  contexts of your application stay yours. Inside its own transactions, layer B (`tabrion.Database`) also sends
  `SET LOCAL statement_timeout` for a `timeout=` and the isolation level you ask for; both end with the transaction.

## Session settings

`SessionSettings` holds the session profile, in **milliseconds** (PostgreSQL's unit). The defaults are finite
timeouts, an `application_name` and UTC:

| Field | Default |
|---|---|
| `statement_timeout_ms` | `30_000` |
| `lock_timeout_ms` | `10_000` |
| `idle_in_transaction_timeout_ms` | `300_000` |
| `transaction_timeout_ms` | `None` (PostgreSQL 17 or later; skipped on older servers) |
| `application_name` | `"tabrion"` |
| `search_path` | `None` (one schema name, sent quoted) |
| `timezone` | `"UTC"` |

`None` leaves a setting alone; `SessionSettings.unchanged()` sets nothing at all. `configure` sends the settings in
one round trip:

```pycon
>>> settings = postgres.SessionSettings(statement_timeout_ms=5_000, application_name="inventory")
>>> postgres.configure(conn, settings)
>>> conn.execute("SHOW statement_timeout").fetchone()[0], conn.execute("SHOW application_name").fetchone()[0]
('5s', 'inventory')
```

`configure` never commits. With autocommit (as here) the settings apply at once; without it, the `SELECT` opens a
transaction that **you** commit, or a later rollback discards the settings.

For a pool, `session_configurator(settings)` returns the `configure` callback of `psycopg_pool.ConnectionPool`. It
validates the settings once, when it is created, and commits when it opened the transaction itself. `psycopg_pool` is
not part of `tabrion[postgres]`; install it as `psycopg[pool]` (or `psycopg-pool`) next to Tabrion:

```pycon
>>> from psycopg_pool import ConnectionPool
>>> pool = ConnectionPool(
...     os.environ["DATABASE_URL"],
...     min_size=1,
...     max_size=2,
...     open=True,
...     configure=postgres.session_configurator(postgres.SessionSettings(application_name="inventory-pool")),
... )
>>> with pool.connection() as pooled:
...     pooled.execute("SHOW application_name").fetchone()[0]
'inventory-pool'
>>> postgres.session_configurator(postgres.SessionSettings(search_path="public; DROP TABLE x"))
Traceback (most recent call last):
  ...
tabrion.errors.ConfigurationError: postgres.invalid_setting (setting=search_path)
```

Configure only connections you own. A pool that belongs to another component keeps its settings; Tabrion's functions
work on it without configuring it.

## Health: `ping` and `diagnose`

```pycon
>>> result = postgres.ping(pool, timeout=2.0)
>>> result.ok, result.in_recovery, result.server_version >= 160000
(True, False, True)
>>> pool.close()
```

`ping(source, *, timeout=5.0)` takes a connection or a pool and **never raises** for database errors: a stopped
server is `ok=False` with a `classification`. `diagnose(conn)` reads what the findings of `tabrion.check` are built
from; fields the role may not read are `None`.

```pycon
>>> d = postgres.diagnose(conn)
>>> d.statement_timeout_ms, d.lock_timeout_ms, d.application_name
(5000, 10000, 'inventory')
>>> d.schema == d.search_path
True
```

## Extensions

`extension_status(conn, name, *, probe_type=None)` says whether an extension is usable from the current
`search_path`, and which command would fix it. It changes nothing.

```pycon
>>> postgres.extension_status(conn, "surely_not_installed").state
'unavailable'
>>> status = postgres.extension_status(conn, "plpgsql")
>>> status.state, status.command
('active', None)
```

The states are `active`, `other_schema` (installed, schema not visible), `installable` and `unavailable`;
`probe_type` (for example `"vector"`) additionally requires that the type resolves.

## Roles

The role your application runs as should be a **runtime role**: no superuser, no `BYPASSRLS`, no table ownership.
`role_info` reports it; `check_role` raises `RoleUnsafe` when it is not restricted:

```pycon
>>> app = psycopg.connect(os.environ["APP_DATABASE_URL"], autocommit=True)
>>> postgres.role_info(app).restricted
True
>>> postgres.check_role(app).role == os.environ["APP_ROLE"]
True
>>> info = postgres.role_info(conn)
>>> info.table_owner
False
>>> _ = conn.execute("CREATE TABLE parts (id int PRIMARY KEY, material text NOT NULL)")
>>> _ = conn.execute("CREATE TABLE app_schema_migrations (version int PRIMARY KEY)")
>>> postgres.role_info(conn).table_owner
True
>>> postgres.check_role(conn, allow_superuser=True, allow_table_owner=True).table_owner
True
```

`owner_scope="schema"` (default) counts every table in `current_schema()`; `owner_scope="rls"` only tables with
row-level security.

`grant_runtime_role(owner_conn, role, *, schema=None, protected_tables=(), grant_connect=True)` gives a runtime role
DML rights on a schema, now and for future objects, and revokes writes on bookkeeping tables. It refuses (and grants
nothing) when the role is the owner, a superuser, has `BYPASSRLS` or owns tables. It needs a transaction of its own,
so it refuses to run inside yours.

```pycon
>>> report = postgres.grant_runtime_role(conn, os.environ["APP_ROLE"], protected_tables=["app_schema_migrations"])
>>> report.restricted, report.tables, report.owned_tables
(True, 2, ())
>>> _ = app.execute("INSERT INTO parts VALUES (1, 'aluminum')")
>>> app.execute("INSERT INTO app_schema_migrations VALUES (1)")
Traceback (most recent call last):
  ...
psycopg.errors.InsufficientPrivilege: permission denied for table app_schema_migrations
>>> sorted(postgres.inspect_role(conn, os.environ["APP_ROLE"]).as_dict())
['bypass_rls', 'can_create', 'connect', 'database', 'owned_tables', 'owner', 'restricted', 'role', 'schema', 'superuser', 'tables']
```

The function is idempotent; run it after every migration. `inspect_role` reads the same report without granting.

## Errors without values

`classify` and `safe_message` from [`tabrion.errors`](errors.md) work on psycopg errors. The PostgreSQL `DETAIL`
line holds the key value; the safe message does not:

```pycon
>>> from tabrion.errors import classify, safe_message
>>> material = "titanium"
>>> _ = conn.execute("CREATE UNIQUE INDEX parts_material ON parts (material)")
>>> _ = conn.execute("INSERT INTO parts VALUES (2, %s)", (material,))
>>> try:
...     conn.execute("INSERT INTO parts VALUES (3, %s)", (material,))
... except psycopg.Error as exc:
...     error = exc
>>> material in str(error)
True
>>> c = classify(error)
>>> c.kind, c.sqlstate, c.constraint
(<Kind.UNIQUE: 'unique'>, '23505', 'parts_material')
>>> safe_message(error)
'unique violation (SQLSTATE 23505, constraint parts_material, table parts)'
```

## Advisory locks

`lock_key(name)` turns a name into a stable 64-bit key (the first eight bytes of SHA-256). Existing integer keys stay
valid.

```pycon
>>> postgres.lock_key("inventory-migrations")
-5209280746044447427
```

On a connection you hold, `try_advisory_lock`, `wait_advisory_lock` and `advisory_unlock` take session-level locks.
`wait_advisory_lock` **polls** `pg_try_advisory_lock` instead of blocking, so no statement and no snapshot stay open
while it waits (a blocking wait would deadlock with `CREATE INDEX CONCURRENTLY` in another session). It refuses to run
inside a transaction.

```pycon
>>> key = postgres.lock_key("inventory-migrations")
>>> postgres.wait_advisory_lock(conn, key, timeout=5.0)
True
>>> postgres.try_advisory_lock(app, key)
False
>>> postgres.advisory_unlock(conn, key)
True
>>> postgres.try_advisory_lock(app, key), postgres.advisory_unlock(app, key)
(True, True)
```

`SessionLock(conninfo)` is a leader lock on a connection of its own (unpooled, autocommit, opened lazily). It counts
repeated acquisitions, probes the connection before each operation, and when the connection is gone, every lock is
gone too: `holds()` then returns `False`. It is thread-safe; after `os.fork()` the child opens a connection of its own.

```pycon
>>> with postgres.SessionLock(os.environ["DATABASE_URL"]) as lock:
...     print(lock.try_acquire(key), lock.try_acquire(key), lock.holds(key), postgres.try_advisory_lock(app, key))
...     lock.release(key)
...     print(lock.holds(key))  # still held once
...     lock.release(key)
...     print(lock.holds(key), postgres.try_advisory_lock(app, key), postgres.advisory_unlock(app, key))
True True True False
True
False True True
```

Closing a `SessionLock` closes its connection, which ends every lock it held; the server notices the closed
connection shortly afterwards.

`acquire(key, timeout=...)` polls like `wait_advisory_lock`. A `SessionLock` configures its connection with
`application_name="tabrion-lock"` unless you pass `configure=`.

## Layer B on PostgreSQL

`tabrion.Database` applies `SessionSettings` to every connection of its pool (with `Profile.application_name`) and
maps `read()` to `BEGIN READ ONLY` ([Database and Profile](database.md)):

```pycon
>>> from tabrion import Database
>>> from tabrion.errors import ReadOnlyViolation
>>> db = Database.from_env("DATABASE_URL")
>>> db.dialect
'postgresql'
>>> with db.transaction(isolation="repeatable_read") as tx:
...     tx.fetch_scalar("SHOW transaction_isolation")
'repeatable read'
>>> db.fetch_scalar("SELECT current_setting('application_name')")
'tabrion'
>>> try:
...     with db.read() as tx:
...         tx.execute("DELETE FROM parts")
... except ReadOnlyViolation as exc:
...     exc.classification.sqlstate
'25006'
>>> db.fetch_scalar("SELECT count(*) FROM parts")
2
>>> db.health().ok
True
>>> db.close()
```

```pycon
>>> app.close()
>>> conn.close()
```
