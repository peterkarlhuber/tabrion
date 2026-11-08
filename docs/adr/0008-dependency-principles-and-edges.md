# ADR 0008: Dependency principles P1-P8 and the allowed edge list K-33; Tabrion depends on no Levuvo platform; no cycles

- **Status:** Accepted (2026-11-07; binding per plan document 00 sections 12.1 and 12.2). It replaces version 2
  ("independence of the base platforms") and version 1 ("Orkexa gets no Tabrion dependency"). A new edge needs an ADR
  and the owner's approval.
- **Plan reference:** TAB-ADR-0008 (planning document 10)

## Context

State on master:

- orkexa and axentra have no Levuvo dependency today. Axentra's import-linter contract forbids `nodestro*`, `orkexa`
  and `axentra_studio` (`axentra/pyproject.toml:137-141`).
- nodestro-runtime depends on orkexa in its core (`pyproject.toml:25`); its extra `postgres` contains
  `orkexa[postgres]`, `psycopg` and `axentra[oidc]`.
- nodestro-api has the extra `server` pointing to `runtime[postgres]`.
- The owner clarified at 22:52Z: "standalone" means a clearly bounded responsibility, not doing without other Levuvo
  libraries.

## Decision

**Principles (owner, binding):**

- P1: each platform has its own domain responsibility.
- P2: general technical functions live in specialized libraries.
- P3: reuse is wanted.
- P4: dependencies are meaningful in their domain and technically manageable.
- P5: **no cycles**.
- P6: **Tabrion depends on no Levuvo platform**.
- P7: security and compatibility guarantees stay.
- P8: required or optional according to the actual functional need; no blanket bans.

**Allowed edges (K-33, target state; the source of truth is `nodestro-studio/platform/dependency-policy.json`, and
each library carries a copy of its outgoing edges):**

| From | To | Kind | Status |
|---|---|---|---|
| tabrion | none | none | P6 |
| axentra | tabrion[postgres] | required (core) | active |
| orkexa `[postgres]` | tabrion[postgres] | optional (extra) | active |
| orkexa | tabrion | required (core, stdlib-only) | approved (E-ORK-CORE = yes, 23:26Z; active from MIG-ORK-3) |
| nodestro-runtime | orkexa | required | unchanged (existing) |
| nodestro-runtime `[postgres]` | orkexa[postgres], axentra[oidc], tabrion[postgres] | optional (extra) | axentra and orkexa existing, tabrion new |
| nodestro-runtime | tabrion | required (core, SQLite, MIG-NOD-6) | active per plan document 06 (K-32) |
| nodestro-api | nodestro-runtime, orkexa, nodestro-examples; `[server]` to nodestro-runtime[postgres]; `dev` to tabrion (same SHA as runtime, RC-2) | as today plus dev | existing plus new |
| nodestro-examples, -integrations, -sdk, -studio | as today | none | unchanged |
| tabrion-studio | tabrion[postgres,sqlalchemy,migrations] | required | active |
| dev extras of axentra, orkexa, nodestro-runtime | tabrion[testing,postgres] | dev | same version as the runtime edge |

**Forbidden:**

- tabrion to any Levuvo platform and to `tabrion_studio`;
- orkexa to axentra or nodestro*;
- axentra to orkexa or nodestro*;
- any library to its Studio;
- any cycle.

**Enforcement:** AC-1..AC-7 check exactly this edge list ([ADR 0012](0012-architecture-checks.md)). A new edge
follows the order ADR, owner approval, policy pull request in the integrator, manifest pull request.

## Rationale

- Generic database functions are maintained once (P2, P3).
- Cycles are structurally impossible, because Tabrion, as the lowest layer, has no outgoing edges (P5, P6).
- Domain boundaries hold, because the edges carry only technical helpers (P1,
  [ADR 0010](0010-model-neutrality.md)).
- The coupling cost stays manageable through ranges instead of SHA pins
  ([ADR 0009](0009-distribution-and-versioning.md)).

## Rejected alternatives

| Alternative | Reason |
|---|---|
| **Blanket independence rule of 22:26Z** (no runtime, extra or dev edges between orkexa, axentra and tabrion) | Lifted by the owner at 22:52Z; contradicts P3 and P8; security-relevant logic (roles and grants, file permissions, error sanitizing) would stay duplicated |
| **Integration through vectors only** (a standard plus vendored test vectors, parity in each platform's own code) | Two or three implementations of the same logic, only guarded against drift; three times the maintenance; findings (B-06-1, B-06-5) would have to be fixed several times; the vectors stay an internal test asset instead (K-35, [ADR 0011](0011-ldp-internal-and-t-notx.md)) |
| Optional integration through Nodestro only (version 2) | Axentra and Orkexa, used as standalone libraries, would not get the improvements |
| A shared "levuvo-db-common" package next to Tabrion | A second library with the same responsibility |
| An edge from tabrion to axentra or orkexa (for example for tenant helpers) | Violates P6 and P5 and model neutrality ([ADR 0010](0010-model-neutrality.md)) |

## Consequences

- Axentra and the Nodestro core get Tabrion as a required dependency; so does the Orkexa core (E-ORK-CORE = yes,
  from MIG-ORK-3).
- A diamond appears (runtime to orkexa, axentra and tabrion). [ADR 0009](0009-distribution-and-versioning.md) ensures
  exactly one Tabrion version per installation.
- Breaking changes in Tabrion 0.x hit several libraries. The stability policy (specification section 12) and the
  consumer-compat job (AC-5) apply.

## Security guarantees

- Tabrion cannot import platform code, neither tenant nor AI code (P6, AC-1).
- A flaw in Tabrion now affects several platforms. Countermeasures: the security patch path S-5, the downstream job
  and the review duty for security-relevant work packages (plan document 07 section 6).

## Verification

- AC-1: import-linter in each repository. In this repository: the contracts in `pyproject.toml`
  (`[tool.importlinter]`), the mutation probe and the prefix scan in `tests/architecture/test_import_contracts.py`.
- AC-2: manifest against policy, 8 negative cases.
- AC-3: blocked-module tests per optional edge (`tests/unit/test_import_isolation.py`,
  `tests/packaging/test_isolation_probe.py`).
- AC-4: the clean-install matrix of plan document 00 section 13 (9 combinations plus negative cases: two pins, a
  pre-release against a final range, a 0.(N+1) alpha; pip 25.0 and current).
- AC-6: no cycles and one Tabrion version.

## Revision triggers

- MIG-ORK-3 fails the conditions of plan document 06 section 3.2.
- A new platform.
- A need for an edge that is not on the list.

## Changes from the plan

- The plan places the edge list as data in `nodestro-studio/platform/dependency-policy.json` (QA-17). Until QA-17
  lands, this ADR and `pyproject.toml` are the record in this repository; Tabrion's own list of outgoing edges is
  empty.
