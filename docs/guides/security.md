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
- `restore()` (since 0.3) checks the source with `integrity_check` before it writes anything, copies
  with SQLite's online backup API in one step (other connections see the old or the new database, never a mix),
  creates a new target `0600`, never overwrites a file that is not a SQLite database, and checks the target after
  the copy. Back up the target first: Tabrion does not.
- `delete_database_file()` (since 0.3) refuses a link, a file that is not a SQLite database and a
  database another connection holds (SQLite's exclusive lock). It works through a directory descriptor opened with
  `O_NOFOLLOW` and compares device and inode right before the unlink, so a file swapped in after the check is not
  deleted; side files that are links are removed as links, never followed. An idle connection in rollback mode holds
  no lock and is not seen, and a process may open the file after the check: stop your own connections first. On
  POSIX it also refuses a database whose directory the group or others may write (`sqlite.unsafe_mode`) or that
  belongs to another user than you and root (`sqlite.unsafe_owner`); ACLs are not evaluated.
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
- `grant_runtime_role` gives the runtime role DML rights only and revokes writes (and `TRIGGER`) on the bookkeeping
  tables that exist, their partitions and inheritance children. It refuses a member of a predefined `pg_*` role, a
  role that still holds `INSERT`, `UPDATE`, `DELETE`, `TRUNCATE` or `TRIGGER` (or a column `INSERT`/`UPDATE`) on one
  of them after the revoke, directly, through `PUBLIC` or through a role it is a member of, and, before PostgreSQL 16,
  a role that has or can reach `CREATEROLE`. It does not see a bookkeeping table, partition or child created or
  attached later: run it again after a migration that creates or attaches one.
- `check_role` does not see predefined roles or `CREATEROLE`; run `tabrion.check` too: TAB-PG-013
  (superuser-equivalent roles, `CREATEROLE` before PostgreSQL 16) and TAB-PG-014 (other predefined roles).
- Tabrion needs no superuser rights itself; `tabrion.testing` needs `CREATEROLE`/`CREATEDB` for throwaway objects.
- `create_database` and `drop_database` (since 0.3) quote every name with `psycopg.sql.Identifier` and
  send option values as validated literals. `drop_database` refuses `postgres`, `template0`, `template1`, any
  template and the connection's own database, and a database with other sessions unless you pass `force=True`.
  Tabrion asks for no confirmation: the typed name, the backup before a drop and the decision to call are yours. A
  `pg_dump` archive is **not a complete backup**: global roles, role memberships, tablespaces and other cluster
  objects are not in it.
- The role DDL (since 0.3) never grants SUPERUSER, BYPASSRLS, CREATEROLE or REPLICATION, also not
  through membership in a role that has them (`INHERIT`, `SET` or `ADMIN` option), nor membership in a predefined
  `pg_*` role, `TRIGGER`, `EXECUTE` on another role's `SECURITY DEFINER` function or `CREATE` on `public` or another
  role's schema. Run it from a separate admin profile with `CREATEROLE` only, on PostgreSQL 16 or later: a session
  whose user has SUPERUSER, BYPASSRLS or REPLICATION is refused (`postgres.caller_unsafe`), and so are grants on
  objects of the system or of a superuser's objects and new privileges for a role that reaches
  `pg_execute_server_program`, `pg_read_server_files` or `pg_write_server_files`. Show `role_privileges` before a
  change (with `public_definers`, the SECURITY DEFINER functions every role may run) and again after it; the functions
  check again in the transaction of the change. Passwords go to the server only as SCRAM verifiers.
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
  rolls back, raises `UsageError("database.read_only")` and, on PostgreSQL and a file-backed SQLite database,
  invalidates the connection, so nothing of it stays in the pool. The one shared connection of an in-memory SQLite
  database is the database itself and stays.
- The read-back sees the end state only. SQL that lifts read-only, writes and restores it before the end, or that
  ends the transaction itself and commits another one (`ROLLBACK; BEGIN READ WRITE; ...; COMMIT`), is committed
  (TODO TB-061).
- On SQLite, `mode=ro` covers the main file only, and only `Database.from_url` with `Profile.readonly()` adds it
  and the `ATTACH` limit; `read()` on a normal profile sets `query_only` for the transaction, not `mode=ro`:
  `Database.from_engine` gets neither, and layer A's `connect(read_only=True)` sets no `SQLITE_LIMIT_ATTACHED`, so an
  `ATTACH` of the same file can open it read-write.
