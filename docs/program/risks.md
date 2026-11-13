# Risk register and rollback plan

English version of planning document 11 (revision 22:52Z with the owner decisions of 2026-11-07 23:26Z), kept up to
date by QA-12. IDs, gates and approval limits follow planning document 00; tests follow planning document 07;
migration steps follow planning document 06.

**Revision 22:52Z (in force, replaces 22:26Z):** aligned with plan document 00 section 12 (K-30..K-36): Axentra to
`tabrion[postgres]` required, `orkexa[postgres]` to `tabrion[postgres]` optional, Orkexa core to `tabrion`
(E-ORK-CORE = yes, 23:26Z), Nodestro uses Tabrion in core and server; one Tabrion version per installation (K-34,
V-A/V-B); vectors are only a test asset inside Tabrion (K-35). Removed: vector drift (old R-07), duplicate
implementations (old R-26). New: R-07 diamond and version conflict, R-26 Tabrion as a bottleneck, R-30..R-34; after
the owner decisions of 23:26Z (plan document 00 section 13) also R-35 (pre-release against a final range), R-19
closed (G-EM = no), B-06-1 early through MIG-AXE-0.

**Changes in this repository (not in planning document 11):**

- 2026-11-08: R-13 updated with the GOV-1 result; **R-36** added (prior use of the name "Tabrion" by a third-party
  Excel/CSV app).
- 2026-11-08: R-13, R-24, R-36, R-CR-6 and ST-10 updated for the owner decision of 2026-11-08 11:26Z: the register check is not ordered
  and no prerequisite of G-T2; Tabrion is distributed through Git SHA pins, a PyPI publication is not planned.
- 2026-11-08: section 6 added with the risks R-CR-1..R-CR-6 of change request CR-DB (planning document 14
  section 10), which the owner took into the program.

**Rating:** likelihood (L) and impact (I) from 1 to 5 each, score = L x I. From a score of 12, the countermeasure is
part of the acceptance of the linked work package. "Owner" is who is responsible for the countermeasure: **Agent**
(in phase B) or **Peter** (decision).

## 1. Risk register

| ID | Risk | Area | L | I | Score | Early indicator | Countermeasure | Owner | WPs / gates |
|---|---|---|---|---|---|---|---|---|---|
| R-01 | Consolidation silently changes the behavior of the existing platforms (timeouts, error texts, lock semantics) | Compatibility | 3 | 5 | 15 | Test count or skip list changes; new error codes in API snapshots | Additive steps only (S2 "new fields only"), consumers' API/OpenAPI snapshots unchanged, contract tests K11, G-M(n) | Agent | MIG-*, G-M |
| R-02 | Role tooling is unified in Tabrion and used by Axentra (required) and Nodestro, so grants or rejection decisions (superuser, `BYPASSRLS`, table ownership) change | Security | 2 | 5 | 10 | Catalog snapshot diff not empty; decision table differs | Catalog snapshot and role decision table (8 variants) from the old code as golden files in both repositories; AX-I-5 (plan document 07 section 8); Nodestro `runtime-role` job | Agent | MIG-AXE (roles), MIG-NOD-5, QA-22 |
| R-03 | Session settings travel across pooled connections (tenant context), bypassing RLS; directly relevant because Axentra uses Tabrion internally and shares the pool with Nodestro (M10) | Security | 2 | 5 | 10 | TAB-T-160/161 or MIG-T-AXE-1/3 red; `set_config(..., false)` in code | Rule R-SET, AX-I-1..4 (plan document 07 section 8) with tests in every MIG-AXE pull request; grep rule in Tabrion and Axentra CI; integration test with a shared pool | Agent | TAB, MIG-AXE, QA-22 |
| R-04 | Job queue finding F-02 occurs in operation (lost job, false "already queued" message) | Technical | 2 | 4 | 8 | Logs `run.already_queued` without a second request; `interrupted` with `attempts=1` without a worker crash | JQ-1..JQ-4 before all MIG steps, independent of Tabrion; regression test with a fault-injection proxy | Agent | JQ, G-M |
| R-05 | The JQ fix hurts throughput or availability (writes no longer repeated, so more visible errors after failover) | Compatibility | 3 | 3 | 9 | More `CONNECTION` errors in the RT2-03 test | An error after sending is reported as "outcome unknown" and reconciled by the caller through an idempotency key (plan document 06 JQ-3); RT2-03 tests stay green | Agent | JQ-3 |
| R-06 | `SQLiteStore` (Nodestro core) moved to Tabrion (K-32) damages existing desktop data or the upgrade path | Data integrity | 2 | 5 | 10 | `foreign_key_check` not empty on fixtures; migration test with an old file red | F-01 (no foreign keys declared), legacy fixtures FX-SQ-0 plus desktop fixtures, U6 pattern (both directions), backup duty stays, AST guard `test_storage_contract.py` adjusted, desktop build in G-M | Agent | MIG-NOD (SQLite) |
| R-07 | **Diamond dependency and version conflict** (runtime to orkexa to tabrion, runtime to axentra to tabrion, runtime to tabrion): V-A, two different SHA pins, so pip refuses to install; V-B, ranges without intersection or silently different versions in Desktop and Server | Dependencies | 4 | 4 | 16 | AC-6 red; `pip install` "conflicting URLs" or ResolutionImpossible; AC-4 combination `nodestro-runtime[postgres]` fails | K-34: exactly one version; AC-6 checks V-A (identical SHA, P-1) and V-B (intersection plus lock); cascade tabrion, orkexa and axentra in parallel, runtime, api, sdk, studio; only one thread bumps pins; E-VER decided (V-B0 to V-B); RC-8 and four rules against conflicting pins (plan document 06 section 6.4); AC-4 negative case "two pins" | Agent / Peter | AC-4, AC-6, QA-15, QA-19, E-VER |
| R-08 | Parallel threads change the same repositories or files (CHANGELOG, versions.json), causing merge conflicts | Organization | 4 | 2 | 8 | Open pull requests of other threads on runtime, api, studio, axentra | Project rule: tell the other active threads before a pull request or merge and report afterwards; pins serially | Agent | all MIG, PIN |
| R-09 | Agent autonomy drifts from the approved concept (scope growth, architecture change without approval) | Organization | 3 | 4 | 12 | A work package without an ID in the backlog; the deviation log grows; core growth without a traceable justification (criteria K-Q) | Backlog work packages only (plan document 09), deviation log in `STATUS.md`, stop criteria (section 4), independent reviews | Agent / Peter | G-T1, G-S1 |
| R-10 | A single maintainer (bus factor 1) next to three platforms; Tabrion is orphaned | Organization | 3 | 4 | 12 | More than 60 days without a merge; more than 10 open Dependabot pull requests | A small core under the qualitative criteria K-Q (owner decision of 2026-11-13), no runtime dependency in Orkexa, consumers use only layer A (easy to roll back), six-month review REV | Peter | REV |
| R-11 | Studio scope grows into a DBeaver clone; the core gets UI logic | Scope | 4 | 3 | 12 | A core pull request justified by Studio without application benefit; Studio backlog beyond the 1.0 scope | Rule of plan documents 02 and 03: a core function only with benefit for applications (consolidation report 8.2); the 1.0 scope fixed in plan document 12; refer to established tools | Agent / Peter | STU, G-S1 |
| R-12 | Studio security flaw (DNS rebinding, CSRF, credential leak) | Security | 3 | 5 | 15 | STU-T red; review finding | SQ-01..SQ-08, security review G-S1 with re-review, write functions only after G-S2, production profiles write-locked | Agent / Peter | G-S1, G-S2 |
| R-13 | The name or trademark "Tabrion" collides (a GitHub user "tabrion" exists; EUIPO unchecked). **Update 2026-11-08:** GOV-1 found no registered mark in the sources searched, but identical prior use by a third-party app (see R-36); the registers EUIPO, DPMA, WIPO and TMview were not searched automatically | Legal | 2 | 4 | 8 | Hits in the GOV-1 research | GOV-1 before creating the repository (G-0); a fallback name prepared; no public announcement before G-R. GOV-1 is done with a conditional result. **Update 2026-11-08 11:26Z:** the owner did not order the register check; it is no prerequisite of G-T2. Tabrion is consumed through Git SHA pins without public marketing; a later public marketing under the name needs a new assessment | Peter | GOV-1, G-0, G-R |
| R-14 | License conflict in dependencies | Legal | 1 | 3 | 3 | License report finding | psycopg 3 is LGPL-3.0: unproblematic as a **separately installed** dependency of an MIT library (no embedding, no modification); duties arise only when shipping it (desktop bundle: keep psycopg replaceable, include the license text). `psycopg-binary` bundles libpq (PostgreSQL License) and OpenSSL (Apache-2.0). SQLAlchemy, Alembic, keyring, FastAPI, CodeMirror, TanStack, Radix and @xyflow are MIT; uvicorn is BSD-3; **elkjs is EPL-2.0** (only if that ER layout is chosen; weak file-level copyleft, acceptable, named in the license report) | Agent | G-R, QA-8 |
| R-15 | SQLAlchemy 2.1 requires Python 3.11 or later; differences between 2.0 and 2.1 in events and the inspector | Dependencies | 2 | 3 | 6 | The SQLAlchemy 2.0 job red | CI against 2.0.x and 2.1.x, lower bound 2.0.30, layer B uses only public events | Agent | QA-1 |
| R-16 | A psycopg 4 break or changed error codes | Dependencies | 2 | 3 | 6 | Dependabot major | Pinned `<4`, TAB-T-190 on the lowest and the newest version | Agent | TAB |
| R-17 | Different SQLite versions (system versus Python build, macOS) | Technical | 3 | 3 | 9 | OS job red | `os` job nightly, `diagnose` checks the minimum version 3.37, fallback for missing `STRICT` documented | Agent | QA-1 |
| R-18 | Misclassification leads to retry loops or swallowed errors | Technical | 2 | 4 | 8 | TAB-T-190 red | No automatic retry in 0.x; `retryable` only for 40001, 40P01 and BUSY before a write | Agent | TAB-errors |
| R-19 | ~~Merging the migration runners destroys bookkeeping, checksums or upgrade paths~~. **Closed:** G-EM = no (owner, 23:26Z); the runners stay separate | Data integrity | - | - | - | - | Dropped ([ADR 0007](../adr/0007-no-shared-migration-runner.md)) | - | ST-9 |
| R-20 | Performance regression (helper overhead, for example 80-130 microseconds in the SQLAlchemy layer) | Technical | 2 | 2 | 4 | TAB-T-300 above 2x baseline | Existing platforms use only layer A (no SQLAlchemy overhead), benchmark nightly | Agent | TAB |
| R-21 | Flaky concurrency or restart tests block CI | Quality | 3 | 2 | 6 | `flaky.txt` has more than 2 entries | Flaky policy (plan document 07 section 4.3), stress tests only nightly | Agent | QA-4 |
| R-22 | The desktop build (Tauri, bundled Python, macOS case clash) breaks because of a new dependency | Compatibility | 2 | 3 | 6 | Desktop CI red | Core stdlib-only; Desktop pins only through runtime; desktop build in G-M of MIG-NOD-3 and MIG-NOD-L1 (desktop bundle without tabrion, K-18) | Agent | MIG-NOD-3, MIG-NOD-L1, PIN-W1..W3 |
| R-23 | Plan and repository documents diverge (German and English) | Organization | 3 | 2 | 6 | Translation reconciliation open | K-13; translation reconciliation as a work package with a report (MUP-ONA 8.7 pattern) | Agent | GOV |
| R-24 | Supply chain (typosquatting `tabrion`, a compromised action). **Update 2026-11-08:** no PyPI publication is planned, so the name `tabrion` stays unclaimed on PyPI; a library declares `tabrion[...]>=0.1,<0.2`, and an install without the SHA pin asks the index for `tabrion`, where a third party could register it (dependency confusion) | Security | 2 | 4 | 8 | pip-audit or npm-audit finding; an install that resolves `tabrion` from an index | TM-10; every consumer install and CI supplies the direct-URL SHA pin (`-r requirements/tabrion.txt` or the application's pin); the AC-4 clean-install matrix (QA-19) gets the negative case "library without the pin fails, never resolves from an index" (TODO TB-067); internal installs use `--no-index` or an internal index. A PyPI name reservation would itself be a publication under G-R; actions pinned by commit SHA and images by digest (TAB-T-181) | Agent / Peter | QA-8, G-R |
| R-25 | The reference use cases are too small to prove value over SQLAlchemy, leading to a wrong REV decision | Product | 3 | 3 | 9 | Metrics (plan documents 02 and 09) not collected | Fix the metrics before building the reference applications and collect them; REV with data | Agent / Peter | RA-A, RA-B, REV |
| R-26 | **Tabrion becomes a bottleneck or single point of failure** for three platforms: a bug or vulnerability in Tabrion hits Axentra, Orkexa[postgres] and Nodestro at once; maintenance hangs on one repository | Organization / Security | 3 | 4 | 12 | AC-5 red for several consumers; open Tabrion issues block consumer work packages | A small core under the qualitative criteria K-Q (owner decision of 2026-11-13), high test thresholds (plan document 07 section 4), AC-5 as a release gate, patch release procedure under 1 day, a security advisory process, consumers bind only the public API (AC-7), the SQLAlchemy layer not required | Agent / Peter | AC-5, AC-7, G-T2 |
| R-27 | **Policy checks too strict or wrongly modeled** (AC-1..AC-4, AC-6) block legitimate K-33 edges (for example `orkexa.backends.postgres` to `tabrion.postgres`) | Organization | 3 | 2 | 6 | Pull requests asking for exceptions; overrides | The policy maps K-33 one to one, including optional edges and extras; positive tests per allowed edge; changes only by ADR plus approval, the policy pull request before the manifest pull request | Peter / Agent | AC-1..AC-6 |
| R-28 | The dependency surface of `nodestro-runtime[postgres]` and `axentra` grows (tabrion[postgres] new; psycopg already present) | Dependencies | 2 | 2 | 4 | AC-4 package list diff | `tabrion[postgres]` adds no new third-party package (psycopg already present); package list snapshot in AC-4 | Agent | AC-4, QA-19 |
| R-29 | Compatibility wrappers in Axentra and Nodestro hide behavior changes of the replaced helpers | Compatibility | 2 | 3 | 6 | Wrapper tests only on signatures | The existing tests of the helpers run unchanged against the wrappers; golden snapshots (roles, error texts, Problem JSON) | Agent | MIG-AXE, MIG-NOD |
| R-30 | **Breaking changes in Tabrion 0.x** break required consumers (Axentra) | Compatibility | 3 | 4 | 12 | API snapshot diff (K10); AC-5 red | SemVer for 0.x: breaking only with a minor bump, CHANGELOG "Breaking" and a migration note; AC-5 before every tag; deprecation over at least 1 minor; `api-consumers.md` as a protection list | Agent | K10, AC-5, AC-7 |
| R-31 | **Forced release cascades:** every Tabrion fix requires pin waves through axentra, orkexa, runtime, api, sdk and studio (with V-A) | Organization | 4 | 3 | 12 | A cascade longer than 2 days; several threads wait for pins | V-B reduces cascades (libraries with ranges, only the application pins); with V-A, bundle into the planned waves W1-W3 (plan document 00 section 11), a security fix as an express wave; cascade script (`check_versions.py`) | Agent / Peter | E-VER, PIN |
| R-32 | **Security regression in Axentra through shared code** (RLS, scope, error texts): a generic Tabrion fix silently changes Axentra's behavior | Security | 2 | 5 | 10 | AX-I tests red in AC-5; review finding | AX-I-1..8 permanently in Axentra CI and in AC-5; RLS, DbScope and M10 stay Axentra code (K-30); every Tabrion pull request in `postgres` or `errors` with a security review agent | Agent | QA-22, AC-5 |
| R-33 | The Orkexa core becomes heavier or coupled through Tabrion (E-ORK-CORE = yes, MIG-ORK-3) | Dependencies | 2 | 3 | 6 | Core import time or package list grows | Tabrion core stdlib-only (AC-3); MIG-ORK-3 only after Tabrion 0.1.x is final and stable (plan document 06 section 3.2); AC-3 blocked-module test until MIG-ORK-3 | Agent | MIG-ORK-3, AC-3 |
| R-34 | A cycle appears (for example a Tabrion test extra imports a consumer, a Studio edge reversed) | Architecture | 1 | 4 | 4 | AC-2 or AC-6 red | AC-1 (tabrion forbids every Levuvo package), AC-2 and AC-6 cycle checks | Agent | AC-1, AC-2, AC-6 |
| R-35 | A pre-release (`0.1.0a1`, `rc1`) against final library ranges: pip rejects it (probe `proben/versionierung/ERGEBNIS.md`), so merges with a Tabrion edge would fail or pre-release specifiers would reach master | Dependencies | 3 | 3 | 9 | AC-4 negative case, RC-8 check (AC-6) red | RC-8: pre-release ranges only on unmerged branches; first merges with a Tabrion edge only from `v0.1.0` final; B-06-1 early and independently through MIG-AXE-0 | Agent | RC-8, AC-4, AC-6, VER-1 |
| R-36 | **Name conflict with an existing "Tabrion" app (added 2026-11-08):** a free Windows Microsoft Store app named "Tabrion" for merging Excel/CSV files and joining tables (publisher "ALHUSSAIN, ALBURAEE", source `github.com/Hussain-007M/Tabrion`, v3.4.1 Store-certified in October 2026). No registration was found, but the name is identical, the goods are class 9 software and the field (tabular data) is close. Prior unregistered use may give rights in some jurisdictions and will cause confusion. The GOV-1 report concluded "G-0 not reached. HOLD: possible ST-10" ([name-check.md](reports/name-check.md)) | Legal | 3 | 4 | 12 | A registration or an objection by the app's publisher; search results mixing both products; a finding of the new name assessment before any public marketing | The owner decided to keep building under the name (GOV-1 "done: conditional"). **Update 2026-11-08 11:26Z:** the register check is not ordered and no prerequisite of G-T2; Tabrion is consumed through Git SHA pins, without PyPI and without public marketing; a later public marketing under the name needs a new assessment first. No PyPI name registration, no announcement, no GitHub release without G-R; keep the name in as few frozen places as possible (package name, profile file extension, extras) so a rename stays cheap (R-CR-6) | Peter | GOV-1, G-T2, G-R, TAB-CR-1 |

**Top risks (score of 12 or more):** R-07 diamond and version conflict (16); R-01, R-12 (15); R-09, R-10, R-11,
R-26, R-30, R-31, R-36 (12). Removed: vector drift and duplicate implementations (K-35, reuse wanted).

## 2. Generic rollback procedure

| Level | Procedure | Evidence |
|---|---|---|
| Consumer pin or range | A revert pull request of the pin or range commit (merge commit, no force push); the cascade in reverse order (studio, sdk, api, runtime, then axentra and orkexa). With V-A, **every** consumer goes back to the same old SHA together (K-34) | `check_versions.py` "pins agree, one tabrion"; required CI green; the old state runs with newly written data (U3 pattern) |
| Consumer code | Every MIG step is **one** pull request with a merge commit, so `git revert -m 1 <merge>` as a new pull request | Test suite equals the state before the step |
| Tabrion tag | Tags are never moved or deleted. A faulty release leads to a patch release `x.y.(z+1)`; consumers pin to the new SHA. After a PyPI approval (G-R), if a publication is decided: `yank` only for security or data-loss bugs, with a CHANGELOG note and a GitHub security advisory | Release notes, advisory link |
| Database schema | No MIG step (S1-S3, S5, local fixes) changes the schemas of the existing platforms. A step that would need DDL is scope growth, so stop | Schema dump before and after identical (`pg_dump --schema-only`, `sqlite3 .schema`) |
| Configuration | New settings (for example `lock_timeout`, `application_name`) can be set back to the old value by environment variable until the step has run stably for one release cycle | Test: variable set, old behavior |

## 3. Rollback per critical step

Principle: every MIG step is a pull request in the consumer; rollback is a revert pull request plus, where needed,
the pin back. No step changes database schemas (otherwise ST-1).

### 3.1 Axentra (required dependency, K-30), per step

| Step (plan document 06) | Preparation | Rollback | Verification |
|---|---|---|---|
| MIG-AXE error classification / `safe_message` (including B-06-1) | Problem JSON and OpenAPI snapshot, canary suite against the old code | Revert pull request; the local B-06-1 fix from MIG-AXE-0 (parameter-free texts, its own pull request merged first right after G-A) and its regression tests stay when only the Tabrion use (MIG-AXE-3) is rolled back | Snapshots identical; MIG-T-AXE-7 green |
| MIG-AXE session configuration / ping | `SHOW` extract (timeouts, `application_name`, `search_path`) per connection kind, including `on_pool` | Revert pull request; environment variables restore the old values | `SHOW` extract equals before; MIG-T-AXE-1..4 green |
| MIG-AXE role check / `grant_runtime_role` | Catalog snapshot plus decision table (8 role variants) from the old code | Revert pull request; in test or staging, run the old `grant_runtime_role` again (idempotent, snapshot equal); production rights are not changed in phase B | Snapshot diff empty; MIG-T-AXE-5/6; `test_rls.py`, `test_rls_tenant_writes.py`, the SR-01 test green |
| MIG-AXE SessionLock (migration lock building block) | Lock tests, including the `CREATE INDEX CONCURRENTLY` path (polling lock) | Revert pull request | Migration tests (`test_migrations.py`, `test_migration_runner_options.py`) green |
| First Tabrion pin in Axentra | Axentra suite plus AC-4 (Axentra alone) green before the pin | Revert the pin; because Tabrion is required, also bring back every helper replaced before (steps in reverse order) | Axentra suite green on the old state; runtime cascade (R-07) consistent |

Every Axentra step needs acceptance by the security review agent (plan document 07 section 8.3); a necessary change
to RLS, `DbScope` or `join`/`on_pool` is ST-13.

### 3.2 Nodestro server profile (error texts, extension status, SessionLock, session configuration, roles)

- **Preparation:** error response snapshot (`problems.py` codes), `SHOW` extract, catalog snapshot after
  `grant_runtime_role` from the old code.
- **Rollback:** revert pull request plus the pin cascade back; the variables `*_LOCK_TIMEOUT=0` and
  `*_APPLICATION_NAME=""` restore the old behavior.
- **Verification:** snapshots identical; SessionLock and LeaderLock tests, the `runtime-role` job and the partition
  tests green; the Axentra integration tests in the server profile (shared pool, M10) green.

### 3.3 Nodestro core `SQLiteStore` (K-32)

- **Preparation:** legacy fixtures (FX-SQ-0, desktop workspaces of older versions) unchanged;
  `PRAGMA foreign_key_check` empty (F-01); the backup duty before a migration stays.
- **Rollback:** revert plus the pin cascade (runtime, api, sdk, studio, desktop). The step changes neither
  `user_version` nor tables; `foreign_keys` is a connection setting.
- **Verification:** the old runtime opens a file written by the new runtime (U6 in both directions);
  `integrity_check = ok`; desktop build (Tauri) green.

### 3.4 Orkexa (`orkexa[postgres]` to `tabrion[postgres]`; core to `tabrion`, E-ORK-CORE = yes, MIG-ORK-3)

- **Preparation:** conformance suite pg-a/pg-b, FX-RANK-0 results before.
- **Rollback:** revert pull request; runtime cascade back.
- **Verification:** conformance and FX-RANK-0 identical; AC-3 core without Tabrion until MIG-ORK-3; when MIG-ORK-3 is
  rolled back, the core is again without Tabrion (AC-3).

### 3.5 JQ: job queue fix (plan document 06)

- **Preparation:** JQ-T-1..4 first red against the unchanged code (fault injection), then the fix.
- **Rollback:** revert pull request in nodestro-runtime; pin cascade back. No schema affected.
- **Verification:** RT2-03 tests green; the JQ tests back as `xfail(strict=True)` so the finding stays visible.

### 3.6 Migration runner: dropped (G-EM = no, owner 23:26Z); kept for reference only

- **Preparation:** dry-run comparison of the old and new runner on FX-PG-0/0v or the Axentra fixtures (same plan
  output, same bookkeeping rows including checksums); the bookkeeping tables (`axentra_schema_migrations`, the
  Nodestro table) keep name and format.
- **Rollback:** revert plus pin back; the old runner reads the rows written by the new one (U3 pattern). Changing the
  bookkeeping format cannot be rolled back and is therefore not allowed (ST-9).
- **Verification:** `migration_checksums.json` unchanged; upgrade from every old state green.

### 3.7 Studio

- A local tool without foreign state: rollback means installing the previous version. The profile file is versioned
  (`schema_version`); an older version rejects a newer file with a message. Keyring entries stay untouched.

## 4. Stop criteria (the agent stops and puts the matter to Peter)

| # | Trigger | Reference (plan document 00 section 7) |
|---|---|---|
| ST-1 | A step would need destructive changes to production data or DDL on existing schemas | Destructive changes / migrations that cannot be rolled back |
| ST-2 | Rolling back a step cannot be tested or fails | Migrations that cannot be rolled back |
| ST-3 | A change to production credentials, repository secrets (especially `NODESTRO_READ`) or branch protection is needed | Credentials |
| ST-4 | A High or Critical finding in the independent review that touches the architecture or RLS, M10 or ADR-0010 | Security-relevant deviation |
| ST-5 | A paid service is needed (for example an external security review, paid CI runners, a trademark filing) | Cost |
| ST-6 | Any public release (PyPI including the first version and pre-releases, npm, GitHub release, announcement); there is no standing approval | G-R per release (Studio: with a human security review, E-QA2) |
| ST-7 | A work package outside the backlog, or substantial core growth or rising complexity against the qualitative criteria K-Q (clear module responsibilities and layering, no needless dependencies or artificial module splits, a traceable justification; owner decision of 2026-11-13 21:44Z, which lifted the fixed line limit), or a new dependency outside plan document 05 | Scope growth |
| ST-8 | Activating Studio write functions (conditional package STU-026) | G-S2 |
| ST-9 | Merging runners or changing bookkeeping formats | Not allowed (G-EM = no); a new proposal to Peter |
| ST-10 | GOV-1 finds a name or trademark conflict | G-0 |
| ST-11 | A new finding with data-loss risk in an existing platform (like F-02) | Scope / priority |
| ST-12 | A cycle or an edge outside K-33 would be needed (especially Tabrion to a Levuvo platform, a library to a Studio) | K-33, scope growth |
| ST-13 | Any change that touches RLS guarantees, `DbScope`, transaction-bound security contexts, `join`/`on_pool` (M10), ADR-0010 or FORCE RLS, or an AX-I test that would turn green only by changing the expectation | Security-relevant deviation |
| ST-14 | AC-6 finds two Tabrion versions in one installation and no consistent cascade is possible without an E-VER decision | K-34 |

On a stop: the finding, options with a recommendation and the affected work packages go into `STATUS.md`;
independent work packages continue.

**ST-10 and GOV-1 (2026-11-08):** GOV-1 reported a possible ST-10 (identical prior use, see R-36). The owner decided
to continue under the name and to check the registers manually before G-T2; the stop is resolved by that decision,
conditional on the check. **Update 2026-11-08 11:26Z:** the owner did not order the check and removed it from
G-T2; a later public marketing under the name needs a new assessment.

## 5. Work packages

| ID | Repository | Description | Prerequisites | Acceptance criteria | Effort (estimate) |
|---|---|---|---|---|---|
| QA-12 | tabrion | Take the risk register over as `docs/program/risks.md` (English) and maintain it in `STATUS.md` per milestone | GOV-2 | Register complete, score review documented per milestone | 0.5 agent days |
| QA-13 | nodestro-runtime, axentra | Snapshot tools for rollback evidence: catalog snapshot plus role decision table, error response and Problem JSON snapshot, `SHOW` extract, schema dump comparison (from the old code) | Before the first MIG-AXE or MIG-NOD step | Scripts reproducible, golden files from the old code | 2 agent days |
| QA-14 | axentra, orkexa, nodestro-* | Rollback probe per MIG-AXE, MIG-ORK, MIG-NOD and JQ step (local revert branch, required suite, U3 pattern) | The step in question | Result in the pull request; no merge without the probe (G-M) | 0.5 agent days per step, about 6 in total |
| (QA-15) | - | Moved to plan document 07 section 10 as AC-6 (one Tabrion version, cycles, policy) | - | - | - |

Total: about 8.5 agent days (estimate); the AC work packages QA-15..QA-21 and the Axentra security evidence QA-25
are in plan document 07 (QA-22 is the conventions audit, plan document 05).

## 6. Change request CR-DB (planning document 14 section 10)

The owner took change request CR-DB (Nodestro database tools, the Tabrion Studio schema designer and the standalone
API export) into the program on 2026-11-08. Its risks are not yet rated with likelihood and impact in the plan; they
are listed here with their countermeasures and get a rating in the M4 score review.

| ID | Risk | Countermeasure | WPs / gates |
|---|---|---|---|
| R-CR-1 | The scope of phase B grows by about 37 % | Own milestones (M9, M10), own gate (G-S3); the critical path is untouched | G-CR, M9, M10 |
| R-CR-2 | Merge conflicts in nodestro-studio, nodestro-api and nodestro-runtime with other threads | Coordination rule, own lane, small pull requests | NOD-CR-* |
| R-CR-3 | S-2 not broadly enabled, so the agent write tools are worthless | The deterministic nodes cover the scenario fully | NOD-CR-5, NOD-CR-7 |
| R-CR-4 | Driver wheels are missing for new Python versions in the future | A check in `check()`, CI matrix | NOD-CR-10, NOD-CR-11 |
| R-CR-5 | Designer migrations for SQLite `ALTER` are error-prone | Rebuild pattern, backup, `foreign_key_check` after the migration | STU-CR-5 |
| R-CR-6 | The name "Tabrion" (GOV-1; register check not ordered, owner decision of 2026-11-08 11:26Z) | Keep the name out of the profile file extension and the extras where a choice exists, so a later rename stays cheap (see R-36) | TAB-CR-1, GOV-1 |
