# Ecosystem migration to Tabrion

This record documents how Axentra, Orkexa and Nodestro moved their generic database building blocks to Tabrion
(work package QA-23). It covers waves W1 to W6, the pin waves PIN-W1 to PIN-W6 and the joint switch to Tabrion
`0.2.1`. Planning document 06 (ecosystem consolidation and migration plan) holds the plan. Planning document 13
(ecosystem dependency architecture) holds the target architecture: the allowed edges K-33, the before/after picture per
platform and the versioning rule E-VER. Per-package status is in [STATUS.md](../program/STATUS.md).
For how a platform uses layer A, see [Existing platforms](../guides/integration-existing-platforms.md).

Status: 2026-11-14. Every pull request below is merged unless it is marked as open. SHAs are merge commits. Each
consumer's `docs/api-consumers.md` lists the exact Tabrion names it imports (QA-7, QA-21).

## Overview

| Wave | Content | Code pull requests | Pin step (studio lock) |
|---|---|---|---|
| W1 (S1) | Test fixtures through `tabrion.testing`, VER-1 pins | axentra #78, orkexa #91, nodestro-runtime #156, nodestro-api #123 | PIN-W1: rides the CI-FAST lock switch, studio #177 `20b8720` and #179 `479ae56` |
| W2 (S2a) | Value-free database error texts (B-06-1, B-06-2) | axentra #79, nodestro-runtime #163, nodestro-api #132 | PIN-W2: studio #185 `73d14b5`, desktop #74 `28d62cf` |
| W3 (S2b) | Session settings, session and advisory locks | axentra #84, nodestro-runtime #167, orkexa #94 | PIN-W3: studio #189 `e8b091d` |
| W4 (S2c) | Statement splitter, extension status | axentra #86, nodestro-runtime #168 | PIN-W4: folded into studio #190 `2058ee3` |
| W5 (S3) | Role tools | axentra #87, nodestro-runtime #169 | PIN-W5: studio #190 `2058ee3` |
| W6 (S4) | SQLite on `tabrion.sqlite`, file permissions | nodestro-runtime #171, #176, orkexa #97, nodestro-api #136, #138 | PIN-W6: studio #192 `9cae375`, desktop #76 `0493679` |
| 0.2.1 | Every consumer moves to `tabrion>=0.2,<0.3` | orkexa #99, nodestro-runtime #178, nodestro-api #139, axentra #94 | studio #193 `25095d2`, desktop #77 `66feb6c` |

## Before and after per repository

### axentra

| Before | Tabrion now | Package | Pull request (merge) |
|---|---|---|---|
| Local B-06-1 fix `core/db_errors.py` (Tabrion-independent stopgap) | (none, replaced by MIG-AXE-3) | MIG-AXE-0 | #61 (`9c4666f`) |
| Pin of Tabrion `v0.1.0` in `requirements/tabrion.txt`, guard test, CI with pip 25.0 and current pip | VER-1 pin file | VER-1 | #76 (`1afe7bb`), #77 (`e18c6f3`) |
| Own PostgreSQL test fixtures (database per session, split roles, truncate) | `tabrion.testing` (`configure`, `PostgresDatabaseHandle`, `RoleSetup`, `truncate_tables`) | MIG-AXE-1 | #78 (`1dbd98f`) |
| Own redaction of database errors in logs | `tabrion.errors` (`DbErrorRedactionFilter`, `safe_message`, `classify`) | MIG-AXE-3 | #79 (`b91e05a`) |
| Pool `_configure`, scheduler leader lock, polling migration lock | `tabrion.postgres` (`configure`, `SessionSettings`, `session_configurator`, `SessionLock`, `wait_advisory_lock`, `advisory_unlock`) | MIG-AXE-4/5 | #84 (`410269c`) |
| Own statement splitter | `tabrion.sql.split_statements` (golden: 240 statements identical) | MIG-AXE-2 | #86 (`3868634`) |
| Own role tools in `roles.py` | `roles.py` as a shim over `tabrion.postgres` (`grant_runtime_role`, `inspect_role`, `role_info`); stricter role check | MIG-AXE-6 | #87 (`6c6fe1b`) |
| A Tabrion from any index | Refused (installed-commit test, poisoned-index CI job) | DC-AX-1 | #83 (`f7e249b`) |
| `tabrion` `0.1` pin | `tabrion[postgres]>=0.2,<0.3`, pin file at `af0458d` | 0.2.1 switch | #94 (`3b80af1`) |

### orkexa

| Before | Tabrion now | Package | Pull request (merge) |
|---|---|---|---|
| No Tabrion | `requirements/tabrion.txt` pin with guard test, CI with pip 25.0 and latest pip | VER-1 | #88 (`68cb46c`), #90 (`905947c`); CI prerequisite #89 (`8e917fe`) |
| Own PostgreSQL test support | `tabrion.testing` (`configure`, `require_extension`, `tabrion_postgres_url`) | MIG-ORK-1 | #91 (`3165be2`) |
| A Tabrion from any index | Refused (installed-pin test, poisoned-index CI leg) | Dependency confusion (DC) | #92 (`7981386`) |
| `PsycopgSource` without timeouts or ping, raw error texts | `tabrion.postgres` (`SessionSettings`, `session_configurator`, `ping`), `tabrion.errors` (`classify`, `safe_message`) | MIG-ORK-2 | #94 (`2474d12`) |
| Core `_storage.py` with its own connection and three transaction context managers | `tabrion.sqlite` (`connect`, `SQLiteProfile`, `read_transaction`, `write_transaction`), `tabrion.errors.UnsafePath` | MIG-ORK-3 (E-ORK-CORE) | #97 (`efc41c4`); policy edge in nodestro-studio #191 (`2540138`) |
| `tabrion` `0.1` pin | `tabrion>=0.2,<0.3` (core), pin file at `af0458d` | 0.2.1 switch | #99 (`3d2aef5`) |

### nodestro-runtime

| Before | Tabrion now | Package | Pull request (merge) |
|---|---|---|---|
| Own PostgreSQL test helpers (`tests/postgres_support.py`) | `tabrion.testing.configure` | MIG-NOD-1 | #156 (`56ae282`) |
| Tabrion dev pin by URL | Minor range; the commit lives in the studio lock | VER-1 (CI-FAST) | #159 (`e05f51d`) |
| Backup and migration errors with values | `nodestro_runtime.error_texts` over `tabrion.errors.safe_message` | MIG-NOD-3 | #163 (`9e0a73d`) |
| A Tabrion from any index | Refused (installed-commit test, poisoned-index job) | DC-NR-1 | #164 (`ac7b16a`) |
| Own session lock and `_configure` | `tabrion.postgres` (`SessionLock`, `configure`, `SessionSettings`); `SHOW` golden unchanged | MIG-NOD-4 (+8) | #167 (`fa7652c`) |
| Own `pgvector_status` query | `tabrion.postgres.extension_status` | MIG-NOD-7 | #168 (`52451df`) |
| Own role tools | `tabrion.postgres` (`grant_runtime_role`, `inspect_role`, `role_info`); stricter role check | MIG-NOD-5 | #169 (`39e8524`); follow-ups #174 (`213a6e4`), #177 (`f04e212`) |
| Own `SQLiteStore` connection, transaction, `user_version` migration and snapshot (also `sqlite_tables.py`, `postgres_import.py`) | `tabrion.sqlite` (`connect`, `write_transaction`, `migrate`, `backup`); new files 0600, symlinks refused, FK on, `trusted_schema` off, `secure_delete` on (E-NOD-SD) | MIG-NOD-6 | #171 (`3c11de4`) |
| Existing world-readable desktop `nodestro.db` | Repair of the file modes only after the user consents (`confirmed=True`), all or nothing | MIG-NOD-9 (E-NOD-PERM) | #176 (`d06a17f`) |
| `tabrion` `0.1` range | `tabrion>=0.2,<0.3` (base), `tabrion[postgres]>=0.2,<0.3` (`postgres` extra) | 0.2.1 switch | #178 (`8ee7eca`) |

### nodestro-api

| Before | Tabrion now | Package | Pull request (merge) |
|---|---|---|---|
| Own PostgreSQL skip/require rule in 23 test files | `tabrion.testing` (dev extra) | MIG-NOD-1 | #123 (`fff4d6a`) |
| Tabrion dev pin by URL | Minor range | VER-1 (CI-FAST) | #126 (`dc0ca17`) |
| Server logs and `pg_dump` stderr with values | `tabrion.errors.DbErrorRedactionFilter` (`db_log_redaction.py`); exports name Tabrion by Git commit; the portable build builds Tabrion from a hash-pinned archive (DC-1) | MIG-NOD-3 (api) | #132 (`97b1ca1`) |
| No repair path for a refused desktop database | Recovery start mode with two guarded endpoints (repair, restart) | MIG-NOD-9 (api) | #136 (`0031251`); SDK contract nodestro-sdk #122 (`f604286`) |
| Portable export resolved `tabrion` by name once orkexa core required it | Tabrion skipped in requirements and closure, built from the pinned archive | MIG-ORK-3 follow-up | #138 (`6ae4781`) |
| `tabrion` `0.1` range | `tabrion[testing,postgres]>=0.2,<0.3` (dev); runtime brings `tabrion` at run time | 0.2.1 switch | #139 (`6ef484d`) |

nodestro-api has no `docs/api-consumers.md` of its own: it imports only `tabrion.errors.DbErrorRedactionFilter`
(tested in `tests/test_db_error_texts.py`), see the QA-7 note below.

### nodestro-studio (lock)

| Before | After | Package | Pull request (merge) |
|---|---|---|---|
| Pin cascade with URL pins per component | One Tabrion per installation in `check_versions.py`, RC-7 drift job | VER-1, QA-15 | #176 (`ac98031`) |
| Pin cascade | Version ranges and one lock (`platform/internal.txt`, `internal-server.txt`); lock at the component masters | CI-FAST, PIN-W1 | #177 (`20b8720`), #179 (`479ae56`) |
| No origin check of Tabrion | Canonical `check_tabrion_source.py`, `lock_check --only` | DC-NS-1/2 | #182 (`bbb53c9`) |
| Tabrion only in the server lock | Tabrion in the studio profile (K-32); runtime `9e0a73d`, api `97b1ca1` | PIN-W2 | #185 (`73d14b5`) |
| | axentra `410269c`, orkexa `2474d12` | PIN-W3 | #189 (`e8b091d`) |
| | axentra `6c6fe1b`, runtime `f04e212` (W4 and W5 code) | PIN-W4, PIN-W5 | #190 (`2058ee3`) |
| Edge orkexa to tabrion pending | Edge active in `dependency-policy.json` | E-ORK-CORE | #191 (`2540138`) |
| | runtime `d06a17f`, orkexa `efc41c4`, api `6ae4781`; recovery dialog in six languages | PIN-W6, MIG-NOD-9 (UI) | #192 (`9cae375`) |
| Tabrion `d5821fa` (0.1.0) in the lock | Tabrion `af0458d` (0.2.1); releases map 0.1.0, 0.2.0, 0.2.1 in `platform/versions.json` | 0.2.1 switch | #193 (`25095d2`) |

### nodestro-desktop

| Before | After | Package | Pull request (merge) |
|---|---|---|---|
| Backend built without Tabrion in the lock | Backend from studio `73d14b5` (Tabrion in the bundle) | PIN-W2 | #74 (`28d62cf`) |
| No restart after a refused database | Backend restart after the consented repair (exit 75/76), studio `9cae375` | PIN-W6, MIG-NOD-9 | #76 (`0493679`) |
| Studio `9cae375` | Studio `25095d2` (Tabrion 0.2.1 lock) | 0.2.1 switch | #77 (`66feb6c`) |

## Compatibility overview

| Item | Value |
|---|---|
| Tabrion during W1 to W6 | `0.1.0`, release commit `d5821fa` (every studio lock from #177 to #192) |
| Tabrion `0.2.0` | release commit `52353da` (tabrion #65); never locked by the studio |
| Tabrion after the switch | `0.2.1`, release commit `af0458d` (tabrion #71), G-T2 passed 2026-11-14 |
| Consumer requirement | `tabrion>=0.2,<0.3` in axentra, orkexa, nodestro-runtime and nodestro-api (with extras as above); no URL pins in manifests (RC-1) |
| Standalone pin files | axentra and orkexa `requirements/tabrion.txt` pin `af0458d` by full SHA |
| Studio lock | nodestro-studio `25095d2`: tabrion `af0458d`, orkexa `3d2aef5`, nodestro-runtime `8ee7eca`, nodestro-api `6ef484d`, axentra `3b80af1` |
| api portable build | `portable_locks/tabrion.txt` names the archive of `af0458d` with a SHA-256 hash |
| pip floor | axentra and orkexa test pip 25.0 and the current pip. nodestro-runtime, nodestro-api and nodestro-studio require `pip>=26.1` (binding owner decision of 2026-11-14 18:52Z): the lock uses `--uploaded-prior-to` and its git references as constraints; nodestro-studio `scripts/pip_cutoff.py` refuses an older pip early with an upgrade hint (#197, `358e2bb`). pip 26 accepts a pre-release when it is the only fit (documented deviation in nodestro-studio #194) |

The API stayed compatible: `0.2.1` has no API change against `0.2.0`, and the consumer suites passed on `af0458d`
before the range switch (G-T2 nightly downstream job 132432986041). The QA audit of 2026-11-14 found the
nodestro-runtime and nodestro-api master runs red only because they started before studio #193 moved the lock. This
was a stale lock, not a code defect, and both were rerun.

## Test evidence

- **Before/after snapshots (QA-13, plan doc 06 field 8).** The golden files were recorded from the old code and
  stayed byte-identical unless a change was intended and approved:
  - nodestro-runtime `tests/test_rollback_snapshots.py`: `settings.json` unchanged (#167), `catalog.json` unchanged
    (#169), `roles.json` rewritten for the superuser variant only (owner decision 2026-11-14 02:05Z),
    `errors.json` only the three value-carrying cases (#163).
  - nodestro-runtime `tests/test_sqlite_store_snapshots.py`: intended differences only (0600, symlink refusal, FK on,
    `trusted_schema` off); `secure_delete` is listed as platform-dependent.
  - axentra `tests/unit/test_split_statements_golden.py` (240 statements identical, #86); QA-25 security evidence
    MIG-T-AXE-1..9 (axentra #64); `catalog.json` identical, `roles.json` changed for the stricter check (#87).
  - orkexa `tests/test_sqlite_storage_snapshots.py` (#97).
- **QA-7 (direct consumer tests).** axentra, orkexa and nodestro-runtime each have `docs/api-consumers.md` (23, 24 and
  31 names) checked by `check_tabrion_api.py --check-export` in CI, and every name has a test. nodestro-api has its own
  list and CI step (#142, `8155ad8`). `--surface` checks `__x__` names by their owner in every copy (source
  tabrion-studio #113, `a94cdc3`), and axentra's lint job runs it (#96, `8369bf3`).
- **QA-16 (import contracts).** axentra, orkexa and tabrion-studio `pyproject.toml` `[tool.importlinter]` plus
  `tests/architecture/test_forbidden_probe.py` (one mutation probe per contract; tabrion-studio `33b8e4b`, green on
  master `ff3d88e`, run 43621888750).
- **QA-17 (dependency policy).** nodestro-studio `tests/test_dependency_policy.py` (8 negative cases, cycles);
  vendored copies with red probes in axentra, orkexa, nodestro-api #140 (`f1755fe`), nodestro-integrations #91
  (`2db82e7`), nodestro-examples #105 (`39c8afa`) and nodestro-runtime #180 (`f7fc3a1`).
- **QA-18 (optional edges).** `test_optional_edges.py` in orkexa, nodestro-runtime and nodestro-api (blocked module,
  probe, meta test against K-33).
- **QA-19/QA-20.** nodestro-studio #194 adds `.github/workflows/tabrion-compat.yml` and `scripts/tabrion_compat.py`. It
  is manual (`workflow_dispatch` with `tabrion_ref`) and report only. Jobs: the consumer suites at a given Tabrion ref
  (QA-20), five clean-install cells and negative resolution cases with pip 25.0 and the latest pip (QA-19). Workflow
  review: accept with conditions, C-1 fixed (`35ab074`). Merged as `5ecf681`. The first dispatch, run 43623198089
  against Tabrion master on 2026-11-14, is green in every job: all four consumer suites, all clean-install cells and
  all negative resolution cases on pip 25.0 and the latest pip.
- **Security reviews.** axentra MIG-AXE-0 (#61), runtime #169 and #171, axentra #87, runtime #176 (MIG-NOD-9), the
  repair flow (api #136, studio #192, desktop #76), and a re-check of the #171 conditions (C1, N1, N2 and N4 met; N3
  tracked as a gate).

## Rollback

Plan document 06 section 12.3 is the rule: every step is reverted per repository, and no step changes a schema or data.

| Step | How a consumer goes back |
|---|---|
| MIG-AXE-1/3/4/5/2, MIG-ORK-1/2, MIG-NOD-1/3/4/7 | Revert the MIG pull request, then move the studio lock back to the earlier component commit |
| MIG-AXE-6, MIG-NOD-5 | Revert, then roll the wave backwards (studio, sdk, api, runtime with the older axentra commit); grants are idempotent; rerun the catalog snapshot |
| MIG-NOD-6, MIG-ORK-3 | Revert and repin; new files keep 0600 (intended); `user_version` is unchanged |
| MIG-NOD-9 | Revert runtime #176, api #136, studio #192 and desktop #76 together; a repair already made only narrowed file modes |
| 0.2.1 switch | Revert the range pull requests (orkexa #99, runtime #178, api #139, axentra #94) and studio #193; the lock goes back to `d5821fa` with ranges `>=0.1,<0.2` |
| VER-1 | Revert (the checks go off) |

Rollback probes (QA-14) are recorded in the pull request bodies, for example axentra #78, orkexa #91,
nodestro-runtime #156, nodestro-api #123: the reverted tree equals the earlier master, the collect diff is empty and
the snapshots pass. Wave pull requests from W2 on carry a "Rollback" section (for example runtime #163, #167, #168,
#171, api #132). A central QA-14 record for every step does not exist yet.

## What was not migrated

By design (plan document 06 section 9):

| Function | Platform | Reason |
|---|---|---|
| `Database`, pool, `on_pool`, `joined`, `DbScope`, `set_config(..., true)`, `Tx` | Axentra | K-30, RLS core, NOD-M10 |
| 16 local `except pg_errors.*` sites | Axentra | Domain mapping |
| Transaction advisory locks, `ping` | Axentra, Nodestro | No gain, own semantics |
| Pool metrics, secret references, LISTEN connection, records, queues, search, rate limits | Axentra | Operations or domain |
| Migration runners (S6/S7) | Axentra, Nodestro | G-EM = no (ADR 0007) |
| Statement catalog, partition guard, `sqlcheck`, conformance suite, schema mechanics, sqlite-vec, ranking | Orkexa | K-31 |
| `PsycopgSource` pool and `tenant_setting` | Orkexa | Orkexa contract; only settings, ping and errors use Tabrion |
| Pool, `SharedPool` (NOD-M10), `DatabaseSource`, tenant checker, jobs, identity, document store, leader logic | Nodestro | Domain; only the lock building block moved |
| Desktop backup contents, `pg_dump` scripts, inline `SQLiteStore` test setup | Nodestro | Domain or no gain |
| Move to SQLAlchemy | Nodestro, Axentra | ADR 0003, K-30 |
| Vendoring of the LDP vectors | all | K-35 |

Deferred or open after W6 (owner acceptance of the migration report, 2026-11-14 18:52Z):

- **Windows reparse points and OneDrive (RT-080).** A binding gate before the first desktop release: the refusal is
  checked on a real Windows machine (nodestro-desktop TODO DSK-021, #78 `4ae3dec`; nodestro-runtime #181 `f8e4308`).
- **Nodestro pip floor: decided.** `pip>=26.1` is binding for nodestro-runtime, nodestro-api, nodestro-studio, the
  server image and the desktop backend (nodestro-studio #197 `358e2bb`, nodestro-desktop #78, nodestro-server #89
  `665bc67`). axentra and orkexa keep pip 25.0.
- **`POISON_VERSION`: done.** The shared `check_tabrion_source.py` serves a poisoned `0.2.99` (inside `>=0.2,<0.3`,
  above the pinned `0.2.1`): nodestro-studio #198 (`60bc2f1`), nodestro-runtime #182 (`41deb16`), orkexa #102 (`c5c69dd`), axentra #97 (`aea059f`) and
  tabrion-studio #110 (`782bc8e`).
- **QA-7 and QA-16 rest: done.** See the test evidence above.
- **`pg_write_all_data`: fixed (TB-150, D-124).** `grant_runtime_role` refuses a member of any predefined role before
  granting and re-checks inside the transaction (#77, `bf6b88c`). axentra words the new reason `predefined_role` (#98,
  `e64ed63`). Open: TB-036 (`check_role`/`diagnose` do not report predefined roles) and the write re-check after the
  REVOKE (TB-150 item 3).
- **Lock drift.** The studio lock holds axentra `3b80af1` (master `b64edbb`, docs only) and nodestro-integrations
  `62138e2`.
- **CR-DBX** (planning document 15): implementation not started. Migration including W6 is its start condition S-3.
