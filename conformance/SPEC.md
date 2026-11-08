# Levuvo Database Profile (LDP)

**Version:** LDP-0.1.0 · **Status:** internal test asset of Tabrion (K-35, [ADR 0011](../docs/adr/0011-ldp-internal-and-t-notx.md))

The Levuvo Database Profile describes the **observable target behavior** of Tabrion. The machine-readable vectors in
`vectors/*.json` (format `ldp-vectors/1`, schema `schema/vector-file.schema.json`, API contract section 10) check it as
regression and behavior contracts. Only Tabrion's own test suite runs them (loader `tests/support/conformance.py`).
They are not in the wheel. Other repositories do **not** vendor or load them: Axentra, Orkexa and Nodestro get the
behavior through the package dependency and test their integration with their own tests (planning document 07 K11).

## Conventions

- The key words MUST, MUST NOT, SHOULD and MAY are used as in RFC 2119. A **MUST** rule is part of Tabrion's stable
  API (S-2 of the [API stability policy](../docs/development/api-stability.md)): changing its expectation is an API
  break that follows S-3. A **SHOULD** rule is checked like a MUST rule, but a deviation is not a break. **INFO**
  vectors record default values. They are checked as well, but are not normative.
- Each rule has an ID `<AREA>-<n>`. Each vector names its rule in `rule` and has an ID `LDP-<AREA>-<nnn>`.
- Every MUST rule has at least one vector. `tests/conformance/test_loader.py` checks that against the rule table
  below.
- Vectors contain no real credentials. Redaction vectors use the marker `SECRET-4711` and expect it to be absent.
- **Versioning:** LDP has a SemVer of its own (`LDP-MAJOR.MINOR.PATCH`), independent of the package version. A
  changed MUST expectation is a MAJOR change. A new rule, area or action is a MINOR change. A new vector for an
  existing rule is a PATCH change. `MANIFEST.json` holds the SHA-256 of every file and a digest over all of them.
  CI fails unless `CHANGELOG.md` names the current digest (`LDP digest <first 12 hex digits>`), so no expectation
  changes without a CHANGELOG entry.

## Rules

| Rule | Level | Area | Rule text | Reference behavior |
|---|---|---|---|---|
| SQP-1 | MUST | sqlite-profile | A database file that Tabrion creates has the mode `0600` (POSIX). | Orkexa `_storage.py` `PRIVATE_FILE_MODE`, `create_private_file` |
| SQP-2 | MUST | sqlite-profile | A symlink at the database path or at its `-wal`, `-shm` or `-journal` file is refused with `UnsafePath("sqlite.symlink")`, whether its target exists or not. | Orkexa `_storage.py` `check_database_path` |
| SQP-3 | MUST | sqlite-profile | Every connection has `foreign_keys` on. | Nodestro `storage.py`, Orkexa `_storage.py` |
| SQP-4 | MUST | sqlite-profile | Every connection has a `busy_timeout` of at least 1 s. | Nodestro `storage.py` |
| SQP-5 | MUST | sqlite-profile | A file database runs in WAL mode under the default, Orkexa and Nodestro profiles. | Nodestro `storage.py`, Orkexa `_storage.py` |
| SQP-6 | INFO | sqlite-profile | Defaults: `synchronous=FULL`, `secure_delete` on, `trusted_schema` off, `busy_timeout` 30 s. | Specification section 4.1 |
| SQP-7 | MUST | sqlite-profile | Opening an existing file never changes its mode. | Contract section 3 (`connect`) |
| SQT-1 | MUST | sqlite-transactions | Writes start with `BEGIN IMMEDIATE`: concurrent read-modify-write transactions lose no update and raise no lock error. | Nodestro `storage.py` `transaction` |
| SQT-2 | MUST | sqlite-transactions | A read transaction has no write right: a write inside it is refused with kind `read_only`, and nothing changes. | Contract section 3 (`read_transaction`) |
| SQM-1 | MUST | sqlite-migrate | The schema version is `PRAGMA user_version`; a successful migration sets it to the target. | Nodestro `storage.py` `migrate` |
| SQM-2 | MUST | sqlite-migrate | A database newer than the target is refused with `SchemaTooNew` and stays unchanged. | Nodestro `storage.py` `migrate` |
| SQM-3 | MUST | sqlite-migrate | Migrating a database that is not fresh needs a backup: without one it is refused with `BackupRequired`; a failing backup stops it with `BackupFailed`. Nothing changes in either case. | Nodestro `storage.py` `migrate` |
| SQM-4 | MUST | sqlite-migrate | All steps run in one transaction: a failing step leaves the data and `user_version` unchanged (`MigrationFailed`). | Nodestro `storage.py` `migrate` |
| SQM-5 | SHOULD | sqlite-migrate | A fresh, empty database migrates without a backup. | Nodestro `storage.py` `migrate` |
| SQM-6 | MUST | sqlite-migrate | Tabrion ends no transaction it did not open (T-NOTX): `migrate` inside an open transaction is refused with `UsageError("sqlite.nested_transaction")`, and the transaction stays open. | ADR 0011, contract T-NOTX |
| PGS-1 | MUST | postgres-session | Only the allowlisted GUCs are set (R-SET); any other setting is refused with `ConfigurationError("postgres.setting_not_allowed")`. | Contract R-SET |
| PGS-2 | MUST | postgres-session | The default session has `statement_timeout` and `idle_in_transaction_session_timeout` greater than 0 and sets `application_name`. | Nodestro `storage_postgres.py` session settings; Axentra `database.py` `_configure` |
| PGS-3 | SHOULD | postgres-session | The default session sets a `lock_timeout` greater than 0. | Specification section 4.2 |
| PGS-4 | MUST | postgres-session | `configure` never changes a custom setting (`app.*`) or the role of the session. | Contract R-SET, TAB-T-G02 |
| PGS-5 | INFO | postgres-session | Default values: `statement_timeout` 30 s, `lock_timeout` 10 s, `idle_in_transaction_session_timeout` 5 min, `TimeZone` UTC, `application_name` `tabrion`. | Specification section 4.2 |
| PGR-1 | MUST | postgres-roles | A superuser, a role with `BYPASSRLS` and the owner of a table with row-level security are reported as not restricted; a plain LOGIN role is restricted. | Axentra `roles.py` `inspect_role`; Nodestro `storage_postgres.py` role checks |
| PGR-2 | MUST | postgres-roles | `grant_runtime_role` gives the runtime role `SELECT`, `INSERT`, `UPDATE` and `DELETE` on the schema's tables, and only `SELECT` on the protected (bookkeeping) tables. | Axentra `roles.py` `grant_runtime_role`; Nodestro `storage_postgres.py` grants |
| PGR-3 | MUST | postgres-roles | `grant_runtime_role` refuses to run inside an open transaction (`UsageError("postgres.nested_transaction")`) and leaves that transaction open (T-NOTX). | ADR 0011, contract T-NOTX |
| PGL-1 | MUST | postgres-locks | An advisory lock held by one session cannot be taken by another until it is released. | Axentra `migrations.py` `_wait_for_lock`; Nodestro `storage_postgres.py` |
| PGL-2 | MUST | postgres-locks | `SessionLock` counts holds: a key taken twice is held until it is released twice. | Contract section 4 (`SessionLock`) |
| PGL-3 | MUST | postgres-locks | A lock lost with its connection is detected: `holds()` is false afterwards. | Contract section 4 (`SessionLock`) |
| PGL-4 | MUST | postgres-locks | Waiting for a lock polls: it gives up after its timeout and keeps no statement or snapshot open while it waits. | Axentra `migrations.py` `_wait_for_lock` |
| PGX-1 | MUST | postgres-extensions | An extension the server does not offer is reported as `unavailable`, with no command. | Contract section 4 (`extension_status`) |
| PGX-2 | SHOULD | postgres-extensions | An installed extension is reported with its installed version. | Contract section 4 (`extension_status`) |
| ERC-1 | MUST | errors-classification | Each SQLSTATE and SQLite result code maps to its `Kind` (contract section 1.3), with `retryable` set as the mapping table says. | Axentra `core/errors.py`, Nodestro and Orkexa error mapping |
| ERC-2 | MUST | errors-classification | A lost connection after the statement was sent, or during `COMMIT`, is `outcome_unknown` and never `retryable`, so no write is repeated automatically. | Contract section 1.3 (F-02) |
| ERR-1 | MUST | errors-redaction | `safe_message` and a log record through `DbErrorRedactionFilter` contain no parameter value and no `DETAIL` text. | Axentra `install_db_error_redaction` (MIG-AXE-0) |
| SPL-1 | MUST | sql-split | `split_statements` splits at semicolons outside quotes, quoted identifiers, comments and dollar quotes, and drops parts without code. | Axentra `migrations.py` `split_statements` |

## Appendix A: action reference

`args` and `expect` per action. A key absent from `expect` is not checked. `error` names the expected
`TabrionError.code`; `kind` names the expected `Kind` value of the classification.

| Action | Engine | `args` | `expect` |
|---|---|---|---|
| `open_new_file` | sqlite | `profile` (`default`, `orkexa`, `nodestro`); optional `symlink` (`main`, `-wal`, `-shm`, `-journal`) and `target_exists` | `file_mode` (octal text), `journal_mode`, `foreign_keys`, `busy_timeout_ms_min`, or `error` |
| `open_existing` | sqlite | `mode` (octal text of the existing file), `profile` | `file_mode`, `foreign_keys` |
| `inspect_pragmas` | sqlite | `profile`, `memory` (bool) | the `SQLiteDiagnosis` fields named |
| `concurrent_read_modify_write` | sqlite | `threads` | `final` (the counter after all threads), `lock_errors` |
| `write_in_read_transaction` | sqlite | `statement` (run inside `read_transaction` after `setup`) | `kind`, `rows` (`SELECT count(*)` of table `t` afterwards) |
| `migrate` | sqlite | `initial_version`, `steps` (version to a list of SQL statements), `target`, `backup` (`none`, `ok`, `fail`), `in_transaction` | `error`, `user_version`, `tables` (user tables afterwards), `backed_up`, `in_transaction_after` |
| `session_settings` | postgres | `settings` (`SessionSettings` fields), optional `extra_field`; `setup` runs first | `show` (GUC to `SHOW` value), `unchanged` (GUCs equal before and after), `error` |
| `role_report` | postgres | `attributes` (role attributes), `owns_rls_table` (bool) | `restricted`, `superuser`, `bypass_rls`, `table_owner` |
| `grant_runtime_role` | postgres | `tables`, `protected_tables`, `in_transaction` | `privileges` (table to sorted privilege list), `error`, `transaction_status` |
| `lock_acquire` | postgres | `steps`: a list of `[session, operation]` with the operations `try_acquire`, `acquire_timeout_0.3`, `release`, `holds`, `waiter_state` | `results`: the result of each step (`null` for `release`) |
| `lock_connection_lost` | postgres | none | `holds_after_loss` |
| `extension_status` | postgres | `name` | `state`, `command`, `has_installed_version` |
| `classify_error` | any | `driver` (`psycopg` with `sqlstate` and `message`, or `sqlite3` with `statement` after `setup`), `sent`, `during_commit` | the `Classification` fields named (`kind` as its value) |
| `redact_error` | any | as `classify_error`, plus `detail` (psycopg) or `params` (sqlite3) | `absent` (texts in neither the safe message nor the logged record), `contains` |
| `split_sql` | any | `sql` | `statements` |

## Changes

- **LDP-0.1.0** (TAB-050): first version: 33 rules in ten areas.
