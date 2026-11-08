# Program status

The status of every work package of the Tabrion program: the backlog of planning document 09 (125 work packages,
M1-M8) and change request CR-DB of planning document 14 (26 work packages, M9, M10 and two in M4), 151 in total. QA-12
keeps this file current; every pull request that finishes a work package updates its row in the same pull request.

> **Last update:** 2026-11-08, governance simplification by the owner decision of 2026-11-08 11:26Z (Git-pin
> distribution, optional repository hardening, G-T2 without a trademark check; branch `claude/project-thread-rdo6g3`;
> [owner actions](#owner-actions), D-67). Before: fourth M2 lane C pull request [#11](https://github.com/peterkarlhuber/tabrion/pull/11) (TAB-020 with the review fixes, DL-1 of #8, TAB-032; branch
> `claude/project-thread-rdo6g3-pg4`, after #8 merged at `6e833f4`). Before: TAB-038 `v0.1.0a1` preparation (branch `claude/project-thread-rdo6g3-a1`, after #7
> merged at `c2711b5`). Before: TB-031 port to `tabrion.errors` (branch `claude/project-thread-rdo6g3-errfix`, after
> #6 merged). Before: second M2 lane C pull request [#6](https://github.com/peterkarlhuber/tabrion/pull/6) (TAB-030, TAB-034, reviews DL-1 and of #6; branch
> `claude/project-thread-rdo6g3-pg2`) after [#4](https://github.com/peterkarlhuber/tabrion/pull/4) merged (`af0f3dc`). Before: M2 lane B pull request [#4](https://github.com/peterkarlhuber/tabrion/pull/4) (TAB-013, TAB-014; branch `claude/project-thread-rdo6g3`), after
> the M2 lane A pull request [#3](https://github.com/peterkarlhuber/tabrion/pull/3) (TAB-010..TAB-012) merged (`6b99604`;
> independent review accepted at `ece7572`).
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
| M1 | Foundation: repositories, skeleton, API freeze | G-A | G-T1 (TAB-002) | Done: conditional (#1 merged; G-T1 conditions below; GOV-3 continues in tabrion-studio; branch protection is optional, owner decision of 2026-11-08 11:26Z) |
| M2 | Tabrion 0.1, internal alpha, reference applications; lane JQ in parallel | G-T1 | G-T2 (TAB-040) | In progress (TAB-010..TAB-012 done, [M2 evidence](#m2-evidence); TAB-013, TAB-014 in review, [lane B evidence](#m2-lane-b-evidence-tab-013-tab-014)) |
| M3 | Versioning, Axentra security evidence, waves W1 and W2, architecture checks | TAB-040 `v0.1.0` final (RC-8) | G-M per step | Not started |
| M4 | Tabrion 0.2 (Studio APIs); TAB-CR-1/2 from CR-DB | G-T2 | G-T2 for 0.2 (TAB-042); no PyPI publication planned, one only after G-R | Not started |
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
| G-0 | Name cleared, repositories may be created (after GOV-1) | Passed conditionally: the owner keeps the name. The register check (O-3) is not ordered and no prerequisite of G-T2 (owner decision of 2026-11-08 11:26Z); a later public marketing under the name needs a new assessment |
| G-T1 | API freeze of the Tabrion contract (TAB-002) | **Passed with conditions** 2026-11-08 (independent review, planning folder `reviews/g-t1-review.md`); conditions C-1..C-8 in [G-T1 conditions](#g-t1-conditions) |
| G-T2 | Tabrion release gate (0.1.0 by TAB-040, 0.2.0 by TAB-042) | Open; no trademark check required (owner decision of 2026-11-08 11:26Z) |
| G-M | Merge gate per migration, JQ and pin step (rollback probe QA-14, AC checks) | Per step |
| G-S1 | Studio MVP security review (STU-016) | Open |
| G-S2 | Owner decision on Studio write functions (STU-026) | Open |
| G-S3 | Studio 1.1 security review and acceptance of the schema designer (STU-CR-8; defined by STU-CR-1) | Open (added with CR-DB) |
| G-CR | Inclusion of CR-DB and decisions E-CR-1..E-CR-8 | Inclusion approved 2026-11-08; the individual decisions E-CR-1..E-CR-7 are to be confirmed (O-6); E-CR-8 (M9, M10, TAB-CR-1/2 in 0.2) is in force |
| G-R | Owner approval per public release (PyPI, npm, GitHub release, announcement); no standing approval (E-PyPI) | Open for every release |
| G-EM | Merge of the migration runners | Decided **no** (2026-11-07 23:26Z); MIG-AXE-7, S6, S7 dropped |

## Work packages

### M1: Foundation (repositories, skeleton, API freeze G-T1)

Planned effort: 12.5 + 3 agent days.

| WP | Title | Repositories | Depends on | Gate | Effort (impl + review) | Lane | Status | Evidence |
|---|---|---|---|---|---|---|---|---|
| GOV-1 | Name and trademark check | - | G-A | - | 0.5 + 0 | Gov | Done: conditional | [name-check.md](reports/name-check.md); owner keeps the name; the register check is not ordered and no prerequisite of G-T2 (O-3 dropped, owner decision of 2026-11-08 11:26Z); a later public marketing under the name needs a new assessment |
| GOV-2 | Create the tabrion repository | tabrion | GOV-1 | G-0 | 0.5 + 0 | Gov | Done | Repository public with MIT license; first CI run was the M1 pull request (#1); branch protection (O-1) is optional repository hardening (owner decision of 2026-11-08 11:26Z) |
| GOV-3 | Create the tabrion-studio repository | tabrion-studio | GOV-1 | G-0 | 0.5 + 0 | Gov | In progress | Repository `peterkarlhuber/tabrion-studio` exists with the MIT license only; CI follows there (STU-001, STU-018); branch protection is optional (O-7) |
| QA-12 | Risk register and STATUS.md | tabrion | GOV-2 | - | 0.5 + 0 | QA | Done | [risks.md](risks.md), this file |
| QA-16 | AC-1: import-linter per edge list K-33 with a mutation probe | tabrion, orkexa, axentra, tabrion-studio | GOV-2 | - | 1 + 0.25 | QA | Done (tabrion part) | `pyproject.toml` `[tool.importlinter]` (six contracts since G-T1 C-5), `tests/architecture/test_import_contracts.py`; the orkexa, axentra and tabrion-studio parts are open |
| TAB-001 | English specification in the repository | tabrion | GOV-2 | - | 1 + 0.25 | Core | Done | [specification.md](../specification.md) |
| TAB-003 | Package skeleton, pyproject, lint, import-linter | tabrion | GOV-2 | - | 1 + 0.25 | Core | Done | `pyproject.toml`, `src/tabrion/`; CI job `lint` |
| TAB-007 | ADRs TAB-ADR-0001..0010 in English | tabrion | GOV-2 | - | 0.5 + 0 | Core | Done | [docs/adr/](../adr/README.md) 0001-0013 |
| TAB-002 | API contract and stubs, G-T1 | tabrion | TAB-003, TAB-001 | G-T1 | 1.5 + 1 | Core | Done: conditional | G-T1 passed with conditions; [contract.md](../api/contract.md) records the decisions G-1..G-10; `tests/unit/test_public_api.py`, `docs/api/surface.json`. Conditions C-1..C-4 in review (follow-up pull request) |
| TAB-004 | CI ci/ci-ok/nightly/release (includes QA-1) | tabrion | TAB-003 | - | 2.5 + 0.5 | Core | Done: conditional | `.github/workflows/`, `.github/ci-manifest.json`, `tests/security/test_workflow_pins.py` |
| TAB-006 | Docs skeleton (MkDocs), README, CONTRIBUTING, SECURITY | tabrion | TAB-003 | - | 1 + 0.25 | Core | Done (logo in review) | `mkdocs.yml`, CI job `docs`; README, CONTRIBUTING, SECURITY, SUPPORT, CODE_OF_CONDUCT. Logo (follow-up pull request): `assets/branding/` derived from the master of 2026-11-08 with the shared brand kit; lockups in the README (`<picture>`) and on the docs site, docs header logo and favicon; social preview (upload: O-9) |
| TAB-023 | API surface check | tabrion | TAB-002 | - | 0.5 + 0 | Core | Done (#2, `c31529d`) | `scripts/api_surface.py`, [surface.json](../api/surface.json) (`since` and `stability` per name), CI job `api surface`, `tests/unit/test_api_surface.py` (G-T1 condition C-6) |
| TAB-005 | Import isolation and extras matrix (includes QA-5) | tabrion | TAB-004 | - | 1.5 + 0.5 | Core | Done | `tests/packaging/`, `tests/unit/test_import_isolation.py`; CI jobs `extras matrix` and `core without extras` |

### M2: Tabrion 0.1 with locks, roles and grants, internal alpha `v0.1.0a1`, reference applications (G-T2); parallel lane JQ

Planned effort: 60.75 + 21.25 agent days.

| WP | Title | Repositories | Depends on | Gate | Effort (impl + review) | Lane | Status | Evidence |
|---|---|---|---|---|---|---|---|---|
| JQ-1 | Fault-injection fixture and regression tests (9 places) | nodestro-runtime | G-A | G-M | 1.5 + 0.5 | JQ | Not started |  |
| JQ-2 | Impact analysis including the export | nodestro-runtime, nodestro-api | JQ-1 | G-M | 1 + 0.5 | JQ | Not started |  |
| JQ-3 | Fix option A and allowlist guard | nodestro-runtime | JQ-2 | G-M | 1.5 + 1 | JQ | Not started |  |
| MIG-AXE-0 | Local B-06-1 fix in Axentra right after G-A, independent of Tabrion (replaced later by MIG-AXE-3) | axentra | G-A | G-M | 1 + 0.5 | JQ | Not started |  |
| TAB-010 | tabrion.errors | tabrion | TAB-002 | - | 2 + 1 | Core | Done | `src/tabrion/errors.py`; `tests/unit/test_errors.py` (E01..E13, E15..E24), `tests/postgres/test_errors_postgres.py` (server errors, E12, E14 fault proxy, E15, E21, E23), H-1/H-2/M-2/M-3 regression tests (`test_h1_*`, `test_h2_*`, `test_m2_*`, `test_m3_*`, unit and PostgreSQL), mutation probes in `tests/unit/test_mutation_probes.py`; [M2 evidence](#m2-evidence). Independent review: not accepted at `9fe568f` (H-1, H-2), fixed in the same pull request, **accepted at `ece7572`** (delta re-review; TB-026 done); merged in #3 (`6b99604`). Re-review N-2 and N-5 fixed in the M2 lane B pull request, N-1, N-3, N-4, N-6, N-7 are TODO TB-038..TB-042 |
| TAB-011 | tabrion.sql split_statements | tabrion | TAB-002 | - | 1 + 0.25 | Core | Done | `src/tabrion/sql.py`; `tests/unit/test_sql.py` (Q01..Q10), golden file `tests/golden/axentra_splitter.json` (all 17 SQL files of Axentra 9c4666f byte-identical); [M2 evidence](#m2-evidence) |
| TAB-012 | tabrion.config safe_url/url_from_env | tabrion | TAB-002 | - | 0.5 + 0 | Core | Done | `src/tabrion/config.py`; `tests/unit/test_config.py` (C01..C08, review M-1, L-1, L-3, L-6); [M2 evidence](#m2-evidence) |
| QA-8 | Supply chain (pip-audit, npm audit, SBOM) | tabrion, tabrion-studio | TAB-004 | - | 1 + 0.25 | QA | Not started |  |
| TAB-051 | API stability policy and deprecation helper | tabrion | TAB-023 | - | 1 + 0.25 | Core | Not started |  |
| JQ-4 | Docs, CHANGELOG, export adjustment, pin wave W7 (JQ) | runtime, api, studio | JQ-3 | G-M | 1 + 0.5 | JQ | Not started |  |
| QA-11 | Release workflow without publishing | tabrion | TAB-005 | - | 1 + 0.25 | QA | Not started |  |
| TAB-013 | tabrion.sqlite connection and profile | tabrion | TAB-010 | - | 2 + 1 | Core | Done (#4, `af0f3dc`) | `src/tabrion/sqlite.py` (`SQLiteProfile`, `create_private_file`, `connect`, `apply_profile`, `check_path`, `loading_extensions`); `tests/sqlite/test_connect.py` (S01..S12), `tests/sqlite/test_profile.py` (S13..S15), mutation probes in `tests/sqlite/test_mutation_probes_sqlite.py`; contract I-21..I-23; [lane B evidence](#m2-lane-b-evidence-tab-013-tab-014). Independent review pending (TB-044) |
| TAB-015 | tabrion.postgres configure, ping, diagnose, extension_status | tabrion | TAB-010, TAB-012 | - | 2 + 0.5 | Core | Done | `src/tabrion/postgres/__init__.py`, `_common.py`; `tests/postgres/test_session.py` (P01..P05, P16, G03), `tests/postgres/test_health.py` (P06..P15), `tests/postgres/test_contract_rules.py` (G01, G02, `search_path` hijack), `tests/unit/test_postgres_unit.py`, PostgreSQL mutation probes; [M2 lane C evidence](#m2-lane-c-evidence). Independent review required (TB-032) |
| TAB-038 | Internal pre-release v0.1.0a1 (errors, sql, config) for unmerged integration branches (RC-8) | tabrion | TAB-010, TAB-011, TAB-012, TAB-005 | - | 0.25 + 0.25 | Core | Done | `__version__ = "0.1.0a1"` at merge commit `a463422` of [#9](https://github.com/peterkarlhuber/tabrion/pull/9) (independent review accepted), CHANGELOG `[0.1.0a1]`, `docs/getting-started/installation.md` (pin the commit SHA, RC-8). Annotated tag `v0.1.0a1` on `a463422`: created by the agent (owner decision of 2026-11-08 11:26Z), not on GitHub because the agent's Git proxy refuses tag pushes; an optional label, consumers pin the SHA. No PyPI, no GitHub release, `release.yml` not run. Master moved on to `0.1.0a2.dev0` (TB-053 done) |
| QA-22 | Conventions audit of the new repositories | tabrion, tabrion-studio | TAB-004, STU-018 | - | 0.5 + 0 | QA | Not started |  |
| TAB-016 | tabrion.postgres reading roles | tabrion | TAB-015 | - | 1.5 + 0.75 | Core | Done | `src/tabrion/postgres/roles.py`; `tests/postgres/test_roles.py` (R01..R10 against the verbatim Axentra and Nodestro queries; R19/R20 roles a session can become, renumbered from R11/R12 in the third lane C pull request because contract 4.6 assigns R11..R18 to the grant), G01/G02; [M2 lane C evidence](#m2-lane-c-evidence). Independent review (TB-032): accepted after the delta re-review; merged in [#5](https://github.com/peterkarlhuber/tabrion/pull/5) (`b34abb2`); DL-1 fixed in the second lane C pull request |
| TAB-030 | SessionLock and advisory lock helpers (0.1) | tabrion | TAB-015 | - | 2 + 1 | Core | Done | `src/tabrion/postgres/locks.py`; `tests/postgres/test_locks.py` (L01..L15, G01), `tests/postgres/test_faults.py` (locks under faults), `tests/unit/test_locks_and_faults_unit.py`, mutation probes; contract I-32; [M2 lane C2 evidence](#m2-lane-c2-evidence). Independent review of #6: accepted with conditions at 0cd79eb, fix round in the same pull request, accepted after the delta re-review at ce5ad7d; merged in [#6](https://github.com/peterkarlhuber/tabrion/pull/6) (`b274d89`); DL-1 and the Info items of the delta re-review in the third lane C pull request ([M2 lane C3 evidence](#m2-lane-c3-evidence)) |
| TAB-034 | Fault-injection proxy (includes QA-3), internal `tabrion.testing._faults` (TB-033) | tabrion | TAB-015 | - | 1.5 + 0.5 | Core | Done | `src/tabrion/testing/_faults.py`; `tests/postgres/test_faults.py` (TAB-T-170, every fault three times), E14 in `tests/postgres/test_errors_postgres.py` on the proxy; contract I-33; [M2 lane C2 evidence](#m2-lane-c2-evidence); merged in [#6](https://github.com/peterkarlhuber/tabrion/pull/6) (`b274d89`) |
| TAB-014 | tabrion.sqlite transactions, migrate, backup, checks | tabrion | TAB-013 | - | 3 + 1.5 | Core | Done (#4, `af0f3dc`) | `src/tabrion/sqlite.py` (transactions, `migrate`, `backup`, checks, `diagnose`); `tests/sqlite/test_transactions.py` (S16..S21), `test_migrate.py` (S22..S30), `test_backup.py` (S31..S34), `test_checks.py` (S35..S41), `test_notx.py` (T-NOTX); contract I-24..I-31; [lane B evidence](#m2-lane-b-evidence-tab-013-tab-014). Independent review pending (TB-044) |
| TAB-020 | tabrion.testing 0.1 | tabrion | TAB-015, TAB-013 | - | 2 + 0.5 | Core | Done | `src/tabrion/testing/` (`configure`, `_config.py`, the URL, schema, server-version and SQLite fixtures, `require_extension`); `tests/unit/test_testing_plugin.py` (X01..X03, X08, X09), `tests/postgres/test_testing_plugin.py` (X04..X07, X10); the PostgreSQL suites run on the plugin with a per-run prefix and a leak check after each test; contract I-36, D-51, D-71; [M2 lane C4 evidence](#m2-lane-c4-evidence); accepted after the second re-review, merged in [#11](https://github.com/peterkarlhuber/tabrion/pull/11) (`e9da2a3`) |
| TAB-031 | grant_runtime_role unifying Nodestro and Axentra (0.1) | tabrion | TAB-016 | - | 2 + 1 | Core | Done | `src/tabrion/postgres/roles.py` (`grant_runtime_role`); `tests/postgres/test_grants.py` (R11..R18), the verbatim reference grants `tests/golden/role_grants.py` and their catalog snapshots `tests/golden/role_grants.json`, `tests/unit/test_postgres_unit.py`, mutation probes; contract I-35, D-46..D-50; [M2 lane C3 evidence](#m2-lane-c3-evidence). Independent review: accepted after the delta re-review; merged in [#8](https://github.com/peterkarlhuber/tabrion/pull/8) (`6e833f4`); DL-1 fixed in #11 |
| TAB-032 | testing: database per session, roles without superuser, truncate, runtime-role mode (0.1) | tabrion | TAB-020 | - | 2 + 0.5 | Core | Done | `src/tabrion/testing/postgres.py` (`tabrion_postgres_database`, `tabrion_postgres_roles`, `truncate_tables`, runtime-role mode); `tests/postgres/test_testing_database.py` (X11..X18), `tests/golden/axentra_conftest.py` and `.json` (X17); contract I-60, D-70, D-72, D-73; [M2 lane C4 evidence](#m2-lane-c4-evidence). Re-review at `5110c8f`: TAB-020 accepted, TAB-032 accepted with conditions (M-1); M-1, DL-1, DL-2, L-1, L-2 and the Info items fixed in #11; accepted after the second re-review, merged in [#11](https://github.com/peterkarlhuber/tabrion/pull/11) (`e9da2a3`); its follow-ups RR2-L1 and Info in the TAB-022 pull request |
| QA-4 | Concurrency and stress suite | tabrion | TAB-014, TAB-015 | - | 1 + 0.25 | QA | Not started |  |
| RA-B-1 | Measurements: local capture on SQLite | tabrion (examples/measurements) | TAB-014 | - | 1.5 + 0.5 | Ref-Apps | Done (#16, `b79e783`) | `examples/measurements` (layer A only, branched from master); `tests/examples/test_measurements.py` runs in the normal suite: 10 000 rows in one `write_transaction` (statement trace), `migrate` with mandatory backup (`BackupRequired`, `BackupFailed`), `0600` files, `SIGKILL` inside the batch leaves 0 rows, after the commit 10 000. Natural key `(device_id, channel, ts_utc)` and `seq` for RA-B-2 |
| TAB-017 | tabrion.check and finding codes | tabrion | TAB-014, TAB-016 | - | 1.5 + 0.5 | Core | Done | `src/tabrion/check.py` (TAB-SQ-001..009), `src/tabrion/postgres/_check.py` (TAB-PG-001..011, 020, 021, 030); `tests/sqlite/test_findings.py` (K01..K09, K27..K30), `tests/postgres/test_check_findings.py` (K10..K20, K22..K24, K27..K30); contract I-50..I-55; independent review accepted with conditions at `397669e`, fix round M-1 and L-1..L-5 in the same pull request (TB-062, TB-063). TAB-PG-012 and TAB-B-001/002 (K21, K25, K26) and `Database.check()` follow the layer-B core (D-60, TODO TB-060); merged in #13 (`ca21de6`) |
| TAB-018 | Layer B core (Database, Profile, Transaction, fetch_*) | tabrion | TAB-014, TAB-015 | - | 4 + 1 | Core | Done (#12, `1af69d2`; follow-ups #17, `a9ec3b8`) | `src/tabrion/_sa/` (`profile.py`, `database.py`, `events.py`); `tests/unit/test_database.py` (SQLite, URLs, TLS, profiles), `tests/postgres/test_database.py`, `tests/unit/test_mutation_probes_layer_b.py`; contract section 5.7 (I-37..I-44, I-56); decides TB-020 (F-11), TB-040 (N-4) and F-14; independent review accepted with conditions at `b9ac2e1`, fix round C-1..C-3 and L-1..L-3 in the same pull request (I-56, D-61, TB-061); [M2 lane B2 evidence](#m2-lane-b2-evidence-tab-018). TAB-017 follows in a pull request of its own |
| TAB-022 | Consumer protection: golden files (blocking) and nightly downstream | tabrion | TAB-014, TAB-016, TAB-030 | - | 3 + 0.75 | Core | Done for (a) (#19, `4d8e486`; follow-up #33, `5a7bc9e`); (b) the nightly job is TB-070 (#34) | (a) `scripts/record_golden.py`, `tests/golden/consumers/` (scenarios and one recorder per consumer), `tests/golden/{axentra,nodestro,nodestro_api,orkexa}.json` (full commit SHAs), the replay tests `tests/postgres/test_golden_consumers.py` and `tests/unit/test_golden_consumers.py`, the coverage test of every A/B row of contract section 8; (b) `scripts/downstream.py`; contract I-61, D-74, D-75; the CI jobs are TODO TB-070; [M2 lane C5 evidence](#m2-lane-c5-evidence) |
| TAB-050 | Internal test asset "Levuvo Database Profile" (vectors, no vendoring) | tabrion | TAB-010, TAB-011, TAB-014, TAB-016 | - | 2 + 0.5 | Core | Not started |  |
| QA-2 | Canary value suite (no values in errors or logs) | tabrion | TAB-010, TAB-018 | - | 1 + 0.5 | QA | Not started |  |
| RA-B-2 | Measurements: idempotent transfer to PostgreSQL | tabrion (examples/measurements) | RA-B-1, TAB-018 | - | 2 + 0.5 | Ref-Apps | Not started |  |
| TAB-019 | tabrion.migrations status/require_current | tabrion | TAB-018 | - | 1 + 0.25 | Core | Done (#14, review tab-019-review.md and re-review ACCEPTED; TB-066) | `src/tabrion/migrations.py`; `tests/unit/test_migrations.py` (TAB-T-M01..M08 on SQLite, `depends_on` after a real upgrade, every broken-script case), `tests/postgres/test_migrations_postgres.py` (version table in a schema); contract I-57. Stacked on the TAB-018 branch (#12); green on Alembic 1.13.0 and 1.20 |
| TAB-021 | Profile and portability test cases | tabrion | TAB-018 | - | 1 + 0.25 | Core | Done (#15, review and re-review ACCEPTED; R-1 open as a follow-up) | `src/tabrion/testing/profiles.py`; `tests/unit/test_profile_cases.py` (F01..F06 pass for `tabrion.sqlite.connect` and fail for broken connect functions; D01..D09 on SQLite file and memory), `tests/postgres/test_portability_postgres.py` (D01..D09 on PostgreSQL); contract I-58, deviations D-62 and D-63. Review fix round (review `tab-021-review.md`, M-1..M-3, L-1..L-3): F05 also refuses links to existing files with a reopen control and accepts only `UnsafePath("sqlite.symlink")`; the D cases create fresh table names without `IF NOT EXISTS` and drop only their own tables; a failing test per rule (mutants of `profiles.py`: 40 of 44 killed, including all 15 of the review; the 4 survivors are equivalent or redundant checks); D03 `GENERATED ALWAYS`. Pull request #15, on master since #12 (TAB-018) merged |
| RA-A-1 | Parts: portable schema and Alembic | tabrion (examples/parts) | TAB-019 | - | 1.5 + 0.5 | Ref-Apps | Not started |  |
| TAB-041 | Complete 0.1 documentation (guides) | tabrion | TAB-019, TAB-020, TAB-017 | - | 3 + 0.75 | Core | Not started |  |
| RA-B-3 | Measurements: offline and failure tests | tabrion (examples/measurements) | RA-B-2, TAB-034 | - | 1.5 + 0.5 | Ref-Apps | Not started |  |
| RA-B-4 | Measurements: plain SQLAlchemy baseline | tabrion (examples/measurements-baseline) | RA-B-2 | - | 1 + 0.25 | Ref-Apps | Not started |  |
| RA-A-2 | Parts: functions and tests on SQLite and PostgreSQL | tabrion (examples/parts) | RA-A-1 | - | 2 + 0.5 | Ref-Apps | Not started |  |
| RA-A-3 | Parts: plain SQLAlchemy baseline | tabrion (examples/parts-baseline) | RA-A-2 | - | 1 + 0.25 | Ref-Apps | Not started |  |
| TAB-040 | Release 0.1.0 (tag), G-T2 | tabrion | TAB-041, TAB-021, TAB-022, TAB-030, TAB-031, TAB-032, RA-A-2, RA-B-3, QA-2, QA-11, TAB-034, TAB-050, TAB-051 | G-T2 | 0.5 + 0.5 | Core | Not started | `v0.1.0` final is the merge commit of the version-bump pull request that sets `0.1.0`; its full SHA goes here, into CHANGELOG `[0.1.0]` and `installation.md`, and the next pull request bumps master to `0.1.1.dev0`. Consumers pin it by full SHA via `git+https`; a tag is an optional label (ADR 0009, amendment of 2026-11-08) |
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
| TAB-043 | PyPI path V-B (bound to G-R): trusted publisher, 0.1.0rc1 | tabrion | TAB-040, GOV-1 | G-R | 0.5 + 0.5 | Core | Not started | Not planned (owner decision of 2026-11-08 11:26Z): consumers use Git SHA pins; only if a publication is decided, then with G-R and the environment `pypi` (O-2) |
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
| GOV-1 | Report `docs/program/reports/name-check.md`; no identical hit in classes 9/42, else stop ST-10 | The report exists; it found identical unregistered prior use (R-36) and recommended HOLD. The owner decided to continue and to check the registers manually before G-T2 (O-3), so the status is "Done: conditional". By the owner decision of 2026-11-08 11:26Z the check is not ordered and no prerequisite of G-T2 |
| GOV-2 | Repository public, MIT, branch protection (required check `ci-ok`, no force push, no deletion) active; first CI run green | Public with MIT; the first CI run is the M1 pull request. Branch protection cannot be set by the agent (the agent proxy gets HTTP 403), so it is owner action O-1, optional by the owner decision of 2026-11-08 11:26Z |
| QA-12 | `docs/program/risks.md` equals plan document 11; `STATUS.md` with every WP ID of the backlog | [risks.md](risks.md) (all of plan document 11 plus R-36 and R-CR-1..6); this file lists all 125 backlog IDs and the 26 CR-DB IDs |
| QA-16 | AC-1: contracts per edge list K-33 (tabrion imports nothing from Levuvo); mutation probe red per repository | Four contracts in `pyproject.toml` (six after G-T1 C-5); `tests/architecture/test_import_contracts.py`: positive control, 13 mutation probes (18 after C-5) that must each name their broken contract, coverage of every module by the layers contract, AST prefix scan. The orkexa, axentra and tabrion-studio parts belong to their repositories |
| TAB-001 | `docs/specification.md` (English) covers plan document 02 chapters 1-11; the review agent finds no contradiction to plan document 04 | [specification.md](../specification.md), chapters 1-11 plus chapter 12 (stability policy) |
| TAB-003 | `pip install -e .[dev]` clean; ruff, mypy --strict, `lint-imports`, empty pytest suite green | CI job `lint` and the `test` matrix; the suite is not empty: it already holds the M1 contract, isolation, packaging, architecture and pin tests |
| TAB-007 | `docs/adr/0001..0010` in English, status "Accepted", content equals plan document 10 | [docs/adr/](../adr/README.md): 0001-0010 and 0011-0013 (also in plan document 10); differences only under "Changes from the plan" |
| TAB-002 | Contract and typed stubs, G-T1 | [contract.md](../api/contract.md); stubs in `src/tabrion/` raise `NotImplementedError` and are mypy-strict clean; `tests/unit/test_public_api.py` freezes `__all__` per module |
| TAB-004 | CI ci, ci-ok, nightly, release (with QA-1) | `.github/workflows/ci.yml`, `ci-ok.yml`, `nightly.yml`, `platforms.yml`, `release.yml`; `.github/ci-manifest.json` checked by `ci_ok.py --self-check`; actions pinned by commit SHA and images by digest (`tests/security/test_workflow_pins.py`, TAB-T-181); `release.yml` runs only by manual dispatch and its publish job uploads nothing (G-R). Conditional: `ci-ok` gates only once O-1 requires its commit status (C-7) |
| TAB-006 | MkDocs skeleton, README, CONTRIBUTING, SECURITY | `mkdocs.yml` with `strict: true`, CI job `docs` (`mkdocs build --strict`); README, CONTRIBUTING, SECURITY, SUPPORT, CODE_OF_CONDUCT, CHANGELOG |
| TAB-005 | Import isolation and extras matrix (with QA-5): every combination installs and imports; `import tabrion.sqlite` without psycopg and SQLAlchemy; negative probe red once | `tests/packaging/test_extras_matrix.py` (8 combinations in fresh virtual environments from the built wheel), `tests/unit/test_import_isolation.py`, `tests/packaging/test_isolation_probe.py` (an injected `import psycopg` must fail), CI jobs `extras matrix` and `core without extras` |

## M2 evidence

What the M2 lane A pull request delivers against the acceptance criteria of planning document 09 and the Definition
of Done (planning document 07 section 6). Local run on 2026-11-08: Python 3.13, PostgreSQL 16.15; CI runs Python
3.11-3.14 and PostgreSQL 16, 17, 18 and pgvector 17.

| WP | Acceptance criterion (plan document 09) | Evidence |
|---|---|---|
| TAB-010 | TAB-T-E01..E20 green including canary values and `DETAIL` removal (B-06-1); stdlib-only; SQLSTATE table complete per doc 04 | Every row of the mapping table: stand-ins in `test_e01_e09_postgres_mapping` and `test_e04_e08_sqlite_codes`, real server errors in `test_e01_e09_server_errors`, `test_e05`..`test_e08` (permission, read-only, timeout, cancel, lock, deadlock, serialization, terminated and closed connections), real SQLite errors in `test_e04_e08_sqlite_real_errors`. Canary `SECRET-4711` (E10) over every SQLSTATE and four `DETAIL` shapes; E11 no `__cause__`; E12 conninfo password; E13 1 000 Hypothesis exceptions, hostile objects, cycles; E14 fault proxy (reply lost after sending and during COMMIT: `outcome_unknown`, not retryable, the row was written once); E15 German message (unit; the server test skips without a `de_DE` locale, TB-025); E16 fresh interpreter; E17..E20 hierarchy (contract I-8); E21..E24 filter (text and JSON output, chains, exception groups, depth 12, SQLAlchemy `[parameters]` with a real `IntegrityError`, preformatted messages, non-database exceptions unchanged). Stdlib-only: `lint-imports` and the allowlist test of TB-019 |
| TAB-011 | TAB-T-Q01..Q10; byte-identical to Axentra for every test vector and every Axentra migration file | Q01 Axentra's vector; Q02 `tests/golden/axentra_splitter.json`: the 14 shipped migrations, two example migrations and the init script of Axentra 9c4666f, compared as bytes, and `tests/golden/axentra_splitter.py` (Axentra's splitter verbatim) reproduces the file; Q10 also 1 000 Hypothesis scripts identical to the oracle. The documented differences (contract I-9) are tested in both directions, and `test_no_axentra_sql_file_hits_a_difference` is the MIG-AXE evidence |
| TAB-012 | TAB-T-C01..C08; the password never in `safe_url`, `repr`, error text | C01..C04 URL, conninfo, query parameter, unparseable and never-raises (Hypothesis, also a property that the password never survives); C05..C08 `url_from_env` (file without the trailing newline, symlink, ambiguous, missing, empty, unreadable, directory, FIFO, invalid UTF-8; no value or path in `str`, `repr`, `args` or `details`) |

| DoD point (07 section 6) | TAB-010 | TAB-011 | TAB-012 |
|---|---|---|---|
| 1. English; acceptance criteria checked one by one with test IDs | Yes (table above) | Yes | Yes |
| 2. Required CI jobs green; no new skip without a reason | CI of this pull request; one new conditional skip (E15 server locale, TB-025) | CI | CI |
| 3. Public API documented, CHANGELOG; security notes | Contract section 1 and I-1..I-8, CHANGELOG, SECURITY.md residual risks | Contract 2.1, I-9, CHANGELOG | Contract 2.3, I-10, I-11, CHANGELOG |
| 4. No parameter value or secret in new log or error paths | E10, E12, E17, E21..E24 | No error path | C06..C08 |
| 5. Rollback path | Revert of this pull request (no consumer uses it yet; TAB-038 not tagged) | Same | Same |
| 6. STATUS.md and TODO.md updated | Yes | Yes | Yes |
| Review model | **Independent review agent required** (TAB package in `errors`); not accepted at `9fe568f`, fix round below, **accepted at `ece7572`** (delta re-review, TB-026 done) | Self-review | Self-review |

Fix round after the independent review (`reviews/tab-010-012-review.md`, not accepted at `9fe568f`):

| Finding | Fix | Evidence |
|---|---|---|
| H-1 (R-NORETRY via `__context__`) | `.orig` and `__cause__` first, `__context__` only as a fallback and never under `__suppress_context__`; an outer connection loss wins (contract I-13, D-26) | Unit: `test_h1_connection_lost_during_commit_inside_a_handler` (40001, 40P01, 55P03, 23505), `test_h1_sqlalchemy_upsert_fallback_loses_the_connection`, `test_h1_outermost_connection_loss_wins_over_a_cause`, `test_h1_context_is_a_fallback_only`, `test_h1_an_older_handled_error_never_classifies_a_driver_error`; PostgreSQL: `test_h1_commit_lost_while_handling_a_serialization_failure`, `test_h1_sqlalchemy_upsert_fallback_loses_the_connection` (real socket cut during COMMIT); two mutation probes |
| H-2 (`DETAIL` via `repr`, containers, `e.args`, dict `msg`, escaped `\n`) | Errors are collected from `args`, `msg`, lists, tuples, sets, dicts and exception `args`; the formatted message is always checked; escaped `\nDETAIL:` forms and `repr` forms are scrubbed (contract I-6) | `test_h2_repr_and_container_arguments` (every probe of the review table), `test_h2_found_errors_inside_containers_set_the_record_attributes`, PostgreSQL `test_h2_real_unique_violation_in_repr_and_containers`; probes for collection and the escaped pattern |
| M-1 (`safe_url` userinfo end) | User information ends at the last `@` before the first `/` (libpq) or before the query when later (contract I-10) | `test_config.py` cases `u:a?b=c@h`, `u:a?password=x@h`, `u:a&b@h`, `u:SEC?RET-4711@h/db`; probe |
| M-2 (multi-line `DETAIL`) | Server text is removed from its marker line to the next boundary line | `test_m2_multi_line_detail_is_removed_completely`; probe |
| M-3 (7 surviving mutants) | A test per mutant, including leak paths 10 and 18 | `test_m3_*` (inline server text, group members, notes, mapping arguments, 57P04/57P05, invalidated connection, wrapper around a lost connection); 12 new probes |
| L-1 (fd leak, directory code) | `os.open` with `O_NONBLOCK`, `fstat` before reading, `close` in `finally`; a directory gives `config.not_regular_file` | `test_c08_no_file_descriptor_leaks`, directory test |
| L-2 (outcome unknown by definition) | 08007 and 40003 always `outcome_unknown`, never retryable | `test_l2_outcome_unknown_by_definition`, E13 property; probe |
| L-3 (file bytes in `__context__`) | Errors raised outside the `except` block | `test_c08_errors_carry_no_context` |
| L-6 (no size limit) | At most 64 KiB, then `config.too_large` (D-27) | `test_c08_large_file_is_refused`; probe |
| I-1, I-2, I-5 (part), I-6 | `str.__str__` normalization; "never raises" documented for `Exception`; a `TabrionError` is classified by its `classification`; `$é$` listed in I-9 | `test_i1_string_subclasses_cannot_steer_the_message`, `test_h1_tabrion_error_keeps_its_classification` |
| L-4, L-5, I-3, I-4, I-5 (rest), I-7 | Not in this pull request | TODO TB-028..TB-030, SECURITY.md residual risks, cross-repository TB-031 |

Mutation probes (`tests/unit/test_mutation_probes.py`): 25 probes. Each of 13 safety rules broken in a copy of the package turns
its test red (R-NORETRY, `outcome_unknown` after COMMIT, statement timeout, depth 5, server-text stripping, clearing
`exc_info`, scrubbing wrappers, nested comments, `E'...'` escapes, comment-only statements, conninfo masking, raw `@`
in a URL password, the regular-file rule); a positive control passes on the unchanged copy; the fix round adds 12 probes for the review findings (H-1, H-2,
M-1, M-2, M-3, L-2, L-6). Three new import-linter
probes for the new bottom layer `tabrion.errors` (contract I-12).

## M2 lane C evidence

What the M2 lane C pull request (TABRION TAB-015, TAB-016) delivers against planning document 09. Local run on
2026-11-08: Python 3.13, PostgreSQL 16.15, psycopg 3.3; CI runs PostgreSQL 16, 17, 18 and pgvector 17 on Python 3.11
and 3.14. Every PostgreSQL suite runs as the superuser **and** as LOGIN roles without superuser (`NOSUPERUSER
NOBYPASSRLS`, created per test in `tests/postgres/conftest.py`); privilege and extension cases use a throwaway
database.

| WP | Acceptance criterion (plan document 09) | Evidence |
|---|---|---|
| TAB-015 | TAB-T-P01..P15 against PostgreSQL 16/17/18; `transaction_timeout` only from 17; one round trip for `configure` | P01 values through `SHOW`/`pg_settings` (quoted `search_path`, defaults, `unchanged()` sends nothing, `transaction_timeout` set from 17 and skipped on 16); P02 `configure` never commits, `session_configurator` commits only its own transaction; P03 inside a transaction; P04 validation (unit: every field and bound; server: nothing reaches it, an unknown time zone passes through as a driver error); P05 one round trip counted with libpq's protocol tracer (with a positive control); P06 ok with and without autocommit, `SET LOCAL` gone afterwards; P07 terminated backend, closed connection, unreachable server through a pool (`sent=False`); P08 a real server-side wait (safe snapshot of a `SERIALIZABLE READ ONLY DEFERRABLE` session) cancelled by the timeout, TIMEOUT 57014, connection IDLE and usable; P09 `psycopg_pool` source, exhausted pool, a Nodestro-style `SharedPool`; P10..P12 `diagnose` as superuser, without `pg_settings`/`pg_stat_ssl` privileges (fields `None`, timeouts from `current_setting`), as a runtime role, SSL on and off; P13..P15 every state with `pg_trgm`/`gtrgm`, `other_schema` and its command executed, quoted odd schema names, and equality with Nodestro's `pgvector_status` query for every state (`vector` in the pgvector job, which must not skip); P16 a `psycopg_pool` with `session_configurator` shared by two consumers (same backend, same GUCs, no tenant leaks) |
| TAB-016 | TAB-T-R01..R10; `owner_scope` schema/rls gives Nodestro- and Axentra-identical results | Every `role_info` case is compared with the verbatim Axentra (`rls`) and Nodestro (`schema`) queries: R01 restricted role, R02 superuser, R03 `BYPASSRLS`, R04 owner of a table without RLS (schema yes, rls no), R05 owner of a table with FORCE RLS, R06 owner through membership and a partitioned table, R07 reasons and flags (`allow_superuser` implies table ownership), R08 `current_schema()` versus `current_schemas(false)`, invalid scope; R09/R10 `inspect_role` equals Nodestro's dict and Axentra's report key by key (owned tables, `schema=`, CREATE on the schema, revoked CONNECT, a superuser target, a runtime role inspecting itself, no current schema), `RoleMissing` without the name. Review fixes (contract I-20, D-32): R06 a NOINHERIT member of the owner is an owner (L-2); R19 (formerly R11) a member of a SUPERUSER or BYPASSRLS role, INHERIT and NOINHERIT, directly and through an intermediate role, is reported and refused in both scopes, by `role_info`, `check_role` and `inspect_role`, while the reference queries miss it (asserted); R20 (formerly R12) a superuser session after `SET ROLE app` and a login role that switched to a plain role but can become a BYPASSRLS role; a control with an unprivileged membership stays restricted |
| Both | T-NOTX, R-SET (TAB-T-G01..G03) | `test_contract_rules.py`: every implemented function in IDLE with autocommit, IDLE without autocommit and INTRANS, with tuple, `dict_row`, `namedtuple_row` + `ClientCursor` and `RawCursor` connections, as superuser and runtime role (204 cases): status, `autocommit`, row and cursor factory, backend unchanged; a caller's open write neither committed nor rolled back; `app.tenant_id` (transaction-local), a session `app.*` setting, `current_user`, `session_user` unchanged; `configure` on IDLE without autocommit is the one documented INTRANS. INERROR is left alone (ping logs nothing there, review I-1). Failure paths (review M-1, D-33): `inspect_role` with a missing schema, `extension_status` with a malformed `probe_type`, `configure` with an unknown time zone and `inspect_role` of a missing role, in an implicit and in a `connection.transaction()` transaction, as superuser and runtime role: the caller's write stays and commits, the time zone is unchanged; defined results in every state. A hostile schema before `pg_catalog` with functions, operators `=` (name, name/text, text, oid, "char", int4) and domains `text`, `name`, `oid`, `regtype` runs none of them (contract I-16, review M-2; the traps are shown to fire on unqualified use). G03: a subclass field outside the allowlist is refused, and the one statement sent names exactly the allowlisted settings |
| Both | Installation without psycopg (G-7) | `test_postgres_functions_give_the_installation_hint_without_psycopg` under the extras blockade |

| DoD point (07 section 6) | TAB-015 | TAB-016 |
|---|---|---|
| 1. English; acceptance criteria checked one by one with test IDs | Yes (table above) | Yes |
| 2. Required CI jobs green; no new skip without a reason | CI of this pull request; no new skip (the pgvector job fails instead of skipping) | CI |
| 3. Public API documented, CHANGELOG; security notes | Contract 4.1..4.4, I-14..I-18, CHANGELOG, SECURITY.md (server-side ping limit, qualified types and operators) | Contract 4.5, 4.6, I-19, I-20, CHANGELOG |
| 4. No parameter value or secret in new log or error paths | Errors carry the field name only (`postgres.invalid_setting`); no logging | `RoleMissing` without the role name; `RoleUnsafe` with reason codes only |
| 5. Rollback path | Revert of this pull request (no consumer uses it yet) | Same |
| 6. STATUS.md and TODO.md updated | Yes | Yes |
| Review model | Independent review agent (TODO TB-032): not accepted at e0060a6; H-1, M-1, M-2, L-2, I-1 fixed, L-1 documented, I-2..I-4 in TODO; delta re-review pending | Same |

Mutation probes (`tests/postgres/test_postgres_mutation_probes.py`, against the server): 15 probes, each turns its
test red: the reader's rollback of its own transaction, a rollback of the caller's transaction, the own `tuple_row`
cursor, a commit in `configure`, the commit of `session_configurator`, the R-SET allowlist, `search_path`
validation, ping's server-side limit, `pg_catalog` qualification, the `pg_stat_ssl` privilege check, schema
visibility in `extension_status`, the `relrowsecurity` filter of the `rls` scope, `allow_superuser` implying table
ownership, the `bypass_rls` refusal and the owned tables of `inspect_role`; plus a positive control. The probe for
schema visibility found a gap in P14 (only checked with a probe type), fixed before this pull request.

Review fixes add 9 probes (24 in all): the savepoint of readers in a running transaction (M-1), the schema oid in
`inspect_role` (M-1), a qualified operator and a qualified type (M-2), `session_user` and membership of a superuser
role in `role_info`, membership of a BYPASSRLS role in `inspect_role` (H-1), `MEMBER` instead of `USAGE` (L-2) and
ping without a savepoint in an aborted transaction (I-1). Each new R19/R20, M-1 and M-2 test was also run against the
reviewed head e0060a6 and fails there.

## M2 lane C2 evidence

What the second M2 lane C pull request (TABRION TAB-030, TAB-034 with QA-3, review DL-1) delivers against planning
document 09. Local run on 2026-11-08: Python 3.13, PostgreSQL 16.15, psycopg 3.3; CI runs PostgreSQL 16, 17, 18 and
pgvector 17. The lock suites run as the superuser and as a LOGIN role without superuser; other processes are real
subprocesses.

| WP | Acceptance criterion (plan document 09) | Evidence |
|---|---|---|
| TAB-030 | TAB-T-L01..L15 against PostgreSQL; polling compatible with `CREATE INDEX CONCURRENTLY`; drop-in for Nodestro and Axentra | `lock_key` fits `bigint` at both ends and the Nodestro/Axentra constants; L01 two processes compete; L02 counting (unlock only at the last release); L03 release and `held_keys`, nothing connects for an unheld key; L04 `pg_terminate_backend` clears every count, also while unlocking; L05 polling with timeout; L06 while `acquire` waits the session is idle without snapshot, with the configured `application_name`; L07 close and context manager; L08 `configure` callback and connect errors passing through; L09 threads; L10 fork (the child does not end the parent's session and opens its own); L11 two processes, one waits (with and without autocommit); L12 idle samples hold no snapshot; L13 `CREATE INDEX CONCURRENTLY` in the holder's session completes while another session waits, and the blocking control fails (D-42); L14 timeout; L15 refused inside a transaction, the helpers end nothing, failed and closed connections; G01 for the helpers (status, autocommit, row factory) and per-database keys. Unit: key, interval and timeout validation before any connection, `repr` without the conninfo, the installation hint without psycopg |
| TAB-034 / QA-3 | The fault proxy; TAB-T-170 "the statement runs exactly once"; reproducible | `tests/postgres/test_faults.py`, each scenario three times: `reply` (INSERT ran once, `then` sees it committed before the client's error; `classify`: CONNECTION, `outcome_unknown`, not retryable), `reply` on `COMMIT` (`during_commit=True`, committed), `inflight` (count 1 after the backend ended), `request` (count 0, still `outcome_unknown` for the client), `delay` (late answer, usable connection); only the armed connection and statement, once; errors of `then` reach the test. Tabrion under faults: `ping` reports each loss and does not reconnect, ping's delay shows in `latency_ms` and is not bounded by `timeout` (TB-034); `SessionLock` raises, forgets the key and gets the lost session's locks back from the server; `wait_advisory_lock` does not retry. E14 now uses the proxy instead of a timer |
| DL-1 | Review of #5: the guard in pipeline mode | `test_dl1_pipeline_mode_keeps_the_callers_transaction` (every failing input of M-1 and three readers inside `connection.pipeline()` with queued writes: no `PipelineAborted`, both writes commit) and `test_dl1_pipeline_mode_on_an_idle_connection`; contract I-34. Fails without the fix for the two inputs that raise on the server |

| DoD point (07 section 6) | TAB-030 | TAB-034 |
|---|---|---|
| 1. English; acceptance criteria checked one by one with test IDs | Yes (table above) | Yes |
| 2. Required CI jobs green; no new skip without a reason | CI of this pull request | CI (the proxy skips only for a Unix-socket server) |
| 3. Public API documented, CHANGELOG; security notes | Contract 4.7, I-32, I-34, CHANGELOG | Internal module, contract I-33, CHANGELOG |
| 4. No parameter value or secret in new log or error paths | `repr` without conninfo; error codes `postgres.lock_closed`, `postgres.lock_in_transaction` | Test-only |
| 5. Rollback path | Revert of this pull request (no consumer uses it yet) | Same |
| 6. STATUS.md and TODO.md updated | Yes | Yes |
| Review model | Independent review of 0cd79eb: accepted with conditions (planning folder `reviews/tab-030-034-review.md`); M-1, L-1..L-4 and Info I-1..I-5 fixed in this pull request (table below) | Same review |

Mutation probes (`tests/postgres/test_postgres_mutation_probes.py`): 9 new, 33 in all, each turns its test red: the
pipeline sync of DL-1; a blocking `pg_advisory_lock` in `wait_advisory_lock` (L13), counts kept after a lost
connection (L04), unlock at the first release (L02), waiting inside a transaction (L15), a non-autocommit lock
connection (unit); the fault proxy forwarding the reply of a `reply` fault, forwarding a `request` fault, and not
delaying. The review fix round adds 10 (43 in all): ping's sync inside its `try` (M-1), no sync after an attempt (L-1),
no sync before the status check (L-4 mutant 14), a lost key taken again (L-3), no new internal lock after a fork
(L-2), `held_keys` without its probe (mutant 10), no default `connect_timeout` (mutant 21), `acquire` polling without
the internal lock (mutant 22), the key range checked on `int(key)` (I-1), and the surrogate error (I-2).

Review of #6 (0cd79eb, accepted with conditions) and the fix round:

| Finding | Fix | Evidence |
|---|---|---|
| M-1 ping swallowed the caller's queued pipeline error | `settle()` before ping's `try`; contract I-34, ping docstring | `test_m1_6_pipeline_error_of_the_caller_propagates` (ping, `role_info`, both lock helpers; with and without autocommit): `UniqueViolation` raised, the aborted work visible to the caller; a probe moves the sync back |
| L-1 waiting in an autocommit pipeline held a statement and snapshot | `settle()` after each attempt | `test_l12_pipeline_autocommit_holds_no_snapshot_while_waiting`; probe |
| L-2 fork while another thread holds the internal lock | `os.register_at_fork` replaces the internal lock of every live `SessionLock` in the child | `test_l10_fork_while_the_internal_lock_is_held` (the child answers instead of hanging), unit test of the hook; probe |
| L-3 re-entrant `try_acquire` after a loss | `UsageError("postgres.lock_lost")` until `holds`/`release` acknowledge (D-43, I-32) | `test_l04_reentrant_acquire_after_a_loss_is_refused`; probe |
| L-4 surviving mutants 10, 14, 21, 22 and the comment in `settle()` | Tests for each; comment corrected | `test_l04_held_keys_probes_the_connection`, `test_l15_pipeline_with_queued_statements`, `test_session_lock_connects_with_the_documented_arguments`, `test_l09_threads_acquire_on_a_fresh_lock` (16 threads, 4 busy keys, one connection, counts 4); four probes |
| I-1 an `int` subclass lying about its value | range check on `int.__index__(key)` | `test_keys_are_checked_by_their_exact_value`; probe |
| I-2 `lock_key` with an unpaired surrogate | `ValueError` without the value | `test_lock_key_unpaired_surrogate`; probe |
| I-3 threads share the holds; I-4 half-open TCP | Documented (I-32, `SessionLock` docstring; SECURITY.md keepalives in `connect_kwargs`) | |
| I-5 the proxy's framing assumptions | Documented in the `FaultProxy` docstring (connect only through `dsn()`; `reply` also drops earlier unrelayed replies) | |
| DL-2 of #4: R02/R10 assumed the admin has BYPASSRLS | The oracle comparison takes the reference's `bypass_rls` for superusers; `bypass_rls` asserted as "any role has BYPASSRLS" (I-20) | `test_r02_superuser`, `test_r10_schema_parameter_privileges_and_superuser` |

## M2 lane B2 evidence (TAB-018)

What the layer-B pull request delivers against planning document 09. Local run on 2026-11-08: Python 3.13 with
SQLAlchemy 2.1.8 and psycopg 3.3.8, Python 3.11 with SQLAlchemy 2.0.30 and psycopg 3.2.2, Python 3.12;
PostgreSQL 16.15. CI runs the SQLAlchemy 2.0 matrix entry, the lowest versions (SQLAlchemy 2.0.30, psycopg 3.2.0)
and PostgreSQL 16, 17, 18.

| WP | Acceptance criterion (plan document 09) | Evidence |
|---|---|---|
| TAB-018 | TAB-T-B01..B25 on SQLAlchemy 2.0.30 and 2.1.x | B01..B06 creation and lifetime (URLs, PostgreSQL rewrite, TLS matrix, path checks, `from_env`, `from_engine`, `close`), B07 five threads without a lost update on both engines (`BEGIN IMMEDIATE`, `FOR UPDATE`), B08 rollback, B09 translated errors (also at `COMMIT`, and a connection killed before `COMMIT` is `outcome_unknown`), B10 nesting and thread binding, B11/B12 read-only on both engines and `Profile.readonly()`, B13/B14 timeouts (recursive CTE, `pg_sleep`; the profile value comes back), B16 `executemany`, B17 `MultipleRowsError`, B18 `SECRET-4711` in no error, traceback or context, B19 `ResourceWarning`, B20 Core expressions, B21 connections return after errors (pool statistics), B22 `query_only` does not stick, B23..B25 health and `check`. B15 (`cancel`) is a 0.2 method (TAB-033). Green on SQLAlchemy 2.0.30 and 2.1.8 |
| TAB-018 | No second connection (counting test) | `test_b06_no_second_connection` (SQLite: one `sqlite3` connection for every transaction after `from_url`'s own check) and `test_b02_no_second_connection` (PostgreSQL: one `connect` event and one session in `pg_stat_activity` for reads, writes and `health`) |
| TAB-018 | `read()` is READ ONLY/`query_only` in the database, a guard against accidents (I-56) | `test_b11_read_is_enforced_by_sqlite`, `test_b12_read_is_enforced_by_postgres` (SQLSTATE 25006), `test_b11_read_only_profile` and `test_b12_read_only_profile` (also on a plain `engine.connect()`, and after a `read()`: D-53); review fix round: `test_b11_query_only_lifted_in_read_is_not_committed`, `test_b12_read_only_lifted_in_read_is_not_committed`, `test_b12_session_default_lifted_in_read_is_rolled_back`; the documented gaps `test_b12_documents_the_gap_transaction_control_in_read` and `test_b12_documents_session_settings_stay_on_the_pooled_connection` |
| TAB-018 | Review fix round (C-2, C-3, L-3) | `test_b06_from_engine_refuses_a_read_only_profile_with_listeners`, `test_b03_tls_a_service_is_not_local`, `test_b03_tls_pgservice_and_pghostaddr_are_not_local`, `test_b09_failed_checkout_is_retryable_with_outcome_known` (M28), `test_b09_failed_rollback_invalidates_the_connection` (M22) |
| TAB-018 | Mutation probes | 11 probes (`BEGIN IMMEDIATE`, `query_only` on and off, untranslated errors, read-only profile, nesting, deadline removal, path checks in `from_url`, two TLS rules, TB-040) |
| G-T1 | F-11, F-14, review N-4 | F-11 and TB-020: documentation-only aliases; F-14: `TypeError` from `Database()`/`Transaction()`; N-4 and TB-040: R-NORETRY over a stored classification with `during_commit` (contract I-43) |

## M2 lane C3 evidence

What the third M2 lane C pull request (TABRION TAB-031; DL-1 and the Info items of the delta re-review of #6)
delivers against planning document 09. Local run on 2026-11-08: Python 3.13, PostgreSQL 16.15, psycopg 3.3; CI runs
PostgreSQL 16, 17, 18 and pgvector 17. The owner role of every grant test is a LOGIN role without superuser; the
catalog is read by the superuser.

| WP | Acceptance criterion (plan document 09, contract 4.6) | Evidence |
|---|---|---|
| TAB-031 | TAB-T-R11..R18: the catalog snapshot equals the golden files of the Nodestro and Axentra behavior on a fresh schema; idempotent; refusals; bookkeeping revoked; an owner without the right to grant CONNECT | The fixture (risk R-T05): a schema with capital letters owned by a role without superuser, a serial table, an identity table `Mixed Case`, a partitioned table and its partition, a sequence, a view, a function, a procedure and both bookkeeping tables. The snapshot: `pg_namespace.nspacl`, `relacl` of every relation, `information_schema.role_table_grants` and `role_routine_grants`, `pg_default_acl`, `has_database_privilege` and the database ACL. R11 Axentra drop-in (`protected_tables=("axentra_schema_migrations",)`, with and without autocommit) and R12 Nodestro drop-in (`SCHEMA_VERSION_TABLES`, schema from `current_schema()`): Tabrion's snapshot equals the live reference's (verbatim copies in `tests/golden/role_grants.py`, Axentra `1a59c84`, Nodestro `6698265`) and the golden file; the returned report equals the reference `inspect_role`; the golden file's two snapshots differ only in `nodestro_schema_migrations`. R13 a second run changes nothing and returns the same report; the default privileges cover the owner's later table and function; the runtime role writes the tables and the partitioned table, reads but cannot write the bookkeeping, cannot `TRUNCATE` or `CREATE`. R14 refusals with nothing granted: the owner itself, a NOINHERIT member of a SUPERUSER role, `BYPASSRLS` directly and as a NOINHERIT member, a table owner, a NOINHERIT member of another table's owner (the references refuse the direct cases and grant the membership ones, asserted); a member of the owner in an empty schema (D-46; the reference grants); `RoleMissing` without the name; no schema. R15 protected tables by exact name in the named schema only (a quoted name, a missing name, a same-named table in a schema earlier on the `search_path`, untouched). R16 CONNECT in a database without PUBLIC `CONNECT`: an owner without grant option (a server warning, `connect=False`, grants kept), an owner with no privilege left on the database (`InsufficientPrivilege` ignored), `grant_connect=False`, and a database owner that grants it. R17 T-NOTX: INTRANS, INERROR and a pipeline with a queued statement raise `nested_transaction` and leave the status as it was; IDLE with and without autocommit (also inside an autocommit pipeline) ends IDLE with autocommit and row factory unchanged; a connection cut by the fault proxy before `ALTER DEFAULT PRIVILEGES` leaves nothing granted. R18 a role and a schema with quotes, spaces and capitals under a `search_path` with a schema before `pg_catalog` holding an always-true `=` for `text` and `name`, `current_schema()`, `pg_has_role` and a same-named bookkeeping table: the snapshot still equals the Axentra golden file, and the member-of-owner refusal holds. Unit: argument validation before the connection is touched, the installation hint without psycopg; `tabrion.postgres` left `STUB_MODULES` |
| DL-1 (delta re-review of #6) | The two surviving mutants (3: `release` with a count of 0 keeps the loss; 5: a forked child keeps the parent's lost keys) | `test_l04_release_acknowledges_a_loss_seen_elsewhere` (the loss noticed by `held_keys()`, which does not acknowledge it; `release(K)` does) and `test_l10_fork_after_a_loss` (the child takes the parent's lost key; the parent still raises `lock_lost`); one probe each |
| Info (delta re-review of #6) | I-32 sentences; DI-1 | I-32: `held_keys()` does not acknowledge a loss, `acquire(timeout=...)` raises at once (L04 asserts it returns within 5 s of a 30 s timeout); `configure` must not fork (I-32 and the `SessionLock` docstring); the one-shot `holds()`/`try_acquire` window of Nodestro's scheduler pattern is TODO TB-052 for MIG-NOD |

| DoD point (07 section 6) | TAB-031 |
|---|---|
| 1. English; acceptance criteria checked one by one with test IDs | Yes (table above) |
| 2. Required CI jobs green; no new skip without a reason | CI of this pull request (the atomicity case skips only for a Unix-socket server, as the fault proxy needs TCP) |
| 3. Public API documented, CHANGELOG; security notes | Contract 4.6, I-35, CHANGELOG; TM-07: only DML on the named schema, never `CREATE`, write privileges on the bookkeeping revoked |
| 4. No parameter value or secret in new log or error paths | `RoleMissing` and `RoleUnsafe` without the role name; `nested_transaction` without values |
| 5. Rollback path | Revert of this pull request (no consumer uses it yet) |
| 6. STATUS.md and TODO.md updated | Yes |
| Review model | Independent review (TB-032) |

Mutation probes (`tests/postgres/test_postgres_mutation_probes.py`): 9 new, 52 in all, each turns its test red: the
two DL-1 mutants; the member-of-owner refusal (R14), the revoke of protected tables (R15), the `nested_transaction`
refusal (R17), the pipeline sync before the status check (R17), the single transaction (R17 atomicity), the
`InsufficientPrivilege` handling of `GRANT CONNECT` (R16) and the `grant_connect` flag (R16). The review fix round
adds 6 (58 in all): the schema-owner refusal (M-1), the 63-byte check (L-2), the missing schema (L-4), `TRUNCATE` in
the revoke, no `CONNECT` grant for a role that may connect through PUBLIC, and only `InsufficientPrivilege` ignored
(L-3, mutants 13, 15, 17).

Review of #8 (4402d33, accepted with conditions, planning folder `reviews/tab-031-review.md`) and the fix round:

| Finding | Fix | Evidence |
|---|---|---|
| M-1 a role that owns the schema, or can become its owner, was granted | `table_owner` also for `pg_has_role(role, nspowner, 'MEMBER')` (D-49, I-35); TB-035 for the references | `test_r14_refusals[schema_owner]` and `[schema_owner_member]` (NOINHERIT): refused, nothing granted, the reference grants (asserted); the D-46 test now uses a schema owned by another role, so each check has its own case; probe |
| L-1 `CREATE` on the schema is kept and reported only | Documented: I-35, the docstring and SECURITY.md (callers check `can_create`) | `test_r14_can_create_is_reported` |
| L-2 a 64-byte schema name granted on the 63-byte schema | `ValueError` for a `role`, `schema` or protected table name over 63 bytes in UTF-8, without the name (D-50) | `test_r14_schema_name_over_63_bytes` (64 characters and 64 characters with 65 bytes next to a 63-byte schema: nothing granted; 63 bytes work); probe |
| L-4 a missing schema surfaced as `InvalidSchemaName` | `UsageError("postgres.schema_missing")` before anything is granted (D-50) | `test_r14_schema_missing`; probe |
| L-3 mutants 13, 15, 17 | Tests | `test_r15_truncate_granted_before_is_revoked`, `test_r16_connect_through_public_adds_no_acl_entry` (database ACL unchanged), `test_r16_a_failing_connect_step_propagates` (a `lock_timeout` in the CONNECT step propagates, the first transaction's grants stay; a second run completes) and `test_r16_other_errors_of_the_connect_step_propagate` (a cut connection); three probes |
| I-1 checks before the grant transaction | TODO TB-054 | |
| I-3 a grantor without grant option gets a WARNING | Documented: check `report.connect` (docstring, I-35, SECURITY.md) | `test_r16_owner_without_the_right_to_grant_connect` |
| Teardown on a shared server | `make_role` skips a database another run dropped meanwhile | |

## M2 lane C4 evidence

What the fourth M2 lane C pull request delivers against planning document 09: TABRION TAB-020 and TAB-032, the fixes
of its review and DL-1 of the delta re-review of #8. Local run on 2026-11-08: Python 3.13, PostgreSQL 16.15, psycopg 3.3; CI runs
PostgreSQL 16, 17, 18 and pgvector 17. Inner pytest runs use `pytester` in a subprocess.

| WP | Acceptance criterion (plan document 09) | Evidence |
|---|---|---|
| TAB-020 | TAB-T-X01..X10; Tabrion's own suite uses only these fixtures | X01 defaults, `env_prefix` for Nodestro and Orkexa, Axentra's own names, the first set URL variable wins, any value but empty or `0` counts, the extension list; X02 validation of every argument, a refused call changes nothing; X03 skip without the variable, error under `TABRION_REQUIRE_POSTGRES=1`, the consumer's names, skip (or error) without psycopg, and a failing test's report shows the URL with the password masked; X04 the schema exists, `owner_url` puts it first on the `search_path`, existing `options` are kept, `repr` hides the password; X04/X05 an inner run whose failing test leaves a connection in a transaction with a lock in its schema: both schemas dropped, names unique and with the configured prefix; X06 `server_version_num`; X07 `require_extension` in a throwaway database: active returned, installable created (with and without autocommit, committed), inside the caller's transaction a savepoint the caller's rollback removes, an extension in another schema left there and made visible through `search_path`, an empty `search_path`, two runs racing for one extension (no skip hides a regression: a skip fails these tests), unavailable and refused (a role without the right) skipped, both failing when listed in `TABRION_REQUIRE_EXTENSIONS`, an invalid name `ValueError`; X08 `tabrion_sqlite_path` in `tmp_path`, `tabrion_sqlite` with the profile (foreign keys, WAL, `trusted_schema` off, 0600) and closed afterwards; X09 no `pytest11` entry point in `pyproject.toml` or the installed metadata, the fixtures exist only with `pytest_plugins`; X10 the PostgreSQL suites run on the plugin (`tests/conftest.py`), every name carries the run's prefix `t_<hex6>_`, and after each test a check finds nothing of it left (D-51) |
| TAB-032 | TAB-T-X11..X18; Axentra's `conftest.py` semantics reproduced in a golden test | X11 an inner run on `RoleSetup()`: owner and app `NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION LOGIN`, the database and `public` owned by the owner, `PUBLIC` holds only `USAGE` on `public`, the app connects but can neither create in `public` nor read the owner's table, the same database for the whole session, a session left open does not keep it, afterwards database and roles gone, no password in a failing test's report; X12 `split=False` (also named), `None` (the admin owns it), named roles, a named role that exists fails the setup and is left untouched (its password still works), a wrong return value of `tabrion_role_setup`, and the name validation (unit); X13 `tabrion_postgres_roles` in the existing database: attributes, the owner creates a schema and a table, the app may not, a transaction of the owner still open at the end; afterwards roles, schema and the database ACL entries gone; X14 one `TRUNCATE` (every statement trigger sees the same `current_query()`), quoted names, the partition with its parent, no view or sequence, identities restarted; X15 `like`, `exclude`, `CASCADE` into an excluded table, a URL target, an explicit schema, commit on an idle connection and a savepoint in the caller's transaction, the errors, an empty schema; X16 runtime-role mode: role `<prefix>rt_<hex12>` per test, DML on a table created afterwards through `owner_url`, no `CREATE`, `DROP` or `TRUNCATE`, its open transaction ended by the teardown, role and `CONNECT` grant gone; X17 Axentra's split-role `pg_urls` and `truncate_all` (verbatim, `tests/golden/axentra_conftest.py`) and the same conftest on the plugin produce the same state (roles, ownership, privileges of the app, RLS, read-only bookkeeping, rows after the truncate), equal to `tests/golden/axentra_conftest.json`; X18 an admin with only `CREATEDB CREATEROLE` runs both fixtures, and in a database it does not own `tabrion_postgres_roles` fails with a clear message and cleans up. Mutation probes: 19 for TAB-032 (`tests/postgres/test_postgres_mutation_probes.py`), each turns its test red |

### Review of #11 (TAB-020)

The independent review (planning folder `reviews/tab-020-review.md`, head `6e74a73`) did not accept #11: one High,
two Low, Info; the delta re-review of #8 left DL-1, also fixed here. Each finding, its fix and the evidence; each fix has a mutation probe in
`tests/postgres/test_postgres_mutation_probes.py` that the named test kills.

| Finding | Fix | Evidence |
|---|---|---|
| H-1 `require_extension` moved a shared extension into the test schema; the teardown's `DROP SCHEMA ... CASCADE` then dropped it and dependent columns elsewhere | `require_extension` never moves an extension: `other_schema` appends the extension's schema to the connection's `search_path`; `installable` runs `CREATE EXTENSION IF NOT EXISTS ... SCHEMA public` (never a schema a fixture drops; two racing runs both end up active). The teardown refuses a schema that holds an extension (schema left in place, teardown fails with the name) (contract I-36) | `test_h1_teardown_never_reaches_a_shared_extension` (citext in `public` with `public.users(email citext)`, and the installable case: after `require_extension` on a test schema's URL and the teardown, extension, column and row intact), `test_h1_teardown_refuses_a_schema_holding_an_extension`, `test_x07_other_schema_is_made_visible_not_moved` (replaces the test that asserted the move), `test_x07_empty_search_path`, `test_x07_two_runs_racing_for_an_installable_extension`; the X07 tests turn a skip into a failure; 3 probes (no `search_path` step, `CREATE` into the current schema, no extension guard) |
| L-1 the teardown ended other roles' sessions (a backup) | Only sessions of the URL's role are ended; another role's lock is waited for with `lock_timeout` 30 s | `test_l1_another_roles_session_is_waited_for_not_ended` (a REPEATABLE READ session of another role, `application_name=pg_dump`: alive, the `DROP` waits and completes after its rollback); probe |
| L-2 the termination scope was untested (mutants 2, 3 survived) | Tests for both filters | `test_l2_a_lock_in_another_database_is_left_alone` (two databases cloned from one template share the object ids; the twin's session survives, the session in the dropped schema's database is ended), `test_l2_a_lock_in_another_schema_is_left_alone`; 2 probes. Mutant 1 (own pid) is equivalent: the teardown connection holds no lock in the schema |
| DL-1 of #8 (delta re-review) the 63-byte limit had no multibyte case | Test | `test_dl1_multibyte_names_are_measured_in_bytes` (a 32-character, 64-byte name refused as `schema`, protected table and role; a 37-character, 63-byte schema accepted); probe (characters instead of bytes) |
| Info require variable `true` skipped silently | Any value but empty or `0` requires | `test_x01_variables_first_flag_and_extension_list` (`0` and empty off; `1`, `true`, `yes` on); probe |

### Re-review of #11 (TAB-020 fixes, TAB-032)

The re-review (planning folder `reviews/tab-020-032-rereview.md`, head `5110c8f`) accepted TAB-020 and accepted
TAB-032 with one condition (M-1). Every finding is fixed in #11; each fix has a mutation probe the named test kills.

| Finding | Fix | Evidence |
|---|---|---|
| M-1 (condition) `truncate_tables` without `schema=` emptied `public` of the shared database, and `CASCADE` crossed schemas | Without `schema=` only a current schema with the run's prefix is accepted (`ValueError` otherwise; `schema="public"` stays allowed); a recursive catalog query finds every table the `CASCADE` reaches (foreign keys and partitions, transitively) and refuses one in another schema before anything is emptied (contract I-60, D-73) | `test_m1_default_schema_must_be_a_test_schema` (the admin URL and a connection on `public` keep their row; `schema="public"` empties it), `test_m1_cascade_into_another_schema_is_refused` (directly, and through an excluded table of the schema; nothing emptied; works once the key is gone); 3 probes (no prefix check, no cascade check, one step instead of the recursion) |
| DL-1 the extension guard missed an extension member in the schema | The `DROP` compares the extensions before and after, in its transaction, and is rolled back when one disappeared (contract I-36) | `test_dl1_teardown_refuses_to_drop_an_extension_member` (`ALTER EXTENSION citext ADD FUNCTION <schema>.f()`: schema, extension and the `citext` column in `public` intact; after `ALTER EXTENSION ... DROP` the teardown works); probe |
| DL-2 an L-2 test (and its probe) ran in the shared admin database | It runs in `scratch_database` | `test_l2_a_lock_in_another_schema_is_left_alone`; its probe now ends sessions only in that scratch database |
| L-1 a `NOINHERIT` admin leaked a database and roles | Memberships `WITH INHERIT TRUE, SET TRUE` from PostgreSQL 16; `drop_all` tries every role and raises the first error at the end; `DROP OWNED` in the admin's database before the roles of `tabrion_postgres_database` go (Info 4) | X18 runs both fixtures under an `INHERIT` and a `NOINHERIT` admin (both without superuser, nothing left over); `test_l2_drop_all_tries_every_role`; 2 probes |
| L-2 three teardown steps untested | Tests | `test_l2_drop_all_ends_the_roles_sessions_everywhere` (sessions in the admin's and another database ended), `test_l2_drop_owned_fails_within_its_lock_timeout` (another role's lock: `LockNotAvailable` after 10 s, that session alive; bounded by a thread so a mutant cannot hang the suite); 3 probes. No probe widens the termination to other roles' sessions |
| Re-review 2 (head `7533dae`, both parts accepted) RR2-L1 and Info, in the TAB-022 pull request | The cascade guard names its reason per table and locks the selected tables (`ONLY`) before the catalog check | `test_m1_cascade_into_another_schema_is_refused` with a partition and an inheritance child in another schema and a foreign key to a partition; `test_m1_selected_tables_are_locked_before_the_check` (a reader's lock makes a refused call wait first); `test_rr2_database_fixture_drops_the_roles_grants_in_the_admin_database`; `test_rr2_role_repr_hides_the_password`; 4 probes |
| Info `_Role` showed its password in `repr`; the password literal in DDL logs; the unquoted `search_path` option; `LIKE` wildcards; the dependents outside a dropped schema | `field(repr=False)`; SECURITY.md names `log_statement` and `pg_stat_statements`; a docstring on `_search_path_url`; the `like` docstring and I-60 show `\_`; I-36 names the dependents | Review; the `repr` is plain dataclass behavior |

## M2 lane C5 evidence

What the fifth M2 lane C pull request delivers against planning document 09: TABRION TAB-022 (consumer protection).
It started stacked on #11 (the coverage test counts `tests/golden/axentra_conftest.json` from TAB-032) and targets master since #11 merged. Local run
on 2026-11-08: Python 3.13, PostgreSQL 16.15 (no pgvector), psycopg 3.3, SQLite 3.53.

| WP | Acceptance criterion (plan documents 04 and 09) | Evidence |
|---|---|---|
| TAB-022 (a) | Golden files `tests/golden/` recorded with `scripts/record_golden.py` in a throwaway environment, with commit provenance; a deviation is red in every pull request; every category A/B row of section 8 has at least one golden file | `scripts/record_golden.py` clones each consumer at a pinned full SHA (Axentra `ea7aa9f`, nodestro-runtime `463e0d9`, nodestro-api `221f9e1`, Orkexa `5bf139e`), installs it alone into a fresh virtual environment and runs its recorder, which calls the consumer's own code (`Database._configure`, `ping`, `role_info`/`check_role`, `inspect_role`, `_wait_for_lock`, `LeaderLock`, `db_errors.safe_message`; `SQLiteStore` opening, `transaction`, `migrate`, `snapshot`, `PostgresDatabase._configure`, its session locks, `role_info`, `inspect_role`, `pgvector_status`, `ServerSettings.from_env`; `diagnostics._database`; `PsycopgSource`, `connect_private_database` and the three transaction helpers) and refuses to run next to Tabrion (`test_recorder_refuses_an_environment_with_tabrion`). The replay tests (16 PostgreSQL, 19 unit) run the same scenarios with Tabrion; the differences the contract table lists (foreign keys on, `trusted_schema` off and 0600 for Nodestro's files, symbolic links refused, Orkexa's source gaining timeouts, D-74, D-75, TB-072, TB-073) are applied explicitly in the tests. `test_every_category_a_b_row_has_a_golden_record` parses section 8: 25 A/B rows, each with records naming a commit. No password and no generated name in a golden file (`test_golden_files_hold_no_secret`). 4 mutation probes show that the golden tests catch a changed write transaction, profile, `role_info` scope and lock count |
| TAB-022 (b) | Nightly `downstream`: the consumers' database tests against Tabrion HEAD, report in the job summary, not blocking | `scripts/downstream.py`: per consumer a clone of `master`, its test extra, Tabrion HEAD through `uv pip install --override` (every Tabrion requirement of the consumer, a Git pin, a range or a transitive one, resolves to the checkout; checked through `direct_url.json`; review M-1 of #19), a throwaway database `t_<hex6>_ds_<consumer>` and its own test variable; a Markdown table (`--summary` for `$GITHUB_STEP_SUMMARY`), exit 0 unless `--strict`. Local run against this branch: Axentra (`ea7aa9f`) 100 passed (again after the fix round, with the override), nodestro-runtime (`463e0d9`) 89 passed and 2 skipped, Orkexa (`5bf139e`) 57 passed and 19 skipped. The consumers' suites create their own fixed roles (`axentra_test_app`, `nodestro_test_runtime`, `orkexa_test_runtime`), so the job belongs on CI's throwaway server, not a shared one. The `nightly.yml` job and a `golden files` job in `ci.yml` need a change under `.github/` (TODO TB-070) |


### Review of #19 (TAB-022), fix round

The review (planning folder `reviews/tab-022-review.md`, head `5dfde86`) accepted the pull request with conditions.
Master `a9ec3b8` (#17) is merged in. Each finding, its fix and the evidence:

| Finding | Fix | Evidence |
|---|---|---|
| M-1 `downstream.py` cannot override a consumer's Tabrion pin (a pip constraint only narrows it) | `install_with_head`: `uv pip install --override` with `tabrion[postgres] @ file://<checkout>` and `--reinstall-package tabrion`, then a check that the environment's `tabrion` is the checkout (else "setup failed"); `uv` from the `dev` extra or a pinned one in the work directory; docstring and TB-070 corrected (no index: Tabrion is consumed by Git commit) | `tests/unit/test_downstream.py`: hand-built wheels, offline; a consumer with `tabrion>=0.1,<0.2`, one with a Git commit pin and one that gets the pin through another package all end with HEAD's stand-in (a pre-release that satisfies neither); a mismatch is `SetupFailed`; without `--override` 4 of the tests fail (checked by hand: the probe harness mutates only `src/tabrion`). Local run of Axentra and Orkexa against HEAD: both installed HEAD; Axentra 100 passed, Orkexa 57 passed and 19 skipped (one run under load had 1 failure that a rerun did not repeat) |
| L-1 the lock covers only the selected tables, so a foreign key to a reached unselected table could be committed between the check and the `TRUNCATE` | The catalog check runs a second time after the `TRUNCATE` (which then holds `ACCESS EXCLUSIVE` on every table it reached) inside the same `conn.transaction()`; a hit raises and the rollback undoes the `TRUNCATE`. I-60 states the lock wait (bounded by `lock_timeout`) and the snapshot limit under `REPEATABLE READ` | `test_l1_a_foreign_key_committed_after_the_check_is_refused_and_rolled_back[idle, in_transaction]` (the reviewer's `rv19_race` setup made deterministic: another session commits the foreign key right after the first check; every row survives); mutation probe 101 (without the second check) |
| L-2 `downstream.py` robustness | Timeouts on every clone, `venv` and install step (30 min) and on each consumer's test run (`--minutes`, default 30); a `psycopg.Error` creating or dropping the database is a failed row (class and SQLSTATE, no message) instead of ending the run; table cells escape `\|` and drop line breaks | `test_report_cells_escape_pipes_and_line_breaks`, `test_a_database_error_is_a_setup_failure_without_its_message` (a canary password and the host are not in the detail) |
| L-3 three older golden files pin short SHAs | `role_grants.json` (Axentra `1a59c84237f77bf9b699187e25005b1fe28f2a2f`, Nodestro `6698265c134b17a337ad0568da02361900050c70`), `axentra_conftest.json` (`1a59c84...`) and `axentra_splitter.json` (`9c4666fe5a44b158ffa9f0149b7bcd6008b1cd7e`) and their reference modules name the full commit (resolved through the GitHub API) | `test_every_category_a_b_row_has_a_golden_record` now requires 40 hex characters |
| Note TB-073 (white space in `url_from_env`) | Decided: `url_from_env` keeps the value as given and removes only the file's trailing newline (deviation D-76); Nodestro's wrapper strips | `test_nodestro_url_from_env` applies D-76; TB-073 Done |


### Delta re-review of #19, follow-up

The delta re-review (planning folder `reviews/tab-022-review.md`, head `24f94f2`) accepted #19 with one condition
(N-1) and Info items. This follow-up pull request, from master `4d8e486`:

| Finding | Fix | Evidence |
|---|---|---|
| N-1 the override `tabrion[postgres]` drops the extras a consumer asks for | `HEAD_EXTRAS = "all"`, `head_requirement(url)`: the override is `tabrion[all] @ file://<checkout>` | `test_head_replaces_the_consumers_tabrion_requirement[consumer_extra]`: a consumer requiring `tabrion[sqlalchemy] @ git+...@<sha>` ends with HEAD and `import sqlalchemy` works (a hand-built stand-in); with `HEAD_EXTRAS = "postgres"` it fails (checked by hand) |
| Info: dropping `--reinstall-package` went unnoticed | `test_head_is_rebuilt_when_only_its_code_changed`: HEAD as a directory with an in-tree PEP 517 backend, built offline twice with only its code changed; the second install has the new code | Pins the behavior. The flag mutant still survives: uv 0.11 reinstalls a local directory named on the command line anyway (checked by hand), so the flag only guards a uv that would not |
| Info: a failed `uv` fallback install ended the run without a report | Every selected consumer gets a failed row "setup failed (installing uv ...)" | `test_a_failed_uv_install_is_a_failed_row_per_consumer` |
| Info: a failed drop of the throwaway database replaced the consumer's result | `with_database`: the result stands and the new `Note` column names the database left behind | `test_a_failed_drop_keeps_the_test_result_and_adds_a_note`; `test_report_cells_escape_pipes_and_line_breaks` covers the column |
| Info: the snapshot limit of I-60 is wider than a caller's transaction | I-60 reworded: the guard is complete only under `READ COMMITTED`; at `REPEATABLE READ` or `SERIALIZABLE`, also as the connection's default, the snapshot predates the `LOCK`, for the selected tables too. Not refused (deviation D-77) | Contract I-60, STATUS D-77 |
| Info: `role_grants.py` docstring named short commits | `role_grants.py`, `axentra_conftest.py` and `axentra_splitter.py` name the full commits in their docstrings as well | - |
| TODO counts did not match the entries | Recounted from the entries: the `TB-` entries whose status is not `Done` (P1 11, P2 11, P3 30, P4 5) and the rule stated above the table | `docs/TODO.md` |

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
| C-7 | `ci-ok` cannot be a required PR check; `master` unprotected (F-06) | `ci-ok.yml` posts the commit status `ci-ok` on the PR head SHA (`ci_ok_status.py`, `statuses: write`, `CI_OK_ENFORCE=1`); ADR 0012 "Merge gate". From the Studio CI review: a PR that changes `.github/workflows/**` or `.github/scripts/**` is NOT OK without the label `workflow-change-reviewed` (F-03), and the status post is retried and fails loudly (F-11). The condition check confirmed C-1..C-6 closed and C-7/C-8 down to owner settings. Takes effect once on `master`; then owner action O-1 (optional) | In review; O-1 optional |
| C-8 | Environment `pypi` and the OIDC token (F-07) | Job `attest` alone holds `id-token: write`; ADR 0009 "Release environment" lists the settings; owner action O-2 | In review; O-2 needed only if a PyPI publication is decided |

### Notes from the G-T1 review (Low and Info findings)

Recorded here as the review asks; none blocks M2. Each is picked up by the work package named.

| Finding | Note | Where it goes |
|---|---|---|
| F-09 | "stdlib-only" is a denylist: any other third-party import (for example `pluggy`, which comes with pytest) passes. An AST test should allow only `sys.stdlib_module_names` and the core modules in `errors`, `sql`, `config`, `sqlite`, `check`. `check.py` already has such a test (C-1) | Fixed in the M2 lane A pull request: `test_core_imports_only_the_standard_library_and_core_modules` with a negative probe (TB-019 closed) |
| F-10 | The root package `tabrion` is in no contract; `from tabrion import Database` inside a core function goes through `__getattr__` to layer B unseen. Extend the AST test of F-09: no `import tabrion` or `from tabrion import` outside `tabrion/__init__.py` | Fixed with F-09 (the same test rejects `import tabrion` and `from tabrion import` in the core files) |
| F-11 | `Statement` and `Params` appear in public layer-B signatures but are not exported from `tabrion` (G-5 rule). Decide: export them as `tabrion.Statement`/`tabrion.Params`, or document them as documentation-only aliases and drop them from `_sa.database.__all__` | Decided in TAB-018: documentation-only aliases, dropped from `_sa.database.__all__`; `surface.json` unchanged (contract I-37, TB-020 closed) |
| F-12 | `DatabaseError.kind` over `classification: Classification \| None`: make `classification` required for `DatabaseError` or return `Kind.UNKNOWN`; plus `args = (code,)` and `__reduce__` for pickling (G-1 notes) | Fixed in TAB-010: `Kind.UNKNOWN` without a classification, `args == (code,)`, `__reduce__` (E18, E19) |
| F-13 | Specification section 10 omitted `Transaction.stream` and the CR-DB modules TAB-CR-1/2 in the 0.2 row | Fixed in the follow-up pull request; the TAB-CR-1/2 modules need a place in the layers contract when they arrive (TB-014, TB-015) |
| F-14 | `Database` has no `__init__`, so `Database()` builds an unusable object | Fixed in TAB-018: `Database()` and `Transaction()` raise `TypeError` naming the factories, through `__new__`, so `surface.json` is unchanged (contract I-37; `test_f14_classes_have_no_public_constructor`) |
| F-15 | macOS and Windows run on pull requests only when `sqlite.py` or `tests/sqlite/**` change; `config.py`, `errors.py`, `testing/sqlite.py` and `pyproject.toml` also matter there | TB-021: add them to `platforms.yml` and the manifest's `path_filters` together (`--self-check` keeps them equal) |
| F-17 | Planning errata: doc 05 section 2.3 ("lazy import ... `allow_indirect_imports=false` + runtime test") is wrong, because import-linter records function-level imports (C-1 replaces it); doc 02 section 9 still writes `tabrion.check(target)` (G-6: the function is `tabrion.check.check`) | Planning errata, owner (O-8) |

### Notes from the G-T1 condition check (PR #2)

| Finding | Note | Status |
|---|---|---|
| CC-1 | `scripts/api_surface.py` counted only `__all__` as exported, so a 0.2 name reachable through `tabrion.__getattr__` passed | Fixed in #2: any successful attribute lookup counts (class members with inheritance); mutation tests "0.2 name importable through `__getattr__` only" and "0.2 mixin mixed in early" |
| CC-2 | `tabrion._sa._v02` imports `Isolation`, `Statement` and `Transaction` from `tabrion._sa.database`; in 0.2 `Database`/`Transaction` inherit from the `_v02` mixins, a circular import | Open, before TAB-033: move the aliases to `tabrion._sa._types` and type `fn` against a forward reference (TODO TB-024) |
| CC-3 | F-15 still open: macOS and Windows run on PRs only for SQLite paths, so they did not run on #2 | Open, TODO TB-021 |
| BR-1 | Nothing excluded the logo from the MIT license | Fixed in #2: `TRADEMARKS.md`, linked from the README and `assets/branding/README.md` |
| BR-2 | The committed master carried a C2PA manifest (`caBX`) and an `eXIf` chunk | Fixed in #2: the chunks are removed without touching the image data; `tests/unit/test_branding.py` pins the new sha256 and the sha256 of the supplied file's decompressed pixel data. The supplied file in the planning folder is unchanged. The designer's original and a vector master for a possible trademark filing stay with the owner |
| BR-3 | The regenerate command was unpinned | Fixed in #2: `pillow==12.3.0`, `numpy==2.5.3` (the versions that produced the committed files) |
| BR-4 | Relative README image paths break on PyPI | Fixed in #2: absolute `raw.githubusercontent.com/.../master/...` URLs (they render once the logo is on `master`) |

## Owner actions

Actions only the owner can take. The agent cannot do them (missing rights) or must not (approval limits).

**Owner decision of 2026-11-08 11:26Z** (plan document 00 section 13): the agent may create Git tags (no GitHub
release); branch protection (O-1, O-7), private vulnerability reporting (O-5) and the social preview (O-9) are
optional repository hardening, never a prerequisite for implementation; a PyPI publication is not needed now, so the
environment `pypi` (O-2) is needed only if a publication is decided later; the trademark check (O-3) is not ordered
and no longer a prerequisite of G-T2. Unchanged: G-R per public publication, the human security review before a
public Tabrion Studio release, and every security, test and quality requirement. No new owner tasks unless they are
technically or legally required.

| # | Action | Needed for | Status |
|---|---|---|---|
| O-1 | **Corrected after G-T1 (C-7, F-06).** Protect `master` of `peterkarlhuber/tabrion` (the agent proxy gets HTTP 403 on the branch protection API): after the follow-up pull request is merged and `ci-ok` has posted its commit status on one pull request, require the status check `ci-ok` (source: GitHub Actions); "require branches to be up to date"; no force pushes, no deletion. Create the label `workflow-change-reviewed`; only the owner or an independent reviewer sets it, on pull requests that change `.github/workflows/**` or `.github/scripts/**` (ADR 0012, "Workflow-change gate"). Recommended, not required (solo owner): `CODEOWNERS` for `/.github/**`, and approval for outside collaborators' workflow runs | GOV-2, C-7 | Optional (owner decision of 2026-11-08 11:26Z); never a prerequisite |
| O-2 | Before any use of `release.yml` with `publish: true` (C-8, F-07): create the environment `pypi` with the owner as required reviewer, "prevent self-review" on, deployment branches and tags restricted to the tag pattern `v*` (no branch rule), no secrets; with TAB-043 bind the PyPI trusted publisher to workflow `release.yml` and environment `pypi` (ADR 0009, "Release environment") | G-R, TAB-043, C-8 | Only if a PyPI publication is decided later (owner decision of 2026-11-08 11:26Z) |
| O-3 | ~~Manual TM register check (TMview/EUIPO/DPMA/WIPO) before G-T2; rename if conflict. Also assess the prior user of the name (R-36).~~ | GOV-1 | Dropped (owner decision of 2026-11-08 11:26Z): not ordered and removed from G-T2; a later public marketing under the name needs a new assessment |
| O-4 | G-T1 review of [contract.md](../api/contract.md), including its open points | TAB-002, G-T1 | Done 2026-11-08: passed with conditions (C-1..C-8) |
| O-5 | Enable private vulnerability reporting in the repository settings (SECURITY.md points to it) | TAB-006 | Optional (owner decision of 2026-11-08 11:26Z) |
| O-6 | Confirm the CR-DB decisions E-CR-1..E-CR-7 (planning document 14 section 9.1) | G-CR, ARC-CR-1, STU-CR-1 | Open |
| O-7 | The same branch protection as O-1 for `peterkarlhuber/tabrion-studio` | GOV-3 | Optional (owner decision of 2026-11-08 11:26Z) |
| O-8 | Record the planning errata of F-17 (doc 05 section 2.3 lazy-import comment, doc 02 section 9 `tabrion.check(target)`) in the planning documents | G-T1 | Open |
| O-9 | Upload `assets/branding/social-preview.png` as the social preview in the repository settings (no API for it) | TAB-006 | Optional, not a blocker (owner decision of 2026-11-08 11:26Z) |

### M2 lane B evidence (TAB-013, TAB-014)

What the M2 lane B pull request delivers against the acceptance criteria of planning document 09 and the Definition of
Done (planning document 07 section 6). Local run on 2026-11-08: Python 3.13 (SQLite 3.53.1), Python 3.11 and 3.12
(SQLite 3.45.1) on Linux; CI runs Python 3.11-3.14 on Linux and the platforms workflow (macOS, Windows), which the
change of `src/tabrion/sqlite.py` and `tests/sqlite/**` triggers. 225 tests in `tests/sqlite/`, 76 of them marked
`security`; branch coverage of `tabrion/sqlite.py` 95 % on Linux (the uncovered lines are the Windows branches and
defensive error paths).

| WP | Acceptance criterion (plan document 09, 07) | Evidence |
|---|---|---|
| TAB-013 | TAB-T-S01..S12; a new file 0600; symlinks refused for the main and side files; FK on; `read_only=True` enforces `query_only` | S01 `test_s01_*`: file, `-wal`, `-shm` and `-journal` 0600 under umask 022, 000 (TAB-T-120) and 277 (the mode never depends on the umask, `fchmod`), also for `create_private_file`; an exact `file_mode` with `allow_shared_file`; S02 an existing 0644 file stays 0644; S03..S06 a link for the file and for each side file, normal and `read_only`, plus a dangling link (TAB-T-121: `UnsafePath("sqlite.symlink")`, the target byte-identical, no path in `str`/`repr`, the path only at DEBUG); S07 every race window through seams: a link planted just before `open` (O_EXCL/O_NOFOLLOW), after the check, only while SQLite opens the file and swapped back (caught by the resolved-name check), and a different inode (TM-04; the randomized 1 000-repetition variant is TB-045); S08 `mode=ro` plus `query_only`, INSERT is READ_ONLY, the file byte-identical, the journal mode kept, `FileNotFoundError` without a path and no file for `read_only`/`create=False`; S09 `:memory:` and `""`; S10 the pragmas of the default, `nodestro()` and a custom profile, and TAB-T-001 (an unknown parent is FOREIGN_KEY); S11 a broken file is CORRUPT and the connection is closed; S12 `O_NOFOLLOW` by platform and reparse points. Directories and FIFOs are `sqlite.unsafe_path`; profile validation (I-21). S13..S15 `apply_profile` (foreign connection, idempotent without a journal-mode write, file detection, refused in a transaction), `check_path`, `loading_extensions` (only inside the block, off after an exception, `extensions_unavailable`) |
| TAB-014 | TAB-T-S13..S41 (doc 02: S13..S35, contract: S16..S41); lost update gives 5 without lock errors (q.py/q2.py); backup target 0600 and atomic | S16 five threads with their own connections read, wait 50 ms and write in `write_transaction`: 5, no error (TAB-T-002/003); S17 rollback on an exception, commit, `BEGIN IMMEDIATE` holds the write lock; S18 every nesting combination and a caller's transaction untouched; S19 `read_transaction` blocks writes (READ_ONLY) and is a snapshot; S20 `query_only` restored after the end and an exception (I-25); S21 `KeyboardInterrupt`; a failing deferred-FK `COMMIT` is rolled back (I-24). S22..S30 `migrate`: each error path compares the SHA-256 of the closed file before and after (TAB-T-010/011); backup order; `BackupFailed`/`MigrationFailed` without `__cause__`/`__context__` and without the exception text; a concurrent migration seen inside the write transaction, and two processes migrating at once (the second waits, sees version 2, runs nothing; the step ran once). S31..S34 `backup`: 0600 under umask 022 and 000 and while copying (TAB-T-122, B-06-3), an interrupt in the middle leaves no file (TAB-T-201), a failed check removes the copy, the check runs on a separate read-only connection, a target that appears during the copy is never replaced, link targets refused, concurrent writers (TAB-T-200, `integrity_check` ok). S35..S38 checks: a damaged file gives `ok=False` without row data (canary), truncation by `max_errors`, a quoted hostile table name, `WITHOUT ROWID`; S39..S41 `diagnose`: every field for a file, `:memory:`, a linked side file, no side effects. T-NOTX (`test_notx.py`): every function that takes a connection, idle and in the caller's transaction, with tuple, `Row` and dict row factories and `text_factory=bytes` |

| DoD point (07 section 6) | TAB-013 | TAB-014 |
|---|---|---|
| 1. English; acceptance criteria checked one by one with test IDs | Yes (table above) | Yes |
| 2. Required CI jobs green; no new skip without a reason | CI of this pull request, including the platforms workflow; skips only where a platform lacks the feature (POSIX modes, FIFOs, symbolic links without the privilege, Python 3.11 without `autocommit`), each with its reason | Same |
| 3. Public API documented, CHANGELOG; security notes | Contract 3.1..3.3, I-21..I-23, CHANGELOG, SECURITY.md (SQLite residual risks) | Contract 3.4..3.8, I-24..I-31, CHANGELOG |
| 4. No parameter value or secret in new log or error paths | No path in any error (`UnsafePath`, `FileNotFoundError`, `FileExistsError`); paths only at DEBUG | `BackupFailed`/`MigrationFailed` carry no text and no chain; check messages without row data (canary); `backup_corrupt` with the page count only |
| 5. Rollback path | Revert of this pull request (no consumer uses `tabrion.sqlite` yet; MIG-NOD-6 and MIG-ORK-3 come later) | Same |
| 6. STATUS.md and TODO.md updated | Yes | Yes |
| Review model | **Independent review agent required** (TAB package in `sqlite`: file permissions); self-review with the DoD and TM-03, TM-04, TM-12, DI-01, DI-02, DI-06, DI-11, SR-08, SR-09; accepted with conditions at `8e78e9c`, fixed in the fix round below (TODO TB-044) | **Independent review agent required** (transactions); same (TB-044) |

Mutation probes (`tests/sqlite/test_mutation_probes_sqlite.py`): 26 probes (17, plus 9 of the review fix round below), each a SQLite safety rule broken in a copy
of the package that turns its test red: `fchmod` (umask independence), `O_EXCL`/`O_NOFOLLOW`, the side-file check,
the resolved-name and inode checks after opening, `foreign_keys`, the `file_mode` rule, `BEGIN IMMEDIATE` (lost
update), rollback on `BaseException`, the `query_only` reset, the version re-read in `migrate`, the 0600 temporary
backup file, the cleanup after an aborted backup, no overwrite of a target that appeared, `max_errors + 1`, quoting of
the table name, and the plain-tuple reads (T-NOTX). Probes that need POSIX modes or symbolic links skip where the
platform lacks them; a positive control passes on the unchanged copy.

Delta re-review of TAB-010..TAB-012 (`reviews/tab-010-012-review.md`, accepted at `ece7572`): N-2 fixed (the
container walk is guarded and lazy: `test_n2_container_whose_iteration_raises_keeps_the_record`,
`test_n3_containers_are_not_copied_in_full`), N-5 fixed (docstring placement); N-1, N-3, N-4, N-6 and N-7 are TODO
TB-038..TB-042, N-4 as a prerequisite of TAB-018.

#### Review fix round (TAB-013, TAB-014)

The independent review at `8e78e9c` (`reviews/tab-013-014-review.md`) is ACCEPTED WITH CONDITIONS. The fix round in
the same pull request, after merging `master` with lane C (`b34abb2`):

| Finding | Fix | Evidence |
|---|---|---|
| M-1 (Medium) an existing file of another user, or group-/world-writable, is opened | `_identity` refuses it on POSIX unless `allow_shared_file`: `UnsafePath("sqlite.unsafe_owner")` / `("sqlite.unsafe_mode")`; existing `-wal`/`-shm`/`-journal` follow the same rules; a readable 0644 file stays accepted (B-06-5) (contract I-22) | `test_m1_foreign_owner_is_refused` (normal and `read_only`, file byte-identical, no path in the error; accepted with `allow_shared_file`), `test_m1_group_or_world_writable_file_is_refused` (0620, 0602, 0664, 0666), `test_m1_readable_but_not_writable_file_is_accepted`, `test_m1_side_file_owner_and_mode`; 3 probes |
| M-2 (Medium) no TM-03 directory check | `diagnose` logs a WARNING on `tabrion.sqlite` for a world-writable directory without the sticky bit, the path only at DEBUG (contract I-30, deviation D-40; the field or check finding is TB-043) | `test_m2_world_writable_directory_without_sticky_bit` (0777 warns; 1777, 0775, 0755, 0700 do not), `test_m2_memory_has_no_directory`; 1 probe |
| M-3 (Medium) `backup` writes into a swapped temporary file | The `(dev, ino)` of `_create` is kept; after SQLite opened the copy and before `connection.backup`, `_same_file` checks it (no link, same inode, the resolved name); `_fsync_file` checks the inode again before publishing (contract I-27) | `test_m3_link_swapped_in_before_sqlite_opens_the_copy` (the attacker file stays empty), `test_m3_file_replaced_before_sqlite_opens_the_copy` (the attacker's hard link stays empty), `test_m3_copy_replaced_before_it_is_published`; the probe `bkrace.py` now gives `UnsafePath sqlite.symlink`, attacker file size 0, secret not in it; 2 probes |
| L-1 (Low) `executescript` in a migration step commits | Documented in the `migrate` docstring and contract I-26 (the former I-19): steps must not use `commit`, `rollback` or `executescript`; the lost transaction is still detected | Docs only; the existing `sqlite.transaction_ended` test covers the detection |
| L-2 (Low) the checks refuse idle legacy connections | Fixed: an idle connection with driver-managed transactions runs the check `PRAGMA`s as they are, without a transaction of Tabrion's own (contract I-29) | `test_l2_checks_on_a_legacy_connection` (`""`, `DEFERRED`, `IMMEDIATE`: idle and inside the driver's transaction, state unchanged); 1 probe |
| L-3 (Low) three surviving mutants | Mutant 4 (`check_path` after `_apply`) and mutant 15 (the early existing-target check) are killed by new seam tests; mutant 18 (`_fsync_file` removed) is not observable without a power-loss harness, but the identity check now inside `_fsync_file` is killed by its own probe; mutant 8 (hang) needs a timeout plugin, TB-049 | `test_l3_link_planted_while_the_profile_is_applied`, `test_l3_existing_target_is_refused_before_the_copy`, `test_m3_copy_replaced_before_it_is_published`; 3 probes |
| I-1..I-4 (Info) | I-3 documented (`loading_extensions` docstring, contract I-23); all four recorded | TODO TB-046..TB-049 (I-4 points to TB-039) |

Lane C's IDs come first after the merge: lane B's contract notes I-14..I-24 are now I-21..I-31, its deviations
D-28..D-33 are D-34..D-39 (new: D-40), and its TODO items TB-032..TB-039 are TB-038..TB-045 (new: TB-046..TB-049).

#### Axentra TB-031 review fixes ported to `tabrion.errors` (after #4)

Axentra's TB-031 pull request (#62, merged at `ea7aa9f`) fixed the findings of `reviews/axe-tb031-review.md` in its
copy of the filter. Each one was first reproduced against Tabrion with a probe (real 23505/23514 from PostgreSQL 16,
real structlog 25 in a scratch environment) and then ported, or marked as not applicable:

| Finding | Before (probe against `af0f3dc`) | Now | Tests |
|---|---|---|---|
| F-1 traceback text without an error object | Leaks: retry loop, `RuntimeError(e)`, `RuntimeError("b", [e])`, a note, a holder with `__str__` | Clean; errors in `args` are found as objects (`db_sqlstate`) | `test_f1_*` (5 shapes); 2 probes |
| F-2 `handleError` | Leaks: broken call inside `except`, failing formatter | Broken calls clean (rewritten, names the handled error) when a database error or its text is handled, or is in the template or an argument (fix round T-1, T-2); a formatter/`emit` failure is a residual (D-44, TB-050, SECURITY.md) | `test_f2_broken_call_while_a_database_error_is_handled`, `test_t1_*` (3 calls, stderr), `test_t2_*`; 3 probes |
| F-3 ReDoS | 20,000 blanks: 1.6 s | 1 ms (look-behind) | `test_f3_long_run_of_blanks_is_linear` (50,000 blanks); 1 probe |
| F-4 boundaries only after a header | Leaks: `+------+`, `File "a", line 1` (a lone `+` and `\| +---` were already clean) | Clean; this also closes re-review N-1 (TODO TB-038 deleted) | `test_f4_*`; 1 probe |
| F-5 mapping `msg` | Became a `str` (structlog's `ProcessorFormatter` breaks) | Stays a mapping with scrubbed values | `test_f5_mapping_message_stays_a_mapping`; 1 probe |
| F-6 `extra={"db_sqlstate": ...}` | No `KeyError`: Tabrion sets the fields in the filter, not in a record factory | Not applicable | none |
| F-7 silent truncation | `login failed user=mallory` | `... user=mallory [redacted: database server text]` | `test_f7_removed_text_is_marked`; 1 probe |
| F-8 two unpinned branches | n/a | Pinned | `test_f8_*` |
| AX D-1 mapping `msg` with `exc_info` | Clean (no mapping branch yet) | Clean; the mapping branch runs after the traceback block | `test_d1_*` |
| AX D-2 structlog `exc_info` | stdlib `ProcessorFormatter` broke (F-5) | stdlib integration clean (`format_exc_info`, `dict_tracebacks`, `ConsoleRenderer`); native structlog clean with the internal processor, which stays internal (D-45, TB-051) | `test_d2_*`; 1 probe |
| AX D-3 guarded attribute access | Clean (`_attr` was already guarded) | Clean with the new F-1 code | `test_d3_*` |
| R2-1 processor fallback | n/a | Only `event` is returned | `test_r2_1_*`; 1 probe |

`diagnose` also follows #4 re-review DL-1: a group- or other-writable database directory warns whatever the sticky
bit ("other users can create files next to the database (side files)"); the non-sticky world-writable case keeps
"can replace the database file"; no path in either message (`test_m2_world_writable_directory_without_sticky_bit`,
8 modes; 1 more probe). TODO TB-031 (the cross-repository port) is done and deleted; new items TB-050, TB-051.

Review of the port (`reviews/tab-errfix-review.md`, at `4faa636`, ACCEPTED WITH CONDITIONS), fix round: T-1 fixed (a
broken call with server text in an argument is rewritten, so `logging`'s "Arguments:" report on stderr never runs:
`log.error("... %s ... %s", str(e))`, a missing mapping key and `%d` with a `str`; `test_t1_*`, 1 probe); T-2 pinned
(`test_t2_broken_call_while_database_text_is_handled` kills the `leaky = False` mutant, 1 probe); T-3, T-4 and T-5
documented in SECURITY.md and contract I-6 (`extra=` with a record-factory install, the two `__str__` residuals, the
label from a suppressed context). After merging #6 the deviations of this port are D-44 and D-45 (were D-41, D-42).

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
| D-21 | TAB-012, QA-16 | Layers contract: `tabrion.errors \| tabrion.sql \| tabrion.config` as one layer of independent modules | `tabrion.errors` is its own bottom layer under `tabrion.sql \| tabrion.config` | `url_from_env` raises `ConfigurationError` (contract 2.3), so `tabrion.config` must import `tabrion.errors`; still six contracts, three new probes (contract I-12) |
| D-22 | TAB-012 | Codes `config.ambiguous` and `config.missing` | Also `config.unreadable` and `config.not_regular_file` | The contract requires a regular file but names no code for a violation or for an unreadable file (contract I-11) |
| D-23 | TAB-010 | Tests TAB-T-E01..E20 | E17..E20 assigned to the hierarchy (contract I-8) | Plan document 04 defines only E01..E16 and E21..E24 |
| D-24 | TAB-011 | Differences from Axentra only for `E'...\'...'` and nested comments | Also dollar tags with digits, `$` inside identifiers, `--` ending at a carriage return, block-comment-only statements (contract I-9) | Each is an input Axentra splits wrongly by PostgreSQL's rules; no Axentra SQL file contains one (golden file) |
| D-25 | TAB-010 | `outcome_unknown` whenever `kind == CONNECTION` and (`sent` or `during_commit`) | Not for SQLSTATE 53300 | The mapping table makes 53300 retryable without condition; it occurs only while connecting (contract I-2) |
| D-26 | TAB-010 | Contract 1.3 "Chains": the first error with a SQLSTATE along `.orig`, `__cause__` and `__context__` | `.orig` and `__cause__` first; `__context__` only as a fallback and not under `__suppress_context__`; an outer connection loss wins (contract I-13) | Review H-1: `__context__` is the error being handled, so a COMMIT lost inside an `except` block for 40001 was classified as retryable, which breaks R-NORETRY |
| D-27 | TAB-012 | Codes of `url_from_env` (D-22) | Also `config.too_large` for a file over 64 KiB (contract I-11) | Review L-6: a huge file or a procfs file was read completely and returned as the URL |
| D-28 | TAB-015 | Contract 4.2: `SET LOCAL statement_timeout` "in a short transaction of its own" | In a running transaction ping uses a savepoint that it rolls back (contract I-14) | A `SET LOCAL` in the caller's transaction would last until that transaction ends (T-NOTX), and a timeout would abort it |
| D-29 | TAB-015, TAB-016 | Contract 4.2: ping raises only `TypeError` | A `timeout` that is not positive and finite, an `owner_scope` or extension name outside its domain raise `ValueError` (contract I-14, I-16) | Programming errors of the same kind as `TypeError`; no database is involved |
| D-30 | TAB-015 | Nodestro `pgvector_status`: `CREATE EXTENSION vector SCHEMA "<schema>";` | `CREATE EXTENSION "vector" SCHEMA "<schema>";` (contract I-18) | Contract 4.4: "identifiers are quoted"; the states are identical (TAB-T-P15), the message text stays Nodestro's |
| D-31 | TAB-015 | Development dependencies of TAB-005 | `psycopg-pool` added to the extra `dev` | P09 and P16 need a real `psycopg_pool.ConnectionPool`; Tabrion itself never imports it (import contract `postgres-psycopg-only`) |
| D-32 | TAB-016 | Axentra `database.py:359-405`, `roles.py:72-104` and Nodestro `storage_postgres.py:757-768`, `947-984`: `rolsuper`/`rolbypassrls` of `current_user` (or the named role) only | `superuser` and `bypass_rls` also when `current_user` or `session_user` (or the named role) is a member of a role with that attribute; `table_owner` also through `session_user` (contract I-20, review H-1, decided by the owner) | A member can `SET ROLE` to that role (NOINHERIT too), a session can `RESET ROLE` to `session_user`: the references report such roles as safe. Signatures and reason codes unchanged (within the G-T1 freeze); TODO TB-035 carries the fix to Axentra and Nodestro |
| D-33 | TAB-015, TAB-016 | Contract 4 (T-NOTX): readers run their statements in the caller's transaction | In the caller's running transaction readers and `configure` run under a savepoint (contract I-15, I-16, review M-1) | A failing statement would otherwise abort the caller's transaction and cost it its writes; two more round trips in that state only |
| D-34 | TAB-013 | Contract 3.2: `check_path`, create, connect, `apply_profile` | Also: an existing file must be a regular file, and after opening the path must still be the checked inode and the name SQLite opened its real path (contract I-22) | TM-04: a link swapped in only while SQLite opens the file, or another file put in place, would otherwise go unnoticed |
| D-35 | TAB-013 | Contract 3.2: `read_only` as `mode=ro` | Also `PRAGMA query_only=ON` (contract I-22) | Plan documents 02 section 4.1 and 09 (TAB-013 acceptance) require it |
| D-36 | TAB-014 | Contract 3.4: `query_only` `OFF` after a read transaction | The previous value is restored (contract I-25) | Otherwise a `read_only` connection would lose `query_only` after its first read transaction |
| D-37 | TAB-014 | Contract 3.5: Nodestro semantics (the version read once) | The version is read again after `BEGIN IMMEDIATE` (contract I-26) | The contract's test for two processes ("the second sees the new version and does nothing") fails with a single read: the second process would run the steps again |
| D-38 | TAB-014 | Contract 3.6: `UsageError` for an open write transaction | For any open transaction; the copy in `journal_mode=DELETE` (contract I-27, I-28) | `sqlite3` cannot tell a read from a write transaction; a WAL-mode copy would leave `-wal`/`-shm` next to the target |
| D-39 | TAB-013, TAB-014 | Test IDs: doc 02 gives S13..S35 to TAB-014; doc 07 a 1 000-repetition race test (TAB-T-123) | S13..S15 under TAB-013 (contract 3.3 names them for `apply_profile`, `check_path`, `loading_extensions`, which are TAB-013 functions); the race windows tested deterministically, the randomized test is TB-045 | The contract is the more specific source; a randomized race test is timing-dependent on pull requests |
| D-40 | TAB-014 | 07 TM-03 / review M-2: a field for a world-writable database directory without the sticky bit | A WARNING on the logger `tabrion.sqlite` from `diagnose` (path only at DEBUG); `SQLiteDiagnosis` unchanged (contract I-30) | `SQLiteDiagnosis` is in the frozen API surface; the `tabrion.check` finding or a 0.2 field is TB-043 |
| D-41 | TAB-034 | Planning documents 07 (K7) and 09: a public `tabrion.testing.faults` | The internal, unstable `tabrion.testing._faults` (contract I-33) | Decision TB-033: the frozen surface is not extended in 0.1; a public module is a 0.2 decision by contract addendum and review |
| D-42 | TAB-030 | Contract 4.7, L13: `CREATE INDEX CONCURRENTLY` "in a third session" | L13 runs it in the holder's session, the Axentra migration case, with a control: a blocking `pg_advisory_lock` waiter makes the server fail one side (deadlock or timeout) | The holder's own index build is where a blocked waiter's snapshot deadlocks; a third session would complete in either design. L12 accepts the moment between an attempt and its rollback without autocommit (contract I-32) |
| D-51 | TAB-020 | Contract 6: Tabrion's own suite uses only the plugin's fixtures and checks that nothing with the prefix `t_` remains | The PostgreSQL suites take their URL and test schema from the plugin (`tests/conftest.py` activates it); the role and scratch-database helpers stay local until TAB-032 brings them, the SQLite suites keep `tmp_path` (TODO TB-055). The leak check covers the run's own prefix `t_<hex6>_` | The server is shared with reviewers and other runs: a check on `t_` would fail on their objects, and a cleanup would drop them |
| D-70 | TAB-032 | Contract 6: `RoleSetup(app=..., owner=...)` names the roles; Axentra's conftest creates `axentra_test_app`/`axentra_test_owner` when missing, reuses them otherwise and never drops them | The fixture creates a named role itself and drops it at the end; a name that exists already fails the setup with a clear message (contract I-60) | The server is shared with reviewers and other runs: taking over a role means changing its password or using it without knowing it, and dropping it at the end would remove another run's role. A consumer moving from the old conftest drops its leftover roles once |
| D-71 | TAB-020 | Contract 6: `require_extension` "tries `CREATE EXTENSION`"; I-36 ran `extension_status`'s command (`CREATE ... SCHEMA <current schema>`, `ALTER EXTENSION ... SET SCHEMA`) | Never a move, never a schema a fixture drops: `CREATE EXTENSION IF NOT EXISTS ... SCHEMA public`, and an extension elsewhere is made visible through the connection's `search_path`; the schema teardown refuses a schema that holds an extension (contract I-36) | Review H-1 of #11: the per-test `DROP SCHEMA ... CASCADE` dropped a database-wide extension and every column of its types in other schemas (Nodestro's pgvector in `public`) |
| D-72 | TAB-032 | Contract 6: `truncate_tables` "every table of the schema", with `exclude` | Ordinary and partitioned tables, not the partitions themselves (they go with the parent); `CASCADE` empties a referencing table even when it is excluded, as documented (contract I-60); X17 compares the Axentra state without the identities, which only `truncate_tables` restarts | The contract fixes `RESTART IDENTITY CASCADE`; naming a partition next to its parent is redundant, and refusing an excluded referencing table would need a second statement |
| D-73 | TAB-032 | Contract 6: `truncate_tables` runs `TRUNCATE ... RESTART IDENTITY CASCADE` | A `CASCADE` that would reach a table in another schema (a foreign key from there, directly or through tables of the schema) is refused with a `ValueError` before anything is emptied; without `schema=` only a schema with the run's prefix is accepted (contract I-60) | Re-review M-1 of #11: the default schema was `public` of a shared database, and `CASCADE` crosses schemas, so one wrong URL emptied other teams' tables. The signature is frozen, so there is no opt-out; a test that really needs it empties the other schema's tables itself after dropping the foreign key, and Axentra's case (`schema="public"`, no cross-schema keys) is unaffected |
| D-74 | TAB-022 | Contract 8.1: `classify`/`safe_message` replace Axentra's generic error paths | The texts are Tabrion's, not Axentra's `core.db_errors.safe_message`: four kind texts differ (`data error` is `invalid data`, `insufficient privilege` is `permission denied`, a statement timeout is `timeout`, not `query canceled`, `syntax error or access rule violation` for 42P01/42703 is `undefined object`), and the table and column follow the constraint; SQLSTATE, constraint and the absence of values are identical (golden test `test_axentra_error_texts`) | The golden file showed the difference; the contract names Tabrion's text form (section 1.3) and the texts are value-free either way. Axentra checks its log and test expectations at adoption (MIG-AX) |
| D-75 | TAB-022 | Contract 8.2: nodestro-api's database check on `diagnose(run_quick_check=True)` | A damaged file is reported `damaged`; nodestro-api today reports `unreadable`, because its own `quick_check` raises on it | `diagnose` reports a failed check instead of raising (contract I-29), which is what the code `damaged` is for; the golden test states the change |
| D-76 | TAB-022 | Contract 8.2: `url_from_env` follows Nodestro's `server_settings.py` `*_FILE` rule | `url_from_env` returns the variable's value as given and the file's content without only its trailing newline; Nodestro strips white space around both | `url_from_env` is a frozen a1 function (contract 2.3); changing what it returns would be a behavior change for every caller, and stripping would also hide a value that is wrong on purpose; libpq accepts a URL with trailing white space, and Nodestro's wrapper strips at adoption (TB-073, golden test `test_nodestro_url_from_env`) |
| D-77 | TAB-032 | Contract 6 and I-60: the cascade guard of `truncate_tables` (D-73) refuses a `TRUNCATE` that would empty a table in another schema | Complete only under `READ COMMITTED`. At `REPEATABLE READ` or `SERIALIZABLE` (the caller's transaction or the connection's default) the catalog checks read a snapshot older than the `LOCK`, so DDL committed concurrently can be missed; the call is not refused | Refusing would make every call on such a connection fail, also without any concurrent DDL, which is the normal case in a test schema, and it would be new API-visible behavior of a frozen function for a race in a test helper; the limit is stated in I-60 (delta re-review of #19) |
| D-78 | TAB-040 | Plan document 07 section 5 (G-T2): "core < 1 500 lines (T-9) or justified"; plan document 01 ST-7 (stop criterion: core over 1 500 lines without a justification) | The stdlib-only core (the package without extras: `__init__`, `_version`, `errors`, `sql`, `config`, `sqlite`, `check`) has **2 128** code lines (lines that are not blank, a comment or a docstring; physical lines 3 037): `errors` 900 (1 278), `sqlite` 750 (1 090), `check` 230 (302), `config` 127 (180), `sql` 78 (114), `__init__` 42 (70). The rest of the package, behind extras: `postgres` 1 145, layer B `_sa` 1 166 and `migrations` 100, `testing` 1 276 (5 815 in all; counted on `5a7bc9e`) | Justified, not split. (1) `errors` is two parts: the classification of both engines and drivers (about 400 code lines: SQLSTATE and SQLite code tables, wrapped-chain walking with depth and cycle limits, R-NORETRY for commits) and the redaction of server text from messages, tracebacks, `logging` records and structlog event dicts (about 500), which the G-T1, Axentra (TB-031 port, #7) and canary reviews grew case by case; each case has a test and many a mutation probe. (2) `sqlite` carries the file-safety rules that the probes S1..S7 demanded (symbolic links, owner and mode 0600, WAL, write lock at `BEGIN`, `migrate` with a pre-migration copy, `backup`, `integrity_check`, `diagnose`), each a contract rule of section 3. (3) `check` is the finding-code table of specification section 9. Nothing in it is dead code: branch coverage is 95 % for `errors` and `sqlite` (local full run, readiness report). Splitting a module only moves lines. What would bring the number down for real: move the log redaction (`RedactingFilter`, the structlog processor and the traceback scrubbing) into a module of its own outside the core count, as a 0.2 layout change with the same public names (TB-050/TB-051 touch it anyway); turn the SQLSTATE and SQLite code tables into one data table; share the path checks of `sqlite.connect`, `migrate` and `backup` through one helper. None of these changes behavior, so none is a 0.1.0 blocker |
| D-43 | TAB-030 | Contract 4.7: after a lost connection "every count is cleared" and the lock is replaced | Also: a key held on the lost connection raises `UsageError("postgres.lock_lost")` in `try_acquire`/`acquire` until `holds` or `release` acknowledges the loss (contract I-32) | Review L-3 of #6: a nested section would otherwise take a fresh hold and its `release` free the lock the outer section relies on. Additive: a new error code, no signature change |
| D-44 | TAB-010 | Axentra TB-031 F-2: `install_db_error_redaction` replaces `Handler.handleError` process-wide | The filter rewrites a broken call made while a database error is handled; formatter and `emit` failures are a documented residual (SECURITY.md) | Tabrion installs nothing and has no install function; a new public name is a surface change; TB-050 |
| D-45 | TAB-010 | Axentra TB-031 D-2: a public `redact_db_errors_processor` for native structlog | The processor is internal (`tabrion.errors._redact_db_errors_processor`); the filter's mapping branch covers structlog's stdlib integration | A new public name extends the frozen surface (`surface.json` unchanged); TB-051 |
| D-46 | TAB-031 | Contract 4.6: refuse `role == owner`, superuser, `BYPASSRLS` or `owned_tables` | Also `table_owner` when `role` can become the connecting role (`pg_has_role(role, CURRENT_USER, 'MEMBER')`, NOINHERIT members too; contract I-35) | Such a role owns every table the owner creates later (I-20, review H-1); the references only notice it once the schema has a table. The reason set stays unchanged |
| D-47 | TAB-031 | Contract 4.6: protected tables "schema-qualified through `to_regclass`"; Nodestro resolves them through the `search_path` | Each name is looked up by its exact text in the target schema in `pg_class`/`pg_namespace` (qualified operators); the `REVOKE` names `schema.table` | `to_regclass` parses its argument as an identifier (case folding, quotes) and an unqualified name resolves through the `search_path`; on the reference fixtures the result is identical (R11, R12) |
| D-48 | TAB-031 | Test IDs: the H-1 tests of #5 were named TAB-T-R11/R12, which contract 4.6 gives to the grant | Renamed to R19/R20 (`test_roles.py`, probes, STATUS, TODO TB-035); R11..R18 are the grant's | The contract numbering wins; the review references to "R11/R12" of #5 mean R19/R20 |
| D-49 | TAB-031 | Contract 4.6: refuse `role == owner`, superuser, `BYPASSRLS` or `owned_tables` | Also `table_owner` when `role` is or can become the owner of the target schema (`pg_has_role(role, nspowner, 'MEMBER')`, NOINHERIT members too; contract I-35) | Review M-1 of #8: the schema's owner may drop or alter every object in it, the power `table_owner` exists to catch; the reason set stays unchanged. Axentra and Nodestro share the gap (TODO TB-035) |
| D-50 | TAB-031 | Contract 4.6 names `RoleMissing` and `RoleUnsafe` only | `UsageError("postgres.schema_missing")` for a schema that does not exist and `ValueError` for a name over 63 bytes, both before anything is granted (contract I-35) | Review L-2/L-4 of #8: a 64-byte name is truncated by the server and granted on another schema; a missing schema surfaced as a raw `InvalidSchemaName`. Additive: a new error code, no signature change |
| D-52 | TAB-018 | Specification 3.1: the SQLite `connect` event calls `tabrion.sqlite.apply_profile` | An engine from `from_url` opens every connection through `tabrion.sqlite.connect` as its `creator`; `from_engine` applies `apply_profile` on checkout | `connect` also runs the path checks, the `0600` creation, the opened-file check and `mode=ro` for each connection, which a listener on an already opened connection cannot; still one connection, no second pool (contract I-37, I-38) |
| D-53 | TAB-018 | Specification 3.1: PostgreSQL needs nothing at `reset`/`checkin` ("transaction ended = READ ONLY ended") | A `checkin` listener gives psycopg's `read_only` back to `None` after a Tabrion `read()` | SQLAlchemy resets `postgresql_readonly` to `read_only = False`, so psycopg sends `BEGIN READ WRITE` afterwards and overrides `default_transaction_read_only` of `Profile.readonly()` (contract I-42) |
| D-60 | TAB-017 | Specification section 9, contract section 7: all codes for every target, TAB-T-K01..K30 | TAB-PG-012, TAB-B-001 and TAB-B-002 (K21, K25, K26) only for a `Database`, in the layer-B collector after TAB-018 (TODO TB-060); `tabrion._sa._check` stays a stub | A raw psycopg connection does not record that a `transaction_timeout` was wanted, and the layer-B codes need a `Database`; the coordinator kept TAB-017 apart from the open layer-B pull request (contract I-55) |
| D-61 | TAB-018 | Contract 5.2 and I-37: `from_engine(engine, profile=...)` with any profile; the listener applies it to connections already in the pool | A read-only profile with `install_listeners=True` raises `ConfigurationError("config.invalid_profile", details={"field": "read_only"})` | The listener would make the host's whole pool read-only, also after `close()` (T-NOTX rule 2, plan 10; review M-2); with `install_listeners=False` the profile still refuses `transaction()` |
| D-62 | TAB-021 | Specification section 6, UUID row: "text (36 characters) in SQLite, `uuid` in PostgreSQL; SQLAlchemy `Uuid`" | TAB-T-D04 maps UUIDs to `String(36)` on SQLite and `Uuid` on PostgreSQL | SQLAlchemy's `Uuid` stores 32 hex characters without hyphens on SQLite, which is not the 36-character text of the rule; the case follows the stored format the rule names (contract I-58). Trade-off: with this mapping the application gets a `str` on SQLite and a `uuid.UUID` on PostgreSQL, so portable code needs a `TypeDecorator` over `String(36)` that converts both ways, or `Uuid().with_variant(String(36), "sqlite")` plus its own conversion of the SQLite `str`; with plain `Uuid` it gets `uuid.UUID` on both engines but 32-character text in SQLite files |
| D-63 | TAB-021 | Specification section 6, timestamp row: "in SQLite ISO 8601 text `YYYY-MM-DDTHH:MM:SS.ffffffZ`. SQLAlchemy: `DateTime(timezone=True)`" | TAB-T-D01 maps timestamps to `String(32)` holding that text on SQLite (written as UTC with `Z`) and `DateTime(timezone=True)` (`timestamptz`) on PostgreSQL | On SQLite, `DateTime(timezone=True)` stores `2024-01-02 03:04:05.000000` (space, no `Z`) and reads it back naive, dropping the offset without converting to UTC; the two halves of the row cannot both hold, so the case follows the stored format the rule names, as D-62 does for UUIDs. A test of the repository pins the pitfall (contract I-58, review L-2). The application gets a `str` on SQLite and an aware `datetime` on PostgreSQL; portable code uses a `TypeDecorator` that writes the UTC text and parses it back |
| D-66 | TB-060 | Specification section 9: TAB-B-002 "Alembic schema not current" (error) | `check(database)` reports TAB-B-002 only as "Not checked" (info) when a table `alembic_version` is visible, pointing to `tabrion.migrations.require_current` | `check()` takes no script directory (frozen API), and layer B must not import Alembic or `tabrion.migrations` (import contracts); `require_current` is the gate that raises (contract I-55) |
| D-67 | GOV-1, TAB-038, TAB-040, TAB-043, GOV-2 | ADR 0009 E-VER: V-B (PyPI) as the goal; G-T2 after the manual register check (O-3); tags `v0.1.0a1` and `v0.1.0` by the owner; branch protection as GOV-2 acceptance | Git SHA pins are the distribution, no PyPI publication planned; `v0.1.0` final is the master merge commit with package version `0.1.0`, pinned by full SHA; tags are optional labels the agent may create; G-T2 without the register check; branch protection, private vulnerability reporting and the social preview optional | Owner decision of 2026-11-08 11:26Z (plan document 00 section 13); ADR 0009 and ADR 0001 amendments of 2026-11-08. G-R per public publication unchanged |

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
