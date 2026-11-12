# TAB-T traceability

This page maps every numbered plan test ID `TAB-T-0nn`..`TAB-T-3nn` to the repository tests that cover it. Gate G-T2
asks for "TAB-T-001..300 present" (planning document 07 section 5). The table is also the evidence for K2 (one
success and one failure case per engine) and for K8 (security regression TAB-T-101..191).

## Scope and sources

- **IDs.** The table has one row for each of the 53 numbered IDs that planning documents 02, 04 and 07 cite. All of
  them come from document 07: the threat model (TM-01..TM-12), data integrity (DI-01..DI-11), the security
  requirements (SR-01..SR-10), the Studio requirements SQ-05 and SQ-06, the probe table and the test categories
  K3..K14. `TAB-T-212` appears only as "TAB-T-211/212".
- **Lettered series.** The lettered IDs, such as `TAB-T-S01`, `B07`, `E10`, `L01`, `K25`, `X11`, `D01`, `M08`,
  `V05` and `Q01`, are not listed here. They are the repository's own test IDs from the API contract, and the tests
  carry them in their names. Their evidence tables are in [STATUS](STATUS.md).
- **Tests column.** A reference is either `path::test_name` or `path::Class::test_name`. A parametrized test is named
  without its parameters.
- **Status.** Each row has one of these values:
  - **Covered:** the named tests check what the plan asks.
  - **Partial:** a part of the plan text has no test yet. The note says which part, and the TODO entry that tracks it.
  - **Gap:** no repository test yet. A TODO entry tracks it.
  - **Covered (0.2):** the tests cover staged 0.2 APIs. These are the Studio bases, which ship with TAB-042.

`tests/unit/test_traceability.py` keeps this page honest. It runs in the normal unit jobs and checks four things:

- Every one of the 53 IDs has exactly one row, in order.
- Every reference resolves to an existing file and test function.
- Every Covered and Partial row names at least one test.
- Every Partial and Gap row names a TODO entry that exists in `docs/TODO.md`.

## Table

| Plan ID | Plan requirement (document 07) | Tests | Status |
|---|---|---|---|
| TAB-T-001 | DI-01, probe S1: a child row with an unknown parent is a constraint error | `tests/sqlite/test_connect.py::test_s10_foreign_keys_are_enforced`, `tests/sqlite/test_connect.py::test_s10_pragmas_after_connect`, `tests/unit/test_profile_cases.py::test_d08_fails_without_foreign_keys` | Covered |
| TAB-T-002 | DI-02, probe S6 (`q.py`): 5 threads read, then write; the result is 5 | `tests/sqlite/test_transactions.py::test_s16_lost_update_five_threads`, `tests/sqlite/test_stress_sqlite.py::test_t002_t003_read_then_write_threads` | Covered |
| TAB-T-003 | DI-02, `q2.py`: no "database is locked" with `busy_timeout=5000` | `tests/sqlite/test_transactions.py::test_s16_lost_update_five_threads`, `tests/sqlite/test_stress_sqlite.py::test_t002_t003_read_then_write_threads`, `tests/sqlite/test_stress_sqlite.py::test_t003_deferred_writes_fail_without_tabrion`, `tests/sqlite/test_stress_sqlite.py::test_busy_timeout_waits_and_gives_up_in_bounded_time` | Covered |
| TAB-T-004 | DI-02: TAB-T-002/003 on both layers | `tests/unit/test_database.py::test_b07_no_lost_update_with_five_threads`, `tests/sqlite/test_stress_sqlite.py::test_t004_layer_b_threads_no_lost_update`, `tests/postgres/test_database.py::test_b07_no_lost_update_with_for_update`, `tests/postgres/test_stress_postgres.py::test_b07_layer_b_no_lost_update` | Covered |
| TAB-T-005 | DI-03: 2 processes x 500 write transactions, 0 losses, 0 lock errors | `tests/sqlite/test_stress_sqlite.py::test_t005_two_processes_500_writes_each` | Covered |
| TAB-T-006 | DI-03: `SessionLock`: a second holder waits or fails; a lost connection frees the lock | `tests/postgres/test_stress_postgres.py::test_t006_session_locks_exclude_each_other`, `tests/postgres/test_stress_postgres.py::test_t006_second_holder_fails_or_waits`, `tests/postgres/test_stress_postgres.py::test_t006_connection_loss_releases_the_lock`, `tests/postgres/test_locks.py::test_l01_two_processes_compete`, `tests/postgres/test_locks.py::test_l04_connection_loss_clears_every_count`, `tests/postgres/test_locks.py::test_l11_two_processes_one_waits` | Covered |
| TAB-T-007 | DI-04: a provoked serialization conflict is `Kind.SERIALIZATION`, `retryable=True` | `tests/postgres/test_errors_postgres.py::test_e07_lock_deadlock_serialization`, `tests/postgres/test_stress_postgres.py::test_t007_serializable_transfers_with_application_retry`, `tests/postgres/test_canary_postgres.py::test_t110_layer_a_serialization` | Covered |
| TAB-T-008 | DI-04: a deadlock is `Kind.DEADLOCK` | `tests/postgres/test_errors_postgres.py::test_e07_lock_deadlock_serialization`, `tests/postgres/test_canary_postgres.py::test_t110_layer_a_deadlock` | Covered |
| TAB-T-009 | DI-05, K7, probe P3: after a database restart, the first request succeeds (`pool_pre_ping`) | `tests/postgres/test_restart_postgres.py::test_t009_first_request_after_a_restart` (restarts the server, so it runs only in the nightly job `pg restart`, which sets `TABRION_PG_RESTART_COMMAND`; skipped elsewhere); related: `tests/postgres/test_health.py::test_p07_terminated_backend`, `tests/postgres/test_check_layer_b.py::test_k25_pool_without_pre_ping` | Covered |
| TAB-T-010 | DI-06: step 3 of 4 fails; the file's SHA-256 is unchanged; a backup exists | `tests/sqlite/test_migrate.py::test_s28_failing_step_leaves_the_file_unchanged` | Covered |
| TAB-T-011 | DI-06: a database newer than the code stops without a write | `tests/sqlite/test_migrate.py::test_s23_schema_too_new`, `tests/unit/test_migrations.py::test_m04_unknown_newer_revision` | Covered |
| TAB-T-012 | DI-06: the Alembic gate `require_current` | `tests/unit/test_migrations.py::test_m08_require_current`, `tests/examples/test_parts.py::test_require_current_under_a_read_only_profile` | Covered |
| TAB-T-020 | DI-07, probes S2 and S3: a portability table pins the return type per engine | `tests/unit/test_profile_cases.py::test_portability_cases_pass_on_sqlite`, `tests/postgres/test_portability_postgres.py::test_portability_cases_pass_on_postgres`, `tests/unit/test_profile_cases.py::test_sqlite_pitfall_numeric_goes_through_float`, `tests/unit/test_profile_cases.py::test_sqlite_pitfall_datetime_with_timezone_comes_back_naive` | Covered |
| TAB-T-021 | DI-08: timestamps round-trip (naive and aware, a DST boundary, microseconds) | `tests/examples/parts_suite.py::PartsSuite::test_values_round_trip_exactly`, `tests/examples/parts_suite.py::PartsSuite::test_types_refuse_floats_and_naive_timestamps`, `tests/examples/parts_suite.py::PartsSuite::test_timestamps_order_and_round_trip_before_year_1000`, `tests/examples/test_parts.py::test_postgresql_results_are_utc_under_another_session_timezone`, `tests/examples/test_measurements.py::test_timestamps_are_stored_in_utc_and_read_back_in_order`. No test crosses a DST boundary | Partial (TB-139) |
| TAB-T-022 | DI-09: decimal amounts round-trip exactly on both engines in the recommended pattern | `tests/examples/parts_suite.py::PartsSuite::test_values_round_trip_exactly`, `tests/examples/parts_suite.py::PartsSuite::test_decimals_order_and_compare_alike_on_both_engines`, `tests/examples/parts_suite.py::PartsSuite::test_decimals_are_stored_scaled_on_sqlite_and_numeric_on_postgresql`, `tests/examples/test_parts.py::test_exact_decimal_precision_limit` | Covered |
| TAB-T-023 | Probe S2: the reference app's STRICT or CHECK pattern enforces the length and type | `tests/examples/parts_suite.py::PartsSuite::test_constraints_hold_on_both_engines`, `tests/examples/parts_suite.py::PartsSuite::test_every_check_has_a_case`, `tests/examples/test_measurements.py::test_a_constraint_violation_becomes_a_fixed_error` | Covered |
| TAB-T-024 | Probe S4: `connect` sets `journal_mode=wal` for files | `tests/sqlite/test_connect.py::test_s10_pragmas_after_connect`, `tests/sqlite/test_findings.py::test_k02_journal_mode_not_wal` | Covered |
| TAB-T-025 | Probe P5 (`:x::int`): a doc example with `CAST`; a test shows the error message with its hint | The guides show `CAST(:x AS int)` (`docs/guides/database.md`, `docs/guides/portability.md`). No test asserts the message or a hint | Gap (TB-139) |
| TAB-T-101 | TM-01, SR-01: no Bandit or ruff `S608` finding in `src/` and `examples/` | ruff with rule set `S` (`pyproject.toml`), run by the CI jobs `lint` (`ruff check .`) and `security` (`ruff check --select S src examples`); `CONTRIBUTING.md` records ruff `S` as the Bandit substitute. Not a pytest test | Covered |
| TAB-T-102 | TM-01: an identifier with `"` or `;` is quoted correctly or refused | `tests/postgres/test_grants.py::test_r18_quoted_names_and_a_hostile_search_path`, `tests/postgres/test_session.py::test_p01_search_path_is_quoted`, `tests/postgres/test_health.py::test_p14_command_quotes_the_schema`, `tests/sqlite/test_checks.py::test_s38_table_name_is_quoted` | Covered |
| TAB-T-110 | TM-02, SR-05: the canary value never appears in `str`, `repr`, a traceback or a log record, for every `Kind` | `tests/security/test_canary_sqlite.py::test_t110_layer_a_statement_errors`, `tests/security/test_canary_sqlite.py::test_t110_layer_b_connection_unknown_resource`, `tests/postgres/test_canary_postgres.py::test_t110_layer_a_serialization`, `tests/postgres/test_canary_postgres.py::test_t110_layer_b_serialization_and_deadlock`, `tests/unit/test_errors.py::test_e10_canary_never_in_safe_message` | Covered |
| TAB-T-111 | TM-02, probe P6: `safe_url` masks the password | `tests/security/test_canary_config.py::test_t111_safe_url_masks_every_form`, `tests/security/test_canary_config.py::test_t111_safe_url_unparseable_never_echoes`, `tests/security/test_canary_sqlite.py::test_t111_frame_locals_hold_no_values` | Covered |
| TAB-T-112 | SR-04: secrets come from the environment, `*_FILE` or a reference; an error names no value | `tests/security/test_canary_config.py::test_t112_url_from_env_errors_name_no_value` | Covered |
| TAB-T-113 | SR-06: the logs hold no parameter values | every test of `tests/security/test_canary_sqlite.py` and `tests/postgres/test_canary_postgres.py` (autouse log capture at DEBUG), for example `tests/security/test_canary_sqlite.py::test_t110_layer_a_statement_errors`, `tests/unit/test_errors.py::test_e21_unique_violation_detail_is_gone_from_text_and_json` | Covered |
| TAB-T-120 | TM-03, probe S5: mode 0600, including a simulated umask of 000 | `tests/sqlite/test_connect.py::test_s01_create_private_file_is_0600_whatever_the_umask`, `tests/sqlite/test_connect.py::test_s01_new_database_and_side_files_are_0600` | Covered |
| TAB-T-121 | TM-03: a link to the main file or to `-wal` is refused; the target is unchanged | `tests/sqlite/test_connect.py::test_s03_s06_symlinks_are_refused` | Covered |
| TAB-T-122 | TM-03: a backup file has mode 0600 | `tests/sqlite/test_backup.py::test_s31_backup_is_0600`, `tests/sqlite/test_backup.py::test_s31_temporary_file_is_0600_while_copying` | Covered |
| TAB-T-123 | TM-04: race test, a link set between the check and the open, 1,000 repetitions, 0 hits | `tests/sqlite/test_connect.py::test_tb045_another_file_opened_by_sqlite_is_refused`, `tests/sqlite/test_connect.py::test_l3_link_planted_while_the_profile_is_applied`. Each window is tested deterministically; the randomized 1,000x test is open | Partial (TB-045) |
| TAB-T-130 | TM-05, probe P1: every timeout is active | `tests/postgres/test_session.py::test_p01_defaults`, `tests/postgres/test_session.py::test_p01_values_through_show`, `tests/postgres/test_session.py::test_p01_transaction_timeout_by_server_version`, `tests/postgres/test_check_findings.py::test_k13_k15_timeouts` | Covered |
| TAB-T-131 | TM-05: a statement over its timeout is `Kind.TIMEOUT`, and the connection stays usable | `tests/postgres/test_errors_postgres.py::test_e06_statement_timeout_and_cancel`, `tests/postgres/test_database.py::test_b14_timeout_on_postgres` | Covered |
| TAB-T-132 | TM-05: `SessionLock(timeout=...)` returns or fails instead of hanging | `tests/postgres/test_locks.py::test_l05_polling_timeout`, `tests/postgres/test_locks.py::test_l14_timeout` | Covered |
| TAB-T-133 | Probe P2: `application_name` is set | `tests/postgres/test_database.py::test_b02_session_settings_and_application_name`, `tests/postgres/test_check_findings.py::test_k16_application_name`, `tests/postgres/test_health.py::test_p10_application_name_and_no_schema` | Covered |
| TAB-T-140 | TM-06: profile `production` with `sslmode=prefer` to a remote host fails at start | `tests/unit/test_database.py::test_b03_tls_rules`, `tests/unit/test_database.py::test_b03_tls_violations`, `tests/postgres/test_check_findings.py::test_k19_k20_tls_of_a_remote_connection` | Covered |
| TAB-T-141 | TM-06: a socket or `localhost` is allowed | `tests/unit/test_database.py::test_b03_tls_without_a_host_follows_pghost`, `tests/unit/test_database.py::test_b03_tls_a_service_is_not_local`, `tests/postgres/test_health.py::test_p12_ssl_and_local_host` | Covered |
| TAB-T-150 | TM-07, probe P7: a superuser is refused (the plan: at start in profile `production`) | `tests/postgres/test_roles.py::test_r02_superuser` (`check_role` raises `RoleUnsafe`), `tests/postgres/test_check_findings.py::test_k10_k11_superuser_and_bypassrls`. The refusal is `check_role` at the application's start (contract 4.6); there is no profile named `production` | Covered |
| TAB-T-151 | TM-07: `BYPASSRLS` is refused | `tests/postgres/test_roles.py::test_r03_bypass_rls`, `tests/postgres/test_roles.py::test_r20_login_role_that_can_become_bypassrls_after_set_role` | Covered |
| TAB-T-152 | TM-07, SR-03: the catalog after `grant_runtime_role` matches the golden file | `tests/postgres/test_grants.py::test_r11_axentra_golden`, `tests/postgres/test_grants.py::test_r12_nodestro_golden`, `tests/postgres/test_grants.py::test_r12_golden_file_matches_the_oracles` | Covered |
| TAB-T-160 | TM-08: after a transaction with `set_config(..., true)`, the setting is empty on the same connection | `tests/postgres/test_session.py::test_p16_pool_shared_by_two_consumers`, `tests/postgres/test_contract_rules.py::test_g01_g02_writers`, `tests/postgres/test_v02_postgres.py::test_b37_on_begin_sets_transaction_local_state` | Covered |
| TAB-T-161 | TM-08: a connection with an open transaction is never reused | `tests/postgres/test_database.py::test_b21_connections_return_after_errors`, `tests/unit/test_database.py::test_b22_failed_reset_invalidates_the_connection`, `tests/unit/test_database.py::test_b09_failed_rollback_invalidates_the_connection`, `tests/postgres/test_session.py::test_p02_session_configurator_commits_and_leaves_idle` | Covered |
| TAB-T-162 | SR-10: an RLS table, two tenants through `transaction()` with `set_config(..., true)`, no access across tenants | none | Gap (TB-137) |
| TAB-T-170 | TM-09: a lost reply is `Kind.CONNECTION`; the statement runs exactly once on the server | `tests/postgres/test_faults.py::test_t170_reply_lost_the_statement_ran_once`, `tests/postgres/test_faults.py::test_t170_reply_lost_during_commit`, `tests/postgres/test_faults.py::test_t170_request_lost_the_statement_never_ran` | Covered |
| TAB-T-180 | TM-10: the core wheel has no `Requires-Dist` outside an extra | `tests/packaging/test_artifacts.py::test_core_wheel_requires_nothing` | Covered |
| TAB-T-181 | TM-10: every workflow `uses:` is pinned to a 40-character SHA | `tests/security/test_workflow_pins.py::test_actions_are_pinned_by_commit_sha`, `tests/security/test_workflow_pins.py::test_the_checks_catch_a_tag` | Covered |
| TAB-T-182 | K14: `import tabrion.sqlite` loads neither `psycopg` nor `sqlalchemy` | `tests/unit/test_import_isolation.py::test_core_imports_load_no_optional_dependency`, `tests/unit/test_import_isolation.py::test_core_imports_with_blocked_extras` | Covered |
| TAB-T-190 | TM-11: a table test over every listed SQLSTATE | `tests/unit/test_errors.py::test_e01_e09_postgres_mapping`, `tests/unit/test_errors.py::test_e04_e08_sqlite_codes`, `tests/postgres/test_errors_postgres.py::test_e07_lock_deadlock_serialization` | Covered |
| TAB-T-191 | TM-11: an unknown code is `Kind.UNKNOWN`, `retryable=False` | `tests/unit/test_errors.py::test_e01_e09_postgres_mapping` (rows `XX000`, `0A000`), `tests/unit/test_errors.py::test_e13_classify_never_raises` | Covered |
| TAB-T-200 | TM-12: a backup under parallel writers passes `integrity_check` | `tests/sqlite/test_backup.py::test_s34_backup_while_another_connection_writes`, `tests/sqlite/test_stress_sqlite.py::test_backup_during_writes_is_a_consistent_snapshot` | Covered |
| TAB-T-201 | TM-12: an abort in the middle of a backup leaves no half target | `tests/sqlite/test_backup.py::test_s32_abort_leaves_no_half_target` | Covered |
| TAB-T-202 | DI-11: restore drill of reference app B: backup, delete the file, restore, same count | none | Gap (TB-138) |
| TAB-T-210 | SQ-05 (Studio basis): read-only enforced by the database, not by a text filter | `tests/sqlite/test_connect.py::test_s08_read_only_never_writes`, `tests/unit/test_database.py::test_b11_read_is_enforced_by_sqlite`, `tests/postgres/test_database.py::test_b12_read_is_enforced_by_postgres`, `tests/postgres/test_database.py::test_b12_read_only_profile` | Covered |
| TAB-T-211 | SQ-06 (Studio basis): time and row limits per query | `tests/unit/test_v02_database.py::test_b27_max_rows_is_enforced`, `tests/postgres/test_v02_postgres.py::test_b27_max_rows`, `tests/postgres/test_v02_postgres.py::test_b30_query_timeout` | Covered (0.2) |
| TAB-T-212 | SQ-06 (Studio basis): stop through `cancel()` or `interrupt()` | `tests/unit/test_v02_database.py::test_b15_cancel_from_a_second_thread`, `tests/postgres/test_v02_postgres.py::test_b15_cancel_from_a_second_thread`, `tests/postgres/test_v02_postgres.py::test_b35_stream_timeout_and_cancel` | Covered (0.2) |
| TAB-T-300 | Probe `s.py`: nightly benchmark, a warning above 2x the baseline, not a gate | none | Gap (TB-140) |

## Summary

- **Covered:** 47 IDs (two of them, TAB-T-211 and TAB-T-212, on staged 0.2 APIs; TAB-T-009 in the nightly job
  `pg restart` only).
- **Partial:** 2 IDs.
  - TAB-T-021: no DST boundary (TB-139).
  - TAB-T-123: no randomized 1,000x race (TB-045).
- **Gap:** 4 IDs.
  - TAB-T-025: the error hint for `:x::int` (TB-139).
  - TAB-T-162: RLS with two tenants (TB-137).
  - TAB-T-202: restore drill (TB-138).
  - TAB-T-300: benchmark, not a gate (TB-140).

The gaps belong to these categories:

- Portability: TAB-T-021, TAB-T-025.
- RLS compatibility (SR-10): TAB-T-162.
- Backup and restore: TAB-T-202.
- A non-gating benchmark: TAB-T-300.

No gap is in K8's TAB-T-101..191 apart from TAB-T-162. The owner decides with TAB-040 whether each gap blocks
G-T2 or becomes a deviation.
