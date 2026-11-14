# ADR 0014: Tabrion scope for CR-DBX: DDL, diff and plan building blocks; no `pg_dump` wrapper, no role-admin UI

- **Status:** Accepted as planning (owner decisions on change request CR-DBX of 2026-11-13 23:28Z, E-X1 and E-X2,
  and 23:35Z, E-X3..E-X13 as recommended with binding refinements of E-X4, E-X5, E-X6 and E-X9). This accepts the
  plan, **not** the start of implementation: no TAB-XW work package starts before Studio 1.0 (G-R), the repository
  migration and a separate development-start approval (G-CX).
- **Plan reference:** planning document 15 (CR-DBX: Database Manager, Schema Designer and Data Editor), sections 3,
  5, 7 and 9.1; work packages TAB-XW-1..TAB-XW-10.

## Context

Change request CR-DBX extends Tabrion Studio with three areas, delivered in this order: the shared write foundation
and the Data Editor (Studio 1.1, gate G-S2), the Schema Designer (Studio 1.2, gate G-S3, milestone M10) and the
Database Manager (Studio 1.3, gate G-S4, milestone M11). CR-DB (planning document 14) put the DDL, diff and
migration-plan logic of the Schema Designer inside Studio (STU-CR-5). Nodestro (drift detection NOD-CR-5, an empty
schema in exports E-CR-6) and other applications need the same logic, which meets the core-API rule of planning
document 03 section 4.7 (a new core API only when a non-Studio application has the same use).

Tabrion 0.2 already has the read side: `tabrion.schema` (`snapshot`, `fingerprint`, `compare`; TAB-CR-2),
`tabrion.profilefile` (TAB-CR-1), `tabrion.sqlite.backup`, `tabrion.postgres.roles` (`role_info`, `check_role`,
`inspect_role`, `grant_runtime_role`) and `tabrion.migrations` (`status`, `require_current`; ADR 0007).

## Decision

- **E-X3:** the generic, UI-free building blocks for schema changes live in Tabrion, as experimental (`X`) modules
  of Tabrion 0.3 (E-X11), built on SQLAlchemy Core DDL (`CreateTable` and friends) and Alembic operations:
  - `tabrion.schema` v2 with foreign keys, unique, check, index and default (TAB-XW-2); fingerprints `v2:`, v1 stays
    readable;
  - dialect-correct DDL for SQLite and PostgreSQL from a target model (TAB-XW-3);
  - a diff into a `MigrationPlan` with a risk class per step, a transactional flag, the SQLite table rebuild and an
    Alembic revision **as text only** (TAB-XW-4);
  - an executor with dry run, binding to the plan hash and the schema fingerprint, `lock_timeout`, non-transactional
    steps run one by one and marked, and `foreign_key_check` after a SQLite rebuild (TAB-XW-5);
  - a guided write transaction (`expect_rowcount`, optimistic predicate, `max_affected`; no upsert or merge,
    TAB-XW-6);
  - SQLite `restore()` (atomic, 0600) and `delete_database_file` (TAB-XW-7);
  - quoted, checked role DDL (`create/alter/drop_role`, `grant`/`revoke`) that refuses SUPERUSER, BYPASSRLS,
    CREATEROLE and REPLICATION and checks the roles reachable through `SET ROLE` (TB-035; TAB-XW-8);
  - PostgreSQL `create_database`/`drop_database` with privilege and session checks (TAB-XW-9).
- **No own query or DDL builder:** SQLAlchemy Core and Alembic operations are the builder. Specification section 1.3
  ("Own query language or builder") is made precise by TAB-XW-1, not changed.
- **Tabrion is not a schema tool UI:** Tabrion never asks, confirms or logs a user action. Previews, confirmations,
  the audit log, write and admin profiles and the secret-reference confirmation of I-109 j belong to Studio.
- **Not a migration runner (ADR 0007 stays):** the executor runs one plan once, without a version table, ordering or
  history. It is meant for DEV and TEST databases without migration management (E-X4). For Alembic projects the
  building blocks only produce revision text; Tabrion never runs a project's `env.py` or revision code.
- **No `pg_dump`/`pg_restore` wrapper (E-X5):** the non-goal of specification section 1.3 stays. A PostgreSQL backup
  or restore is Studio's job, through the verified system tools. Tabrion adds only the SQLite `restore()` to the
  existing `backup()`.
- **No role-admin UI (E-X6):** Tabrion provides the checked role DDL only; role wizards, password handling and the
  admin profile are in Studio, for PostgreSQL only. SQLite has no roles.
- **No vector database functions:** the non-goal "AI, vector, RAG functions" of specification section 1.3 stays;
  Tabrion keeps reporting the extension status only.
- **No new repository (E-X12):** the building blocks live in this repository; no new dependency edge (K-33
  unchanged).
- **Release:** Tabrion 0.3 with these modules as experimental; G-T2 for 0.3 with an independent API review
  (TAB-XW-10, as TAB-042). Stable (`S`) only after a first consumer used them (API stability policy S-1).
- **Milestone names (E-X13):** M9 and M10 stay the program milestones of STATUS.md. Where existing records mean the
  Nodestro guarantees (the atomic migration "M9" in ADR 0007 and the shared pool "M10" in ADR 0003, 0006, 0011, 0013,
  the contract and the specification), those references stay valid; new texts call them NOD-M9 and NOD-M10.

## Rationale

- The logic is generic (schema model, DDL, diff, execution guards) and has non-Studio users, so it belongs in the
  library; the user interaction does not.
- SQLAlchemy Core and Alembic operations already produce dialect-correct DDL; a builder of our own would duplicate
  them and violate the non-goal.
- A backup wrapper and an admin UI are operations tooling; keeping them out keeps Tabrion a library (ADR 0001).

## Rejected alternatives

| Alternative | Reason |
|---|---|
| DDL, diff and plan inside Studio (STU-CR-5 as in CR-DB) | Nodestro and applications would copy the logic (planning document 15, E-X3 option a) |
| A `pg_dump`/`pg_restore` wrapper in Tabrion | Non-goal of specification section 1.3; an operations tool (E-X5 option a) |
| An own DDL builder | Non-goal; SQLAlchemy Core is the builder |
| A new repository `tabrion-admin` | Not needed; no standalone admin product is ordered (E-X12) |

## Consequences

- Planned work packages TAB-XW-1..TAB-XW-10 (19 + 9.5 agent days) in [STATUS.md](../program/STATUS.md#change-request-cr-dbx-planning-document-15)
  and [TODO TB-149](../TODO.md); none starts before the development-start approval.
- TAB-XW-1 writes the contract addendum and the precision of specification section 1.3.
- `tabrion.profilefile` gets the profile modes `read_write` and `admin` (E-X8) with the addendum; the multi-host
  question of TB-148 (W-10) is decided with STU-CR-6.

## Security guarantees

- Role DDL never grants SUPERUSER, BYPASSRLS, CREATEROLE or REPLICATION and refuses a grant that makes such a role
  reachable.
- The executor runs exactly the plan whose hash and source fingerprint it was given; drift aborts it.
- No secrets in plans, revision text, errors or logs (redaction as in `tabrion.errors`; canary tests).
- Writing stays off by default for every consumer: nothing in Tabrion enables a write path on its own.

## Verification

- Before G-T2 for 0.3 (TAB-XW-10): golden tests of the SQLite rebuild (triggers, views, indexes kept), plan-hash and
  drift tests, refused role attributes and `SET ROLE` reachability, canary tests for plans and revision text,
  mutation probes, and the independent API review.

## Revision triggers

- A consumer needs a PostgreSQL migration runner with a version table (then a new decision on ADR 0007).
- The owner withdraws or changes CR-DBX, or the development-start approval names a different scope.

## Changes from the plan

None. The plan is planning document 15; this record is its English decision record for this repository.
