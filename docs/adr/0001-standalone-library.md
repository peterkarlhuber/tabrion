# ADR 0001: Tabrion is a standalone library, not a platform or a service

- **Status:** Accepted (2026-11-07, plan approval G-A). The name `tabrion` is conditional on GOV-1: the name check
  ([report](../program/reports/name-check.md)) is done with a conditional result, and the owner checks the trademark
  registers manually before G-T2. A conflict leads to a rename (decision E-Name).
- **Amendment 2026-11-08** (owner decision of 2026-11-08 11:26Z): the owner did not order the register check; it
  is no longer a prerequisite of G-T2. Tabrion is consumed through Git SHA pins without public marketing; a later
  public marketing under this name needs a new assessment first.
- **Plan reference:** TAB-ADR-0001 (planning document 10)

## Context

- The first assessment (`architekturbewertung-datenbankplattform.md`) recommended neither a fourth platform nor a
  central database service, but the enablement kit H1.
- The owner redefined "platform" for Tabrion: a library with its own repository, versioning, APIs, tests and
  documentation. Database access runs directly from the application through established drivers.
- Real duplication exists in role tooling, session locks, test fixtures, SQLite hardening and error texts
  (consolidation report chapter 3; plan document 06 section 0).
- New model-neutral applications (parts, measurements) need the same rules without depending on Axentra. Axentra's
  runner always loads Axentra's own schema (`axentra/src/axentra/data/migrations.py:354`), so it is not
  model-neutral.

## Decision

- `tabrion` is a Python library in its own repository `peterkarlhuber/tabrion` (MIT, public per E-Vis).
- It runs in the application's process. There is no server, no service, no central pool and no data held by Tabrion.
- Consolidation happens through code: Axentra, Nodestro and Orkexa (E-ORK-CORE = yes) use layer A
  ([ADR 0003](0003-existing-platforms-layer-a.md), [ADR 0013](0013-axentra-adopts-tabrion.md)). The "Levuvo Database
  Profile" with its vectors is only an internal Tabrion test asset ([ADR 0011](0011-ldp-internal-and-t-notx.md), K-35).
- The kit content from H1 (PostgreSQL baseline, role model, backup and PITR guide) lives as documentation in this
  repository (`docs/guides/infrastructure-baseline.md`), not as code.

## Rationale

- It is the smallest building block that removes the proven duplication and serves new applications.
- A library creates no operational responsibility; a service would need operations, high availability,
  authentication and money.

## Rejected alternatives

| Alternative | Reason |
|---|---|
| Fourth platform or database service (DBaaS-like) | Operations, cost, attack surface; no proven need (matrix in the first report: D 2.5-2.6 against H1 4.03) |
| Enablement kit only (documentation and templates, no code) | Does not remove the code duplication (roles, locks, fixtures, error texts); consolidation matrix H 3.95 against variants 1-5 at most 3.53 |
| Integrate into Axentra | Axentra is a SaaS foundation (identity, tenancy, RLS); new applications without a tenant model would inherit that dependency; its runner is not model-neutral (`migrations.py:354`) |
| Integrate into Orkexa | Orkexa is an AI framework and the lowest layer; database tooling for parts applications does not belong in an AI package; it would couple every Orkexa consumer |

## Consequences

- A new repository with its own CI and release cadence.
- New edges only per K-33 ([ADR 0008](0008-dependency-principles-and-edges.md)): axentra to `tabrion[postgres]`
  (required), nodestro-runtime to `tabrion` (base) and `tabrion[postgres]` (extra), `orkexa[postgres]` to
  `tabrion[postgres]`, the Orkexa core to `tabrion` (E-ORK-CORE = yes), tabrion-studio to `tabrion`. Libraries
  declare ranges; only applications pin the SHA (V-B0, [ADR 0009](0009-distribution-and-versioning.md)).
- Tabrion becomes part of a diamond (runtime to axentra to tabrion, and runtime to tabrion); exactly one version per
  installation (K-34, [ADR 0012](0012-architecture-checks.md)).
- Maintained by one maintainer (risk R-10).

## Security guarantees

- Tabrion opens no network ports and stores no credentials.
- Tabrion needs no superuser rights. The exception is `tabrion.testing` with `CREATEDB`/`CREATEROLE`
  (specification section 5.4).

## Verification

- The import-linter contract "Tabrion depends on no Levuvo platform" (`pyproject.toml`, QA-16).
- The reference applications RA-A and RA-B run without Orkexa, Nodestro and Axentra.

## Revision triggers

- The six-month review (T-E8): number of consumers and the share of direct engine use (R-T01).
- A proven need for central provisioning (then a separate ADR for the provisioning CLI H2, still no service).

## Changes from the plan

- The plan's contract name "Tabrion never imports a Levuvo product" is implemented as "Tabrion depends on no Levuvo
  platform" (the wording of P6). The rule is the same.
