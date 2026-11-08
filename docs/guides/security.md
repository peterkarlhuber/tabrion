# Security

What Tabrion protects against, what it does not, and what is left to you. The binding text is the
[specification, section 5](../specification.md#5-security-concept); report vulnerabilities as
[SECURITY.md](https://github.com/peterkarlhuber/tabrion/blob/master/SECURITY.md) describes.

## Files (SQLite)

- New database files are created `0600` before SQLite opens them; the umask never decides.
- Symlinks are refused for the database and its `-wal`, `-shm` and `-journal` files, at the check and again after
  SQLite opened the file (a link swapped in meanwhile is caught). Existing files must belong to the process user and
  must not be writable by group or others.
- Backups are written as a `0600` temporary file next to the target, checked with `quick_check` and renamed
  atomically; an existing target is never replaced unless you ask.
- Extension loading is off, and on only inside `loading_extensions()`.
- `trusted_schema=OFF`: views and triggers in a database file cannot call functions with side effects.

```pycon
>>> from tabrion import sqlite
>>> conn = sqlite.connect("private.db")
>>> d = sqlite.diagnose(conn)
>>> d.trusted_schema, d.secure_delete, d.is_symlink
(False, True, False)
>>> conn.close()
```

<!-- docs-test: posix -->
```pycon
>>> conn = sqlite.connect("private.db")
>>> oct(sqlite.diagnose(conn).file_mode)
'0o600'
>>> conn.close()
```

A directory that other users can write lets them replace the database file; `diagnose` logs a warning for it. Keep
databases in a directory only the service user can write.

## Error messages and logs

- Tabrion's messages never contain parameter values, PostgreSQL `DETAIL`/`HINT` lines or passwords: they are built
  from the classification ([Errors](errors.md)).
- The driver error is kept as `.original`, never chained as `__cause__`, so a default traceback does not print it.
  `TabrionError.statement` holds the SQL text without parameters; log it at debug level only.
- `DbErrorRedactionFilter` cleans log records of your own code that log driver exceptions.
- `safe_url` masks passwords; `repr(Database)` uses it.
- Tabrion's loggers are `tabrion.*` and log no parameters at any level. SQLAlchemy's `echo=True` logs parameters:
  never enable it in production.

```pycon
>>> from tabrion import Database
>>> db = Database.from_url("postgresql://app:s3cret@db.example.com/shop")
>>> "s3cret" in repr(db) or "s3cret" in db.safe_url
False
>>> db.close()
```

## TLS (PostgreSQL)

Layer B refuses a remote PostgreSQL host without TLS unless you say `tls="insecure"`
([Database and Profile](database.md#tls-for-postgresql)). Use `TLSMode.VERIFY_FULL` in production: `require`
encrypts but does not check the server's certificate. `verify-full` also needs the root certificate the server's
certificate chains to: `sslrootcert` in the URL, or `~/.postgresql/root.crt`
([Infrastructure baseline](infrastructure-baseline.md)). `tabrion.check` reports TAB-PG-010 (no `verify-full`) and
TAB-PG-011 (no TLS). Layer A enforces nothing on connections you open yourself.

## Roles and least privilege

- Run the application as a **runtime role**: no superuser, no `BYPASSRLS`, owning no tables. Run migrations as the
  owner role. `check_role` at start-up refuses anything else ([PostgreSQL](postgresql.md#roles)).
- `grant_runtime_role` gives the runtime role DML rights only and revokes writes on bookkeeping tables.
- Tabrion needs no superuser rights itself; `tabrion.testing` needs `CREATEROLE`/`CREATEDB` for throwaway objects.
- Tabrion sets no security context: no `SET ROLE`, no `row_security`, no `app.*` settings, no `RESET ALL`. Row-level
  security scopes (`set_config(..., true)`) stay your code and stay transaction-local.

## SQL injection

- Every statement Tabrion sends is parameterized; identifiers are quoted; pragma values are validated enums or
  integers.
- Pass values as parameters (`:name` in layer B, `?` or `%s` in the drivers), never with string formatting.
  `split_statements` only splits; it does not make untrusted SQL safe.

## Read-only is a guard, not a boundary

`read()`, `Profile.readonly()` and `connect(read_only=True)` stop accidental writes; the database refuses them. SQL
that the caller controls can switch read-only off again. Where untrusted SQL runs (a SQL editor), the boundary is a
role without write rights, or file permissions that do not let the process write the SQLite file.

What the guard does and where it ends (contract implementation note [I-56](../api/contract.md#57-implementation-notes-tab-018)):

- Right before the `COMMIT` of a `read()`, Tabrion reads the read-only state back. When the SQL lifted it, Tabrion
  rolls back, raises `UsageError("database.read_only")` and invalidates the connection, so nothing of it stays in the
  pool.
- The read-back sees the end state only. SQL that lifts read-only, writes and restores it before the end, or that
  ends the transaction itself and commits another one (`ROLLBACK; BEGIN READ WRITE; ...; COMMIT`), is committed
  (TODO TB-061).
- On SQLite, `mode=ro` covers the main file only, and only `Database.from_url` adds it and the `ATTACH` limit:
  `Database.from_engine` gets neither, and layer A's `connect(read_only=True)` sets no `SQLITE_LIMIT_ATTACHED`, so an
  `ATTACH` of the same file can open it read-write.
