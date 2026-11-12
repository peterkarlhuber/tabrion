# Errors

`tabrion.errors` answers two questions about any database exception: **what kind of error is it** (and may the
transaction be retried), and **what may be logged or shown** without leaking values. It needs only the standard
library and recognizes `sqlite3`, psycopg and SQLAlchemy errors by duck typing, without importing them. The contract
is [section 1 of the API contract](../api/contract.md#1-tabrionerrors-standard-library); the reasons are in
[ADR 0004](../adr/0004-error-model.md).

## Classifying a driver error

`classify(error)` returns a frozen `Classification`. It never raises, whatever you pass it.

```pycon
>>> import sqlite3
>>> from tabrion.errors import Kind, classify, safe_message
>>> conn = sqlite3.connect(":memory:")
>>> _ = conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL)")
>>> email = "ann@example.com"
>>> _ = conn.execute("INSERT INTO users (email) VALUES (?)", (email,))
>>> try:
...     conn.execute("INSERT INTO users (email) VALUES (?)", (email,))
... except sqlite3.Error as exc:
...     error = exc
>>> c = classify(error)
>>> c.kind, c.retryable, c.connection_usable, c.outcome_unknown
(<Kind.UNIQUE: 'unique'>, False, True, False)
>>> c.sqlite_code, c.constraint, c.table, c.column
('SQLITE_CONSTRAINT_UNIQUE', 'users.email', 'users', 'email')
>>> conn.close()
```

The fields:

| Field | Meaning |
|---|---|
| `kind` | one of the `Kind` members: `connection`, `timeout`, `canceled`, `locked`, `deadlock`, `serialization`, `unique`, `foreign_key`, `not_null`, `check`, `exclusion`, `data`, `undefined_object`, `syntax`, `permission`, `read_only`, `corrupt`, `disk_full`, `resource`, `unknown` |
| `retryable` | the **whole transaction** may be run again: lock timeouts, deadlocks, serialization failures, and lost connections whose outcome is known |
| `outcome_unknown` | the statement or the `COMMIT` may have run (the connection broke while it was being sent); never retry automatically |
| `connection_usable` | the connection can still be used after the error |
| `driver`, `sqlstate`, `sqlite_code` | where it came from: PostgreSQL SQLSTATE or SQLite's extended error name |
| `constraint`, `table`, `column` | object names, never values |

Kinds are `StrEnum` members, so they compare with plain strings and serialize as such:

```pycon
>>> c.kind == "unique"
True
```

Anything unknown is `Kind.UNKNOWN`, not retryable:

```pycon
>>> classify(ValueError("not a database error")).kind
<Kind.UNKNOWN: 'unknown'>
```

The full mapping from SQLSTATE and SQLite codes to kinds is the table in
[contract section 1.3](../api/contract.md#13-functions). The most useful rows:

| PostgreSQL | SQLite | Kind | retryable |
|---|---|---|---|
| `08xxx`, `57P01..03`, broken connection | `SQLITE_CANTOPEN`, `SQLITE_IOERR*` | `connection` | PostgreSQL: yes when the outcome is known |
| `57014` statement timeout | interrupt by a Tabrion deadline | `timeout` | no |
| `55P03` lock not available | `SQLITE_BUSY*`, `SQLITE_LOCKED*` | `locked` | yes |
| `40P01` / `40001` | - | `deadlock` / `serialization` | yes |
| `23505`, `23503`, `23502`, `23514` | `SQLITE_CONSTRAINT_*` | `unique`, `foreign_key`, `not_null`, `check` | no |
| `42501` | `SQLITE_AUTH` | `permission` | no |
| `25006` | `SQLITE_READONLY*` | `read_only` | no |

Chains are followed: a SQLAlchemy `DBAPIError` is unwrapped through `.orig`, and `__cause__` (then `__context__`) up
to five links, so you can classify the exception you caught, whatever wrapped it.

## Safe messages

`str(exc)` of a driver error can contain values: PostgreSQL's `DETAIL` line holds key values
(`Key (email)=(ann@example.com) already exists.`), SQLAlchemy appends `[parameters: ...]`. `safe_message` builds the
text only from the classification fields:

```pycon
>>> safe_message(error)
'unique violation (SQLite SQLITE_CONSTRAINT_UNIQUE, constraint users.email, table users, column email)'
>>> email in safe_message(error)
False
```

Use it wherever an error leaves your process: API responses, logs, metrics labels.

## Redacting logs

Logging an exception with `logger.exception(...)` writes its `str` into the traceback. `DbErrorRedactionFilter` is a
`logging.Filter` that rewrites the record so every database exception line carries the safe message. It suppresses
nothing, keeps the stack lines, and adds `record.db_sqlstate` and `record.db_constraint` for JSON formatters. Tabrion
installs it nowhere; add it to your handlers.

```pycon
>>> import io, logging
>>> import sqlalchemy as sa
>>> from tabrion.errors import DbErrorRedactionFilter
>>> engine = sa.create_engine("sqlite://")
>>> with engine.begin() as c:
...     _ = c.exec_driver_sql("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT UNIQUE)")
...     _ = c.execute(sa.text("INSERT INTO users (email) VALUES (:email)"), {"email": email})
>>> stream = io.StringIO()
>>> handler = logging.StreamHandler(stream)
>>> handler.addFilter(DbErrorRedactionFilter())
>>> log = logging.getLogger("shop.signup")
>>> log.addHandler(handler)
>>> log.propagate = False
>>> try:
...     with engine.begin() as c:
...         c.execute(sa.text("INSERT INTO users (email) VALUES (:email)"), {"email": email})
... except sa.exc.DBAPIError as exc:
...     print(email in str(exc))
...     log.exception("signup failed")
True
>>> output = stream.getvalue()
>>> email in output
False
>>> output.splitlines()[-1]
'sqlalchemy.exc.IntegrityError: unique violation (SQLite SQLITE_CONSTRAINT_UNIQUE, constraint users.email, table users, column email)'
>>> log.removeHandler(handler)
>>> engine.dispose()
```

The filter changes the log record only, never the exception object.

## Errors Tabrion raises

Everything Tabrion raises derives from `TabrionError`. Each carries a stable `code`, value-free `details`, and,
for database errors, the `classification` and the driver error as `original`. The original is **not** chained as
`__cause__`, so a traceback printer never shows its text.

| Class | Raised for | Example codes |
|---|---|---|
| `UsageError` | misuse: a nested transaction, a closed database, a wrong thread | `sqlite.nested_transaction`, `database.closed` |
| `ConfigurationError` | an invalid profile or URL, a TLS rule, a missing environment variable | `config.missing`, `config.tls`, `config.driver` |
| `DatabaseError` and its subclasses | layer B: translated driver errors, one class per group of kinds | `database.unique`, `database.timeout` |
| `MultipleRowsError` | `fetch_one`/`fetch_scalar` found more than one row | `database.multiple_rows` |
| `MigrationError` and its subclasses | `tabrion.sqlite.migrate` | `sqlite.schema_too_new`, `sqlite.backup_required` |
| `SchemaNotCurrent` | `tabrion.migrations.require_current` | `migrations.not_current` |
| `RoleMissing`, `RoleUnsafe` | `tabrion.postgres` role checks | `postgres.role_missing`, `postgres.role_unsafe` |
| `UnsafePath` | SQLite path checks | `sqlite.symlink`, `sqlite.unsafe_mode` |

The `DatabaseError` subclasses group the kinds: `ConnectionFailed` (connection, resource), `StatementTimeout`,
`Canceled`, `TransientError` (locked, deadlock, serialization), `IntegrityViolation` (unique, foreign key, not null,
check, exclusion), `PermissionDenied`, `ReadOnlyViolation`, `StatementError` (syntax, undefined object, data),
`StorageCorrupt` (corrupt, disk full) and `UnknownDatabaseError`.

```pycon
>>> from tabrion import Database
>>> from tabrion.errors import IntegrityViolation, TabrionError
>>> db = Database.from_url("sqlite://")
>>> db.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT UNIQUE)")
0
>>> db.execute("INSERT INTO users (email) VALUES (:email)", {"email": email})
1
>>> try:
...     db.execute("INSERT INTO users (email) VALUES (:email)", {"email": email})
... except IntegrityViolation as exc:
...     caught = exc
>>> caught.code, caught.kind
('database.unique', <Kind.UNIQUE: 'unique'>)
>>> caught.statement
'INSERT INTO users (email) VALUES (?)'
>>> caught.__cause__ is None, type(caught.original).__name__
(True, 'IntegrityError')
>>> email in str(caught) or email in repr(caught)
False
>>> repr(caught)
"IntegrityViolation('database.unique')"
>>> isinstance(caught, TabrionError)
True
>>> db.close()
```

`statement` is the SQL text without parameters, cut to 200 characters; keep it for debug logs, it is never part of
`str` or `repr`. Map errors to your own by `code` or by class; the codes are part of the stable API and change only after a
deprecation ([specification section 10](../specification.md#10-stability-and-versioning-e)).

## Retry rules

- Retry the **whole transaction**, never a single statement, and only when `retryable` is true.
- `retryable` is false whenever `outcome_unknown` is true: the `COMMIT` may have run. Read back, or use an
  idempotency key, before you try again ([Transactions](transactions.md#unknown-outcomes)).
- Tabrion itself never retries.
