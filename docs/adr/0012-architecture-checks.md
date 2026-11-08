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

### `tabrion.check` and the import contracts (G-T1 condition C-1)

`tabrion.check` must reach the PostgreSQL checks and layer B, but it belongs to the standard-library core. import-linter
(grimp) also records imports inside functions, so the lazy import that plan document 05 section 2.3 describes would
break the core and the layers contract (G-T1 finding F-01). Decision (fix B): the collectors live in
`tabrion.postgres._check` and `tabrion._sa._check`; `tabrion.check` keeps a fixed table from the module of the
target's type to the collector module and loads it with `importlib.import_module`. The contracts stay unchanged and
use no `ignore_imports`. `tests/architecture/test_import_contracts.py` checks that `check.py` has no non-stdlib import
outside `TYPE_CHECKING` and that the table names exactly these two modules; the mutation probe "`import
tabrion.postgres` in `check.py` is BROKEN" stays.

### Merge gate (G-T1 condition C-7)

`ci-ok.yml` runs through `workflow_run`, so GitHub always takes it, `ci_ok.py` and the manifest from the default
branch, and a pull request cannot change its own gate. Its own check run is attached to the default branch's commit,
never to the pull request's head, so it cannot be a required check (G-T1 finding F-06). Decision: after each
evaluation, `.github/scripts/ci_ok_status.py` posts the verdict as the commit status `ci-ok` on the head SHA
(`statuses: write`): `success` for OK, `pending` while listed workflows run, `failure` for NOT OK, `error` when no
report exists. The evaluation runs with `CI_OK_ENFORCE=1`. Branch protection on `master` can require the status `ci-ok`
(owner action O-1, optional repository hardening by the owner decision of 2026-11-08 11:26Z; without it, a green
`ci-ok` is a merge rule that the person who merges checks). `ci_ok.py` stays byte-identical to the canonical copy in nodestro-studio; the status script is the
Tabrion addition.

**Workflow-change gate** (Studio CI review F-03). The mandatory jobs run from the pull request's own workflow files,
so a same-repository pull request could weaken them (for example a test step that cannot fail), or add a workflow
with `statuses: write` that forges `ci-ok`. Therefore `ci_ok_status.py` posts `failure` for every pull request that
changes `.github/workflows/**` or `.github/scripts/**`, with the message "needs label `workflow-change-reviewed`",
until that label is set. Rules:

- Only the owner, or an independent review (a reviewer who did not write the change), sets
  `workflow-change-reviewed`, and only after reading the workflow and script diff. The author of the change never
  sets it.
- A label does not trigger `workflow_run`; after setting it, re-run any job of the pull request's CI run so that ci-ok
  evaluates again.
- A pull request whose number cannot be determined (a fork run without a pull request list) fails closed.
- The gate takes effect once it is on `master`. The pull request that introduces it is judged by the previous gate;
  every later workflow-changing pull request needs the label.

The status post is retried with backoff (2, 4, 8, 16 s), and the job fails loudly when it never succeeds (F-11), so
a missing status is visible and the required check stays "expected", never green.

`CODEOWNERS` for `/.github/**` with "Require review from Code Owners" is **recommended, not required**: with a single
owner it would block every workflow change that the owner authors. The label is the required control.

Residual risk: a commit status can be posted by any workflow with `statuses: write`. The nodestro-studio design note
prefers a check run (`checks: write`); a check run created with the workflow token belongs to the GitHub Actions app
as well, so it does not close this path either. The workflow-change gate closes it for this repository's own pull
requests (adding such a workflow is a workflow change), and a unit test fails when any workflow other than
`ci-ok.yml` requests `statuses: write`.

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
- In this repository (M1): AC-1 through `lint-imports` and `tests/architecture/test_import_contracts.py` (six
  contracts, one mutation probe per forbidden edge, each must name its broken contract); AC-3 through
  `tests/unit/test_import_isolation.py`, `tests/packaging/test_isolation_probe.py` and the CI job
  `core without extras`; the Tabrion part of AC-4 through `tests/packaging/test_extras_matrix.py`.

## Revision triggers

- A new platform.
- The merge of MIG-ORK-3 (edge orkexa to tabrion active) or the transition from V-B0 to V-B after G-R; then the
  status of the edges or the AC-6 variant changes.

## Changes from the plan

- `tabrion.check` reaches `tabrion.postgres` and layer B through private collector modules loaded by name, not by a
  lazy import (plan document 05 section 2.3; G-T1 condition C-1, see "Decision").
- Two additional contracts from plan document 02 section 2: layer B needs only SQLAlchemy, and `tabrion.migrations`
  never imports pytest (G-T1 condition C-5).
- `ci-ok` posts a commit status on the pull request's head; the plan names `ci-ok` as the required check without
  saying how a `workflow_run` result reaches the pull request (G-T1 condition C-7).
- The Tabrion contracts additionally forbid `levuvo_nextcloud_mcp` and `nodestro_client` and run an AST prefix scan
  for `nodestro*`, `orkexa*`, `axentra*`, `levuvo*` and `tabrion_studio*`, because import-linter cannot express a
  prefix.
