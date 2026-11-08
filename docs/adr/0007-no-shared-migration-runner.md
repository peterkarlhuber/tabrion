# ADR 0007: No PostgreSQL migration runner in Tabrion; existing runners are not merged

- **Status:** Accepted (2026-11-07). **E-06-2 / G-EM = no** was decided by the owner at 23:26Z. MIG-AXE-7, S6/S7
  and the atomic Nodestro mode are dropped.
- **Plan reference:** TAB-ADR-0007 (planning document 10)

## Context

Comparison of guarantees (plan document 06 section 5.1):

- **Nodestro:**
  - atomic across all steps (M9) with `pg_advisory_xact_lock` in the transaction (`storage_postgres.py:521`);
  - adoption of legacy checksums after the backup (OPS2-01, `:608-613`);
  - a required backup inside the open transaction;
  - Python steps without a checksum.
- **Axentra:**
  - namespaces and entry points;
  - one transaction per migration, `no-transaction` for `CREATE INDEX CONCURRENTLY`;
  - a polling session lock (`migrations.py:309-317`);
  - timeout directives, `before_apply`, `plan`, `allow_newer`.
- **Orkexa:** one version table for its own tables.
- Both existing runners run on the same server database with different lock keys.

## Decision

- Tabrion 0.x contains no PostgreSQL migration runner.
- New Python applications use Alembic through `tabrion.migrations.status` and `require_current`; Tabrion does not run
  `env.py`. For SQLite there is `tabrion.sqlite.migrate` (`user_version`); for non-Python applications the kit
  recommends dbmate.
- Only rules are shared, not code:
  - `tabrion.sql.split_statements`: Axentra's splitter moves to Tabrion and stays in Axentra as a re-export
    (MIG-AXE-2, category A, byte-identical over every migration file);
  - `wait_advisory_lock`: Axentra's runner uses it for its migration lock (MIG-AXE-5) with the lock key unchanged;
    `try_advisory_lock`/`SessionLock` for leader locks in Axentra and Nodestro;
  - the runner itself stays in Axentra (AX-15, category C; G-EM = no).
- The checksum function is **not** shared (N-01, API contract section 2.2).
- **Former criteria for G-EM = yes** (plan document 06 v3 section 5.4; after the decision "no", only rationale):
  - **S6** (Axentra's runner becomes `tabrion.postgres.migrations`, MIG-AXE-7) only if all three hold:
    1. A second consumer with SQL-file migrations in Python is named and has excluded Alembic with reasons.
    2. Stages S1, S2a, S2b, S2c, S3 and S4 (waves W1-W6, plan document 06 section 7.1) have been stable without
       findings for at least three months.
    3. The dry-run comparison (`plan`) across every Axentra namespace and the backup-restore drill are prepared as
       acceptance tests.
  - **S7** (Nodestro in the atomic mode of the same runner) only if S6 is stable **and** Nodestro needs a runner
    extension that S6 already offers (for example namespaces).

## Rationale

- A merged runner would be the union of two runners with a mode switch, not a shared abstraction.
- Benefit close to zero, cost 8-12 agent days, risk at the most critical place (upgrade paths U1-U8,
  `migration_checksums.json`, backup order) (plan document 06 section 5.3).

## Rejected alternatives

| Alternative | Reason |
|---|---|
| Axentra's runner as `tabrion.postgres.migrations` (S6, MIG-AXE-7) | Dropped (G-EM = no); no second user proven; 6 + 2 agent days (plan document 06 section 5.2) |
| Nodestro on the merged runner in `atomic` mode (S7) | Same; the highest regression risk |
| A Tabrion runner of its own for new applications | Alembic covers Python applications; dbmate covers the rest |
| A shared checksum function | An identical one-liner; coupling without benefit |

## Consequences

- S6/S7 and MIG-AXE-7 are dropped (G-EM = no) and not part of the effort.
- The guarantee table of both runners becomes part of the Tabrion migration guide.
- Studio reads the version state of the Levuvo runners read-only, with copied fixtures and no dependency (STU-024).

## Security guarantees

- Bookkeeping tables, checksums, lock keys, backup order and gates of the existing runners do not change.
- A later merge must not change the bookkeeping tables and needs a tested rollback (plan document 11 section 3.5).

## Verification

- TAB-T-Q01..Q10: splitter identity with every Axentra case and migration file (acceptance MIG-AXE-2:
  byte-identical).
- MIG-AXE-5: runner concurrency with `wait_advisory_lock`.
- TAB-T-L01..L10: locks.
- The existing runner tests in Nodestro and Axentra stay green unchanged.

## Revision triggers

- A second consumer with SQL-file migrations appears; then the agent puts a new decision to the owner (stop
  criterion ST-9, plan document 11 section 4).

## Changes from the plan

None.
