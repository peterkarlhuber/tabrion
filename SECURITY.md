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
    a link planted later in the database's directory is followed. Keep databases in a directory that only the
    owner can write: `diagnose` logs a warning for a world-writable directory without the sticky bit (TM-03), and
    a `tabrion.check` finding for it is TODO TB-043;
  - a `read_only` connection to a WAL database may create `-wal` and `-shm` with the main file's mode, which an
    existing world-readable file passes on; `connect` never changes the mode of an existing file (MIG-NOD-9 does
    that for Nodestro);
  - the files are not protected against the owning user or root, and they are not encrypted at rest.
- `TLSMode.INSECURE` is an explicit opt-in for local development.
- Using `Database.engine` directly bypasses Tabrion's transaction modes.
- `DbErrorRedactionFilter` protects only the records it sees: the application installs it (on its handlers, its
  loggers or through the log record factory); Tabrion installs it nowhere. A value the application logs on its own
  (for example `exc.diag.message_detail` as a plain string) is not recognized.
- Text-only residuals of `DbErrorRedactionFilter`, inherited from Axentra's MIG-AXE-0 review (R-1, R-2):
  - (R-1) values in the *primary* message of pre-formatted text, for example 22P02
    `invalid input syntax for type integer: "..."`, also through a wrapper that quotes `repr(e)` or the first line
    of the error text, are not removed: the filter removes only `DETAIL`, `HINT`, `CONTEXT`, `QUERY`, `LINE n` and
    SQLAlchemy `[parameters]`, or replaces a database error it finds as an object;
  - (R-2) values passed through `extra=` are not inspected;
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
  hard deadline matters (TODO TB-034).
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
- `tabrion.testing._faults` (internal, for tests only) listens on `127.0.0.1` and relays the PostgreSQL protocol in
  plain text: its `dsn()` disables TLS and GSS encryption, so the password travels unencrypted over loopback. Never
  point it at a production server or use it outside a test.
- Vulnerabilities in SQLite, PostgreSQL, psycopg, SQLAlchemy or Alembic belong to their maintainers.
