# Security policy

## Supported versions

Tabrion is in pre-alpha and has no release yet. Security fixes are made on the default branch (`master`). Once
releases exist, the latest minor release line receives fixes; a fix that tightens behavior may ship in a patch
release and is listed under `Security` in the changelog.

| Version | Supported |
| --- | --- |
| `master` (unreleased 0.1.0) | yes |

## Reporting a vulnerability

Please do not report vulnerabilities in public issues, pull requests or discussions.

1. Use **Report a vulnerability** in the repository's **Security** tab (GitHub private vulnerability reporting).
   Only the maintainers can see the report.
2. If that option is not available, open an issue titled "Security contact request" **without any details**, so
   that a private channel can be arranged.

Include the affected module and version or commit, the impact, and steps or a minimal script to reproduce. Never
include real passwords, connection strings or data; use placeholders such as `SECRET-4711`.

The target is a first response within 7 days. Fixes are coordinated with the reporter before details are
published, through a GitHub security advisory.

## Scope

In scope are vulnerabilities in the code of this repository, for example:

- parameter values, `DETAIL` lines, passwords or paths in error messages, `repr` or logs;
- SQLite files or backups readable by other users, or redirected through symlinks;
- session settings that leak across pooled connections, or any setting outside the documented allowlist
  (statement, lock, idle-in-transaction and transaction timeouts, `application_name`, `search_path`, `TimeZone`);
- grants that give a runtime role more than DML, or a role check that accepts a superuser, `BYPASSRLS` or table
  owner;
- a statement repeated after its outcome became unknown;
- SQL injection through identifiers Tabrion builds.

Accepted residual risks (documented, not vulnerabilities):

- Tabrion does not enforce TLS on connections it did not open itself (layer A only diagnoses them).
- SQLite files (`tabrion.sqlite`, TAB-013, TAB-014):
  - on Windows `O_NOFOLLOW` does not exist and SQLite does not resolve links in the name it opens; there the
    `lstat` check (reparse points included, also right before a new file is created, because `CREATE_NEW` follows a
    dangling link) and the inode comparison after opening protect the file, not the resolved-name comparison of
    POSIX (contract I-22);
  - `connect` refuses an existing database or side file that belongs to another user or is group- or
    world-writable (`sqlite.unsafe_owner`, `sqlite.unsafe_mode`; POSIX, unless `allow_shared_file`), but accepts a
    group- or world-*readable* one (existing Nodestro files are 0644) and leaves its mode as it is;
  - SQLite opens `-wal`, `-shm` and `-journal` by name when it first needs them, after `connect` has checked them;
    a link or file planted later in the database's directory is used, also in a sticky shared directory such as
    `/tmp`, where another user can create a `-journal` that then receives page data. Keep databases in a directory
    that only the owner can write: `diagnose` logs a warning for a directory the group or others can write (TM-03,
    sticky or not), and a `tabrion.check` finding for it is TODO TB-043;
  - a `read_only` connection to a WAL database may create `-wal` and `-shm` with the main file's mode, which an
    existing world-readable file passes on; `connect` never changes the mode of an existing file (MIG-NOD-9 does
    that for Nodestro);
  - the files are not protected against the owning user or root, and they are not encrypted at rest.
- `TLSMode.INSECURE` is an explicit opt-in for local development. Under `TLSMode.AUTO` a local host (Unix socket,
  `localhost`, loopback) keeps the URL's `sslmode`, whatever it is; a remote one gets at least `require`, which
  encrypts but does not verify the server: use `TLSMode.VERIFY_FULL` where a man in the middle is a concern
  (contract I-41).
- Using `Database.engine` directly bypasses Tabrion's transaction modes (SQLite writes then begin `DEFERRED`). The
  connection profile and `Profile.read_only` apply there as the connection was set up, but SQL can change them:
  `Database.engine` is not read-only once a session default was switched off (contract I-56).
- `read()` and `Profile.read_only` are a best-effort guard against accidents, not a security boundary (contract
  I-56). The caller's SQL can end read-only. Tabrion reads the state once more before the `COMMIT` of a `read()`
  and rolls back when `SET TRANSACTION READ WRITE`, `SET transaction_read_only`, `SET
  default_transaction_read_only` or SQLite's `PRAGMA query_only = OFF` is still in force at the end, but SQL that
  lifts read-only, writes and restores it before the end, or that ends the transaction and commits its own
  (`ROLLBACK; BEGIN READ WRITE; ...; COMMIT`), writes (TODO TB-061). SQLite's `mode=ro` covers the main file
  only; `Profile.read_only` therefore forbids `ATTACH`, which would open the same file read-write. The only hard
  boundary is a PostgreSQL role without write rights, or on SQLite file permissions that do not let the process
  write the file: give untrusted SQL such a role or file (SR-03).
- A `SET` without `LOCAL` inside a Tabrion transaction stays on the pooled connection for every later checkout
  (TM-08); Tabrion runs no `RESET` or `DISCARD`. Use `SET LOCAL` or `set_config(..., true)` (contract I-56).
- `Database.from_engine` with listeners changes the session settings of every connection of the foreign engine,
  the host's own included, and `close()` does not undo them; a read-only profile is refused there (contract I-37).
- `tabrion.migrations.status` never runs `env.py`, but Alembic imports every revision file of the script directory,
  so module-level code in a revision file runs: point it only at a trusted script directory (contract I-57).
- Layer-B timeouts bound the database's work, not every wait: a SQLite lock wait is bounded by the busy timeout, a
  wait for a pool connection by `pool_timeout`, a network that stops answering by the connection's TCP settings
  (contract I-40, I-44; TODO TB-056).
- `TabrionError.original` of a layer-B error is the driver error, with the server's `DETAIL` and, for SQLAlchemy's
  own refusals, the parameters: log the `TabrionError`, not its `original`.
- `DbErrorRedactionFilter` protects only the records it sees: the application installs it (on its handlers, its
  loggers or through the log record factory); Tabrion installs it nowhere. A value the application logs on its own
  (for example `exc.diag.message_detail` as a plain string) is not recognized.
- Text-only residuals of `DbErrorRedactionFilter`, inherited from Axentra's MIG-AXE-0 review (R-1, R-2):
  - (R-1) values in the *primary* message of pre-formatted text, for example 22P02
    `invalid input syntax for type integer: "..."`, also through a wrapper that quotes `repr(e)` or the first line
    of the error text, are not removed: the filter removes only `DETAIL`, `HINT`, `CONTEXT`, `QUERY`, `LINE n` and
    SQLAlchemy `[parameters]`, or replaces a database error it finds as an object;
  - (R-2) values passed through `extra=` are not inspected;
  - when a formatter or a handler's `emit` fails while a database error is being handled, `logging`'s
    `Handler.handleError` prints the failure's chain, with that error's `DETAIL`, to `sys.stderr`; no filter sees
    that output (Axentra review F-2). A broken call (too few arguments, a missing key, a wrong type) is covered: the
    filter rewrites it when a database error or its text is being handled, or is in the template or an argument, so
    `logging` never reports it. Set `logging.raiseExceptions = False` in production entry points; a redacting
    `handleError` needs a public install function (TODO TB-050);
  - installed through the log record factory, the filter sets `db_sqlstate` and `db_constraint` before
    `Logger.makeRecord` applies `extra=`, so these two names are reserved: passing them in `extra=` raises
    `KeyError` from the logging call. Installed on a handler or logger, the filter overwrites them instead (TODO
    TB-050 covers a `makeRecord` that accepts them);
  - `db_sqlstate`/`db_constraint` name the first database error found in the record, also one in a suppressed
    context (`raise ... from None`); `classify` of the logged exception follows I-13 and may say `UNKNOWN` there;
  - a mapping-message value whose `repr` is clean but whose `str` embeds a database error is kept, and a renderer
    that uses `str` (`json.dumps(..., default=str)`) prints it; an argument whose `__str__` returns clean text on
    the first call and the error text on the second is printed (the filter does not freeze the message). Both need
    such objects in the application; log database errors as objects;
  - structlog's native processor chain renders `exc_info` itself; the filter covers structlog's stdlib integration
    (`ProcessorFormatter`), not native structlog loggers (TODO TB-051);
  - a value line that is exactly a traceback structure line ends a removed part early, but only inside a formatted
    traceback; a removed part is marked `[redacted: database server text]`, and attacker-controlled input with a
    label can still cut the rest of a message (visibly);
  - a wrapper that quotes `TabrionError.original` without chaining it is not recognized (TODO TB-030).

  Log database errors as objects (`logger.exception(...)`, `%s` with the exception as an argument), never as text
  built with `str(exc)` or f-strings; enforce this with a lint rule against formatted logging messages (ruff `G001`
  to `G004`) and review `str(exc)` arguments.
- A pickled `TabrionError` contains its `original`, including the server's `DETAIL` values: do not persist or send
  pickled errors.
- `classify` "never raises" covers `Exception`; a `SystemExit` or `KeyboardInterrupt` raised by a hostile `__str__`
  or property propagates.
- `safe_url` cannot tell where a password with both a raw `/` and a raw `?` ends; such a URL gives
  `"<unparseable url>"`. Percent-encode special characters in URL passwords.
- `tabrion.postgres.ping` bounds the server's work (`SET LOCAL statement_timeout`) and the pool checkout, not a
  network that stops answering in the middle of a statement, nor a connection another thread holds (psycopg's
  connection lock); set TCP keepalives or `tcp_user_timeout` on the connection, or run the check in a thread, when a
  hard deadline matters (TODO TB-034). The same holds for `tabrion.postgres.SessionLock`: after a half-open TCP loss
  the server keeps the old session and its locks until TCP gives up, so pass keepalives (`keepalives_idle`,
  `keepalives_interval`, `keepalives_count`) or `tcp_user_timeout` in `connect_kwargs`.
- `tabrion.postgres` qualifies every catalog function, relation, type and operator it uses with `pg_catalog`
  (`::pg_catalog.text`, `OPERATOR(pg_catalog.=)`), so a schema that a caller's `search_path` names *before*
  `pg_catalog` (the CVE-2018-1058 pattern) can neither change its answers nor run its owner's code as the role that
  calls the checks. Nothing of this kind is known to remain; the contract test traps functions, operators and domains
  (contract I-16). Your own SQL on such a connection is not protected: keep `pg_catalog` first, or leave it out of
  the path, for roles that run privileged checks.
- `tabrion.postgres.check_role` reports a role as superuser or BYPASSRLS also when it, or the session's login role,
  can become one with `SET ROLE` (contract I-20). Membership of predefined roles such as `pg_execute_server_program`
  is not reported yet (TODO TB-036). Applications that still use Axentra's or Nodestro's own checks have the older
  gap (TODO TB-035).
- `tabrion.postgres.grant_runtime_role` grants DML only, never `CREATE`, and refuses a runtime role that is or can
  become the owner, a superuser or a BYPASSRLS role (contract I-35); Axentra's and Nodestro's own grant functions
  accept such members (TODO TB-035). It runs as the owner role; it never reads a name through the `search_path`. A
  later run without `protected_tables` gives the runtime role write access to the bookkeeping tables again: pass the
  same `protected_tables` every time. It also refuses a role that owns the schema or can become its owner, a
  schema that does not exist and names over 63 bytes (which the server would truncate to another object's name).
  Check the returned report: `can_create=True` (`CREATE` on the schema, granted by someone else and kept) lets the
  role plant objects that shadow others on a `search_path`, although `restricted` holds; `connect=False` means the
  owner could not grant `CONNECT` (PostgreSQL only warns).
- `tabrion.testing._faults` (internal, for tests only) listens on `127.0.0.1` and relays the PostgreSQL protocol in
  plain text: its `dsn()` disables TLS and GSS encryption, so the password travels unencrypted over loopback. Never
  point it at a production server or use it outside a test.
- Vulnerabilities in SQLite, PostgreSQL, psycopg, SQLAlchemy or Alembic belong to their maintainers.
