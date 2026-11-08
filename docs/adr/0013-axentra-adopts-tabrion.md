# ADR 0013: Axentra adopts Tabrion (`tabrion[postgres]` required) with unchanged RLS, DbScope and M10 guarantees

- **Status:** Accepted (2026-11-07; K-30). In addition, an independent security review agent checks every MIG-AXE
  step with a focus on AX-I-1..8 (plan document 07 section 8).
- **Plan reference:** TAB-ADR-0013 (planning document 10)

## Context

Inventory AX-1..AX-19 (plan document 06 section 2.1, axentra 40c0f2c):

- The role tooling `AX/data/roles.py:30-190` is nearly identical to `RT/storage_postgres.py:808-905`.
- `role_info`/`check_role` (`database.py:359-405`, `owner_scope` RLS).
- `_configure` sets only `statement_timeout` (`:264-266`).
- Leader lock (`events/schedules.py:~80-118`); a polling migration lock (`migrations.py:309-317`).
- Splitter (`migrations.py:125-178`).
- B-06-1: `DETAIL` values reach the log through `api/problems.py:57`; `redact_text` (`core/redaction.py:76-84`) does
  not remove them.
- The RLS core: `DbScope`, `set_config(..., true)`, `join`/`joined`/`on_pool` (M10), FORCE RLS (34 policies,
  ADR 0005).

## Decision

Categories per function:

| Cat. | Functions | Implementation |
|---|---|---|
| **A** (only in Tabrion, a shim in Axentra) | AX-1 `RoleReport`/`inspect_role`/`grant_runtime_role`; AX-7 `split_statements`; AX-14 fixture base | MIG-AXE-6 (roles), MIG-AXE-2 (splitter, re-export, byte-identical), MIG-AXE-1 (`tabrion.testing` database per session, split roles, truncate; `pg_urls` keeps its names and dict form) |
| **B** (the Axentra function stays and uses Tabrion inside) | AX-2 `role_info`/`check_role` (`owner_scope="rls"`; rejection logic and messages stay); AX-3 `_configure` to `tabrion.postgres.configure` (new options off by default; `commit` stays in Axentra); AX-4 leader lock to `SessionLock`; AX-5 migration lock to `wait_advisory_lock(conn, key, poll=0.2)`; AX-8 error logging to `safe_message` plus a logging filter (closes B-06-1) | MIG-AXE-6, -4, -5, -3 |
| **C** (stays in Axentra) | `Database`, pool ownership, `on_pool`, `join`, `DbScope`, RLS (AX-13), `Tx` (AX-12), the 16 local `except pg_errors.*` (AX-9), `ping` (AX-10), transaction advisory locks (AX-6), pool metrics, secret references, LISTEN, records, queues, search, the runner (AX-15; G-EM = no) | no change |
| **D** (third party) | `ConnectionPool.check_connection` (AX-11) | stays psycopg_pool |

- The edge `axentra` to `tabrion[postgres]` is required in the core, as a range per V-B0
  ([ADR 0009](0009-distribution-and-versioning.md)). `dev` points to `tabrion[testing,postgres]`.
- **No `configure` on borrowed pools:** in the `on_pool` path (Nodestro `SharedPool`, M10, `database.py:218-224`)
  Axentra sets no session parameters and does not call `tabrion.postgres.configure`. `configure` runs only in
  Axentra's own pool `configure` callback.
- No move to SQLAlchemy ([ADR 0003](0003-existing-platforms-layer-a.md)). No runner move (G-EM = no; MIG-AXE-7 is
  dropped, [ADR 0007](0007-no-shared-migration-runner.md)). B-06-1 is fixed locally right after G-A (MIG-AXE-0) and
  moved to Tabrion in MIG-AXE-3.

**Guarantees (must hold at every step; evidence in plan document 06 section 2.2):**

| ID | Guarantee |
|---|---|
| G-AX-1 | FORCE RLS unchanged: no migration or policy changed, Tabrion creates no objects |
| G-AX-2 | `DbScope` stays bound to the transaction; Tabrion is never called inside a `DbScope` transaction; T-NOTX |
| G-AX-3 | The psycopg 3 architecture (`dict_row`, `autocommit=False`) unchanged; Tabrion does not change `row_factory` |
| G-AX-4 | Pool behavior unchanged; Tabrion has no pool; `configure` only from Axentra's callback |
| G-AX-5 | M10: a borrowed pool (`on_pool`/`joined`) without session changes; the savepoint join stays |
| G-AX-6 | API contracts (`axentra.data.roles.*`, `split_statements`, `Database.role_info/check_role/ping`) keep names, signatures, return values and error types |
| G-AX-7 | Migrations and checksums unchanged; splitter output byte-identical; `axentra migrate --dry-run` identical |
| G-AX-8 | No new server requirements; CI matrix PostgreSQL 16/17/18 |

| ID | Invariant (security plan for the Axentra adoption, plan document 07 section 8) |
|---|---|
| AX-I-1 | Scope (`axentra.scope`, `product_id`, `tenant_id`) only through `set_config(..., true)`; empty after `COMMIT`/`ROLLBACK` |
| AX-I-2 | Tabrion never uses `set_config(..., false)` or `SET` without `LOCAL` for scope names; session-wide only timeouts, `application_name` and `search_path` at connection setup |
| AX-I-3 | Connections with an open or failed transaction never return to the pool with a scope (also in Tabrion's error path) |
| AX-I-4 | `join`/`on_pool`: the savepoint join restores the previous scope; Tabrion opens no connection and no pool in this path |
| AX-I-5 | The role check rejects superuser, `BYPASSRLS` and (optionally) table ownership with an identical decision and message code |
| AX-I-6 | FORCE RLS on every tenant table equals the before snapshot (`relforcerowsecurity`) |
| AX-I-7 | Error texts outward and in the log carry no parameter values or `DETAIL` (B-06-1); the Problem JSON schema is unchanged |
| AX-I-8 | Tabrion helpers never repeat write statements after sending has started (R-NORETRY) |

## Rationale

- About 260 lines less to maintain; the security-relevant role and grant logic exists only once (together with
  Nodestro).
- B-06-1 is closed.
- Axentra's RLS core and pool ownership stay entirely in Axentra (P1, P7).

## Rejected alternatives

| Alternative | Reason |
|---|---|
| Axentra without Tabrion, parity through vectors (version 2) | Lifted (K-30, K-35); the duplication stays |
| Tabrion optional in Axentra (an extra) | The data layer is Axentra's core and psycopg is required there; an extra would create two code paths |
| Move `Tx`/`DbScope` to Tabrion | Couples the RLS handle to Tabrion and violates model neutrality ([ADR 0010](0010-model-neutrality.md)) |
| The 16 local `except pg_errors.*` through `classify` | Precise mappings in their domain; a detour without gain |
| Tabrion's `ping` instead of Axentra's `ping` | Ten lines with their own semantics; no gain |

## Consequences

- Axentra has a new required edge. The release cascade for a Tabrion minor: first axentra (range), then runtime.
- Standalone Axentra installs pull `tabrion[postgres]`, under V-B0 from a Git source (README).
- Rollback: revert the range and shim pull requests in reverse order (plan document 11 section 3).

## Security guarantees

G-AX-1..8 and AX-I-1..8; a stop criterion for every finding that touches RLS, scope or M10 (plan document 11 ST-4,
ST-13).

## Verification

- The RLS suite and SR-01 in mode `AXENTRA_TEST_SPLIT_ROLES=1`.
- `test_scope_survives_tabrion_calls`.
- `SHOW ALL` snapshots: own pool and borrowed pool (M10, Nodestro's `test_axentra_*`).
- Signature and CLI snapshots.
- Splitter comparison over every `*.sql`.
- A catalog snapshot of the grants.
- MIG-T-AXE-1..8.
- The canary suite.
- Fault injection (QA-3).

## Revision triggers

- A review finding on AX-I.
- Axentra changes its RLS model.

## Changes from the plan

None. The Axentra side of this decision is implemented in the axentra repository (MIG-AXE-*); this repository
provides the layer A functions.
