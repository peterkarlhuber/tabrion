"""TAB-016 against a real PostgreSQL: ``role_info``, ``check_role`` (TAB-T-R01..R08), ``inspect_role`` (R09, R10).

The roles under test are LOGIN roles without superuser (``make_role``), next to the superuser of
``TABRION_TEST_POSTGRES``. The reference queries of Axentra (``data/database.py`` ``role_info``, ``data/roles.py``
``inspect_role``, master 40c0f2c) and Nodestro (``storage_postgres.py`` ``role_info`` and ``inspect_role``, master
40511eb) are copied verbatim below as oracles: Tabrion must give the same answer for every case.
"""

# The SQL is built from names this file generates (uuid hex) and fixed text, never from outside input.

from __future__ import annotations

from collections.abc import Callable, Iterator
import dataclasses
from typing import Any

import pytest

from tabrion.errors import RoleMissing, RoleUnsafe
from tabrion.postgres import RoleInfo, RoleReport, check_role, inspect_role, role_info

from .conftest import Role, new_name

psycopg = pytest.importorskip("psycopg")
from psycopg import pq  # noqa: E402
from psycopg.rows import dict_row, tuple_row  # noqa: E402

pytestmark = pytest.mark.postgres

IDLE = pq.TransactionStatus.IDLE

AXENTRA_ROLE_INFO = (
    "SELECT r.rolsuper AS superuser, r.rolbypassrls AS bypass_rls,"
    " EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
    "   WHERE c.relkind IN ('r', 'p') AND c.relrowsecurity"
    "   AND n.nspname = ANY (current_schemas(false))"
    "   AND pg_has_role(current_user, c.relowner, 'MEMBER')) AS table_owner"
    " FROM pg_roles r WHERE r.rolname = current_user"
)
NODESTRO_ROLE_INFO = (
    "SELECT current_user::text, r.rolsuper, r.rolbypassrls,"
    " EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
    "   WHERE n.nspname = current_schema() AND c.relkind IN ('r', 'p')"
    "   AND pg_has_role(current_user, c.relowner, 'MEMBER'))"
    " FROM pg_roles r WHERE r.rolname = current_user"
)


def _oracle_role_info(conn: Any, scope: str) -> RoleInfo:
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute("SELECT current_user::text")
        name = cur.fetchone()[0]
        if scope == "rls":
            cur.execute(AXENTRA_ROLE_INFO)
            superuser, bypass, owner = cur.fetchone()
        else:
            cur.execute(NODESTRO_ROLE_INFO)
            _, superuser, bypass, owner = cur.fetchone()
    if not conn.autocommit:
        conn.rollback()
    return RoleInfo(role=name, superuser=bool(superuser), bypass_rls=bool(bypass), table_owner=bool(owner))


def _any_bypassrls_role(conn: Any) -> bool:
    """Whether any role has BYPASSRLS: a superuser can become it (contract I-20)."""
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolbypassrls)")
        found = bool(cur.fetchone()[0])
    if not conn.autocommit:
        conn.rollback()
    return found


def _axentra_inspect_role(conn: Any, role: str, schema: str | None = None) -> dict[str, Any]:
    """Axentra ``inspect_role`` (data/roles.py:72-104) without the dataclass."""
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute("SELECT current_user, coalesce(%s, current_schema())", (schema,))
        owner, schema_name = cur.fetchone() or ("", "")
        cur.execute("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = %s", (role,))
        attrs = cur.fetchone()
        cur.execute(
            "SELECT c.relname, pg_has_role(%s, c.relowner, 'MEMBER') FROM pg_class c"
            " JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE n.nspname = %s AND c.relkind IN ('r', 'p') ORDER BY c.relname",
            (role, schema_name),
        )
        tables = cur.fetchall()
        cur.execute(
            "SELECT has_database_privilege(%s, current_database(), 'CONNECT'), has_schema_privilege(%s, %s, 'CREATE')",
            (role, role, schema_name),
        )
        connect, create = cur.fetchone() or (False, False)
    if not conn.autocommit:
        conn.rollback()
    owned = [str(name) for name, own in tables if own]
    return {
        "role": role,
        "schema": str(schema_name),
        "owner": str(owner),
        "tables": len(tables),
        "connect": bool(connect),
        "superuser": bool(attrs[0]),
        "bypass_rls": bool(attrs[1]),
        "owned_tables": owned,
        "can_create": bool(create),
        "restricted": not (attrs[0] or attrs[1] or owned),
    }


def _nodestro_inspect_role(conn: Any, role: str) -> dict[str, Any]:
    """Nodestro ``inspect_role`` (storage_postgres.py:947-984) on a connection instead of its database object."""
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute(
            "SELECT current_user::text, current_schema()::text, current_database()::text, r.rolsuper,"
            " r.rolbypassrls FROM pg_roles r WHERE r.rolname = %s",
            (role,),
        )
        owner, schema, dbname, superuser, bypass = cur.fetchone()
        cur.execute(
            "SELECT c.relname, pg_has_role(%s, c.relowner, 'MEMBER') FROM pg_class c"
            " JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE n.nspname = current_schema() AND c.relkind IN ('r', 'p') ORDER BY c.relname",
            (role,),
        )
        tables = cur.fetchall()
        cur.execute(
            "SELECT has_database_privilege(%s, current_database(), 'CONNECT'),"
            " has_schema_privilege(%s, current_schema(), 'CREATE')",
            (role, role),
        )
        connect, create = cur.fetchone()
    if not conn.autocommit:
        conn.rollback()
    owned = [str(name) for name, own in tables if own]
    return {
        "role": role,
        "owner": owner,
        "schema": schema,
        "database": dbname,
        "tables": len(tables),
        "superuser": bool(superuser),
        "bypass_rls": bool(bypass),
        "owned_tables": owned,
        "connect": bool(connect),
        "can_create": bool(create),
        "restricted": not (superuser or bypass or owned),
    }


@pytest.fixture
def setup(admin: Any, schema: str) -> Callable[..., None]:
    """``setup(sql, ...)``: run statements as the admin (with ``{s}`` for the schema)."""

    def run(*statements: str) -> None:
        for statement in statements:
            admin.execute(statement.format(s=schema))

    run("GRANT USAGE ON SCHEMA {s} TO PUBLIC")
    return run


def _as(connect: Callable[..., Any], role: Role, schema: str, *, path: str | None = None, **kwargs: Any) -> Any:
    return connect(role.url, options=f"-c search_path={path or schema}", **kwargs)


@pytest.mark.parametrize("scope", ["schema", "rls"])
def test_r01_restricted_runtime_role(
    connect: Callable[..., Any], make_role: Callable[..., Role], schema: str, setup: Any, scope: str
) -> None:
    setup("ALTER TABLE {s}.parts ENABLE ROW LEVEL SECURITY")
    role = make_role()
    conn = _as(connect, role, schema)
    info = role_info(conn, owner_scope=scope)  # type: ignore[arg-type]
    assert info == RoleInfo(role=role.name, superuser=False, bypass_rls=False, table_owner=False)
    assert info.restricted
    assert info == _oracle_role_info(conn, scope)
    assert check_role(conn, owner_scope=scope) == info  # type: ignore[arg-type]
    assert conn.info.transaction_status == IDLE


@pytest.mark.parametrize("scope", ["schema", "rls"])
def test_r02_superuser(connect: Callable[..., Any], schema: str, setup: Any, scope: str) -> None:
    setup("ALTER TABLE {s}.parts ENABLE ROW LEVEL SECURITY")
    conn = connect(options=f"-c search_path={schema}")
    info = role_info(conn, owner_scope=scope)  # type: ignore[arg-type]
    assert info.superuser and info.table_owner and not info.restricted
    # DL-2: the references read rolbypassrls of the superuser itself, which a superuser created NOBYPASSRLS lacks; a
    # superuser is a member of every role, so Tabrion reports bypass_rls when any role has BYPASSRLS (contract I-20).
    oracle = _oracle_role_info(conn, scope)
    assert dataclasses.replace(info, bypass_rls=oracle.bypass_rls) == oracle
    assert info.bypass_rls is _any_bypassrls_role(conn)
    with pytest.raises(RoleUnsafe) as refused:
        check_role(conn, owner_scope=scope)  # type: ignore[arg-type]
    assert refused.value.code == "postgres.role_unsafe"
    reasons = str(refused.value.details["reasons"]).split(",")
    assert reasons[0] == "superuser" and "table_owner" in reasons
    assert check_role(conn, allow_superuser=True, owner_scope=scope) == info  # type: ignore[arg-type]
    with pytest.raises(RoleUnsafe) as still:
        check_role(conn, allow_table_owner=True, owner_scope=scope)  # type: ignore[arg-type]
    assert "table_owner" not in str(still.value.details["reasons"])


@pytest.mark.parametrize("scope", ["schema", "rls"])
def test_r03_bypass_rls(
    connect: Callable[..., Any], make_role: Callable[..., Role], schema: str, setup: Any, scope: str
) -> None:
    role = make_role("BYPASSRLS")
    conn = _as(connect, role, schema)
    info = role_info(conn, owner_scope=scope)  # type: ignore[arg-type]
    assert info == RoleInfo(role=role.name, superuser=False, bypass_rls=True, table_owner=False)
    assert not info.restricted
    assert info == _oracle_role_info(conn, scope)
    with pytest.raises(RoleUnsafe) as refused:
        check_role(conn, owner_scope=scope, allow_table_owner=True)  # type: ignore[arg-type]
    assert refused.value.details["reasons"] == "bypass_rls"
    assert check_role(conn, allow_superuser=True, owner_scope=scope) == info  # type: ignore[arg-type]


def test_r04_table_owner_without_rls(
    connect: Callable[..., Any], make_role: Callable[..., Role], schema: str, setup: Any
) -> None:
    """A table without row-level security: an owner for the schema scope (Nodestro), not for the rls scope."""
    role = make_role()
    setup(f'ALTER TABLE {{s}}.parts OWNER TO "{role.name}"')
    conn = _as(connect, role, schema)
    assert role_info(conn).table_owner is True
    assert role_info(conn, owner_scope="rls").table_owner is False
    for scope in ("schema", "rls"):
        assert role_info(conn, owner_scope=scope) == _oracle_role_info(conn, scope)
    with pytest.raises(RoleUnsafe) as refused:
        check_role(conn)
    assert refused.value.details["reasons"] == "table_owner"
    assert check_role(conn, owner_scope="rls").table_owner is False


def test_r05_table_owner_with_rls(
    connect: Callable[..., Any], make_role: Callable[..., Role], schema: str, setup: Any
) -> None:
    role = make_role()
    setup(
        "ALTER TABLE {s}.parts ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE {s}.parts FORCE ROW LEVEL SECURITY",
        f'ALTER TABLE {{s}}.parts OWNER TO "{role.name}"',
    )
    conn = _as(connect, role, schema)
    for scope in ("schema", "rls"):
        info = role_info(conn, owner_scope=scope)
        assert info.table_owner is True and not info.restricted
        assert info == _oracle_role_info(conn, scope)
        with pytest.raises(RoleUnsafe) as refused:
            check_role(conn, owner_scope=scope)
        assert refused.value.details["reasons"] == "table_owner"


def test_r06_owner_through_membership_and_partitions(
    connect: Callable[..., Any], make_role: Callable[..., Role], schema: str, setup: Any
) -> None:
    """``pg_has_role(..., 'MEMBER')``: a member of the owning role counts as owner; a partitioned table counts."""
    owner = make_role()
    member = make_role()
    setup(
        "CREATE TABLE {s}.events (id int, at date) PARTITION BY RANGE (at)",
        "ALTER TABLE {s}.events ENABLE ROW LEVEL SECURITY",
        f'ALTER TABLE {{s}}.events OWNER TO "{owner.name}"',
        f'GRANT "{owner.name}" TO "{member.name}"',
    )
    conn = _as(connect, member, schema)
    for scope in ("schema", "rls"):
        info = role_info(conn, owner_scope=scope)
        assert info.table_owner is True
        assert info == _oracle_role_info(conn, scope)


def test_r07_flags_and_reasons(
    connect: Callable[..., Any], make_role: Callable[..., Role], schema: str, setup: Any
) -> None:
    role = make_role("BYPASSRLS")
    setup(f'ALTER TABLE {{s}}.parts OWNER TO "{role.name}"')
    conn = _as(connect, role, schema)
    with pytest.raises(RoleUnsafe) as both:
        check_role(conn)
    assert both.value.details["reasons"] == "bypass_rls,table_owner"
    assert "reasons=bypass_rls,table_owner" in str(both.value)
    assert repr(both.value) == "RoleUnsafe('postgres.role_unsafe')"
    with pytest.raises(RoleUnsafe) as one:
        check_role(conn, allow_table_owner=True)
    assert one.value.details["reasons"] == "bypass_rls"
    assert check_role(conn, allow_superuser=True).table_owner is True  # allow_superuser implies table ownership
    with pytest.raises(TypeError):
        check_role(conn, allow_superuser="yes")  # type: ignore[arg-type]


def test_r08_scopes_follow_the_search_path(
    connect: Callable[..., Any], make_role: Callable[..., Role], admin: Any, schema: str, setup: Any
) -> None:
    """``schema`` looks at current_schema() only; ``rls`` at every schema of current_schemas(false)."""
    role = make_role()
    second = new_name()
    admin.execute(f"CREATE SCHEMA {second}")
    try:
        admin.execute(f"GRANT USAGE ON SCHEMA {second} TO PUBLIC")
        admin.execute(f"CREATE TABLE {second}.owned (id int)")
        admin.execute(f"ALTER TABLE {second}.owned ENABLE ROW LEVEL SECURITY")
        admin.execute(f'ALTER TABLE {second}.owned OWNER TO "{role.name}"')
        first_only = _as(connect, role, schema)
        both = _as(connect, role, schema, path=f"{schema},{second}")
        assert role_info(first_only).table_owner is False
        assert role_info(first_only, owner_scope="rls").table_owner is False
        assert role_info(both).table_owner is False  # not current_schema()
        assert role_info(both, owner_scope="rls").table_owner is True
        for conn in (first_only, both):
            for scope in ("schema", "rls"):
                assert role_info(conn, owner_scope=scope) == _oracle_role_info(conn, scope)
    finally:
        admin.execute(f"DROP SCHEMA {second} CASCADE")


def test_r08_invalid_owner_scope(connect: Callable[..., Any]) -> None:
    conn = connect()
    with pytest.raises(ValueError, match="owner_scope"):
        role_info(conn, owner_scope="tenant")  # type: ignore[arg-type]
    assert conn.info.transaction_status == IDLE  # validated before anything is sent


def test_r09_inspect_role_report(
    connect: Callable[..., Any], make_role: Callable[..., Role], admin_url: str, schema: str, setup: Any
) -> None:
    runtime = make_role()
    setup(
        "CREATE TABLE {s}.zeta (id int)",
        "CREATE TABLE {s}.alpha (id int)",
        f'ALTER TABLE {{s}}.zeta OWNER TO "{runtime.name}"',
        f'ALTER TABLE {{s}}.alpha OWNER TO "{runtime.name}"',
    )
    owner = connect(admin_url, options=f"-c search_path={schema}", row_factory=dict_row)
    report = inspect_role(owner, runtime.name)
    assert isinstance(report, RoleReport)
    assert report.role == runtime.name
    assert report.owner == owner.info.user
    assert report.schema == schema
    assert report.database == owner.info.dbname
    assert report.tables == 3
    assert report.owned_tables == ("alpha", "zeta")
    assert report.connect is True  # PUBLIC has CONNECT on the test database
    assert report.can_create is False
    assert (report.superuser, report.bypass_rls, report.restricted) == (False, False, False)
    assert owner.info.transaction_status == IDLE
    assert report.as_dict() == _nodestro_inspect_role(owner, runtime.name)
    axentra = _axentra_inspect_role(owner, runtime.name)
    assert {k: v for k, v in report.as_dict().items() if k in axentra} == axentra
    assert list(report.as_dict()) == [
        "role",
        "owner",
        "schema",
        "database",
        "tables",
        "superuser",
        "bypass_rls",
        "owned_tables",
        "connect",
        "can_create",
        "restricted",
    ]


def test_r09_role_missing(connect: Callable[..., Any]) -> None:
    conn = connect()
    with pytest.raises(RoleMissing) as missing:
        inspect_role(conn, "t_no_such_role_4711")
    assert missing.value.code == "postgres.role_missing"
    assert "t_no_such_role_4711" not in str(missing.value) + repr(missing.value)
    assert conn.info.transaction_status == IDLE  # the implicit transaction was rolled back after the error


def test_r10_schema_parameter_privileges_and_superuser(
    connect: Callable[..., Any],
    make_role: Callable[..., Role],
    admin: Any,
    scratch_database: str,
    schema: str,
    setup: Any,
) -> None:
    """``schema=`` (Axentra) instead of current_schema(), CREATE on the schema, no CONNECT, a superuser target."""
    runtime = make_role()
    setup(f'GRANT CREATE ON SCHEMA {{s}} TO "{runtime.name}"')
    conn = connect(autocommit=True)  # current_schema() is public, the report names the given schema
    report = inspect_role(conn, runtime.name, schema=schema)
    assert report.schema == schema and report.tables == 1 and report.can_create is True
    assert report.owned_tables == () and report.restricted
    axentra = _axentra_inspect_role(conn, runtime.name, schema)
    assert {k: v for k, v in report.as_dict().items() if k in axentra} == axentra

    with psycopg.connect(scratch_database, autocommit=True) as scratch:
        scratch.execute("REVOKE CONNECT ON DATABASE " + scratch.info.dbname + " FROM PUBLIC")
        assert inspect_role(scratch, runtime.name).connect is False
        superuser = inspect_role(scratch, scratch.info.user)
        assert superuser.superuser and not superuser.restricted
        nodestro = _nodestro_inspect_role(scratch, scratch.info.user)
        assert {**superuser.as_dict(), "bypass_rls": nodestro["bypass_rls"]} == nodestro  # DL-2, see R02
        assert superuser.bypass_rls is _any_bypassrls_role(scratch)


def test_r10_runtime_role_inspects_itself(
    connect: Callable[..., Any], make_role: Callable[..., Role], schema: str, setup: Any
) -> None:
    """A role without superuser may read its own report (no privileged catalog access needed)."""
    role = make_role()
    conn = _as(connect, role, schema)
    report = inspect_role(conn, role.name)
    assert report.owner == role.name and report.restricted and report.tables == 1
    assert report.as_dict() == _nodestro_inspect_role(conn, role.name)


def test_r10_no_current_schema(connect: Callable[..., Any]) -> None:
    conn = connect(options="-c search_path=t_does_not_exist")
    report = inspect_role(conn, conn.info.user)
    assert (report.schema, report.tables, report.owned_tables, report.can_create) == ("", 0, (), False)


def test_r06_noinherit_member_of_the_owner(
    connect: Callable[..., Any], make_role: Callable[..., Role], admin_url: str, schema: str, setup: Any
) -> None:
    """Review L-2: a NOINHERIT member does not use the owner's privileges, but may ``SET ROLE`` to it: an owner
    (``pg_has_role(..., 'MEMBER')``, not ``'USAGE'``)."""
    owner = make_role()
    member = make_role("NOINHERIT NOBYPASSRLS")
    setup(
        "ALTER TABLE {s}.parts ENABLE ROW LEVEL SECURITY",
        f'ALTER TABLE {{s}}.parts OWNER TO "{owner.name}"',
        f'GRANT "{owner.name}" TO "{member.name}"',
    )
    conn = _as(connect, member, schema)
    for scope in ("schema", "rls"):
        assert role_info(conn, owner_scope=scope).table_owner is True
        with pytest.raises(RoleUnsafe) as refused:
            check_role(conn, owner_scope=scope)
        assert refused.value.details["reasons"] == "table_owner"
    owner_conn = connect(admin_url, options=f"-c search_path={schema}")
    assert inspect_role(owner_conn, member.name).owned_tables == ("parts",)


# --- TAB-T-R11, R12: roles a session can become (contract I-20, review finding H-1) --------------------------------


@pytest.fixture
def group_role(admin: Any) -> Iterator[Callable[[str], str]]:
    """``group_role(attributes)``: a NOLOGIN role (``SUPERUSER``, ``BYPASSRLS``), dropped afterwards."""
    made: list[str] = []

    def factory(attributes: str) -> str:
        name = new_name()
        admin.execute(f'CREATE ROLE "{name}" NOLOGIN {attributes}')
        made.append(name)
        return name

    yield factory
    for name in made:
        admin.execute(f'DROP ROLE IF EXISTS "{name}"')


@pytest.mark.parametrize("inherit", ["INHERIT", "NOINHERIT"])
@pytest.mark.parametrize(("attribute", "reason"), [("SUPERUSER", "superuser"), ("NOSUPERUSER BYPASSRLS", "bypass_rls")])
def test_r11_member_of_a_privileged_role(
    connect: Callable[..., Any],
    make_role: Callable[..., Role],
    group_role: Callable[[str], str],
    admin_url: str,
    schema: str,
    setup: Any,
    attribute: str,
    reason: str,
    inherit: str,
) -> None:
    """A login role that may ``SET ROLE`` to a superuser or BYPASSRLS role is reported as one, in both scopes.

    The reference queries of Axentra and Nodestro miss this (they read the attributes of current_user only)."""
    privileged = group_role(attribute)
    member = make_role(f"{inherit} NOBYPASSRLS")
    setup(f'GRANT "{privileged}" TO "{member.name}"')
    conn = _as(connect, member, schema)
    for scope in ("schema", "rls"):
        info = role_info(conn, owner_scope=scope)
        assert info.role == member.name
        assert (info.superuser, info.bypass_rls) == (reason == "superuser", reason == "bypass_rls")
        assert not info.restricted
        oracle = _oracle_role_info(conn, scope)
        assert (oracle.superuser, oracle.bypass_rls) == (False, False)  # the gap of the references
        with pytest.raises(RoleUnsafe) as refused:
            check_role(conn, owner_scope=scope, allow_table_owner=True)
        assert refused.value.details["reasons"] == reason
        assert check_role(conn, allow_superuser=True, owner_scope=scope) == info
    # The membership is real: the role becomes the privileged one with SET ROLE.
    conn.execute(f'SET ROLE "{privileged}"')
    flags = conn.execute("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user").fetchone()
    assert flags == (reason == "superuser", reason == "bypass_rls")
    conn.execute("RESET ROLE")
    report = inspect_role(connect(admin_url, options=f"-c search_path={schema}"), member.name)
    assert (report.superuser, report.bypass_rls, report.restricted) == (
        reason == "superuser",
        reason == "bypass_rls",
        False,
    )


def test_r11_indirect_membership(
    connect: Callable[..., Any],
    make_role: Callable[..., Role],
    group_role: Callable[[str], str],
    schema: str,
    setup: Any,
) -> None:
    """Membership counts through an intermediate role."""
    privileged = group_role("BYPASSRLS")
    middle = group_role("NOBYPASSRLS")
    member = make_role()
    setup(f'GRANT "{privileged}" TO "{middle}"', f'GRANT "{middle}" TO "{member.name}"')
    conn = _as(connect, member, schema)
    assert role_info(conn).bypass_rls is True
    with pytest.raises(RoleUnsafe):
        check_role(conn)


def test_r12_superuser_session_after_set_role(
    connect: Callable[..., Any], make_role: Callable[..., Role], schema: str, setup: Any
) -> None:
    """A superuser login with ``SET ROLE app``: ``RESET ROLE`` gives the superuser back, so it is one."""
    runtime = make_role()
    conn = connect(autocommit=True, options=f"-c search_path={schema}")
    conn.execute(f'SET ROLE "{runtime.name}"')
    for scope in ("schema", "rls"):
        info = role_info(conn, owner_scope=scope)
        assert info.role == runtime.name and info.superuser and not info.restricted
        with pytest.raises(RoleUnsafe) as refused:
            check_role(conn, owner_scope=scope)
        assert str(refused.value.details["reasons"]).split(",")[0] == "superuser"
    # inspect_role judges the named role, not the session: the runtime role itself stays restricted.
    assert inspect_role(conn, runtime.name).restricted


def test_r12_login_role_that_can_become_bypassrls_after_set_role(
    connect: Callable[..., Any],
    make_role: Callable[..., Role],
    group_role: Callable[[str], str],
    schema: str,
    setup: Any,
) -> None:
    """SESSION_USER counts too: a login role that switched to a plain role may switch back and on to BYPASSRLS."""
    privileged = group_role("BYPASSRLS")
    plain = group_role("NOBYPASSRLS")
    login = make_role()
    setup(f'GRANT "{privileged}" TO "{login.name}"', f'GRANT "{plain}" TO "{login.name}"')
    conn = _as(connect, login, schema, autocommit=True)
    conn.execute(f'SET ROLE "{plain}"')
    info = role_info(conn)
    assert info.role == plain and info.bypass_rls is True and info.superuser is False
    with pytest.raises(RoleUnsafe) as refused:
        check_role(conn)
    assert refused.value.details["reasons"] == "bypass_rls"


def test_r11_plain_membership_stays_restricted(
    connect: Callable[..., Any],
    make_role: Callable[..., Role],
    group_role: Callable[[str], str],
    schema: str,
    setup: Any,
) -> None:
    """Control: membership of an unprivileged role changes nothing."""
    plain = group_role("NOBYPASSRLS")
    member = make_role()
    setup(f'GRANT "{plain}" TO "{member.name}"')
    conn = _as(connect, member, schema)
    for scope in ("schema", "rls"):
        assert role_info(conn, owner_scope=scope).restricted
        assert check_role(conn, owner_scope=scope).restricted
