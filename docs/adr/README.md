# Architecture decision records

Tabrion keeps its architecture decisions here as `NNNN-kebab-title.md`, following the scheme of nodestro-studio and
axentra. The records are the English versions of the plan's decision records (planning document 10, version 3).
A record may differ in content from the plan only when it says so under **Changes from the plan**.

Every record uses the same template: status, context, decision, rationale, rejected alternatives, consequences,
security guarantees, verification, revision triggers.

| ADR | Title | Status |
|---|---|---|
| [0001](0001-standalone-library.md) | Tabrion is a standalone library, not a platform or a service | Accepted (name conditional on GOV-1) |
| [0002](0002-two-layers-one-distribution.md) | Two layers in one distribution, extras, import isolation | Accepted |
| [0003](0003-existing-platforms-layer-a.md) | Existing platforms use layer A; no SQLAlchemy migration | Accepted |
| [0004](0004-error-model.md) | Error model: classify in layer A, raise sanitized errors in layer B | Accepted |
| [0005](0005-sqlite-profile.md) | SQLite profile | Accepted (E-T01) |
| [0006](0006-postgres-session-profile.md) | PostgreSQL session profile, R-SET, R-NORETRY | Accepted (E-T02) |
| [0007](0007-no-shared-migration-runner.md) | No PostgreSQL migration runner in Tabrion; existing runners are not merged | Accepted |
| [0008](0008-dependency-principles-and-edges.md) | Dependency principles P1-P8 and the allowed edge list K-33 | Accepted |
| [0009](0009-distribution-and-versioning.md) | Distribution and versioning under diamond dependencies | Accepted |
| [0010](0010-model-neutrality.md) | Model neutrality | Accepted |
| [0011](0011-ldp-internal-and-t-notx.md) | "Levuvo Database Profile" as an internal test asset; contract rule T-NOTX | Accepted |
| [0012](0012-architecture-checks.md) | Automated architecture checks AC-1..AC-7 against the edge list | Accepted |
| [0013](0013-axentra-adopts-tabrion.md) | Axentra adopts Tabrion with unchanged RLS, DbScope and M10 guarantees | Accepted |

## Status values

- **Proposed**: written, not yet accepted.
- **Accepted**: in force. All records here were accepted with the overall plan approval G-A (2026-11-07) and the
  owner decisions of 2026-11-07 23:26Z (E-VER, E-PyPI, E-Vis, E-ORK-CORE, E-NOD-PERM, E-06-2/G-EM, E-S1). Where a
  record depended on a decision (E-T01, E-T02), the recommended option was taken.
- **Superseded**: replaced by a later record, which it names.

## Adding a record

A new dependency edge, a change to a security rule or a change to the stability policy needs a record before the
code. Copy the template of an existing record, take the next number, add it to this table, and open the pull request
with the record and the change together. A new edge follows the order ADR, owner approval, policy pull request in the
integrator (`nodestro-studio/platform/dependency-policy.json`), manifest pull request.

The Studio records (STU-ADR-0001..0004) live in the tabrion-studio repository. Addenda to records in the existing
repositories (axentra 0003/0005/0009, nodestro-studio 0002/0004) are written there with the integration step that
needs them (plan document 10 section 15).
