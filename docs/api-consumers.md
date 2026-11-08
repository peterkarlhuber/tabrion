# API consumers

This page lists which public Tabrion functions the Levuvo consumers (Axentra, Nodestro, Orkexa) use, and which
consumer test covers each one. It is plan document 07, test category K11: every public function a consumer uses is
covered by at least one consumer test.

## Where the evidence comes from

**Golden replay (blocking).** No consumer depends on Tabrion yet. Until the migration steps MIG-AXE, MIG-NOD and
MIG-ORK land, a consumer test means the consumer's own code:

1. `scripts/record_golden.py` installs each consumer at a pinned commit, in a throwaway environment without Tabrion.
2. It runs the consumer's code through the shared scenarios in `tests/golden/consumers/scenarios.py`.
3. It writes what the code did to `tests/golden/<consumer>.json`.
4. The replay tests named below run the same scenarios with the Tabrion call, in every pull request. A difference is
   red unless contract section 8 or a deviation in `docs/program/STATUS.md` lists it.

Contract I-61 describes the mechanism.

**Downstream (nightly).** `scripts/downstream.py` runs each consumer's own database test subset against Tabrion HEAD
(TAB-022b; the nightly job is TODO TB-070). Once a consumer has adopted a function, its own test of that function
gets a row here too.

`tests/unit/test_api_consumers.py` keeps this page honest. It requires every category A and B row of contract
section 8 to appear here, and every test named here to exist.

## Coverage by public name

| Tabrion API | Consumer and contract row | Golden record | Replay test |
|---|---|---|---|
| `tabrion.postgres.session_configurator`, `SessionSettings` | Axentra `Database._configure` (`database.py:273-275`); Nodestro `PostgresDatabase._configure` (`storage_postgres.py:275-286`) | `axentra.json` `configure.*`; `nodestro.json` `configure.*` | `tests/postgres/test_golden_consumers.py::test_axentra_configure`, `tests/postgres/test_golden_consumers.py::test_nodestro_configure` |
| `tabrion.postgres.configure` | Orkexa `backends/postgres/connection.py` `PsycopgSource` (no timeouts, no ping) | `orkexa.json` `source` | `tests/postgres/test_golden_consumers.py::test_orkexa_source_with_tabrion_configure` |
| `tabrion.postgres.ping` | Axentra `Database.ping` (`database.py:348-357`); Orkexa `PsycopgSource` | `axentra.json` `ping`; `orkexa.json` `source` | `tests/postgres/test_golden_consumers.py::test_axentra_ping`, `tests/postgres/test_golden_consumers.py::test_orkexa_ping_is_new` |
| `tabrion.postgres.role_info`, `check_role` | Axentra `Database.role_info`/`check_role` (`database.py:359-405`) with `owner_scope="rls"`; Nodestro `role_info`, `inspect_role`, `grant_runtime_role` (`:679-689`, `:808-904`) with `owner_scope="schema"` | `axentra.json` `role_info.check_role`; `nodestro.json` `role_info` | `tests/postgres/test_golden_consumers.py::test_axentra_role_info_and_check_role`, `tests/postgres/test_golden_consumers.py::test_nodestro_role_info` |
| `tabrion.postgres.inspect_role`, `RoleReport` | Axentra `roles.inspect_role`, `RoleReport` (`roles.py:30-104`); Nodestro `inspect_role` | `axentra.json` `inspect_role`; `nodestro.json` `inspect_role` | `tests/postgres/test_golden_consumers.py::test_axentra_inspect_role`, `tests/postgres/test_golden_consumers.py::test_nodestro_inspect_role` |
| `tabrion.postgres.grant_runtime_role` | Axentra `roles.grant_runtime_role` (`roles.py:107-190`); Nodestro `grant_runtime_role` | `role_grants.json` (verbatim oracles in `tests/golden/role_grants.py`) | `tests/postgres/test_grants.py::test_r11_axentra_golden`, `tests/postgres/test_grants.py::test_r12_nodestro_golden`, `tests/postgres/test_grants.py::test_r12_golden_file_matches_the_oracles` |
| `tabrion.postgres.wait_advisory_lock`, `advisory_unlock` | Axentra `migrations._wait_for_lock` (`migrations.py:309-318`) | `axentra.json` `wait_for_lock` | `tests/postgres/test_golden_consumers.py::test_axentra_wait_for_lock` |
| `tabrion.postgres.SessionLock`, `try_advisory_lock` | Axentra Worker lock (own connection, `events/worker.py`); Nodestro `try_/holds_/release_session_lock` (`:418-481`) | `axentra.json` `leader_lock`; `nodestro.json` `session_lock` | `tests/postgres/test_golden_consumers.py::test_axentra_leader_lock`, `tests/postgres/test_golden_consumers.py::test_nodestro_session_locks` |
| `tabrion.postgres.extension_status` | Nodestro `pgvector_status` (`:907-968`) | `nodestro.json` `pgvector_status` (only `unavailable` so far, TB-071) | `tests/postgres/test_golden_consumers.py::test_nodestro_pgvector_status` |
| `tabrion.errors.classify`, `safe_message` | Axentra: 16 typed and 4 generic `except psycopg...` | `axentra.json` `safe_message` (deviation D-74) | `tests/postgres/test_golden_consumers.py::test_axentra_error_texts` |
| `tabrion.sql.split_statements` | Axentra `migrations.split_statements` (`migrations.py:125-171`) | `axentra_splitter.json` (verbatim oracle `tests/golden/axentra_splitter.py`) | `tests/unit/test_sql.py::test_q02_golden_file_matches_the_oracle`, `tests/unit/test_sql.py::test_q02_byte_identical_for_every_axentra_sql_file` |
| `tabrion.sqlite.connect`, `SQLiteProfile`, `check_path` | Nodestro `SQLiteStore.__init__` (`storage.py:271-286`); Orkexa `_storage.py:80-166` | `nodestro.json` `sqlite.open`, `sqlite.symlink`; `orkexa.json` `sqlite.open`, `sqlite.symlink` | `tests/unit/test_golden_consumers.py::test_nodestro_open`, `tests/unit/test_golden_consumers.py::test_nodestro_symlinks_are_refused_now`, `tests/unit/test_golden_consumers.py::test_orkexa_open`, `tests/unit/test_golden_consumers.py::test_orkexa_symlinks` |
| `tabrion.sqlite.write_transaction` | Nodestro `SQLiteStore._transaction` (`storage.py:313-332`); Orkexa SQLite transactions x3 (`files.py:85-94`, `memory.py:84-92`, `index.py:473-500`) | `nodestro.json` `sqlite.transaction`; `orkexa.json` `sqlite.transaction.*` | `tests/unit/test_golden_consumers.py::test_nodestro_transaction`, `tests/unit/test_golden_consumers.py::test_orkexa_write_transactions` |
| `tabrion.sqlite.read_transaction` | Orkexa `index.py:473-500` | `orkexa.json` `sqlite.read.index` | `tests/unit/test_golden_consumers.py::test_orkexa_read_transaction` |
| `tabrion.sqlite.migrate` | Nodestro `SQLiteStore.migrate` (`storage.py:563-637`) | `nodestro.json` `sqlite.migrate` | `tests/unit/test_golden_consumers.py::test_nodestro_migrate` |
| `tabrion.sqlite.backup` | Nodestro `SQLiteStore.snapshot` (`storage.py:649-658`) | `nodestro.json` `sqlite.snapshot` | `tests/unit/test_golden_consumers.py::test_nodestro_snapshot` |
| `tabrion.sqlite.diagnose` | nodestro-api `diagnostics.py:110-133` | `nodestro_api.json` `diagnostics.database` (deviation D-75) | `tests/unit/test_golden_consumers.py::test_nodestro_api_database_check` |
| `tabrion.config.url_from_env` | Nodestro `server_settings.py:115-141` (`*_FILE`) | `nodestro.json` `url_from_env` (deviation D-76) | `tests/unit/test_golden_consumers.py::test_nodestro_url_from_env` |
| `tabrion.testing.configure` | Nodestro `tests/postgres_support.py` and 13 local fixtures; Orkexa `tests/backends/postgres/support.py` | `nodestro.json` and `orkexa.json` `testing.environment` | `tests/unit/test_golden_consumers.py::test_testing_environment_names` |
| `tabrion.testing` fixtures `tabrion_postgres_database`, `tabrion_postgres_roles`, `truncate_tables` | Axentra `tests/conftest.py` (throwaway database, roles without superuser, truncate) | `axentra_conftest.json` (verbatim copy `tests/golden/axentra_conftest.py`) | `tests/postgres/test_testing_database.py::test_x17_axentra_conftest_golden` |

## Not covered by a consumer record yet

| Tabrion API | Consumer use | Why, and what covers it |
|---|---|---|
| `tabrion.errors.classify`, `safe_message` | Orkexa `PsycopgSource` and `_storage.py` (contract 8.3) | `orkexa.json` has no error-text scenario. The same functions are covered by the replay of Axentra's call sites (`test_axentra_error_texts`) and by Tabrion's own canary tests. MIG-ORK-2 adds Orkexa's own test |
| `tabrion.sqlite.check_path` outside `connect` | Orkexa `check_database_path` | It is recorded only through `connect`'s refusals (`test_orkexa_symlinks`) |

Category C rows of contract section 8 stay in the consumers without Tabrion, so they have no row here.
