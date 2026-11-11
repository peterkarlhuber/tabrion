# TODO: tabrion

> Open work for this repository: release blockers, owner actions, the open work packages of the coming milestones,
> security follow-ups and technical debt.
> The program backlog is the source of truth for scope and acceptance criteria ([STATUS.md](program/STATUS.md),
> planning documents 09 and 14); this file is the entry point and summary. "TABRION <WP-ID>" names a work package of
> that backlog.
>
> **Last full review:** 2026-11-08 against branch `claude/project-thread-rdo6g3` (M2 lane B pull request: TABRION
> TAB-013, TAB-014, review fix round, after the M2 lane C pull request #5 merged at `b34abb2`). Lane C entries (TB-032..TB-037)
> updated 2026-11-08 on branch `claude/project-thread-rdo6g3-pg2` (TAB-030, TAB-034, after #4 merged); the TB-031 port
> (TB-050, TB-051) on branch `claude/project-thread-rdo6g3-errfix`; layer B (TB-007, TB-020, TB-040, TB-056, TB-061)
> on branch `claude/project-thread-rdo6g3-layerb` (TABRION TAB-018). Governance entries (TB-001 closed, TB-003, TB-011,
> TB-014, TB-016, new TB-067, TB-068) updated 2026-11-08 on branch `claude/project-thread-rdo6g3` for the owner decision of 2026-11-08
> 11:26Z.

## Summary

The counts are the `TB-` entries below whose status is not `Done` (recounted from the entries on 2026-11-11,
master merge into #41).

| Priority | Count |
|---|---|
| P0 Critical | 0 |
| P1 High | 10 |
| P2 Medium | 12 |
| P3 Low | 31 |
| P4 Optional | 5 |

**Release blockers:** none outside the work packages of M2. The owner decision of 2026-11-08 11:26Z removed the
trademark register check from gate G-T2 (TB-001 closed; a later public marketing under the name needs a new
assessment) and made the repository settings of TB-003 optional. Tabrion is consumed through Git SHA pins; a PyPI
publication is not planned (TB-016). Every public publication (PyPI, npm, GitHub release, announcement) still needs
the owner's approval G-R per version; there is no published version.

M1 and the G-T1 follow-up are merged: the package skeleton, the frozen API as typed stubs, CI, the import contracts,
the documentation and the program files ([STATUS.md](program/STATUS.md#g-t1-conditions)). M2 has started:
`tabrion.errors`, `tabrion.sql` and `tabrion.config` are merged (TABRION TAB-010..TAB-012, pull request #3); the M2
lane B pull request implements `tabrion.sqlite` (TABRION TAB-013, TAB-014,
[M2 lane B evidence](program/STATUS.md#m2-lane-b-evidence-tab-013-tab-014)); `tabrion.postgres` is merged up to
TAB-030 and completed by TAB-031 in the third lane C pull request; `tabrion.check` (TAB-017) is merged, without
its layer-B part (TB-060); the layer-B pull request implements the layer-B core (TABRION TAB-018,
[M2 lane B2 evidence](program/STATUS.md#m2-lane-b2-evidence-tab-018)); `tabrion.migrations` and
`tabrion.testing` still raise `NotImplementedError`.

## Priorities and statuses

- **P0 Critical**: blocks a gate or a release, or a legal or security exposure.
- **P1 High**: on the critical path of the next milestone, or blocks a consumer.
- **P2 Medium**: in scope, schedule soon.
- **P3 Low**: minor gap or debt.
- **P4 Optional**: improvement idea.

Statuses: `Open`, `In progress` (PR linked), `Blocked` (names the blocker), `Needs decision` (owner decision pending),
`Needs external review`, `Accepted risk` (deliberately not fixed; reason stated), `Optional` (useful, never a
prerequisite), `Not planned` (starts only after a new decision, for example a G-R).

## Release blockers and critical items

None open. TB-001 (manual trademark register check before G-T2) is closed: the owner did not order the check and
removed it from G-T2 (owner decision of 2026-11-08 11:26Z; [STATUS.md](program/STATUS.md#owner-actions), O-3).

## Owner actions

Things only the owner can do; the agent lacks the rights or must not do them. Mirrored in
[STATUS.md](program/STATUS.md#owner-actions). No new owner tasks are added unless they are technically or legally
required (owner decision of 2026-11-08 11:26Z).

### TB-003 · Optional repository hardening: branch protection, environment `pypi`, private vulnerability reporting

- **Priority:** P4 Optional · **Status:** Optional · **Refs:** TABRION GOV-2, TABRION GOV-3, TABRION TAB-043,
  G-R, G-T1 C-7, C-8; owner actions O-1, O-2, O-5, O-7, O-9
- **What:** by the owner decision of 2026-11-08 11:26Z these settings are useful but optional hardening, never a
  prerequisite for implementation, a merge or G-T2; the environment `pypi` is needed only if a PyPI publication is
  decided later (TB-016), and the social preview is not a blocker. Without branch protection, `ci-ok` green stays a
  merge rule that the person who merges checks (CONTRIBUTING.md). The settings: branch protection on `master`: the required status check `ci-ok` is the commit status that `ci-ok.yml`
  posts on the PR head SHA (C-7; it exists only after the follow-up pull request is merged), plus no force pushes and
  no deletion (the agent proxy gets HTTP 403); recommended `CODEOWNERS` for `/.github/**` with code-owner review. The
  same for tabrion-studio. The environment `pypi` (C-8): the owner as required reviewer, "prevent self-review",
  deployment refs restricted to tags `v*`, no secrets, and later the trusted publisher bound to `release.yml` and
  `pypi` (ADR 0009, "Release environment"). The label `workflow-change-reviewed` for the ci-ok workflow-change gate
  (set only by the owner or an independent reviewer; ADR 0012). Private vulnerability reporting switched on (SECURITY.md points to it). Upload
  `assets/branding/social-preview.png` as the repository's social preview (O-9).
- **Next step:** none required. When the owner sets one, record it in STATUS.md. Stale comments: the header of
  `.github/workflows/release.yml` still calls the environment `pypi` owner action O-2 as if it were required, and
  `.github/workflows/ci-ok.yml` says branch protection requires `ci-ok` (O-1); reword both in a later pull request
  with the label `workflow-change-reviewed` (ADR 0012).

### TB-004 · Confirm the CR-DB decisions E-CR-1..E-CR-7

- **Priority:** P2 Medium · **Status:** Needs decision · **Refs:** TABRION ARC-CR-1, TABRION STU-CR-1, G-CR
- **What:** CR-DB is part of the program; E-CR-8 (M9, M10, TAB-CR-1/2 in 0.2) is in force. The other decisions of
  planning document 14 section 9.1 (edge `nodestro-runtime[database]` to `tabrion[sqlalchemy]`, Studio 1.1 with G-S3,
  one node type, agent write rules, psycopg-binary in bundles, empty SQLite schema in exports, server defaults) have
  recommendations but no recorded confirmation.
- **Next step:** owner confirms or changes each; ARC-CR-1 records the new edge.

### TB-023 · Planning errata from G-T1 (F-17)

- **Priority:** P3 Low · **Status:** Open · **Refs:** G-T1 F-17, owner action O-8
- **What:** planning document 05 section 2.3 describes a lazy import in `tabrion.check` that import-linter would flag
  (replaced by condition C-1); planning document 02 section 9 writes `tabrion.check(target)` (G-6: the function is
  `tabrion.check.check`).
- **Next step:** the owner records both in the planning errata.

## M2: Tabrion 0.1

### TB-006 · Layer A implementation (TABRION TAB-010..TAB-017, TAB-030, TAB-031)

- **Priority:** P1 High · **Status:** In progress (TAB-010..TAB-016, TAB-030 merged; TAB-031 in the third lane C
  pull request) · **Refs:**
  TABRION TAB-010, TAB-011, TAB-012, TAB-013, TAB-014, TAB-015, TAB-016, TAB-017, TAB-030, TAB-031
- **What:** replace the stubs in order of the critical path: `tabrion.errors` (TAB-010), `tabrion.sql` (TAB-011),
  `tabrion.config` (TAB-012), `tabrion.sqlite` connection and profile (TAB-013) then transactions, `migrate`,
  `backup` and checks (TAB-014), `tabrion.postgres` configure, ping, diagnose, extension status (TAB-015), roles
  (TAB-016), `tabrion.check` and finding codes (TAB-017), `SessionLock` and advisory helpers (TAB-030),
  `grant_runtime_role` (TAB-031). Each with the TAB-T test IDs of the contract.
- **Next step:** TAB-010..TAB-012 are merged (independent review accepted at `ece7572`). TAB-013 and TAB-014 are in
  review (independent review TB-044). `test_stubs_raise_not_implemented` shrinks module by module as stubs are
  replaced (`errors`, `sql`, `config`, `sqlite` have left it); every change to a signature also runs
  `scripts/api_surface.py --write` and updates the contract. TAB-017 follows the dispatch of C-1 (contract section
  7). TAB-015 and TAB-016 are merged (contract I-14..I-20, [M2 lane C evidence](program/STATUS.md#m2-lane-c-evidence),
  independent review TB-032 accepted); TAB-030 is merged (#6, I-32, I-34); TAB-031 is in review (I-35,
  [M2 lane C3 evidence](program/STATUS.md#m2-lane-c3-evidence)) and completes `tabrion.postgres`, which has left
  `STUB_MODULES` in `tests/unit/test_public_api.py`. Layer B (TAB-018) raises its `DatabaseError` subclass through `tabrion.errors._error_class(kind)` and passes
  `sent=False` for checkout errors. TAB-017 is in review on branch `claude/project-thread-rdo6g3-check`
  (`tabrion.check` with TAB-SQ-* and TAB-PG-*, contract I-50..I-55; `tabrion.check` left the stub list); its
  layer-B part is TB-060.

### TB-007 · Layer B, migrations, testing (TABRION TAB-018..TAB-021, TAB-032, TAB-034)

- **Priority:** P1 High · **Status:** In progress (TAB-018 in review in the layer-B pull request) · **Refs:**
  TABRION TAB-018, TAB-019, TAB-020, TAB-021, TAB-032, TAB-034
- **What:** layer B core (`Database`, `Profile`, `Transaction`, `fetch_*`, TAB-018), `tabrion.migrations` (TAB-019),
  `tabrion.testing` 0.1 (TAB-020) with a database per session, roles without superuser, truncate and runtime-role
  mode (TAB-032), profile and portability cases (TAB-021). The fault-injection proxy of TAB-034 (includes QA-3) is in
  the second lane C pull request as the internal `tabrion.testing._faults` (TB-033).
- **Status note:** TAB-020 and TAB-032 are in the fourth lane C pull request (#11; contract I-36, I-60), after #8
  (TAB-031) merged. Re-review at `5110c8f`: TAB-020 accepted, TAB-032 accepted with conditions; M-1 (the condition),
  DL-1, DL-2, L-1, L-2 and the Info items are fixed in #11 (D-73); both accepted after the second re-review and merged
  in #11 (`e9da2a3`). Its non-blocking follow-ups (RR2-L1, Info) are in the TAB-022 pull request.
- **Next step:** TAB-018 is in review (contract section 5.7, I-37..I-44; it decided TB-040, TB-020 and F-14).
  TAB-019 (`tabrion.migrations`, contract I-57) is done (#14). TAB-021 (`tabrion.testing.profiles`, contract I-58,
  deviations D-62 and D-63) is done (#15) after its review fix round (M-1..M-3, L-1..L-3; follow-up TB-064).
  TAB-021 uses the case types of G-3 (revised), TAB-032 the fixture
  `tabrion_role_setup` (G-10). TAB-017 (`tabrion.check`, which `Database.check()` calls) is a pull request of its own.

### TB-008 · Consumer protection, conformance, stability policy (TABRION TAB-022, TAB-050, TAB-051)

- **Priority:** P1 High · **Status:** Blocked · **Refs:** TABRION TAB-022, TAB-050, TAB-051, AC-5
- **What:** golden files `tests/golden/` of the existing Nodestro and Axentra behavior (blocking job `golden files`)
  and the nightly `downstream` job (TAB-022); the internal "Levuvo Database Profile" with `conformance/SPEC.md`,
  vectors, manifest and the job `conformance` (TAB-050); the API stability policy and a deprecation helper
  (TAB-051).
- **Status note:** TAB-022 is in the fifth lane C pull request (on master after #11; contract I-61, D-74, D-75): the
  consumer golden files, their replay and coverage tests, `scripts/record_golden.py` and `scripts/downstream.py`.
  The CI jobs are TB-070.
- **Next step:** TAB-050 after TAB-016. TAB-051 is in review (branch `claude/project-thread-rdo6g3-tab051`; the
  workflow parts are TB-090).

### TB-009 · Quality suites and release path in this repository (TABRION QA-2, QA-4, QA-8, QA-11, QA-22)

- **Priority:** P2 Medium · **Status:** Open · **Refs:** TABRION QA-2, QA-4, QA-8, QA-11, QA-22
- **What:** the canary suite (no values in errors or logs, QA-2); concurrency and stress suite, nightly (QA-4);
  supply chain with SBOM next to the existing `dependency audit` job (QA-8); the release workflow is in place but
  its acceptance as QA-11 needs a dry run on a test tag (QA-11); conventions audit (QA-22).
- **Next step:** QA-8 and QA-11 can start now (they depend on TAB-004 and TAB-005 only).

### TB-010 · Reference applications and measurement report (TABRION RA-A-1..3, RA-B-1..4, RA-EVAL)

- **Priority:** P2 Medium · **Status:** In progress (RA-B-1 merged in #16; RA-A-1 merged in #22; RA-B-2 in review,
  #23) · **Refs:** TABRION RA-A-1, RA-A-2, RA-A-3, RA-B-1, RA-B-2, RA-B-3, RA-B-4, RA-EVAL
- **What:** the parts application (portable schema with Alembic, functions and tests on SQLite and PostgreSQL, a plain
  SQLAlchemy baseline) and the measurements application (local capture, idempotent transfer, offline and failure
  tests, baseline) under `examples/`, then the value report against plain SQLAlchemy. RA-B-1 is
  `examples/measurements` (local capture, layer A). RA-B-2 adds the idempotent transfer through layer B
  (`measurements/transfer.py`, `measurements/upstream.py`: batches by the high-water mark `seq`, `ON CONFLICT DO
  NOTHING` on the natural key, the mark moved only after the upstream commit; local migration 3 `transfer_mark`).
  The mark is not tied to an upstream identity (review L-1 of #23): `transfer --from-start` (`Store.reset_mark`) is
  the documented reset after switching or restoring the upstream; storing an upstream fingerprint with the mark
  (for example a UUID row that `init-upstream` creates) is open for RA-EVAL.
  The tests live in `tests/examples/` and run inside the normal suite: `tests/examples/conftest.py` puts each
  application on `sys.path`, and `mypy_path` in `pyproject.toml` names it for `mypy --strict src tests scripts`. The
  transfer tests run against an upstream SQLite file in the core jobs and against PostgreSQL (marker `postgres`) in
  the `pg (...)` jobs.
- **Open:** a dedicated CI job `examples`, one per application, also against PostgreSQL for RA-B-2 and RA-A-*. It is
  a workflow change that lane B does not make (`.github/**`); until then the existing jobs run the example tests.
- **Next step:** RA-A-2 (#29) and RA-A-3 (baseline); RA-B-3 (offline and failure tests with the fault proxy of
  TAB-034, #26) and RA-B-4 (plain SQLAlchemy baseline, #28) after RA-B-2 (lane H).

### TB-011 · Documentation 0.1, internal alpha and release 0.1.0 (TABRION TAB-041, TAB-038, TAB-040)

- **Priority:** P1 High · **Status:** Blocked · **Refs:** TABRION TAB-041, TAB-038, TAB-040, G-T2, RC-8
- **What:** the guides of 0.1 (TAB-041, including `docs/guides/infrastructure-baseline.md` of ADR 0001); the internal
  pre-release `v0.1.0a1` only for unmerged integration branches (TAB-038); `v0.1.0` final and gate G-T2 (TAB-040).
  `v0.1.0` final is the merge commit of the version-bump pull request that sets `0.1.0` (not a pre-release); TAB-040
  records its full SHA in CHANGELOG `[0.1.0]`, STATUS TAB-040 and `installation.md`, and the next pull request bumps
  master to `0.1.1.dev0` (the TB-053 pattern). Consumers pin it by full commit SHA via `git+https`, and a tag
  `v0.1.0` is an optional label (ADR 0009, amendment of 2026-11-08).
  No GitHub release and no PyPI upload without G-R.
- **Next step:** TAB-038 is merged (#9, `a463422`, accepted after independent review). The agent created the
  annotated tag `v0.1.0a1` on that commit in its clone; the agent's Git proxy refuses tag pushes, so it is not on
  GitHub. That is optional: consumers pin the commit SHA and nothing waits on the tag. Then TAB-041 and TAB-040.

### TB-022 · Release check: no 0.1 name is still a stub (TABRION TAB-040, G-T1 F-08)

- **Priority:** P1 High · **Status:** Open · **Refs:** TABRION TAB-040, TAB-023, G-T2, G-T1 F-08
- **What:** before the tag `v0.1.0`, a check that no name of `docs/api/surface.json` with `since` at or below the
  package version raises `NotImplementedError` (today every name does, by design). `scripts/api_surface.py` already
  keeps names with a newer `since` out of the exported surface.
- **Next step:** with TAB-040, as a mode of `scripts/api_surface.py` run by `release.yml`.

### TB-062 · Checks outside the specification's code table (TAB-017 review Info of #13; 0.2 addendum candidate)

- **Priority:** P3 Low · **Status:** Needs decision (contract addendum) · **Refs:** TABRION TAB-017, specification
  section 9, planning document 02 line 281, TB-036, contract I-50..I-55
- **What:** `tabrion.check` reports nothing for: row-level security disabled, or enabled but not forced, on tables in
  scope (an owner with `expect="owner"` bypasses it, and nothing reports it); `SECURITY DEFINER` functions without a
  fixed `search_path` (the CVE-2018-1058 pattern); extensions installed in `public` or in a schema the runtime role
  can create in; TAB-PG-022 ("a role with `pg_read_all_data` exists"), which planning document 02 names in the
  backup section (TAB-PG-020..022) but not in the code table.
- **Next step:** a 0.2 contract and specification addendum with codes and severities (owner decision), or align the
  specification by removing TAB-PG-022; do not implement before.

### TB-063 · Remaining file and host gaps of `tabrion.check` (TAB-017 review L-2, L-4 of #13)

- **Priority:** P3 Low · **Status:** Open · **Refs:** TABRION TAB-017, TAB-015, contract I-17, I-52, I-54
- **What:** TAB-SQ-003 does not report a database file owned by another user (a plain `sqlite3` connection may open
  one; `tabrion.sqlite.connect` refuses it). `tabrion.postgres.diagnose().host_is_local` (TAB-015, I-17) still looks
  at `info.host` only; `tabrion.check` uses `info.hostaddr` itself.
- **Next step:** an owner rule for TAB-SQ-003 (or a new code, by addendum); `diagnose` to prefer `info.hostaddr`
  when set (lane C, a note in I-17).

### TB-066 · `tabrion.migrations`: `version_locations` and recursive version directories (TAB-019 review L-3 of #14)

- **Priority:** P3 Low · **Status:** Open (0.2) · **Refs:** TABRION TAB-019, contract I-57
- **What:** `status` builds `ScriptDirectory(script_location)` itself and never reads `alembic.ini`, so it sees
  only `<script_location>/versions`. A project that keeps revisions elsewhere (`version_locations`, also per
  branch) or in subdirectories (`recursive_version_locations`) gets an incomplete graph: missing parents raise
  `migrations.revisions`, missing heads make the database look current or newer (`unknown`).
- **Next step:** in 0.2, keyword arguments `version_locations` and `recursive_version_locations` passed through to
  `ScriptDirectory` (a contract addendum, as `surface.json` is frozen for 0.1); until then I-57 documents the rule.

### TB-060 · Layer-B checks: `Database` targets, TAB-PG-012, TAB-B-001/002 (TABRION TAB-017, TAB-018)

- **Priority:** P1 High · **Status:** Done (merged in #17, `a9ec3b8`; review L-1 with RA-A-1) · **Refs:** TABRION TAB-017,
  TAB-018, TAB-019, contract section 7, I-44, I-55, deviations D-60, D-66, TAB-T-K21, K25, K26
- **What:** `tabrion._sa._check.collect` was a stub (`check(database)` raised `NotImplementedError`). It was to
  run the SQLite or PostgreSQL checks on a raw driver connection of the `Database` (read only, T-NOTX) and add
  TAB-B-001 (pool without `pre_ping`), TAB-B-002 (Alembic schema not current, through `tabrion.migrations`) and
  TAB-PG-012 (the `Profile` sets a `transaction_timeout`, server before 17), and `Database.check()` was to call it.
- **Done in the follow-up:** `collect` runs the dialect's checks on the pooled driver connection inside one
  `read()` and adds TAB-PG-012, TAB-B-001 and TAB-B-002 (`tests/unit/test_check_database.py`,
  `tests/postgres/test_check_layer_b.py`). TAB-B-002 stays an `info` "Not checked" when an `alembic_version` table
  exists: `check()` has no script directory and layer B never imports Alembic (D-66); `require_current` is the gate.
- **Next step:** none for 0.1 (merged; D-66 accepted by the coordinator as the 0.1 design); see the 0.2 candidate.
- **Review of #17 (L-1), done with RA-A-1:** a unit test pins the TAB-PG-012 version gate with a stubbed
  `server_version` (CI runs PostgreSQL 16 only), a PostgreSQL test pins that `expect` reaches the layer-A check, and
  I-55 says that a never-migrated database (no version table) gives no TAB-B-002.
- **0.2 addendum candidate:** TAB-B-002 as the specified `error`, through a `script_location` (and version-table
  options) for `check()`/`Database.check()`; a surface change, so a contract and specification addendum first.

## M3, M5 and M8: work in this repository

### TB-012 · Architecture checks and ecosystem documentation (TABRION QA-16, QA-17, QA-18, QA-19, QA-21, QA-23, QA-24)

- **Priority:** P2 Medium · **Status:** Open · **Refs:** TABRION QA-16, QA-17, QA-18, QA-19, QA-21, QA-23, QA-24
- **What:** the Tabrion part of QA-16 is in the M1 pull request; the orkexa, axentra and tabrion-studio parts are
  open there. Tabrion's copy of its outgoing edges (empty) for the dependency policy (QA-17); blocked-module tests per
  optional edge beyond the core (QA-18); the Tabrion rows of the clean-install matrix with pip 25.0 and the current
  pip (QA-19); `docs/api-consumers.md` as the export target of the consumers' API check (QA-21); the ecosystem
  migration documentation under `docs/ecosystem/` (QA-23) and the closing record (QA-24).
- **Next step:** QA-17 after QA-16 is done in every repository.

## M4: Tabrion 0.2

### TB-013 · Studio APIs and release 0.2.0 (TABRION TAB-033, TAB-042)

- **Priority:** P1 High · **Status:** Blocked (TAB-040) · **Refs:** TABRION TAB-033, TAB-042
- **What:** `query`/`Result`, `stream`, `cancel`, `inspector` (TAB-033), then the release 0.2.0 with the 0.2
  documentation (TAB-042). The critical path to Studio runs through these. The frozen 0.2 stubs are staged in
  `tabrion._sa._v02`; TAB-033 mixes them into `Database` and `Transaction`, exports `Result` and `Column` from
  `tabrion` and drops the `staged` keys from `surface.json` when the version becomes 0.2.
- **Next step:** after G-T2 for 0.1.

### TB-014 · Connection profile format v1 (TABRION TAB-CR-1)

- **Priority:** P1 High · **Status:** Blocked (G-T1) · **Refs:** TABRION TAB-CR-1, CR-DB, R-CR-6
- **What:** a Tabrion connection profile format v1: schema, parser, secret references, no storage (storage stays in
  Studio and Nodestro). Studio STU-006 and Nodestro NOD-CR-3 use the same format. 2 + 0.5 agent days. New public API:
  needs a contract addendum and its own G-T1 style review.
- **Next step:** the format after G-T1. The profile file extension no longer waits on a register check (owner
  decision of 2026-11-08 11:26Z); keep the name out of frozen places where a choice exists, so a later rename stays
  cheap (R-36, R-CR-6).

### TB-015 · Schema fingerprint and comparison (TABRION TAB-CR-2)

- **Priority:** P1 High · **Status:** Blocked (TAB-CR-1, TAB-033) · **Refs:** TABRION TAB-CR-2, CR-DB, NOD-CR-5
- **What:** a schema fingerprint and a comparison through `Database.inspector`; NOD-CR-5 (node `database`) depends on
  it. 1.5 + 0.5 agent days. New public API, like TB-014.
- **Next step:** after TAB-CR-1 and the inspector of TAB-033.

### TB-016 · PyPI path (TABRION TAB-043), not planned

- **Priority:** P4 Optional · **Status:** Not planned (G-R) · **Refs:** TABRION
  TAB-043, E-PyPI, G-R, TB-003
- **What:** trusted publisher and `0.1.0rc1` on PyPI. Not needed now (owner decision of 2026-11-08 11:26Z): the
  consumers pin Git SHAs or use internal packages, and their CI installs Tabrion from the Git SHA pin. Every public
  release needs its own G-R. The `publish` job of `release.yml` fails on purpose until then.
- **Next step:** none, unless the owner decides a publication; then G-R for that version, the environment `pypi`
  (TB-003) and, before public marketing under the name, a new name assessment.

## Tests and quality assurance

### TB-067 · Negative case "library without the Tabrion pin" in the clean-install matrix (TABRION QA-19, R-24)

- **Priority:** P2 Medium · **Status:** Open · **Refs:** TABRION QA-19, AC-4, R-24, ADR 0009 amendment of
  2026-11-08, review M-2 of #20
- **What:** no PyPI publication is planned, so the name `tabrion` stays unclaimed on PyPI. A library such as axentra
  or orkexa declares `tabrion[...]>=0.1,<0.2`; installed without `-r requirements/tabrion.txt` or the application's
  direct-URL pin, pip asks the index for `tabrion`, which a third party could register (dependency confusion).
- **Next step:** with QA-19, add the negative case "library without the pin fails, never resolves from an index"
  (an index that offers a fake `tabrion` 0.1.x must not be used), and state in each consumer's README that the pin
  is always supplied; internal installs use `--no-index` or an internal index.

### TB-068 · AC-6 checks the version at the pinned SHA (TABRION QA-15, RC-8)

- **Priority:** P2 Medium · **Status:** Open · **Refs:** TABRION QA-15, AC-6, RC-8, TAB-040, review M-1 of #20
- **What:** pip accepts a direct-URL pin on a commit that builds as `0.1.1.dev0` or `0.1.1rc1` against
  `>=0.1,<0.2` (it rejects only versions outside the bounds), so pip alone does not enforce "SHA pin of a
  final-version commit".
- **Next step:** with QA-15, AC-6 reads the package version at the pinned SHA and refuses any SHA that is not a
  recorded release commit (CHANGELOG, STATUS TAB-040); negative tests with a `.dev0` and an `rc` commit.

### TB-017 · CI jobs that arrive with later work packages

- **Priority:** P3 Low · **Status:** Open · **Refs:** TABRION TAB-022, TAB-034, TAB-050, QA-4, RA-A-1, RA-B-1;
  deviation D-2
- **What:** `conformance`, `golden files` and `examples` in `ci.yml` (`api surface` arrived with TAB-023); fault injection, stress and
  `downstream` in `nightly.yml`. Each is added with its work package and listed in `.github/ci-manifest.json`.
- **Next step:** with each work package.

### TB-090 · Workflow steps of the API stability policy (TABRION TAB-051, deviation D-90)

- **Priority:** P2 Medium · **Status:** Open · **Refs:** TABRION TAB-051, S-6, S-8, contract I-81
- **What:** the job `api surface` in `ci.yml` runs `scripts/api_surface.py` without `--base`, so a surface change
  without a CHANGELOG entry fails only when a reviewer runs `--base origin/master` (the job needs
  `fetch-depth: 0` and `--base ${{ github.event.pull_request.base.sha }}`). `release.yml` has no gate that runs
  `--base <previous release SHA>` and checks the `downstream` result (S-7). The downstream issues of the release
  flow stay manual: the owner, or the coordinator on the owner's go, opens them (`docs/development/api-stability.md`).
- **Next step:** a separate, reviewed pull request for `.github/workflows/ci.yml` and `release.yml`; until then the
  release checklist runs both steps by hand.

### TB-091 · Error codes in `surface.json` (TABRION TAB-051, deviation D-91)

- **Priority:** P3 Low · **Status:** Open · **Refs:** TABRION TAB-051, TAB-023, S-2, S-6
- **What:** S-2 makes `TabrionError.code` values stable and S-6 wants `api_surface.py` to compare them, but the
  codes are string literals at the raise sites and not in `surface.json`.
- **Next step:** decide with TAB-040 whether to collect the codes (for example from the contract tables or an
  internal registry) into a `codes` section of `surface.json`.

### TB-018 · PostgreSQL matrix cost on pull requests

- **Priority:** P3 Low · **Status:** Open · **Refs:** deviation D-3
- **What:** all eight PostgreSQL jobs (16, 17, 18, pgvector 17 on Python 3.11 and 3.14) run on every pull request,
  following planning document 05; planning document 07 runs 16 and 18 only nightly. Cheap while the suite is small.
- **Next step:** revisit when the PostgreSQL suite of M2 makes the jobs slow.

### TB-020 · Export `Statement` and `Params`, or make them documentation-only (G-T1 F-11)

- **Priority:** P3 Low · **Status:** Done (decided in TABRION TAB-018) · **Refs:** G-T1 F-11, G-5, TABRION TAB-018
- **What:** both aliases appear in public layer-B signatures and in `tabrion._sa.database.__all__`, but not in
  `tabrion.__all__`, against the G-5 rule.
- **Next step:** none. TAB-018 keeps them documentation-only aliases and drops them from
  `tabrion._sa.database.__all__`; `surface.json` is unchanged (contract I-37).

### TB-021 · Platform path filters too narrow (G-T1 F-15, CC-3)

- **Priority:** P3 Low · **Status:** Open · **Refs:** G-T1 F-15, CC-3, TABRION TAB-004
- **What:** macOS and Windows run on pull requests only when `sqlite.py` or `tests/sqlite/**` change; `config.py`,
  `errors.py`, `testing/sqlite.py` and `pyproject.toml` also affect those platforms, and so do the Windows-specific
  paths of `scripts/downstream.py`, `tests/unit/test_downstream.py` and `tests/unit/test_golden_consumers.py`
  (TB-134: those failed on Windows in the nightly only).
- **Next step:** add the paths to `platforms.yml` and to the manifest's `path_filters` in one pull request merged
  before the change that needs them (ci-ok judges by the default branch's manifest).

### TB-134 · Windows: downstream runner and golden replay failed in the platforms job (issue #18)

- **Priority:** P2 Medium · **Status:** Done · **Refs:** issue #18, nightly run 42809181374, TAB-022, TB-021
- **What:** eight Windows failures besides the two of TB-074 (#37):
  - six `tests/unit/test_downstream.py` installs: `uv pip install --python <venv>\Scripts\python` exits 2 ("No
    virtual environment or system Python installation found"). `subprocess` starts `Scripts\python` because
    `CreateProcess` adds `.exe`, but uv takes the path as given. A real bug of `scripts/downstream.py` on Windows,
    in `run_consumer` as well, not only in the tests. Fixed: `downstream.venv_python()` returns
    `Scripts\python.exe` on Windows, used by the script and the tests; `test_venv_python_is_the_interpreter_file`
    pins it (the file must exist).
  - `test_a_database_error_is_a_setup_failure_without_its_message`: Windows retries a refused local connect until
    `connect_timeout`, so psycopg raises `ConnectionTimeout`, a subclass of `OperationalError`. The script's report
    was right (class name, no message); the test now asserts the cause is an `OperationalError`, exactly that class on
    POSIX and that class or `ConnectionTimeout` on Windows, and the exact report text.
  - `test_golden_consumers.py::test_orkexa_open` compared the golden file, recorded on Linux, with the current
    platform's mode. The golden file holds `0o600` everywhere; the replay records `None` on Windows (no POSIX mode
    bits), which the test now asserts explicitly.
- **Next step:** none. The platforms job runs these files on pull requests only once TB-021 widens its path filter;
  the fix was proven by a `workflow_dispatch` run of `platforms.yml` on the fix branch.

### TB-025 · Localized server messages are not tested in CI (TABRION TAB-010, TAB-T-E15)

- **Priority:** P3 Low · **Status:** Open · **Refs:** TABRION TAB-010, TAB-T-E15, R-T02
- **What:** `test_e15_localized_messages` sets `lc_messages = 'de_DE.UTF-8'` on the server and skips when the server
  has no such locale, which is the case for the PostgreSQL images of the CI jobs and for the local Ubuntu server. The
  unit test `test_e15_localized_cancel_message_is_canceled` covers the classification with a German message.
- **Next step:** with TAB-034 or QA-4, build or select an image with the `de_DE` locale for one PostgreSQL job.

### TB-027 · Statement splitter limitations (TABRION TAB-011)

- **Priority:** P3 Low · **Status:** Accepted risk · **Refs:** TABRION TAB-011, contract I-9
- **What:** like Axentra's splitter, `split_statements` does not track parentheses (`CREATE RULE ... DO (...; ...)`)
  or `BEGIN ATOMIC ... END` function bodies (PostgreSQL 14), and SQLite trigger bodies (`BEGIN ...; END`) are split.
  Byte identity with Axentra rules out changing it in 0.1; no Axentra migration uses these forms.
- **Reason for accepting:** such statements can be written with a dollar-quoted body or run on their own. Revisit
  for the Studio editor (TAB-033) if it needs them.

### TB-028 · Splitter: `E'...'` after a dollar quote or in a string continuation (review L-4)

- **Priority:** P3 Low · **Status:** Open · **Refs:** TABRION TAB-011, TAB-T-Q04, contract I-9, review L-4
- **What:** `$$a$$E'\'; DROP TABLE x; --'` is one statement for PostgreSQL but three for `split_statements`: the
  look-behind for the `E` prefix sees the closing `$` of the dollar quote as an identifier character. In
  `E'a'\n'b\'; x'` PostgreSQL continues the escape mode into the second segment and Tabrion does not. Axentra's
  splitter is equally wrong and none of its SQL uses either form, so byte identity is not at stake.
- **Next step:** decide the `E` prefix in the identifier branch (a word that is exactly `E` or `e` followed by `'`),
  carry the escape mode across `'...'<newline>'...'` continuations, add both to Q04 and to contract I-9.

### TB-029 · `safe_url`: percent-encoded `password` key, non-ASCII white space in conninfo (review L-5)

- **Priority:** P3 Low · **Status:** Open · **Refs:** TABRION TAB-012, contract I-10, review L-5
- **What:** `postgresql://h/db?pass%77ord=secret` is not masked although libpq percent-decodes query keys; in
  keyword conninfo `password=se\u00a0x=y` gives `password=*** x=y` because `str.isspace` treats NBSP as a separator
  and libpq does not.
- **Next step:** percent-decode query keys before comparing them; use ASCII white space (`" \t\n\r\f\v"`) in
  `_mask_conninfo`; add both to C02/C03 and the "password never survives" property.

### TB-030 · `tabrion.errors` follow-ups from the review Info items (I-3, I-5)

- **Priority:** P3 Low · **Status:** Open · **Refs:** TABRION TAB-010, review I-3, I-5
- **What:** (I-3) `tabrion.errors` imports `sqlite3` at module level, so a Python build without `_sqlite3` cannot
  import it, even for PostgreSQL-only consumers; a guarded import would help. (I-5, rest) the redaction chain walk
  does not follow `TabrionError.original`, so a wrapper that quotes `err.original` (for example after unpickling) is
  not scrubbed. `classify` already returns a `TabrionError`'s own classification (fix round).
- **Next step:** guard the import (`sqlite3 = None` and skip SQLite recognition) with an isolation test; follow
  `original` in the filter's chain walk with a canary test.

### TB-032 · Independent review of TAB-015 and TAB-016 (planning document 07 section 6)

- **Priority:** P1 High · **Status:** Done (accepted after the delta re-review of 4be19b2; merged as b34abb2) · **Refs:** TABRION TAB-015, TAB-016, T-NOTX, R-SET, TM-07, TM-08, SR-02, SR-10, planning folder
  `reviews/tab-015-016-review.md`
- **What:** the review model requires an independent review agent for TAB packages in `postgres` (roles, locks);
  `configure` and `session_configurator` sit next to R-SET (settings on shared pools, M10). The M2 lane C pull request
  carries the self-review ([M2 lane C evidence](program/STATUS.md#m2-lane-c-evidence)).
- **Result so far:** the review of e0060a6 was "not accepted": H-1 (roles reachable with `SET ROLE` reported safe),
  M-1 (a failing reader aborted the caller's transaction), M-2 (unqualified types and operators). The fix round in the
  same pull request implements H-1 as decided by the owner (contract I-20, D-32), M-1 with a savepoint (I-15, I-16,
  D-33), M-2 by qualifying every type and operator, L-2 (NOINHERIT member test) and I-1 (ping without a savepoint in
  an aborted transaction); L-1 is documented in I-14 and I-16; I-2..I-4 are in TB-034, TB-036 and TB-037.
- **Delta re-review:** accepted; one new Low, DL-1 (in psycopg pipeline mode no savepoint was opened, so a failing
  reader aborted the caller's pipeline transaction). Fixed in the second lane C pull request (contract I-34, test
  `test_dl1_*`, a mutation probe).
- **TAB-030/TAB-034:** the independent review of #6 at 0cd79eb was "accepted with conditions" (M-1: `ping` in
  pipeline mode swallowed the caller's queued error; L-1..L-4, Info I-1..I-5); all fixed or documented in the same
  pull request ([M2 lane C2 evidence](program/STATUS.md#m2-lane-c2-evidence)), together with DL-2 of #4.
- **Next step:** confirmation of the fix round by the reviewer.

### TB-033 · Public `tabrion.testing.faults`: a 0.2 surface decision (TABRION TAB-034)

- **Priority:** P3 Low · **Status:** Decided for 0.1 (internal module); open for 0.2 · **Refs:** TABRION TAB-034,
  QA-3, RA-B-3, MIG-T-AXE-8, `docs/api/surface.json`, contract I-33
- **What:** planning documents 07 (K7: `tabrion.testing.faults.DropReplyProxy`) and 09 (TAB-034) made the
  fault-injection proxy a public module, while the frozen contract has no `tabrion.testing.faults`. Decision
  (coordinator, 2026-11-08): do not extend the frozen surface now. TAB-034 ships the proxy as the internal, unstable
  `tabrion.testing._faults` (not in `surface.json`, not exported), used by Tabrion's own tests.
- **Next step:** making it public (for RA-B-3 and Axentra's MIG-T-AXE-8) is a 0.2 surface decision: a contract
  addendum with `surface.json` entries and a review.

### TB-034 · `ping` has no client-side deadline (TABRION TAB-015)

- **Priority:** P3 Low · **Status:** Accepted risk · **Refs:** TABRION TAB-015, contract I-14, SECURITY.md
- **What:** `ping` bounds the server's work (`SET LOCAL statement_timeout`) and a pool checkout, but a network that
  stops answering mid-statement blocks until TCP gives up, because psycopg has no client-side statement deadline.
- **Reason for accepting:** the contract defines the limit on the server; callers that need a hard deadline run the
  check in a thread (as Nodestro's `DatabaseHealth` does) and set TCP keepalives or `tcp_user_timeout`. Revisit with
  the delay mode of TAB-034. The same holds for a connection another thread is using: `ping` waits for psycopg's
  connection lock without a bound, which `timeout` does not cover (review I-2; SECURITY.md).

### TB-035 · Axentra and Nodestro role checks miss roles reachable with `SET ROLE` (review H-1)

- **Priority:** P1 High · **Status:** Open · **Refs:** TABRION TAB-016, MIG-AXE, MIG-NOD, contract I-20, D-32,
  planning folder `reviews/tab-015-016-review.md` H-1
- **What:** Axentra (`data/database.py:359-405` `role_info`/start check, `data/roles.py:72-104` `inspect_role`) and
  Nodestro (`storage_postgres.py:757-768` `role_info`, `947-984` `inspect_role`) read `rolsuper` and `rolbypassrls`
  of `current_user` (or the named role) only. A login role that is a member of a superuser or BYPASSRLS role
  (INHERIT or NOINHERIT), and a superuser session after `SET ROLE app`, pass their start checks; row-level security
  then does not protect against SQL that runs as that role. Tabrion's fix (I-20) shows the gap: TAB-T-R19 asserts
  that the reference queries miss it. Their `grant_runtime_role` functions share it: TAB-T-R14 asserts that both
  grant a NOINHERIT member of a SUPERUSER or BYPASSRLS role and of another table's owner, and Axentra's grants a
  member of the owner in an empty schema (D-46). Both also grant a role that owns the target schema, or can become
  its owner (NOINHERIT too), which may then drop every table in it (review M-1 of #8, D-49; asserted in TAB-T-R14),
  and grant on a different schema when a 64-byte schema name is truncated by the server (review L-2, D-50).
- **Next step:** fix both in their MIG waves (MIG-AXE, MIG-NOD) by moving to `tabrion.postgres.check_role` and
  `inspect_role`, or earlier with the membership query of I-20 and the R19/R20 cases in their own suites.

### TB-036 · Superuser-equivalent predefined roles and `CREATEROLE` are not reported (review I-4)

- **Priority:** P2 Medium · **Status:** Open · **Refs:** TABRION TAB-016, TAB-017, contract I-20, review H-1
  (optional part), I-4
- **What:** membership of `pg_execute_server_program`, `pg_write_server_files` or `pg_read_server_files`, and
  `CREATEROLE` on servers before 16, are ways to superuser-equivalent power that `check_role` does not name.
  `pg_read_all_data` and `pg_write_all_data` do not bypass row-level security. A new reason code would extend the
  frozen reason set (a contract addendum); a finding of `tabrion.check` (TAB-017) would not.
- **Next step:** decide with TAB-017: a `tabrion.check` finding for these memberships (preferred, no API change), or a
  reason code in 0.2 by contract addendum. TAB-017 implements only the codes of specification section 9: a new
  code is a specification and contract addendum (owner decision); until then TAB-PG-001 covers only roles with
  `rolsuper` (I-20, I-53).

### TB-037 · Review follow-ups L-1 and I-3 of TAB-015/TAB-016

- **Priority:** P3 Low · **Status:** Open · **Refs:** TABRION TAB-015, contract I-14, I-16, I-17, review L-1, I-3
- **What:** (L-1) a reader or `ping` that runs first in a caller's fresh transaction takes its snapshot; a later
  `SET TRANSACTION ISOLATION LEVEL` fails and REPEATABLE READ sees the data as of the check. Documented in I-14 and
  I-16; nothing in T-NOTX forbids it. (I-3) `diagnose._milliseconds` raises `ValueError` on a time format it does not
  know; reached only without `pg_settings`, where `current_setting` always prints one unit.
- **Next step:** with layer B (TAB-018), recommend calling the checks outside a transaction in the guide; make
  `_milliseconds` return `None` for an unknown format if a server ever prints one.

### TB-039 · Redaction: each record is formatted twice (review N-3)

- **Priority:** P3 Low · **Status:** Open · **Refs:** TABRION TAB-010, review N-3
- **What:** the filter calls `record.getMessage()` for every record, and the formatter formats it again, so argument
  `__str__` methods run twice. The `islice` part (containers are no longer copied in full) is done in the M2 lane B
  pull request.
- **Next step:** a fast path for records whose arguments are only `str`, `int`, `float` and `None` and whose
  template has no marker; freeze a clean expensive message (`record.msg, record.args = message, None`); document the
  double `__str__` call.

### TB-040 · `classify(tabrion_error, during_commit=True)` ignores the flags (review N-4; before TAB-018)

- **Priority:** P2 Medium · **Status:** Done (TABRION TAB-018) · **Refs:** TABRION TAB-010, TAB-018,
  R-NORETRY, contract I-1, I-13, review N-4
- **What:** `classify` returns a `TabrionError`'s stored classification unchanged, ignoring `sent` and
  `during_commit`: a `ConnectionFailed` classified at checkout (`sent=False`, retryable) stays retryable for
  `classify(err, during_commit=True)`. Nothing builds such errors before layer B.
- **Next step:** none. TAB-018 applies R-NORETRY on top of a stored classification for `during_commit=True` (kind
  CONNECTION becomes `outcome_unknown`, not retryable); `sent` is not applied, its default cannot tell a deliberate
  call (contract I-43, `test_tb040_during_commit_overrides_a_stored_classification`, mutation probe).

### TB-041 · Equivalent mutant of the `msg` replacement (review N-6)

- **Priority:** P4 Optional · **Status:** Open · **Refs:** TABRION TAB-010, review N-6
- **What:** dropping the replacement of a database exception passed as `msg` survives every test, because the
  formatted-message scrub replaces its text anyway.
- **Next step:** optionally assert that `record.msg` is the safe message, which kills the mutant.

### TB-042 · Inline `DETAIL:` with a single space is not stripped (review N-7)

- **Priority:** P4 Optional · **Status:** Open · **Refs:** TABRION TAB-010, contract I-6, review N-7
- **What:** the inline alternative of `_SERVER_TEXT_START` needs two spaces after `DETAIL:`; text rewritten to single
  spaces (`str(e).replace("\n", " ").replace("  ", " ")`) keeps the value. It needs a deliberate rewrite; the same
  text with a newline is caught.
- **Next step:** accept `\s+` after a sentence end (`." DETAIL:`), or document it as a residual.

### TB-043 · Parent directory and side-file checks for SQLite files (07 TM-03, TABRION TAB-017)

- **Priority:** P2 Medium · **Status:** Open · **Refs:** TABRION TAB-013, TAB-017, TM-03, TAB-SQ-003, TAB-SQ-004,
  contract I-22, SECURITY.md
- **What:** planning document 07 TM-03 asks for a warning when the database's directory is world-writable without
  the sticky bit. Since the review fix round (M-2) `diagnose` logs a WARNING on `tabrion.sqlite` for it; the frozen
  `SQLiteDiagnosis` has no field (deviation D-40). SQLite opens `-wal`/`-shm`/`-journal` by name after `connect`
  checked them, so such a directory is the remaining way to redirect them (SECURITY.md).
- **Next step:** TAB-017 checks the directory of `SQLiteDiagnosis.path` itself and reports a finding (a new code
  next to TAB-SQ-003/004 needs a contract addendum); 0.2 may add a `directory_unsafe` field to `SQLiteDiagnosis`
  by contract addendum. Since #4 re-review DL-1 the warning also covers a group- or other-writable directory with
  the sticky bit ("other users can create files next to the database"): in `/tmp` another user can plant the
  `-journal` that SQLite then writes page data into; the finding must cover both cases. TAB-017 implements only
  the codes of specification section 9 (side files: TAB-SQ-003 and -004, contract I-52); the directory finding
  still needs the addendum for its code.

### TB-044 · Independent review of TAB-013 and TAB-014 (planning document 07 section 6)

- **Priority:** P1 High · **Status:** In progress (fix round in pull request #4) · **Refs:** TABRION TAB-013, TAB-014,
  B-06-3, B-06-5, MIG-NOD-6, `reviews/tab-013-014-review.md`
- **What:** the review model requires an independent review agent for TAB packages in `sqlite` (file permissions,
  transactions). The review at `8e78e9c` is ACCEPTED WITH CONDITIONS (M-1..M-3); the fix round fixes M-1..M-3 and
  L-1..L-3 in the same pull request ([STATUS.md](program/STATUS.md#review-fix-round-tab-013-tab-014)); the Info
  items are TB-046..TB-049.
- **Next step:** delta re-review of the fix round, then merge.

### TB-045 · SQLite open race: descriptor identity check and randomized race test (TAB-T-123, QA-4)

- **Priority:** P3 Low · **Status:** In progress (hardening in the TB-045 pull request; the randomized test is
  open) · **Refs:** TABRION TAB-013, QA-4, TM-04, TAB-T-123, contract I-22
- **What:** planning document 07 asks for a race test with 1,000 repetitions (a link swapped in between the check
  and the open, 0 hits). The pull request tests every window deterministically through seams (TAB-T-S07: before
  creation, after the check, while SQLite opens and swapped back, an inode swap); a randomized repetition test would
  be timing-dependent on pull requests. A time-of-check to time-of-use window remained inside SQLite's own open,
  which no check of the name sees: on POSIX `connect` now verifies that the descriptor SQLite opened is the checked
  file (I-22 (e), `test_tb045_*` with mutation probes), and the last name check resolves the directory only. On
  Windows the name checks remain the only check (documented and tested as such).
- **Next step:** add the randomized race test to the nightly stress job with QA-4; then close.

### TB-046 · ATTACH on a read-only SQLite connection creates an empty file (review I-1)

- **Priority:** P3 Low · **Status:** Open · **Refs:** TABRION TAB-013, contract I-22, review I-1
- **What:** `ATTACH 'other.db'` on a `connect(read_only=True)` connection creates an empty file at the attached
  path. `query_only` blocks writes into it, and the main file stays byte-identical, but the file appears.
- **Next step:** set `SQLITE_LIMIT_ATTACHED` to 0 for read-only connections (`Connection.setlimit`, Python 3.11+), or
  document it in I-22; decide with TAB-017. TAB-017 has no code for it (the specification lists none), so the fix
  stays in `tabrion.sqlite.connect`. Layer B has the same gap inside `read()` (contract I-56, TAB-018 review
  L-1: the file gets the process umask, not `0600`); the same limit would close both. Since the second TAB-018 fix
  round the layer-B creator sets `SQLITE_LIMIT_ATTACHED` to 0 under `Profile.read_only` (I-56, re-review N-2);
  `tabrion.sqlite.connect(read_only=True)` itself still allows `ATTACH`.

### TB-047 · Windows: file modes are cosmetic, the `CREATE_NEW` race remains (review I-2)

- **Priority:** P3 Low · **Status:** Open (accepted for M2) · **Refs:** TABRION TAB-013, TAB-T-S12, contract I-22,
  SECURITY.md, review I-2
- **What:** Windows does not enforce 0600 (ACLs are inherited from the directory), and between the `_is_link` check
  and `CREATE_NEW` (which follows a dangling link) a link can still be planted.
- **Next step:** with S4 (Desktop), set an owner-only ACL on new files (`SetSecurityInfo`) and create through
  `CreateFileW` with `FILE_FLAG_OPEN_REPARSE_POINT`; both need `ctypes` code in the core, so an ADR first.

### TB-048 · `loading_extensions` cannot restore an enabled state (review I-3)

- **Priority:** P4 Optional · **Status:** Open (documented) · **Refs:** TABRION TAB-013, contract I-23, review I-3
- **What:** the block always disables extension loading on exit, even when the caller had enabled it before;
  `sqlite3` has no getter. Documented in the docstring and I-23 in the fix round.
- **Next step:** none unless a consumer needs it; then track the state on the connection in a `WeakKeyDictionary`.

### TB-049 · Backup tests have no explicit timeout (review L-3, mutant 8; I-4)

- **Priority:** P3 Low · **Status:** Open · **Refs:** TABRION TAB-014, QA-4, review L-3, I-4
- **What:** removing the `in_transaction` guard of `backup` makes the backup block on its own lock: the suite hangs
  instead of failing (the review counts it as caught, CI times out). `pytest-timeout` is not a development
  dependency. (I-4: review N-3, the double formatting cost, stays TB-039.)
- **Next step:** add `pytest-timeout` with QA-4 and mark the backup and migration tests with a timeout.

### TB-050 · Redacting `Handler.handleError` needs a public install function (Axentra review F-2)

- **Priority:** P2 Medium · **Status:** Needs decision (contract addendum) · **Refs:** TABRION TAB-010, contract
  I-6, SECURITY.md, `axe-tb031-review.md` F-2
- **What:** when a formatter or a handler's `emit` fails while a database error is handled, the standard
  `Handler.handleError` prints the failure's chain with that error's `DETAIL` to stderr. The filter covers the
  broken-call case (it rewrites the call); the other failures need `handleError` replaced process-wide, as Axentra's
  `install_db_error_redaction` does. Tabrion installs nothing and has no install function, and a new public name is
  a surface change.
- **Next step:** decide for 0.2: a staged `tabrion.errors.install_redaction()` (record factory, `handleError`,
  optional `makeRecord` for `extra=`) by contract addendum, or keep it documented (`logging.raiseExceptions =
  False`); MIG-AXE-3 needs the answer before Axentra drops its own install function.

### TB-051 · Public structlog processor for native structlog loggers (Axentra review D-2)

- **Priority:** P3 Low · **Status:** Needs decision (contract addendum) · **Refs:** TABRION TAB-010, contract I-6,
  `axe-tb031-review.md` D-2, R2-1
- **What:** native structlog loggers render `exc_info` in their own processor chain, which the logging filter never
  sees. The port has the processor as the internal `tabrion.errors._redact_db_errors_processor` (duck-typed, tested,
  including the R2-1 fallback); Axentra exports it as `redact_db_errors_processor`. Exporting it extends the frozen
  surface.
- **Next step:** decide with TB-050 whether 0.2 exports it (contract addendum, `surface.json`); until then consumers
  that use native structlog keep Axentra's processor or render no tracebacks.

### TB-024 · Circular import of the staged 0.2 mixins (G-T1 condition check CC-2)

- **Priority:** P2 Medium · **Status:** Open · **Refs:** CC-2, TABRION TAB-033, TB-013
- **What:** `tabrion._sa._v02` imports `Isolation`, `Statement` and `Transaction` from `tabrion._sa.database`. When
  TAB-033 lets `Database` and `Transaction` inherit from the `_v02` mixins, the two modules import each other.
- **Next step:** before TAB-033, move the type aliases to `tabrion._sa._types`, type `run_transaction`'s `fn` against
  a forward reference, and keep `surface.json` unchanged.

### TB-061 · Read-only is a guard, not a boundary: optional hardening (TABRION TAB-018 review H-1)

- **Priority:** P3 Low · **Status:** Open · **Refs:** TABRION TAB-018, contract I-56, SECURITY.md, plan 03:245-247,
  07 SQ-05, SR-03
- **What:** the read-back before `COMMIT` (I-56) sees the state at the end of a `read()` only. SQL inside `read()`
  that lifts read-only, writes and restores it (`SET TRANSACTION READ WRITE; INSERT ...; SET transaction_read_only =
  on`, `set_config(..., true)`, `SET LOCAL default_transaction_read_only = on`, SQLite `PRAGMA query_only = OFF;
  ...; PRAGMA query_only = ON` under a writable profile), or that ends the transaction and commits its own
  (`ROLLBACK; BEGIN READ WRITE; ...; COMMIT`), writes, on both profiles, and can leave
  `default_transaction_read_only = off` on a pooled connection until the next `read()` invalidates it. Pinned by
  `test_b11_documents_the_gap_lift_write_restore`, `test_b12_documents_the_gap_lift_write_restore`,
  `test_b12_documents_the_gap_session_default_restored_locally` and
  `test_b12_documents_the_gap_transaction_control_in_read`. A server cannot refuse transaction control; the hard
  boundary is a role without write rights (or SQLite file permissions).
- **Next step:** Studio and TAB-032 (runtime-role mode) run untrusted SQL only under such a role. Optional: a
  `checkin` check of `default_transaction_read_only` under `Profile.read_only` that invalidates the connection, and
  a snapshot-taking statement after `BEGIN READ ONLY` (review H-1); decide with TAB-041.

### TB-056 · Layer-B waits outside `timeout` (TABRION TAB-018, TAB-041)

- **Priority:** P3 Low · **Status:** Open · **Refs:** TABRION TAB-018, TAB-041, contract I-40, I-44
- **What:** a statement `timeout` on SQLite counts VM steps, so a wait for a lock is bounded by the busy timeout, not
  by `timeout`; `Database.health(timeout=...)` waits for a pool connection up to `pool_timeout`, not `timeout`.
- **Next step:** document both in the layer-B guide (TAB-041); decide for 0.2 whether `health` checks out with
  `min(timeout, pool_timeout)` (needs no API change).

### TB-065 · F05 refusal for connection factories other than Tabrion's (TABRION TAB-021)

- **Priority:** P3 Low · **Status:** Open · **Refs:** TABRION TAB-021, contract I-58, contract 04 section 6, planning
  folder `reviews/tab-021-rereview.md` (R-1)
- **What:** TAB-T-F05 counts only `UnsafePath("sqlite.symlink")` as a refusal, so an independent connect function
  (for example the Orkexa core) that refuses every link with its own exception fails F05, although the contract says
  the cases work for any connection factory. The strictness keeps sqlite3's own `OperationalError` from passing.
- **Next step:** let the caller name the refusal exception (`sqlite_profile_cases(refusal=...)`, a 0.2 addendum
  candidate), or record a deviation before MIG-ORK uses the cases.

### TB-064 · Portable UUID and timestamp columns in the guides (TABRION TAB-021, TAB-041)

- **Priority:** P3 Low · **Status:** Open · **Refs:** TABRION TAB-021, TAB-041, contract I-58, deviations D-62 and
  D-63, planning folder `reviews/tab-021-review.md` (L-1, L-2)
- **What:** the SQLite mappings of TAB-T-D01 and D04 follow the stored formats of specification section 6 (UTC text
  with `Z`, 36-character UUID text), so an application gets a `str` on SQLite and a `datetime` or `uuid.UUID` on
  PostgreSQL. On SQLite, the cases D01, D02, D04, D07 and D09 document the mapping and cannot fail for an engine
  setting; the SQLite pitfalls behind D01 and D02 are pinned by repository tests only.
- **Next step:** the layer-B guide (TAB-041) shows a `TypeDecorator` for each of the two columns (UTC text, UUID
  text) and says which portability cases check the engine; Tabrion ships no type library (specification section 6),
  so the decorators stay examples unless a reference application needs them in the package.

### TB-052 · MIG-NOD note: one `lock_lost` in the scheduler pattern after a loss (delta re-review of #6)

- **Priority:** P3 Low · **Status:** Open · **Refs:** TABRION TAB-030, MIG-NOD, contract I-32, D-43, planning
  folder `reviews/tab-030-034-review.md` (delta re-review)
- **What:** Nodestro's scheduler pattern (`jobs.py:933`: `held and holds(K)`, else `try_acquire(K)`) keeps working
  after a lost lock connection, with one narrow window: when the connection dies between `holds(K)` returning `True`
  and the next `try_acquire(K)`, that `try_acquire` raises `UsageError("postgres.lock_lost")` once (the loss was not
  acknowledged yet); the next round recovers. A consumer that expects a bool there catches `UsageError` with that
  code, or calls `holds(K)` once more.
- **Next step:** in the MIG-NOD wave, when Nodestro moves to `tabrion.postgres.SessionLock`: catch the code in the
  scheduler loop and add a test for the window.

### TB-054 · `grant_runtime_role`: the checks run before the grant transaction (review I-1 of #8)

- **Priority:** P3 Low · **Status:** Open · **Refs:** TABRION TAB-031, contract I-35, planning folder
  `reviews/tab-031-review.md` (I-1)
- **What:** the refusal checks read the catalog in `inspect_role`'s and an own read transaction; the grants run in a
  later one. A membership granted in between (for example `GRANT superuser_role TO runtime`) is not seen. Whoever can
  do that already has the power, so it is no escalation; running the checks inside the grant transaction would make
  the guarantee exact.
- **Next step:** decide with the next change of `grant_runtime_role` (a contract note for the order of statements).

### TB-055 · Complete the dogfooding of `tabrion.testing` (TABRION TAB-020, TAB-032)

- **Priority:** P3 Low · **Status:** Open · **Refs:** TABRION TAB-020, TAB-032, contract section 6, I-36, D-51
- **What:** the PostgreSQL suites use the plugin's URL and schema fixtures; `make_role` and `scratch_database` in
  `tests/postgres/conftest.py` stay local, and the SQLite suites use `tmp_path` instead of `tabrion_sqlite_path`.
- **Next step:** the fixtures exist now (TAB-032, #11); move `make_role` and `scratch_database` onto
  `tabrion_postgres_roles` and `tabrion_postgres_database` where the cases allow it (most role cases need several
  roles per test with chosen attributes, which the session fixtures do not give); the SQLite suites follow when
  lane B next touches them.

### TB-070 · CI jobs `golden files` and nightly `downstream` (TABRION TAB-022)

- **Priority:** P2 Medium · **Status:** Open · **Refs:** TABRION TAB-022, TB-017, specification S-7, contract I-61
- **What:** the golden tests run today in the existing unit and `pg` jobs. The plan wants a job `golden files` in
  `ci.yml` (blocking) and a job `downstream` in `nightly.yml` that runs `python scripts/downstream.py --summary
  "$GITHUB_STEP_SUMMARY"` against a PostgreSQL service (not blocking; its result is a release-gate criterion of
  TAB-040). Lane C does not change `.github/`. The script installs each consumer with `uv pip install --override`,
  so HEAD replaces the consumer's Tabrion requirement (a Git commit pin; Tabrion is not published to an index),
  and checks that it did (review M-1 of #19, fixed); the override names `tabrion[all]`, so a consumer keeps the
  extras it asks for (N-1 of the delta re-review, fixed). The job needs network access for the clones and installs.
- **Next step:** whoever may change `.github/` adds both jobs (with the label `workflow-change-reviewed`) and lists
  them in `.github/ci-manifest.json`; the release gate reads the last nightly result.

### TB-071 · Record `pgvector_status` on a server with pgvector (TABRION TAB-022)

- **Priority:** P3 Low · **Status:** Open · **Refs:** TABRION TAB-022, contract 8.2, I-61
- **What:** `tests/golden/nodestro.json` was recorded on a server without pgvector, so only the `unavailable` case of
  Nodestro's `pgvector_status` is recorded; on a server with pgvector the replay test checks the states the contract
  lists instead (`other_schema`, `active`).
- **Next step:** run `scripts/record_golden.py --consumer nodestro` against the pgvector server of CI (the `pg`
  job with pgvector 17) and commit the file.

### TB-072 · The pgvector switch of the consumers' test suites (TABRION TAB-022)

- **Priority:** P3 Low · **Status:** Open · **Refs:** TABRION TAB-022, TAB-020, contract 8.2, 8.3, I-36
- **What:** Nodestro and Orkexa have `<PREFIX>_REQUIRE_PGVECTOR=1` as a flag; `tabrion.testing` reads a list,
  `<PREFIX>_REQUIRE_EXTENSIONS=vector` (golden test `test_testing_environment_names`). With `configure(env_prefix=...)`
  alone, the consumers' CI switch would be ignored and a missing pgvector would skip instead of fail.
- **Next step:** at adoption (MIG-NOD, MIG-ORK) the consumers' CI jobs set `<PREFIX>_REQUIRE_EXTENSIONS=vector`
  instead of `<PREFIX>_REQUIRE_PGVECTOR=1` (pointing `require_extensions_env` at the old name would read its `1` as
  an extension name).

### TB-073 · `url_from_env` keeps white space that Nodestro strips (TABRION TAB-022, TAB-012)

- **Priority:** P3 Low · **Status:** Done (decided: deviation D-76) · **Refs:** TABRION TAB-012, TAB-022, contract 2.3, 8.2
- **What:** the golden test `test_nodestro_url_from_env` shows that Nodestro's `server_settings.py` strips white
  space around `NODESTRO_DATABASE_URL` and around the content of `_FILE`; `url_from_env` removes only the file's
  trailing newline. The contract row said "identical rule". A value such as `" postgresql://..."` from a
  hand-edited secret file therefore reaches libpq with its spaces (libpq accepts them in a URL only at the end).
- **Decision:** keep it (deviation D-76): `url_from_env` is a frozen a1 function and returns the value as given;
  Nodestro's wrapper strips at adoption (MIG-NOD). Nothing left to do here.

## Cross-repository dependencies

| This repo item | Other repo | Other item | Relation |
|---|---|---|---|
| TB-012 | orkexa, axentra, tabrion-studio | TABRION QA-16 | Their AC-1 contracts and mutation probes |
| TB-014 | tabrion-studio, nodestro-runtime | TABRION STU-006, NOD-CR-3 | Use the profile format |
| TB-015 | nodestro-runtime | TABRION NOD-CR-5 | Uses the schema fingerprint |
| TB-011 | axentra, orkexa, nodestro-runtime | TABRION VER-1, MIG-* | Need the SHA of the `v0.1.0` final commit (RC-8) |
| TB-067 | axentra, orkexa, nodestro-runtime, tabrion-studio | TABRION QA-19 (AC-4) | Always supply the Tabrion SHA pin; negative case without it |
| TB-068 | nodestro-studio | TABRION QA-15 (AC-6) | Check the version at the pinned SHA |
| TB-008 | nodestro-studio | TABRION QA-20 (AC-5) | Consumer compat gates Tabrion tags |
| TB-035 | axentra, nodestro-runtime | TABRION MIG-AXE, MIG-NOD | Their role checks miss roles reachable with `SET ROLE` (review H-1), and their grants accept the schema's owner and its members (review M-1 of #8); fix in their MIG waves or earlier |
| TB-052 | nodestro-runtime | TABRION MIG-NOD | Catch the one-shot `lock_lost` of the scheduler pattern after a lost lock connection |
| TB-006 | axentra | TABRION MIG-AXE-2, MIG-AXE-3 | Re-export `split_statements` and use `tabrion.errors`; the splitter differences of contract I-9 go into Axentra's CHANGELOG as a fix |

## Maintaining this file

- Add an entry when work is identified but not done in the current pull request. Use the next free ID; IDs are never
  reused.
- When an item is finished, delete its entry in the pull request that completes it, mention the ID in the pull
  request description and update [STATUS.md](program/STATUS.md).
- Accepted risks stay listed with their reason until the risk is removed or re-evaluated.
- Re-review the whole file before each tag and update the "Last full review" line.
- Security issues that are not yet fixed are described only in general terms here. Report details privately (GitHub
  private vulnerability reporting), never in this file or in public issues.
