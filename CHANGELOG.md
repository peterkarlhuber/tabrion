# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/)
with the 0.x rules of the specification (section 10) and ADR 0009.

## [Unreleased]

### Changed

- Master builds as `0.4.0.dev0` after the `0.3.0` release commit `7488c764327ae428718ffc9492a6aaed73a06cef` (the TB-053 pattern), so
  later commits never build as `0.3.0`. The release commit's full SHA is recorded in `[0.3.0]` below, in the
  installation guide and in STATUS G-T2. `0.4.0.dev0` is outside `>=0.3,<0.4` (PEP 440: `<0.4` excludes the
  pre-releases of 0.4), so a consumer on the 0.3 range never resolves to this master.

## [0.3.0] - 2026-11-18

TABRION TAB-XW-10, gate G-T2 (passed with conditions, planning folder `reviews/g-t2-0.3-review.md`): `v0.3.0` final,
the CR-DBX release (TAB-XW-1..TAB-XW-9, TB-149). The release commit is the merge commit of the pull request that sets
this version (#95): `7488c764327ae428718ffc9492a6aaed73a06cef` (recorded by the follow-up pull request #96; ADR 0009,
amendment of 2026-11-08). Consumers pin that commit by its full SHA via `git+https`; a tag `v0.3.0` on it is an optional label and
needs the owner's explicit approval. No PyPI upload, no GitHub release and no announcement (G-R). It satisfies
`tabrion>=0.3,<0.4` and not `>=0.2,<0.3`, so Axentra, Nodestro and Orkexa stay on `0.2.2` until they move their
range. **New names:** the guided write transaction (`Transaction.execute_checked`, `tabrion.rowedit`,
`RowCountMismatch`), `tabrion.schema` level 2, `tabrion.ddl`, `tabrion.plan` with `execute_plan`, SQLite `restore`
and `delete_database_file`, PostgreSQL `create_database`, `drop_database`, `database_sessions` and the role DDL, all
**stable (S)** at this release, although the entries below were written while they were experimental (see Changed,
TB-149 and 7A). 312 public names (`scripts/api_surface.py --base 5bc690e` consistent, no stubs); still experimental
(X): `Database.run_transaction`, `Database.on_begin`, `Transaction.savepoint` and `tabrion.profilefile`. **No 0.2
stable name changes**; `tabrion.schema.compare` (experimental in 0.2) takes `expected` and `actual` by keyword only.
The psycopg floor of the extras is `>=3.2.3`. Between `0.2.2` and this release master built as `0.3.0.dev0` (from the
0.3 switch after `5bc690e2de74a92cd44bb4522cf2533cc2c8d14f`).

### Added

- **Experimental, 0.3** (TABRION TAB-XW-5, CR-DBX; contract section 5.13, I-125, moved from addendum A.4): the
  executor `tabrion.plan.execute_plan(database, target, *, plan_hash, expected_fingerprint, environment, ...)` and
  its result `tabrion.plan.ExecutionResult`. It runs a confirmed schema change on a database the caller declares
  `"dev"` or `"test"` (required keyword, no default; ADR 0015 D-3) that has no migration markers, and never takes a
  plan: inside its own transaction it takes a level-2 snapshot, checks it against `expected_fingerprint`, plans again
  from it and the target model and runs only a plan whose hash is the confirmed `plan_hash` and whose risk classes
  are in `allow` (review of #89, P89-03). On PostgreSQL it locks the tables the steps change (`READ COMMITTED`,
  `SET LOCAL lock_timeout`) and repeats the fingerprint and the plan under the locks (ADR 0015 D-5.4, N-1); a lock
  timeout raises `TransientError` with `details["step"]`; `CREATE`/`DROP INDEX CONCURRENTLY` steps run alone in plan
  order. On SQLite foreign keys are turned off before `BEGIN IMMEDIATE` (refused while they stay on, reason
  `foreign_keys_on`), `trusted_schema` off and defensive mode on (Python 3.12 and later), each statement runs alone
  under the caller's authorizer, and `foreign_key_check` and `integrity_check` run before the commit (P89-01, K-1).
  `dry_run=True` rolls back. New errors `tabrion.errors.SchemaDrift` (`plan.drift`, with the differences from the
  preview's snapshot `source`) and `tabrion.errors.PlanFailed` (`plan.failed`, details `step`, `completed`, `state`);
  new `PlanMismatch` codes `plan.hash_mismatch` and `plan.fingerprint_mismatch`; new `PlanRefused` reasons
  `environment`, `migration_managed`, `risk_not_allowed` and `foreign_keys_on`. Review of #91: a plan of
  `DROP INDEX CONCURRENTLY` steps only returns a complete result (P91-01), and the transaction after leading drops
  reads the migration markers again (P91-02, `PlanFailed` state `partial`, `check` `migration_managed`).
- **Experimental, 0.3** (TABRION TAB-XW-4, CR-DBX; contract section 5.13, I-124, moved from addendum A.3): schema
  change plans in the new module `tabrion.plan` (extra `sqlalchemy`; column steps also `migrations`, as in
  `tabrion.ddl`). `tabrion.plan.plan(current, target)` diffs two
  level-2 snapshots into a `tabrion.plan.MigrationPlan` of numbered `tabrion.plan.PlanStep`s (the DDL of
  `tabrion.ddl`, sorted `tabrion.plan.RiskClass`es: `additive`, `data_changing`, `destructive`, `rebuild`, `locking`,
  `non_transactional`; whether the step is transactional), ordered so dependencies hold and bound to both
  fingerprints by `plan_hash`, SHA-256 over the canonical JSON; `MigrationPlan.from_json` recomputes it and refuses an
  edited plan (`PlanMismatch("plan.invalid")`; integrity, not authenticity, ADR 0015 N-2). On SQLite a change it
  cannot make in place becomes one `rebuild_table` step without `RENAME` (holder table, drop, create from the target,
  copy back, indexes, triggers), so views and referencing tables stay valid; trigger texts from `sqlite_schema` must
  be one complete `CREATE TRIGGER <own name>` statement (K-1, `PlanRefused` reason `unsafe_schema_sql`);
  `server_version` allows `DROP COLUMN` in place from 3.35 and `concurrent_indexes=True` writes PostgreSQL index steps
  `CONCURRENTLY`. `tabrion.plan.revision_text` writes the plan as Alembic revision text (fixed docstring, every caller
  value an `ascii()` literal, revision ids `[0-9a-zA-Z_]{1,64}` or `plan.invalid_revision`, `op.execute(sa.text(...))`
  with `:` escaped, `autocommit_block` for `CONCURRENTLY`, an engine check, a `downgrade` that raises; K-3); Tabrion
  never writes the file or runs `env.py`. `tabrion.plan.migration_markers` reads the catalog for `alembic_version`, the
  Nodestro and Axentra version tables and SQLite `user_version`. New errors `tabrion.errors.PlanError`,
  `tabrion.errors.PlanMismatch` and `tabrion.errors.PlanRefused` (reasons `dialect_mismatch`, `engine_unsupported`,
  `unsupported_feature`, `unsafe_schema_sql`). Nothing executes a plan yet (TAB-XW-5).
- **Experimental, 0.3** (TAB-XW-4, ADR 0015 D-5; contract I-111 i): the level-2 schema model reads the D-5 inputs.
  New frozen dataclasses `tabrion.schema.TriggerShape`, `tabrion.schema.PolicyShape` and
  `tabrion.schema.FunctionShape`; new `TableShape` fields `triggers`, `policies`, `functions` (the trigger and policy
  functions with their whole definition), `row_security`, `force_row_security` and `features`, and
  `SchemaSnapshot.features` (PostgreSQL event triggers); feature markers for what the shape does not describe
  (PostgreSQL partitioning, inheritance, foreign tables, rules, domain columns, `UNLOGGED`, typed tables, storage
  parameters, replica identity, access methods, `NO INHERIT`, unknown relation kinds; SQLite `STRICT`,
  `WITHOUT ROWID`, `AUTOINCREMENT`, `ON CONFLICT`, column collations, column-level `DEFERRABLE`, virtual tables). A
  change of any of them changes the `v2:` fingerprint and is named by `compare` (new kinds `trigger_missing`,
  `trigger_added`, `policy_missing`, `policy_added`, `function_missing`, `function_added`, `row_security`,
  `feature_missing`, `feature_added`); a table without them keeps its `v2:` fingerprint. `tabrion.plan` refuses to
  change a table with a PostgreSQL marker or a trigger or policy function in a language other than PL/pgSQL or SQL,
  any change under an event trigger, and a SQLite rebuild that would lose a marked clause (`PlanRefused`, reason
  `unsupported_feature`); `tabrion.ddl` refuses the new fields (I-123 e). The split of D-5 between TAB-XW-4 (inputs
  and plan refusal) and TAB-XW-5 (locks and the repeated drift check) is recorded in ADR 0015 section 10.
- **Experimental, 0.3** (review of #89): the revision of a SQLite plan with a table rebuild or drop refuses to run
  while `PRAGMA foreign_keys` is on (a `DROP TABLE` would delete child rows through `ON DELETE CASCADE`) and ends
  with `PRAGMA foreign_key_check`. The level-2 snapshot reads SQLite views (`tabrion.schema.ViewShape`,
  `SchemaSnapshot.views`, difference kinds `view_missing` and `view_added`); a plan refuses to drop a column or table
  that the text of a view or trigger names (`PlanRefused`, reason `dependent_object`) and checks every view after a
  rebuild or table drop. Risk classes and statement heads are checked against each step's operation on every plan,
  so `MigrationPlan.from_json` refuses a drop labeled additive. `CONCURRENTLY` index steps run first (drops) and last
  (creations). `revision_text` and `PlanStep` take only exact `str` values.
- **Experimental, 0.3** (TABRION TAB-XW-3, CR-DBX; contract section 5.12, I-123, moved from addendum A.2): DDL
  generation from the level-2 schema model in the new module `tabrion.ddl` (extra `sqlalchemy`; column statements
  also need `migrations`). `tabrion.ddl.create_statements` writes `CREATE TABLE` for every table of a model (referred
  tables first; on PostgreSQL a foreign key cycle as `ALTER TABLE ... ADD CONSTRAINT` after the tables) and then every
  `CREATE INDEX`; `tabrion.ddl.table_statements`, `tabrion.ddl.drop_table_statement`,
  `tabrion.ddl.add_column_statement`, `tabrion.ddl.drop_column_statement`, `tabrion.ddl.alter_column_statements`,
  `tabrion.ddl.add_constraint_statement`, `tabrion.ddl.drop_constraint_statement`,
  `tabrion.ddl.create_index_statement` and `tabrion.ddl.drop_index_statement` write one change each, as the frozen
  dataclass `tabrion.ddl.DdlStatement` (`sql`, `target`, `transactional`). The text is compiled by SQLAlchemy Core
  (`CreateTable`, `CreateIndex`, `AddConstraint` ...) and Alembic's DDL constructs for SQLite and PostgreSQL without a
  connection; identifiers are quoted by the dialect, nothing runs. Columns with defaults and PostgreSQL serials,
  primary keys, foreign keys with actions, `DEFERRABLE` and `MATCH FULL`, unique constraints (PostgreSQL `INCLUDE` and
  `NULLS NOT DISTINCT` where the installed SQLAlchemy writes them), checks, and indexes with expressions, order,
  collations, operator classes, access methods, `INCLUDE`, `NULLS NOT DISTINCT` and predicates round-trip: the
  statements run on an empty database reflect back to the same model and `v2:` fingerprint, on SQLite and
  PostgreSQL 16, with SQLAlchemy 2.0.30 and 2.1. Default, check, expression and predicate texts are schema text from
  reflection, written as they are; they are refused when they could leave their place in the statement (`;`,
  comments, unbalanced parentheses, a top-level comma; on PostgreSQL a backslash or `$`; `ddl.unsafe_sql_text`). A
  type the dialect does not know, a PostgreSQL identifier above 63 bytes and every feature the module cannot generate
  (exclusion constraints, `NOT VALID`, invalid indexes, unknown access methods, any shape field it does not know: ADR
  0015 D-5.3) are refused, never generated without it; SQLite changes it cannot make in place raise
  `ddl.rebuild_required` for the plan of TAB-XW-4. Review of #87: identity and generated columns are refused
  (`identity_column`, `generated_column`) instead of being written as plain columns (M-2), and a table name with more
  than one dot (`s.x.y`, which could name `s."x.y"` or `"s.x".y`) raises `ddl.invalid_shape` (M-1).
- **Experimental, 0.3** (review of #87; contract I-111 b, e, f, g): the level-2 schema model reads identity and
  generated columns into the new `tabrion.schema.ColumnShape` fields `identity` (`ALWAYS`, `BY DEFAULT`),
  `generated` (`STORED`, `VIRTUAL`) and `generation_expression` (PostgreSQL `pg_attribute`, SQLite
  `pragma_table_xinfo` and the `CREATE TABLE` text), with the new difference kinds `column_identity` and
  `column_generated`; a format-2 column has seven entries. The PostgreSQL column collation is read from
  `pg_attribute.attcollation`, so the floor SQLAlchemy 2.0.30 gives the same type text (`TEXT COLLATE "C"`) and
  `v2:` fingerprint as 2.1. At level 2 a schema or table name with a dot raises `schema.unsupported_name`
  (`snapshot` and a level-2 `SchemaSnapshot`); level 1 is unchanged (D-4).
- **Experimental, 0.3** (TABRION TAB-XW-7, CR-DBX; contract section 3.10, I-170, I-171, moved from addendum A.6):
  `tabrion.sqlite.restore` copies a backup file into a database with SQLite's online backup API in one step (atomic
  for the target's other connections, also in WAL mode), checks the source with `integrity_check` before anything is
  written and the target after the copy, creates a new target `0600`, never overwrites a file that is not a SQLite
  database, and returns `tabrion.sqlite.RestoreResult`; a target another connection holds is refused.
  `tabrion.sqlite.delete_database_file` deletes a database file with its `-wal`, `-shm` and `-journal` files after an
  exclusive-lock check, refuses links and files that are not SQLite databases, and works through a directory
  descriptor opened with `O_NOFOLLOW` with a device and inode check right before the unlink (ADR 0015 N-4); side files
  that are links are removed as links. Standard library only. New error class `tabrion.errors.DatabaseInUse`
  (`sqlite.in_use`, `postgres.database_in_use`). Security review of #88 folded in: `delete_database_file` refuses a
  database whose directory the group or others may write, sticky bit or not (`sqlite.unsafe_mode`), and reports a
  missing directory (`FileNotFoundError`, `()` with `missing_ok`) or a path component that is no directory
  (`sqlite.unsafe_path`) without the path; both functions `lstat` a file before they open it and never open a
  directory, FIFO or device; `restore` reports a source that SQLite finds damaged while opening it (such as a header
  with a page count beyond the file) as `sqlite.restore_source_corrupt` instead of the driver's `DatabaseError`.
- **Experimental, 0.3** (TABRION TAB-XW-8, CR-DBX; contract section 4.9, I-180..I-186, moved from addendum A.7):
  role DDL with the attribute ban, also indirect (owner decision E-X6). `tabrion.postgres.role_privileges` reads a
  role's attributes, direct memberships, every role reachable through membership (INHERIT, SET and ADMIN option
  alike), findings (`dangerous`: SUPERUSER, BYPASSRLS, CREATEROLE or REPLICATION on the role or a reachable role,
  CREATEROLE before PostgreSQL 16, TRIGGER on a relation, EXECUTE on a foreign SECURITY DEFINER function, CREATE on
  `public` or a foreign schema; `broad`: reachable predefined roles) and its owned objects and default privileges, as
  the frozen dataclass `tabrion.postgres.RolePrivileges`. `tabrion.postgres.create_role`,
  `tabrion.postgres.alter_role` (with `tabrion.postgres.RoleOptions`: login, inherit, create database, connection
  limit, expiry; no field for the four banned attributes), `tabrion.postgres.drop_role`,
  `tabrion.postgres.grant_membership`, `tabrion.postgres.revoke_membership`, `tabrion.postgres.grant_privileges` and
  `tabrion.postgres.revoke_privileges` run on PostgreSQL 16 and later in a transaction of their own, check the target
  right before and again right after the statement, compose every name with `psycopg.sql.Identifier`, never grant
  `WITH ADMIN OPTION`, `WITH GRANT OPTION`, TRIGGER or membership in a predefined role, and refuse with `RoleUnsafe`
  (new reasons such as `reachable_superuser`, `predefined_role`, `security_definer`, `schema_create`, `trigger`,
  `owns_objects`, `current_user`). A password is hashed on the client and sent only as a SCRAM-SHA-256 verifier; it
  is never in an error or a traceback frame. Driver errors are raised as the `DatabaseError` of their kind with the
  safe message. `tabrion.profilefile` accepts the profile mode `admin` (addendum A.9, E-X8). Security review of #90
  folded in: every change refuses a session whose current or session user has SUPERUSER, BYPASSRLS or REPLICATION
  (`RoleUnsafe("postgres.caller_unsafe")`); `grant_privileges` refuses objects of the system (`system_object`) and
  objects whose owner has one of these attributes or is or reaches one of the three server-access roles below
  (`object_owner`; the latter from the review Info of #90); a role that is or reaches
  `pg_execute_server_program`, `pg_read_server_files` or `pg_write_server_files` counts as one with a banned
  attribute (`server_access:<role>` in `dangerous`, reason `reachable_server_access`); new field
  `RolePrivileges.public_definers` (SECURITY DEFINER functions PUBLIC may execute, a superuser owner highlighted);
  `create_role` refuses names with control, format or separator characters; passwords are limited to 1 024
  characters.
- **Experimental, 0.3** (TABRION TAB-XW-9, CR-DBX; contract section 4.8, I-190..I-193, moved from addendum A.8):
  `tabrion.postgres.create_database` and `tabrion.postgres.drop_database` compose `CREATE DATABASE`/`DROP DATABASE`
  with `psycopg.sql.Identifier` and validated literals, need an idle autocommit connection (never switched), check
  `CREATEDB` and the owner before creating, refuse `postgres`, the templates and the connection's own database, and
  refuse a database with other sessions unless `force=True` (`WITH (FORCE)`); `tabrion.postgres.database_sessions`
  counts the other sessions for a preview.
- **Experimental, 0.3** (TABRION TAB-XW-2, CR-DBX; contract section 5.10, I-111, moved from addendum A.1): the schema
  model v2. `tabrion.schema.snapshot(..., level=2)` also reads column defaults, foreign keys, unique and check
  constraints and indexes on SQLite and PostgreSQL, into the new frozen dataclasses `tabrion.schema.ForeignKeyShape`,
  `tabrion.schema.UniqueShape`, `tabrion.schema.CheckShape` and `tabrion.schema.IndexShape`, including
  `DEFERRABLE`/`INITIALLY DEFERRED` of foreign and unique keys, PostgreSQL `INCLUDE` and `NULLS NOT DISTINCT`,
  partial index predicates, the order, collation and operator class of index elements, the PostgreSQL access
  method and `indisvalid`, unique constraint collations, `NOT VALID` and `MATCH`, and PostgreSQL exclusion constraints
  in the new `tabrion.schema.ExclusionShape` (kinds `exclusion_missing`, `exclusion_added`); `IndexShape.expressions`
  marks expression elements. The blind spots that remain (triggers, policies, domain checks, partitioning,
  `UNLOGGED`, storage parameters) are listed in I-111 d. SQLite index text is parsed
  without regular expressions in linear time (review of #85, F1); SQLite unique constraints and foreign keys come
  from the pragmas, without touching the warning filters (F3, F5); SQLite CHECK constraints come from a linear
  tokenizer over the `CREATE TABLE` text, because SQLAlchemy 2.0.30 (the floor) merges two checks into one; PostgreSQL
  `INCLUDE` columns come from `pg_index`, which SQLAlchemy 2.0.30 does not report for a unique constraint.
- Docs: ADR 0015 section 10 records the owner decision D-5 of 2026-11-15 on the drift blind spots of the `v2:`
  fingerprint (review of #85, F2): triggers and RLS policies with their directly referenced functions, domain checks
  and partitioning, an allowlist for unknown PostgreSQL features, a drift check after locking, and a notice in the
  Data Editor. Implemented in TAB-XW-3..TAB-XW-5; G-S3 stays locked until then. TODO TB-149 and contract I-111 d. Its fingerprint is
  `v2:sha256:<hex>` over the canonical JSON format 2, in which the order of constraints and indexes does not count.
  `compare` names the new kinds `column_default`, `foreign_key_missing`/`_added`, `unique_missing`/`_added`,
  `check_missing`/`_added` and `index_missing`/`_added` (only `index_added` is not breaking), with the constraint or
  index name; a changed constraint is missing and added. On SQLite the foreign key actions and the indexes are read
  from the pragmas, because SQLAlchemy misses the actions of a column-level `REFERENCES` and leaves out an expression
  index; `NO ACTION` is `None` on both engines. **Level 1 stays the default and its `v1:` fingerprints stay
  byte-identical** (ADR 0015 D-4; `tests/golden/schema_v1.json`, recorded with the 0.2 code). A level-2 snapshot is
  also the target model for `tabrion.ddl` (TAB-XW-3) and `tabrion.plan` (TAB-XW-4). No DDL is generated.

### Changed

- **Stable, 0.3** (TB-149, before the G-T2 review of 0.3, TAB-XW-10; contract sections 0, 3.10, 4.8, 4.9, 5.10,
  5.12 and 5.13): the remaining experimental (X) names of the CR-DBX building blocks change to stable (S) in
  `docs/api/surface.json`, with their error codes, `details` keys and refusal reasons. They change from now on only
  after a `DeprecationWarning` (S-3):
  - `tabrion.sqlite.restore`, `tabrion.sqlite.RestoreResult`, `tabrion.sqlite.delete_database_file` (section 3.10);
  - `tabrion.postgres.create_database`, `tabrion.postgres.drop_database`, `tabrion.postgres.database_sessions` and
    `tabrion.errors.DatabaseInUse` (section 4.8);
  - the role DDL `tabrion.postgres.RoleOptions`, `RolePrivileges`, `role_privileges`, `create_role`, `alter_role`,
    `drop_role`, `grant_membership`, `revoke_membership`, `grant_privileges` and `revoke_privileges` (section 4.9);
  - every name of `tabrion.schema` (section 5.10), including the 0.2 names (`ColumnShape`, `TableShape`,
    `SchemaSnapshot`, `SchemaDifference`, `snapshot`, `fingerprint`, `compare`);
  - every name of `tabrion.ddl` (section 5.12);
  - every name of `tabrion.plan` (`RiskClass`, `PlanStep`, `MigrationPlan`, `plan`, `revision_text`,
    `migration_markers`, `ExecutionResult`, `execute_plan`) and `tabrion.errors.PlanError`, `PlanMismatch`,
    `PlanRefused`, `SchemaDrift` and `PlanFailed` (section 5.13).

  Still experimental (X), unchanged: `tabrion.Database.run_transaction`, `tabrion.Database.on_begin`,
  `tabrion.Transaction.savepoint` and `tabrion.profilefile`. `tests/unit/test_api_surface.py::test_cr_dbx_names_are_stable`
  pins the promotion in `surface.json` and contract section 0.
- **Breaking for `0.3.0.dev0` callers** (TB-149, before the promotion above; the rule of the G-S2 follow-up 7A: two
  parameters of the same type, where a swap changes the result without an error, are both keyword-only). A
  positional call raises `TypeError`; nothing else changes (no behavior, check or error code):
  - `tabrion.plan.plan(*, current, target, server_version=None, concurrent_indexes=False)` (before:
    `plan(current, target, *, ...)`; a swap planned the reverse change);
  - `tabrion.ddl.alter_column_statements(table, *, old, new, dialect)` (before: `(table, old, new, *, dialect)`; a
    swap wrote the reverse column change);
  - `tabrion.sqlite.restore(*, source, target, overwrite=False, verify=True)` (before: `(source, target, *, ...)`; a
    swap with `overwrite=True` overwrote the backup with the database);
  - `tabrion.schema.compare(*, expected, actual)` (before: `(expected, actual)`, since 0.2 and experimental; a swap
    turned a missing name into an added one, which is not `breaking`).
- **Stable, 0.3** (G-S2 follow-up 7A, owner decision before the Studio 1.1 release; contract sections 0 and 5.11,
  I-120..I-122): `tabrion.Transaction.execute_checked`, `tabrion.rowedit.insert_row`, `tabrion.rowedit.update_row`,
  `tabrion.rowedit.delete_row` and `tabrion.errors.RowCountMismatch` change from experimental (X) to stable (S) in
  `docs/api/surface.json`. They are the names Studio's Data Editor (STU-026) uses, and consumers use only stable
  names (S-2 of the API stability policy). With them the error codes (`transaction.rowcount_mismatch`,
  `transaction.too_many_rows`, `transaction.limit_required`, `transaction.failed`, `rowedit.primary_key_required`,
  `rowedit.old_required`, `rowedit.new_required`) and `details` keys are stable; from now on they change only after a
  `DeprecationWarning` (S-3). `tests/unit/test_api_surface.py::test_studio_data_editor_names_are_stable` pins the
  stability in `surface.json` and in contract section 0.
- **Breaking for `0.3.0.dev0` callers** (G-S2 follow-up 7A, before the promotion above; no released version had
  these names): `rowedit.update_row` and `rowedit.delete_row` take `old` and `new` as keyword-only arguments,
  `update_row(table, key, *, old, new)` and `delete_row(table, key, *, old)`, so the two mappings of the same type
  cannot be swapped by position; `key` stays positional. A positional `old` or `new` raises `TypeError`. An empty
  `new` raises `UsageError("rowedit.new_required")` (before: `ValueError` without a code), symmetric with
  `rowedit.old_required`.
- **Dependency floor:** psycopg `>=3.2.3,<4` (was `>=3.2,<4`) in the extras `postgres` and `dev` (TB-152). psycopg
  3.2.0 to 3.2.2 ship binary packages with libpq 16, where `cancel_safe()` falls back to psycopg's blocking
  `cancel()`: it has no timeout of its own, and the C implementation holds the GIL while it waits for the cancel
  connection, so `Transaction.cancel()` stalled every thread of the process for that time (the TAB-T-B40 test, whose
  slow cancel proxy runs in the same process, hung with the lowest versions). From 3.2.3 the Linux and macOS binary
  packages bring libpq 17 and the non-blocking, bounded cancel; psycopg 3.2.0's `[binary]` extra could not be
  installed at all (psycopg #853). With a libpq older than 17 (`psycopg[c]` built against one, the Windows binary
  packages, the pure-Python implementation with such a system libpq) the unbounded fallback remains; contract I-102
  and SECURITY.md say so. The lowest-versions CI job installs psycopg 3.2.3.
- `tabrion.schema` (experimental since 0.2, TAB-XW-2; additive under S-4, every new field and parameter has a default
  that keeps the 0.2 behavior): `schema.ColumnShape` gains `default` (`schema.ColumnShape.__init__` takes it as an
  optional last argument); `schema.TableShape` gains `foreign_keys`, `unique_constraints`, `check_constraints` and
  `indexes` (`schema.TableShape.__init__`); `schema.SchemaSnapshot` gains `level` (`schema.SchemaSnapshot.__init__`),
  and a level-1 snapshot with level-2 detail or a level other than 1 or 2 raises `UsageError`
  (`schema.level_mismatch`, `schema.invalid_level`); `schema.SchemaDifference` gains `name`
  (`schema.SchemaDifference.__init__`) and new `kind` values; `schema.snapshot` gains `level=1`; `compare` refuses
  snapshots of different levels (`schema.level_mismatch`); `SchemaSnapshot.from_json` reads format 1 and 2 and
  refuses a `format` that is not an integer (`true`, `1.0`, `2.0`; before, `true` and `1.0` read as format 1).
  `tabrion.profilefile` accepts a `v2:sha256:<hex>` `schema_fingerprint` as well as `v1:` (I-109 e).
- `tabrion.sqlite.delete_database_file` (experimental since 0.3, TAB-XW-7; note N-1 of the security review of #88):
  on POSIX the database's directory must also belong to the effective user or to root, otherwise
  `UnsafePath("sqlite.unsafe_owner")`; before, only its mode bits were checked. ACLs are not evaluated (SECURITY.md).
- Master builds as `0.3.0.dev0` (the 0.3 switch, as TAB-042 did for 0.2; not a release: no tag, no publication):
  `tabrion.Transaction` inherits the 0.3 mixin of `tabrion._sa._v03`, `tabrion.errors.RowCountMismatch` is an
  ordinary exported class (no longer the private `_RowCountMismatch` bound by the version), `tabrion.rowedit` imports
  without a version check, the private test-only module `tabrion._sa._staged` is removed, and `docs/api/surface.json`
  has no `staged` entry any more (240 names, 0 staged). The experimental TAB-XW-6 names of `[0.2.2]` (guided write
  transaction, contract section 5.11) are therefore public; until the switch `tabrion._since` kept them unimportable,
  and `tests/unit/test_v03_gate.py` now checks the public state. `tabrion._since` stays, unused, for a name planned
  for a later minor than the package's; `scripts/api_surface.py` keeps its later-minor check (`MODULE_SINCE` is
  empty). The consumers' range `tabrion>=0.2,<0.3` excludes `0.3.0.dev0` (PEP 440: `<0.3` excludes the pre-releases
  of 0.3), so no consumer resolves to this master by range.
- **0.2.x base:** the last master commit before the 0.3 switch, `v0.2.2` final
  `5bc690e2de74a92cd44bb4522cf2533cc2c8d14f` (#83), is the base commit for a later 0.2.x maintenance branch. The
  TAB-XW-6 code is in the base, but not importable there. Whether a branch or tag is made is the owner's decision;
  none exists yet.

## [0.2.2] - 2026-11-15

TABRION TAB-042 follow-up: `v0.2.2` final, a patch release with the role and profile security fixes TB-150 (#77,
#81), TB-036 (#81) and the profile host check (#82). The release commit is the merge commit of the pull request that
sets this version, `5bc690e2de74a92cd44bb4522cf2533cc2c8d14f` (#83; ADR 0009, amendment
of 2026-11-08). Consumers pin that commit by its full SHA via `git+https`; a tag `v0.2.2` on it is an optional
label. No PyPI upload, no GitHub release and no announcement (G-R). It satisfies the range `tabrion>=0.2,<0.3`.
**No new or removed public names**: the 235 public names are those of `0.2.1` (`scripts/api_surface.py --base
af0458d`: 240 names, the 5 more are staged for 0.3 and not importable below 0.3, TAB-XW-6); `RoleReport` gains
the defaulted field `broad` at the end (see Changed). **Behavior changes**:
`grant_runtime_role` refuses more roles (new `RoleUnsafe` reasons `predefined_role`, `protected_writable` and,
before PostgreSQL 16, `createrole`), `tabrion.check` reports TAB-PG-013 and TAB-PG-014, and `tabrion.profilefile`
refuses a comma in every `host` and `%`, `&`, `=` in a TCP host. Between `0.2.1` and this release master built as
`0.2.2.dev0` (from `af0458d54467571abfc1afdd44d76e1f4d727c06`).

### Added

- Docs: migration follow-ups after the owner accepted the migration report (2026-11-14 18:52Z).
  `docs/ecosystem/migration.md` and `docs/program/STATUS.md` record QA-16 as done including tabrion-studio, the QA-7
  rest, the binding Nodestro pip floor 26.1, `POISON_VERSION` 0.2.99 in every copy, the TB-150 fix and the
  Windows/OneDrive release gate (DSK-021). `docs/TODO.md` TB-012 status note. No code change.
- **Experimental, planned for 0.3** (TABRION TAB-XW-6, CR-DBX; not importable while the version is below 0.3): the
  guided write transaction, contract section 5.11 (moved from addendum A.5). `Transaction.execute_checked(statement,
  params, *, expect_rowcount, max_affected, timeout)` runs one statement and checks its row count; a mismatch raises
  the new `tabrion.errors.RowCountMismatch` (`transaction.rowcount_mismatch`, `transaction.too_many_rows`, counts
  only) and fails the transaction, which rolls back even when the error is caught. The new module `tabrion.rowedit`
  (`insert_row`, `update_row`, `delete_row`) builds one-row statements with an optimistic, NULL-safe predicate on the
  old values (PostgreSQL `IS NOT DISTINCT FROM`, SQLite `IS`), bound parameters only and the exact primary key
  (`rowedit.primary_key_required`) and at least one old value (`rowedit.old_required`); the limits cover the
  statement's own rows, not those of rules or triggers. False conflicts with floating-point and JSON values are documented (N-5). The
  names are in `surface.json` with `since` 0.3 and stability X; `tabrion._since` keeps them unimportable before 0.3,
  and `scripts/api_surface.py` and `tests/unit/test_v03_gate.py` check both states.
- Docs: TABRION TAB-XW-1 (CR-DBX, Tabrion 0.3 write and DDL primitives). ADR 0015
  (`docs/adr/0015-crdbx-write-and-ddl-primitives.md`): DDL and diff on SQLAlchemy Core and Alembic operations
  (E-X3); revision text only for Alembic projects, never `env.py`, project code or `alembic upgrade` (E-X4); direct
  DDL only on a declared DEV/TEST database without migration management; plan binding (plan hash and fingerprint),
  drift check right before execution and dry run; role DDL that never grants SUPERUSER, BYPASSRLS, CREATEROLE or
  REPLICATION, also not through membership (E-X6); `pg_dump` scope limits (E-X5); primitives only, without any
  enable or activate switch, the gates G-S2/G-S3/G-S4 stay in Studio (E-X9). The planned contract addendum
  `docs/api/contract-0.3-addendum.md` (names, signatures and error codes of TAB-XW-2..TAB-XW-9, **planned for 0.3,
  not implemented**), the CR-DBX clarification of specification sections 1.3 and 8, a pointer in `docs/api/contract.md`,
  `docs/adr/README.md`, `mkdocs.yml`, `docs/program/STATUS.md` (G-CX in effect after S-1..S-4, O-10, CR-DBX section)
  and `docs/TODO.md` (TB-149 in progress, TB-148 (1) decided for 0.3). No code change, no API change. The security
  review of #76 (accepted with conditions) is written in as requirements with named tests: K-1 SQLite rebuild SQL
  from `sqlite_schema` as one checked statement, K-2 no escalation through `TRIGGER`, foreign `SECURITY DEFINER`
  functions or `CREATE` on shared schemas, K-3 injection-safe revision text; decisions role DDL only on
  PostgreSQL 16 and later, no predefined `pg_*` role, a required `environment` keyword, v2 fingerprints opt-in;
  notes N-1..N-5.
- Docs: ecosystem migration record `docs/ecosystem/migration.md` (QA-23): before/after per consumer repository with the
  MIG and PIN pull requests and merge SHAs, compatibility (`0.1.0` to `0.2.1`, `tabrion>=0.2,<0.3`), test evidence,
  rollback and what was not migrated; `docs/program/STATUS.md` migration and QA rows synced (QA-17, QA-19, QA-20 and
  QA-23 done; QA-20 evidence: nodestro-studio `tabrion-compat.yml` run 43623198089 against Tabrion master, green).
  No code change.
- Docs: the owner accepted QA-S1 for tabrion-studio 1.0 on 2026-11-14 16:34Z. `docs/program/STATUS.md` (QA-S1 and
  STU-054 done, M6, M7, G-CX: start condition S-1 met, S-2..S-4 open) and TB-149 in `docs/TODO.md`. Not a release:
  H-2 and G-R stay open. No code change.
- Docs: CR-DBX conditional development start (owner, 2026-11-14 11:01Z, G-CX). ADR 0014, `docs/adr/README.md`,
  `docs/program/STATUS.md` (G-CX row, CR-DBX section, O-10) and TB-149 in `docs/TODO.md`: TAB-XW work starts without
  a new approval once S-1..S-4 (Studio 1.0 and QA-S1, security, migration incl. W6, CI) are proven; H-2 and G-R are
  needed for public releases only; write and admin code stays behind default-off kill switches until its gate. No
  code change.

### Changed

- `tabrion.postgres.RoleReport` gets the field `broad: tuple[str, ...] = ()` at the end
  (`tabrion.postgres.RoleReport.__init__` takes it as an optional last argument; additive under S-4): the predefined
  roles the role can reach, as `"broad:<role>"` (TB-150, ADR 0015 D-2). It is not part of `restricted` or of
  `as_dict()`, so the consumers' dict formats stay as they are.
- Master builds as `0.2.2.dev0` after the `0.2.1` release commit `af0458d54467571abfc1afdd44d76e1f4d727c06` (the
  TB-053 pattern), so later commits never build as `0.2.1`. The release commit's full SHA is recorded in `[0.2.1]`
  below, in the installation guide and in STATUS TAB-042; the README examples pin it (`[0.2.1]` Security, DC-TB-1,
  still names the `0.2.0` pin that was current when it was released).

### Security

- `tabrion.profilefile`: a `host` that starts with `/` (a socket directory) skipped the refused-character check, so
  `host = "/socket,remote"` was accepted and libpq would read it as a host list with a remote host behind the socket
  path. A comma in `host` is now refused before any other check (`profilefile.value`, I-109 d, TB-148 (1), W-10).
  A TCP `host` also refuses `%`, `&` and `=` (hardening against a URL library that unquotes the host; an IPv6 zone
  id such as `fe80::1%eth0` is therefore refused too).
- `tabrion.postgres.grant_runtime_role` refuses a runtime role that is a member of any predefined role (`pg_*` or an
  oid below `FirstNormalObjectId`), directly, through other roles, NOINHERIT or `SET`-only, with `RoleUnsafe`
  reason `predefined_role`, before anything is granted (TB-150). Before, a member of `pg_write_all_data` got the
  grants and kept `INSERT, UPDATE, DELETE` on the protected migration bookkeeping tables despite the `REVOKE`, and
  `inspect_role` reported it as restricted; `inspect_role` now names such memberships in `RoleReport.broad`. The
  membership is read again inside the grant transaction, so one granted concurrently rolls the grants back. Contract
  I-35 and decision D-124 (owner-confirmed, ADR 0015 D-2).
  **Behavior changes for consumers:**
  - Runtime roles that were accepted before and are now refused at deploy time:
    - a role that owns the database (implicit member of `pg_database_owner`), in any schema (in `public` it was
      already refused as `table_owner`);
    - a role that holds a monitoring or operations role: `pg_monitor`, `pg_read_all_stats`, `pg_read_all_settings`,
      `pg_stat_scan_tables`, `pg_signal_backend`, `pg_checkpoint`, `pg_maintain` (PostgreSQL 17+) and the rest;
    - a role that holds `pg_read_all_data` or `pg_write_all_data`.
    In each case the membership may be direct or through other roles, with any INHERIT, SET or ADMIN option.
  - `details["reasons"]` can contain `predefined_role` next to the earlier values. A superuser runtime role is now
    refused with `superuser,bypass_rls,table_owner,predefined_role`.
  - Consumers that compare `reasons` exactly, or map each value to a text of their own, should handle
    `predefined_role` before they pin a Tabrion with this fix. Axentra's `_refusal` gives an empty phrase for an
    unknown value. Axentra (#87) and Nodestro (#169) already refuse `pg_write_all_data` themselves.
- `tabrion.postgres.grant_runtime_role` revokes `INSERT, UPDATE, DELETE, TRUNCATE, TRIGGER` on each existing
  protected table and on its partitions and inheritance children (before: the named table only, without `TRIGGER`).
  After the `REVOKE`, inside the grant transaction, it checks `has_table_privilege(..., 'INSERT, UPDATE, DELETE,
  TRUNCATE, TRIGGER')` and `has_any_column_privilege(..., 'INSERT, UPDATE')` on those tables for the role and every
  role it is a member of (NOINHERIT and `SET`-only too). That covers grants to `PUBLIC`, to an inherited or `SET
  ROLE`-reachable role, column grants, grants by another grantor and a membership committed concurrently before the
  check. A hit rolls the transaction back (nothing granted) and raises `RoleUnsafe` with the new reason
  `protected_writable` (TB-150 item 3, contract I-35, decision D-125). On a server before PostgreSQL 16 it also
  refuses a role that has, or can become a role with, `CREATEROLE` (new reason `createrole`, D-127), read before the
  grants and again in the grant transaction. Not covered: a protected table, partition or child created or attached after the grant gets the
  default privileges; run the function again after such a migration (contract I-35). **Behavior change:** a deploy that
  relied on such a grant (or, before 16, on a runtime role with `CREATEROLE`) is now refused; consumers that map
  `reasons` values should handle `protected_writable` and `createrole`. Axentra's `_writable_protected_tables` and
  Nodestro's own re-check stay valid.
- `tabrion.check` reports two new PostgreSQL codes (TB-036, decision D-126, an addendum to specification section 9;
  no API change): **TAB-PG-013** (`error`, whatever `expect`) when `current_user` or `session_user` can reach
  `pg_execute_server_program`, `pg_read_server_files` or `pg_write_server_files` (superuser-equivalent), or has
  `CREATEROLE` (also through a role it can become) on a server before PostgreSQL 16; **TAB-PG-014** (`warning` for
  `expect="runtime"`, `info` for `"any"`, none for `"owner"`) for a membership of any other predefined `pg_*` role,
  for example `pg_write_all_data` or the database owner's `pg_database_owner`. Memberships count through other roles,
  NOINHERIT, `SET`-only and ADMIN-only. A superuser gets TAB-PG-001 only. `role_info` and `check_role` are unchanged
  (contract I-20, I-53). **Behavior change:** callers that fail on any `error` or `warning` finding may see these
  codes for roles that passed before.

## [0.2.1] - 2026-11-14

TABRION TAB-042 follow-up: `v0.2.1` final, a patch release with the log redaction fixes C-02 (#67) and TB-145 (#70)
and the license change. The release commit is the merge commit of the pull request that sets this version (#71):
`af0458d54467571abfc1afdd44d76e1f4d727c06` (recorded by the follow-up pull request that moves master to `0.2.2.dev0`;
ADR 0009, amendment of 2026-11-08). Consumers pin that commit by its full SHA via `git+https`; a tag `v0.2.1` on it is
an optional label. No PyPI upload, no GitHub release and no announcement (G-R). It satisfies the range
`tabrion>=0.2,<0.3`. **No API change**: the public surface (`docs/api/surface.json`, 235 names) is the same as in
`0.2.0` (`scripts/api_surface.py --base 52353da`); the redaction fixes only remove more, and the install hints change
message text only. Between `0.2.0` and this release master built as `0.2.1.dev0` (from
`52353daee6cd6e37a7efc45b74061a48beb2b280`).

### Added

- Docs: ADR 0014 records Tabrion's scope in change request CR-DBX (planning document 15, owner decisions of
  2026-11-13; planning confirmed, implementation not started): DDL, diff and migration-plan building blocks for
  Tabrion 0.3 on SQLAlchemy Core and Alembic operations, no own builder, no schema tool UI, no `pg_dump` wrapper, no
  role-admin UI, no vector functions. `docs/TODO.md` TB-149 lists TAB-XW-1..TAB-XW-10 as blocked on the
  development-start approval. No code change.

### Changed

- Docs: `docs/program/STATUS.md` adds CR-DBX (milestone M11, gates G-CX and G-S4, M7b as Studio 1.1, M10 as Studio
  1.2), narrows G-CR and TB-004 to the open Nodestro decisions, and fixes stale rows: G-S1 passed for tabrion-studio
  `9a5d4fa`, the M6 and M7 rows synced to tabrion-studio `807acc6`, and VER-1, MIG-AXE-1, MIG-NOD-1 and MIG-ORK-1
  done.
- License: Tabrion is proprietary software of Levuvo (all rights reserved; use only with written consent) instead of
  MIT. `LICENSE` follows the Levuvo template, the package metadata says `license = "LicenseRef-Proprietary"`, and the
  README, `TRADEMARKS.md`, the branding notes and the MkDocs copyright follow. The open-source components Tabrion uses
  keep their own licenses.
- The `0.2.0` release commit `52353daee6cd6e37a7efc45b74061a48beb2b280` is recorded in `[0.2.0]` below, in the
  installation guide and in STATUS TAB-042; the README examples pin it.

### Security

- Log redaction: an inline `DETAIL:`/`HINT:`/`CONTEXT:`/`QUERY:`/`LINE n:` label directly before a line break now
  starts a removed part even with one blank or none after it, and a no-break space or another non-ASCII blank after
  or before a label counts as a blank. Both forms kept the value (Tabrion Studio delta check D-2, TB-145).
- Log redaction (`DbErrorRedactionFilter`, `tabrion.errors`): psycopg's `LINE n:` statement echo is removed also
  when it is the only server text label in a message. The cheap pre-check that decides whether a text is scanned had
  no `LINE` entry, so an escaped `\nLINE n:` inside a `repr` (for example a cast error in a list argument) kept the
  statement and its literals (Tabrion Studio security assessment C-02, Low). A `LINE n:` in the middle of a line (a
  syntax error joined onto one line) is removed as well (TODO TB-145). The scan stays linear in the input length.
- Dependency confusion (DC-TB-1, TODO TB-132): the `ImportError` hints of a missing extra and the psycopg skip of
  `tabrion.testing` name the Git-pin form `tabrion[<extra>] @ git+https://github.com/peterkarlhuber/tabrion@<commit-sha>`
  instead of the bare requirement, which a package index would resolve; the README examples pin the `0.2.0` release
  commit; a unit test keeps `src/`, `docs/`, `examples/`, the README and CONTRIBUTING free of a bare install
  command. SECURITY.md and the installation guide record the residual risk: the names `tabrion` and
  `tabrion-studio` are not registered on PyPI (owner decision of 2026-11-13).

## [0.2.0] - 2026-11-13

TABRION TAB-042, gate G-T2: `v0.2.0` final, the Studio APIs release (milestone M4). The release commit is the merge
commit of the pull request that sets this version (#65): `52353daee6cd6e37a7efc45b74061a48beb2b280` (recorded by the
follow-up pull request that moves master to `0.2.1.dev0`; ADR 0009, amendment of 2026-11-08). Consumers pin that
commit by its full SHA via `git+https`; a tag `v0.2.0` on it is an optional label. No PyPI upload, no GitHub release
and no announcement (G-R). It satisfies the range `tabrion>=0.2,<0.3`. **No breaking change** (RC-3): every 0.1 name
keeps its signature and behavior, so `>=0.1,<0.2` consumers move by widening their range and pin; the redaction fix
only removes more. Between `0.1.0` and this release master built as `0.1.1.dev0` (from `d5821faa`) and then
`0.2.0.dev0`.

### Added

- The 0.2 Studio APIs are public (TABRION TAB-033, TAB-042; contract sections 5.3 to 5.6, notes I-100..I-106):
  `tabrion.Result` and `tabrion.Column`; `Database.query`, `Database.inspector`, `Transaction.query`,
  `Transaction.stream`, `Transaction.cancel`; experimental `Database.on_begin`, `Database.run_transaction` and
  `Transaction.savepoint`. New guide "Query, stream, cancel and inspect (0.2)" with tested examples. No 0.1 name
  changes, so this release has no breaking change (RC-3).
- `tabrion.profilefile` (experimental, standard library only; TABRION TAB-CR-1, contract section 5.9, I-109): the
  connection profile file format v1 (TOML) with `parse`, `load`, `dump`, `ConnectionProfile`, `SecretRef`,
  `ProfileFile`, `ProfileIssue` and `ProfileFileError`. Passwords are references (`env:`, `keyring:`, `file:`,
  `prompt`); a plain password is refused and never repeated. Tabrion Studio's schema version 1 is a valid file (except
  a libpq multi-host `host` and URL characters in `path` and `database`). A consumer resolves the references of a file
  from elsewhere only after the user confirms host and reference (I-109 j).
- `tabrion.schema` (experimental, extra `sqlalchemy`; TABRION TAB-CR-2, contract section 5.10, I-110): `snapshot`
  of tables through `Database.inspector()`, `fingerprint` (`v1:sha256:...`) and `compare` with named differences.
  Guide "Profile files and schema fingerprints (0.2)".

### Changed

- `tabrion.Database` and `tabrion.Transaction` inherit the 0.2 mixins of `tabrion._sa._v02` (TAB-042); the private
  test-only module `tabrion._sa._staged` is removed, and `docs/api/surface.json` has no `staged` entry any more.
  `scripts/api_surface.py` counts the public methods of a private Tabrion base class as members of the public class.
- `query` and `stream` refuse a statement that ends the transaction (`COMMIT`, `ROLLBACK`, ... `AND CHAIN`) also
  when it comes as `text().columns()`, `DDL(...)` or another executable, not only as `text()` (TODO TB-123, review of
  #32 C-1; contract I-104).
- The `dev` extra pins ruff to `>=0.17.5,<0.18` (TB-146), so a new ruff minor release cannot fail the lint job
  without a change of ours; the unused `# noqa: B008` on `tabrion.postgres.configure` is gone (RUF100).

### Security

- `DbErrorRedactionFilter` and the other users of the server-text removal now also remove a `DETAIL:`, `HINT:`,
  `CONTEXT:` or `QUERY:` part in the middle of a line when only one blank follows the label (a message joined onto
  one line, or with collapsed blanks), also in a record without a database exception. Before, only psycopg's two
  blanks matched mid-line, so such a value stayed in the log (tabrion-studio TD-056, TODO TB-144). Lower-case prose
  (`details:`, `Hint:`), longer identifiers (`MY_DETAIL:`) and a label without a value are kept.
- `tabrion.profilefile` refuses a `?`, `#` or `%` in a SQLite `path` (`profilefile.value`). SQLAlchemy 2.1 unquotes
  the path of `ConnectionProfile.url()`, so a percent-encoded network path such as `%2F%2Fhost/share/x.db` would
  otherwise pass the network-path check of an untrusted profile file (G-T2 review, B-1; contract I-109 c and d).

## [0.1.0] - 2026-11-12

TABRION TAB-040, gate G-T2: `v0.1.0` final. The release commit is the merge commit of the pull request that sets
this version (#56): `d5821faa8742dea012abda3b663549c038d76543` (recorded by the follow-up pull request that moves
master to `0.1.1.dev0`; ADR 0009, amendment of 2026-11-08). Consumers pin that commit by its full SHA via
`git+https`; a tag `v0.1.0` on it is an optional label. No PyPI upload, no GitHub release and no announcement (G-R).
It satisfies the final range `tabrion>=0.1,<0.2` (RC-8).

### Added

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
- Tests: the PostgreSQL suites carry 101 mutation probes against the server (43 in `0.1.0a1`). The extra `dev` adds
  `uv` for `scripts/downstream.py`.

- Layer B hint for `:name::type` (planning document 07, probe P5, TAB-T-025; contract I-107, TODO TB-139):
  SQLAlchemy's `text()` leaves a parameter before a `::` cast unbound, so `:x::int` fails with `database.syntax`
  (PostgreSQL, SQLSTATE 42601) or `database.unknown` (SQLite). Such an error now carries
  `details={"hint": "write CAST(:name AS type) instead of :name::type"}`, and its text shows the hint. The hint is
  fixed text without the parameter's name, its value or the statement; kinds and classes are unchanged, and errors
  without the pattern keep empty `details`.

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
  This release ships LDP-0.1.1 (see Fixed).
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

- The concurrency and stress suite (QA-4, `pytest -m stress`): SQLite read-then-write threads and two processes x 500
  write transactions without a lost update or a lock error, WAL readers, busy timeouts, `backup` during writes; PostgreSQL `SessionLock` and advisory-lock exclusion, connection loss, layer-B
  transactions, pool exhaustion and serializable retries. `TABRION_STRESS_REPEAT` sets the repetitions (5 by
  default); a hanging test ends the run with every thread's traceback. The backup and migration tests got the same
  time bound (TB-049).

- `scripts/api_surface.py --no-stubs`, the release check of TB-022 (TAB-040, G-T2). It fails when a name of the
  package's minor is still only `staged`, or when its body, after the docstring, is only
  `raise NotImplementedError`, `pass` or `...`. The body is read with `ast`, so nothing is called.
- `docs/program/traceability.md` maps each of the 53 numbered plan test IDs (TAB-T-001..300; G-T2, K2, K8) to the
  tests that cover it, and marks 1 gap, TAB-T-300 (TODO TB-140); no ID is partial.
  `tests/unit/test_traceability.py` checks that every ID has a row and that every named test exists.
- TAB-T-009 (K7, TB-136): `tests/postgres/test_restart_postgres.py` restarts the PostgreSQL server between two
  requests and checks that the first request through a pool with `pool_pre_ping` succeeds, with a pool without it as
  the control. New marker `restart`; the test runs only when `TABRION_PG_RESTART_COMMAND` is set (the nightly job
  `pg restart`) and is skipped elsewhere.
- Tests for the TAB-040 gaps:
  - TAB-T-021: timestamps across both 2026 DST switches, on both parts variants and both engines.
  - TAB-T-025: the `:x::int` error on PostgreSQL and SQLite, and the `CAST(:x AS int)` form. The error carries the
    hint of contract I-107 (TB-139).
  - TAB-T-162: row-level security with `FORCE`, a non-owner runtime role, and two tenants through
    `Database.transaction()` with `set_config(..., true)`.
  - TAB-T-161: the next checkout after a failed, raised or interrupted transaction is idle.
  - TAB-T-202: the restore drill of the measurements application.
  - K1: branch tests for `tabrion.sqlite`, which lift its coverage above 95 %. They cover a failed `fchmod` or
    `fstat` after the exclusive create, a name `lstat` cannot inspect, a name changed after SQLite opened the
    file, a failing `ROLLBACK`, and a `COMMIT` that SQLite rolled back itself.

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
- CI (workflow change, TB-130, workflow review of #34/#40 C-3/C-4): the wheel and sdist are built with the hashed
  build-backend pins of `build-constraints.txt` (`PIP_REQUIRE_HASHES`) in `ci.yml` and `release.yml`; the CycloneDX
  SBOM of the wheel (`scripts/sbom.py`) is uploaded with the artifacts and attested with them in a release. `api
  surface` always runs `--base <merge base>` on pull requests; `mutation probes` selects its files by the pattern the
  other jobs ignore; ci-ok's workflow-change gate also covers `.github/ci-manifest.json`; the `platforms.yml` filter
  adds `__init__.py`, `check.py`, `tests/conftest.py` and `tests/unit/test_mutation_probes.py`.
- CI (workflow change, TAB-040 gaps): `nightly.yml` has the jobs `stress (200 repetitions)` (K6, QA-4, TB-086:
  `pytest -m stress` with `TABRION_STRESS_REPEAT=200`, SQLite and PostgreSQL 17) and `pg restart` (K7, TB-136:
  `docker restart` of the job's PostgreSQL service container inside the TAB-T-009 test); both are nightly only and
  in the needs of the nightly report. The dead transitional guard of `conformance` is removed (TB-135, review C-3b),
  and `api surface` runs `scripts/api_surface.py --no-stubs` (TB-022) in `ci.yml` and in the new `release.yml` job
  `surface`, which `build` needs.
- CI (workflow change, QA-11, K1, K7/K8): `release.yml` takes an annotated tag or a full commit SHA on master,
  requires a final `__version__` and a dated CHANGELOG section (`.github/scripts/release_check.py`), and runs the
  attestation only with the opt-in input `attest`. `ci.yml` has the jobs `coverage` (branch coverage with the whole
  suite and PostgreSQL; floors: total 93.5 %, `errors` 95 %, `sql` 95 %, `sqlite` 95 %) and `mutation probes` (split
  out of `test`). `tests/ci/flaky.txt` (empty) with its format test; ruff `S` documented as the Bandit substitute and
  run over `examples/` too. The TB-017 guards of `api surface` and `conformance` decide from the base branch.
- CI (workflow change, TODO TB-141, deviation D-79): no test is skipped silently. `ci.yml` and `platforms.yml` set
  `TABRION_SKIP_GUARD=1`, which activates the pytest plugin `tests/ci/skipguard.py` (loaded by `tests/conftest.py`):
  a skip or non-strict `xfail` reason, and a strict `xfail` reason outside `tests/examples/`, that matches no pattern
  of the reviewed allowlist `tests/ci/skip-reasons.txt` turns the job red, and each job's log lists its skipped node
  IDs with their reasons (section "skip guard"), which is that job's skip record; `--skip-guard=report` prints the
  list without failing. One allowlist for all jobs replaces the per-job skip lists of planning document 07
  section 4.3.
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

- `tabrion.sqlite.connect` raises `UnsafePath("sqlite.unsafe_path")` when the database name changes while the name
  SQLite opened is resolved after the open. Before Python 3.13, `os.path.realpath` raised `OSError(EINVAL)` there,
  and `connect` passed it on, with the file name in its text, after closing the connection (nothing was opened).
  Contract I-22, TODO TB-045; found by the randomized open race test of TAB-T-123
  (`tests/sqlite/test_stress_open_race.py`, 1,000 repetitions in the nightly `stress` job).
- Conformance vector LDP-PGL-005 (PGL-4) no longer fails intermittently in the `coverage` job (TODO TB-142): the
  `waiter_state` action sampled the waiting backend in step with its 0.1 s polls and, under coverage tracing, met the
  poll in two of ten samples. It now samples from the waiter's first poll on, takes a sample that meets a poll
  statement in flight again once that statement has ended, counts a statement running for more than 0.2 s on the
  server's clock and every lock or timeout wait as open, requires every sample to be idle without a snapshot (no
  allowance for one sample any more), and ends the wait by handing the key over. The lock tests L06 and L12 use the
  same sampler instead of their allowance of two samples. SPEC appendix A describes the new procedure, and the
  versioning rule names a changed action procedure without a changed expectation as a PATCH change; no expectation
  changed.
  LDP-0.1.1 digest 575fd6b03028.
- The internal fault proxy `tabrion.testing._faults` (contract I-33) no longer lets the server's answer through an
  `inflight` cut (TODO TB-143). It forwarded the batch and only then closed both sides; under CPU load the forwarding
  thread could be descheduled in between, the other thread relayed a fast answer, and the client saw the statement
  succeed: `test_session_lock_raises_and_forgets[inflight]` failed with "DID NOT RAISE OperationalError" (16 of 300
  runs, 5 of 100 with `--cov`, under load), and `test_ping_reports_a_lost_connection[inflight]` reported
  `PingResult(ok=True)` (6 of 1000 runs in the review of #55). The proxy now mutes the connection before it forwards
  the batch, forwards the batch through its `Sync` (also when it arrives in several reads), and then closes both
  sides; a server message at the moment of the cut can no longer keep the batch from the server. `SessionLock` was not
  at fault and is unchanged. Unit tests: `test_fault_proxy_inflight_lets_no_answer_through`,
  `test_fault_proxy_inflight_forwards_the_batch_when_the_server_writes_at_the_cut` and
  `test_fault_proxy_inflight_forwards_a_batch_split_across_reads`.
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
  transaction state, and carry 43 mutation probes against the server. The extra `dev` adds `psycopg-pool` for the
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
