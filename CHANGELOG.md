# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/)
with the 0.x rules of the specification (section 10) and ADR 0009.

## [Unreleased]

### Added

- Layer B core (TAB-018): `Profile` with validation and the presets `default()`, `readonly()`, `edge()` and
  `testing()`; `Database.from_url`, `from_env` and `from_engine` (a read-only profile only without listeners) for SQLite (`pysqlite`) and PostgreSQL
  (`postgresql://` rewritten to `+psycopg`), with the TLS rules of `TLSMode` (a libpq `service` or `PGSERVICE` counts as remote), the SQLite path checks and `0600`
  creation before the first connect, `StaticPool` for memory databases, `safe_url`, `close` and a `ResourceWarning`
  for a forgotten `close`; `transaction()` (SQLite `BEGIN IMMEDIATE`, PostgreSQL isolation levels) and `read()`
  (`query_only` or `BEGIN READ ONLY` in the database; a best-effort guard against accidents, not a security boundary: SQL that
  lifts it inside `read()` is rolled back before the `COMMIT`, and the hard boundary is a role without write rights,
  contract I-56), nesting refused per thread, per-statement and
  per-transaction timeouts, `execute` (with `executemany`), `fetch_one`, `fetch_all`, `fetch_scalar` and the
  short forms on `Database`; driver errors become the `DatabaseError` subclass of their kind without a cause chain,
  and a lost `COMMIT` is outcome unknown; `health(timeout=..., deep=...)` never raises for database errors; `check()`
  calls `tabrion.check.check`. The layer-A profile runs on the same DBAPI connection through `tabrion.sqlite.connect`
  and SQLAlchemy events (no second connection). Tested on SQLAlchemy 2.0.30 and 2.1, with mutation probes.

- `tabrion.testing.profiles` (TAB-021): `sqlite_profile_cases()` (TAB-T-F01..F06: foreign keys, WAL, busy
  timeout, `0600` files, symlink refusal, no DEFERRED writes) for any SQLite connect function, and
  `portability_cases()` (TAB-T-D01..D09, the data type rules of specification section 6) for SQLite and PostgreSQL.
  F05 refuses symlinks to missing and to existing files, for the main path and the `-wal`, `-shm` and `-journal`
  files, checks that the plain file reopens, and counts only `UnsafePath("sqlite.symlink")` as a refusal. The
  portability cases create fresh table names (`tabrion_d0N_<random>`) without `IF NOT EXISTS`, fail on an existing
  name without touching it and drop only their own tables; run them in an empty or dedicated schema. D03 uses
  `GENERATED ALWAYS AS IDENTITY` and checks `bigint` on PostgreSQL; D04 and D07 check the stored or server type
  (deviations D-62 and D-63, contract I-58).

- `tabrion.check.check` (TAB-017) for `sqlite3` and psycopg connections, read only and without changing the
  connection's transaction or row factory: the finding codes TAB-SQ-001..009 and TAB-PG-001..011, 020, 021 and 030
  of specification section 9. `expect="owner"` suppresses TAB-PG-003 and TAB-PG-009, `expect="any"` reports them
  as `info`; a missing privilege or an unreadable file gives an `info` finding "Not checked"; no detail names a
  path. A `Database` target, TAB-PG-012 and TAB-B-001/002 follow with layer B (TODO TB-060).
- `tabrion.migrations.status` and `require_current` (TAB-019): the Alembic schema state (current revisions,
  heads, pending revisions in order, unknown newer revisions) read in one `Database.read()` without running
  `env.py` and without migrating; `require_current` raises `SchemaNotCurrent`. `is_current` means nothing
  unknown and nothing pending, so a head covered by `depends_on` counts as applied; a script directory Alembic
  cannot resolve raises `ConfigurationError("migrations.revisions")` without path or cause chain. Only
  `<script_location>/versions` is read (TODO TB-066).

### Changed

- `classify(error, during_commit=True)` on a `TabrionError` whose stored classification is a connection error now
  reports the outcome as unknown and not retryable (R-NORETRY, TB-040). `Database()` and `Transaction()` raise
  `TypeError` instead of building an unusable object (G-T1 F-14).
- Master builds as `0.1.0a2.dev0` after the `0.1.0a1` commit (TB-053), so later commits never build as the
  internal pre-release. The installation guide pins the a1 commit SHA instead of the tag name and says not to run
  `release.yml` for it (TAB-038 review Info notes).

## [0.1.0a1] - internal pre-release, not published

TABRION TAB-038: the tag `v0.1.0a1` on the merge commit of this version is an internal pre-release for **unmerged**
integration branches only (RC-8; [installation](docs/getting-started/installation.md#internal-pre-release-v010a1)).
Its scope is `tabrion.errors`, `tabrion.sql` and `tabrion.config` (TAB-010..TAB-012). The other modules that are
already in the tree (`tabrion.sqlite`, `tabrion.postgres`) are included as they are, without the a1 promise; layer
B, `tabrion.check`, `tabrion.migrations` and `tabrion.testing` still raise `NotImplementedError`. No PyPI upload,
no GitHub release and no announcement (G-R). It satisfies no final range such as `tabrion>=0.1,<0.2`.

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
- `tabrion.sqlite` connection and profile (TAB-013): `SQLiteProfile` with validation and the `orkexa()`/`nodestro()`
  presets; `connect` creates new files `0600` (exactly, whatever the umask) with `O_CREAT|O_EXCL|O_NOFOLLOW` before
  SQLite opens them, refuses symbolic links for the file and its `-wal`, `-shm` and `-journal` files, checks after
  opening that SQLite opened the checked file, opens `read_only` as `mode=ro` plus `query_only`, and applies the
  profile (`foreign_keys` on, WAL, `synchronous=FULL`, `secure_delete`, `trusted_schema` off, busy timeout 30 s);
  `apply_profile`, `check_path`, `create_private_file` and `loading_extensions`.
- `tabrion.sqlite` transactions, migrations, backup and checks (TAB-014): `write_transaction` (`BEGIN IMMEDIATE`)
  and `read_transaction` (snapshot, `query_only`), both refusing to nest; `migrate` with Nodestro's semantics (one
  write transaction, backup first, the file unchanged on every error, a concurrent migration is seen);
  `backup` (online, `0600` temporary file, `quick_check`, `fsync`, atomic rename, never replaces a target that
  appeared meanwhile); `integrity_check`, `quick_check`, `foreign_key_check` and `diagnose`. Every function that
  takes a connection follows rule T-NOTX. Contract section 3.9 lists what the implementation settled (I-21..I-31).
- Tests `tests/sqlite/` (TAB-T-S01..S41): file modes under several umasks, symbolic-link and race cases, the
  five-thread lost-update scenario, two processes migrating at once, byte-identical files after failed migrations,
  backups under concurrent writes, damaged files, T-NOTX in every state, and the mutation probes; the platforms
  workflow runs them on macOS and Windows.
- Port of Axentra's TB-031 review fixes (`axe-tb031-review.md`) to `DbErrorRedactionFilter`: tracebacks are
  redacted when a non-database exception carries database text in its message, `args` or notes (F-1); a broken log
  call no longer reaches `logging`'s error report on stderr when a database error or its text is being handled or
  is in its template or arguments (F-2 for broken calls; a failing formatter or `emit` is a documented residual); the inline label search is linear (F-3, a log-injection DoS); traceback structure lines end a
  removed part only inside a traceback (F-4, re-review N-1); a mapping message stays a mapping and an `exc_info` in
  it is redacted, so structlog's `ProcessorFormatter` keeps working (F-5, D-1, D-2); removed text is marked
  `[redacted: database server text]` (F-7); guarded attribute access (D-3). `diagnose` also warns when the database
  directory is group- or other-writable with the sticky bit (#4 re-review DL-1).
- Fixes from the independent review of TAB-013/TAB-014: `connect` refuses an existing file (or side file) owned by
  another user or writable by group or others unless `allow_shared_file` (M-1); `diagnose` warns when the database
  directory is world-writable without the sticky bit (M-2); `backup` checks that SQLite opened the temporary file it
  created before the first page is written, and again before publishing (M-3); the checks run on idle connections
  with driver-managed transactions (L-2); `executescript` in a migration step and `loading_extensions` documented
  (L-1, I-3); 26 mutation probes (L-3).
- Fixes from the delta re-review of TAB-010..TAB-012: `DbErrorRedactionFilter` no longer replaces a record whose
  container argument raises while it is iterated, and reads containers lazily (at most 1,000 items, review N-2 and
  the `islice` part of N-3); a misplaced docstring in `tabrion.errors` (N-5).

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
  given connection. Errors are never retried. A key held on a lost connection raises
  `UsageError("postgres.lock_lost")` until `holds` or `release` acknowledges the loss; a forked child gets a fresh
  internal lock; `lock_key` raises `ValueError` for a string without a UTF-8 form.
- `tabrion.postgres.grant_runtime_role` (TAB-031), unifying Axentra's and Nodestro's: run as the owner role, it grants
  the runtime role `USAGE` on the schema, `SELECT, INSERT, UPDATE, DELETE` on its tables, `USAGE, SELECT` on its
  sequences, `EXECUTE` on its functions and the same default privileges, revokes the write privileges on each of
  `protected_tables` that exists in the schema, all in one transaction, then grants `CONNECT` where the owner may,
  and returns a fresh `inspect_role`. Idempotent; never `CREATE`. Refuses (`RoleUnsafe`, nothing granted) the owner
  itself, a role that can become the owner or a superuser or BYPASSRLS role (also as a NOINHERIT member), and a role
  owning tables of the schema or owning the schema itself (or able to become its owner); refuses a schema that does
  not exist (`UsageError("postgres.schema_missing")`), names over 63 bytes (`ValueError`) and running inside the
  caller's transaction (`UsageError("postgres.nested_transaction")`). The catalog afterwards equals the Axentra and Nodestro results
  (golden files of both reference functions).
- In psycopg pipeline mode every `tabrion.postgres` function syncs the caller's pipeline first, so a failing statement
  no longer aborts the caller's pipeline transaction (review DL-1 of #5); an error of a statement the caller queued
  propagates from every function, `ping` included, and `wait_advisory_lock` syncs each attempt before it sleeps.
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
  transaction state, and carry 58 mutation probes against the server. The extra `dev` adds `psycopg-pool` for the
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
