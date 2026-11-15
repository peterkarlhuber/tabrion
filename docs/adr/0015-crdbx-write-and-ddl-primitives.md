# ADR 0015: Tabrion 0.3 write and DDL primitives: SQLAlchemy and Alembic, revision text only, plan binding and guards

- **Status:** Accepted (2026-11-14, TABRION TAB-XW-1), with the conditions K-1..K-3, the decisions D-1..D-4 (and D-5 of 2026-11-15, section 10) and the
  notes N-1..N-5 of the security review of #76 (planning folder `reviews/pr76-tabxw1-review.md`, ACCEPTED WITH
  CONDITIONS) written in as requirements (section 9). The content was decided by the owner on change request CR-DBX
  (2026-11-13 23:35Z: E-X3..E-X13 as recommended, with the binding refinements of E-X4, E-X5, E-X6 and E-X9 in
  planning document 15 section 3.1). The development start is the conditional approval G-CX of 2026-11-14 11:01Z;
  its start conditions S-1..S-4 are proven in the planning file `cr-dbx-startbedingungen.md` (overall status met,
  2026-11-14 about 17:55Z, tabrion master `4eca6ac`). This record writes the design down; it implements nothing and
  enables nothing.
- **Plan reference:** planning document 15 sections 3, 3.1, 3.2, 5, 6, 7, 9.1 and 9.4; work package TAB-XW-1;
  builds on [ADR 0014](0014-crdbx-tabrion-scope.md) (scope) and [ADR 0007](0007-no-shared-migration-runner.md) (no
  runner).

## Context

[ADR 0014](0014-crdbx-tabrion-scope.md) decided **what** of CR-DBX lives in Tabrion: the schema model v2, DDL, the
diff into a migration plan, an executor, a guided write transaction, SQLite `restore()` and file deletion, role DDL
and PostgreSQL `create_database`/`drop_database` (TAB-XW-2..TAB-XW-9). It left to TAB-XW-1 **how** these building
blocks behave, and that is what the owner's refinements of 2026-11-13 23:35Z bind:

- **E-X4 (refined):** direct schema changes only on DEV and TEST databases without existing migration management.
  For Alembic projects only revisions are produced (a text file, written into the project by Studio after project
  trust); no `env.py`, no project code and no `alembic upgrade` is ever run. Preview, plan binding (plan hash and
  fingerprint), a drift check right before execution (abort on any difference) and an explicit confirmation are
  mandatory for every change.
- **E-X5 (refined):** PostgreSQL backup and restore only through verified system `pg_dump`/`pg_restore`, run by
  Studio; restore tests are mandatory; `pg_dump` is not a complete backup.
- **E-X6 (refined):** role management never grants SUPERUSER, BYPASSRLS, CREATEROLE or REPLICATION, neither directly
  nor through membership in a role that has them; existing privileges, memberships and indirect privileges are
  checked before every change.
- **E-X9 (refined):** G-CX enables no write function; every new write or admin function stays technically locked
  until its area gate (G-S2, G-S3, G-S4).

Two contradictions of planning document 15 section 2 touch this repository: W-6 (change request 14 wanted to "run"
Alembic revisions "through `tabrion.migrations`", which only offers `status`/`require_current` and never runs
`env.py`) and W-7 (the non-goals of specification section 1.3 against the new building blocks). Both are resolved in
the plan (section 2.1); this record carries the resolution into the repository.

## Decision

### 1. Built on SQLAlchemy and Alembic, no own builder (E-X3)

- Schema DDL is produced by SQLAlchemy Core (`CreateTable`, `CreateIndex`, `DropTable`, the dialect compilers) and
  by Alembic operations, with identifiers quoted by the dialect's preparer. Role and database DDL (TAB-XW-8,
  TAB-XW-9), which SQLAlchemy does not model, is composed with `psycopg.sql` and `Identifier`, as
  `grant_runtime_role` does today. Beyond that Tabrion only sends fixed statements without identifiers
  (`PRAGMA foreign_keys`, `SET LOCAL lock_timeout`).
- The non-goal "Own query language or builder" of [specification section 1.3](../specification.md#13-non-goals-binding)
  stays: composing SQLAlchemy and Alembic constructs is not a builder. Section 1.3 gets a CR-DBX clarification in
  the same pull request.
- The new modules are experimental (`X`) in Tabrion 0.3 (E-X11) and need the extra `sqlalchemy`; revision text needs
  the extra `migrations` (Alembic). The standard-library core gains only the SQLite primitives of TAB-XW-7.

### 2. Alembic projects get revision text only (E-X4)

- `tabrion.plan.revision_text` returns a **string**. Tabrion never writes it to a project, never reads a project's
  directory, `alembic.ini` or `env.py`, never imports project code and never calls `alembic upgrade`,
  `alembic.command.*` or a `MigrationContext` that runs revisions. Writing the file after project trust is the
  consumer's job (Studio STU-XSD-3).
- The rendered text is inert data from Tabrion's point of view: every identifier and the message are written as
  Python string literals (`repr`), never as code, so a table name or a message cannot inject statements into the
  revision file. This includes the module docstring, `revision` and `down_revision` (K-3, section 9).
- The file name or slug of the revision is the consumer's (STU-XSD-3: no path parts, no `..`); Tabrion returns text
  only.
- `tabrion.migrations` keeps `status` and `require_current` (ADR 0007); nothing in 0.3 makes it a runner.

### 3. Direct execution only without migration management, declared DEV or TEST (E-X4)

- The executor (`tabrion.plan.execute_plan`, TAB-XW-5) runs one confirmed plan **once**: no version table, no
  ordering, no history, no "upgrade to head". It is not a migration runner ([ADR 0007](0007-no-shared-migration-runner.md)
  stays).
- It refuses a database with migration management before it runs anything (`PlanRefused`, reason
  `migration_managed`): an `alembic_version` table, the Nodestro and Axentra bookkeeping tables
  (`nodestro_schema_migrations`, `axentra_schema_migrations`) or a SQLite `user_version` above 0
  (`tabrion.sqlite.migrate`). `tabrion.plan.migration_markers` reports the same markers for a preview.
- The caller must declare the environment: `environment` is a **required keyword parameter without a default**,
  typed `Literal["dev", "test"]`; any other value is refused at run time too (reason `environment`). Tabrion cannot
  know a database's environment: this is a guard rail, **not a boundary**, a second lock behind the consumer's own
  PROD lock (E-X7: PROD stays technically locked in Studio). Studio passes the value from the user-local stored
  profile, never from the request (Studio #109 K-1). Decided in the review of #76 (D-3).

### 4. Plan binding, drift check, dry run (E-X4, R-X3)

- A `MigrationPlan` carries the fingerprint of the snapshot it was computed from (`source_fingerprint`), the target
  fingerprint and a `plan_hash`: SHA-256 over the canonical JSON of dialect, both fingerprints and every step with its
  exact SQL text. `MigrationPlan.from_json` recomputes the hash and refuses a plan whose content does not match it.
  This proves **integrity, not authenticity**: a plan whose hash matches its own content can come from anywhere. A
  consumer never takes a plan from an untrusted source (a project file, a URL, an import); Studio keeps plans
  server-side (Studio #109 TX-08; N-2).
- `execute_plan` takes the hash the user saw in the preview (`plan_hash=`) and the fingerprint the preview was made
  against (`expected_fingerprint=`). It runs exactly that plan or nothing:
  1. the plan's own hash must equal `plan_hash` (`PlanMismatch`), and `source_fingerprint` must equal
     `expected_fingerprint` (`PlanMismatch`);
  2. **right before the first step**, inside the execution transaction where the engine allows it, a fresh snapshot
     of the database must have the fingerprint `expected_fingerprint`; any difference aborts before a statement runs
     (`SchemaDrift`, with both fingerprints and the named differences, never data). On PostgreSQL the executor first
     takes the locks on the plan's target tables (`LOCK TABLE ... IN SHARE ROW EXCLUSIVE MODE`, or the stronger mode
     the step needs, under `lock_timeout`) and only then computes the fingerprint, so concurrent DDL cannot land
     between the check and the first step (N-1). On SQLite the check runs under `BEGIN IMMEDIATE`;
  3. every step's risk class must be in the caller's `allow` set (the classes the user confirmed); otherwise refused
     before anything runs (reason `risk_not_allowed`).
- **Dry run:** PostgreSQL runs every transactional step in one transaction and rolls it back; SQLite runs the plan on
  a private backup copy (`tabrion.sqlite.backup`, 0600, deleted afterwards). Non-transactional steps
  (`CREATE INDEX CONCURRENTLY`, `CREATE`/`DROP DATABASE`, `VACUUM`) are never run in a dry run and are reported as
  untested.
- **Execution:** PostgreSQL in one transaction with `SET LOCAL lock_timeout` (default 5 s, planning document 03
  section 5.3); non-transactional steps one at a time, after the transaction, each marked. SQLite rebuilds (new table,
  copy, drop, rename, indexes, triggers and views recreated from `sqlite_schema` under the rules of K-1 in section 9)
  with `PRAGMA foreign_keys=OFF` outside the
  transaction, `foreign_key_check` before the commit (a violation rolls back) and the pragma restored afterwards
  (R-CR-5). A failure reports the step and whether the database was rolled back or is partly changed (only possible
  after a non-transactional step). Write statements are never retried (R-NORETRY).
- Tabrion **does not confirm**: "confirmed" means the caller passed the hash, the fingerprint and the allowed risk
  classes. Asking the user, showing the preview and logging the action stay in Studio (STU-XW-3, STU-XW-2).

### 5. Role DDL never grants dangerous attributes, also not indirectly (E-X6, TB-035, TB-036)

- Role DDL runs only on **PostgreSQL 16 and later**; on 15 and earlier every role DDL function refuses with
  `UsageError("postgres.role_ddl_requires_16")`, because CREATEROLE before 16 can grant membership in almost any
  non-superuser role and is effectively superuser-equivalent. `role_privileges` stays read-only on every version and
  reports CREATEROLE before 16 as `dangerous` (`createrole_pre16`), so Studio shows an over-privilege finding (D-1).
- `tabrion.postgres` role DDL (TAB-XW-8) has **no parameter** for SUPERUSER, BYPASSRLS, CREATEROLE or REPLICATION:
  they cannot be requested. `RoleOptions` covers `LOGIN`, `INHERIT`, `CREATEDB`, `CONNECTION LIMIT` and
  `VALID UNTIL` only.
- Before every change the function computes the affected role's privileges (`role_privileges`): attributes, direct
  memberships, every role reachable through membership (with INHERIT, the SET option and the **ADMIN option**, since
  with ADMIN a role can grant that membership to itself; N-3), object ownership and default privileges. It refuses
  (`RoleUnsafe`, nothing executed) a change after which the role, or any role that can reach it, could reach a role
  with SUPERUSER, BYPASSRLS, CREATEROLE or REPLICATION.
- **No membership in any predefined role** (allowlist, D-2): a role is predefined when its name starts with `pg_`
  and its oid is below 16384 (`FirstNormalObjectId`); the bootstrap superuser is not `broad`, the superuser check
  covers it. `grant_membership` refuses every predefined role, including `pg_read_all_data`, `pg_write_all_data`,
  `pg_monitor`, `pg_signal_backend`, `pg_create_subscription` and the server-file roles of TB-036. Studio grants
  object privileges explicitly instead. Existing memberships in such roles are reported by `role_privileges` as `broad:<role>`, a
  finding, not a block.
- **Escalation without attributes (K-2):** `TRIGGER` is not grantable; `EXECUTE` on a `SECURITY DEFINER` function
  whose owner is not the grantee is refused; `CREATE` on `public` or on any schema the grantee does not own is
  refused (CVE-2018-1058 class). `role_privileges` reports such existing grants as `dangerous`.
- `WITH ADMIN OPTION` and `WITH GRANT OPTION` are not offered.
- The check runs in the same transaction as the change, right before it (role DDL is transactional in
  PostgreSQL); a refused check leaves nothing changed.
- A password is hashed on the client (`scram-sha-256` through libpq's `PQencryptPassword`) and sent only as the
  verifier, so the clear text never reaches the server log, an error, a `repr` or a frame local.
- Tabrion grants itself nothing and creates no admin role. The admin profile with the minimal rights is provided
  outside Studio (E-X6); `role_privileges` on the session user lets Studio report an over-privileged admin profile
  as a finding (STU-XW-1, STU-XDM-1).

### 6. `pg_dump` scope limits (E-X5)

- Tabrion contains no `pg_dump`/`pg_restore` wrapper and no backup orchestration; the non-goal of specification
  section 1.3 stays unchanged. Studio runs the verified system tools (absolute path from its settings, client version
  at least the server version, no `PATH` fallback, an argument list without a shell, the password only through a
  temporary 0600 `PGPASSFILE`).
- Every Tabrion text that mentions a PostgreSQL backup (guides, docstrings) states that a `pg_dump` archive is **not
  a complete backup**: global roles, role memberships, tablespaces and other cluster objects are not in it.
- Tabrion's share is the SQLite side: `backup()` (exists) and `restore()` (TAB-XW-7, atomic, 0600, with a round-trip
  test backup, restore, `integrity_check` and fingerprint equal), plus `delete_database_file` with the `-wal`,
  `-shm` and `-journal` side files.

### 7. Primitives only: no enable or activate semantics; the gates live in Studio (E-X9)

- Tabrion provides building blocks. It has **no** feature flag, kill switch, "writes enabled" setting, gate check or
  activation state, and no environment variable or configuration that unlocks anything. A primitive that is
  imported can be called; whether a product may call it is the product's decision.
- The gates G-S2 (write foundation and Data Editor), G-S3 (Schema Designer) and G-S4 (Database Manager) and their
  default-off server-side kill switches are in tabrion-studio (STU-ADR-0003 and its successor in STU-XW-5). Tabrion
  shipping a primitive in 0.3 enables nothing in Studio.
- Tabrion never asks, confirms or logs a user action (ADR 0014). It refuses unsafe input (the guards above) and
  never relaxes a guard because a caller says so: there is no `force` for the attribute ban, for plan binding or for
  migration management. The only explicit opt-ins are `drop_database(force=True)` (`WITH (FORCE)`, planning document
  15 section 4) and `overwrite=True` on file targets, as in `backup()`.

### 8. Follow-up work packages

| WP | Content | Depends on | Contract addendum |
|---|---|---|---|
| TAB-XW-2 | `tabrion.schema` v2: foreign keys, unique, check, index, default; fingerprint `v2:`, v1 stays readable | TAB-CR-2 | [A.1](../api/contract-0.3-addendum.md#a1-tabrionschema-v2-tab-xw-2) |
| TAB-XW-3 | DDL for SQLite and PostgreSQL from a target model (`tabrion.ddl`) | TAB-XW-2 | [A.2](../api/contract-0.3-addendum.md#a2-tabrionddl-tab-xw-3) |
| TAB-XW-4 | Diff to `MigrationPlan`, risk classes, SQLite rebuild, Alembic revision text (`tabrion.plan`) | TAB-XW-3 | [A.3](../api/contract-0.3-addendum.md#a3-tabrionplan-diff-plan-and-revision-text-tab-xw-4) |
| TAB-XW-5 | Executor with dry run, plan binding, drift check, `lock_timeout`, foreign key check | TAB-XW-4 | [A.4](../api/contract-0.3-addendum.md#a4-tabrionplanexecute_plan-tab-xw-5) |
| TAB-XW-6 | Guided write transaction (`expect_rowcount`, optimistic predicate, `max_affected`) | - | [A.5](../api/contract-0.3-addendum.md#a5-guided-write-transaction-tab-xw-6) |
| TAB-XW-7 | SQLite `restore`, `delete_database_file` | - | [A.6](../api/contract-0.3-addendum.md#a6-tabrionsqlite-restore-and-delete_database_file-tab-xw-7) |
| TAB-XW-8 | Role DDL with the attribute ban, also indirect (TB-035, TB-036) | - | [A.7](../api/contract-0.3-addendum.md#a7-tabrionpostgres-role-ddl-tab-xw-8) |
| TAB-XW-9 | PostgreSQL `create_database`/`drop_database` | - | [A.8](../api/contract-0.3-addendum.md#a8-tabrionpostgres-create_database-and-drop_database-tab-xw-9) |
| TAB-XW-10 | G-T2 for 0.3 with an independent API review (as TAB-042) | TAB-XW-1..9 | the whole addendum |

### 9. Conditions, decisions and notes of the security review of #76 (binding requirements)

The review of #76 (planning folder `reviews/pr76-tabxw1-review.md`, 2026-11-14, ACCEPTED WITH CONDITIONS) adds the
following requirements. Each names the work package that implements it and the test that proves it; the test names
are binding for that work package (file paths are the planned ones). The [contract addendum 0.3](../api/contract-0.3-addendum.md)
repeats each requirement at its function.

| ID | Requirement | WP | Named tests |
|---|---|---|---|
| **K-1** | The SQLite rebuild re-runs index, trigger and view SQL read from `sqlite_schema`, which in a hostile file is attacker data (`writable_schema`). Each text runs as **exactly one statement** through `execute`, never `executescript`; it must pass `sqlite3.complete_statement` with no remainder after the first statement; its head must match the row's `type` and name (`CREATE [TEMP] TRIGGER`, `CREATE [TEMP] VIEW`, `CREATE [UNIQUE] INDEX` followed by the row's name, quoted or not); it runs on the caller's connection under the caller's authorizer, with `trusted_schema = OFF` and defensive mode (`SQLITE_DBCONFIG_DEFENSIVE`; Studio TX-16). Any violation refuses the plan (`PlanRefused`, reason `unsafe_schema_sql`) before a statement runs: at `plan` time and again at execution | TAB-XW-4, TAB-XW-5 | `tests/unit/test_plan_sqlite_rebuild.py::test_rebuild_refuses_second_statement_in_schema_sql`, `::test_rebuild_refuses_type_or_name_mismatch`, `::test_rebuild_never_uses_executescript`, `::test_rebuild_runs_under_authorizer_and_defensive` |
| **K-2** | Escalation without role attributes: `TRIGGER` is not in the grantable privileges; `EXECUTE` on a `SECURITY DEFINER` function whose owner is not the grantee is refused (`RoleUnsafe`, reason `security_definer`); `CREATE` on `public` or on a schema the grantee does not own is refused (reason `schema_create`, CVE-2018-1058 class); `role_privileges` reports existing grants of these kinds as `dangerous` (`trigger:<table>`, `security_definer:<function>`, `schema_create:<schema>`). Same matrix as the attribute tests | TAB-XW-8 | `tests/postgres/test_role_ddl.py::test_trigger_privilege_not_grantable`, `::test_execute_on_foreign_security_definer_refused`, `::test_create_on_public_or_foreign_schema_refused`, `::test_role_privileges_reports_escalation_grants` |
| **K-3** | Revision text: the module docstring is a fixed text with no caller text in it; the message appears only as a Python string literal (`repr`), so `"""`, a backslash, a line break or NUL cannot break out. `revision` and `down_revision` are validated against `[0-9a-zA-Z_]{1,64}` (`down_revision` may be `None`), else `UsageError("plan.invalid_revision")`. Every revision-text test checks the output with `ast.parse`: only literals appear at those places | TAB-XW-4 | `tests/unit/test_plan_revision_text.py::test_message_cannot_break_docstring` (parametrized `"""`, `\`, `\r\n`, `\x00`), `::test_revision_ids_validated` (a `down_revision` of `"; import os`), `::test_revision_text_ast_has_only_literals` |
| **D-1** | Role DDL only on PostgreSQL 16 and later: `UsageError("postgres.role_ddl_requires_16")` below; `role_privileges` reports CREATEROLE before 16 as `dangerous` (`createrole_pre16`) | TAB-XW-8 | `tests/postgres/test_role_ddl.py::test_role_ddl_refused_before_16`, `::test_createrole_pre16_reported_dangerous` |
| **D-2** | No membership in any predefined role (a role is predefined when its name starts with `pg_` and its oid is below 16384, `FirstNormalObjectId`; the bootstrap superuser is not `broad`, it is covered by the superuser check), an allowlist; includes `pg_read_all_data` and `pg_write_all_data`; existing memberships reported as `broad:<role>` | TAB-XW-8 | `tests/postgres/test_role_ddl.py::test_membership_in_any_predefined_role_refused`, `::test_existing_predefined_membership_reported_broad` |
| **D-3** | `environment` is a required keyword without a default, typed `Literal["dev", "test"]`; a guard rail, not a boundary; Studio fills it from the user-local profile | TAB-XW-5 | `tests/unit/test_plan_execute.py::test_environment_is_required_keyword_without_default` (signature check), `::test_environment_other_values_refused` |
| **D-4** | v2 fingerprints stay opt-in in 0.3: `snapshot(level=1)` stays the default, `v1:` fingerprints stay byte-identical | TAB-XW-2 | `tests/unit/test_schema_v2.py::test_default_level_is_1`, `::test_v1_fingerprint_unchanged_against_02_golden` |
| **N-1** | PostgreSQL drift check: lock the plan's target tables first (under `lock_timeout`), then compute the fingerprint | TAB-XW-5 | `tests/postgres/test_plan_execute_postgres.py::test_locks_taken_before_drift_fingerprint`, `::test_concurrent_ddl_after_check_blocked` |
| **N-2** | `from_json` proves integrity, not authenticity; consumers never take plans from untrusted sources (documented in the guide and the docstring) | TAB-XW-4 | `tests/unit/test_plan.py::test_from_json_docstring_states_integrity_only` |
| **N-3** | Reachability counts the ADMIN option on a dangerous role | TAB-XW-8 | `tests/postgres/test_role_ddl.py::test_admin_option_on_dangerous_role_counts_as_reachable` |
| **N-4** | `delete_database_file` works through a directory file descriptor with `O_NOFOLLOW` and compares device and inode of the checked file before the unlink; side files that are links are only unlinked, never followed | TAB-XW-7 | `tests/unit/test_sqlite_delete.py::test_swap_between_check_and_unlink_refused`, `::test_side_file_symlink_unlinked_not_followed` |
| **N-5** | `IS NOT DISTINCT FROM` on floating-point, JSON or collation-dependent columns may give false conflicts (safe side: no write); documented in the guide | TAB-XW-6 | `tests/unit/test_rowedit.py::test_float_and_json_old_values_may_conflict` |

### 10. D-5: drift blind spots of the level-2 fingerprint (owner decision of 2026-11-15)

The review of #85 (planning folder `reviews/pr85-tabxw2-review.md`, F2) found schema features that the `v2:`
fingerprint of `tabrion.schema` does not see ([contract I-111 d](../api/contract.md#510-schema-fingerprint-tabrionschema-since-02-experimental-extra-sqlalchemy-tab-cr-2)),
so a plan bound to a fingerprint (section 4) would not notice them changing. The owner decided how to close them on
2026-11-15 at 06:37Z (confirmed 06:38Z). The decision is binding for TAB-XW-3..TAB-XW-5; TAB-XW-2 (#85) only records
it. **G-S3 stays locked** until the implementation is in place, independently reviewed and tested.

| ID | Decision | WP |
|---|---|---|
| **D-5.1** | **Triggers and row-level security policies** go into the level-2 fingerprint, together with the functions they reference directly (the function body). Calls nested inside those functions are not followed; that is a documented residual risk. A table whose trigger or policy function is written in C or in a language Tabrion does not know is refused. As long as a table's triggers and policies cannot be checked reliably, schema changes on that table are refused | TAB-XW-3..TAB-XW-5 |
| **D-5.2** | **Domain `CHECK` constraints and partitioning** are detected; schema changes on affected tables are refused with a clear explanation | TAB-XW-4, TAB-XW-5 |
| **D-5.3** | **Unknown PostgreSQL features fail closed:** an allowlist of known relation kinds and features; anything else is refused, for example table inheritance, rules, foreign tables and event triggers | TAB-XW-4, TAB-XW-5 |
| **D-5.4** | **Drift check right before execution**, repeated in the execution transaction after the affected tables are locked under `lock_timeout` (section 4, N-1). Functions cannot be locked; a function changed between the check and a step is a documented residual risk | TAB-XW-5 |
| **D-5.5** | **Data Editor (G-S2):** a table with triggers gets a notice in Studio, not a block | Studio (STU-026, STU-XDE) |

Residual risks, to be repeated in the contract and the guides when TAB-XW-3..TAB-XW-5 implement D-5: functions called
from a trigger or policy function are not in the fingerprint, and a function can change after the drift check because
it cannot be locked.

## Rationale

- Revision text only keeps Tabrion and Studio away from running project code: an Alembic `env.py` is arbitrary
  Python with the project's credentials (Studio threat T-08). Producing text costs the user one `alembic upgrade`;
  running it would make Tabrion a code-execution path.
- Refusing managed databases keeps one owner per schema: a direct change to a database with a version table would
  make the next migration fail or, worse, apply on top of an unexpected shape.
- Plan hash plus fingerprint plus a drift check right before execution closes the gap between preview and execution
  (R-X3): a swapped plan, a stale preview and a concurrent schema change all abort before the first statement.
- The attribute ban by construction (no parameter) is stronger than a check of a parameter, and the reachability
  check closes the membership path that TB-035 found in the consumers' role checks.
- Keeping gates and switches out of Tabrion keeps one place where a write function is enabled (Studio) and keeps
  Tabrion a library for other consumers (Nodestro, applications) that have their own rules.

## Rejected alternatives

| Alternative | Reason |
|---|---|
| Run Alembic revisions for the user (`command.upgrade`, a `MigrationContext`) | Runs `env.py` and project code (T-08); contradicts E-X4 and W-6; would make Tabrion a runner (ADR 0007) |
| Direct execution on any non-PROD database, also with migration management | Two owners of one schema; refused by E-X4 |
| A Tabrion feature flag or setting `writes_enabled` | Two places that enable writing; the gates are Studio's (E-X9). A library flag is also trivially set by any caller |
| Plan binding by plan hash only, without the fingerprint and a drift check | A plan stays valid against a changed database; R-X3 needs both |
| A `force`/`allow_dangerous` parameter for role attributes | E-X6 forbids the grant in every case; a parameter would be an invitation |
| Role checks on direct attributes only | Misses membership and `SET ROLE` paths (TB-035, review H-1) |
| A `pg_dump` wrapper in Tabrion | Non-goal of specification section 1.3; E-X5 option a rejected |
| An own DDL builder or SQL templates | Non-goal; SQLAlchemy Core and Alembic already compile dialect-correct DDL |

## Consequences

- TAB-XW-2..TAB-XW-9 implement the planned surface of the [contract addendum for 0.3](../api/contract-0.3-addendum.md);
  names and signatures there are planned, not implemented, and may still change until TAB-XW-10. Each work package
  moves its section into [the contract](../api/contract.md), adds the names to `docs/api/surface.json` with
  `"since": "0.3"` and stability `X`, and keeps them unimportable before the version is 0.3 (as G-T1 condition C-6
  did for 0.2).
- [Specification section 1.3](../specification.md#13-non-goals-binding) gets the CR-DBX clarification and section 8
  the note on SQLite `restore()` in 0.3.
- `tabrion.profilefile` keeps refusing libpq multi-host profiles in 0.3 (W-10, TB-148 (1)); the profile `mode`
  `admin` (E-X8) is added with TAB-XW-8, `read_write` exists since 0.2.
- Studio's work packages that use these primitives (STU-026, STU-CR-2..5, STU-XSD-2..3, STU-XDM-2..4) stay locked by
  Studio until their gate.

## Security guarantees

- No code path in Tabrion runs `env.py`, project code or `alembic upgrade`; no Tabrion function reads a project
  directory.
- The executor refuses a database with migration management and an undeclared or non-DEV/TEST environment, and runs
  exactly the plan whose hash and source fingerprint it was given, after a drift check; otherwise nothing runs.
- No role DDL grants SUPERUSER, BYPASSRLS, CREATEROLE or REPLICATION, directly or through membership (INHERIT, SET
  or ADMIN option), nor membership in any predefined `pg_*` role, nor `TRIGGER`, `EXECUTE` on a foreign
  `SECURITY DEFINER` function or `CREATE` on `public` or a foreign schema; role DDL runs only on PostgreSQL 16 and
  later.
- SQL text read from a SQLite file's `sqlite_schema` runs only as one checked statement under the caller's
  authorizer and defensive mode (K-1); revision text holds caller text only as literals (K-3).
- No secret in plans, revision text, errors, `repr`, logs or frame locals (redaction of `tabrion.errors`, canary
  `SECRET-4711` tests); a role password reaches the server only as a SCRAM verifier.
- Tabrion has no switch that enables a write or admin function.
- R-SET and T-NOTX stay: the executor sets only `lock_timeout` (`SET LOCAL`, on the allowlist) and the SQLite
  `foreign_keys` pragma on a connection it opened itself; `create_database`/`drop_database` need an autocommit
  connection and never switch it.

## Verification

Acceptance criteria of planning document 15 section 9.4 for the Tabrion work packages, checked in TAB-XW-10:

- **TAB-XW-4:** a detected Alembic marker gives revision text only; negative tests that no `env.py`, no project
  module and no `alembic.command` is touched (import and file-access probes); identifier and message injection into
  the revision text is inert.
- **TAB-XW-5:** plan binding (plan hash and fingerprint), drift check right before execution with abort, tests for a
  swapped plan, an edited plan, drift between preview and execution, and the negative tests "Alembic version table",
  "Nodestro or Axentra bookkeeping table", "SQLite `user_version` > 0" and "environment `prod` or missing"; dry run
  leaves the database unchanged (fingerprint before and after); SQLite rebuild golden tests (triggers, views, indexes
  kept, R-X2) with `foreign_key_check`.
- **TAB-XW-7:** SQLite `restore` round trip: backup, restore, `integrity_check` `ok` and fingerprint equal.
- **TAB-XW-8:** the attribute ban, also indirect: a member of a SUPERUSER, BYPASSRLS, CREATEROLE or REPLICATION role
  (INHERIT and NOINHERIT, with and without the SET option, with the ADMIN option), chains of two and more roles,
  every predefined role; the escalation grants of K-2; refusal on PostgreSQL 15 and earlier; `role_privileges` shows
  existing privileges, memberships and indirect privileges; password canaries.
- The named tests of section 9 for K-1..K-3, D-1..D-3 and N-1..N-5.
- Mutation probes for each guard (a removed check turns a test red) and the independent API review (TAB-XW-10).

## Revision triggers

- A consumer needs Tabrion to apply Alembic revisions or to run a migration with a version table (a new decision on
  ADR 0007 and E-X4).
- The owner changes E-X4, E-X5, E-X6, E-X7 or E-X9, or allows writing on PROD.
- PostgreSQL adds a role attribute with superuser-equivalent power (extend the ban list). New predefined roles need
  no change: every `pg_*` role is refused (D-2).
- A consumer other than Studio asks for a gate or switch in Tabrion.

## Changes from the plan

None in substance. Two precisions of the plan's text, both within E-X4 and E-X6: the "DEV/TEST" condition becomes an
explicit `environment` declaration by the caller (Tabrion cannot detect it), and "migration management" is
detected by the markers listed in section 3 of this record.
