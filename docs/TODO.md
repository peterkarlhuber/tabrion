# TODO: tabrion

> Open work for this repository: release blockers, owner actions, the open work packages of the coming milestones,
> security follow-ups and technical debt.
> The program backlog is the source of truth for scope and acceptance criteria ([STATUS.md](program/STATUS.md),
> planning documents 09 and 14); this file is the entry point and summary. "TABRION <WP-ID>" names a work package of
> that backlog.
>
> **Last full review:** 2026-11-08 against branch `claude/project-thread-rdo6g3` (M2 lane A pull request: TABRION
> TAB-010, TAB-011, TAB-012, after the G-T1 follow-up pull request #2 merged)

## Summary

| Priority | Count |
|---|---|
| P0 Critical | 1 |
| P1 High | 11 |
| P2 Medium | 6 |
| P3 Low | 10 |
| P4 Optional | 0 |

**Release blockers:** TB-001 (manual trademark register check before G-T2). Nothing is released from this repository
before G-R in any case; there is no published version.

M1 and the G-T1 follow-up are merged: the package skeleton, the frozen API as typed stubs, CI, the import contracts,
the documentation and the program files ([STATUS.md](program/STATUS.md#g-t1-conditions)). M2 has started: the M2 lane
A pull request implements `tabrion.errors`, `tabrion.sql` and `tabrion.config` (TABRION TAB-010..TAB-012,
[M2 evidence](program/STATUS.md#m2-evidence)); every other function still raises `NotImplementedError`.

## Priorities and statuses

- **P0 Critical**: blocks a gate or a release, or a legal or security exposure.
- **P1 High**: on the critical path of the next milestone, or blocks a consumer.
- **P2 Medium**: in scope, schedule soon.
- **P3 Low**: minor gap or debt.
- **P4 Optional**: improvement idea.

Statuses: `Open`, `In progress` (PR linked), `Blocked` (names the blocker), `Needs decision` (owner decision pending),
`Needs external review`, `Accepted risk` (deliberately not fixed; reason stated).

## Release blockers and critical items

### TB-001 · Manual trademark register check before G-T2 (TABRION GOV-1)

- **Priority:** P0 Critical · **Status:** Needs decision · **Refs:** TABRION GOV-1, R-13, R-36, R-CR-6, ST-10, G-T2
- **What:** GOV-1 found identical, unregistered prior use of the name "Tabrion" by a Windows Excel/CSV app
  ([name-check.md](program/reports/name-check.md)). The owner keeps the name for now (GOV-1 "done: conditional").
  The registers were not searched automatically.
- **Next step:** owner action: **manual TM register check (TMview/EUIPO/DPMA/WIPO) before G-T2; rename if
  conflict.** Until then no PyPI name, no announcement, and the profile file extension of TABRION TAB-CR-1 stays
  unfrozen.

## Owner actions

Things only the owner can do; the agent lacks the rights or must not do them. Mirrored in
[STATUS.md](program/STATUS.md#owner-actions).

### TB-003 · Repository settings: branch protection, environment `pypi`, private vulnerability reporting

- **Priority:** P1 High · **Status:** Open · **Refs:** TABRION GOV-2, TABRION GOV-3, TABRION TAB-043, G-R, G-T1 C-7,
  C-8; owner actions O-1, O-2, O-5, O-7, O-9
- **What:** branch protection on `master`: the required status check `ci-ok` is the commit status that `ci-ok.yml`
  posts on the PR head SHA (C-7; it exists only after the follow-up pull request is merged), plus no force pushes and
  no deletion (the agent proxy gets HTTP 403); recommended `CODEOWNERS` for `/.github/**` with code-owner review. The
  same for tabrion-studio. The environment `pypi` (C-8): the owner as required reviewer, "prevent self-review",
  deployment refs restricted to tags `v*`, no secrets, and later the trusted publisher bound to `release.yml` and
  `pypi` (ADR 0009, "Release environment"). The label `workflow-change-reviewed` for the ci-ok workflow-change gate
  (set only by the owner or an independent reviewer; ADR 0012). Private vulnerability reporting switched on (SECURITY.md points to it). Upload
  `assets/branding/social-preview.png` as the repository's social preview (O-9).
- **Next step:** set them in the repository settings; then mark GOV-2 done in STATUS.md.

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

- **Priority:** P1 High · **Status:** In progress (TAB-010..TAB-012 in the M2 lane A pull request) · **Refs:**
  TABRION TAB-010, TAB-011, TAB-012, TAB-013, TAB-014, TAB-015, TAB-016, TAB-017, TAB-030, TAB-031
- **What:** replace the stubs in order of the critical path: `tabrion.errors` (TAB-010), `tabrion.sql` (TAB-011),
  `tabrion.config` (TAB-012), `tabrion.sqlite` connection and profile (TAB-013) then transactions, `migrate`,
  `backup` and checks (TAB-014), `tabrion.postgres` configure, ping, diagnose, extension status (TAB-015), roles
  (TAB-016), `tabrion.check` and finding codes (TAB-017), `SessionLock` and advisory helpers (TAB-030),
  `grant_runtime_role` (TAB-031). Each with the TAB-T test IDs of the contract.
- **Next step:** TAB-010..TAB-012 are in review (the G-1 notes and F-12 are done; the independent review of TAB-010
  is TB-026). TAB-013 and TAB-015 can start once they are merged. `test_stubs_raise_not_implemented` shrinks module
  by module as stubs are replaced (`errors`, `sql`, `config` have left it); every change to a signature also runs
  `scripts/api_surface.py --write` and updates the contract. TAB-017 follows the dispatch of C-1 (contract section
  7). Layer B (TAB-018) raises its `DatabaseError` subclass through `tabrion.errors._error_class(kind)` and passes
  `sent=False` for checkout errors.

### TB-007 · Layer B, migrations, testing (TABRION TAB-018..TAB-021, TAB-032, TAB-034)

- **Priority:** P1 High · **Status:** Blocked (TAB-014, TAB-015) · **Refs:** TABRION TAB-018, TAB-019, TAB-020,
  TAB-021, TAB-032, TAB-034
- **What:** layer B core (`Database`, `Profile`, `Transaction`, `fetch_*`, TAB-018), `tabrion.migrations` (TAB-019),
  `tabrion.testing` 0.1 (TAB-020) with a database per session, roles without superuser, truncate and runtime-role
  mode (TAB-032), profile and portability cases (TAB-021), the fault-injection proxy `tabrion.testing.faults`
  (TAB-034, includes QA-3).
- **Next step:** after TAB-014 and TAB-015. TAB-018 also settles F-11 (TB-020) and F-14 (`Database()` without a
  factory raises `TypeError`); TAB-021 uses the case types of G-3 (revised), TAB-032 the fixture `tabrion_role_setup`
  (G-10).

### TB-008 · Consumer protection, conformance, stability policy (TABRION TAB-022, TAB-050, TAB-051)

- **Priority:** P1 High · **Status:** Blocked · **Refs:** TABRION TAB-022, TAB-050, TAB-051, AC-5
- **What:** golden files `tests/golden/` of the existing Nodestro and Axentra behavior (blocking job `golden files`)
  and the nightly `downstream` job (TAB-022); the internal "Levuvo Database Profile" with `conformance/SPEC.md`,
  vectors, manifest and the job `conformance` (TAB-050); the API stability policy and a deprecation helper
  (TAB-051).
- **Next step:** TAB-022 after TAB-014, TAB-016 and TAB-030; TAB-050 after TAB-016; TAB-051 after TAB-023.

### TB-009 · Quality suites and release path in this repository (TABRION QA-2, QA-4, QA-8, QA-11, QA-22)

- **Priority:** P2 Medium · **Status:** Open · **Refs:** TABRION QA-2, QA-4, QA-8, QA-11, QA-22
- **What:** the canary suite (no values in errors or logs, QA-2); concurrency and stress suite, nightly (QA-4);
  supply chain with SBOM next to the existing `dependency audit` job (QA-8); the release workflow is in place but
  its acceptance as QA-11 needs a dry run on a test tag (QA-11); conventions audit (QA-22).
- **Next step:** QA-8 and QA-11 can start now (they depend on TAB-004 and TAB-005 only).

### TB-010 · Reference applications and measurement report (TABRION RA-A-1..3, RA-B-1..4, RA-EVAL)

- **Priority:** P2 Medium · **Status:** Blocked (TAB-014, TAB-019) · **Refs:** TABRION RA-A-1, RA-A-2, RA-A-3,
  RA-B-1, RA-B-2, RA-B-3, RA-B-4, RA-EVAL
- **What:** the parts application (portable schema with Alembic, functions and tests on SQLite and PostgreSQL, a plain
  SQLAlchemy baseline) and the measurements application (local capture, idempotent transfer, offline and failure
  tests, baseline) under `examples/`, then the value report against plain SQLAlchemy. The CI job `examples` arrives
  with them.
- **Next step:** RA-B-1 after TAB-014; RA-A-1 after TAB-019.

### TB-011 · Documentation 0.1, internal alpha and release 0.1.0 (TABRION TAB-041, TAB-038, TAB-040)

- **Priority:** P1 High · **Status:** Blocked · **Refs:** TABRION TAB-041, TAB-038, TAB-040, G-T2, RC-8
- **What:** the guides of 0.1 (TAB-041, including `docs/guides/infrastructure-baseline.md` of ADR 0001); the internal
  pre-release `v0.1.0a1` only for unmerged integration branches (TAB-038); the tag `v0.1.0` and gate G-T2 (TAB-040).
  A tag needs TB-001 done first; no GitHub release and no PyPI upload without G-R.
- **Next step:** TAB-038 after TAB-010..TAB-012 and TAB-005.

### TB-022 · Release check: no 0.1 name is still a stub (TABRION TAB-040, G-T1 F-08)

- **Priority:** P1 High · **Status:** Open · **Refs:** TABRION TAB-040, TAB-023, G-T2, G-T1 F-08
- **What:** before the tag `v0.1.0`, a check that no name of `docs/api/surface.json` with `since` at or below the
  package version raises `NotImplementedError` (today every name does, by design). `scripts/api_surface.py` already
  keeps names with a newer `since` out of the exported surface.
- **Next step:** with TAB-040, as a mode of `scripts/api_surface.py` run by `release.yml`.

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

- **Priority:** P1 High · **Status:** Blocked (G-T1, TB-001 for the file extension) · **Refs:** TABRION TAB-CR-1,
  CR-DB, R-CR-6
- **What:** a Tabrion connection profile format v1: schema, parser, secret references, no storage (storage stays in
  Studio and Nodestro). Studio STU-006 and Nodestro NOD-CR-3 use the same format. 2 + 0.5 agent days. New public API:
  needs a contract addendum and its own G-T1 style review.
- **Next step:** the format after G-T1; the profile file extension only after the register check (TB-001).

### TB-015 · Schema fingerprint and comparison (TABRION TAB-CR-2)

- **Priority:** P1 High · **Status:** Blocked (TAB-CR-1, TAB-033) · **Refs:** TABRION TAB-CR-2, CR-DB, NOD-CR-5
- **What:** a schema fingerprint and a comparison through `Database.inspector`; NOD-CR-5 (node `database`) depends on
  it. 1.5 + 0.5 agent days. New public API, like TB-014.
- **Next step:** after TAB-CR-1 and the inspector of TAB-033.

### TB-016 · PyPI path (TABRION TAB-043)

- **Priority:** P2 Medium · **Status:** Blocked (G-R) · **Refs:** TABRION TAB-043, E-PyPI, G-R, TB-001, TB-003
- **What:** trusted publisher and `0.1.0rc1` on PyPI. Every public release needs its own G-R. The `publish` job of
  `release.yml` fails on purpose until then.
- **Next step:** only after G-R, the register check (TB-001) and the environment `pypi` (TB-003).

## Tests and quality assurance

### TB-017 · CI jobs that arrive with later work packages

- **Priority:** P3 Low · **Status:** Open · **Refs:** TABRION TAB-022, TAB-034, TAB-050, QA-4, RA-A-1, RA-B-1;
  deviation D-2
- **What:** `conformance`, `golden files` and `examples` in `ci.yml` (`api surface` arrived with TAB-023); fault injection, stress and
  `downstream` in `nightly.yml`. Each is added with its work package and listed in `.github/ci-manifest.json`.
- **Next step:** with each work package.

### TB-018 · PostgreSQL matrix cost on pull requests

- **Priority:** P3 Low · **Status:** Open · **Refs:** deviation D-3
- **What:** all eight PostgreSQL jobs (16, 17, 18, pgvector 17 on Python 3.11 and 3.14) run on every pull request,
  following planning document 05; planning document 07 runs 16 and 18 only nightly. Cheap while the suite is small.
- **Next step:** revisit when the PostgreSQL suite of M2 makes the jobs slow.

### TB-020 · Export `Statement` and `Params`, or make them documentation-only (G-T1 F-11)

- **Priority:** P3 Low · **Status:** Needs decision · **Refs:** G-T1 F-11, G-5, TABRION TAB-018
- **What:** both aliases appear in public layer-B signatures and in `tabrion._sa.database.__all__`, but not in
  `tabrion.__all__`, against the G-5 rule.
- **Next step:** decide in TAB-018; update the contract and `surface.json` together.

### TB-021 · Platform path filters too narrow (G-T1 F-15, CC-3)

- **Priority:** P3 Low · **Status:** Open · **Refs:** G-T1 F-15, CC-3, TABRION TAB-004
- **What:** macOS and Windows run on pull requests only when `sqlite.py` or `tests/sqlite/**` change; `config.py`,
  `errors.py`, `testing/sqlite.py` and `pyproject.toml` also affect those platforms.
- **Next step:** add the paths to `platforms.yml` and to the manifest's `path_filters` in one pull request merged
  before the change that needs them (ci-ok judges by the default branch's manifest).

### TB-025 · Localized server messages are not tested in CI (TABRION TAB-010, TAB-T-E15)

- **Priority:** P3 Low · **Status:** Open · **Refs:** TABRION TAB-010, TAB-T-E15, R-T02
- **What:** `test_e15_localized_messages` sets `lc_messages = 'de_DE.UTF-8'` on the server and skips when the server
  has no such locale, which is the case for the PostgreSQL images of the CI jobs and for the local Ubuntu server. The
  unit test `test_e15_localized_cancel_message_is_canceled` covers the classification with a German message.
- **Next step:** with TAB-034 or QA-4, build or select an image with the `de_DE` locale for one PostgreSQL job.

### TB-026 · Independent review of TAB-010 (planning document 07 section 6)

- **Priority:** P1 High · **Status:** Needs external review (delta re-review) · **Refs:** TABRION TAB-010, TAB-011,
  TAB-012, B-06-1, review `tab-010-012-review.md`
- **What:** the review model requires an independent review agent for every TAB package in `errors`. The review of
  pull request #3 at `9fe568f` was **not accepted** (H-1 R-NORETRY through `__context__`, H-2 `DETAIL` in `repr` and
  container arguments). The fix round in the same pull request fixes H-1, H-2, M-1..M-3, L-1..L-3, L-6, I-1, I-2,
  I-6 and part of I-5 ([M2 evidence](program/STATUS.md#m2-evidence), D-26, D-27); L-4, L-5, I-3, I-4, the rest of
  I-5 and I-7 are TB-028..TB-030 and SECURITY.md.
- **Next step:** delta re-review on the new pull request head.

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

### TB-031 · Port the H-2 redaction fix to Axentra's filter (and check the H-1 analog) (MIG-AXE-3)

- **Priority:** P1 High · **Status:** Open (scheduled after this pull request is accepted) · **Refs:** TABRION
  MIG-AXE-0, MIG-AXE-3, review H-1, H-2, B-06-1
- **What:** Tabrion's filter follows Axentra's reviewed MIG-AXE-0 filter, which has the identical H-2 gap: a
  `DETAIL` value survives in `repr`/`%r` form (escaped `\nDETAIL:`), inside list, tuple, set or dict arguments, in
  `e.args`, in a dict `msg`, and as a SQLAlchemy error inside a list. Axentra's error handling may also have the H-1
  analog (a lost COMMIT classified by the `__context__` error being handled) and must be checked.
- **Next step:** an Axentra pull request ports the collection and escaped-form scrubbing of contract I-6 and the
  H-2 tests, checks its chain rule against I-13, or replaces the filter by `tabrion.errors` with MIG-AXE-3.

### TB-024 · Circular import of the staged 0.2 mixins (G-T1 condition check CC-2)

- **Priority:** P2 Medium · **Status:** Open · **Refs:** CC-2, TABRION TAB-033, TB-013
- **What:** `tabrion._sa._v02` imports `Isolation`, `Statement` and `Transaction` from `tabrion._sa.database`. When
  TAB-033 lets `Database` and `Transaction` inherit from the `_v02` mixins, the two modules import each other.
- **Next step:** before TAB-033, move the type aliases to `tabrion._sa._types`, type `run_transaction`'s `fn` against
  a forward reference, and keep `surface.json` unchanged.

## Cross-repository dependencies

| This repo item | Other repo | Other item | Relation |
|---|---|---|---|
| TB-012 | orkexa, axentra, tabrion-studio | TABRION QA-16 | Their AC-1 contracts and mutation probes |
| TB-014 | tabrion-studio, nodestro-runtime | TABRION STU-006, NOD-CR-3 | Use the profile format |
| TB-015 | nodestro-runtime | TABRION NOD-CR-5 | Uses the schema fingerprint |
| TB-011 | axentra, orkexa, nodestro-runtime | TABRION VER-1, MIG-* | Need `v0.1.0` final (RC-8) |
| TB-008 | nodestro-studio | TABRION QA-20 (AC-5) | Consumer compat gates Tabrion tags |
| TB-031 | axentra | TABRION MIG-AXE-3 | Axentra's redaction filter has the identical H-2 gap (and possibly the H-1 analog); port the fix of this pull request |
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
