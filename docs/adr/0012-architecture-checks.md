# ADR 0012: Automated architecture checks AC-1..AC-7 against the edge list

- **Status:** Accepted (2026-11-07; K-33, K-36; details in plan document 07 section 7 and plan document 05 sections
  4.2-4.4).
- **Plan reference:** TAB-ADR-0012 (planning document 10)

## Context

- The edge list ([ADR 0008](0008-dependency-principles-and-edges.md)) and "exactly one Tabrion version"
  ([ADR 0009](0009-distribution-and-versioning.md)) hold only if a machine checks them, especially with agent-driven
  implementation (R-09).
- Existing patterns: import-linter in orkexa, axentra and runtime; blocked-module tests (orkexa
  `tests/packaging/block_extras.py`); `check_versions.py`.

## Decision

The source of truth is `nodestro-studio/platform/dependency-policy.json` with `{from, to, kind: required|optional,
extra, status}`. Each library carries a copy of its outgoing edges; Tabrion's is empty.

| ID | Check | Where | Status |
|---|---|---|---|
| AC-1 | import-linter per edge list: tabrion forbids every platform and Studio; orkexa forbids axentra and nodestro* (its core also `tabrion` until MIG-ORK-3, E-ORK-CORE = yes; `orkexa.backends.postgres` may use `tabrion.postgres`); axentra forbids orkexa, nodestro* and axentra_studio and allows `tabrion`; each library forbids its Studio | tabrion, orkexa, axentra, tabrion-studio | Required |
| AC-2 | Manifest against policy and no cycles (`scripts/check_dependency_policy.py`); 8 negative cases, among them `tabrion` to a platform, the cycle axentra and tabrion, core instead of extra, a direct URL in a library (RC-1) | every Python repository | Required |
| AC-3 | Blocked-module tests per optional edge: the Tabrion core without psycopg, sqlalchemy and alembic; the Orkexa core without `tabrion` (until MIG-ORK-3) and without psycopg; the runtime core without axentra and `tabrion.postgres`; api desktop without axentra and psycopg | tabrion, orkexa, runtime, api | Required |
| AC-4 | Clean-install matrix per plan document 00 section 13: tabrion per extras combination, axentra standalone (through `requirements/tabrion.txt`), orkexa core, orkexa[postgres], axentra with orkexa, runtime core (Desktop, Local Docker), runtime[postgres], api[server], tabrion-studio; negative cases two pins, a pre-release against a final range, a 0.(N+1) alpha; pip 25.0 and current; `pip check` empty, forbidden packages absent | tabrion, axentra, orkexa, runtime, api | Required on a manifest change, nightly otherwise |
| AC-5 | Consumer compat: Tabrion master against the suites of Axentra, Orkexa[postgres] and Nodestro (core and server, pg-a/pg-b, runtime role) | nodestro-studio `components-at-master`, nightly | Not blocking for consumers, **blocking for Tabrion tags** (two green runs) |
| AC-6 | **Exactly one Tabrion version per installation** and the pin cascade (`check_versions.py`): V-B0/V-B mean a non-empty range intersection, the application pin inside it, SHA runtime equal to api `dev`; V-A means an identical SHA everywhere; the graph acyclic and equal to the policy | nodestro-studio | Required |
| AC-7 | Public Tabrion API only (`scripts/check_tabrion_api.py`: no `tabrion._*`); the names in use are exported to `tabrion/docs/api-consumers.md` | tabrion-studio, axentra, orkexa, nodestro-runtime | Required |

Operating rule: a new edge follows the order ADR, owner approval, policy pull request in the integrator, manifest
pull request.

## Rationale

- The checks replace the blanket bans (plan document 00 section 9, lifted) with a positive list that satisfies P8.
- They reuse existing tools and are cheap.

## Rejected alternatives

| Alternative | Reason |
|---|---|
| Blanket import bans between the base platforms (22:26Z) | Lifted; they forbid wanted reuse |
| A review checklist only | Not reproducible |
| A central architecture repository | A component in stock |
| AC-5 blocking for consumers | Couples their CI to Tabrion master |

## Consequences

- Small extra pull requests without product code in orkexa and axentra (AC-1, AC-2, AC-7).
- AC-5 becomes a release gate for Tabrion (S-7).

## Security guarantees

- Unintended edges, cycles and duplicate Tabrion versions are caught before the merge, and so is the use of private
  Tabrion internals that the stability guarantee does not cover.

## Verification

- Every AC check has a negative test or a mutation probe that must be red (plan document 07 section 7).
- In this repository (M1): AC-1 through `lint-imports` and `tests/architecture/test_import_contracts.py` (one
  mutation probe per forbidden edge, each must name its broken contract); AC-3 through
  `tests/unit/test_import_isolation.py`, `tests/packaging/test_isolation_probe.py` and the CI job
  `core without extras`; the Tabrion part of AC-4 through `tests/packaging/test_extras_matrix.py`.

## Revision triggers

- A new platform.
- The merge of MIG-ORK-3 (edge orkexa to tabrion active) or the transition from V-B0 to V-B after G-R; then the
  status of the edges or the AC-6 variant changes.

## Changes from the plan

- The Tabrion contracts additionally forbid `levuvo_nextcloud_mcp` and `nodestro_client` and run an AST prefix scan
  for `nodestro*`, `orkexa*`, `axentra*`, `levuvo*` and `tabrion_studio*`, because import-linter cannot express a
  prefix.
