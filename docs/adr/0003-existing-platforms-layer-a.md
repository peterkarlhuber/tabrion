# ADR 0003: Existing platforms use layer A; no SQLAlchemy migration

- **Status:** Accepted (2026-11-07). Binding through K-30..K-32 (plan document 00 section 12) and T-E3 (no forced
  SQLAlchemy migration). The Orkexa core uses `tabrion` (E-ORK-CORE = yes, decided 2026-11-07 23:26Z, conditions in
  plan document 06 section 3.2; MIG-ORK-3). Moving any platform to SQLAlchemy is finally rejected.
- **Plan reference:** TAB-ADR-0003 (planning document 10)

## Context

- **Axentra:**
  - 229 call sites of `.transaction(DbScope...)` or `.transaction(scope...)` under `axentra/src`, 257 calls of
    `.transaction(` in total (grep 2026-11-07, master 40c0f2c).
  - `Tx` with `dict_row` (`data/database.py:79-127`).
  - RLS through `DbScope` and `set_config(..., true)`.
  - Savepoint join on a foreign pool (`join`/`on_pool`, M10).
  - LISTEN/NOTIFY; `CREATE INDEX CONCURRENTLY` with `no-transaction`.
  - psycopg is already required there (`axentra/pyproject.toml:26`).
- **Nodestro:**
  - `PostgresDatabase` with its own pool, liveness check and RT2-03; `SharedPool` (M10).
  - The tenant checker (ADR-0010).
  - `SQLiteStore` in the core (`storage.py:263-669`); findings B-06-2, B-06-3 and B-06-5 (plan document 06
    section 0.2).
- **Orkexa:**
  - `_storage.py:80-166` with private SQLite files.
  - `PsycopgSource` without timeouts, ping or error sanitizing (`backends/postgres/connection.py`).
- **Measurement (probe s.py):** SQLAlchemy costs 80-130 microseconds extra per statement. RLS would be technically
  possible with SQLAlchemy (`set_config(..., true)` in the `begin` event).

## Decision

- All existing platforms use **layer A** only (`tabrion.sqlite`, `tabrion.postgres`, `tabrion.errors`,
  `tabrion.sql`, `tabrion.testing`). Categories A/B/C/D per function are in plan document 06.

| Platform | Edge | Use |
|---|---|---|
| Axentra | **required** `tabrion[postgres]` (K-30) | Role tooling, splitter, fixture base (A); `role_info`/`check_role`, session configuration, leader and migration lock, safe error logging (B). Details in [ADR 0013](0013-axentra-adopts-tabrion.md) |
| Nodestro | core `tabrion` (SQLite, MIG-NOD-6) and the server profile `tabrion[postgres]` in the extra `[postgres]` (K-32) | `SQLiteStore` delegates opening, transactions, migration and backup (B). `PostgresDatabase` delegates roles, `SessionLock`, extension status, error texts and optionally `configure`. Pool, M10, runner M9, jobs and tenant checker stay (C) |
| Orkexa | extra `orkexa[postgres]` to `tabrion[postgres]` (optional, MIG-ORK-2); core `orkexa` to `tabrion` (**E-ORK-CORE = yes**, MIG-ORK-3) | `PsycopgSource` gets timeouts, `ping` and safe error texts; core SQLite opening and path rules (A/B); the AI parts stay |

- Public platform APIs (`SQLiteStore`, `PostgresDatabase`, `axentra.data.Database`, `axentra.data.roles.*`,
  `split_statements`, `PsycopgSource`) keep their names, signatures and error types. Where the implementation moves
  to Tabrion, a thin compatibility shim stays.
- No platform moves to SQLAlchemy Core or the ORM.

## Rationale

- Reuse is wanted (P3). The generic and partly security-relevant functions (roles and grants, file permissions, error
  sanitizing) are maintained once and tested against PostgreSQL 16, 17 and 18.
- Layer A fits the psycopg and sqlite3 architecture of all three platforms; layer B solves no problem there.
- SQLAlchemy would be expensive (229 call sites, M10, savepoint join, LISTEN, per-statement overhead). The regression
  risk would sit at the security boundaries RLS (Axentra ADR 0005) and tenant checking (Nodestro ADR-0010).

## Rejected alternatives

| Alternative | Reason |
|---|---|
| Axentra on SQLAlchemy Core | No functional gain at high cost (plan document 06 section 2) |
| Nodestro on SQLAlchemy Core | Pool, liveness check (RT2-03) and `SharedPool` would have to be rebuilt; the desktop bundle grows |
| Orkexa backends on SQLAlchemy | The statement catalog and the partition guard are bound to psycopg and sqlite3 |
| Blanket independence (22:26Z) with vector parity instead of use | Lifted by the owner (plan document 00 section 12); the duplication would stay ([ADR 0008](0008-dependency-principles-and-edges.md)) |
| Partial migration to SQLAlchemy (new modules only) | Two access paradigms in one repository |

## Consequences

- Layer B has no existing consumer in this program, only the reference applications and Studio. The six-month review
  measures its value.
- Tabrion is a required dependency of Axentra and the Nodestro core and so reaches Desktop and Local Docker. That is
  why the stdlib-only guarantee of the core (AC-3) and the API stability policy (specification section 12,
  [ADR 0009](0009-distribution-and-versioning.md)) apply.
- ADR addenda: Nodestro ADR-0002, Axentra ADR 0003 and ADR 0005 (plan document 10 section 15).

## Security guarantees

- The contract rule **T-NOTX** applies ([ADR 0011](0011-ldp-internal-and-t-notx.md)): Tabrion never ends, commits or
  changes the transaction or session state of a connection it is given; only `configure` sets session parameters.
- For Axentra, G-AX-1..8 and AX-I-1..8 apply ([ADR 0013](0013-axentra-adopts-tabrion.md)).
- For Nodestro, ADR-0010, M10 and RT2-03 stay untouched.
- Orkexa's partition guard stays in Orkexa.

## Verification

- Contract tests compare `conn.info.transaction_status`, `row_factory` and `current_setting(...)` before and after
  every layer A call.
- The Axentra RLS suite and SR-01 run in split-role mode (MIG-T-AXE-*).
- `tenant_sql_check` and the M10 tests (`SharedPool` with Axentra) stay green.
- The Orkexa conformance suite (`orkexa/testing/conformance/`).
- AC-1..AC-7 ([ADR 0012](0012-architecture-checks.md)).

## Revision triggers

- MIG-ORK-3 fails the conditions of plan document 06 section 3.2.
- A consumer needs functions that only SQLAlchemy offers (new ADR, separate approval).

## Changes from the plan

None.
