"""Mutation probes for TAB-015, TAB-016, TAB-030 and TAB-034 (planning document 07 section 7), against PostgreSQL.

Like ``tests/unit/test_mutation_probes.py``: a copy of the package with one rule broken must turn the named test
red (exit code 1: a failure, not a skip, since ``TABRION_TEST_POSTGRES`` reaches the subprocess). Each anchor must
exist exactly once, so a refactoring cannot silently disarm a probe.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit.test_mutation_probes import _copy, _run

from .conftest import admin_conninfo

pytestmark = [pytest.mark.postgres, pytest.mark.slow]

READERS = "tests/postgres/test_contract_rules.py::test_g01_g02_readers"

PROBES = [
    # T-NOTX: the implicit transaction a reader opened is rolled back (IDLE stays IDLE).
    (
        "postgres/_common.py",
        "            connection.rollback()\n",
        "            pass\n",
        f"{READERS}[superuser-role_info-idle-tuple]",
    ),
    # T-NOTX: a caller's running transaction is never rolled back.
    (
        "postgres/_common.py",
        "    own = not connection.autocommit and connection.info.transaction_status == pq.TransactionStatus.IDLE\n",
        "    own = not connection.autocommit\n",
        f"{READERS}[superuser-diagnose-intrans-tuple]",
    ),
    # Own tuple_row cursor, whatever the connection's row and cursor factories.
    (
        "postgres/_common.py",
        "        with guarded(connection), psycopg.Cursor(connection, row_factory=tuple_row) as cursor:\n",
        "        with guarded(connection), connection.cursor() as cursor:\n",
        f"{READERS}[superuser-inspect_role-idle-autocommit-dict_row]",
    ),
    # configure never commits.
    (
        "postgres/__init__.py",
        "    _apply(connection, _validated(settings))\n",
        "    _apply(connection, _validated(settings))\n    connection.commit()\n",
        "tests/postgres/test_session.py::test_p02_configure_never_commits",
    ),
    # session_configurator commits the transaction it opened (psycopg_pool needs IDLE).
    (
        "postgres/__init__.py",
        "            connection.commit()\n",
        "            pass\n",
        "tests/postgres/test_session.py::test_p02_session_configurator_commits_and_leaves_idle",
    ),
    # R-SET: a field outside the allowlist is refused.
    (
        "postgres/__init__.py",
        "        if guc is None:\n",
        "        if guc is None and False:\n",
        "tests/postgres/test_session.py::test_g03_allowlist_r_set",
    ),
    # search_path is validated before it is sent.
    (
        "postgres/__init__.py",
        "            if type(value) is not str or not _SCHEMA_NAME.fullmatch(value):\n",
        "            if type(value) is not str:\n",
        "tests/postgres/test_session.py::test_p04_validation_against_the_server",
    ),
    # ping limits its SELECT on the server.
    (
        "postgres/__init__.py",
        '            cur.execute(sql.SQL("SET LOCAL statement_timeout = {}")'
        ".format(sql.Literal(_timeout_ms(seconds))))\n",
        "            pass\n",
        "tests/postgres/test_health.py::test_p08_statement_timeout",
    ),
    # Catalog functions are schema-qualified.
    (
        "postgres/__init__.py",
        "_PING_SQL = \"SELECT pg_catalog.current_setting('server_version_num')::int,"
        ' pg_catalog.pg_is_in_recovery()"\n',
        "_PING_SQL = \"SELECT pg_catalog.current_setting('server_version_num')::int, pg_is_in_recovery()\"\n",
        "tests/postgres/test_contract_rules.py::test_catalog_functions_cannot_be_hijacked",
    ),
    # diagnose reads pg_stat_ssl only with the privilege.
    (
        "postgres/__init__.py",
        "        if ssl_readable:\n",
        "        if True:\n",
        "tests/postgres/test_health.py::test_p11_without_read_privileges",
    ),
    # extension_status: installed but not visible is other_schema, not active.
    (
        "postgres/__init__.py",
        "    if installed is not None and visible and (probe_type is None or probe_ok):\n",
        "    if installed is not None and (probe_type is None or probe_ok):\n",
        "tests/postgres/test_health.py::test_p14_other_schema",
    ),
    # The rls scope counts only tables with row-level security.
    (
        "postgres/roles.py",
        '    + "c.relrowsecurity AND n.nspname OPERATOR(pg_catalog.=) ANY (pg_catalog.current_schemas(false))"\n',
        '    + "n.nspname OPERATOR(pg_catalog.=) ANY (pg_catalog.current_schemas(false))"\n',
        "tests/postgres/test_roles.py::test_r04_table_owner_without_rls",
    ),
    # allow_superuser implies allow_table_owner (Axentra).
    (
        "postgres/roles.py",
        "    if info.table_owner and not (allow_table_owner or allow_superuser):\n",
        "    if info.table_owner and not allow_table_owner:\n",
        "tests/postgres/test_roles.py::test_r07_flags_and_reasons",
    ),
    # A bypass_rls role is refused without allow_superuser.
    (
        "postgres/roles.py",
        "        if info.bypass_rls:\n",
        "        if False:\n",
        "tests/postgres/test_roles.py::test_r03_bypass_rls",
    ),
    # inspect_role reports the tables the role owns.
    (
        "postgres/roles.py",
        "        owned_tables=tuple(str(name) for name, owned in tables if owned),\n",
        "        owned_tables=(),\n",
        "tests/postgres/test_roles.py::test_r09_inspect_role_report",
    ),
    # Review M-1: a reader in the caller's running transaction runs under a savepoint.
    (
        "postgres/_common.py",
        "    if connection.info.transaction_status == pq.TransactionStatus.INTRANS:\n",
        "    if False:\n",
        "tests/postgres/test_contract_rules.py::test_g01_failure_keeps_the_callers_transaction",
    ),
    # Review M-1: inspect_role passes the schema's oid, never its name, to has_schema_privilege.
    (
        "postgres/roles.py",
        "            (role_oid, role_oid, schema_oid),\n",
        "            (role_oid, role_oid, schema_oid if schema_oid is not None else schema_name),\n",
        "tests/postgres/test_contract_rules.py::test_m1_defined_results",
    ),
    # Review M-2: operators are qualified.
    (
        "postgres/roles.py",
        '    "   ON n.oid OPERATOR(pg_catalog.=) c.relnamespace"\n',
        '    "   ON n.oid = c.relnamespace"\n',
        "tests/postgres/test_contract_rules.py::test_catalog_functions_cannot_be_hijacked",
    ),
    # Review M-2: types are qualified.
    (
        "postgres/__init__.py",
        '            " pg_catalog.current_schema()::pg_catalog.text,"\n            " pg_catalog.to_regtype(',
        '            " pg_catalog.current_schema()::text,"\n            " pg_catalog.to_regtype(',
        "tests/postgres/test_contract_rules.py::test_catalog_functions_cannot_be_hijacked",
    ),
    # Review H-1 / I-20: SESSION_USER counts (a superuser session after SET ROLE).
    (
        "postgres/roles.py",
        "_ROLE_INFO_SQL = {\n",
        '_ROLE_INFO_HEAD = _ROLE_INFO_HEAD.replace("SESSION_USER", "CURRENT_USER")\n_ROLE_INFO_SQL = {\n',
        "tests/postgres/test_roles.py::test_r12_superuser_session_after_set_role",
    ),
    # Review H-1 / I-20: superuser through membership, not the attribute of current_user alone.
    (
        "postgres/roles.py",
        "_ROLE_INFO_SQL = {\n",
        '_ROLE_INFO_HEAD = _ROLE_INFO_HEAD.replace("WHERE r.rolsuper", "WHERE r.rolsuper'
        ' AND r.rolname OPERATOR(pg_catalog.=) CURRENT_USER")\n_ROLE_INFO_SQL = {\n',
        "tests/postgres/test_roles.py::test_r11_member_of_a_privileged_role",
    ),
    # Review H-1: inspect_role judges membership of BYPASSRLS roles too.
    (
        "postgres/roles.py",
        '            " EXISTS (SELECT 1 FROM pg_catalog.pg_roles s WHERE s.rolbypassrls"\n',
        '            " EXISTS (SELECT 1 FROM pg_catalog.pg_roles s WHERE s.rolbypassrls'
        ' AND s.oid OPERATOR(pg_catalog.=) r.oid"\n',
        "tests/postgres/test_roles.py::test_r11_member_of_a_privileged_role",
    ),
    # Review L-2: MEMBER, not USAGE (a NOINHERIT member may SET ROLE to the owner).
    (
        "postgres/roles.py",
        "_ROLE_INFO_SQL = {\n",
        "_ROLE_INFO_TAIL = _ROLE_INFO_TAIL.replace(\"'MEMBER'\", \"'USAGE'\")\n_ROLE_INFO_SQL = {\n",
        "tests/postgres/test_roles.py::test_r06_noinherit_member_of_the_owner",
    ),
    # Review I-1: ping takes no savepoint in an aborted transaction.
    (
        "postgres/__init__.py",
        "        if connection.info.transaction_status == pq.TransactionStatus.INERROR:\n",
        "        if False:\n",
        "tests/postgres/test_contract_rules.py::test_i1_ping_in_failed_transaction_logs_nothing",
    ),
    # Review DL-1: in pipeline mode the caller's pipeline is synced before the savepoint decision.
    (
        "postgres/_common.py",
        "    if connection.pgconn.pipeline_status != pq.PipelineStatus.OFF:\n",
        "    if False:\n",
        "tests/postgres/test_contract_rules.py::test_dl1_pipeline_mode_keeps_the_callers_transaction",
    ),
    # TAB-030: wait_advisory_lock polls pg_try_advisory_lock; a blocking pg_advisory_lock holds a snapshot that
    # CREATE INDEX CONCURRENTLY in the holder's session waits for.
    (
        "postgres/locks.py",
        '        cursor.execute("SELECT pg_catalog.pg_try_advisory_lock(%s)", (key,))\n        return bool(',
        '        cursor.execute("SELECT pg_catalog.pg_advisory_lock(%s), true", (key,))\n        return bool(',
        "tests/postgres/test_locks.py::test_l13_create_index_concurrently_while_one_holds_and_one_waits",
    ),
    # TAB-030: a lost connection clears every count.
    (
        "postgres/locks.py",
        "            self._lost.update(self._held)\n        self._held.clear()\n",
        "            self._lost.update(self._held)\n",
        "tests/postgres/test_locks.py::test_l04_connection_loss_clears_every_count",
    ),
    # TAB-030: the unlock comes at the last hold, not the first release.
    (
        "postgres/locks.py",
        "            if count > 1:\n",
        "            if False:\n",
        "tests/postgres/test_locks.py::test_l02_counting",
    ),
    # TAB-030: waiting inside a running transaction is refused (it would hold a snapshot).
    (
        "postgres/locks.py",
        '            raise UsageError("postgres.lock_in_transaction")\n',
        "            pass\n",
        "tests/postgres/test_locks.py::test_l15_inside_a_transaction",
    ),
    # TAB-030: the lock connection is never pooled or reused: autocommit, a connection of its own.
    (
        "postgres/locks.py",
        '        if "autocommit" in kwargs and kwargs.pop("autocommit") is not True:\n',
        "        if False:\n",
        "tests/unit/test_locks_and_faults_unit.py::test_session_lock_arguments",
    ),
    # TAB-034: a lost reply is dropped from the moment the statement leaves for the server.
    (
        "testing/_faults.py",
        "                        link.dropping.set()  # before the statement can reach the server\n",
        "                        pass\n",
        "tests/postgres/test_faults.py::test_t170_reply_lost_the_statement_ran_once",
    ),
    # TAB-034: a request fault never forwards the statement.
    (
        "testing/_faults.py",
        '                    if fault == "request":\n',
        '                    if fault == "request-disabled":\n',
        "tests/postgres/test_faults.py::test_t170_request_lost_the_statement_never_ran",
    ),
    # TAB-034: the delay mode holds the answer back.
    (
        "testing/_faults.py",
        "                        time.sleep(max(0.0, until - time.monotonic()))\n",
        "                        pass\n",
        "tests/postgres/test_faults.py::test_t170_delay_keeps_the_connection",
    ),
    # Review M-1 (#6): ping syncs the caller's pipeline outside its try, so the caller's error propagates.
    (
        "postgres/__init__.py",
        "    settle(connection)\n    try:\n        if connection.info.transaction_status",
        "    try:\n        settle(connection)\n        if connection.info.transaction_status",
        "tests/postgres/test_contract_rules.py::test_m1_6_pipeline_error_of_the_caller_propagates",
    ),
    # Review L-1 (#6): in pipeline mode each attempt is synced before the sleep.
    (
        "postgres/locks.py",
        "        # statement and no snapshot stay open while waiting (review L-1).\n        settle(connection)\n",
        "        # statement and no snapshot stay open while waiting (review L-1).\n",
        "tests/postgres/test_locks.py::test_l12_pipeline_autocommit_holds_no_snapshot_while_waiting",
    ),
    # Review L-4 (#6), mutant 14: the sync before the status check.
    (
        "postgres/locks.py",
        "        settle(connection)  # pipeline mode: ACTIVE until a sync, then the real status (review DL-1)\n",
        "",
        "tests/postgres/test_locks.py::test_l15_pipeline_with_queued_statements",
    ),
    # Review L-3 (#6): a key lost with its connection is not taken again silently.
    (
        "postgres/locks.py",
        '        if key in self._lost:\n            raise UsageError("postgres.lock_lost")\n',
        "",
        "tests/postgres/test_locks.py::test_l04_reentrant_acquire_after_a_loss_is_refused",
    ),
    # Review L-2 (#6): a forked child gets a fresh internal lock.
    (
        "postgres/locks.py",
        "        lock._guard = threading.Lock()\n",
        "        pass\n",
        "tests/postgres/test_locks.py::test_l10_fork_while_the_internal_lock_is_held",
    ),
    # Review L-4 (#6), mutant 10: held_keys probes the connection.
    (
        "postgres/locks.py",
        "            if self._held:\n                self._alive()\n",
        "",
        "tests/postgres/test_locks.py::test_l04_held_keys_probes_the_connection",
    ),
    # Review L-4 (#6), mutant 21: the default connect_timeout.
    (
        "postgres/locks.py",
        '        kwargs.setdefault("connect_timeout", _CONNECT_TIMEOUT)\n',
        "",
        "tests/unit/test_locks_and_faults_unit.py::test_session_lock_connects_with_the_documented_arguments",
    ),
    # Review L-4 (#6), mutant 22: acquire polls under the internal lock.
    (
        "postgres/locks.py",
        "            with self._guard:\n                self._check_open()\n                if self._try(key):\n"
        "                    return True\n            if not _sleep_until_next",
        "            if True:\n                self._check_open()\n                if self._try(key):\n"
        "                    return True\n            if not _sleep_until_next",
        "tests/postgres/test_locks.py::test_l09_threads_acquire_on_a_fresh_lock",
    ),
    # Review Info I-1 (#6): the key's exact value is checked.
    (
        "postgres/locks.py",
        "    value = int.__index__(key)\n",
        "    value = int(key)\n",
        "tests/unit/test_locks_and_faults_unit.py::test_keys_are_checked_by_their_exact_value",
    ),
    # Review Info I-2 (#6): an unpaired surrogate gives the documented ValueError.
    (
        "postgres/locks.py",
        "    except UnicodeEncodeError:\n",
        "    except LookupError:\n",
        "tests/unit/test_locks_and_faults_unit.py::test_lock_key_unpaired_surrogate",
    ),
]


@pytest.fixture(scope="module", autouse=True)
def _needs_postgres() -> None:
    admin_conninfo()


def test_unmutated_copy_passes(tmp_path: Path) -> None:
    """Positive control: the probed tests pass on an unchanged copy."""
    result = _run(_copy(tmp_path), sorted({p[3] for p in PROBES}))
    assert result.returncode == 0, result.stdout[-3000:]


@pytest.mark.parametrize(
    ("module", "anchor", "replacement", "test"),
    PROBES,
    ids=[p[3].split("::")[1].split("[")[0] + f"-{i}" for i, p in enumerate(PROBES)],
)
def test_mutation_turns_the_test_red(tmp_path: Path, module: str, anchor: str, replacement: str, test: str) -> None:
    copy = _copy(tmp_path)
    target = copy / "tabrion" / module
    text = target.read_text(encoding="utf-8")
    assert text.count(anchor) == 1, f"probe anchor not found exactly once in {module}: {anchor!r}"
    target.write_text(text.replace(anchor, replacement), encoding="utf-8")
    result = _run(copy, [test])
    assert result.returncode == 1, f"mutation in {module} went unnoticed by {test}:\n{result.stdout[-3000:]}"
