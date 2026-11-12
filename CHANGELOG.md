# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/)
with the 0.x rules of the specification (section 10) and ADR 0009.

## [Unreleased]

### Added

- Dependency policy (TABRION QA-17, AC-2): `dependency-policy.json` is this repository's copy of the dependency policy
  K-33 from nodestro-studio (only its outgoing edges: none, tabrion depends on no Levuvo package, P6), and
  `scripts/check_dependency_policy.py` is the unchanged checker from there; `scripts/DEPENDENCY_POLICY_SOURCE.md`
  records their origin and SHA-256. `python scripts/check_dependency_policy.py` checks `pyproject.toml` (core, every
  extra and dependency group) against the copy. `tests/architecture/test_dependency_policy.py` checks the checksums,
  runs the checker and probes forbidden requirements (a platform, a library, a studio, an unknown Levuvo package).
  Ruff skips the vendored script (it is linted in nodestro-studio). The sdist ships the three files, since it ships
  the tests.
- Conventions audit (QA-22): report `docs/program/reports/qa-22-conventions.md` (both new repositories against the
  checklist and against orkexa and axentra) and `tests/security/test_conventions.py` (license, security policy,
  community files, Dependabot policy with a red-tested no-auto-merge check, CI manifest).

- Supply chain (QA-8): `scripts/sbom.py` writes a deterministic CycloneDX 1.6 SBOM for a built wheel (standard
  library only); packaging tests check that two builds give the same wheel hash and that the SBOM describes the
  wheel. The build backend is pinned exactly (`hatchling==1.33.0`, and with hashes in `build-constraints.txt`), so
  the wheel hash is reproducible for that backend, also offline from a local wheel cache. Report
  `docs/program/reports/qa-8-supply-chain.md`.

- Blocked-module tests per optional edge (AC-3, TABRION QA-18): `tests/packaging/edges.py` lists each extra
  (`postgres`, `sqlalchemy`, `migrations`, `testing`) with what imports without it and what refuses with the
  installation hint; `tests/unit/test_optional_edges.py` runs one fresh `python -I` per edge with the extra's modules
  blocked, checks that the list equals the extras of `pyproject.toml` and turns red when a copy of the package imports
  the extra at the top of a module. `tests/packaging/test_independent.py` imports every module while orkexa, axentra,
  `nodestro_*` and `tabrion_studio` are blocked by a meta path finder (document 05, section 3.2), with probes. Tests
  only.

- User guides for 0.1 (TAB-041): a quickstart and guides for SQLite, PostgreSQL, layer B (`Database`, `Profile`),
  transactions, errors, `tabrion.sql`, `tabrion.config`, migrations, `tabrion.check`, `tabrion.testing`,
  portability, security, the PostgreSQL infrastructure baseline, existing platforms and troubleshooting. Every code
  example runs in the test suite (`tests/docs/`, PostgreSQL pages in the pg jobs). Installation stays Git-SHA pins,
  no PyPI. The migrations guide gives Alembic a profile of its own (SQLite foreign keys off before a batch rebuild,
  `PRAGMA foreign_key_check` afterwards, longer PostgreSQL timeouts), because a batch rebuild with foreign keys on
  deletes child rows through `ON DELETE CASCADE`.

- `docs/api-consumers.md`: which public function each consumer uses and which consumer test covers it (K11).
  `tests/unit/test_api_consumers.py` checks it against contract section 8 (API names per row), the golden record keys
  and the replay tests. `advisory_unlock` is listed as not yet covered by a consumer record.

- The 0.2 Studio APIs (TAB-033), implemented and tested on SQLite and PostgreSQL but **not public yet**: they stay
  staged in `tabrion._sa._v02` until the version is 0.2, so the 0.1 surface does not grow (G-T1 condition C-6).
  `query` returns a frozen `Result` with `Column` names and PostgreSQL type names, cut at `max_rows`; `stream` yields
  batches from a PostgreSQL server-side cursor or SQLite `fetchmany`; `cancel()` from another thread stops the
  running statement within a second (`interrupt()`, `cancel_safe()`), never reaches a later user of the pooled
  connection, and a canceled transaction is never committed; `inspector()` runs on a read-only connection;
  `query` and `stream` send user SQL through the extended query protocol, so PostgreSQL refuses a text with several
  commands (the multi-statement escape of `read()`); experimental `on_begin`, `run_transaction` (retries only
  retryable errors with a known outcome) and `savepoint`. Contract implementation notes I-100..I-106, deviations
  D-120 and D-121. Every SQLite statement of a layer-B transaction now runs with Tabrion's progress handler, so a
  cancel cannot be lost (I-102). A PostgreSQL transaction that sent a cancel request never returns its connection to
  the pool, because a late request would cancel the next user's statement; a `COMMIT`, `ROLLBACK` (also
  `... AND CHAIN`), `END`, `ABORT` or `PREPARE TRANSACTION` of the caller inside `query` or `stream` is refused
  before it is sent, with `UsageError`, and a statement that still ended the transaction is caught afterwards, so
  later statements never run outside the transaction (review and re-review of #32).
- API stability policy (TAB-051, `docs/development/api-stability.md`): rules S-1..S-8, the steps to deprecate
  and remove a stable name, and the downstream notice in the release flow. The internal helper
  `tabrion._deprecated` (not public, no new name in `surface.json`) warns with `DeprecationWarning` naming the
  replacement and the removal version, and refuses a window shorter than S-3. `scripts/api_surface.py` checks every
  deprecation against `surface.json` (`"deprecated"`) and the CHANGELOG, and with `--base <ref>` that every surface
  change since `<ref>` is named under `Deprecated`, `Removed` or `Changed` in `[Unreleased]` (S-6).
- Internal test asset "Levuvo Database Profile" LDP-0.1.0 (TAB-050, K-35; not in the wheel, not vendored by any
  other repository): `conformance/SPEC.md` (33 rules in ten areas, MUST/SHOULD/INFO, with the reference behavior of
  Nodestro, Orkexa and Axentra), the JSON Schema of API contract section 10.1, 115 vectors in `conformance/vectors/`,
  `conformance/MANIFEST.json` and the internal loader `tests/support/conformance.py`. Tabrion passes every vector;
  a changed expectation or action handler fails the tests until the manifest is regenerated, the LDP version is
  raised and this file names the new version and digest. `.gitattributes` keeps LF line endings for the hashed files,
  so the manifest also holds on a Windows checkout with `core.autocrlf=true`.
  LDP-0.1.0 digest d70eeb2a7dc5.

- Reference application `examples/measurements` (RA-B-1): local capture on SQLite with `tabrion.sqlite` only;
  not part of the wheel or the sdist. Its tests (`tests/examples/`) run in the normal suite and skip without
  `examples/`. RA-B-2 adds the idempotent transfer to PostgreSQL through layer B (high-water mark, `ON CONFLICT DO
  NOTHING` on the natural key, the mark moved only after the upstream commit, `transfer --from-start` after
  switching the upstream), tested on SQLite and PostgreSQL. RA-B-3 adds its offline and failure tests: network
  faults through the internal fault proxy, process kills and 24 hours offline simulated, each with 0 losses and 0
  duplicates. RA-B-4 adds the plain SQLAlchemy baseline `examples/measurements-baseline` for RA-EVAL; the same test
  suite runs against both variants.
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
  path. For a `Database` (and `Database.check()`), the dialect's checks run on the pooled driver connection inside
  one `read()`, plus TAB-PG-012, TAB-B-001 and TAB-B-002; TAB-B-002 is an `info` "Not checked" when an
  `alembic_version` table exists, since `check()` has no script directory (TB-060, D-66).
- `tabrion.migrations.status` and `require_current` (TAB-019): the Alembic schema state (current revisions,
  heads, pending revisions in order, unknown newer revisions) read in one `Database.read()` without running
  `env.py` and without migrating; `require_current` raises `SchemaNotCurrent`. `is_current` means nothing
  unknown and nothing pending, so a head covered by `depends_on` counts as applied; a script directory Alembic
  cannot resolve raises `ConfigurationError("migrations.revisions")` without path or cause chain. Only
  `<script_location>/versions` is read (TODO TB-066).
- Reference application `examples/parts` (RA-A-1): a portable schema (material, part, property, part_relation)
  with Alembic on SQLite and PostgreSQL and `require_current` as the gate; decimals are scaled integers on SQLite,
  so they order and compare as on PostgreSQL, and migrations run in `Database.transaction()`; not part of the wheel
  or the sdist.
- `examples/parts` functions (RA-A-2): `parts.catalog` with CRUD, a status life cycle under an optimistic lock,
  exact properties, components, bill of materials and where-used; the same tests on SQLite and PostgreSQL, including
  a lost-update test; the cycle check is serialized (an advisory transaction lock on PostgreSQL), and the bill of
  materials is linear, exact and cycle-safe. Its foreign keys use `NO ACTION` (TODO TB-110).
- `examples/parts-baseline` (RA-A-3): the parts application with plain SQLAlchemy and Alembic, run by the same
  shared suite (`tests/examples/parts_suite.py`); its deviations are strict `xfail`s, measurements for RA-EVAL.
  The parts catalog also refuses values that do not fit a column and bill-of-materials totals that would round to
  0 with `InvalidValue`.

- The canary suite (QA-2, TAB-T-110..113): a unique value in parameters, rows, passwords and secret files, through
  every kind of both engines and both layers; no `str`, `repr`, traceback, stderr output, log record or pytest report
  may hold it (`tests/canary.py`).

### Changed

- `tabrion.sqlite.connect` hardens its open against a time-of-check to time-of-use race (TB-045, contract I-22,
  TM-04): on POSIX it lists the process's open regular files (`/proc/self/fd`, or `/dev/fd` on macOS)
  before and after SQLite opens the database and requires the descriptor SQLite opened to be the checked file
  (`(st_dev, st_ino)`); a mismatch is retried after a short random pause, eight attempts in all, then refused with
  `UnsafePath("sqlite.unsafe_path")`. The final name check now resolves only the directory of the path, not its
  last component again. An existing file this user may not open for its check is now `UnsafePath("sqlite.unsafe_path")`
  instead of being left to SQLite (`sqlite3.OperationalError` before); other failures of that open (for example no
  descriptors left) are raised as `OSError`. A name that disappears during the check is checked again. On Windows, and
  where no complete descriptor listing exists, the existing name checks after opening remain the only check
  (documented in I-22).
- `Database.safe_url` and `repr(Database)` show a SQLite file path as it is instead of percent-encoded, on every
  OS: a Windows path (`C:\...` instead of `C%3A%5C...`), and on POSIX too a path with `@`, `%`, `:`, `?` or
  non-ASCII characters (`/tmp/ü.db` instead of `/tmp/%C3%BC.db`). A character that is not printable (newline, ESC,
  BEL, DEL, bidi overrides) is still percent-encoded, so a file name cannot forge a log line, and the path goes in
  after the URL is masked, so `:` and `@` in a path never look like credentials (TB-074). The output is now the
  same under SQLAlchemy 2.0 and 2.1: 2.0 rendered the path raw (control characters included, and `C:\a@b\x.db`
  was misread as credentials by the masking), 2.1 percent-encodes it.
- Windows (issue #18, TB-134): `scripts/downstream.py` hands uv the interpreter file `Scripts\python.exe` (new
  `venv_python()`), so a downstream run works there; the downstream and golden-replay tests assert the Windows
  behavior (psycopg's `ConnectionTimeout` for a refused local connect, no POSIX mode in the replay).
- CI (workflow change, TODO TB-017): `ci.yml` has the jobs `conformance` (TAB-050), `golden files` (TAB-022) and
  `examples` (RA-A, RA-B), each listed in `.github/ci-manifest.json`; on a pull request the job `api surface` also
  runs `scripts/api_surface.py --base <merge base>` (S-6, TAB-051). `nightly.yml` has the non-blocking job
  `downstream` (S-7, TAB-022b). `release.yml` has the job `downstream-notice`, a dry run that prints the consumer
  issues of the release flow and opens none. `platforms.yml` also runs on pull requests that change `conformance/**`,
  `tests/conformance/**`, `config.py`, `errors.py`, `testing/sqlite.py`, `pyproject.toml`, `scripts/downstream.py`,
  `tests/unit/test_downstream.py` or `tests/unit/test_golden_consumers.py` (TB-021, TB-134). No new secret, trigger or
  write permission.
- Documentation (owner decision of 2026-11-08 11:26Z): Tabrion is distributed through Git SHA pins, a PyPI
  publication is not planned for now, and the environment `pypi` is needed only if one is decided later. `v0.1.0`
  final (TAB-040) is the merge commit of the version-bump pull request that sets `0.1.0`, pinned by full SHA; its
  SHA will be recorded in the section `[0.1.0]`, and master moves to `0.1.1.dev0` right after. pip does not refuse an
  in-range dev or pre-release pin; only AC-6 does. Tags are optional labels (ADR 0009, amendment of 2026-11-08). Branch protection, private vulnerability reporting and the social
  preview are optional repository hardening; gate G-T2 no longer requires a trademark register check. Every public
  publication still needs the owner's approval G-R per version.
- A `read()` refused by the read-back now also invalidates a file-backed SQLite connection, so an attached database
  or a raised attach limit does not stay in the pool (re-review 2 R-2 of #12). `tabrion.migrations` maps a
  `revision` or `down_revision` of the wrong type to `ConfigurationError("migrations.revisions")` (DL-1 of #14).
- `classify(error, during_commit=True)` on a `TabrionError` whose stored classification is a connection error now
  reports the outcome as unknown and not retryable (R-NORETRY, TB-040). `Database()` and `Transaction()` raise
  `TypeError` instead of building an unusable object (G-T1 F-14).
- Master builds as `0.1.0a2.dev0` after the `0.1.0a1` commit (TB-053), so later commits never build as the
  internal pre-release. The installation guide pins the a1 commit SHA instead of the tag name and says not to run
  `release.yml` for it (TAB-038 review Info notes).

### Fixed

- `classify` maps a SQLite error whose code the `sqlite3` module does not name (`sqlite_errorname == "unknown"`,
  for example `SQLITE_CONSTRAINT_DATATYPE` of a STRICT table on Python 3.13) by its number: `DATA` instead of
  `UNKNOWN` (contract I-75).
- The PostgreSQL fixtures of `tabrion.testing` no longer print the password when the server is unreachable: pytest
  showed the conninfo argument of psycopg's frames; the test now fails with the masked URL (contract I-75). A failed
  `CREATE ROLE` or membership `GRANT` (an admin without `CREATEROLE`) fails the test the same way, without the
  generated role password. A caller of `truncate_tables` with an unreachable URL gets pytest's `Failed` instead of
  `psycopg.OperationalError`.
- Layer B and `tabrion.config` keep values out of the frame locals of their tracebacks, which Sentry, `rich` and
  `pytest -l` show: the statement parameters and SQL text (`execute`, `fetch_*`, on `Database` and `Transaction`;
  the staged 0.2 `query` and `stream` with the rows of an earlier batch; also when `Canceled` or a `KeyboardInterrupt`
  leaves them), the fetched rows of `MultipleRowsError`, the URL and `engine_options` of `from_url` and `from_env` (every
  exception of `from_url`, also a `TypeError` of `create_engine` for an unknown engine option, is raised without
  the frames of its helpers), the environment and the file
  content in `url_from_env`, and the original error of `BackupFailed` and `MigrationFailed` in `tabrion.sqlite.migrate`
  (contract I-76).
- `Database.from_url` creates its engine with `hide_parameters=True`: SQLAlchemy's `sqlalchemy.engine` log records
  and the `[parameters: ...]` part of its error messages leave the values out (`engine_options={"hide_parameters":
  False}` turns them back on; contract I-76).

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
- `tabrion.testing` (TAB-020), activated with `pytest_plugins = ["tabrion.testing"]`: `configure` chooses the
  environment variable names (`env_prefix`, or the consumers' own names) and the name prefix; `tabrion_postgres_url`
  skips without a server and fails under the require variable, and masks the password in pytest's reports;
  `tabrion_postgres_schema` gives every test its own schema and drops it afterwards, also after a failure and with a
  connection of the same role still holding locks (another role's session is waited for, never ended), but never
  cascades into an extension; `postgres_server_version`; `require_extension` never moves an extension: it creates a
  missing one in `public` and makes one in another schema visible through the connection's `search_path`, or skips
  (fails when required); a require variable counts unless it is empty or `0`; `tabrion_sqlite_path` and
  `tabrion_sqlite`. Tabrion's own PostgreSQL suites run on it and check after each test that nothing they created is
  left on the server.
- Consumer protection (TAB-022): golden files of the consumers' behavior for every category A/B row of contract
  section 8 (`tests/golden/{axentra,nodestro,nodestro_api,orkexa}.json`, each with the full commit), recorded by
  `scripts/record_golden.py` from the consumers' own code in throwaway environments and replayed against Tabrion in
  every test run, each naming the consumer's full 40-character commit; `scripts/downstream.py` runs the consumers'
  database tests against Tabrion HEAD, which `uv pip install --override` puts in place of any Tabrion requirement
  the consumer has (a Git commit pin), with all extras, with a timeout on every step; a database that cannot be
  dropped is a note beside the consumer's result.
- `tabrion.testing` re-review fixes (#11): `truncate_tables` without `schema=` empties only a schema the plugin created
  and refuses a `CASCADE` into another schema; a schema teardown that would drop an extension is rolled back; the
  fixtures work under a `NOINHERIT` admin on PostgreSQL 16+, try every role at the end and drop the roles' grants in
  the admin's database. `truncate_tables` locks the selected tables before its check and names why a table in
  another schema would be emptied (foreign key, partition, inheritance child); it checks again after the `TRUNCATE`
  and rolls it back when a foreign key was added in between (review L-1 of #19).
- `tabrion.testing` database per session and roles without superuser (TAB-032): `tabrion_postgres_database` creates a
  throwaway database with an owner and an app role as `tabrion_role_setup` describes (`RoleSetup()`, `split=False`,
  named roles, or none) and drops database and roles at the end; `tabrion_postgres_roles` gives the same two roles in
  the existing database; the runtime-role mode of `tabrion_postgres_schema` adds a runtime role per test with the
  grants of `grant_runtime_role`; `truncate_tables` empties a schema's tables in one `TRUNCATE ... RESTART IDENTITY
  CASCADE`. Every role is `NOSUPERUSER NOBYPASSRLS` with a random password, and a role the fixtures did not create
  is never changed or dropped. A golden test checks Axentra's `tests/conftest.py` against the same setup on the
  plugin.
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
  transaction state, and carry 101 mutation probes against the server. The extra `dev` adds `psycopg-pool` for the
  pool tests and `uv` for `scripts/downstream.py`.

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
