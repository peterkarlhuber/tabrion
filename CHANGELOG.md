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
