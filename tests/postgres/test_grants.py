"""TAB-031 against a real PostgreSQL: ``grant_runtime_role`` (TAB-T-R11..R18, contract 4.6, I-35).

The oracles are the grant functions of Axentra and Nodestro, verbatim (``tests/golden/role_grants.py``). Each case
builds a fresh fixture: an owner role **without** superuser, a schema with capital letters that it owns (risk R-T05)
and its objects (a serial table, an identity table named ``Mixed Case``, a partitioned table and its partition, a
sequence, a view, a function, a procedure and both bookkeeping tables), and a runtime role. The catalog snapshot
(``pg_namespace.nspacl``, ``relacl``, ``information_schema.role_table_grants`` and ``role_routine_grants``,
``pg_default_acl``, ``has_database_privilege``) after Tabrion's grant equals the one after the reference's grant on
its own fixture, and both equal ``tests/golden/role_grants.json``. ``TABRION_RECORD_GOLDEN=1`` rewrites the file from
the references.
"""

# The SQL is built from names this file generates (uuid hex) and fixed text, never from outside input.

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any
import uuid

import pytest

from tabrion.errors import RoleMissing, RoleUnsafe, UsageError
from tabrion.postgres import grant_runtime_role
from tabrion.testing._faults import FaultProxy, wait_backend_gone

from .conftest import Role, new_name

psycopg = pytest.importorskip("psycopg")
from psycopg import pq, sql  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

from tests.golden.role_grants import (  # noqa: E402
    AXENTRA_COMMIT,
    NODESTRO_COMMIT,
    axentra_grant_runtime_role,
    axentra_inspect_role,
    nodestro_grant_runtime_role,
    nodestro_inspect_role,
)

pytestmark = pytest.mark.postgres

IDLE = pq.TransactionStatus.IDLE
GOLDEN = Path(__file__).resolve().parent.parent / "golden" / "role_grants.json"
AXENTRA_PROTECTED = ("axentra_schema_migrations",)
NODESTRO_PROTECTED = ("nodestro_schema_migrations", "axentra_schema_migrations")

FIXTURE_OBJECTS = (
    "CREATE TABLE plain (id serial PRIMARY KEY, label text)",
    'CREATE TABLE "Mixed Case" (id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, note text)',
    "CREATE TABLE events (id int, at date NOT NULL) PARTITION BY RANGE (at)",
    "CREATE TABLE events_2026 PARTITION OF events FOR VALUES FROM ('2026-01-01') TO ('2027-01-01')",
    "CREATE SEQUENCE counter",
    "CREATE VIEW plain_labels AS SELECT label FROM plain",
    "CREATE FUNCTION answer() RETURNS int LANGUAGE sql AS 'SELECT 42'",
    "CREATE PROCEDURE touch() LANGUAGE sql AS 'SELECT 1'",
    "CREATE TABLE axentra_schema_migrations (version int PRIMARY KEY)",
    "CREATE TABLE nodestro_schema_migrations (version int PRIMARY KEY)",
)

# The snapshot of what ``role`` holds in ``schema``; every row names the grantee by placeholder (only the runtime
# role's rows are read) and the grantor by placeholder.
SNAPSHOT = {
    "nspacl": "SELECT a.privilege_type, a.is_grantable, pg_get_userbyid(a.grantor)::text AS grantor"
    " FROM pg_namespace n, aclexplode(n.nspacl) a"
    " WHERE n.nspname = %(schema)s AND a.grantee = %(oid)s",
    "relacl": "SELECT c.relname::text, c.relkind::text, a.privilege_type, a.is_grantable,"
    " pg_get_userbyid(a.grantor)::text AS grantor"
    " FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace, aclexplode(c.relacl) a"
    " WHERE n.nspname = %(schema)s AND a.grantee = %(oid)s",
    "role_table_grants": "SELECT table_name::text, privilege_type::text, is_grantable::text, grantor::text"
    " FROM information_schema.role_table_grants"
    " WHERE table_schema = %(schema)s AND grantee = %(role)s",
    "role_routine_grants": "SELECT routine_name::text, privilege_type::text, is_grantable::text, grantor::text"
    " FROM information_schema.role_routine_grants"
    " WHERE routine_schema = %(schema)s AND grantee = %(role)s",
    "pg_default_acl": "SELECT d.defaclobjtype::text, a.privilege_type, a.is_grantable,"
    " pg_get_userbyid(d.defaclrole)::text AS grantor"
    " FROM pg_default_acl d JOIN pg_namespace n ON n.oid = d.defaclnamespace, aclexplode(d.defaclacl) a"
    " WHERE n.nspname = %(schema)s AND a.grantee = %(oid)s",
    "database": "SELECT has_database_privilege(%(oid)s, current_database(), 'CONNECT') AS connect,"
    " EXISTS (SELECT 1 FROM pg_database d, aclexplode(d.datacl) a"
    "   WHERE d.datname = current_database() AND a.grantee = %(oid)s) AS granted",
}


@dataclass(frozen=True)
class Fixture:
    schema: str
    owner: Role
    runtime: Role


def snapshot(admin: Any, fixture: Fixture, *, role: str | None = None, schema: str | None = None) -> dict[str, Any]:
    role = role or fixture.runtime.name
    schema = schema or fixture.schema
    names = {fixture.owner.name: "<owner>", role: "<runtime>"}
    with admin.cursor(row_factory=dict_row) as cur:
        oid = cur.execute("SELECT oid FROM pg_roles WHERE rolname = %s", (role,)).fetchone()["oid"]
        result: dict[str, Any] = {}
        for key, query in SNAPSHOT.items():
            rows = cur.execute(query, {"schema": schema, "role": role, "oid": oid}).fetchall()
            normalized = [{k: names.get(v, v) if k == "grantor" else v for k, v in row.items()} for row in rows]
            # Sorted here, not by ORDER BY: the order of names depends on the server's collation ("Mixed Case").
            result[key] = sorted(normalized, key=lambda row: json.dumps(row, sort_keys=True))
    return result


def _quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def owner_connection(
    connect: Callable[..., Any],
    fixture: Fixture,
    *,
    path: Sequence[str] | None = None,
    url: str | None = None,
    autocommit: bool = True,
) -> Any:
    """The owner's connection with ``search_path`` set to the fixture schema (or to ``path``)."""
    conn = connect(url or fixture.owner.url, autocommit=autocommit)
    conn.execute(
        "SELECT pg_catalog.set_config('search_path', %s, false)",
        (", ".join(_quoted(p) for p in (path or [fixture.schema])),),
    )
    if not autocommit:
        conn.commit()
    return conn


@pytest.fixture
def make_fixture(
    admin: Any, admin_url: str, make_role: Callable[..., Role], connect: Callable[..., Any]
) -> Iterator[Callable[..., Fixture]]:
    """``make_fixture(objects=FIXTURE_OBJECTS, schema=None, runtime=None)``: owner, schema, objects, runtime role."""
    schemas: list[str] = []
    owners: list[str] = []

    def factory(
        objects: Sequence[str] = FIXTURE_OBJECTS, *, schema: str | None = None, runtime: Role | None = None
    ) -> Fixture:
        owner = make_role()
        name = schema or f"t_Grant_{uuid.uuid4().hex[:12]}"
        admin.execute(
            sql.SQL("CREATE SCHEMA {} AUTHORIZATION {}").format(sql.Identifier(name), sql.Identifier(owner.name))
        )
        schemas.append(name)
        owners.append(owner.name)
        fixture = Fixture(name, owner, runtime or make_role())
        conn = owner_connection(connect, fixture)
        for statement in objects:
            conn.execute(statement)
        conn.close()
        return fixture

    yield factory
    # A failed test may leave an owner connection in a transaction that holds locks in the schema; the connections
    # close only after this teardown, so end the owners' sessions instead of waiting for them.
    admin.execute(
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename = ANY (%s) AND pid <> pg_backend_pid()",
        (owners,),
    )
    for name in schemas:
        admin.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(name)))


def _golden() -> dict[str, Any]:
    return dict(json.loads(GOLDEN.read_text(encoding="utf-8")))


def _record(key: str, value: dict[str, Any]) -> None:
    data = _golden() if GOLDEN.exists() else {}
    data.update(
        {
            "about": "Catalog snapshots after the reference grant_runtime_role on the fixture of"
            " tests/postgres/test_grants.py (TAB-031, TAB-T-R11/R12); TABRION_RECORD_GOLDEN=1 rewrites them.",
            "axentra_commit": AXENTRA_COMMIT,
            "nodestro_commit": NODESTRO_COMMIT,
            key: value,
        }
    )
    GOLDEN.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def _reference_snapshot(
    admin: Any, connect: Callable[..., Any], make_fixture: Callable[..., Fixture], reference: str
) -> dict[str, Any]:
    fixture = make_fixture()
    conn = owner_connection(connect, fixture)
    if reference == "axentra":
        axentra_grant_runtime_role(conn, fixture.runtime.name)
    else:
        nodestro_grant_runtime_role(conn, fixture.runtime.name)
    result = snapshot(admin, fixture)
    if os.environ.get("TABRION_RECORD_GOLDEN") == "1":
        _record(reference, result)
    return result


# --- TAB-T-R11, R12: the catalog equals the references' and the golden file -----------------------------------------


@pytest.mark.parametrize("autocommit", [True, False])
def test_r11_axentra_golden(
    admin: Any, connect: Callable[..., Any], make_fixture: Callable[..., Fixture], autocommit: bool
) -> None:
    """Axentra drop-in: ``protected_tables=("axentra_schema_migrations",)``, ``schema=`` given."""
    reference = _reference_snapshot(admin, connect, make_fixture, "axentra")
    fixture = make_fixture()
    conn = owner_connection(connect, fixture, autocommit=autocommit)
    report = grant_runtime_role(conn, fixture.runtime.name, schema=fixture.schema, protected_tables=AXENTRA_PROTECTED)
    assert conn.info.transaction_status == IDLE
    ours = snapshot(admin, fixture)
    assert ours == reference == _golden()["axentra"]
    # nodestro_schema_migrations is not protected here: the runtime role may write it, as with Axentra.
    assert {
        "table_name": "nodestro_schema_migrations",
        "privilege_type": "INSERT",
        "is_grantable": "NO",
        "grantor": "<owner>",
    } in ours["role_table_grants"]
    # The returned report is a fresh inspect_role: it equals Axentra's report on the same catalog.
    expected = axentra_inspect_role(owner_connection(connect, fixture), fixture.runtime.name, fixture.schema)
    assert {k: v for k, v in report.as_dict().items() if k != "database"} == expected
    assert report.restricted and report.connect and not report.can_create and report.tables == 6


def test_r12_nodestro_golden(admin: Any, connect: Callable[..., Any], make_fixture: Callable[..., Fixture]) -> None:
    """Nodestro drop-in: ``protected_tables=SCHEMA_VERSION_TABLES``, the schema from ``current_schema()``."""
    reference = _reference_snapshot(admin, connect, make_fixture, "nodestro")
    fixture = make_fixture()
    conn = owner_connection(connect, fixture)
    report = grant_runtime_role(conn, fixture.runtime.name, protected_tables=NODESTRO_PROTECTED)
    ours = snapshot(admin, fixture)
    assert ours == reference == _golden()["nodestro"]
    assert report.as_dict() == nodestro_inspect_role(owner_connection(connect, fixture), fixture.runtime.name)


def test_r12_golden_file_matches_the_oracles() -> None:
    """The golden file names the reference commits, and the two references differ only in the protected table."""
    data = _golden()
    assert (data["axentra_commit"], data["nodestro_commit"]) == (AXENTRA_COMMIT, NODESTRO_COMMIT)
    axentra, nodestro = data["axentra"], data["nodestro"]
    assert {k: v for k, v in axentra.items() if k not in ("relacl", "role_table_grants")} == {
        k: v for k, v in nodestro.items() if k not in ("relacl", "role_table_grants")
    }
    only_axentra = [row for row in axentra["role_table_grants"] if row not in nodestro["role_table_grants"]]
    assert {row["table_name"] for row in only_axentra} == {"nodestro_schema_migrations"}
    assert {row["privilege_type"] for row in only_axentra} == {"INSERT", "UPDATE", "DELETE"}
    assert not [row for row in nodestro["role_table_grants"] if row not in axentra["role_table_grants"]]


# --- TAB-T-R13: idempotent; the default privileges cover the owner's later objects -----------------------------------


def test_r13_idempotent_and_default_privileges(
    admin: Any, connect: Callable[..., Any], make_fixture: Callable[..., Fixture]
) -> None:
    fixture = make_fixture()
    conn = owner_connection(connect, fixture)
    first = grant_runtime_role(conn, fixture.runtime.name, protected_tables=NODESTRO_PROTECTED)
    once = snapshot(admin, fixture)
    second = grant_runtime_role(conn, fixture.runtime.name, protected_tables=NODESTRO_PROTECTED)
    assert second == first
    assert snapshot(admin, fixture) == once
    # Objects the owner creates later are covered by the default privileges.
    conn.execute("CREATE TABLE later (id serial PRIMARY KEY, label text)")
    conn.execute("CREATE FUNCTION later_answer() RETURNS int LANGUAGE sql AS 'SELECT 7'")
    runtime = connect(fixture.runtime.url, autocommit=True)
    table = sql.Identifier(fixture.schema)
    runtime.execute(sql.SQL("INSERT INTO {}.later (label) VALUES ('x')").format(table))
    assert runtime.execute(sql.SQL("SELECT {}.later_answer()").format(table)).fetchone() == (7,)
    runtime.execute(sql.SQL('INSERT INTO {}."Mixed Case" (note) VALUES (%s)').format(table), ("y",))
    runtime.execute(sql.SQL("INSERT INTO {}.events VALUES (1, '2026-05-01')").format(table))
    assert runtime.execute(sql.SQL("SELECT count(*) FROM {}.axentra_schema_migrations").format(table)).fetchone()
    for statement in (
        "INSERT INTO {}.nodestro_schema_migrations VALUES (1)",
        "TRUNCATE {}.plain",
        "CREATE TABLE {}.mine (id int)",
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            runtime.execute(sql.SQL(statement).format(table))
    assert grant_runtime_role(conn, fixture.runtime.name).tables == 7


# --- TAB-T-R14: refusals; nothing is granted -------------------------------------------------------------------------


@pytest.fixture
def nologin_role(admin: Any) -> Iterator[Callable[..., str]]:
    """``nologin_role(attributes="", name=None)``: a NOLOGIN role, dropped afterwards (with what it owns)."""
    created: list[str] = []

    def factory(attributes: str = "", name: str | None = None) -> str:
        role = name or new_name()
        admin.execute(sql.SQL("CREATE ROLE {} NOLOGIN " + attributes).format(sql.Identifier(role)))
        created.append(role)
        return role

    yield factory
    for role in reversed(created):
        admin.execute(sql.SQL("DROP OWNED BY {} CASCADE").format(sql.Identifier(role)))
        admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))


def _nothing_granted(admin: Any, fixture: Fixture, role: str) -> None:
    """No privilege of ``role`` in the schema granted by another role (the owner's own rows name itself)."""
    taken = snapshot(admin, fixture, role=role)
    for key in ("nspacl", "relacl", "role_table_grants", "role_routine_grants", "pg_default_acl"):
        assert [row for row in taken[key] if row["grantor"] != "<runtime>"] == [], key


CASES = [
    "owner",
    "superuser_member",
    "bypassrls",
    "bypassrls_member",
    "owns_table",
    "owner_of_table_member",
    "schema_owner",
    "schema_owner_member",
]


@pytest.mark.parametrize("case", CASES)
def test_r14_refusals(
    admin: Any,
    connect: Callable[..., Any],
    make_fixture: Callable[..., Fixture],
    make_role: Callable[..., Role],
    nologin_role: Callable[..., str],
    case: str,
) -> None:
    fixture = make_fixture()
    target = fixture.runtime.name
    expected = {
        "owner": "table_owner",
        "superuser_member": "superuser",
        "bypassrls": "bypass_rls",
        "bypassrls_member": "bypass_rls",
        "owns_table": "table_owner",
        "owner_of_table_member": "table_owner",
        "schema_owner": "table_owner",
        "schema_owner_member": "table_owner",
    }[case]
    reference_refuses = case in ("owner", "bypassrls", "owns_table", "owner_of_table_member")
    ident = sql.Identifier(target)
    if case == "owner":
        target = fixture.owner.name
    elif case == "superuser_member":
        admin.execute(sql.SQL("ALTER ROLE {} NOINHERIT").format(ident))
        admin.execute(sql.SQL("GRANT {} TO {}").format(sql.Identifier(nologin_role("SUPERUSER")), ident))
    elif case == "bypassrls":
        target = make_role("BYPASSRLS").name
    elif case == "bypassrls_member":
        admin.execute(sql.SQL("ALTER ROLE {} NOINHERIT").format(ident))
        admin.execute(sql.SQL("GRANT {} TO {}").format(sql.Identifier(nologin_role("BYPASSRLS")), ident))
    elif case in ("schema_owner", "schema_owner_member"):
        # The schema's owner (or a NOINHERIT member of it) may drop every table in it (review M-1, D-49). The
        # connecting role keeps its tables and may grant on the schema.
        holder = target if case == "schema_owner" else nologin_role()
        nsp = sql.Identifier(fixture.schema)
        admin.execute(sql.SQL("ALTER SCHEMA {} OWNER TO {}").format(nsp, sql.Identifier(holder)))
        admin.execute(
            sql.SQL("GRANT USAGE, CREATE ON SCHEMA {} TO {} WITH GRANT OPTION").format(
                nsp, sql.Identifier(fixture.owner.name)
            )
        )
        if case == "schema_owner_member":
            admin.execute(sql.SQL("ALTER ROLE {} NOINHERIT").format(ident))
            admin.execute(sql.SQL("GRANT {} TO {}").format(sql.Identifier(holder), ident))
    elif case == "owns_table":
        admin.execute(sql.SQL("ALTER TABLE {}.plain OWNER TO {}").format(sql.Identifier(fixture.schema), ident))
    else:  # a NOINHERIT member of a role (not the owner) that owns a table of the schema
        holder = nologin_role()
        admin.execute(
            sql.SQL("ALTER TABLE {}.events OWNER TO {}").format(sql.Identifier(fixture.schema), sql.Identifier(holder))
        )
        admin.execute(sql.SQL("ALTER ROLE {} NOINHERIT").format(ident))
        admin.execute(sql.SQL("GRANT {} TO {}").format(sql.Identifier(holder), ident))
    conn = owner_connection(connect, fixture, autocommit=False)
    with pytest.raises(RoleUnsafe) as info:
        grant_runtime_role(conn, target, protected_tables=NODESTRO_PROTECTED)
    assert expected in str(info.value.details["reasons"]).split(",")
    assert conn.info.transaction_status == IDLE
    _nothing_granted(admin, fixture, target)
    reference = owner_connection(connect, fixture)
    if reference_refuses:
        for grant in (nodestro_grant_runtime_role, axentra_grant_runtime_role):
            with pytest.raises(RuntimeError):
                grant(reference, target)
        _nothing_granted(admin, fixture, target)
    else:
        # The references read rolsuper/rolbypassrls of the role itself and miss the membership (TODO TB-035).
        nodestro_grant_runtime_role(reference, target)
        assert snapshot(admin, fixture, role=target)["nspacl"]


def test_r14_member_of_the_owner_is_refused_in_an_empty_schema(
    admin: Any, connect: Callable[..., Any], make_fixture: Callable[..., Fixture], nologin_role: Callable[..., str]
) -> None:
    """A role that can ``SET ROLE`` to the owner will own every table the owner creates (contract I-35, D-46).

    The references see no owned table in an empty schema and grant; Tabrion refuses (also NOINHERIT members). The
    schema belongs to another role here, so the check of the schema's owner (D-49) does not hide this one."""
    fixture = make_fixture(objects=())
    holder = nologin_role()
    nsp = sql.Identifier(fixture.schema)
    admin.execute(sql.SQL("ALTER SCHEMA {} OWNER TO {}").format(nsp, sql.Identifier(holder)))
    admin.execute(
        sql.SQL("GRANT USAGE, CREATE ON SCHEMA {} TO {} WITH GRANT OPTION").format(
            nsp, sql.Identifier(fixture.owner.name)
        )
    )
    member = nologin_role("NOINHERIT")
    admin.execute(sql.SQL("GRANT {} TO {}").format(sql.Identifier(fixture.owner.name), sql.Identifier(member)))
    conn = owner_connection(connect, fixture)
    with pytest.raises(RoleUnsafe) as info:
        grant_runtime_role(conn, member)
    assert info.value.details == {"reasons": "table_owner"}
    _nothing_granted(admin, fixture, member)
    axentra_grant_runtime_role(conn, member)  # the reference grants
    assert snapshot(admin, fixture, role=member)["nspacl"]
    admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(nsp))  # before the roles it names go


def test_r14_role_missing(connect: Callable[..., Any], make_fixture: Callable[..., Fixture]) -> None:
    fixture = make_fixture()
    conn = owner_connection(connect, fixture)
    with pytest.raises(RoleMissing) as info:
        grant_runtime_role(conn, "t_no_such_role_" + uuid.uuid4().hex[:8])
    assert "t_no_such_role" not in str(info.value)


def test_r14_no_schema(connect: Callable[..., Any], make_fixture: Callable[..., Fixture]) -> None:
    fixture = make_fixture()
    conn = connect(fixture.owner.url, autocommit=True)
    conn.execute("SELECT pg_catalog.set_config('search_path', '', false)")
    with pytest.raises(ValueError, match="schema"):
        grant_runtime_role(conn, fixture.runtime.name)


# --- TAB-T-R15: the protected tables; only the named schema -----------------------------------------------------------


def test_r15_protected_tables_in_the_named_schema_only(
    admin: Any, connect: Callable[..., Any], make_fixture: Callable[..., Fixture]
) -> None:
    fixture = make_fixture(objects=(*FIXTURE_OBJECTS, 'CREATE TABLE "odd ""name""" (id int)'))
    # A second schema of the same owner, first on the search_path, with a bookkeeping table of the same name.
    other = f"t_other_{uuid.uuid4().hex[:8]}"
    admin.execute(
        sql.SQL("CREATE SCHEMA {} AUTHORIZATION {}").format(sql.Identifier(other), sql.Identifier(fixture.owner.name))
    )
    try:
        owner = owner_connection(connect, fixture, path=[other, fixture.schema])
        owner.execute(sql.SQL("CREATE TABLE {}.extra_migrations (version int)").format(sql.Identifier(other)))
        report = grant_runtime_role(
            owner,
            fixture.runtime.name,
            schema=fixture.schema,
            protected_tables=("axentra_schema_migrations", 'odd "name"', "extra_migrations", "missing"),
        )
        assert report.schema == fixture.schema
        grants = {(r["table_name"], r["privilege_type"]) for r in snapshot(admin, fixture)["role_table_grants"]}
        for table in ("axentra_schema_migrations", 'odd "name"'):
            assert {p for t, p in grants if t == table} == {"SELECT"}
        assert {p for t, p in grants if t == "nodestro_schema_migrations"} == {"SELECT", "INSERT", "UPDATE", "DELETE"}
        untouched = snapshot(admin, fixture, schema=other)
        assert untouched["relacl"] == untouched["nspacl"] == untouched["pg_default_acl"] == []
    finally:
        admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(other)))


# --- TAB-T-R16: CONNECT ---------------------------------------------------------------------------------------------


@pytest.fixture
def closed_database(
    scratch_database: str, make_role: Callable[..., Role], connect: Callable[..., Any]
) -> Iterator[tuple[Any, Fixture]]:
    """A database without ``CONNECT`` for PUBLIC, an owner role allowed to connect, its schema and a runtime role."""
    dbname = psycopg.conninfo.conninfo_to_dict(scratch_database)["dbname"]
    owner, runtime = make_role(dbname=dbname), make_role(dbname=dbname)
    admin = connect(scratch_database, autocommit=True)
    database = sql.Identifier(str(dbname))
    admin.execute(sql.SQL("REVOKE CONNECT ON DATABASE {} FROM PUBLIC").format(database))
    admin.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(database, sql.Identifier(owner.name)))
    schema = new_name()
    admin.execute(
        sql.SQL("CREATE SCHEMA {} AUTHORIZATION {}").format(sql.Identifier(schema), sql.Identifier(owner.name))
    )
    yield admin, Fixture(schema, owner, runtime)
    admin.execute(sql.SQL("ALTER DATABASE {} OWNER TO CURRENT_USER").format(database))


def test_r16_owner_without_the_right_to_grant_connect(connect: Callable[..., Any], closed_database: Any) -> None:
    """PostgreSQL grants nothing (a warning): the grants are kept and the report says ``connect=False``."""
    admin, fixture = closed_database
    conn = owner_connection(connect, fixture)
    report = grant_runtime_role(conn, fixture.runtime.name)
    assert not report.connect
    assert snapshot(admin, fixture)["nspacl"]
    with pytest.raises(psycopg.OperationalError):
        connect(fixture.runtime.url)


def test_r16_insufficient_privilege_is_ignored(connect: Callable[..., Any], closed_database: Any) -> None:
    """An owner with no privilege at all on the database gets ``InsufficientPrivilege``; it is ignored."""
    admin, fixture = closed_database
    conn = owner_connection(connect, fixture)
    database = sql.Identifier(str(conn.info.dbname))
    admin.execute(
        sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC, {}").format(database, sql.Identifier(fixture.owner.name))
    )
    with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
        conn.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(database, sql.Identifier(fixture.runtime.name))
        )
    report = grant_runtime_role(conn, fixture.runtime.name)
    assert not report.connect and conn.info.transaction_status == IDLE
    assert snapshot(admin, fixture)["nspacl"]  # the grant transaction before it stays committed


def test_r16_database_owner_grants_connect(connect: Callable[..., Any], closed_database: Any) -> None:
    admin, fixture = closed_database
    conn = owner_connection(connect, fixture)
    database = sql.Identifier(str(conn.info.dbname))
    admin.execute(sql.SQL("ALTER DATABASE {} OWNER TO {}").format(database, sql.Identifier(fixture.owner.name)))
    report = grant_runtime_role(conn, fixture.runtime.name, grant_connect=False)
    assert not report.connect and snapshot(admin, fixture)["database"] == [{"connect": False, "granted": False}]
    report = grant_runtime_role(conn, fixture.runtime.name)
    assert report.connect and snapshot(admin, fixture)["database"] == [{"connect": True, "granted": True}]
    connect(fixture.runtime.url).close()


# --- TAB-T-R17: a transaction of its own (T-NOTX); atomic ---------------------------------------------------------


@pytest.mark.parametrize("state", ["intrans", "inerror", "pipeline"])
def test_r17_refuses_the_callers_transaction(
    admin: Any, connect: Callable[..., Any], make_fixture: Callable[..., Fixture], state: str
) -> None:
    fixture = make_fixture()
    conn = owner_connection(connect, fixture, autocommit=False)
    if state == "pipeline":
        with conn.pipeline():
            conn.execute("SELECT 1")
            with pytest.raises(UsageError) as info:
                grant_runtime_role(conn, fixture.runtime.name)
            assert conn.execute("SELECT 2").fetchone() == (2,)
        assert conn.info.transaction_status == pq.TransactionStatus.INTRANS
    else:
        conn.execute("SELECT 1")
        if state == "inerror":
            with pytest.raises(psycopg.errors.DivisionByZero):
                conn.execute("SELECT 1 / 0")
        status = conn.info.transaction_status
        with pytest.raises(UsageError) as info:
            grant_runtime_role(conn, fixture.runtime.name)
        assert conn.info.transaction_status == status
    assert info.value.code == "postgres.nested_transaction"
    conn.rollback()
    _nothing_granted(admin, fixture, fixture.runtime.name)


@pytest.mark.parametrize("autocommit", [True, False])
def test_r17_own_transaction_leaves_the_connection_as_it_was(
    admin: Any, connect: Callable[..., Any], make_fixture: Callable[..., Fixture], autocommit: bool
) -> None:
    fixture = make_fixture()
    conn = owner_connection(connect, fixture, autocommit=autocommit)
    conn.row_factory = dict_row
    grant_runtime_role(conn, fixture.runtime.name)
    assert conn.info.transaction_status == IDLE
    assert conn.autocommit is autocommit and conn.row_factory is dict_row
    assert snapshot(admin, fixture)["pg_default_acl"]
    # In an autocommit pipeline the queued statement ends with the sync (status IDLE, contract I-34): it runs.
    if autocommit:
        with conn.pipeline():
            conn.execute("SELECT 1")
            assert grant_runtime_role(conn, fixture.runtime.name).restricted
        assert conn.info.transaction_status == IDLE


def test_r17_atomic_when_the_connection_is_lost(
    admin: Any, admin_url: str, connect: Callable[..., Any], make_fixture: Callable[..., Fixture]
) -> None:
    """The connection is cut before the first ``ALTER DEFAULT PRIVILEGES``: the earlier grants are rolled back."""
    if str(psycopg.conninfo.conninfo_to_dict(admin_url).get("host") or "").startswith("/"):
        pytest.skip("the fault proxy needs a TCP server address")
    fixture = make_fixture()
    with FaultProxy.in_front_of(admin_url) as proxy:
        conn = owner_connection(connect, fixture, url=proxy.dsn(fixture.owner.url))
        backend = conn.info.backend_pid
        proxy.arm(b"ALTER DEFAULT PRIVILEGES", fault="request")
        with pytest.raises(psycopg.OperationalError):
            grant_runtime_role(conn, fixture.runtime.name)
        assert proxy.fired == 1
    proxy.check()
    assert wait_backend_gone(admin_url, backend)
    _nothing_granted(admin, fixture, fixture.runtime.name)


# --- TAB-T-R18: quoting; a hostile search_path ---------------------------------------------------------------------


def test_r18_quoted_names_and_a_hostile_search_path(
    admin: Any,
    connect: Callable[..., Any],
    make_fixture: Callable[..., Fixture],
    make_role: Callable[..., Role],
    nologin_role: Callable[..., str],
) -> None:
    """Names with quotes, spaces and capitals; a schema before ``pg_catalog`` with an ``=`` that always holds.

    The catalog afterwards equals the Axentra golden file: nothing resolved through the search path."""
    odd = nologin_role(name=f'T_Run "x" {uuid.uuid4().hex[:8]}')
    fixture = make_fixture(schema=f't_Odd "s" {uuid.uuid4().hex[:8]}')
    fixture = Fixture(fixture.schema, fixture.owner, Role(odd, ""))
    trap = f"t_trap_{uuid.uuid4().hex[:8]}"
    admin.execute(
        sql.SQL("CREATE SCHEMA {} AUTHORIZATION {}").format(sql.Identifier(trap), sql.Identifier(fixture.owner.name))
    )
    try:
        owner = owner_connection(connect, fixture, path=[trap, "pg_catalog", fixture.schema])
        t = sql.Identifier(trap)
        for statement in (
            "CREATE FUNCTION {t}.always(text, text) RETURNS boolean LANGUAGE sql AS 'SELECT true'",
            "CREATE OPERATOR {t}.= (LEFTARG = text, RIGHTARG = text, FUNCTION = {t}.always)",
            "CREATE FUNCTION {t}.always(name, name) RETURNS boolean LANGUAGE sql AS 'SELECT true'",
            "CREATE OPERATOR {t}.= (LEFTARG = name, RIGHTARG = name, FUNCTION = {t}.always)",
            "CREATE FUNCTION {t}.current_schema() RETURNS name LANGUAGE sql AS $$SELECT 'public'::name$$",
            "CREATE FUNCTION {t}.pg_has_role(oid, name, text) RETURNS boolean LANGUAGE sql AS 'SELECT false'",
            "CREATE TABLE {t}.axentra_schema_migrations (version int)",
        ):
            owner.execute(sql.SQL(statement).format(t=t))
        report = grant_runtime_role(owner, odd, schema=fixture.schema, protected_tables=AXENTRA_PROTECTED)
        assert (report.role, report.schema, report.tables) == (odd, fixture.schema, 6)
        assert snapshot(admin, fixture) == _golden()["axentra"]
        assert snapshot(admin, fixture, schema=trap)["relacl"] == []
        # The member-of-owner check is not fooled either.
        admin.execute(sql.SQL("GRANT {} TO {}").format(sql.Identifier(fixture.owner.name), sql.Identifier(odd)))
        with pytest.raises(RoleUnsafe):
            grant_runtime_role(owner, odd, schema=fixture.schema)
    finally:
        admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(trap)))


# --- Review of #8: M-1 (above, R14), L-1..L-4 ---------------------------------------------------------------------


def test_r14_can_create_is_reported(
    admin: Any, connect: Callable[..., Any], make_fixture: Callable[..., Fixture]
) -> None:
    """Review L-1: CREATE on the schema, granted by someone else, is kept and reported; the caller checks it."""
    fixture = make_fixture()
    admin.execute(
        sql.SQL("GRANT CREATE ON SCHEMA {} TO {}").format(
            sql.Identifier(fixture.schema), sql.Identifier(fixture.runtime.name)
        )
    )
    report = grant_runtime_role(owner_connection(connect, fixture), fixture.runtime.name)
    assert report.can_create is True
    assert report.restricted is True  # frozen definition: superuser, BYPASSRLS, owned tables only


def test_r14_schema_name_over_63_bytes(
    admin: Any, connect: Callable[..., Any], make_fixture: Callable[..., Fixture]
) -> None:
    """Review L-2: a 64-byte name would be truncated to the 63-byte schema next to it; refused before any grant."""
    name = "t_long_" + uuid.uuid4().hex[:8]
    name += "x" * (63 - len(name))
    fixture = make_fixture(schema=name)
    conn = owner_connection(connect, fixture)
    for schema in (name + "Z", name + "é"):  # 64 characters, 65 bytes
        with pytest.raises(ValueError, match="63 bytes") as info:
            grant_runtime_role(conn, fixture.runtime.name, schema=schema)
        assert name not in str(info.value)
    _nothing_granted(admin, fixture, fixture.runtime.name)
    with pytest.raises(ValueError, match="63 bytes"):
        grant_runtime_role(conn, fixture.runtime.name, protected_tables=("p" * 64,))
    with pytest.raises(ValueError, match="63 bytes"):
        grant_runtime_role(conn, "r" * 64)
    assert grant_runtime_role(conn, fixture.runtime.name, schema=name).schema == name  # 63 bytes are fine


def test_dl1_multibyte_names_are_measured_in_bytes(
    admin: Any, connect: Callable[..., Any], make_fixture: Callable[..., Fixture]
) -> None:
    """Delta re-review DL-1 of #8: the limit counts UTF-8 bytes, not characters (a chars mutant accepts these)."""
    name = "t_" + uuid.uuid4().hex[:8] + "\u00e9" * 26 + "x"  # 37 characters, 63 bytes: accepted
    assert (len(name), len(name.encode())) == (37, 63)
    fixture = make_fixture(schema=name)
    conn = owner_connection(connect, fixture)
    too_long = "\u00e9" * 32  # 32 characters, 64 bytes
    for kwargs in ({"schema": too_long}, {"protected_tables": (too_long,)}):
        with pytest.raises(ValueError, match="63 bytes"):
            grant_runtime_role(conn, fixture.runtime.name, **kwargs)
    with pytest.raises(ValueError, match="63 bytes"):
        grant_runtime_role(conn, too_long)
    _nothing_granted(admin, fixture, fixture.runtime.name)
    assert grant_runtime_role(conn, fixture.runtime.name, schema=name).schema == name


def test_r14_schema_missing(admin: Any, connect: Callable[..., Any], make_fixture: Callable[..., Fixture]) -> None:
    """Review L-4: a schema that does not exist is refused before any grant, without its name."""
    fixture = make_fixture()
    missing = "t_missing_" + uuid.uuid4().hex[:8]
    conn = owner_connection(connect, fixture, autocommit=False)
    with pytest.raises(UsageError) as info:
        grant_runtime_role(conn, fixture.runtime.name, schema=missing)
    assert info.value.code == "postgres.schema_missing" and missing not in str(info.value)
    assert conn.info.transaction_status == IDLE
    path = owner_connection(connect, fixture, path=[missing])  # a search_path naming it: no current schema
    with pytest.raises(ValueError, match="needs a schema"):
        grant_runtime_role(path, fixture.runtime.name)


def test_r15_truncate_granted_before_is_revoked(
    admin: Any, connect: Callable[..., Any], make_fixture: Callable[..., Fixture]
) -> None:
    """Review L-3 (mutant 13): on a schema that is not fresh the REVOKE also takes back TRUNCATE."""
    fixture = make_fixture()
    for table in ("axentra_schema_migrations", "plain"):
        admin.execute(
            sql.SQL("GRANT TRUNCATE ON {}.{} TO {}").format(
                sql.Identifier(fixture.schema), sql.Identifier(table), sql.Identifier(fixture.runtime.name)
            )
        )
    grant_runtime_role(owner_connection(connect, fixture), fixture.runtime.name, protected_tables=AXENTRA_PROTECTED)
    held = {(r["relname"], r["privilege_type"]) for r in snapshot(admin, fixture)["relacl"]}
    assert ("axentra_schema_migrations", "TRUNCATE") not in held
    assert ("axentra_schema_migrations", "SELECT") in held
    assert ("plain", "TRUNCATE") in held  # not protected: left as it was


@pytest.fixture
def owned_database(closed_database: Any) -> tuple[Any, Fixture]:
    """``closed_database`` owned by its owner role, which may then grant ``CONNECT``."""
    admin, fixture = closed_database
    admin.execute(
        sql.SQL("ALTER DATABASE {} OWNER TO {}").format(
            sql.Identifier(str(admin.info.dbname)), sql.Identifier(fixture.owner.name)
        )
    )
    return admin, fixture


def test_r16_connect_through_public_adds_no_acl_entry(connect: Callable[..., Any], owned_database: Any) -> None:
    """Review L-3 (mutant 15): a role that may connect already (PUBLIC) gets no entry of its own."""
    admin, fixture = owned_database
    database = sql.Identifier(str(admin.info.dbname))
    admin.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO PUBLIC").format(database))
    report = grant_runtime_role(owner_connection(connect, fixture), fixture.runtime.name)
    assert report.connect
    assert snapshot(admin, fixture)["database"] == [{"connect": True, "granted": False}]


def test_r16_other_errors_of_the_connect_step_propagate(
    admin_url: str, connect: Callable[..., Any], owned_database: Any
) -> None:
    """Review L-3 (mutant 17): only InsufficientPrivilege is ignored; a lost connection during the CONNECT step
    propagates, and the grants of the first transaction stay (contract I-35)."""
    if str(psycopg.conninfo.conninfo_to_dict(admin_url).get("host") or "").startswith("/"):
        pytest.skip("the fault proxy needs a TCP server address")
    admin, fixture = owned_database
    with FaultProxy.in_front_of(admin_url) as proxy:
        conn = owner_connection(connect, fixture, url=proxy.dsn(fixture.owner.url))
        proxy.arm(b"GRANT CONNECT", fault="request")
        with pytest.raises(psycopg.OperationalError):
            grant_runtime_role(conn, fixture.runtime.name)
        assert proxy.fired == 1
    proxy.check()
    taken = snapshot(admin, fixture)
    assert taken["nspacl"] and taken["database"] == [{"connect": False, "granted": False}]


def test_r16_a_failing_connect_step_propagates(connect: Callable[..., Any], owned_database: Any) -> None:
    """Review L-3 (mutant 17): an error other than InsufficientPrivilege is the caller's. Here the database row is
    locked by another transaction and the owner's ``lock_timeout`` ends the wait (55P03); the connection stays
    usable, the grants of the first transaction stay."""
    admin, fixture = owned_database
    database = sql.Identifier(str(admin.info.dbname))
    blocker = connect(admin.info.dsn + " password=" + str(admin.info.password or ""))
    blocker.execute(sql.SQL("GRANT TEMPORARY ON DATABASE {} TO PUBLIC").format(database))  # holds the row lock
    conn = owner_connection(connect, fixture)
    conn.execute("SELECT pg_catalog.set_config('lock_timeout', '300ms', false)")
    try:
        with pytest.raises(psycopg.errors.LockNotAvailable):
            grant_runtime_role(conn, fixture.runtime.name)
    finally:
        blocker.rollback()
    assert conn.info.transaction_status == IDLE
    assert snapshot(admin, fixture)["nspacl"]
    assert grant_runtime_role(conn, fixture.runtime.name).connect  # a second run completes
