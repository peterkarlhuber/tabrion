"""TAB-032 against a real PostgreSQL: database per session, roles without superuser, runtime role, ``truncate_tables``.

TAB-T-X11 ``tabrion_postgres_database`` with the default ``RoleSetup()`` (owner and app without superuser, the
database and ``public`` owned by the owner, ``CREATE`` on ``public`` revoked from ``PUBLIC``, dropped with its roles
at the end), X12 the other role setups (``split=False``, ``None``, named roles, a named role that exists already),
X13 ``tabrion_postgres_roles`` (in the existing database, ``DROP OWNED`` and ``DROP ROLE`` at the end, also with a
session still open), X14 ``truncate_tables`` in one statement, X15 its selection, targets and errors, X16 the
runtime-role mode of ``tabrion_postgres_schema``, X17 Axentra's ``tests/conftest.py`` on the plugin against its
verbatim copy, X18 both fixtures under an admin without superuser. The session fixtures run in inner pytest runs
(``pytester``, a subprocess) with a prefix below this run's ``PREFIX``: their teardown is part of what is checked,
and the leak check after each test covers them.
"""

# The SQL is built from names this file generates and fixed text, never from outside input.

from __future__ import annotations

from collections.abc import Callable, Iterator
import json
import os
from pathlib import Path
import re
import secrets
from typing import Any

import pytest

from tabrion.testing import PostgresSchema, truncate_tables

from .conftest import PREFIX, Role, leaked, new_name, with_params

psycopg = pytest.importorskip("psycopg")
from psycopg import pq, sql  # noqa: E402

pytestmark = pytest.mark.postgres

INNER_PREFIX = f"{PREFIX}in_"

# Shared by the inner runs: facts about a role, and what the role may do.
HELPERS = """
import psycopg
from psycopg.conninfo import conninfo_to_dict

ATTRIBUTES = ("rolsuper", "rolbypassrls", "rolcreatedb", "rolcreaterole", "rolreplication", "rolcanlogin")


def user(url):
    return conninfo_to_dict(url)["user"]


def attributes(conn, role):
    row = conn.execute(
        "SELECT " + ", ".join(ATTRIBUTES) + " FROM pg_roles WHERE rolname = %s", (role,)
    ).fetchone()
    return dict(zip(ATTRIBUTES, row))


def public_privileges(conn):
    # What PUBLIC holds on the schema public.
    rows = conn.execute(
        "SELECT a.privilege_type FROM pg_namespace n, aclexplode(n.nspacl) a"
        " WHERE n.nspname = 'public' AND a.grantee = 0"
    ).fetchall()
    return sorted(row[0] for row in rows)


PLAIN_LOGIN = dict.fromkeys(ATTRIBUTES, False) | {"rolcanlogin": True}
"""


def _run(pytester: pytest.Pytester, conftest: str, tests: str, *args: str) -> pytest.RunResult:
    pytester.makeconftest(
        'pytest_plugins = ["tabrion.testing"]\n'
        f"from tabrion.testing import RoleSetup, configure\nconfigure(schema_prefix={INNER_PREFIX!r})\n" + conftest
    )
    pytester.makepyfile(helpers=HELPERS)
    pytester.makepyfile(tests)
    return pytester.runpytest_subprocess("-p", "no:cacheprovider", "-p", "no:randomly", "-s", *args, timeout=180)


def _printed(result: pytest.RunResult, tag: str) -> list[str]:
    line = next(line for line in result.outlines if f"{tag} " in line)
    return line[line.index(f"{tag} ") :].split()[1:]


# --- TAB-T-X11: tabrion_postgres_database with RoleSetup() ----------------------------------------------------------

DATABASE_DEFAULT = """
import psycopg
import pytest

from helpers import PLAIN_LOGIN, attributes, public_privileges, user

seen = []


def test_split(tabrion_postgres_database):
    db = tabrion_postgres_database
    owner, app = user(db.owner_url), user(db.app_url)
    print("NAMES", db.name, owner, app)
    seen.append(db.name)
    with psycopg.connect(db.url, autocommit=True) as admin:
        assert admin.execute("SELECT current_database()").fetchone() == (db.name,)
        assert admin.execute(
            "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = current_database()"
        ).fetchone() == (owner,)
        assert admin.execute(
            "SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname = 'public'"
        ).fetchone() == (owner,)
        assert attributes(admin, owner) == PLAIN_LOGIN
        assert attributes(admin, app) == PLAIN_LOGIN
        # CREATE on public is revoked from PUBLIC: only the owner has it.
        assert public_privileges(admin) == ["USAGE"]
        assert admin.execute(
            "SELECT has_database_privilege(%s, current_database(), 'CONNECT')", (app,)
        ).fetchone() == (True,)
    with psycopg.connect(db.owner_url, autocommit=True) as conn:
        assert conn.execute("SELECT current_user, current_database()").fetchone() == (owner, db.name)
        conn.execute("CREATE TABLE public.items (id int)")
        conn.execute("CREATE SCHEMA app_data")
    with psycopg.connect(db.app_url, autocommit=True) as conn:
        assert conn.execute("SELECT current_user").fetchone() == (app,)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("CREATE TABLE public.mine (id int)")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT * FROM public.items")
    # A session still open at the end does not keep the database alive (WITH (FORCE)).
    open_conn = psycopg.connect(db.app_url)
    open_conn.execute("SELECT 1")
    seen.append(open_conn)


def test_same_database_in_the_session(tabrion_postgres_database):
    assert tabrion_postgres_database.name == seen[0]
"""


def test_x11_database_with_split_roles(pytester: pytest.Pytester, admin: Any) -> None:
    result = _run(pytester, "", DATABASE_DEFAULT)
    result.assert_outcomes(passed=2)
    name, owner, app = _printed(result, "NAMES")
    hex12 = "[0-9a-f]{12}"
    assert re.fullmatch(re.escape(INNER_PREFIX) + hex12, name)
    assert re.fullmatch(re.escape(INNER_PREFIX) + "own_" + hex12, owner)
    assert re.fullmatch(re.escape(INNER_PREFIX) + "app_" + hex12, app)
    assert not leaked(admin, INNER_PREFIX)  # the database and both roles are gone


def test_x11_passwords_are_not_shown(pytester: pytest.Pytester) -> None:
    """A failing test shows the handle with ``repr``: no password of the roles appears."""
    result = _run(
        pytester,
        "",
        """
from psycopg.conninfo import conninfo_to_dict


def test_fails(tabrion_postgres_database):
    print("PASSWORDS", conninfo_to_dict(tabrion_postgres_database.owner_url)["password"],
          conninfo_to_dict(tabrion_postgres_database.app_url)["password"])
    assert tabrion_postgres_database is None
""",
        "-l",
    )
    result.assert_outcomes(failed=1)
    passwords = _printed(result, "PASSWORDS")
    shown = "\n".join(line for line in result.outlines if "PASSWORDS" not in line)
    assert len(passwords) == 2 and all(len(p) >= 24 and p not in shown for p in passwords)


# --- TAB-T-X12: the other role setups -------------------------------------------------------------------------------

SPLIT_FALSE = """
import psycopg

from helpers import PLAIN_LOGIN, attributes, user


def test_one_role(tabrion_postgres_database):
    db = tabrion_postgres_database
    assert db.owner_url == db.app_url
    role = user(db.owner_url)
    print("NAMES", role)
    with psycopg.connect(db.owner_url, autocommit=True) as conn:
        assert conn.execute(
            "SELECT current_user, pg_get_userbyid(datdba) FROM pg_database WHERE datname = current_database()"
        ).fetchone() == (role, role)
        assert attributes(conn, role) == PLAIN_LOGIN
        conn.execute("CREATE TABLE public.items (id int)")
"""

NO_ROLES = """
import psycopg

from helpers import public_privileges


def test_admin_owns_it(tabrion_postgres_database, tabrion_postgres_url):
    db = tabrion_postgres_database
    assert db.owner_url == db.app_url == db.url
    with psycopg.connect(tabrion_postgres_url, autocommit=True) as admin:
        me = admin.execute("SELECT current_user").fetchone()[0]
    with psycopg.connect(db.url, autocommit=True) as conn:
        assert conn.execute(
            "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = current_database()"
        ).fetchone() == (me,)
        assert public_privileges(conn) == ["USAGE"]
"""

NAMED = """
import psycopg

from helpers import user


def test_named(tabrion_postgres_database):
    db = tabrion_postgres_database
    print("NAMES", user(db.owner_url), user(db.app_url))
    with psycopg.connect(db.owner_url, autocommit=True) as conn:
        conn.execute("CREATE TABLE public.items (id int)")
"""


@pytest.mark.parametrize(
    ("setup", "tests", "count"),
    [
        ("RoleSetup(split=False)", SPLIT_FALSE, 1),
        ("RoleSetup(split=False, app={app!r})", SPLIT_FALSE, 1),
        ("None", NO_ROLES, 0),
        ("RoleSetup(owner={owner!r}, app={app!r})", NAMED, 2),
    ],
    ids=["split_false", "split_false_named", "none", "named"],
)
def test_x12_role_setups(pytester: pytest.Pytester, admin: Any, setup: str, tests: str, count: int) -> None:
    owner, app = f"{INNER_PREFIX}axentra_owner", f"{INNER_PREFIX}axentra_app"
    conftest = (
        "import pytest\n\n\n@pytest.fixture(scope='session')\ndef tabrion_role_setup():\n"
        f"    return {setup.format(owner=owner, app=app)}\n"
    )
    result = _run(pytester, conftest, tests)
    result.assert_outcomes(passed=1)
    if count:
        names = _printed(result, "NAMES")
        assert len(names) == count
        if "owner=" in setup:
            assert names == [owner, app]
        elif "app=" in setup:
            assert names == [app]
        else:
            assert re.fullmatch(re.escape(INNER_PREFIX) + "own_[0-9a-f]{12}", names[0])
    assert not leaked(admin, INNER_PREFIX)


def test_x12_a_named_role_that_exists_is_left_alone(
    pytester: pytest.Pytester, admin: Any, make_role: Callable[..., Role]
) -> None:
    """The fixture never changes or drops a role it did not create: the run errors, the role is unchanged."""
    existing = make_role()
    conftest = (
        "import pytest\n\n\n@pytest.fixture(scope='session')\ndef tabrion_role_setup():\n"
        f"    return RoleSetup(owner={existing.name!r})\n"
    )
    result = _run(pytester, conftest, "def test_x(tabrion_postgres_database):\n    pass\n")
    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines([f"*the role '{existing.name}' already exists*"])
    with psycopg.connect(existing.url, autocommit=True) as conn:  # same password, still there
        assert conn.execute("SELECT current_user").fetchone() == (existing.name,)
    assert not leaked(admin, INNER_PREFIX)  # the app role created before the error is gone again


def test_x12_invalid_role_setup(pytester: pytest.Pytester, admin: Any) -> None:
    conftest = "import pytest\n\n\n@pytest.fixture(scope='session')\ndef tabrion_role_setup():\n    return 'roles'\n"
    result = _run(pytester, conftest, "def test_x(tabrion_postgres_database):\n    pass\n")
    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(["*TypeError: tabrion_role_setup must return a RoleSetup or None*"])
    assert not leaked(admin, INNER_PREFIX)


# --- TAB-T-X13: tabrion_postgres_roles ------------------------------------------------------------------------------

ROLES = """
import psycopg
import pytest

from helpers import PLAIN_LOGIN, attributes

held = []


def test_roles(tabrion_postgres_roles, tabrion_postgres_url):
    roles = tabrion_postgres_roles
    print("NAMES", roles.owner, roles.app)
    with psycopg.connect(tabrion_postgres_url, autocommit=True) as admin:
        database = admin.info.dbname
        assert attributes(admin, roles.owner) == PLAIN_LOGIN
        assert attributes(admin, roles.app) == PLAIN_LOGIN
    with psycopg.connect(roles.owner_url, autocommit=True) as conn:
        assert conn.execute("SELECT current_user, current_database()").fetchone() == (roles.owner, database)
        conn.execute(f"CREATE SCHEMA {roles.owner}")  # under the run's prefix: the leak check sees it
        conn.execute(f"CREATE TABLE {roles.owner}.items (id int)")
    with psycopg.connect(roles.app_url, autocommit=True) as conn:
        assert conn.execute("SELECT current_user").fetchone() == (roles.app,)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(f"CREATE SCHEMA {roles.app}")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(f"SELECT * FROM {roles.owner}.items")
    # A transaction of the owner still open at the end, holding a lock on its table.
    conn = psycopg.connect(roles.owner_url)
    conn.execute(f"INSERT INTO {roles.owner}.items VALUES (1)")
    held.append(conn)
"""


def test_x13_roles_in_the_existing_database(pytester: pytest.Pytester, admin: Any) -> None:
    result = _run(pytester, "", ROLES)
    result.assert_outcomes(passed=1)
    owner, app = _printed(result, "NAMES")
    assert re.fullmatch(re.escape(INNER_PREFIX) + "own_[0-9a-f]{12}", owner)
    assert re.fullmatch(re.escape(INNER_PREFIX) + "app_[0-9a-f]{12}", app)
    # The roles, the owner's schema (DROP OWNED) and the database grants (in the ACL of the database) are gone.
    assert not leaked(admin, INNER_PREFIX)
    acl = admin.execute("SELECT datacl::text FROM pg_database WHERE datname = current_database()").fetchone()[0]
    assert acl is None or INNER_PREFIX not in acl


# --- TAB-T-X18: an admin without superuser ---------------------------------------------------------------------------


@pytest.fixture(params=["INHERIT", "NOINHERIT"])
def plain_admin(request: pytest.FixtureRequest, admin: Any, scratch_database: str) -> Iterator[tuple[str, str]]:
    """A LOGIN role with only CREATEDB and CREATEROLE, owner of a scratch database: its URLs (that database, and
    the admin's own database, which it does not own). Also as ``NOINHERIT`` (review L-1 of the re-review: from
    PostgreSQL 16 the fixtures' memberships must inherit explicitly)."""
    if request.param == "NOINHERIT" and admin.info.server_version < 160000:
        pytest.skip("a NOINHERIT admin needs the membership options of PostgreSQL 16 (contract I-60)")
    name = new_name("adm_")
    password = secrets.token_hex(16)
    admin.execute(
        sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOBYPASSRLS CREATEDB CREATEROLE {} PASSWORD {}").format(
            sql.Identifier(name), sql.SQL(request.param), sql.Literal(password)
        )
    )
    try:
        database = psycopg.conninfo.conninfo_to_dict(scratch_database)["dbname"]
        admin.execute(sql.SQL("ALTER DATABASE {} OWNER TO {}").format(sql.Identifier(database), sql.Identifier(name)))
        owned = with_params(scratch_database, user=name, password=password)
        yield owned, with_params(owned, dbname=admin.info.dbname)
    finally:
        with psycopg.connect(with_params(scratch_database, dbname=admin.info.dbname), autocommit=True) as conn:
            conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(database)))
            conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(name)))
            conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(name)))


def test_x18_both_fixtures_without_superuser(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, admin: Any, plain_admin: tuple[str, str]
) -> None:
    monkeypatch.setenv("TABRION_TEST_POSTGRES", plain_admin[0])
    pytester.makepyfile(test_roles=ROLES)
    result = _run(pytester, "", DATABASE_DEFAULT)
    result.assert_outcomes(passed=3)
    assert not leaked(admin, INNER_PREFIX)


def test_x18_roles_fixture_needs_the_grant_option(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, admin: Any, plain_admin: tuple[str, str]
) -> None:
    """In a database the admin does not own, its GRANT is only a warning: the fixture reports it and cleans up."""
    monkeypatch.setenv("TABRION_TEST_POSTGRES", plain_admin[1])
    result = _run(pytester, "", "def test_x(tabrion_postgres_roles):\n    pass\n")
    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(["*could not grant CONNECT and CREATE on the database*"])
    assert not leaked(admin, INNER_PREFIX)


# --- TAB-T-X16: runtime-role mode -----------------------------------------------------------------------------------

RUNTIME = """
import psycopg
import pytest

from helpers import PLAIN_LOGIN, attributes, user

held = []


def test_runtime(tabrion_postgres_schema, tabrion_postgres_url):
    schema = tabrion_postgres_schema
    runtime = user(schema.runtime_url)
    print("NAMES", schema.name, runtime)
    with psycopg.connect(tabrion_postgres_url, autocommit=True) as admin:
        assert attributes(admin, runtime) == PLAIN_LOGIN
        # A privilege outside the schema, as a test may grant one: the teardown's DROP OWNED revokes it.
        admin.execute(f'GRANT TEMPORARY ON DATABASE "{admin.info.dbname}" TO {runtime}')
    with psycopg.connect(schema.owner_url, autocommit=True) as owner:
        owner.execute("CREATE TABLE items (id int GENERATED ALWAYS AS IDENTITY PRIMARY KEY, label text)")
    with psycopg.connect(schema.runtime_url, autocommit=True) as conn:
        assert conn.execute("SELECT current_user, current_schema()").fetchone() == (runtime, schema.name)
        conn.execute("INSERT INTO items (label) VALUES ('a')")  # the default privileges cover the new table
        assert conn.execute("UPDATE items SET label = 'b' RETURNING id").fetchone() == (1,)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("CREATE TABLE mine (id int)")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("DROP TABLE items")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("TRUNCATE items")
    password = psycopg.conninfo.conninfo_to_dict(schema.runtime_url)["password"]
    assert password not in repr(schema)
    # The runtime role's transaction still holds a lock in the schema at the end: the teardown ends it.
    conn = psycopg.connect(schema.runtime_url)
    conn.execute("INSERT INTO items (label) VALUES ('c')")
    held.append(conn)


def test_runtime_role_per_test(tabrion_postgres_schema):
    print("SECOND", user(tabrion_postgres_schema.runtime_url))
"""


def test_x16_runtime_role_mode(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, admin: Any) -> None:
    monkeypatch.setenv("TABRION_TEST_RUNTIME_ROLE", "1")
    result = _run(pytester, "", RUNTIME)
    result.assert_outcomes(passed=2)
    name, runtime = _printed(result, "NAMES")
    (second,) = _printed(result, "SECOND")
    assert re.fullmatch(re.escape(INNER_PREFIX) + "rt_[0-9a-f]{12}", runtime) and second != runtime
    assert re.fullmatch(re.escape(INNER_PREFIX) + "[0-9a-f]{12}", name)
    assert not leaked(admin, INNER_PREFIX)  # schemas and runtime roles are gone
    acl = admin.execute("SELECT datacl::text FROM pg_database WHERE datname = current_database()").fetchone()[0]
    assert acl is None or INNER_PREFIX not in acl  # the CONNECT grant went with the role


def test_x16_without_the_variable_no_runtime_role(tabrion_postgres_schema: PostgresSchema) -> None:
    assert tabrion_postgres_schema.runtime_url is None


# --- TAB-T-X17: Axentra's tests/conftest.py on the plugin ------------------------------------------------------------

AXENTRA_GOLDEN = Path(__file__).resolve().parent.parent / "golden" / "axentra_conftest.json"

AXENTRA_ON_THE_PLUGIN = """
import json

import psycopg
from psycopg.conninfo import conninfo_to_dict
import pytest

from tabrion.postgres import grant_runtime_role
from tabrion.testing import truncate_tables
from tests.golden.axentra_conftest import migrate, state


@pytest.fixture(scope="session")
def pg_urls(tabrion_postgres_database, tabrion_postgres_url):
    db = tabrion_postgres_database
    urls = {
        "admin": db.url,
        "app": db.app_url,
        "owner": db.owner_url,
        "owner_role": conninfo_to_dict(db.owner_url)["user"],
        "server_admin": tabrion_postgres_url,
        "dbname": db.name,
    }
    migrate(urls["owner"])
    with psycopg.connect(urls["owner"], autocommit=True) as conn:
        app = conninfo_to_dict(db.app_url)["user"]
        assert grant_runtime_role(
            conn, app, schema="public", protected_tables=("axentra_schema_migrations",)
        ).restricted
    return urls


def truncate_all(admin_url):
    truncate_tables(admin_url, schema="public", like="axentra_%", exclude=["axentra_schema_migrations"])


def test_state(pg_urls):
    print("STATE", json.dumps(state(pg_urls, truncate_all), sort_keys=True))
"""


def test_x17_axentra_conftest_golden(pytester: pytest.Pytester, admin: Any, admin_url: str) -> None:
    """Axentra's split-role ``pg_urls`` and ``truncate_all`` (verbatim copy) and the same conftest on the plugin
    (``tabrion_postgres_database`` with ``RoleSetup()``, ``grant_runtime_role``, ``truncate_tables``) give the same
    state, and it equals ``tests/golden/axentra_conftest.json``. ``truncate_tables`` also restarts the identities,
    which the state does not compare (Axentra's ``TRUNCATE`` keeps them)."""
    from tests.golden.axentra_conftest import AXENTRA_COMMIT, migrate, pg_urls, state, truncate_all

    urls_gen = pg_urls(admin_url, f"{PREFIX}ref_app", f"{PREFIX}ref_own", migrate, f"{PREFIX}ax_")
    try:
        reference = state(next(urls_gen), truncate_all)
    finally:
        urls_gen.close()
    reference = json.loads(json.dumps(reference))  # tuples as lists, as in the file
    if os.environ.get("TABRION_RECORD_GOLDEN") == "1":
        AXENTRA_GOLDEN.write_text(
            json.dumps(
                {
                    "about": "State after Axentra's tests/conftest.py pg_urls (split roles) and truncate_all, verbatim"
                    " in tests/golden/axentra_conftest.py (TAB-032, TAB-T-X17); TABRION_RECORD_GOLDEN=1 rewrites it.",
                    "axentra_commit": AXENTRA_COMMIT,
                    "state": reference,
                },
                indent=1,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    golden = json.loads(AXENTRA_GOLDEN.read_text(encoding="utf-8"))
    assert golden["axentra_commit"] == AXENTRA_COMMIT
    assert reference == golden["state"]
    assert not leaked(admin)  # the copy cleaned up after itself

    root = str(Path(__file__).resolve().parents[2])
    result = _run(pytester, f"import sys\nsys.path.insert(0, {root!r})\n", AXENTRA_ON_THE_PLUGIN)
    result.assert_outcomes(passed=1)
    (line,) = [line for line in result.outlines if "STATE " in line]
    assert json.loads(line[line.index("STATE ") + len("STATE ") :]) == golden["state"]
    assert not leaked(admin, INNER_PREFIX)


# --- TAB-T-X14, X15: truncate_tables --------------------------------------------------------------------------------


@pytest.fixture
def filled(admin: Any, tabrion_postgres_schema: PostgresSchema) -> PostgresSchema:
    """Tables with identity columns, a foreign key, quoted names, a partitioned table and a statement log.

    Each table but the log has an ``AFTER TRUNCATE`` statement trigger that records ``current_query()``.
    """
    with psycopg.connect(tabrion_postgres_schema.owner_url, autocommit=True) as conn:
        conn.execute(
            '''
            CREATE TABLE truncate_log (tbl text, query text);
            CREATE FUNCTION log_truncate() RETURNS trigger LANGUAGE plpgsql SET search_path FROM CURRENT AS $$
            BEGIN INSERT INTO truncate_log VALUES (TG_TABLE_NAME, current_query()); RETURN NULL; END $$;
            CREATE TABLE authors (id int GENERATED ALWAYS AS IDENTITY PRIMARY KEY, name text);
            CREATE TABLE books (id int GENERATED ALWAYS AS IDENTITY, author int REFERENCES authors);
            CREATE TABLE "Odd ""Name""" (id int);
            CREATE TABLE readings (at int) PARTITION BY RANGE (at);
            CREATE TABLE readings_1 PARTITION OF readings FOR VALUES FROM (0) TO (100);
            CREATE VIEW book_view AS SELECT * FROM books;
            CREATE SEQUENCE loose_seq;
            '''
        )
        for table in ("authors", "books", 'Odd "Name"', "readings"):
            conn.execute(
                sql.SQL(
                    "CREATE TRIGGER log AFTER TRUNCATE ON {} FOR EACH STATEMENT EXECUTE FUNCTION log_truncate()"
                ).format(sql.Identifier(table))
            )
        _fill(conn)
    return tabrion_postgres_schema


def _fill(conn: Any) -> None:
    conn.execute("INSERT INTO authors (name) VALUES ('a'), ('b')")
    conn.execute("INSERT INTO books (author) VALUES (1), (2)")
    conn.execute('INSERT INTO "Odd ""Name""" VALUES (1)')
    conn.execute("INSERT INTO readings VALUES (5)")


def _counts(conn: Any) -> dict[str, int]:
    tables = ["authors", "books", 'Odd "Name"', "readings", "truncate_log"]
    return {t: conn.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(t))).fetchone()[0] for t in tables}


def test_x14_one_statement_restart_identity(filled: PostgresSchema) -> None:
    with psycopg.connect(filled.owner_url, autocommit=True) as conn:
        assert truncate_tables(conn, exclude=["truncate_log"]) == 4
        log = conn.execute("SELECT tbl, query FROM truncate_log").fetchall()  # sorted here: ORDER BY uses a collation
        assert sorted(row[0] for row in log) == ['Odd "Name"', "authors", "books", "readings"]
        queries = {row[1] for row in log}
        assert len(queries) == 1, queries  # every trigger saw the same, single statement
        (query,) = queries
        assert query.startswith("TRUNCATE TABLE ") and query.endswith(" RESTART IDENTITY CASCADE")
        assert f'"{filled.name}"."Odd ""Name"""' in query and f'"{filled.name}"."readings"' in query
        assert "readings_1" not in query and "book_view" not in query and "loose_seq" not in query
        assert _counts(conn) == {"authors": 0, "books": 0, 'Odd "Name"': 0, "readings": 0, "truncate_log": 4}
        assert conn.execute("SELECT count(*) FROM readings_1").fetchone() == (0,)
        conn.execute("INSERT INTO authors (name) VALUES ('c')")
        assert conn.execute("SELECT id FROM authors").fetchone() == (1,)  # RESTART IDENTITY


def test_x15_like_and_exclude(filled: PostgresSchema) -> None:
    with psycopg.connect(filled.owner_url, autocommit=True) as conn:
        assert truncate_tables(conn, like="b%") == 1
        assert _counts(conn) == {"authors": 2, "books": 0, 'Odd "Name"': 1, "readings": 1, "truncate_log": 1}
        assert truncate_tables(conn, like="no_such_%") == 0
        assert truncate_tables(conn, exclude=["truncate_log", "authors", "books", "readings", 'Odd "Name"']) == 0
        assert conn.execute("SELECT count(*) FROM truncate_log").fetchone() == (1,)  # no statement ran


def test_x15_cascade_reaches_an_excluded_table(filled: PostgresSchema) -> None:
    """``exclude`` does not protect a table that references a truncated one (``CASCADE``; contract I-60)."""
    with psycopg.connect(filled.owner_url, autocommit=True) as conn:
        assert truncate_tables(conn, exclude=["books", "truncate_log"]) == 3
        assert conn.execute("SELECT count(*) FROM books").fetchone() == (0,)


def test_x15_url_and_explicit_schema(admin_url: str, filled: PostgresSchema) -> None:
    assert truncate_tables(filled.owner_url, like="authors") == 1
    assert truncate_tables(admin_url, schema=filled.name, exclude=["truncate_log"]) == 4
    with psycopg.connect(filled.owner_url, autocommit=True) as conn:
        # authors, books (CASCADE), then the four tables: six statement triggers fired.
        assert set(_counts(conn).values()) == {0, 6}


def test_x15_idle_connection_commits_and_callers_transaction_rolls_back(filled: PostgresSchema) -> None:
    with psycopg.connect(filled.owner_url) as conn, psycopg.connect(filled.owner_url, autocommit=True) as other:
        assert conn.info.transaction_status == pq.TransactionStatus.IDLE
        assert truncate_tables(conn, like="authors") == 1
        assert conn.info.transaction_status == pq.TransactionStatus.IDLE  # committed
        assert other.execute("SELECT count(*) FROM authors").fetchone() == (0,)
        _fill(other)
        conn.execute("SELECT 1")  # the caller's transaction
        assert truncate_tables(conn, like="authors") == 1
        assert conn.info.transaction_status == pq.TransactionStatus.INTRANS
        conn.rollback()
        assert other.execute("SELECT count(*) FROM authors").fetchone() == (2,)


def test_x15_errors(admin: Any, filled: PostgresSchema) -> None:
    with psycopg.connect(filled.owner_url, autocommit=True) as conn:
        with pytest.raises(TypeError, match="exclude"):
            truncate_tables(conn, exclude="truncate_log")
        with pytest.raises(TypeError, match="schema"):
            truncate_tables(conn, schema="")
        with pytest.raises(TypeError, match="like"):
            truncate_tables(conn, like=1)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="does not exist"):
            truncate_tables(conn, schema=new_name())
        conn.execute("SET search_path = ''")
        with pytest.raises(ValueError, match="no current schema"):
            truncate_tables(conn)
        assert conn.info.transaction_status == pq.TransactionStatus.IDLE
        conn.execute(sql.SQL("SET search_path = {}").format(sql.Identifier(filled.name)))
        assert _counts(conn)["authors"] == 2  # nothing was truncated


def test_x15_empty_schema(tabrion_postgres_schema: PostgresSchema) -> None:
    assert truncate_tables(tabrion_postgres_schema.owner_url) == 0


# --- Re-review M-1: truncate_tables never empties a schema the run did not create by default ------------------------


def test_m1_default_schema_must_be_a_test_schema(scratch_database: str) -> None:
    """The admin URL's current schema is ``public``: without ``schema=`` nothing is emptied (a ``ValueError``);
    ``schema="public"`` is a deliberate choice and works."""
    with psycopg.connect(scratch_database, autocommit=True) as conn:
        conn.execute("CREATE TABLE public.prod (id serial PRIMARY KEY, v text)")
        conn.execute("INSERT INTO public.prod (v) VALUES ('keep')")
        with pytest.raises(ValueError, match=r"current schema 'public' is not a test schema.*pass schema="):
            truncate_tables(scratch_database)
        with pytest.raises(ValueError, match="not a test schema"):
            truncate_tables(conn)
        assert conn.info.transaction_status == pq.TransactionStatus.IDLE
        assert conn.execute("SELECT count(*) FROM public.prod").fetchone() == (1,)
        assert truncate_tables(conn, schema="public") == 1
        assert conn.execute("SELECT count(*) FROM public.prod").fetchone() == (0,)


# Each case: the statements (``{s}`` is the test schema, the search_path), the tables to exclude, the table in
# ``public`` the TRUNCATE would reach with its reason, and how to remove it again.
_CASCADE_CASES = {
    "direct": (
        "CREATE TABLE parent (id int PRIMARY KEY); INSERT INTO parent VALUES (1);"
        " CREATE TABLE public.audit (ref int REFERENCES {s}.parent); INSERT INTO public.audit VALUES (1)",
        [],
        '"public"."audit" (foreign key)',
        "DROP TABLE public.audit",
    ),
    "through_an_excluded_table": (
        "CREATE TABLE parent (id int PRIMARY KEY); INSERT INTO parent VALUES (1);"
        " CREATE TABLE middle (id int PRIMARY KEY REFERENCES parent); INSERT INTO middle VALUES (1);"
        " CREATE TABLE public.audit (ref int REFERENCES {s}.middle); INSERT INTO public.audit VALUES (1)",
        ["middle"],
        '"public"."audit" (foreign key)',
        "DROP TABLE public.audit",
    ),
    "partition_in_another_schema": (
        "CREATE TABLE parent (id int) PARTITION BY RANGE (id);"
        " CREATE TABLE public.part PARTITION OF {s}.parent FOR VALUES FROM (0) TO (10); INSERT INTO parent VALUES (1)",
        [],
        '"public"."part" (partition)',
        "DROP TABLE public.part; INSERT INTO parent SELECT 1 WHERE false",
    ),
    "inheritance_child_in_another_schema": (
        "CREATE TABLE parent (id int); INSERT INTO parent VALUES (1);"
        " CREATE TABLE public.child () INHERITS ({s}.parent); INSERT INTO public.child VALUES (2)",
        [],
        '"public"."child" (inheritance child)',
        "DROP TABLE public.child",
    ),
    "foreign_key_to_a_partition": (
        "CREATE TABLE parent (id int PRIMARY KEY) PARTITION BY RANGE (id);"
        " CREATE TABLE parent_1 PARTITION OF parent FOR VALUES FROM (0) TO (10); INSERT INTO parent VALUES (1);"
        " CREATE TABLE public.audit (ref int REFERENCES {s}.parent_1); INSERT INTO public.audit VALUES (1)",
        [],
        '"public"."audit" (foreign key)',
        "DROP TABLE public.audit",
    ),
}


@pytest.mark.parametrize("case", list(_CASCADE_CASES))
def test_m1_cascade_into_another_schema_is_refused(scratch_database: str, case: str) -> None:
    """A table in another schema that the TRUNCATE would empty (a foreign key, directly or through a table of the
    schema; a partition; an inheritance child; a foreign key to a partition): a ``ValueError`` naming it and the
    reason, before anything is emptied; once it is gone the call works."""
    setup, exclude, reached, remove = _CASCADE_CASES[case]
    test_schema = new_name()
    with psycopg.connect(scratch_database, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(test_schema)))
        conn.execute(sql.SQL("SET search_path = {}").format(sql.Identifier(test_schema)))
        conn.execute(setup.replace("{s}", sql.Identifier(test_schema).as_string(conn)))
        rows = conn.execute("SELECT count(*) FROM ONLY parent").fetchone()
        outside = reached.split(" (")[0]
        before = conn.execute(f"SELECT count(*) FROM {outside}").fetchone()  # noqa: S608 - a fixed name
        with pytest.raises(ValueError, match=re.escape(f"would also empty {reached} in another schema")):
            truncate_tables(conn, exclude=exclude)
        assert conn.info.transaction_status == pq.TransactionStatus.IDLE
        assert conn.execute("SELECT count(*) FROM ONLY parent").fetchone() == rows
        assert conn.execute(f"SELECT count(*) FROM {outside}").fetchone() == before  # noqa: S608
        conn.execute(remove)
        assert truncate_tables(conn, exclude=exclude) == 1
        assert conn.execute("SELECT count(*) FROM parent").fetchone() == (0,)


def test_m1_selected_tables_are_locked_before_the_check(scratch_database: str) -> None:
    """The selected tables are locked before the catalog check, so nothing can be attached to them in between:
    with another session reading ``parent``, a call that would be refused waits for that lock first (here: until
    the caller's ``lock_timeout``) instead of answering from a catalog that may still change."""
    test_schema = new_name()
    with psycopg.connect(scratch_database, autocommit=True) as conn, psycopg.connect(scratch_database) as reader:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(test_schema)))
        conn.execute(sql.SQL("SET search_path = {}").format(sql.Identifier(test_schema)))
        conn.execute("CREATE TABLE parent (id int PRIMARY KEY); CREATE TABLE public.audit (ref int REFERENCES parent)")
        reader.execute(sql.SQL("SELECT count(*) FROM {}.parent").format(sql.Identifier(test_schema)))  # ACCESS SHARE
        conn.execute("SET lock_timeout = '300ms'")
        with pytest.raises(psycopg.errors.LockNotAvailable):
            truncate_tables(conn)
        reader.rollback()
        with pytest.raises(ValueError, match="foreign key"):
            truncate_tables(conn)


@pytest.mark.parametrize("inside", [False, True], ids=["idle", "in_transaction"])
def test_l1_a_foreign_key_committed_after_the_check_is_refused_and_rolled_back(
    scratch_database: str, monkeypatch: pytest.MonkeyPatch, inside: bool
) -> None:
    """Review L-1 of #19: ``middle`` (excluded, so not locked) references the selected ``parent``. Right after the
    first catalog check another session commits ``public.audit REFERENCES middle`` with a row. The ``TRUNCATE``
    reaches ``audit``; the second check after it refuses, and the rollback keeps every row, ``audit``'s too."""
    from tabrion.testing import postgres as module

    test_schema = new_name()
    real = module._refuse_cascade_out_of
    calls: list[int] = []

    def check_then_race(conn: Any, namespace: int, name: str, tables: list[int]) -> None:
        calls.append(1)
        real(conn, namespace, name, tables)
        if len(calls) == 1:
            with psycopg.connect(scratch_database, autocommit=True) as other:
                statement = (
                    "CREATE TABLE public.audit (ref int REFERENCES {}.middle); INSERT INTO public.audit VALUES (1)"
                )
                other.execute(sql.SQL(statement).format(sql.Identifier(test_schema)))

    with psycopg.connect(scratch_database, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(test_schema)))
        conn.execute(sql.SQL("SET search_path = {}").format(sql.Identifier(test_schema)))
        conn.execute(
            "CREATE TABLE parent (id int PRIMARY KEY); INSERT INTO parent VALUES (1);"
            " CREATE TABLE middle (id int PRIMARY KEY REFERENCES parent); INSERT INTO middle VALUES (1)"
        )
        conn.execute("SET lock_timeout = '10s'")
        monkeypatch.setattr(module, "_refuse_cascade_out_of", check_then_race)
        with pytest.raises(ValueError, match=re.escape('would also empty "public"."audit" (foreign key)')):
            if inside:
                with conn.transaction():
                    truncate_tables(conn, exclude=["middle"])
            else:
                truncate_tables(conn, exclude=["middle"])
        assert calls == [1, 1]
        assert conn.info.transaction_status == pq.TransactionStatus.IDLE
        counts = conn.execute(
            "SELECT (SELECT count(*) FROM parent), (SELECT count(*) FROM middle), (SELECT count(*) FROM public.audit)"
        ).fetchone()
        assert counts == (1, 1, 1)


# --- Re-review L-2: the teardown steps of the roles ------------------------------------------------------------------


def test_l2_drop_all_ends_the_roles_sessions_everywhere(admin: Any, admin_url: str, scratch_database: str) -> None:
    """Sessions of a fixture role in the admin's database and in another one are ended before ``DROP ROLE`` (else
    they would live on as orphans of a dropped role)."""
    from tabrion.testing.postgres import _RoleSet

    roles = _RoleSet(admin_url)
    role = roles.create(new_name("own_"))
    sessions = [psycopg.connect(role.url(url), autocommit=True) for url in (admin_url, scratch_database)]
    try:
        roles.drop_all()
        for session in sessions:
            with pytest.raises(psycopg.OperationalError):
                session.execute("SELECT 1")
        assert admin.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role.name,)).fetchone() is None
    finally:
        for session in sessions:
            session.close()


def test_l2_drop_all_tries_every_role(admin: Any, admin_url: str, scratch_database: str) -> None:
    """One role that cannot be dropped (it still owns a database) does not keep the others; its error is raised.
    The roles are dropped in reverse order, so the blocked one is the last created."""
    from tabrion.testing.postgres import _RoleSet

    roles = _RoleSet(admin_url)
    first, second = roles.create(new_name("own_")), roles.create(new_name("app_"))
    database = psycopg.conninfo.conninfo_to_dict(scratch_database)["dbname"]
    admin.execute(
        sql.SQL("ALTER DATABASE {} OWNER TO {}").format(sql.Identifier(database), sql.Identifier(second.name))
    )
    try:
        with pytest.raises(psycopg.errors.DependentObjectsStillExist):
            roles.drop_all()
        names = {
            r[0]
            for r in admin.execute(
                "SELECT rolname FROM pg_roles WHERE rolname = ANY (%s)", ([first.name, second.name],)
            ).fetchall()
        }
        assert names == {second.name}
    finally:
        admin.execute(sql.SQL("ALTER DATABASE {} OWNER TO CURRENT_USER").format(sql.Identifier(database)))
        admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(second.name)))


def test_l2_drop_owned_fails_within_its_lock_timeout(
    admin: Any, scratch_database: str, make_role: Callable[..., Role]
) -> None:
    """Another role's lock on a table the fixture role owns: ``DROP OWNED`` fails after its ``lock_timeout``
    instead of waiting forever, and the other session is not ended."""
    import threading
    import time

    from tabrion.testing.postgres import _RoleSet

    roles = _RoleSet(scratch_database)
    role = roles.create(new_name("own_"))
    other = make_role(dbname=psycopg.conninfo.conninfo_to_dict(scratch_database)["dbname"])
    with psycopg.connect(scratch_database, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE TABLE public.owned (id int)"))
        conn.execute(sql.SQL("ALTER TABLE public.owned OWNER TO {}").format(sql.Identifier(role.name)))
        conn.execute(sql.SQL("GRANT SELECT ON public.owned TO {}").format(sql.Identifier(other.name)))
    holder = psycopg.connect(other.url)
    try:
        holder.execute("SELECT count(*) FROM public.owned")  # ACCESS SHARE until the rollback
        errors: list[BaseException] = []

        def drop() -> None:
            try:
                roles.drop_owned(scratch_database)
            except BaseException as exc:
                errors.append(exc)

        worker = threading.Thread(target=drop)
        started = time.monotonic()
        worker.start()
        worker.join(25)
        waited = time.monotonic() - started
        holder.execute("SELECT 1")  # still alive: the other role's session is never ended
        holder.rollback()  # lets a DROP OWNED without a timeout finish, so the thread ends
        worker.join(30)
        assert waited < 25, "DROP OWNED waited without a lock_timeout"
        assert waited >= 9 and len(errors) == 1 and isinstance(errors[0], psycopg.errors.LockNotAvailable), errors
        roles.drop_owned(scratch_database)
    finally:
        holder.close()
        roles.drop_all()


# --- Re-review 2 (#11): RR2-L1 and Info ------------------------------------------------------------------------------

GRANT_IN_THE_ADMIN_DATABASE = """
import psycopg
from psycopg import sql

from helpers import user


def test_grant(tabrion_postgres_database, tabrion_postgres_url):
    owner = user(tabrion_postgres_database.owner_url)
    with psycopg.connect(tabrion_postgres_url, autocommit=True) as conn:
        database = sql.Identifier(conn.info.dbname)
        conn.execute(sql.SQL("GRANT CREATE ON DATABASE {} TO {}").format(database, sql.Identifier(owner)))
"""


def test_rr2_database_fixture_drops_the_roles_grants_in_the_admin_database(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, admin: Any, scratch_database: str
) -> None:
    """A grant to a fixture role in the admin's database (here a scratch database) would block its ``DROP ROLE``;
    the teardown's ``DROP OWNED`` there removes it, and nothing is left."""
    monkeypatch.setenv("TABRION_TEST_POSTGRES", scratch_database)
    result = _run(pytester, "", GRANT_IN_THE_ADMIN_DATABASE)
    result.assert_outcomes(passed=1)
    assert not leaked(admin, INNER_PREFIX)


def test_rr2_role_repr_hides_the_password() -> None:
    from tabrion.testing.postgres import _Role

    role = _Role("t_role", "SECRET-4711")
    assert "SECRET-4711" not in repr(role) and "t_role" in repr(role)
    assert "SECRET-4711" in role.url("host=localhost dbname=x")
