# ADR 0010: Model neutrality

- **Status:** Accepted (2026-11-07; the owner's requirement of 2026-11-07 21:08Z).
- **Plan reference:** TAB-ADR-0010 (planning document 10)

## Context

- Use cases without a user, product or tenant model: parts, machines, measurements.
- Tenant logic exists on purpose in different forms:
  - Orkexa: the partition guard.
  - Nodestro: the tenant SQL checker without RLS (ADR-0010, `nodestro-studio/docs/adr/0010-nodestro-rls.md`).
  - Axentra: FORCE RLS (ADR 0005).

## Decision

- Tabrion knows no tenants, users, products, application role models, billing or AI functions.
- Tabrion defines no tables outside `tabrion.testing`.
- Role tooling works only with **database** roles (owner, runtime, backup).
- For tenants, product-owner models, application role models and AI, these parts stay in their platforms:
  - `tenant_sql_check`/`tenant_sql_allowlist` (Nodestro);
  - `orkexa.testing.sqlcheck` and the statement catalog and partition guard (Orkexa);
  - `DbScope`, RLS, `join`/`joined`, `Tx` (Axentra);
  - jobs, leader logic, identity, records.
- Extension points stay generic, for example `Database.on_begin(callback)` (experimental) for a consumer's
  transaction-local settings.

## Rationale

- A model-neutral library serves every use case and creates no dependency on SaaS concepts.
- Tenant isolation is security-critical and decided afresh in each platform.

## Rejected alternatives

| Alternative | Reason |
|---|---|
| Tenant scope as a Tabrion function | Would presuppose a tenant model; RLS responsibility would blur |
| Move `sqlcheck` or the tenant checker to Tabrion | Tenant-specific, specific to Nodestro or Orkexa |
| A pgvector index strategy in Tabrion | AI storage belongs to Orkexa; Tabrion only reports extension availability (`extension_status`) |

## Consequences

- Tabrion stays small.
- Studio shows Levuvo specifics only read-only and through copied fixtures (STU-024).

## Security guarantees

- Isolation (partition guard, checker, RLS) stays the platform's sole responsibility. Tabrion sets no
  security-relevant session settings (R-SET, [ADR 0006](0006-postgres-session-profile.md)).

## Verification

- import-linter: no import of a Levuvo platform.
- API surface check (TAB-023): no public name containing `tenant`, `user` or `product`. Until TAB-023 lands,
  `tests/unit/test_public_api.py::test_no_model_specific_names` checks the M1 surface.
- RA-A and RA-B run without these concepts.

## Revision triggers

- Several consumers provably need the same model-neutral helper for a tenant context. Then a new ADR examines only
  the mechanism, not the model.

## Changes from the plan

None.
