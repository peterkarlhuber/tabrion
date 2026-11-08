"""TAB-020 against a real PostgreSQL: the plugin's schema fixture, server version, ``require_extension``, leak check.

TAB-T-X04 ``tabrion_postgres_schema`` (created, ``search_path`` in ``owner_url``, dropped afterwards, also after a
failing test and with a connection still holding locks in it), X05 names (unique, ``schema_prefix``), X06
``postgres_server_version``, X07 ``require_extension`` (active, installable, other schema, refused, unavailable,
required, in the caller's transaction), X10 the suite's leak check. Inner pytest runs (``pytester``, a subprocess)
use a prefix below this run's ``PREFIX``, so the leak check after each test covers them too.
"""

# The SQL is built from names this file generates and fixed text, never from outside input.

from __future__ import annotations

from collections.abc import Callable
import re
from typing import Any

import pytest

from tabrion.testing import PostgresSchema, require_extension
from tabrion.testing.postgres import _search_path_url

from .conftest import PREFIX, Role, leaked, new_name

psycopg = pytest.importorskip("psycopg")
from psycopg import pq, sql  # noqa: E402

pytestmark = pytest.mark.postgres


def _schema_exists(admin: Any, name: str) -> bool:
    return admin.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", (name,)).fetchone() is not None


# --- TAB-T-X04, X05: tabrion_postgres_schema --------------------------------------------------------------------------


def test_x04_schema_and_search_path(admin: Any, admin_url: str, tabrion_postgres_schema: PostgresSchema) -> None:
    schema = tabrion_postgres_schema
    assert re.fullmatch(re.escape(PREFIX) + "[0-9a-f]{12}", schema.name)
    assert schema.runtime_url is None
    assert _schema_exists(admin, schema.name)
    with psycopg.connect(schema.owner_url, autocommit=True) as conn:
        assert conn.execute("SELECT current_schema()").fetchone() == (schema.name,)
        conn.execute("CREATE TABLE items (id int)")  # unqualified: lands in the test schema
    assert admin.execute(sql.SQL("SELECT count(*) FROM {}.items").format(sql.Identifier(schema.name))).fetchone() == (
        0,
    )
    password = psycopg.conninfo.conninfo_to_dict(admin_url).get("password")
    if password:
        assert str(password) not in repr(schema)  # pytest shows fixture values with repr


def test_x04_owner_url_keeps_existing_options() -> None:
    url = _search_path_url("dbname=x options='-c work_mem=64kB'", "t_s")
    assert psycopg.conninfo.conninfo_to_dict(url)["options"] == "-c work_mem=64kB -c search_path=t_s"


INNER = """
import pytest
import psycopg

seen = []


def test_a(tabrion_postgres_schema):
    seen.append(tabrion_postgres_schema.name)
    conn = psycopg.connect(tabrion_postgres_schema.owner_url)  # stays open, in a transaction holding a lock
    conn.execute("CREATE TABLE held (id int)")
    conn.execute("INSERT INTO held VALUES (1)")
    pytest.fail("a failing test still gets its schema dropped")


def test_b(tabrion_postgres_schema):
    seen.append(tabrion_postgres_schema.name)
    assert seen[0] != seen[1]
    print("NAMES", " ".join(seen))
"""


def test_x04_x05_dropped_after_failure_and_locks(pytester: pytest.Pytester, admin: Any) -> None:
    inner = f"{PREFIX}in_"
    pytester.makeconftest(
        'pytest_plugins = ["tabrion.testing"]\n'
        f"from tabrion.testing import configure\nconfigure(schema_prefix={inner!r})\n"
    )
    pytester.makepyfile(INNER)
    result = pytester.runpytest_subprocess("-p", "no:cacheprovider", "-p", "no:randomly", "-s", timeout=120)
    result.assert_outcomes(passed=1, failed=1)
    line = next(line for line in result.outlines if "NAMES " in line)
    names = line[line.index("NAMES ") :].split()[1:3]
    assert len(names) == 2 and all(re.fullmatch(re.escape(inner) + "[0-9a-f]{12}", n) for n in names)
    assert not leaked(admin, inner)


# --- TAB-T-X06: postgres_server_version -----------------------------------------------------------------------------


def test_x06_server_version(admin: Any, postgres_server_version: int) -> None:
    assert postgres_server_version >= 140000
    assert postgres_server_version == int(admin.execute("SHOW server_version_num").fetchone()[0])


# --- TAB-T-X07: require_extension -----------------------------------------------------------------------------------


@pytest.fixture
def fresh_db(scratch_database: str, connect: Callable[..., Any]) -> Any:
    """A connection (superuser, autocommit) to a throwaway database without extensions besides plpgsql."""
    return connect(scratch_database, autocommit=True)


def test_x07_active_is_returned(fresh_db: Any) -> None:
    status = require_extension(fresh_db, "plpgsql")
    assert status.state == "active" and status.command is None


@pytest.mark.parametrize("autocommit", [True, False])
def test_x07_installable_is_created(scratch_database: str, connect: Callable[..., Any], autocommit: bool) -> None:
    conn = connect(scratch_database, autocommit=autocommit)
    available = conn.execute("SELECT 1 FROM pg_available_extensions WHERE name = 'citext'").fetchone()
    if available is None:
        pytest.skip("the server has no citext (contrib)")
    if not autocommit:
        conn.rollback()
    status = require_extension(conn, "citext")
    assert status.state == "active" and status.extension_schema == "public"
    assert conn.info.transaction_status == pq.TransactionStatus.IDLE
    with psycopg.connect(scratch_database, autocommit=True) as other:  # committed
        assert other.execute("SELECT 1 FROM pg_extension WHERE extname = 'citext'").fetchone() == (1,)


def test_x07_in_the_callers_transaction(fresh_db: Any, scratch_database: str, connect: Callable[..., Any]) -> None:
    """Inside a running transaction the CREATE is a savepoint of it: the caller's rollback removes it too."""
    conn = connect(scratch_database)
    if conn.execute("SELECT 1 FROM pg_available_extensions WHERE name = 'citext'").fetchone() is None:
        pytest.skip("the server has no citext (contrib)")
    assert require_extension(conn, "citext").state == "active"
    assert conn.info.transaction_status == pq.TransactionStatus.INTRANS
    conn.rollback()
    assert fresh_db.execute("SELECT 1 FROM pg_extension WHERE extname = 'citext'").fetchone() is None


def test_x07_other_schema_is_moved(fresh_db: Any) -> None:
    if fresh_db.execute("SELECT 1 FROM pg_available_extensions WHERE name = 'citext'").fetchone() is None:
        pytest.skip("the server has no citext (contrib)")
    fresh_db.execute("CREATE SCHEMA elsewhere")
    fresh_db.execute("CREATE EXTENSION citext SCHEMA elsewhere")
    status = require_extension(fresh_db, "citext")
    assert (status.state, status.extension_schema) == ("active", "public")


SKIP_CASES = """
import psycopg
import pytest

from tabrion.testing import require_extension


def test_unavailable(tabrion_postgres_url):
    with psycopg.connect(tabrion_postgres_url, autocommit=True) as conn:
        require_extension(conn, "t_no_such_extension")


def test_refused(tabrion_postgres_url):
    # A role without superuser in a database where it may not create anything: CREATE EXTENSION fails.
    with psycopg.connect(tabrion_postgres_url, autocommit=True) as conn:
        conn.execute("SET ROLE {role}")
        status = require_extension(conn, "dblink")
"""


@pytest.mark.parametrize("required", ["", "t_no_such_extension, dblink"])
def test_x07_skip_or_fail(
    pytester: pytest.Pytester,
    monkeypatch: pytest.MonkeyPatch,
    make_role: Callable[..., Role],
    required: str,
) -> None:
    role = make_role()
    monkeypatch.setenv("TABRION_REQUIRE_EXTENSIONS", required)
    pytester.makeconftest('pytest_plugins = ["tabrion.testing"]\n')
    pytester.makepyfile(SKIP_CASES.replace("{role}", role.name))
    result = pytester.runpytest_subprocess("-p", "no:cacheprovider", "-rs", timeout=120)
    if required:
        result.assert_outcomes(failed=2)
        result.stdout.fnmatch_lines(["*it is required by TABRION_REQUIRE_EXTENSIONS*"])
    else:
        result.assert_outcomes(skipped=2)
        result.stdout.fnmatch_lines(["*'t_no_such_extension' is unavailable and could not be activated*"])


def test_x07_invalid_name(fresh_db: Any) -> None:
    with pytest.raises(ValueError, match="name"):
        require_extension(fresh_db, "Bad-Name")


# --- TAB-T-X10: the leak check --------------------------------------------------------------------------------------


def test_x10_leak_check_sees_this_runs_objects_only(admin: Any) -> None:
    mine = new_name()
    admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(mine)))
    try:
        assert leaked(admin) == [f"schema {mine}"]
        assert f"schema {mine}" not in leaked(admin, "t_zz_other_run_")
    finally:
        admin.execute(sql.SQL("DROP SCHEMA {}").format(sql.Identifier(mine)))
    assert leaked(admin) == []
