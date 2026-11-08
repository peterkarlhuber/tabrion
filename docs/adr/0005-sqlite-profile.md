# ADR 0005: SQLite profile

- **Status:** Accepted (2026-11-07). It depended on **E-T01** (`synchronous=FULL` as the default); the recommended
  option was taken. E-06-4 (Nodestro keeps FULL) follows from it.
- **Plan reference:** TAB-ADR-0005 (planning document 10)

## Context

- Probe p.py: the SQLite default is `foreign_keys=OFF` and journal mode `delete`; new files are created with 0644.
- q.py: deferred transactions lose 4 of 5 updates. q2.py: with `autocommit=False` there are 4 "database is locked"
  errors. Only `BEGIN IMMEDIATE` gives 5 of 5.
- Orkexa sets 0600 before opening and rejects symlinks, including `-wal`, `-shm` and `-journal`
  (`orkexa/src/orkexa/_storage.py:80-115`). Nodestro does neither.
- Nodestro's snapshot file is created with 0644 (B-06-3, `storage.py:649-659`).
- F-01: no existing SQLite schema declares `REFERENCES`.

## Decision

Defaults of `SQLiteProfile` (specification section 4.1):

| Area | Rule |
|---|---|
| Write transaction | `BEGIN IMMEDIATE`; read transaction deferred with `PRAGMA query_only=ON` |
| Driver | `isolation_level=None` (no implicit driver transaction) |
| Journal and locking | `journal_mode=WAL`, `busy_timeout` 30 000 ms |
| Integrity and durability | `foreign_keys=ON`, `synchronous=FULL` (E-T01), `secure_delete=ON` (Nodestro's own `SQLiteStore` keeps `OFF`), `trusted_schema=OFF` |
| Files | New files `0600` through `O_CREAT\|O_EXCL\|O_NOFOLLOW` **before** `sqlite3.connect`; symlinks rejected for the main, `-wal`, `-shm` and `-journal` files; the error message has no path |
| Backups | Written as a 0600 temporary file in the target directory, then `quick_check` and `os.replace`; the target must not be a symlink |
| Extensions | Loaded only inside the context manager `loading_extensions()` |
| Read-only open | `connect(read_only=True)`: URI `mode=ro` plus `query_only` |
| Migrations | `migrate()` on `user_version`, with a required backup for a non-empty database; `SchemaTooNew` / `MigrationMissing` / `BackupRequired` / `BackupFailed` / `MigrationFailed`; hook `is_fresh` |
| Predefined profiles | `Profile.edge()`: `synchronous=NORMAL`, `secure_delete=OFF` for measurement bulk loads, documented |

## Rationale

- Correctness before throughput.
- The values match what Orkexa and Nodestro already do by hand. New are the gaps: foreign keys, file permissions in
  Nodestro and `trusted_schema`.

## Rejected alternatives

| Alternative | Reason |
|---|---|
| `synchronous=NORMAL` as the default | Consistent with WAL but can lose the last transaction on power loss; only as an opt-in (`Profile.edge()`) |
| SQLAlchemy or Python default transactions | Proven lost update or lock errors (q.py, q2.py) |
| `foreign_keys` off for compatibility | F-01: no existing foreign key, so neutral; wrong for new applications |
| File permissions through `os.chmod` after opening | Time window (TOCTOU); WAL and SHM inherit the permissions too early |

## Consequences

- Nodestro adopts the profile in `SQLiteStore` (MIG-NOD-6, K-32) with `synchronous=FULL`, `foreign_keys=ON`,
  `busy_timeout` 30 s, WAL and `secure_delete=OFF`. This closes B-06-3 (snapshot 0600) and B-06-5: today
  `nodestro.db` is created with umask permissions through `SQLiteStore` (`RT/workspace.py:338-339`) before Orkexa's
  0600 rule applies (`:349`). Afterwards one source sets the rules for that file.
- Existing world-readable files are set to 0600 in MIG-NOD-9 (E-NOD-PERM = yes, decided 23:26Z): Desktop workspaces
  only, after a compatibility check, with safeguards for existing files (permissions only, no foreign owner or
  symlink, a log note, a warning instead of an abort).
- E-NOD-SQ is dropped.
- Tabrion itself does not change existing files; `diagnose()` reports world-readable files (`TAB-SQ-003`); Nodestro
  tightens them (MIG-NOD-9).
- Orkexa adopts `tabrion.sqlite` in MIG-ORK-3 (E-ORK-CORE = yes) once Tabrion 0.1.x is final and stable; until then
  `_storage.py` stays.

## Security guarantees

- Private files are never group- or world-readable and cannot be redirected through a symlink.
- Backups are protected the same way.
- `trusted_schema=OFF` stops views and triggers from calling unsafe functions.

## Verification

- TAB-T-S01..S07: permissions, symlinks and the race between the check and `open`.
- TAB-T-S13..S15: profile.
- TAB-T-S16..S21: transactions, including the lost-update scenario 5/5 and `query_only` back to OFF afterwards.
- TAB-T-S22..S30: `migrate`.
- TAB-T-S31..S34: `backup`.
- TAB-T-B07, B22: layer B.
- MIG-NOD-6: U1-U8, legacy fixtures FX-SQ-0, `PRAGMA foreign_key_check` empty, the B-06-5 test (`nodestro.db`,
  `-wal`, `-shm` 0600), snapshot 0600, the symlink test, desktop builds on three operating systems (RC-6).
  MIG-ORK-3: conformance of files, memory and index, PS-8. MIG-NOD-9: a test with an existing 0644 file.

## Revision triggers

- RA-B measurements show that `secure_delete` or FULL slows the measurement use case unacceptably (then adjust the
  profile recommendation, not the default).
- SQLite changes its WAL behavior (for example `wal2`).

## Changes from the plan

- `SQLiteProfile.allow_shared_file` is a field in the M1 stubs; the API contract mentions it only in prose. Part of
  the G-T1 review (`docs/api/contract.md`, open points).
