# Program status

The status of every work package of the Tabrion program: the backlog of planning document 09 (125 work packages,
M1-M8) and change request CR-DB of planning document 14 (26 work packages, M9, M10 and two in M4), 151 in total. QA-12
keeps this file current; every pull request that finishes a work package updates its row in the same pull request.

> **Last update:** 2026-11-08, G-T1 follow-up pull request [#2](https://github.com/peterkarlhuber/tabrion/pull/2) (branch `claude/project-thread-rdo6g3`), after the M1 pull
> request [#1](https://github.com/peterkarlhuber/tabrion/pull/1) merged (`b0309c5`).
> Detailed open work for this repository: [docs/TODO.md](../TODO.md). Risks: [risks.md](risks.md).

## Status values

- **Not started**: no work yet. The Definition of Ready (below) decides when it may start.
- **In progress**: work has started; the evidence column says what exists.
- **In review**: done by its author in an open pull request; becomes **Done** with the merge.
- **Done**: merged on master, acceptance criteria met with the evidence named.
- **Done: conditional**: done, but the result holds under a condition the evidence names.
- **Blocked**: names the blocker. **Dropped**: removed from the scope by a decision, which it names.

"Lane" is the owner lane of planning document 09 section 5 (Gov, Core, QA, JQ, Ref-Apps, Migration, Studio), plus
"CR-DB Nodestro" for the Nodestro work of change request CR-DB. Effort is in agent days (implementation + review) and
is an estimate. Status outside this repository (Axentra, Orkexa, Nodestro, tabrion-studio) is recorded here only when
the owning thread reports it.

## Milestones and gates

| Milestone | Content | Entry | Exit gate | Status |
|---|---|---|---|---|
| M1 | Foundation: repositories, skeleton, API freeze | G-A | G-T1 (TAB-002) | Done: conditional (#1 merged; G-T1 conditions below; GOV-2 and GOV-3 wait for owner actions) |
| M2 | Tabrion 0.1, internal alpha, reference applications; lane JQ in parallel | G-T1 | G-T2 (TAB-040) | Not started |
| M3 | Versioning, Axentra security evidence, waves W1 and W2, architecture checks | TAB-040 `v0.1.0` final (RC-8) | G-M per step | Not started |
| M4 | Tabrion 0.2 (Studio APIs); TAB-CR-1/2 from CR-DB | G-T2 | G-T2 for 0.2 (TAB-042); PyPI only after G-R | Not started |
| M5 | Waves W3-W6 | M3 | G-M per step | Not started |
| M6 | Studio MVP 0.1 | GOV-3, TAB-002 (skeleton), TAB-042 (main part) | G-S1 (STU-016) | Not started |
| M7 | Studio 1.0 without the data editor | G-S1 | G-R for a public release (STU-028) | Not started |
| M7b | Conditional data editor (STU-026) | G-S2 | outside the 1.0 acceptance | Not started |
| M8 | Program close | M5, M7 | QA-24 closing record, then M-REV six-month review | Not started |
| **M9** | **Nodestro database nodes and export (CR-DB)**: database connections, node `database`, agent database tools, export with drivers, E2E, security review | M4 (Tabrion 0.2 final), PIN-W6 (SQLite wave in Nodestro), JQ-3; its own lane in parallel to M6/M7 | Security review of the Nodestro database work before the write operations merge (NOD-CR-15); G-R for any release | Not started |
| **M10** | **Studio 1.1 schema designer (CR-DB)**: new-database wizards, designer, migration generation, role wizard | M7 (Studio 1.0) | **G-S3** (STU-CR-8) | Not started |

| Gate | Meaning | Status |
|---|---|---|
| G-A | Overall plan approval | Passed 2026-11-07 |
| G-0 | Name cleared, repositories may be created (after GOV-1) | Passed conditionally: the owner keeps the name and checks the registers before G-T2 (O-3) |
| G-T1 | API freeze of the Tabrion contract (TAB-002) | **Passed with conditions** 2026-11-08 (independent review, planning folder `reviews/g-t1-review.md`); conditions C-1..C-8 in [G-T1 conditions](#g-t1-conditions) |
| G-T2 | Tabrion release gate (0.1.0 by TAB-040, 0.2.0 by TAB-042) | Open; requires O-3 first |
| G-M | Merge gate per migration, JQ and pin step (rollback probe QA-14, AC checks) | Per step |
| G-S1 | Studio MVP security review (STU-016) | Open |
| G-S2 | Owner decision on Studio write functions (STU-026) | Open |
| G-S3 | Studio 1.1 security review and acceptance of the schema designer (STU-CR-8; defined by STU-CR-1) | Open (added with CR-DB) |
| G-CR | Inclusion of CR-DB and decisions E-CR-1..E-CR-8 | Inclusion approved 2026-11-08; the individual decisions E-CR-1..E-CR-7 are to be confirmed (O-6); E-CR-8 (M9, M10, TAB-CR-1/2 in 0.2) is in force |
| G-R | Owner approval per public release (PyPI, GitHub release, announcement); no standing approval (E-PyPI) | Open for every release |
| G-EM | Merge of the migration runners | Decided **no** (2026-11-07 23:26Z); MIG-AXE-7, S6, S7 dropped |

## Work packages

### M1: Foundation (repositories, skeleton, API freeze G-T1)

Planned effort: 12.5 + 3 agent days.

| WP | Title | Repositories | Depends on | Gate | Effort (impl + review) | Lane | Status | Evidence |
|---|---|---|---|---|---|---|---|---|
| GOV-1 | Name and trademark check | - | G-A | - | 0.5 + 0 | Gov | Done: conditional | [name-check.md](reports/name-check.md); owner keeps the name, manual register check before G-T2 (owner action O-3) |
| GOV-2 | Create the tabrion repository | tabrion | GOV-1 | G-0 | 0.5 + 0 | Gov | In progress | Repository public with MIT license; branch protection is owner action O-1; first CI run is the M1 pull request |
| GOV-3 | Create the tabrion-studio repository | tabrion-studio | GOV-1 | G-0 | 0.5 + 0 | Gov | In progress | Repository `peterkarlhuber/tabrion-studio` exists with the MIT license only; branch protection and CI follow there (STU-001, STU-018) |
| QA-12 | Risk register and STATUS.md | tabrion | GOV-2 | - | 0.5 + 0 | QA | Done | [risks.md](risks.md), this file |
| QA-16 | AC-1: import-linter per edge list K-33 with a mutation probe | tabrion, orkexa, axentra, tabrion-studio | GOV-2 | - | 1 + 0.25 | QA | Done (tabrion part) | `pyproject.toml` `[tool.importlinter]` (six contracts since G-T1 C-5), `tests/architecture/test_import_contracts.py`; the orkexa, axentra and tabrion-studio parts are open |
| TAB-001 | English specification in the repository | tabrion | GOV-2 | - | 1 + 0.25 | Core | Done | [specification.md](../specification.md) |
| TAB-003 | Package skeleton, pyproject, lint, import-linter | tabrion | GOV-2 | - | 1 + 0.25 | Core | Done | `pyproject.toml`, `src/tabrion/`; CI job `lint` |
| TAB-007 | ADRs TAB-ADR-0001..0010 in English | tabrion | GOV-2 | - | 0.5 + 0 | Core | Done | [docs/adr/](../adr/README.md) 0001-0013 |
| TAB-002 | API contract and stubs, G-T1 | tabrion | TAB-003, TAB-001 | G-T1 | 1.5 + 1 | Core | Done: conditional | G-T1 passed with conditions; [contract.md](../api/contract.md) records the decisions G-1..G-10; `tests/unit/test_public_api.py`, `docs/api/surface.json`. Conditions C-1..C-4 in review (follow-up pull request) |
| TAB-004 | CI ci/ci-ok/nightly/release (includes QA-1) | tabrion | TAB-003 | - | 2.5 + 0.5 | Core | Done: conditional | `.github/workflows/`, `.github/ci-manifest.json`, `tests/security/test_workflow_pins.py` |
| TAB-006 | Docs skeleton (MkDocs), README, CONTRIBUTING, SECURITY | tabrion | TAB-003 | - | 1 + 0.25 | Core | Done (logo in review) | `mkdocs.yml`, CI job `docs`; README, CONTRIBUTING, SECURITY, SUPPORT, CODE_OF_CONDUCT. Logo (follow-up pull request): `assets/branding/` derived from the master of 2026-11-08 with the shared brand kit; lockups in the README (`<picture>`) and on the docs site, docs header logo and favicon; social preview (upload: O-9) |
| TAB-023 | API surface check | tabrion | TAB-002 | - | 0.5 + 0 | Core | In review | `scripts/api_surface.py`, [surface.json](../api/surface.json) (`since` and `stability` per name), CI job `api surface`, `tests/unit/test_api_surface.py` (G-T1 condition C-6) |
| TAB-005 | Import isolation and extras matrix (includes QA-5) | tabrion | TAB-004 | - | 1.5 + 0.5 | Core | Done | `tests/packaging/`, `tests/unit/test_import_isolation.py`; CI jobs `extras matrix` and `core without extras` |

### M2: Tabrion 0.1 with locks, roles and grants, internal alpha `v0.1.0a1`, reference applications (G-T2); parallel lane JQ

Planned effort: 60.75 + 21.25 agent days.

| WP | Title | Repositories | Depends on | Gate | Effort (impl + review) | Lane | Status | Evidence |
|---|---|---|---|---|---|---|---|---|
| JQ-1 | Fault-injection fixture and regression tests (9 places) | nodestro-runtime | G-A | G-M | 1.5 + 0.5 | JQ | Not started |  |
| JQ-2 | Impact analysis including the export | nodestro-runtime, nodestro-api | JQ-1 | G-M | 1 + 0.5 | JQ | Not started |  |
| JQ-3 | Fix option A and allowlist guard | nodestro-runtime | JQ-2 | G-M | 1.5 + 1 | JQ | Not started |  |
| MIG-AXE-0 | Local B-06-1 fix in Axentra right after G-A, independent of Tabrion (replaced later by MIG-AXE-3) | axentra | G-A | G-M | 1 + 0.5 | JQ | Not started |  |
| TAB-010 | tabrion.errors | tabrion | TAB-002 | - | 2 + 1 | Core | Not started |  |
| TAB-011 | tabrion.sql split_statements | tabrion | TAB-002 | - | 1 + 0.25 | Core | Not started |  |
| TAB-012 | tabrion.config safe_url/url_from_env | tabrion | TAB-002 | - | 0.5 + 0 | Core | Not started |  |
| QA-8 | Supply chain (pip-audit, npm audit, SBOM) | tabrion, tabrion-studio | TAB-004 | - | 1 + 0.25 | QA | Not started |  |
| TAB-051 | API stability policy and deprecation helper | tabrion | TAB-023 | - | 1 + 0.25 | Core | Not started |  |
| JQ-4 | Docs, CHANGELOG, export adjustment, pin wave W7 (JQ) | runtime, api, studio | JQ-3 | G-M | 1 + 0.5 | JQ | Not started |  |
| QA-11 | Release workflow without publishing | tabrion | TAB-005 | - | 1 + 0.25 | QA | Not started |  |
| TAB-013 | tabrion.sqlite connection and profile | tabrion | TAB-010 | - | 2 + 1 | Core | Not started |  |
| TAB-015 | tabrion.postgres configure, ping, diagnose, extension_status | tabrion | TAB-010, TAB-012 | - | 2 + 0.5 | Core | Not started |  |
| TAB-038 | Internal pre-release v0.1.0a1 (errors, sql, config) for unmerged integration branches (RC-8) | tabrion | TAB-010, TAB-011, TAB-012, TAB-005 | - | 0.25 + 0.25 | Core | Not started |  |
| QA-22 | Conventions audit of the new repositories | tabrion, tabrion-studio | TAB-004, STU-018 | - | 0.5 + 0 | QA | Not started |  |
| TAB-016 | tabrion.postgres reading roles | tabrion | TAB-015 | - | 1.5 + 0.75 | Core | Not started |  |
| TAB-030 | SessionLock and advisory lock helpers (0.1) | tabrion | TAB-015 | - | 2 + 1 | Core | Not started |  |
| TAB-034 | Fault-injection proxy tabrion.testing.faults (includes QA-3) | tabrion | TAB-015 | - | 1.5 + 0.5 | Core | Not started |  |
| TAB-014 | tabrion.sqlite transactions, migrate, backup, checks | tabrion | TAB-013 | - | 3 + 1.5 | Core | Not started |  |
| TAB-020 | tabrion.testing 0.1 | tabrion | TAB-015, TAB-013 | - | 2 + 0.5 | Core | Not started |  |
| TAB-031 | grant_runtime_role unifying Nodestro and Axentra (0.1) | tabrion | TAB-016 | - | 2 + 1 | Core | Not started |  |
| TAB-032 | testing: database per session, roles without superuser, truncate, runtime-role mode (0.1) | tabrion | TAB-020 | - | 2 + 0.5 | Core | Not started |  |
| QA-4 | Concurrency and stress suite | tabrion | TAB-014, TAB-015 | - | 1 + 0.25 | QA | Not started |  |
| RA-B-1 | Measurements: local capture on SQLite | tabrion (examples/measurements) | TAB-014 | - | 1.5 + 0.5 | Ref-Apps | Not started |  |
| TAB-017 | tabrion.check and finding codes | tabrion | TAB-014, TAB-016 | - | 1.5 + 0.5 | Core | Not started |  |
| TAB-018 | Layer B core (Database, Profile, Transaction, fetch_*) | tabrion | TAB-014, TAB-015 | - | 4 + 1 | Core | Not started |  |
| TAB-022 | Consumer protection: golden files (blocking) and nightly downstream | tabrion | TAB-014, TAB-016, TAB-030 | - | 3 + 0.75 | Core | Not started |  |
| TAB-050 | Internal test asset "Levuvo Database Profile" (vectors, no vendoring) | tabrion | TAB-010, TAB-011, TAB-014, TAB-016 | - | 2 + 0.5 | Core | Not started |  |
| QA-2 | Canary value suite (no values in errors or logs) | tabrion | TAB-010, TAB-018 | - | 1 + 0.5 | QA | Not started |  |
| RA-B-2 | Measurements: idempotent transfer to PostgreSQL | tabrion (examples/measurements) | RA-B-1, TAB-018 | - | 2 + 0.5 | Ref-Apps | Not started |  |
| TAB-019 | tabrion.migrations status/require_current | tabrion | TAB-018 | - | 1 + 0.25 | Core | Not started |  |
| TAB-021 | Profile and portability test cases | tabrion | TAB-018 | - | 1 + 0.25 | Core | Not started |  |
| RA-A-1 | Parts: portable schema and Alembic | tabrion (examples/parts) | TAB-019 | - | 1.5 + 0.5 | Ref-Apps | Not started |  |
| TAB-041 | Complete 0.1 documentation (guides) | tabrion | TAB-019, TAB-020, TAB-017 | - | 3 + 0.75 | Core | Not started |  |
| RA-B-3 | Measurements: offline and failure tests | tabrion (examples/measurements) | RA-B-2, TAB-034 | - | 1.5 + 0.5 | Ref-Apps | Not started |  |
| RA-B-4 | Measurements: plain SQLAlchemy baseline | tabrion (examples/measurements-baseline) | RA-B-2 | - | 1 + 0.25 | Ref-Apps | Not started |  |
| RA-A-2 | Parts: functions and tests on SQLite and PostgreSQL | tabrion (examples/parts) | RA-A-1 | - | 2 + 0.5 | Ref-Apps | Not started |  |
| RA-A-3 | Parts: plain SQLAlchemy baseline | tabrion (examples/parts-baseline) | RA-A-2 | - | 1 + 0.25 | Ref-Apps | Not started |  |
| TAB-040 | Release 0.1.0 (tag), G-T2 | tabrion | TAB-041, TAB-021, TAB-022, TAB-030, TAB-031, TAB-032, RA-A-2, RA-B-3, QA-2, QA-11, TAB-034, TAB-050, TAB-051 | G-T2 | 0.5 + 0.5 | Core | Not started |  |
| RA-EVAL | Measurement report: value over SQLAlchemy | tabrion | RA-A-3, RA-B-3, RA-B-4 | - | 1 + 0.5 | Ref-Apps | Not started |  |

### M3: Versioning (VER-1), Axentra security evidence, waves W1 (S1 fixtures) and W2 (S2a errors), architecture checks

Planned effort: 34.3 + 8.35 agent days.

| WP | Title | Repositories | Depends on | Gate | Effort (impl + review) | Lane | Status | Evidence |
|---|---|---|---|---|---|---|---|---|
| QA-17 | AC-2: dependency-policy.json and check_dependency_policy.py (8 negative cases, cycles) | all Python repositories, nodestro-studio | QA-16 | - | 2 + 0.5 | QA | Not started |  |
| QA-21 | AC-7: public API check and export of api-consumers.md | tabrion-studio, axentra, orkexa, nodestro-runtime | TAB-002 | - | 1 + 0.25 | QA | Not started |  |
| QA-14 | Rollback probe per MIG and JQ step | all affected | JQ-3 | - | 6 + 0 | QA | Not started |  |
| QA-18 | AC-3: blocked-module test per optional edge | tabrion, orkexa, nodestro-runtime, nodestro-api | TAB-005 | - | 1.5 + 0.5 | QA | Not started |  |
| QA-13 | Snapshot tools for rollback evidence | nodestro-runtime, axentra | TAB-038 | - | 2 + 0.5 | QA | Not started |  |
| VER-1 | Versioning infrastructure V-B0 (RC-1/RC-2/RC-8, requirements/tabrion.txt, drift nightly) | nodestro-studio, orkexa, axentra, nodestro-runtime | TAB-040 | - | 1.5 + 0.5 | Migration | Not started |  |
| MIG-NOD-3 | Error texts SQLite and PostgreSQL (B-06-2) and filter (S2a) | nodestro-runtime, nodestro-api | TAB-040, JQ-4, VER-1 | G-M | 1.5 + 0.5 | Migration | Not started |  |
| QA-15 | AC-6: check_versions.py: one Tabrion version, cascade, cycles, policy | nodestro-studio | QA-17, VER-1 | - | 2 + 0.5 | QA | Not started |  |
| QA-25 | Security evidence for the Axentra adoption (MIG-T-AXE-1..9, snapshots of the old code) | axentra | QA-13 | - | 3 + 1 | QA | Not started |  |
| MIG-AXE-3 | B-06-1: safe logging through tabrion.errors and filter (S2a); replaces MIG-AXE-0 | axentra | QA-25, TAB-040, VER-1, MIG-AXE-0 | G-M | 1.5 + 0.75 | Migration | Not started |  |
| MIG-AXE-1 | Axentra test fixtures through tabrion.testing (S1) | axentra | QA-25, TAB-040, VER-1 | G-M | 1.5 + 0.5 | Migration | Not started |  |
| MIG-NOD-1 | PostgreSQL test fixtures through tabrion.testing (S1) | nodestro-runtime, nodestro-api | TAB-040, JQ-4, VER-1 | G-M | 2 + 0.5 | Migration | Not started |  |
| MIG-ORK-1 | PostgreSQL test support through tabrion[testing] (S1) | orkexa | TAB-040, VER-1 | G-M | 1 + 0.25 | Migration | Not started |  |
| PIN-W1 | Pin wave W1 (S1 fixtures) | tabrion to (orkexa ∥ axentra) to runtime to api to sdk to studio to builds | MIG-NOD-1, MIG-AXE-1, MIG-ORK-1, QA-15 | G-M | 0.9 + 0.3 | Migration | Not started |  |
| QA-7 | Direct consumer tests and docs/api-consumers.md | axentra, orkexa, nodestro-runtime | MIG-AXE-1, MIG-ORK-1, MIG-NOD-1 | - | 2 + 0.5 | QA | Not started |  |
| PIN-W2 | Pin wave W2 (S2a errors; first base edge axentra to tabrion) | tabrion to (orkexa ∥ axentra) to runtime to api to sdk to studio to builds | PIN-W1, MIG-AXE-3, MIG-NOD-3 | G-M | 0.9 + 0.3 | Migration | Not started |  |
| QA-19 | AC-4: clean-install matrix (plan document 00 section 13) | tabrion, axentra, orkexa, nodestro-runtime, nodestro-api, tabrion-studio | PIN-W1 | - | 2 + 0.5 | QA | Not started |  |
| QA-20 | AC-5: consumer-compat workflow against Tabrion master (gate for Tabrion tags) | nodestro-studio | QA-7 | - | 2 + 0.5 | QA | Not started |  |

### M4: Tabrion 0.2 (Studio APIs), PyPI path only after G-R

Planned effort: 4.5 + 1.75 agent days; with CR-DB also TAB-CR-1 and TAB-CR-2 (3.5 + 1 agent days, listed in the CR-DB table below).

| WP | Title | Repositories | Depends on | Gate | Effort (impl + review) | Lane | Status | Evidence |
|---|---|---|---|---|---|---|---|---|
| TAB-033 | 0.2: query/Result, stream, cancel, inspector (Studio APIs) | tabrion | TAB-040 | - | 3 + 0.75 | Core | Not started |  |
| TAB-043 | PyPI path V-B (bound to G-R): trusted publisher, 0.1.0rc1 | tabrion | TAB-040, GOV-1 | G-R | 0.5 + 0.5 | Core | Not started |  |
| TAB-042 | Release 0.2.0 (Studio APIs) and 0.2 documentation | tabrion | TAB-033 | G-T2 | 1 + 0.5 | Core | Not started |  |

### M5: Waves W3-W6 (S2b session and locks, S2c, S3 roles, S4 SQLite including Desktop); G-EM = no

Planned effort: 18.85 + 9.2 agent days.

| WP | Title | Repositories | Depends on | Gate | Effort (impl + review) | Lane | Status | Evidence |
|---|---|---|---|---|---|---|---|---|
| MIG-AXE-2 | Splitter to tabrion.sql (S2c) | axentra | QA-25, TAB-040, PIN-W2 | G-M | 0.5 + 0.25 | Migration | Not started |  |
| MIG-AXE-4 | _configure through tabrion.postgres.configure (S2b) | axentra | QA-25, TAB-040, PIN-W2 | G-M | 0.75 + 0.25 | Migration | Not started |  |
| MIG-AXE-5 | Leader lock to SessionLock, migration lock to wait_advisory_lock (S2b) | axentra | QA-25, TAB-040, PIN-W2 | G-M | 1 + 0.5 | Migration | Not started |  |
| MIG-NOD-4 | SessionLock and configure, including -8 (S2b) | nodestro-runtime | TAB-040, PIN-W2 | G-M | 1.5 + 0.5 | Migration | Not started |  |
| MIG-NOD-7 | pgvector_status through extension_status (S2c) | nodestro-runtime | TAB-040, PIN-W2 | G-M | 0.5 + 0.25 | Migration | Not started |  |
| MIG-ORK-2 | PsycopgSource: configure, ping, error texts through tabrion[postgres] (S2b) | orkexa | TAB-040, PIN-W2 | G-M | 1.5 + 0.5 | Migration | Not started |  |
| PIN-W3 | Pin wave W3 (S2b session and locks) | tabrion to (orkexa ∥ axentra) to runtime to api to sdk to studio to builds | MIG-AXE-4, MIG-AXE-5, MIG-NOD-4, MIG-ORK-2 | G-M | 0.9 + 0.3 | Migration | Not started |  |
| PIN-W4 | Pin wave W4 (S2c; may coincide with W3) | tabrion to (orkexa ∥ axentra) to runtime to api to sdk to studio to builds | PIN-W3, MIG-AXE-2, MIG-NOD-7 | G-M | 0.9 + 0.3 | Migration | Not started |  |
| MIG-AXE-6 | Roles: roles.py as a shim, role_info through Tabrion (S3) | axentra | QA-25, TAB-040, PIN-W4 | G-M | 2 + 1.5 | Migration | Not started |  |
| MIG-NOD-5 | Roles through Tabrion, wrappers (S3) | nodestro-runtime, nodestro-api | PIN-W4 | G-M | 2 + 1.5 | Migration | Not started |  |
| PIN-W5 | Pin wave W5 (S3 roles, same Tabrion version) | tabrion to (orkexa ∥ axentra) to runtime to api to sdk to studio to builds | MIG-AXE-6, MIG-NOD-5 | G-M | 0.9 + 0.3 | Migration | Not started |  |
| MIG-NOD-6 | SQLiteStore on tabrion.sqlite; B-06-3, B-06-5 (S4) | nodestro-runtime, nodestro-api | PIN-W5, MIG-NOD-3 | G-M | 3 + 1.5 | Migration | Not started |  |
| MIG-ORK-3 | Core SQLite on tabrion (E-ORK-CORE = yes, S4) | orkexa | PIN-W5 | G-M | 2 + 1 | Migration | Not started |  |
| MIG-NOD-9 | E-NOD-PERM (yes): existing desktop database files to 0600 | nodestro-runtime | MIG-NOD-6 | G-M | 0.5 + 0.25 | Migration | Not started |  |
| PIN-W6 | Pin wave W6 (S4 SQLite: orkexa to runtime) | tabrion to (orkexa ∥ axentra) to runtime to api to sdk to studio to builds | MIG-NOD-6, MIG-ORK-3 | G-M | 0.9 + 0.3 | Migration | Not started |  |

### M6: Studio MVP 0.1 (G-S1)

Planned effort: 59 + 18 agent days.

| WP | Title | Repositories | Depends on | Gate | Effort (impl + review) | Lane | Status | Evidence |
|---|---|---|---|---|---|---|---|---|
| STU-001 | Studio project skeleton (without CI) | tabrion-studio | GOV-3, TAB-002 | - | 1.5 + 0.5 | Studio | Not started |  |
| QA-9 | E2E framework Playwright and axe | tabrion-studio | STU-001 | - | 1.5 + 0.5 | QA | Not started |  |
| STU-002 | CLI and process model | tabrion-studio | STU-001 | - | 2 + 0.5 | Studio | Not started |  |
| STU-005 | App shell, design system, i18n de/en | tabrion-studio | STU-001 | - | 4 + 1 | Studio | Not started |  |
| STU-018 | Studio CI (ci/ci-ok/nightly) and wheel build hook | tabrion-studio | STU-001 | - | 1.5 + 0.5 | Studio | Not started |  |
| STU-031 | License and supply chain check for Studio | tabrion-studio | STU-001 | - | 1 + 0.25 | Studio | Not started |  |
| STU-004 | Problem JSON and logging | tabrion-studio | STU-001, TAB-038 | - | 1.5 + 0.5 | Studio | Not started |  |
| STU-003 | Access protection (token, cookie, host/origin, CSP) | tabrion-studio | STU-002 | - | 2.5 + 1.25 | Studio | Not started |  |
| STU-006 | Configuration, profiles, project trust | tabrion-studio | STU-004 | - | 2.5 + 0.5 | Studio | Not started |  |
| STU-051 | Status displays and dialogs | tabrion-studio | STU-005 | - | 1.5 + 0.5 | Studio | Not started |  |
| STU-007 | Secret management | tabrion-studio | STU-006 | - | 1.5 + 0.75 | Studio | Not started |  |
| STU-009 | Connection registry and read-only enforcement | tabrion-studio | STU-007, TAB-040 | - | 2.5 + 1.25 | Studio | Not started |  |
| STU-008 | Connection manager UI and connection test | tabrion-studio | STU-005, STU-007, STU-009 | - | 3 + 0.75 | Studio | Not started |  |
| STU-014 | Diagnosis (MVP) | tabrion-studio | STU-009 | - | 3 + 0.75 | Studio | Not started |  |
| STU-015 | Migration status Alembic and SQLite | tabrion-studio | STU-009 | - | 2 + 0.5 | Studio | Not started |  |
| STU-010 | Explorer | tabrion-studio | STU-009, TAB-042 | - | 4 + 1 | Studio | Not started |  |
| STU-011 | Query engine (SSE, caps, cancel) | tabrion-studio | STU-009, TAB-042 | - | 4 + 2 | Studio | Not started |  |
| STU-012 | SQL editor UI (MVP) | tabrion-studio | STU-011 | - | 5 + 1.25 | Studio | Not started |  |
| STU-013 | Data viewer | tabrion-studio | STU-011 | - | 3 + 0.75 | Studio | Not started |  |
| STU-050 | UX acceptance MVP | tabrion-studio | STU-008, STU-010, STU-012, STU-013, STU-014, STU-015, STU-051, QA-9 | - | 2 + 0.5 | Studio | Not started |  |
| STU-052 | Keyboard and command palette | tabrion-studio | STU-012 | - | 2 + 0.5 | Studio | Not started |  |
| STU-053 | Accessibility audit MVP | tabrion-studio | STU-050 | - | 1.5 + 0.5 | Studio | Not started |  |
| STU-016 | MVP hardening and security review, G-S1 | tabrion-studio | STU-050, STU-003, STU-031, STU-018, STU-052, STU-053 | G-S1 | 3 + 1 | Studio | Not started |  |
| STU-017 | MVP documentation and tag 0.1.0 | tabrion-studio | STU-016 | - | 2 + 0.5 | Studio | Not started |  |
| QA-S1 | Usability walkthrough with Peter (MVP and 1.0) | - | STU-017 | - | 1 + 0 | QA | Not started |  |

### M7: Studio 1.0 (without the data editor; languages de/en)

Planned effort: 21 + 5.25 agent days.

| WP | Title | Repositories | Depends on | Gate | Effort (impl + review) | Lane | Status | Evidence |
|---|---|---|---|---|---|---|---|---|
| STU-020 | Favorites, history, scripts, autocomplete | tabrion-studio | STU-017 | - | 5 + 1.25 | Studio | Not started |  |
| STU-021 | Export CSV/JSON | tabrion-studio | STU-017 | - | 1.5 + 0.5 | Studio | Not started |  |
| STU-022 | DDL, policies, row estimates | tabrion-studio | STU-017 | - | 2 + 0.5 | Studio | Not started |  |
| STU-023 | ER diagram (xyflow and dagre) | tabrion-studio | STU-017 | - | 3 + 0.75 | Studio | Not started |  |
| STU-024 | Migration status of the Levuvo formats | tabrion-studio | STU-017 | - | 3 + 0.75 | Studio | Not started |  |
| STU-025 | Diagnosis report and backup hints | tabrion-studio | STU-017 | - | 1.5 + 0.5 | Studio | Not started |  |
| STU-054 | UX acceptance 1.0 (without data editor) | tabrion-studio | STU-020, STU-021, STU-022, STU-023, STU-024, STU-025 | - | 2 + 0.5 | Studio | Not started |  |
| STU-028 | 1.0 documentation, clean install, release preparation | tabrion-studio | STU-054 | G-R (release only) | 3 + 0.5 | Studio | Not started |  |

### M7b: Conditional package: data editor (only after G-S2, outside the 1.0 acceptance)

Planned effort: 5 + 2.5 agent days.

| WP | Title | Repositories | Depends on | Gate | Effort (impl + review) | Lane | Status | Evidence |
|---|---|---|---|---|---|---|---|---|
| STU-026 | Data editor for non-production (conditional, only after G-S2; not part of the 1.0 acceptance) | tabrion-studio | STU-017 | G-S2 | 5 + 2.5 | Studio | Not started |  |

### M8: Program close

Planned effort: 4 + 1.5 agent days.

| WP | Title | Repositories | Depends on | Gate | Effort (impl + review) | Lane | Status | Evidence |
|---|---|---|---|---|---|---|---|---|
| QA-23 | Ecosystem migration documentation (before/after, compatibility, what was not migrated) | tabrion | PIN-W6, QA-20 | - | 2 + 0.5 | QA | Not started |  |
| QA-24 | Final review and closing record | all | QA-23, STU-028, TAB-042, RA-EVAL, QA-S1, QA-22, QA-8, QA-14, QA-16, QA-17, QA-18, QA-19, QA-20, QA-21, QA-25, MIG-NOD-9 | - | 2 + 1 | QA | Not started |  |

### Change request CR-DB (planning document 14 section 8)

Added 2026-11-08 after the owner took CR-DB into the program. Milestones per planning document 14 section 9: TAB-CR-1 and TAB-CR-2 in Tabrion 0.2 (M4), the Nodestro work packages in M9, the Studio work packages in M10. ARC-CR-1 is not placed by the plan; it is listed under M9 because the Nodestro edge it records (E-CR-1) is needed first there. Planned effort: 80 + 27.25 agent days (architecture 1.5 + 0.5, Tabrion 3.5 + 1, Nodestro 47.5 + 16, Studio 1.1 27.5 + 9.75). Short forms in "Depends on" (`-4`) refer to the same prefix (NOD-CR-4).

| WP | Title | Repositories | Depends on | Milestone | Effort (impl + review) | Lane | Status | Evidence |
|---|---|---|---|---|---|---|---|---|
| ARC-CR-1 | K-33 addendum, AC-1/AC-4 extension, dependency policy, plan document 13 | all affected | G-CR, E-CR-1 | M9 | 1.5 + 0.5 | QA | Not started | |
| TAB-CR-1 | Tabrion connection profile format v1 (schema, parser, secret references, no storage) | tabrion | TAB-002 (G-T1) | M4 | 2 + 0.5 | Core | Not started | |
| TAB-CR-2 | Schema fingerprint and comparison through Database.inspector | tabrion | TAB-CR-1, Inspector (M4) | M4 | 1.5 + 0.5 | Core | Not started | |
| NOD-CR-1 | ADR and English specification of the database nodes and tools | runtime | G-CR | M9 | 1.5 + 0.5 | CR-DB Nodestro | Not started | |
| NOD-CR-2 | DatabaseConnection: secrets (SecretBox/Vault), write-only API, tenant binding, health, egress allowlist, block of the internal database | runtime, api | NOD-CR-1, Tabrion 0.2 final | M9 | 4 + 1.5 | CR-DB Nodestro | Not started | |
| NOD-CR-3 | Import of a Tabrion profile file | runtime, api | NOD-CR-2, TAB-CR-1 | M9 | 1 + 0.25 | CR-DB Nodestro | Not started | |
| NOD-CR-4 | Value reference resolver (field paths) | runtime | NOD-CR-1 | M9 | 1.5 + 0.5 | CR-DB Nodestro | Not started | |
| NOD-CR-5 | Node database: query/insert/update/upsert/batch, validation, transactions, idempotency, outcome_unknown, resume | runtime | NOD-CR-2, -4, TAB-CR-2, JQ-3 | M9 | 6 + 2 | CR-DB Nodestro | Not started | |
| NOD-CR-6 | delete with safeguards | runtime | NOD-CR-5 | M9 | 1.5 + 0.75 | CR-DB Nodestro | Not started | |
| NOD-CR-7 | Agent database tools (named operations, S-1, S-2, gate path) | runtime | NOD-CR-5 | M9 | 3 + 1.5 | CR-DB Nodestro | Not started | |
| NOD-CR-8 | API, OpenAPI, SDK | api, sdk | NOD-CR-5, -7 | M9 | 2.5 + 0.5 | CR-DB Nodestro | Not started | |
| NOD-CR-9 | Studio UI: connections, database inspector, mapping editor, palette, tool binding UI, i18n in 6 languages | studio | NOD-CR-8 | M9 | 8 + 2 | CR-DB Nodestro | Not started | |
| NOD-CR-10 | Export: release and manifest fields, closure, packages and extras, vendoring, check() rules, env templates, start checks, network needs, optional hash lock, format_version check | runtime, api | NOD-CR-5 | M9 | 6 + 2 | CR-DB Nodestro | Not started | |
| NOD-CR-11 | Portable: driver matrix, license aggregation and license CI | api | NOD-CR-10 | M9 | 2.5 + 0.75 | CR-DB Nodestro | Not started | |
| NOD-CR-12 | E2E invoice on PostgreSQL and SQLite (Nodestro, standard export, portable smoke, fault injection) | api, runtime, server | NOD-CR-9, -10, -11 | M9 | 4 + 1 | CR-DB Nodestro | Not started | |
| NOD-CR-13 | Backward compatibility (golden bundles, export without a database) | runtime, api | NOD-CR-10 | M9 | 1 + 0.25 | CR-DB Nodestro | Not started | |
| NOD-CR-14 | Docs in English and help in 6 languages, example, CHANGELOG, TODO | api, studio, examples | NOD-CR-12 | M9 | 3 + 0.5 | CR-DB Nodestro | Not started | |
| NOD-CR-15 | Independent security review and fixes | all Nodestro repositories | NOD-CR-12 | M9 | 2 + 2 | CR-DB Nodestro | Not started | |
| STU-CR-1 | Threat model, ADR, change of the non-goals, definition of G-S3 | tabrion-studio | G-CR, E-CR-2 | M10 | 1 + 0.5 | Studio | Not started | |
| STU-CR-2 | Wizard "New database" for SQLite | tabrion-studio | STU-CR-1, M7 | M10 | 2 + 0.5 | Studio | Not started | |
| STU-CR-3 | Wizard for PostgreSQL with guided setup | tabrion-studio | STU-CR-2 | M10 | 3 + 1 | Studio | Not started | |
| STU-CR-4 | Table, column and constraint designer | tabrion-studio | STU-CR-1 | M10 | 8 + 2 | Studio | Not started | |
| STU-CR-5 | Migration generation, DDL diff preview, execution, backup, destructive-change protection | tabrion-studio | STU-CR-4 | M10 | 6 + 2 | Studio | Not started | |
| STU-CR-6 | Role wizard and profile export | tabrion-studio | STU-CR-3, TAB-CR-1 | M10 | 2.5 + 1 | Studio | Not started | |
| STU-CR-7 | i18n de/en, docs, E2E | tabrion-studio | STU-CR-5, -6 | M10 | 3 + 0.75 | Studio | Not started | |
| STU-CR-8 | Security review G-S3 and fixes | tabrion-studio | STU-CR-7 | M10 | 2 + 2 | Studio | Not started | |

## M1 evidence

What the M1 pull request delivers against the acceptance criteria of planning document 09 (M1):

| WP | Acceptance criterion (plan document 09) | Evidence |
|---|---|---|
| GOV-1 | Report `docs/program/reports/name-check.md`; no identical hit in classes 9/42, else stop ST-10 | The report exists; it found identical unregistered prior use (R-36) and recommended HOLD. The owner decided to continue and to check the registers manually before G-T2 (O-3), so the status is "Done: conditional" |
| GOV-2 | Repository public, MIT, branch protection (required check `ci-ok`, no force push, no deletion) active; first CI run green | Public with MIT; the first CI run is the M1 pull request. Branch protection cannot be set by the agent (the agent proxy gets HTTP 403), so it is owner action O-1 |
| QA-12 | `docs/program/risks.md` equals plan document 11; `STATUS.md` with every WP ID of the backlog | [risks.md](risks.md) (all of plan document 11 plus R-36 and R-CR-1..6); this file lists all 125 backlog IDs and the 26 CR-DB IDs |
| QA-16 | AC-1: contracts per edge list K-33 (tabrion imports nothing from Levuvo); mutation probe red per repository | Four contracts in `pyproject.toml` (six after G-T1 C-5); `tests/architecture/test_import_contracts.py`: positive control, 13 mutation probes (18 after C-5) that must each name their broken contract, coverage of every module by the layers contract, AST prefix scan. The orkexa, axentra and tabrion-studio parts belong to their repositories |
| TAB-001 | `docs/specification.md` (English) covers plan document 02 chapters 1-11; the review agent finds no contradiction to plan document 04 | [specification.md](../specification.md), chapters 1-11 plus chapter 12 (stability policy) |
| TAB-003 | `pip install -e .[dev]` clean; ruff, mypy --strict, `lint-imports`, empty pytest suite green | CI job `lint` and the `test` matrix; the suite is not empty: it already holds the M1 contract, isolation, packaging, architecture and pin tests |
| TAB-007 | `docs/adr/0001..0010` in English, status "Accepted", content equals plan document 10 | [docs/adr/](../adr/README.md): 0001-0010 and 0011-0013 (also in plan document 10); differences only under "Changes from the plan" |
| TAB-002 | Contract and typed stubs, G-T1 | [contract.md](../api/contract.md); stubs in `src/tabrion/` raise `NotImplementedError` and are mypy-strict clean; `tests/unit/test_public_api.py` freezes `__all__` per module |
| TAB-004 | CI ci, ci-ok, nightly, release (with QA-1) | `.github/workflows/ci.yml`, `ci-ok.yml`, `nightly.yml`, `platforms.yml`, `release.yml`; `.github/ci-manifest.json` checked by `ci_ok.py --self-check`; actions pinned by commit SHA and images by digest (`tests/security/test_workflow_pins.py`, TAB-T-181); `release.yml` runs only by manual dispatch and its publish job uploads nothing (G-R). Conditional: `ci-ok` gates only once O-1 requires its commit status (C-7) |
| TAB-006 | MkDocs skeleton, README, CONTRIBUTING, SECURITY | `mkdocs.yml` with `strict: true`, CI job `docs` (`mkdocs build --strict`); README, CONTRIBUTING, SECURITY, SUPPORT, CODE_OF_CONDUCT, CHANGELOG |
| TAB-005 | Import isolation and extras matrix (with QA-5): every combination installs and imports; `import tabrion.sqlite` without psycopg and SQLAlchemy; negative probe red once | `tests/packaging/test_extras_matrix.py` (8 combinations in fresh virtual environments from the built wheel), `tests/unit/test_import_isolation.py`, `tests/packaging/test_isolation_probe.py` (an injected `import psycopg` must fail), CI jobs `extras matrix` and `core without extras` |

## G-T1 conditions

The independent G-T1 review (2026-11-08, planning folder `reviews/g-t1-review.md`) passed the API freeze with eight
conditions. The follow-up pull request [#2](https://github.com/peterkarlhuber/tabrion/pull/2) closes C-1..C-6 and the repository
part of C-7 and C-8; the rest is owner action.

| # | Condition (finding) | Decision and evidence | Status |
|---|---|---|---|
| C-1 | `tabrion.check` must reach `tabrion.postgres` and layer B without breaking the contracts (F-01) | Fix B: collectors `tabrion.postgres._check` and `tabrion._sa._check`, loaded by name from a fixed table keyed on the target's type module; contracts unchanged, no `ignore_imports`. Contract section 7, ADR 0012, `pyproject.toml` comment; tests `test_check_imports_only_the_standard_library`, `test_check_dispatches_*`, probe "`import tabrion.postgres` in `check.py`" stays | In review |
| C-2 | G-3 cannot carry the planned cases (F-02) | G-3 revised (D-10 reverted): `SQLiteConnect = Callable[[Path], sqlite3.Connection]`, `ProfileCase.check(connect, directory)`, `PortabilityCase.check(Database)`; contract section 6 | In review |
| C-3 | `Database.health(deep=...)` (F-03) | G-9: `health(self, *, timeout=5.0, deep=False)`, as doc 04 section 5.5 intends (`quick_check` only on request); contract section 5.5, specification section 10 | In review |
| C-4 | How a consumer passes `RoleSetup` (F-04) | G-10: overridable session fixture `tabrion_role_setup() -> RoleSetup \| None`, requested by `tabrion_postgres_database`; `tests/unit/test_testing_fixtures.py` (pytester) | In review |
| C-5 | Contracts for `_sa` and `migrations` (F-05) | Contracts "Layer B needs only SQLAlchemy" and "Migration helpers never import pytest", five new mutation probes | In review |
| C-6 | TAB-023 before the first M2 implementation pull request (F-08) | `scripts/api_surface.py`, `docs/api/surface.json` with `since` and `stability`; 0.2 names (`Result`, `Column`, `query`, `stream`, `cancel`, `inspector`, `run_transaction`, `on_begin`, `savepoint`) are not exported in 0.1 and are staged in `tabrion._sa._v02`; CI job `api surface` | In review |
| C-7 | `ci-ok` cannot be a required PR check; `master` unprotected (F-06) | `ci-ok.yml` posts the commit status `ci-ok` on the PR head SHA (`ci_ok_status.py`, `statuses: write`, `CI_OK_ENFORCE=1`); ADR 0012 "Merge gate". From the Studio CI review: a PR that changes `.github/workflows/**` or `.github/scripts/**` is NOT OK without the label `workflow-change-reviewed` (F-03), and the status post is retried and fails loudly (F-11). The condition check confirmed C-1..C-6 closed and C-7/C-8 down to owner settings. Takes effect once on `master`; then owner action O-1 | In review; O-1 open |
| C-8 | Environment `pypi` and the OIDC token (F-07) | Job `attest` alone holds `id-token: write`; ADR 0009 "Release environment" lists the settings; owner action O-2 | In review; O-2 open |

### Notes from the G-T1 review (Low and Info findings)

Recorded here as the review asks; none blocks M2. Each is picked up by the work package named.

| Finding | Note | Where it goes |
|---|---|---|
| F-09 | "stdlib-only" is a denylist: any other third-party import (for example `pluggy`, which comes with pytest) passes. An AST test should allow only `sys.stdlib_module_names` and the core modules in `errors`, `sql`, `config`, `sqlite`, `check`. `check.py` already has such a test (C-1) | TAB-010..TAB-014 (first implementation PR), TODO TB-019 |
| F-10 | The root package `tabrion` is in no contract; `from tabrion import Database` inside a core function goes through `__getattr__` to layer B unseen. Extend the AST test of F-09: no `import tabrion` or `from tabrion import` outside `tabrion/__init__.py` | with F-09, TB-019 |
| F-11 | `Statement` and `Params` appear in public layer-B signatures but are not exported from `tabrion` (G-5 rule). Decide: export them as `tabrion.Statement`/`tabrion.Params`, or document them as documentation-only aliases and drop them from `_sa.database.__all__` | TAB-018, TB-020 |
| F-12 | `DatabaseError.kind` over `classification: Classification \| None`: make `classification` required for `DatabaseError` or return `Kind.UNKNOWN`; plus `args = (code,)` and `__reduce__` for pickling (G-1 notes) | TAB-010 |
| F-13 | Specification section 10 omitted `Transaction.stream` and the CR-DB modules TAB-CR-1/2 in the 0.2 row | Fixed in the follow-up pull request; the TAB-CR-1/2 modules need a place in the layers contract when they arrive (TB-014, TB-015) |
| F-14 | `Database` has no `__init__`, so `Database()` builds an unusable object | TAB-018: a private keyword-only constructor or an `__init__` that raises `TypeError` pointing to `from_url`/`from_env`/`from_engine` |
| F-15 | macOS and Windows run on pull requests only when `sqlite.py` or `tests/sqlite/**` change; `config.py`, `errors.py`, `testing/sqlite.py` and `pyproject.toml` also matter there | TB-021: add them to `platforms.yml` and the manifest's `path_filters` together (`--self-check` keeps them equal) |
| F-17 | Planning errata: doc 05 section 2.3 ("lazy import ... `allow_indirect_imports=false` + runtime test") is wrong, because import-linter records function-level imports (C-1 replaces it); doc 02 section 9 still writes `tabrion.check(target)` (G-6: the function is `tabrion.check.check`) | Planning errata, owner (O-8) |

### Notes from the G-T1 condition check (PR #2)

| Finding | Note | Status |
|---|---|---|
| CC-1 | `scripts/api_surface.py` counted only `__all__` as exported, so a 0.2 name reachable through `tabrion.__getattr__` passed | Fixed in #2: any successful attribute lookup counts (class members with inheritance); mutation tests "0.2 name importable through `__getattr__` only" and "0.2 mixin mixed in early" |
| CC-2 | `tabrion._sa._v02` imports `Isolation`, `Statement` and `Transaction` from `tabrion._sa.database`; in 0.2 `Database`/`Transaction` inherit from the `_v02` mixins, a circular import | Open, before TAB-033: move the aliases to `tabrion._sa._types` and type `fn` against a forward reference (TODO TB-024) |
| CC-3 | F-15 still open: macOS and Windows run on PRs only for SQLite paths, so they did not run on #2 | Open, TODO TB-021 |
| BR-1 | Nothing excluded the logo from the MIT license | Fixed in #2: `TRADEMARKS.md`, linked from the README and `assets/branding/README.md` |
| BR-2 | The committed master carried a C2PA manifest (`caBX`) and an `eXIf` chunk | Fixed in #2: the chunks are removed without touching the image data; `tests/unit/test_branding.py` pins the new sha256 and the sha256 of the supplied file's decompressed pixel data. The supplied file in the planning folder is unchanged. The designer's original and a vector master for the trademark filing stay with the owner (O-3) |
| BR-3 | The regenerate command was unpinned | Fixed in #2: `pillow==12.3.0`, `numpy==2.5.3` (the versions that produced the committed files) |
| BR-4 | Relative README image paths break on PyPI | Fixed in #2: absolute `raw.githubusercontent.com/.../master/...` URLs (they render once the logo is on `master`) |

## Owner actions

Actions only the owner can take. The agent cannot do them (missing rights) or must not (approval limits).

| # | Action | Needed for | Status |
|---|---|---|---|
| O-1 | **Corrected after G-T1 (C-7, F-06).** Protect `master` of `peterkarlhuber/tabrion` (the agent proxy gets HTTP 403 on the branch protection API): after the follow-up pull request is merged and `ci-ok` has posted its commit status on one pull request, require the status check `ci-ok` (source: GitHub Actions); "require branches to be up to date"; no force pushes, no deletion. Create the label `workflow-change-reviewed`; only the owner or an independent reviewer sets it, on pull requests that change `.github/workflows/**` or `.github/scripts/**` (ADR 0012, "Workflow-change gate"). Recommended, not required (solo owner): `CODEOWNERS` for `/.github/**`, and approval for outside collaborators' workflow runs | GOV-2, C-7 | Open |
| O-2 | Before any use of `release.yml` with `publish: true` (C-8, F-07): create the environment `pypi` with the owner as required reviewer, "prevent self-review" on, deployment branches and tags restricted to the tag pattern `v*` (no branch rule), no secrets; with TAB-043 bind the PyPI trusted publisher to workflow `release.yml` and environment `pypi` (ADR 0009, "Release environment") | G-R, TAB-043, C-8 | Open |
| O-3 | **Manual TM register check (TMview/EUIPO/DPMA/WIPO) before G-T2; rename if conflict.** Also assess the prior user of the name (R-36) | GOV-1, G-T2 | Open |
| O-4 | G-T1 review of [contract.md](../api/contract.md), including its open points | TAB-002, G-T1 | Done 2026-11-08: passed with conditions (C-1..C-8) |
| O-5 | Enable private vulnerability reporting in the repository settings (SECURITY.md points to it) | TAB-006 | Open |
| O-6 | Confirm the CR-DB decisions E-CR-1..E-CR-7 (planning document 14 section 9.1) | G-CR, ARC-CR-1, STU-CR-1 | Open |
| O-7 | The same branch protection as O-1 for `peterkarlhuber/tabrion-studio` | GOV-3 | Open |
| O-8 | Record the planning errata of F-17 (doc 05 section 2.3 lazy-import comment, doc 02 section 9 `tabrion.check(target)`) in the planning documents | G-T1 | Open |
| O-9 | Upload `assets/branding/social-preview.png` as the social preview in the repository settings (no API for it) | TAB-006 | Open |

## Deviation log

Deviations from the plan, with the reason. A deviation that changes a decision needs an ADR note under "Changes from
the plan"; the API deviations were open points of the G-T1 review, which decided them (contract, "Open points for G-T1").

| # | WP | Plan | Implemented | Reason |
|---|---|---|---|---|
| D-1 | TAB-005 | Seven extras combinations | Eight: the union of planning documents 05 and 07 (core, `postgres`, `sqlalchemy`, `migrations`, `testing`, `postgres,sqlalchemy`, `postgres,migrations,testing`, `all`) | The two documents list different sets; the union covers both |
| D-2 | TAB-004 | CI jobs `api surface`, `conformance`, `golden files`, `examples`; nightly fault injection, stress and downstream | Not yet in the workflows | Their content arrives with TAB-023, TAB-050, TAB-022, RA-*, TAB-034, QA-4; an empty job would be a check without content |
| D-3 | TAB-004 | Plan document 07 runs PostgreSQL 16 and 18 nightly only | The PostgreSQL matrix of plan document 05 (16, 17, 18, pgvector 17, Python 3.11 and 3.14) runs on every pull request | Plan document 05 is the more specific source; the jobs are short while the PostgreSQL suite is small. Revisit when the suite grows |
| D-4 | TAB-004 | Job names | Names follow plan document 05; the job `architecture` comes from plan document 07 | Two sources; both covered |
| D-5 | TAB-004, QA-11 | Release on a published GitHub release | `workflow_dispatch` only, plus the protected environment `pypi`; the publish job fails on purpose until TAB-043 | A GitHub release needs G-R itself; nothing may publish on its own |
| D-6 | TAB-002 | `tabrion.postgres` needs only psycopg | psycopg is imported for type checking only in M1 | `Profile` (layer B) holds `SessionSettings`; a SQLite-only application may install SQLAlchemy without psycopg |
| D-7 | TAB-002 | "`tabrion.check(target)`" | `tabrion.check` is a module; the function is `tabrion.check.check` | A module and a function of the same name cannot both be the attribute `tabrion.check` |
| D-8 | TAB-002 | `TabrionError` constructor not specified | A proposed constructor | Needed for typed stubs; accepted at G-T1 |
| D-9 | TAB-002 | `SQLiteProfile.allow_shared_file` only in prose | A field of `SQLiteProfile` | Needed for typed stubs; accepted at G-T1 |
| D-10 | TAB-002 | `ProfileCase` not specified | **Reverted after G-T1 (C-2).** The M1 `ConnectionFactory` is gone; `SQLiteConnect`, `ProfileCase` and `PortabilityCase` follow the review (contract G-3) | G-T1 rejected the zero-argument factory |
| D-11 | TAB-002 | `psycopg.Connection` | `psycopg.Connection[Any]` | mypy --strict requires the type parameter |
| D-12 | TAB-002 | `__all__` lists | `Migration` (`tabrion.sqlite`), `SQLiteConnect` and `PortabilityCase` (`tabrion.testing.profiles`) and the fixture `tabrion_role_setup` (`tabrion.testing`) are in `__all__` | They appear in public signatures or are the G-10 mechanism |
| D-13 | QA-16 | Layers of plan document 05 | `tabrion.migrations` added to the layers contract | Every module must sit in a layer, so a new module cannot slip past |
| D-14 | TAB-003 | Line length 110 (orkexa convention) | 120 | Long typed signatures of the contract; ruff format keeps it consistent |
| D-15 | TAB-004 | `ci-ok` as the required check on pull requests | **Corrected after G-T1 (C-7).** `ci-ok` runs from the default branch through `workflow_run` and posts its verdict as the commit status `ci-ok` on the PR head SHA, which branch protection requires (O-1) | The M1 reasoning was wrong: a `workflow_run` check run is attached to the default branch's commit, never to the PR head, so it could not be required at all |
| D-16 | TAB-001 | Plan document 05 places the specification under `docs/architecture/` | `docs/specification.md` | The acceptance criterion of TAB-001 names `docs/specification.md` |
| D-17 | TAB-017 | Plan document 05 section 2.3: `tabrion.check` imports `tabrion.postgres` and layer B lazily | Collector modules loaded by name (C-1, ADR 0012) | import-linter records function-level imports; the lazy import breaks two contracts (F-01, F-17) |
| D-18 | TAB-002, TAB-023 | Contract section 0 lists `Result`, `Column` and the 0.2 methods; the stubs had them | Not exported before 0.2; staged in `tabrion._sa._v02`, frozen in `surface.json` with `since: "0.2"` | G-T1 C-6: the 0.1.0 surface must not export a 0.2 name |
| D-19 | TAB-021 | Doc 04 section 6: `portability_cases() -> list[ProfileCase]` | `list[PortabilityCase]` | G-T1 C-2: a portability case needs a `Database` (both engines), a profile case a path |
| D-20 | TAB-004 | nodestro-studio design note: post the verdict as a check run (`checks: write`) | A commit status (`statuses: write`) | The G-T1 review (F-06, option b) and the G-T1 follow-up brief ask for a commit status; a check run from the workflow token comes from the same app and is no harder to forge (ADR 0012) |

## Score review

QA-12 reviews the risk scores at the end of every milestone.

| Milestone | Date | Result |
|---|---|---|
| M1 | 2026-11-08 | Scores of planning document 11 unchanged. R-13 updated with the GOV-1 result. New R-36 (prior use of the name by a third-party Excel/CSV app), score 12, owner Peter, countermeasure O-3. R-CR-1..R-CR-6 from CR-DB added unrated; rating due at the M4 review. Top risks: R-07 (16); R-01, R-12 (15); R-09, R-10, R-11, R-26, R-30, R-31, R-36 (12) |

## Definition of Ready and Definition of Done

From planning document 09 section 9.

**Definition of Ready (before a work package starts):**

1. Every predecessor work package is "Done" in this file.
2. The gate of the predecessor has passed (column "Gate").
3. The affected repositories are on the current master, and no open competing pull request touches the same files
   (coordination rule).
4. The acceptance criteria are measurable and the test IDs reserved.
5. The rollback path from planning document 11 is known (MIG, JQ, PIN).
6. For security-relevant work packages, the relevant TM, DI and SR IDs of planning document 07 are noted.

**Definition of Done:** planning document 07 section 6 (six mandatory points, review model) applies unchanged to
every work package. In addition, for MIG, JQ and PIN: the rollback probe QA-14 is documented in the pull request, and
QA-15 (AC-6, one Tabrion version) and QA-16..QA-18 (AC-1..AC-3, edge list K-33) are green; for MIG-AXE also QA-25
before and after the step.
