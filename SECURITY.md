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
- On Windows `O_NOFOLLOW` does not exist; only the symlink check protects new files there.
- `TLSMode.INSECURE` is an explicit opt-in for local development.
- Using `Database.engine` directly bypasses Tabrion's transaction modes.
- `DbErrorRedactionFilter` protects only the records it sees: the application installs it (on its handlers, its
  loggers or through the log record factory); Tabrion installs it nowhere. A value the application logs on its own
  (for example `exc.diag.message_detail` as a plain string) is not recognized.
- `safe_url` cannot tell where a password with a raw, not percent-encoded `?` ends; such a URL gives
  `"<unparseable url>"`. Percent-encode special characters in URL passwords.
- Vulnerabilities in SQLite, PostgreSQL, psycopg, SQLAlchemy or Alembic belong to their maintainers.
