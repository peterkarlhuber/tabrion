# API contract addendum 0.3 (CR-DBX, planned)

!!! warning "Planned 0.3, not implemented"
    Everything on this page is **planned for Tabrion 0.3 and not implemented**, except the sections marked as moved
    into the contract (A.1, TAB-XW-2; A.2, TAB-XW-3; A.3, TAB-XW-4; A.4, TAB-XW-5; A.5, TAB-XW-6; A.6, TAB-XW-7; A.7 and A.9, TAB-XW-8; A.8, TAB-XW-9). None of the other names exists in the code, in `__all__` or in
    [`surface.json`](surface.json); none may be imported or relied on. Names, signatures and
    error codes may still change until the independent API review of TAB-XW-10. When a work package implements its
    part, it moves that section into [the contract](contract.md) with implementation notes, adds the names to
    `surface.json` with `"since": "0.3"` and stability `X`, and deletes the section here.

Work package TABRION TAB-XW-1 (change request CR-DBX, planning document 15 sections 5 and 9.1). The design rules are
[ADR 0015](../adr/0015-crdbx-write-and-ddl-primitives.md); the scope is [ADR 0014](../adr/0014-crdbx-tabrion-scope.md).
The conditions K-1..K-3, decisions D-1..D-4 and notes N-1..N-5 of the security review of #76 are binding
requirements; each is repeated below at its function with its named test, and the full table is
[ADR 0015 section 9](../adr/0015-crdbx-write-and-ddl-primitives.md#9-conditions-decisions-and-notes-of-the-security-review-of-76-binding-requirements).
The conventions of the contract apply unchanged: keyword-only parameters after `*`, frozen slotted dataclasses, times
in seconds as `float`, T-NOTX for connections handed to Tabrion, R-SET, R-NORETRY, no values in errors.

## A.0 Overview

Every planned name is experimental (`X`) and arrives with 0.3. No stable name changes.

| Module | Planned names | WP | Extra |
|---|---|---|---|
| `tabrion.schema` | `ForeignKeyShape`, `UniqueShape`, `CheckShape`, `IndexShape`; new fields of `ColumnShape`, `TableShape`, `SchemaSnapshot`; `snapshot(level=)`; new `SchemaDifference.kind` values (implemented, contract section 5.10, I-111) | TAB-XW-2 | `sqlalchemy` |
| `tabrion.ddl` (new) | `DdlStatement`, `create_statements`, `table_statements` and the in-place statements (implemented, contract section 5.12, I-123) | TAB-XW-3 | `sqlalchemy`; column statements also `migrations` |
| `tabrion.plan` (new) | `RiskClass`, `PlanStep`, `MigrationPlan`, `plan`, `revision_text`, `migration_markers` (implemented, contract section 5.13, I-124); `ExecutionResult`, `execute_plan` (implemented, contract section 5.13, I-125) | TAB-XW-4, TAB-XW-5 | `sqlalchemy`; column steps also `migrations` |
| `tabrion.rowedit` (new) | `insert_row`, `update_row`, `delete_row` (implemented, contract section 5.11) | TAB-XW-6 | `sqlalchemy` |
| `tabrion` (layer B) | `Transaction.execute_checked` (implemented, contract section 5.11) | TAB-XW-6 | `sqlalchemy` |
| `tabrion.sqlite` | `restore`, `RestoreResult`, `delete_database_file` (implemented, contract section 3.10) | TAB-XW-7 | none (standard library) |
| `tabrion.postgres` | `RoleOptions`, `RolePrivileges`, `role_privileges`, `create_role`, `alter_role`, `drop_role`, `grant_membership`, `revoke_membership`, `grant_privileges`, `revoke_privileges` (implemented, contract section 4.9) | TAB-XW-8 | `postgres` |
| `tabrion.postgres` | `create_database`, `drop_database`, `database_sessions` (implemented, contract section 4.8) | TAB-XW-9 | `postgres` |
| `tabrion.profilefile` | `ConnectionProfile.mode` value `"admin"` (implemented, contract section 5.9, I-109 e) | TAB-XW-8 (E-X8) | none |
| `tabrion.errors` | `RowCountMismatch`, `PlanError`, `PlanMismatch`, `PlanRefused`, `DatabaseInUse`, `SchemaDrift`, `PlanFailed` (implemented, contract section 1.4); new `RoleUnsafe` reasons | TAB-XW-4..9 | none |

**Not planned** (non-goals stay, ADR 0014, ADR 0015): a `pg_dump`/`pg_restore` wrapper, an Alembic runner or
`upgrade`, a migration version table, a feature flag, gate or "writes enabled" setting, upsert or merge helpers, role
attributes SUPERUSER, BYPASSRLS, CREATEROLE and REPLICATION, libpq multi-host profiles (W-10, TB-148 (1)).

## A.0.1 Planned errors (`tabrion.errors`, since 0.3, X)

```python
class RowCountMismatch(TabrionError)   # implemented (TAB-XW-6, contract section 1.4 and 5.11):
                                        # "transaction.rowcount_mismatch" (details expected, actual)
                                        # "transaction.too_many_rows" (details limit, actual)
class PlanError(TabrionError)          # implemented (TAB-XW-4, contract sections 1.4 and 5.13): base of the plan errors
    class PlanMismatch(PlanError)          # implemented with "plan.invalid" (details field); TAB-XW-5 added
                                           # "plan.hash_mismatch", "plan.fingerprint_mismatch" (implemented)
    class SchemaDrift(PlanError)           # implemented (TAB-XW-5, contract I-125): "plan.drift": details expected,
                                           # actual (fingerprints), differences (count)
                                           # .differences: tuple[SchemaDifference, ...]
    class PlanRefused(PlanError)           # "plan.refused": details reason in
                                           # {"environment", "migration_managed", "risk_not_allowed",
                                           #  "dialect_mismatch", "engine_unsupported", "unsafe_schema_sql",
                                           #  "unsupported_feature"}, markers (comma list); implemented
                                           # (TAB-XW-4) with the last four, "unsupported_feature" added for
                                           # ADR 0015 D-5; TAB-XW-5 implemented the first three and
                                           # added "foreign_keys_on" (contract I-125)
    class PlanFailed(PlanError)            # implemented (TAB-XW-5, contract I-125): "plan.failed": details step,
                                           # completed, state in {"rolled_back", "partial", "unknown"}; original set;
                                           # never chained
class DatabaseInUse(TabrionError)      # implemented (TAB-XW-7, TAB-XW-9, contract sections 1.4, 3.10, 4.8):
                                        # "postgres.database_in_use" (details sessions), "sqlite.in_use"
```

- `RoleUnsafe` (exists, `postgres.role_unsafe`) gets new values in `details["reasons"]`: `createrole`,
  `replication`, `reachable_superuser`, `reachable_bypass_rls`, `reachable_createrole`, `reachable_replication`,
  `predefined_role`, `owns_objects`, `current_user`, `security_definer`, `schema_create` (implemented with TAB-XW-8,
  contract section 4.9, which also adds `trigger`). New `UsageError` codes:
  `postgres.role_ddl_requires_16` (D-1; implemented with TAB-XW-8, with `postgres.role_exists` and
  `postgres.object_missing`), `plan.invalid_revision` (K-3). New reason values and new error classes are additive (API
  stability policy S-4).
- The rules of contract section 1.4 apply: `str` is the safe message, no `__cause__`, `details` never hold data
  values, passwords, revision text or SQL with literals. A fingerprint and a plan hash are not secrets.

## A.1 `tabrion.schema` v2 (TAB-XW-2)

**Implemented (TAB-XW-2) and moved into the contract:**
[section 5.10](contract.md#510-schema-fingerprint-tabrionschema-since-02-experimental-extra-sqlalchemy-tab-cr-2) with
the implementation note I-111. `tabrion.schema.ForeignKeyShape`, `UniqueShape`, `CheckShape`, `IndexShape` and (added after the review of #85)
`ExclusionShape` are in
`surface.json` with `"since": "0.3"` and stability `X`; the new fields of `ColumnShape` (`default`), `TableShape`
(`foreign_keys`, `unique_constraints`, `check_constraints`, `indexes`), `SchemaSnapshot` (`level`) and
`SchemaDifference` (`name`, the new `kind` values) and `snapshot(level=)` have defaults that keep the 0.2 behavior.
`ConnectionProfile.schema_fingerprint` accepts `v1:` and `v2:` (I-109 e). D-4 is
`tests/unit/test_schema_v2.py::test_default_level_is_1` and `::test_v1_fingerprint_unchanged_against_02_golden`. Added
in the implementation: `UsageError("schema.invalid_level")` for a level other than 1 or 2, and `schema.level_mismatch`
also for a level-1 snapshot that holds level-2 detail (I-111 a); `NO ACTION` is `None` (I-111 b); beyond the planned
shape, level 2 also reads `DEFERRABLE`/`INITIALLY DEFERRED` (foreign and unique keys), PostgreSQL `INCLUDE` and
`NULLS NOT DISTINCT`, partial index predicates and the order of index elements (new defaulted fields, I-111 b), so a
change of only these changes a `v2:` fingerprint (drift checks of TAB-XW-5). After the review of #85 also index
collations, operator classes, access method and `indisvalid`, unique constraint collations, `NOT VALID`, `MATCH` and
exclusion constraints (new kinds `exclusion_missing`, `exclusion_added`), and `IndexShape.expressions` marks expression
elements. The remaining blind spots are listed in I-111 d, with a decision due before G-S3.

## A.2 `tabrion.ddl` (TAB-XW-3)

**Implemented (TAB-XW-3) and moved into the contract:**
[section 5.12](contract.md#512-ddl-from-the-schema-model-tabrionddl-since-03-experimental-extra-sqlalchemy-tab-xw-3) with
the implementation note I-123. `tabrion.ddl.DdlStatement`, `create_statements` and `table_statements` keep the planned
signatures (`transactional` got the default `True`); added in the implementation, so the plan of TAB-XW-4 has a
statement for each of its operations: `drop_table_statement`, `add_column_statement`, `drop_column_statement`,
`alter_column_statements`, `add_constraint_statement`, `drop_constraint_statement`, `create_index_statement` and
`drop_index_statement` (with `concurrently`), all in `surface.json` with `"since": "0.3"` and stability `X`. Column
statements use Alembic's DDL constructs and need the extra `migrations` as well (ADR 0015 section 1). Error codes
beyond the planned `ddl.unsupported_type`, `ddl.identifier_too_long` and `ddl.level_required`:
`ddl.unsupported_dialect`, `ddl.dialect_mismatch`, `ddl.invalid_shape`, `ddl.unsafe_sql_text` (a default, check,
expression or predicate text that could leave its place in the statement), `ddl.unsupported_feature` (fail closed,
ADR 0015 D-5.3, including every shape field the module does not know) and `ddl.rebuild_required` (an in-place change
SQLite cannot make). The tests named in A.2 (golden DDL, round trip, quoting) are in `tests/unit/test_ddl.py` and
`tests/postgres/test_ddl_postgres.py`.

## A.3 `tabrion.plan`: diff, plan and revision text (TAB-XW-4)

**Implemented (TAB-XW-4) and moved into the contract:**
[section 5.13](contract.md#513-schema-change-plans-tabrionplan-since-03-experimental-extra-sqlalchemy-tab-xw-4) with
the implementation note I-124; the D-5 parts of the schema model are I-111 i. `RiskClass`, `PlanStep`,
`MigrationPlan`, `plan`, `revision_text`, `migration_markers`, `PlanError`, `PlanMismatch`, `PlanRefused`,
`TriggerShape`, `PolicyShape` and `FunctionShape` are in `surface.json` with `"since": "0.3"` and stability `X`.
Changes from the planned text, each the safe reading of ADR 0015:

- `plan` has the new keyword `concurrent_indexes=False`; without it no step could be `non_transactional`.
- `PlanRefused` has the new reason `unsupported_feature` (ADR 0015 D-5, `details` `table` and `feature`); new
  `UsageError` codes `plan.unsupported_option`, `plan.unsupported_change` (a PostgreSQL primary key, column order or
  unnamed constraint) and `plan.invalid_target` (a foreign key to a dropped table).
- `locking` covers every lock that blocks writes on an existing PostgreSQL table, not only `ACCESS EXCLUSIVE`.
- The SQLite rebuild never renames: it copies into a holder table, drops and recreates the table and copies back, so
  views and the foreign keys of other tables stay valid and are not recreated; indexes are written from the target
  shape, so triggers are the only `sqlite_schema` text a plan takes over (K-1 checks them; a `TEMP` head is refused).
- `revision_text` writes every statement as `op.execute(sa.text(...))` (a rebuild included, not
  `op.batch_alter_table`), so the revision runs exactly the previewed SQL; writing the text needs no Alembic. `plan`
  needs the extra `migrations` for column steps, as the column statements of `tabrion.ddl` do. `downgrade()` always raises `NotImplementedError`.
- The K-1 check at execution (authorizer, `trusted_schema = OFF`, defensive mode) and
  `test_rebuild_runs_under_authorizer_and_defensive` stay in A.4 (TAB-XW-5).
- Review of #89 (ACCEPTED with conditions) folded in: the SQLite foreign key guard and check in the revision
  (P89-01), views in the level-2 snapshot (`ViewShape`) and the new reason `dependent_object` (P89-02), risk
  classes checked against each step's operation and statements (P89-03), `CONCURRENTLY` steps first and last
  (P89-06), exact `str` checks (P89-07); the remaining points are residual risks in contract I-124 j.

## A.4 `tabrion.plan.execute_plan` (TAB-XW-5)

**Implemented (TAB-XW-5) and moved into the contract:**
[section 5.13](contract.md#513-schema-change-plans-tabrionplan-since-03-experimental-extra-sqlalchemy-tab-xw-4) with
the implementation note I-125. `tabrion.plan.ExecutionResult`, `execute_plan` and `tabrion.errors.SchemaDrift`,
`PlanFailed` are in `surface.json` with `"since": "0.3"` and stability `X`. Changes from the planned text, each the
safe reading of ADR 0015 and of the review of #89:

- **No `plan` parameter (P89-03).** The executor takes the target model (`target`) instead of a plan and plans again
  itself, inside its transaction and under the locks of D-5.4; it runs only a plan whose hash is the `plan_hash` the
  user confirmed, and checks the risk classes of that plan. New keywords: `schemas` (the scope of the snapshot, as in
  the preview), `server_version` and `concurrent_indexes` (the options of the preview's `plan`), and `source` (the
  preview's snapshot, optional: it must have `expected_fingerprint`, else `PlanMismatch("plan.fingerprint_mismatch")`,
  and `SchemaDrift.differences` names the differences from it).
- **Order of checks.** Drift comes before the hash check, because the new plan is made from the snapshot it checks;
  the markers are read inside the execution transaction (I-125 b).
- **New reason `foreign_keys_on`** (P89-01): SQLite foreign keys are turned off before `BEGIN` for every SQLite plan,
  read back, and the executor refuses to start while they stay on. `foreign_key_check` and `integrity_check` run
  before every SQLite commit. Without defensive mode (Python 3.11) a plan that recreates a trigger is refused (reason
  `engine_unsupported`, `feature` `sqlite_defensive`).
- **`PlanFailed` state `unknown`** for a `COMMIT` that may have run (connection lost), and details `check` and
  `violations` for a failed check.
- **`CONCURRENTLY` steps in plan order:** the leading drops before the transactional steps (which then plan again
  under new locks and must be exactly the remaining steps), the creations after the commit (I-125 e).
- **SQLite dry run** is the rollback of the execution transaction, not a run on a backup copy (I-125 f).

## A.5 Guided write transaction (TAB-XW-6)

**Implemented (TAB-XW-6) and moved into the contract:**
[section 5.11](contract.md#511-guided-write-transaction-since-03-tab-xw-6) with the implementation notes
I-120..I-122. `Transaction.execute_checked`, `tabrion.rowedit.insert_row`, `update_row`, `delete_row` and
`tabrion.errors.RowCountMismatch` are in `surface.json` with `"since": "0.3"` and stability `S` (G-S2 follow-up 7A;
they were `X` until then, and `old` and `new` became keyword-only); they were unimportable while the package version was below 0.3 and are public since master builds as `0.3.0.dev0`. N-5 is test
`tests/unit/test_rowedit.py::test_float_and_json_old_values_may_conflict`.

## A.6 `tabrion.sqlite`: `restore` and `delete_database_file` (TAB-XW-7)

**Implemented (TAB-XW-7) and moved into the contract:**
[section 3.10](contract.md#310-restore-delete_database_file-since-03-experimental-tab-xw-7) with the implementation
notes I-170 and I-171. `restore`, `RestoreResult`, `delete_database_file` and `tabrion.errors.DatabaseInUse` are in
`surface.json` with `"since": "0.3"` and stability `X`. N-4 is tests
`tests/unit/test_sqlite_delete.py::test_swap_between_check_and_unlink_refused` and
`::test_side_file_symlink_unlinked_not_followed`; the round trip of planning document 15 section 9.4 is
`tests/sqlite/test_restore.py::test_round_trip_backup_restore_integrity_and_fingerprint`.

## A.7 `tabrion.postgres`: role DDL (TAB-XW-8)

**Implemented (TAB-XW-8) and moved into the contract:**
[section 4.9](contract.md#49-role-ddl-role_privileges-create_role-alter_role-drop_role-memberships-privileges-since-03-experimental-tab-xw-8)
with the implementation notes I-180..I-186. `RoleOptions`, `RolePrivileges`, `role_privileges`, `create_role`,
`alter_role`, `drop_role`, `grant_membership`, `revoke_membership`, `grant_privileges` and `revoke_privileges` are in
`surface.json` with `"since": "0.3"` and stability `X`. The named tests are in `tests/postgres/test_role_ddl.py`: D-1
`::test_role_ddl_refused_before_16`, `::test_createrole_pre16_reported_dangerous`; N-3
`::test_admin_option_on_dangerous_role_counts_as_reachable`; D-2 `::test_membership_in_any_predefined_role_refused`,
`::test_existing_predefined_membership_reported_broad`; K-2 `::test_trigger_privilege_not_grantable`,
`::test_execute_on_foreign_security_definer_refused`, `::test_create_on_public_or_foreign_schema_refused`,
`::test_role_privileges_reports_escalation_grants`.

## A.8 `tabrion.postgres`: `create_database` and `drop_database` (TAB-XW-9)

**Implemented (TAB-XW-9) and moved into the contract:**
[section 4.8](contract.md#48-create_database-drop_database-database_sessions-since-03-experimental-tab-xw-9) with
the implementation notes I-190..I-193. `create_database`, `drop_database` and `database_sessions` are in
`surface.json` with `"since": "0.3"` and stability `X`; the tests are `tests/postgres/test_databases.py`.

## A.9 `tabrion.profilefile`: mode `admin` (E-X8)

**Implemented (TAB-XW-8) and moved into the contract:** section 5.9, rule I-109 (e). `ConnectionProfile.mode` is
`Literal["read_only", "read_write", "admin"]`; `parse` accepts `admin`, and Tabrion attaches no behavior to any mode.
Multi-host `host` values stay refused (W-10). Test `tests/unit/test_profilefile.py::test_admin_mode_parses_and_round_trips`.
