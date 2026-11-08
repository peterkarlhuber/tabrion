"""TAB-020 against a real PostgreSQL: the plugin's schema fixture, server version, ``require_extension``, leak check.

TAB-T-X04 ``tabrion_postgres_schema`` (created, ``search_path`` in ``owner_url``, dropped afterwards, also after a
failing test and with a connection still holding locks in it), X05 names (unique, ``schema_prefix``), X06
``postgres_server_version``, X07 ``require_extension`` (active, installable, other schema, refused, unavailable,
required, in the caller's transaction; never a move, review H-1), X10 the suite's leak check, and the teardown's
scope (review H-1: never into an extension; L-1, L-2: whose sessions it ends). Inner pytest runs (``pytester``, a
subprocess) use a prefix below this run's ``PREFIX``, so the leak check after each test covers them too.
"""

# The SQL is built from names this file generates and fixed text, never from outside input.

from __future__ import annotations

from collections.abc import Callable
import re
from typing import Any

import pytest

from tabrion.testing import PostgresSchema, require_extension
from tabrion.testing.postgres import _drop_schema, _search_path_url

from .conftest import PREFIX, Role, leaked, new_name, with_params

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


def _activate(conn: Any, name: str) -> Any:
    """``require_extension`` where a skip is a failure: a regression must not hide as a skipped test (review H-1)."""
    try:
        return require_extension(conn, name)
    except pytest.skip.Exception as exc:
        pytest.fail(f"require_extension skipped: {exc}")


def _need_citext(conn: Any) -> None:
    if conn.execute("SELECT 1 FROM pg_available_extensions WHERE name = 'citext'").fetchone() is None:
        pytest.skip("the server has no citext (contrib)")


def _extension_schema(conn: Any, name: str) -> str | None:
    row = conn.execute(
        "SELECT n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace WHERE e.extname = %s",
        (name,),
    ).fetchone()
    return None if row is None else str(row[0])


def test_x07_active_is_returned(fresh_db: Any) -> None:
    status = _activate(fresh_db, "plpgsql")
    assert status.state == "active" and status.command is None


@pytest.mark.parametrize("autocommit", [True, False])
def test_x07_installable_is_created(scratch_database: str, connect: Callable[..., Any], autocommit: bool) -> None:
    conn = connect(scratch_database, autocommit=autocommit)
    _need_citext(conn)
    if not autocommit:
        conn.rollback()
    status = _activate(conn, "citext")
    assert status.state == "active" and status.extension_schema == "public"
    assert conn.info.transaction_status == pq.TransactionStatus.IDLE
    with psycopg.connect(scratch_database, autocommit=True) as other:  # committed
        assert _extension_schema(other, "citext") == "public"


def test_x07_in_the_callers_transaction(fresh_db: Any, scratch_database: str, connect: Callable[..., Any]) -> None:
    """Inside a running transaction the CREATE is a savepoint of it: the caller's rollback removes it too."""
    conn = connect(scratch_database)
    _need_citext(conn)
    assert _activate(conn, "citext").state == "active"
    assert conn.info.transaction_status == pq.TransactionStatus.INTRANS
    conn.rollback()
    assert _extension_schema(fresh_db, "citext") is None


def test_x07_other_schema_is_made_visible_not_moved(fresh_db: Any, scratch_database: str) -> None:
    """Review H-1: the extension stays where it is; its schema is appended to the connection's search_path."""
    _need_citext(fresh_db)
    fresh_db.execute("CREATE SCHEMA elsewhere")
    fresh_db.execute("CREATE EXTENSION citext SCHEMA elsewhere")
    with psycopg.connect(_search_path_url(scratch_database, "public"), autocommit=True) as conn:
        status = _activate(conn, "citext")
        assert (status.state, status.extension_schema) == ("active", "elsewhere")
        assert conn.execute("SHOW search_path").fetchone() == ("public, elsewhere",)
        assert conn.execute("SELECT 'A'::citext = 'a'").fetchone() == (True,)
        conn.execute("CREATE TABLE still_first (id int)")  # new objects still land in the first schema
        assert conn.execute("SELECT 'still_first'::regclass::text").fetchone() == ("still_first",)
    assert _extension_schema(fresh_db, "citext") == "elsewhere"


def test_x07_empty_search_path(fresh_db: Any, scratch_database: str) -> None:
    _need_citext(fresh_db)
    fresh_db.execute("CREATE EXTENSION citext SCHEMA public")
    with psycopg.connect(scratch_database, autocommit=True) as conn:
        conn.execute("SET search_path = ''")
        assert _activate(conn, "citext").extension_schema == "public"
        assert conn.execute("SHOW search_path").fetchone() == ("public",)
        assert conn.execute("SELECT current_schemas(false)").fetchone() == (["public"],)


def test_x07_two_runs_racing_for_an_installable_extension(
    fresh_db: Any, scratch_database: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other run created it between the status and the CREATE: this run finds it installed and uses it."""

    import tabrion.postgres

    _need_citext(fresh_db)
    real = tabrion.postgres.extension_status
    calls: list[str] = []

    def stale(conn: Any, name: str, **kwargs: Any) -> Any:
        status = real(conn, name, **kwargs)
        calls.append(status.state)
        if len(calls) == 1:
            fresh_db.execute("CREATE SCHEMA other_run")
            fresh_db.execute("CREATE EXTENSION citext SCHEMA other_run")  # the other run wins the race
        return status

    monkeypatch.setattr(tabrion.postgres, "extension_status", stale)
    with psycopg.connect(scratch_database, autocommit=True) as conn:
        status = _activate(conn, "citext")
    assert calls[0] == "installable" and status.extension_schema == "other_run"
    assert _extension_schema(fresh_db, "citext") == "other_run"  # not moved, not created twice


@pytest.mark.parametrize("installed", [True, False], ids=["in_public", "installable"])
def test_h1_teardown_never_reaches_a_shared_extension(fresh_db: Any, scratch_database: str, installed: bool) -> None:
    """Review H-1 regression: an extension used by a column in another schema survives require_extension on a
    test schema's URL and that schema's teardown, and so does the column."""
    _need_citext(fresh_db)
    if installed:
        fresh_db.execute("CREATE EXTENSION citext SCHEMA public")
    test_schema = new_name()
    fresh_db.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(test_schema)))
    with psycopg.connect(_search_path_url(scratch_database, test_schema), autocommit=True) as conn:
        assert _activate(conn, "citext").extension_schema == "public"
    fresh_db.execute("CREATE TABLE public.users (id int, email public.citext)")
    fresh_db.execute("INSERT INTO public.users VALUES (1, 'A@x')")
    _drop_schema(scratch_database, test_schema)
    assert not _schema_exists(fresh_db, test_schema)
    assert _extension_schema(fresh_db, "citext") == "public"
    assert fresh_db.execute("SELECT email::text FROM public.users WHERE email = 'a@X'").fetchone() == ("A@x",)


def test_h1_teardown_refuses_a_schema_holding_an_extension(fresh_db: Any, scratch_database: str) -> None:
    """A test that put an extension into its schema itself: the schema is left in place and the teardown fails."""
    _need_citext(fresh_db)
    test_schema = new_name()
    fresh_db.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(test_schema)))
    fresh_db.execute(sql.SQL("CREATE EXTENSION citext SCHEMA {}").format(sql.Identifier(test_schema)))
    fresh_db.execute(sql.SQL("CREATE TABLE public.users (email {}.citext)").format(sql.Identifier(test_schema)))
    with pytest.raises(pytest.fail.Exception, match=r"holds the extension\(s\) citext.*left in place"):
        _drop_schema(scratch_database, test_schema)
    assert _schema_exists(fresh_db, test_schema) and _extension_schema(fresh_db, "citext") == test_schema
    columns = fresh_db.execute("SELECT count(*) FROM information_schema.columns WHERE table_name = 'users'")
    assert columns.fetchone() == (1,)
    # (the scratch database, with all of it, is dropped by its fixture)


# --- Review L-1, L-2: whose sessions the teardown ends ----------------------------------------------------------------


def _locking_session(url: str, table: str) -> Any:
    """A session in a transaction that holds ACCESS SHARE on ``table``."""
    conn = psycopg.connect(url, connect_timeout=10)
    conn.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.SQL(table)))
    return conn


def _alive(conn: Any) -> bool:
    try:
        conn.execute("SELECT 1")
    except psycopg.OperationalError:
        return False
    return True


def test_l1_another_roles_session_is_waited_for_not_ended(
    admin: Any, admin_url: str, make_role: Callable[..., Role]
) -> None:
    """A backup-like session of another role holding a lock in the schema survives; the DROP waits for it."""
    import threading

    reader = make_role()
    test_schema = new_name()
    admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(test_schema)))
    admin.execute(sql.SQL("CREATE TABLE {}.t (id int)").format(sql.Identifier(test_schema)))
    admin.execute(
        sql.SQL("GRANT USAGE ON SCHEMA {0} TO {1}").format(sql.Identifier(test_schema), sql.Identifier(reader.name))
    )
    admin.execute(
        sql.SQL("GRANT SELECT ON {0}.t TO {1}").format(sql.Identifier(test_schema), sql.Identifier(reader.name))
    )
    backup = psycopg.connect(reader.url, connect_timeout=10, application_name="pg_dump")
    try:
        backup.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        backup.execute(sql.SQL("SELECT count(*) FROM {}.t").format(sql.Identifier(test_schema)))
        errors: list[BaseException] = []

        def drop() -> None:
            try:
                _drop_schema(admin_url, test_schema)
            except BaseException as exc:
                errors.append(exc)

        worker = threading.Thread(target=drop)
        worker.start()
        worker.join(2)
        assert worker.is_alive(), "the DROP did not wait for the other role's lock"
        assert _alive(backup), "the teardown ended another role's session"
        backup.rollback()
        worker.join(30)
        assert not worker.is_alive() and not errors, errors
        assert not _schema_exists(admin, test_schema)
    finally:
        backup.close()
        admin.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(test_schema)))


def test_l2_a_lock_in_another_schema_is_left_alone(admin: Any, admin_url: str) -> None:
    """Same role, same database, a lock in a schema other than the dropped one: the session survives."""
    test_schema, other_schema = new_name(), new_name()
    for name in (test_schema, other_schema):
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name)))
        admin.execute(sql.SQL("CREATE TABLE {}.t (id int)").format(sql.Identifier(name)))
    other = _locking_session(admin_url, sql.Identifier(other_schema, "t").as_string(admin))
    try:
        _drop_schema(admin_url, test_schema)
        assert _alive(other)
        assert not _schema_exists(admin, test_schema)
    finally:
        other.close()
        admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(other_schema)))


def test_l2_a_lock_in_another_database_is_left_alone(admin: Any, admin_url: str) -> None:
    """Two databases cloned from one template share their object ids: a lock on the twin of the dropped schema's
    table, in the other database, does not end that session."""
    template, first, second = new_name(), new_name(), new_name()
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(template)))
    try:
        test_schema = new_name()
        with psycopg.connect(with_params(admin_url, dbname=template), autocommit=True) as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(test_schema)))
            conn.execute(sql.SQL("CREATE TABLE {}.t (id int)").format(sql.Identifier(test_schema)))
        for name in (first, second):
            admin.execute(
                sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(sql.Identifier(name), sql.Identifier(template))
            )
        table = sql.Identifier(test_schema, "t").as_string(admin)
        twin = _locking_session(with_params(admin_url, dbname=second), table)
        mine = _locking_session(with_params(admin_url, dbname=first), table)  # this one is ended
        try:
            oids = {int(c.execute("SELECT %s::regclass::oid", (table,)).fetchone()[0]) for c in (twin, mine)}
            assert len(oids) == 1  # the same oid in both databases: only the database filter tells them apart
            _drop_schema(with_params(admin_url, dbname=first), test_schema)
            assert _alive(twin) and not _alive(mine)
        finally:
            twin.close()
            mine.close()
    finally:
        for name in (first, second, template):
            admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))


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
