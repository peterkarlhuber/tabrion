# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/)
with the 0.x rules of the specification (section 10) and ADR 0009.

## [Unreleased]

### Added

- `tabrion.errors` (TAB-010): `classify` maps psycopg, sqlite3 and SQLAlchemy errors to a value-free
  `Classification` (kind, retryable, outcome unknown, connection usable, SQLSTATE or SQLite code, object names) by
  duck typing, without importing a driver, and never raises; it never marks an error retryable whose outcome is
  unknown. `safe_message` describes an error from those fields only, so `DETAIL`, `HINT`, `CONTEXT`, parameters,
  paths and passwords never appear (B-06-1). `DbErrorRedactionFilter` rewrites log records (tracebacks, chained
  exceptions, exception groups, arguments, preformatted messages, SQLAlchemy `[parameters]`) and sets
  `db_sqlstate`/`db_constraint`. `TabrionError` and its subclasses: value-free `str`/`repr`, read-only `details`,
  pickling; `DatabaseError.kind` without a classification is `Kind.UNKNOWN`.
- `tabrion.sql.split_statements` (TAB-011): PostgreSQL quoting rules (`E'...'` escapes, dollar tags, nested block
  comments); byte-identical to Axentra's splitter for every SQL file Axentra ships (golden file).
- `tabrion.config` (TAB-012): `safe_url` masks passwords in URLs, keyword conninfo and `password` query parameters;
  `url_from_env` reads `NAME` or the regular file `NAME_FILE` (codes `config.ambiguous`, `config.missing`,
  `config.unreadable`, `config.not_regular_file`; no value in any message).
- Fixes from the independent review of TAB-010..TAB-012, before any release: `classify` follows `__context__` only
  as a fallback and lets an outer connection loss win, so a COMMIT lost while handling a serialization failure is
  never retryable (R-NORETRY, H-1); SQLSTATEs 08007 and 40003 are always `outcome_unknown`; `DbErrorRedactionFilter`
  also finds database errors in `repr` form and inside list, tuple, set and dict arguments and exception `args`, and
  removes multi-line `DETAIL` text (H-2, M-2); `safe_url` ends the user information where libpq does (M-1);
  `url_from_env` no longer leaks a file descriptor for a directory, keeps no file bytes in `__context__` and refuses
  files over 64 KiB (`config.too_large`).
- Tests: the mapping table against PostgreSQL and SQLite, a fault-injection proxy for `outcome_unknown`, canary values,
  Hypothesis properties, mutation probes for the safety rules, and an allowlist check that the core imports only the
  standard library (G-T1 F-09, F-10).

- `tabrion.postgres` session helpers (TAB-015): `configure` applies `SessionSettings` in one
  `SELECT set_config(..., false)` round trip from a fixed allowlist (R-SET) and never commits;
  `session_configurator` validates once and commits only the transaction it opened (a `psycopg_pool` callback);
  `SessionSettings.unchanged()`; `transaction_timeout` from PostgreSQL 17. `ping` checks a connection or a pool under
  `SET LOCAL statement_timeout` in a transaction or savepoint of its own and never raises for database errors;
  `diagnose` reads server, session, SSL and role facts (fields without the read privilege are `None`);
  `extension_status` reports `active`, `other_schema`, `installable` or `unavailable` with the fixing command, with
  Nodestro's semantics for `probe_type`.
- `tabrion.postgres` reading role functions (TAB-016): `role_info` with `owner_scope="schema"` (Nodestro) or
  `"rls"` (Axentra), `check_role` raising `RoleUnsafe` with reason codes, `inspect_role` returning a `RoleReport`
  whose `as_dict()` has the keys of both existing formats; `RoleMissing` without the role name. A role counts as
  superuser or BYPASSRLS also when it, or the session's login role, can become one with `SET ROLE` (also as a
  NOINHERIT member), which the Axentra and Nodestro checks miss; a `schema` that does not exist gives an empty report.
- `tabrion.postgres` advisory locks (TAB-030): `lock_key` (stable int64 from SHA-256), `SessionLock` on an own
  autocommit connection (lazy, counted holds, liveness probe, counts cleared on a lost connection, thread-safe, safe
  across `fork`, conninfo never in `repr`), and `try_advisory_lock`, `wait_advisory_lock` (polling, refused inside a
  transaction, no open snapshot between attempts, compatible with `CREATE INDEX CONCURRENTLY`), `advisory_unlock` on a
  given connection. Errors are never retried.
- In psycopg pipeline mode every `tabrion.postgres` function syncs the caller's pipeline first, so a failing statement
  no longer aborts the caller's pipeline transaction (review DL-1 of #5).
- Every `tabrion.postgres` function reads through its own `tuple_row` cursor, leaves the transaction state, row
  factory and `autocommit` of a handed-in connection unchanged (T-NOTX), runs under a savepoint inside the caller's
  transaction so that a failing statement does not abort it, and qualifies catalog functions, types and operators
  with `pg_catalog`, so a hostile `search_path` can neither replace them nor run its code as the caller.
- Tests: an internal fault-injection proxy (`tabrion.testing._faults`, unstable and not public API; TAB-034, QA-3)
  that cuts a statement's reply, the connection in flight or the request, or delays the answer, placed by the
  protocol: a lost reply leaves the statement run exactly once and classified as outcome unknown; `ping`,
  `SessionLock` and the lock helpers are tested under each fault.
- Tests: the PostgreSQL suites run as the superuser and as roles without superuser, compare the role and extension
  results with the verbatim Axentra and Nodestro queries, check T-NOTX and R-SET for every function in every
  transaction state, and carry 33 mutation probes against the server. The extra `dev` adds `psycopg-pool` for the
  pool tests.

- Milestone M1 ("foundation"): package skeleton with the extras `postgres`, `sqlalchemy`, `migrations`, `testing`
  and `all`; ruff, mypy `--strict` and import-linter contracts (layers, stdlib-only core, no Levuvo platform).
- The complete public API of 0.1 and 0.2 as typed stubs (`__all__` in every module, every function raises
  `NotImplementedError`) and the API contract `docs/api/contract.md` for the API freeze G-T1.
- CI (`ci.yml`, `platforms.yml`, `ci-ok.yml` with `.github/ci-manifest.json`, `nightly.yml`) and a manual
  `release.yml` that builds and verifies but never publishes.
- Import isolation and the extras matrix: the core imports without psycopg, SQLAlchemy and Alembic.
- Documentation: specification, ADRs 0001 to 0013, risk register, program status, MkDocs site.
- G-T1 follow-up: the API surface check `scripts/api_surface.py` with `docs/api/surface.json` (`since` and
  `stability` per name; CI job `api surface`); `Database.health(deep=...)`; the fixture `tabrion_role_setup`;
  `tabrion.testing.profiles.SQLiteConnect` and `PortabilityCase`; import contracts for layer B and
  `tabrion.migrations`; `ci-ok` posts its verdict as a commit status on the pull request head; the release
  attestation runs in its own job.
- Brand assets derived from the logo master (`assets/branding/`): lockups for light and dark surfaces, favicons and
  the social preview; used in the README and the documentation site.
- `TRADEMARKS.md`: the name and the logos are not covered by the MIT license.
- `ci-ok`: a pull request that changes `.github/workflows/**` or `.github/scripts/**` is NOT OK until the label
  `workflow-change-reviewed` is set; the status post is retried and fails loudly.

### Changed

- Import contracts: `tabrion.errors` is the bottom layer under `tabrion.sql` and `tabrion.config`, because
  `tabrion.config` raises `ConfigurationError`.

- G-T1 follow-up: the 0.2 names (`Result`, `Column` and the 0.2 methods of `Database` and `Transaction`) are no
  longer exported before 0.2; `tabrion.testing.profiles.ConnectionFactory` is replaced by `SQLiteConnect`.
