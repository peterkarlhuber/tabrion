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
> (TB-050, TB-051) on branch `claude/project-thread-rdo6g3-errfix`.

## Summary

| Priority | Count |
|---|---|
| P0 Critical | 1 |
| P1 High | 12 |
| P2 Medium | 10 |
| P3 Low | 20 |
| P4 Optional | 3 |

**Release blockers:** TB-001 (manual trademark register check before G-T2). Nothing is released from this repository
before G-R in any case; there is no published version.

M1 and the G-T1 follow-up are merged: the package skeleton, the frozen API as typed stubs, CI, the import contracts,
the documentation and the program files ([STATUS.md](program/STATUS.md#g-t1-conditions)). M2 has started:
`tabrion.errors`, `tabrion.sql` and `tabrion.config` are merged (TABRION TAB-010..TAB-012, pull request #3); the M2
lane B pull request implements `tabrion.sqlite` (TABRION TAB-013, TAB-014,
[M2 lane B evidence](program/STATUS.md#m2-lane-b-evidence-tab-013-tab-014)); the functions of `tabrion.postgres`,
`tabrion.check`, layer B and `tabrion.testing` still raise `NotImplementedError`.

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

- **Priority:** P1 High · **Status:** In progress (TAB-010..TAB-016 merged; TAB-030 in the second lane C pull
  request) · **Refs:**
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
  independent review TB-032 accepted); TAB-030 is in review (I-32, I-34); `IMPLEMENTED` in
  `tests/unit/test_public_api.py` lists the functions of the partly implemented `tabrion.postgres` (only
  `grant_runtime_role` is left). Then TAB-031 on `role_info`/`inspect_role`. Layer B (TAB-018) raises its `DatabaseError` subclass through `tabrion.errors._error_class(kind)` and passes
  `sent=False` for checkout errors.

### TB-007 · Layer B, migrations, testing (TABRION TAB-018..TAB-021, TAB-032, TAB-034)

- **Priority:** P1 High · **Status:** Blocked (TAB-014) · **Refs:** TABRION TAB-018, TAB-019, TAB-020,
  TAB-021, TAB-032, TAB-034
- **What:** layer B core (`Database`, `Profile`, `Transaction`, `fetch_*`, TAB-018), `tabrion.migrations` (TAB-019),
  `tabrion.testing` 0.1 (TAB-020) with a database per session, roles without superuser, truncate and runtime-role
  mode (TAB-032), profile and portability cases (TAB-021). The fault-injection proxy of TAB-034 (includes QA-3) is in
  the second lane C pull request as the internal `tabrion.testing._faults` (TB-033).
- **Status note:** TAB-020 is in the fourth lane C pull request (contract I-36); TAB-032 follows there once the grant
  of TAB-031 (#8) is merged.
- **Next step:** after TAB-014 and TAB-015. TAB-018 first decides TB-040 (review N-4), also settles F-11 (TB-020) and
  F-14 (`Database()` without a
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
  The tag `v0.1.0` needs TB-001 done first; no GitHub release and no PyPI upload without G-R.
- **Next step:** TAB-038 is merged (#9, `a463422`, accepted after independent review); the annotated tag `v0.1.0a1`
  on that commit is an owner action (the agent's Git proxy only pushes branches). Consumers pin the commit SHA. The
  internal a1 tag does not need TB-001; the tag `v0.1.0` does. Then TAB-041 and TAB-040.

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
  then does not protect against SQL that runs as that role. Tabrion's fix (I-20) shows the gap: TAB-T-R11 asserts
  that the reference queries miss it.
- **Next step:** fix both in their MIG waves (MIG-AXE, MIG-NOD) by moving to `tabrion.postgres.check_role` and
  `inspect_role`, or earlier with the membership query of I-20 and the R11/R12 cases in their own suites.

### TB-036 · Superuser-equivalent predefined roles and `CREATEROLE` are not reported (review I-4)

- **Priority:** P2 Medium · **Status:** Open · **Refs:** TABRION TAB-016, TAB-017, contract I-20, review H-1
  (optional part), I-4
- **What:** membership of `pg_execute_server_program`, `pg_write_server_files` or `pg_read_server_files`, and
  `CREATEROLE` on servers before 16, are ways to superuser-equivalent power that `check_role` does not name.
  `pg_read_all_data` and `pg_write_all_data` do not bypass row-level security. A new reason code would extend the
  frozen reason set (a contract addendum); a finding of `tabrion.check` (TAB-017) would not.
- **Next step:** decide with TAB-017: a `tabrion.check` finding for these memberships (preferred, no API change), or a
  reason code in 0.2 by contract addendum.

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

- **Priority:** P2 Medium · **Status:** Open (prerequisite of TABRION TAB-018) · **Refs:** TABRION TAB-010, TAB-018,
  R-NORETRY, contract I-1, I-13, review N-4
- **What:** `classify` returns a `TabrionError`'s stored classification unchanged, ignoring `sent` and
  `during_commit`: a `ConnectionFailed` classified at checkout (`sent=False`, retryable) stays retryable for
  `classify(err, during_commit=True)`. Nothing builds such errors before layer B.
- **Next step:** decide in TAB-018 before its retry and translation code: apply R-NORETRY on top of the stored
  classification (CONNECTION with `during_commit` or a later `sent` forces `outcome_unknown` and not retryable), or
  document in I-1/I-13 that the flags are ignored and add a TAB-018 test.

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
  `-journal` that SQLite then writes page data into; the finding must cover both cases.

### TB-044 · Independent review of TAB-013 and TAB-014 (planning document 07 section 6)

- **Priority:** P1 High · **Status:** In progress (fix round in pull request #4) · **Refs:** TABRION TAB-013, TAB-014,
  B-06-3, B-06-5, MIG-NOD-6, `reviews/tab-013-014-review.md`
- **What:** the review model requires an independent review agent for TAB packages in `sqlite` (file permissions,
  transactions). The review at `8e78e9c` is ACCEPTED WITH CONDITIONS (M-1..M-3); the fix round fixes M-1..M-3 and
  L-1..L-3 in the same pull request ([STATUS.md](program/STATUS.md#review-fix-round-tab-013-tab-014)); the Info
  items are TB-046..TB-049.
- **Next step:** delta re-review of the fix round, then merge.

### TB-045 · Randomized symlink race (TAB-T-123) in the nightly stress suite (QA-4)

- **Priority:** P3 Low · **Status:** Open · **Refs:** TABRION TAB-013, QA-4, TM-04, TAB-T-123
- **What:** planning document 07 asks for a race test with 1,000 repetitions (a link swapped in between the check
  and the open, 0 hits). The pull request tests every window deterministically through seams (TAB-T-S07: before
  creation, after the check, while SQLite opens and swapped back, an inode swap); a randomized repetition test would
  be timing-dependent on pull requests.
- **Next step:** add it to the nightly stress job with QA-4.

### TB-046 · ATTACH on a read-only SQLite connection creates an empty file (review I-1)

- **Priority:** P3 Low · **Status:** Open · **Refs:** TABRION TAB-013, contract I-22, review I-1
- **What:** `ATTACH 'other.db'` on a `connect(read_only=True)` connection creates an empty file at the attached
  path. `query_only` blocks writes into it, and the main file stays byte-identical, but the file appears.
- **Next step:** set `SQLITE_LIMIT_ATTACHED` to 0 for read-only connections (`Connection.setlimit`, Python 3.11+), or
  document it in I-22; decide with TAB-017.

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

### TB-055 · Complete the dogfooding of `tabrion.testing` (TABRION TAB-020, TAB-032)

- **Priority:** P3 Low · **Status:** Open · **Refs:** TABRION TAB-020, TAB-032, contract section 6, I-36, D-51
- **What:** the PostgreSQL suites use the plugin's URL and schema fixtures; `make_role` and `scratch_database` in
  `tests/postgres/conftest.py` stay local, and the SQLite suites use `tmp_path` instead of `tabrion_sqlite_path`.
- **Next step:** with TAB-032, move the role and database helpers onto `tabrion_postgres_roles` and
  `tabrion_postgres_database` where the cases allow it; the SQLite suites follow when lane B next touches them.

## Cross-repository dependencies

| This repo item | Other repo | Other item | Relation |
|---|---|---|---|
| TB-012 | orkexa, axentra, tabrion-studio | TABRION QA-16 | Their AC-1 contracts and mutation probes |
| TB-014 | tabrion-studio, nodestro-runtime | TABRION STU-006, NOD-CR-3 | Use the profile format |
| TB-015 | nodestro-runtime | TABRION NOD-CR-5 | Uses the schema fingerprint |
| TB-011 | axentra, orkexa, nodestro-runtime | TABRION VER-1, MIG-* | Need `v0.1.0` final (RC-8) |
| TB-008 | nodestro-studio | TABRION QA-20 (AC-5) | Consumer compat gates Tabrion tags |
| TB-035 | axentra, nodestro-runtime | TABRION MIG-AXE, MIG-NOD | Their role checks miss roles reachable with `SET ROLE` (review H-1); fix in their MIG waves or earlier |
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
