"""TAB-XW-8 against a real PostgreSQL: role DDL with the attribute ban, also indirect.

Contract section 4.9, notes I-180..I-186; ADR 0015 section 5 and the conditions K-2, D-1, D-2 and N-3 of the security
review of #76 (the test names below are the ones the addendum and ADR 0015 section 9 name). The functions run as an
admin role **without** superuser that has only CREATEROLE (E-X6: a separate admin profile with minimal rights); the
superuser connection only builds the fixtures that need it (roles with banned attributes, memberships in them). The
matrix: a banned attribute (SUPERUSER, BYPASSRLS, CREATEROLE, REPLICATION) reached directly, through INHERIT, NOINHERIT,
SET-only and ADMIN-only memberships and chains of two; every predefined role; the escalation grants of K-2. Every
refusal leaves the catalog unchanged (a snapshot before and after); a password reaches the server only as a SCRAM
verifier. Every role and schema starts with the run's ``PREFIX``, so the leak check of ``conftest.py`` covers this
file.
"""

# The SQL of the fixtures is built from names this file generates (uuid hex) and fixed text, never from outside input.

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
import datetime
import logging
from pathlib import Path
import secrets
import sys
from types import TracebackType
from typing import Any

import pytest

from tabrion.errors import DatabaseError, PermissionDenied, RoleMissing, RoleUnsafe, TabrionError, UsageError
from tabrion.postgres import (
    RoleOptions,
    RolePrivileges,
    alter_role,
    create_role,
    drop_role,
    grant_membership,
    grant_privileges,
    revoke_membership,
    revoke_privileges,
    role_privileges,
)
import tabrion.postgres._roleddl as roleddl

from .conftest import PREFIX, new_name, with_params

psycopg = pytest.importorskip("psycopg")
from psycopg import pq, sql  # noqa: E402

pytestmark = pytest.mark.postgres

IDLE = pq.TransactionStatus.IDLE
CANARY = "SECRET-4711"
PACKAGE = str(Path(roleddl.__file__).resolve().parent.parent)
"""The ``tabrion`` package directory: the frames whose locals must not hold a password."""
BANNED = {"SUPERUSER": "superuser", "BYPASSRLS": "bypass_rls", "CREATEROLE": "createrole", "REPLICATION": "replication"}
# How a role reaches the dangerous one (PostgreSQL 16 grant options); N-3: the ADMIN option alone counts too.
EDGES = {
    "inherit": "WITH INHERIT TRUE, SET TRUE",
    "inherit_without_set": "WITH INHERIT TRUE, SET FALSE",
    "noinherit_set_only": "WITH INHERIT FALSE, SET TRUE",
    "admin_only": "WITH ADMIN TRUE, INHERIT FALSE, SET FALSE",
    "noinherit_role": "",  # the member role is NOINHERIT; the grant takes its defaults
}


@dataclass
class Env:
    su: Any
    """Superuser, autocommit: fixtures only."""
    adm: Any
    """The admin profile (CREATEROLE only), not in autocommit mode: the connection under test."""
    adm_setup: Any
    """The same role in autocommit mode, for fixtures the admin builds itself."""
    admin: str
    schema: str
    """A schema the admin owns, with table ``t`` and functions ``definer(integer)`` (SECURITY DEFINER) and
    ``plain(integer)``."""
    url: str
    names: list[str] = field(default_factory=list)

    def name(self, suffix: str = "") -> str:
        """A fresh role name of this run (dropped afterwards)."""
        value = new_name() + suffix
        self.names.append(value)
        return value

    def role(self, attributes: str = "", *, by_superuser: bool = False) -> str:
        """``CREATE ROLE`` (NOLOGIN); by the admin, which then holds the ADMIN option on it, or by the superuser."""
        value = self.name()
        conn = self.su if by_superuser else self.adm_setup
        conn.execute(sql.SQL("CREATE ROLE {} NOLOGIN {}").format(sql.Identifier(value), sql.SQL(attributes)))
        return value

    def su_sql(self, text: str, *names: str) -> None:
        self.su.execute(sql.SQL(text).format(*(sql.Identifier(name) for name in names)))

    def member_of(self, role: str, member: str) -> bool:
        return bool(
            self.su.execute(
                "SELECT pg_has_role(%s, %s, 'MEMBER') AND EXISTS (SELECT 1 FROM pg_auth_members m"
                " WHERE m.roleid = %s::regrole AND m.member = %s::regrole)",
                (member, role, quote(role), quote(member)),
            ).fetchone()[0]
        )


def quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


@pytest.fixture
def env(admin_url: str) -> Iterator[Env]:
    admin = new_name("adm_")
    password = secrets.token_hex(16)
    schema = new_name("s_")
    with psycopg.connect(admin_url, autocommit=True) as su:
        su.execute(
            sql.SQL("CREATE ROLE {} LOGIN CREATEROLE PASSWORD {}").format(sql.Identifier(admin), sql.Literal(password))
        )
        su.execute(sql.SQL("CREATE SCHEMA {} AUTHORIZATION {}").format(sql.Identifier(schema), sql.Identifier(admin)))
        url = with_params(admin_url, user=admin, password=password)
        environment: Env | None = None
        try:
            with psycopg.connect(url) as adm, psycopg.connect(url, autocommit=True) as adm_setup:
                adm_setup.execute(
                    sql.SQL(
                        "CREATE TABLE {s}.t (id int); CREATE SEQUENCE {s}.counter;"
                        " CREATE FUNCTION {s}.definer(integer) RETURNS int LANGUAGE sql SECURITY DEFINER AS 'SELECT 1';"
                        " CREATE FUNCTION {s}.plain(integer) RETURNS int LANGUAGE sql AS 'SELECT 1'"
                    ).format(s=sql.Identifier(schema))
                )
                environment = Env(su, adm, adm_setup, admin, schema, url)
                yield environment
        finally:
            su.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
            for name in [*(environment.names if environment else []), admin][::-1]:
                exists = su.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (name,)).fetchone()
                if exists:
                    su.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(name)))
                    su.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(name)))


def snapshot(env: Env) -> list[str]:
    """Memberships, attributes, passwords and ACLs of everything of this run (and of ``public``, the database)."""
    rows = env.su.execute(
        "SELECT 'member ' || row(r.rolname, m.rolname, g.rolname, a.admin_option, a.inherit_option, a.set_option)::text"
        " FROM pg_auth_members a JOIN pg_roles r ON r.oid = a.roleid JOIN pg_roles m ON m.oid = a.member"
        " JOIN pg_roles g ON g.oid = a.grantor"
        " WHERE starts_with(r.rolname, %(p)s) OR starts_with(m.rolname, %(p)s)"
        " UNION ALL SELECT 'role ' || row(rolname, rolsuper, rolinherit, rolcreaterole, rolcreatedb, rolcanlogin,"
        "   rolreplication, rolbypassrls, rolconnlimit, rolvaliduntil::text, rolpassword)::text"
        " FROM pg_authid WHERE starts_with(rolname, %(p)s)"
        " UNION ALL SELECT 'rel ' || c.oid::regclass::text || ' ' || coalesce(c.relacl::text, '')"
        " FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE starts_with(n.nspname, %(p)s)"
        " UNION ALL SELECT 'schema ' || nspname || ' ' || coalesce(nspacl::text, '') FROM pg_namespace"
        " WHERE starts_with(nspname, %(p)s) OR nspname = 'public'"
        " UNION ALL SELECT 'function ' || p.oid::regprocedure::text || ' ' || coalesce(p.proacl::text, '')"
        " FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE starts_with(n.nspname, %(p)s)"
        " UNION ALL SELECT 'database ' || datname || ' ' || coalesce(datacl::text, '') FROM pg_database"
        " WHERE datname = current_database()"
        " UNION ALL SELECT 'default ' || coalesce(defaclacl::text, '') FROM pg_default_acl",
        {"p": PREFIX},
    ).fetchall()
    return sorted(str(row[0]) for row in rows)


def refused(env: Env, call: Callable[[], object], *reasons: str) -> RoleUnsafe:
    """``call`` raises ``RoleUnsafe`` naming every one of ``reasons``, and nothing changed."""
    before = snapshot(env)
    with pytest.raises(RoleUnsafe) as caught:
        call()
    assert caught.value.code == "postgres.role_unsafe"
    named = str(caught.value.details["reasons"]).split(",")
    for reason in reasons:
        assert reason in named, (reason, named)
    assert snapshot(env) == before
    assert env.adm.info.transaction_status == IDLE
    return caught.value


def caller_refused(env: Env, call: Callable[[], object], *reasons: str) -> RoleUnsafe:
    """``call`` raises ``RoleUnsafe("postgres.caller_unsafe")`` naming each of ``reasons`` and nothing but the three
    caller attributes, and nothing changed (a bootstrap superuser also has BYPASSRLS and REPLICATION)."""
    before = snapshot(env)
    with pytest.raises(RoleUnsafe) as caught:
        call()
    assert caught.value.code == "postgres.caller_unsafe"
    named = str(caught.value.details["reasons"]).split(",")
    assert set(reasons) <= set(named) <= {"superuser", "bypass_rls", "replication"}, named
    assert snapshot(env) == before
    return caught.value


# ---------------------------------------------------------------------------------------------------------------------
# D-1: PostgreSQL 16 and later
# ---------------------------------------------------------------------------------------------------------------------


def test_role_ddl_refused_before_16(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    target = env.role()
    other = env.role()
    monkeypatch.setattr(roleddl, "_ROLE_DDL_VERSION", env.adm.info.server_version + 1)
    before = snapshot(env)
    calls: list[Callable[[], object]] = [
        lambda: create_role(env.adm, env.name(), password=CANARY),
        lambda: alter_role(env.adm, target, options=RoleOptions(login=True)),
        lambda: drop_role(env.adm, target),
        lambda: grant_membership(env.adm, other, member=target),
        lambda: revoke_membership(env.adm, other, member=target),
        lambda: grant_privileges(env.adm, target, privileges=["SELECT"], on="table", objects=[f"{env.schema}.t"]),
        lambda: revoke_privileges(env.adm, target, privileges=["SELECT"], on="table", objects=[f"{env.schema}.t"]),
    ]
    for call in calls:
        with pytest.raises(UsageError) as caught:
            call()
        assert caught.value.code == "postgres.role_ddl_requires_16"
    assert snapshot(env) == before
    # role_privileges stays read-only on every version.
    assert role_privileges(env.adm, target).role == target


def test_createrole_pre16_reported_dangerous(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    creator = env.role("CREATEROLE", by_superuser=True)
    member = env.role()
    env.su_sql("GRANT {} TO {} WITH INHERIT FALSE, SET FALSE, ADMIN TRUE", creator, member)
    now = role_privileges(env.adm, member)
    assert f"createrole:{creator}" in now.dangerous and "createrole_pre16" not in now.dangerous
    monkeypatch.setattr(roleddl, "_ROLE_DDL_VERSION", env.adm.info.server_version + 1)
    before16 = role_privileges(env.adm, member)
    assert "createrole_pre16" in before16.dangerous and not before16.safe
    assert "createrole_pre16" in role_privileges(env.adm, creator).dangerous
    assert "createrole_pre16" not in role_privileges(env.adm, env.role()).dangerous


# ---------------------------------------------------------------------------------------------------------------------
# E-X6: the attribute ban, also indirect (N-3)
# ---------------------------------------------------------------------------------------------------------------------


def _reach(env: Env, attribute: str, edge: str, depth: int) -> tuple[str, str]:
    """A role with ``attribute`` and an admin-made role that reaches it through ``edge`` at ``depth``."""
    dangerous = env.role(attribute, by_superuser=True)
    chain = [env.role("NOINHERIT" if edge == "noinherit_role" else "") for _ in range(depth)]
    env.su_sql("GRANT {} TO {} " + EDGES[edge], dangerous, chain[-1])
    for upper, lower in zip(chain[1:], chain[:-1], strict=False):
        env.su_sql("GRANT {} TO {}", upper, lower)
    return dangerous, chain[0]


@pytest.mark.parametrize("depth", [1, 2])
@pytest.mark.parametrize("edge", sorted(EDGES))
@pytest.mark.parametrize("attribute", sorted(BANNED))
def test_reachable_banned_attribute_refused(env: Env, attribute: str, edge: str, depth: int) -> None:
    dangerous, top = _reach(env, attribute, edge, depth)
    reason = BANNED[attribute]
    found = role_privileges(env.adm, top)
    assert dangerous in found.reachable and f"{reason}:{dangerous}" in found.dangerous and not found.safe
    member = env.role()
    refused(env, lambda: grant_membership(env.adm, top, member=member), f"reachable_{reason}")
    assert not env.member_of(top, member)
    refused(env, lambda: alter_role(env.adm, top, options=RoleOptions(login=True)), f"reachable_{reason}")
    refused(env, lambda: alter_role(env.adm, top, password=CANARY), f"reachable_{reason}")
    refused(
        env,
        lambda: grant_privileges(env.adm, top, privileges=["SELECT"], on="table", objects=[f"{env.schema}.t"]),
        f"reachable_{reason}",
    )


@pytest.mark.parametrize("attribute", sorted(BANNED))
def test_own_banned_attribute_refused(env: Env, attribute: str) -> None:
    dangerous = env.role(attribute, by_superuser=True)
    reason = BANNED[attribute]
    found = role_privileges(env.adm, dangerous)
    assert reason in found.attributes and found.dangerous == (f"{reason}:{dangerous}",)
    member = env.role()
    refused(env, lambda: grant_membership(env.adm, dangerous, member=member), f"reachable_{reason}")
    refused(env, lambda: alter_role(env.adm, dangerous, options=RoleOptions()), reason)
    refused(
        env,
        lambda: grant_privileges(env.adm, dangerous, privileges=["USAGE"], on="schema", objects=[env.schema]),
        reason,
    )
    # The member side too: a role with a banned attribute gets no new membership from Tabrion.
    refused(env, lambda: grant_membership(env.adm, member, member=dangerous), reason)


def test_admin_option_on_dangerous_role_counts_as_reachable(env: Env) -> None:
    """N-3: with only the ADMIN option a role can grant the membership to itself, so it counts as reachable."""
    superuser = env.role("SUPERUSER", by_superuser=True)
    holder = env.role()
    env.su_sql("GRANT {} TO {} WITH ADMIN TRUE, INHERIT FALSE, SET FALSE", superuser, holder)
    found = role_privileges(env.adm, holder)
    assert found.member_of == (superuser,) and found.reachable == (superuser,)
    assert found.dangerous == (f"superuser:{superuser}",)
    member = env.role()
    refused(env, lambda: grant_membership(env.adm, holder, member=member), "reachable_superuser")
    assert not env.member_of(holder, member)


def test_no_parameter_requests_a_banned_attribute(env: Env) -> None:
    import inspect

    words = ("super", "bypass", "createrole", "create_role", "replication", "admin", "grant_option")
    for function in (create_role, alter_role, grant_membership, grant_privileges):
        for parameter in inspect.signature(function).parameters:
            assert not any(word in parameter.lower() for word in words), (function.__name__, parameter)
    assert {f.name for f in RoleOptions.__dataclass_fields__.values()} == {
        "login",
        "inherit",
        "create_db",
        "connection_limit",
        "valid_until",
    }
    # A superuser connection creates nothing (P90-01); the admin creates a role without them, with no membership.
    name = env.name()
    caller_refused(env, lambda: create_role(env.su, name, options=RoleOptions(login=True)), "superuser")
    created = create_role(env.adm, name, options=RoleOptions(login=True))
    assert created.attributes == ("login", "inherit") and created.safe and created.member_of == ()
    row = env.su.execute(
        "SELECT rolsuper, rolbypassrls, rolcreaterole, rolreplication FROM pg_roles WHERE rolname = %s", (name,)
    ).fetchone()
    assert row == (False, False, False, False)


def test_membership_has_no_admin_option_and_round_trips(env: Env) -> None:
    group = env.role()
    member = env.role()
    granted = grant_membership(env.adm, group, member=member)
    assert granted.role == member and granted.member_of == (group,) and granted.reachable == (group,)
    options = env.su.execute(
        "SELECT admin_option FROM pg_auth_members WHERE roleid = %s::regrole AND member = %s::regrole",
        (quote(group), quote(member)),
    ).fetchall()
    assert options == [(False,)]
    assert env.adm.info.transaction_status == IDLE and not env.adm.autocommit
    revoked = revoke_membership(env.adm, group, member=member)
    assert revoked.member_of == () and not env.member_of(group, member)


# ---------------------------------------------------------------------------------------------------------------------
# D-2: no predefined roles
# ---------------------------------------------------------------------------------------------------------------------


def test_membership_in_any_predefined_role_refused(env: Env) -> None:
    predefined = [
        str(row[0])
        for row in env.su.execute(
            "SELECT rolname FROM pg_roles WHERE starts_with(rolname, 'pg_') AND oid < 16384 ORDER BY rolname"
        ).fetchall()
    ]
    assert {"pg_read_all_data", "pg_write_all_data", "pg_monitor", "pg_signal_backend"} <= set(predefined)
    member = env.role()
    for role in predefined:
        refused(env, lambda role=role: grant_membership(env.adm, role, member=member), "predefined_role")  # type: ignore[misc]
        refused(env, lambda role=role: grant_membership(env.adm, member, member=role), "predefined_role")  # type: ignore[misc]
    # Indirect: a group that is a member of pg_write_all_data (NOINHERIT and SET-only too).
    group = env.role()
    env.su_sql("GRANT pg_write_all_data TO {} WITH INHERIT FALSE, SET TRUE", group)
    refused(env, lambda: grant_membership(env.adm, group, member=member), "predefined_role")
    assert not env.member_of(group, member)


def test_existing_predefined_membership_reported_broad(env: Env) -> None:
    member = env.role()
    group = env.role()
    env.su_sql("GRANT pg_read_all_data TO {}", member)
    env.su_sql("GRANT pg_monitor TO {} WITH INHERIT FALSE, SET FALSE, ADMIN TRUE", group)
    env.su_sql("GRANT {} TO {}", group, member)
    found = role_privileges(env.adm, member)
    assert "broad:pg_read_all_data" in found.broad and "broad:pg_monitor" in found.broad
    assert found.safe  # broad is a finding of its own, not dangerous
    assert found.as_dict()["broad"] == list(found.broad)
    # Revoking such a membership is the way to remove the finding; the superuser granted it, and a superuser session
    # changes nothing through Tabrion (P90-01), so the superuser revokes it in SQL.
    caller_refused(env, lambda: revoke_membership(env.su, "pg_read_all_data", member=member), "superuser")
    env.su_sql("REVOKE pg_read_all_data FROM {}", member)
    assert "broad:pg_read_all_data" not in role_privileges(env.adm, member).broad
    # The database owner reaches pg_database_owner implicitly.
    owner = env.su.execute("SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = current_database()")
    assert "broad:pg_database_owner" in role_privileges(env.adm, str(owner.fetchone()[0])).broad


# ---------------------------------------------------------------------------------------------------------------------
# K-2: escalation without attributes
# ---------------------------------------------------------------------------------------------------------------------


def test_trigger_privilege_not_grantable(env: Env) -> None:
    target = env.role()
    before = snapshot(env)
    attempts: list[Any] = [["TRIGGER"], ["SELECT", "TRIGGER"], ["ALL"], ["ALL PRIVILEGES"], ["trigger"], ["MAINTAIN"]]
    for privileges in attempts:
        with pytest.raises(UsageError) as caught:
            grant_privileges(env.adm, target, privileges=privileges, on="table", objects=[f"{env.schema}.t"])
        assert caught.value.code == "postgres.invalid_argument"
        assert dict(caught.value.details) == {"parameter": "privileges"}
    assert snapshot(env) == before
    assert not hasattr(roleddl, "TRIGGER")
    assert all("TRIGGER" not in allowed for allowed in roleddl._GRANTABLE.values())


def test_execute_on_foreign_security_definer_refused(env: Env) -> None:
    target = env.role()
    definer = f"{env.schema}.definer(integer)"
    refused(
        env,
        lambda: grant_privileges(env.adm, target, privileges=["EXECUTE"], on="function", objects=[definer]),
        "security_definer",
    )
    # A plain function is fine, and so is a SECURITY DEFINER function the grantee owns itself.
    plain = grant_privileges(
        env.adm, target, privileges=["EXECUTE"], on="function", objects=[f"{env.schema}.plain(int4)"]
    )
    assert plain.safe
    env.su_sql("ALTER FUNCTION {}.definer(integer) OWNER TO {}", env.schema, target)
    env.su_sql("GRANT EXECUTE ON FUNCTION {}.definer(integer) TO {} WITH GRANT OPTION", env.schema, env.admin)
    own = grant_privileges(env.adm, target, privileges=["EXECUTE"], on="function", objects=[definer])
    assert own.safe


def test_create_on_public_or_foreign_schema_refused(env: Env) -> None:
    target = env.role()
    for schema in ("public", env.schema):
        refused(
            env,
            lambda schema=schema: grant_privileges(  # type: ignore[misc]
                env.adm, target, privileges=["CREATE"], on="schema", objects=[schema]
            ),
            "schema_create",
        )
    assert grant_privileges(env.adm, target, privileges=["USAGE"], on="schema", objects=[env.schema]).safe
    own = env.name("_s")
    env.su_sql("CREATE SCHEMA {} AUTHORIZATION {}", own, target)
    env.su_sql("GRANT CREATE ON SCHEMA {} TO {} WITH GRANT OPTION", own, env.admin)
    try:
        assert grant_privileges(env.adm, target, privileges=["CREATE"], on="schema", objects=[own]).safe
    finally:
        env.su_sql("DROP SCHEMA {}", own)


def test_role_privileges_reports_escalation_grants(env: Env) -> None:
    target = env.role()
    group = env.role()
    env.su_sql("GRANT {} TO {} WITH INHERIT FALSE, SET TRUE", group, target)
    env.su_sql("GRANT TRIGGER ON {}.t TO {}", env.schema, target)
    env.su_sql("GRANT EXECUTE ON FUNCTION {}.definer(integer) TO {}", env.schema, group)
    env.su_sql("GRANT CREATE ON SCHEMA {} TO {}", env.schema, group)
    found = role_privileges(env.adm, target)
    assert {
        f"trigger:{env.schema}.t",
        f"security_definer:{env.schema}.definer(integer)",
        f"schema_create:{env.schema}",
    } <= set(found.dangerous)
    assert not found.safe and found.as_dict()["safe"] is False
    # Such a group is not granted to anyone else (the member would gain the grants).
    member = env.role()
    refused(env, lambda: grant_membership(env.adm, group, member=member), "security_definer", "schema_create")
    # Revoking TRIGGER removes that finding.
    after = revoke_privileges(env.adm, target, privileges=["TRIGGER"], on="table", objects=[f"{env.schema}.t"])
    assert f"trigger:{env.schema}.t" not in after.dangerous
    # CREATE on public for PUBLIC is a finding of every role.
    env.su.execute("GRANT CREATE ON SCHEMA public TO PUBLIC")
    try:
        assert "schema_create:public" in role_privileges(env.adm, member).dangerous
    finally:
        env.su.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    assert "schema_create:public" not in role_privileges(env.adm, member).dangerous


# ---------------------------------------------------------------------------------------------------------------------
# create, alter, drop
# ---------------------------------------------------------------------------------------------------------------------


def test_create_and_alter_options_round_trip(env: Env) -> None:
    name = env.name()
    until = datetime.datetime(2031, 5, 4, 3, 2, 1, tzinfo=datetime.timezone(datetime.timedelta(hours=2)))
    created = create_role(
        env.adm, name, options=RoleOptions(login=True, inherit=False, connection_limit=5, valid_until=until)
    )
    assert created.attributes == ("login",) and created.safe and created.owned_objects == 0
    row = env.su.execute(
        "SELECT rolcanlogin, rolinherit, rolcreatedb, rolconnlimit, rolvaliduntil FROM pg_roles WHERE rolname = %s",
        (name,),
    ).fetchone()
    assert row == (True, False, False, 5, until)
    # Back to the defaults; the admin has no CREATEDB, and NOCREATEDB is not sent when nothing changes (PostgreSQL 16
    # refuses even NOCREATEDB from such a role).
    altered = alter_role(env.adm, name, options=RoleOptions())
    assert altered.attributes == ("inherit",)
    row = env.su.execute(
        "SELECT rolcanlogin, rolinherit, rolconnlimit, rolvaliduntil::text FROM pg_roles WHERE rolname = %s", (name,)
    ).fetchone()
    assert row == (False, True, -1, "infinity")
    # Unchanged options send nothing.
    before = snapshot(env)
    alter_role(env.adm, name, options=RoleOptions())
    assert snapshot(env) == before
    assert env.adm.info.transaction_status == IDLE


def test_create_db_needs_the_admins_createdb_and_errors_are_safe(env: Env) -> None:
    """A driver error becomes the DatabaseError of its kind with the safe message, never the server's text."""
    name = env.name()
    before = snapshot(env)
    with pytest.raises(PermissionDenied) as caught:
        create_role(env.adm, name, options=RoleOptions(create_db=True), password=CANARY)
    error = caught.value
    assert isinstance(error, DatabaseError) and error.code == "database.permission"
    assert error.classification is not None and error.classification.sqlstate == "42501"
    assert error.__cause__ is None and error.__context__ is None
    assert "CREATEDB" not in str(error) and name not in str(error)
    assert snapshot(env) == before
    assert env.adm.info.transaction_status == IDLE


def test_existing_role_refused(env: Env) -> None:
    name = env.role()
    with pytest.raises(UsageError) as caught:
        create_role(env.adm, name)
    assert caught.value.code == "postgres.role_exists"


def test_alter_and_drop_unknown_role(env: Env) -> None:
    for call in (lambda: alter_role(env.adm, env.name(), password=CANARY), lambda: drop_role(env.adm, env.name())):
        with pytest.raises(RoleMissing) as caught:
            call()
        assert dict(caught.value.details) == {"parameter": "role"}
    with pytest.raises(RoleMissing) as missing:
        grant_membership(env.adm, env.role(), member=env.name())
    assert dict(missing.value.details) == {"parameter": "member"}


def test_drop_role_refusals(env: Env) -> None:
    owner = env.role()
    env.su_sql("CREATE TABLE {}.owned (id int)", env.schema)
    env.su_sql("ALTER TABLE {}.owned OWNER TO {}", env.schema, owner)
    assert role_privileges(env.adm, owner).owned_objects == 1
    refused(env, lambda: drop_role(env.adm, owner), "owns_objects")
    defaults = env.role()
    env.su_sql("ALTER DEFAULT PRIVILEGES FOR ROLE {} GRANT SELECT ON TABLES TO PUBLIC", defaults)
    assert role_privileges(env.adm, defaults).default_privileges == 1
    refused(env, lambda: drop_role(env.adm, defaults), "owns_objects")
    grantee = env.role()
    env.su_sql(
        "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA {} GRANT SELECT ON TABLES TO {}", env.admin, env.schema, grantee
    )
    refused(env, lambda: drop_role(env.adm, grantee), "owns_objects")
    refused(env, lambda: drop_role(env.adm, env.admin), "current_user", "owns_objects")
    refused(env, lambda: drop_role(env.adm, "pg_monitor"), "predefined_role")


def test_drop_role_round_trip_and_held_privilege(env: Env) -> None:
    gone = env.role()
    drop_role(env.adm, gone)
    assert env.su.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (gone,)).fetchone() is None
    holder = env.role()
    grant_privileges(env.adm, holder, privileges=["SELECT"], on="table", objects=[f"{env.schema}.t"])
    before = snapshot(env)
    with pytest.raises(DatabaseError) as caught:
        drop_role(env.adm, holder)  # PostgreSQL refuses: privileges depend on it (no DROP OWNED by Tabrion)
    assert caught.value.code.startswith("database.") and env.schema not in str(caught.value)
    assert snapshot(env) == before
    revoke_privileges(env.adm, holder, privileges=["SELECT"], on="table", objects=[f"{env.schema}.t"])
    drop_role(env.adm, holder)


def test_session_role_never_changed(env: Env) -> None:
    other = env.role()
    refused(env, lambda: alter_role(env.adm, env.admin, password=CANARY), "current_user")
    refused(env, lambda: grant_membership(env.adm, other, member=env.admin), "current_user")
    refused(env, lambda: grant_membership(env.adm, env.admin, member=other), "current_user")
    refused(env, lambda: revoke_membership(env.adm, other, member=env.admin), "current_user")
    refused(
        env,
        lambda: grant_privileges(env.adm, env.admin, privileges=["USAGE"], on="schema", objects=[env.schema]),
        "current_user",
    )


# ---------------------------------------------------------------------------------------------------------------------
# Privileges
# ---------------------------------------------------------------------------------------------------------------------


def _acl(env: Env, kind: str, name: str) -> str:
    query = {
        "table": "SELECT coalesce(relacl::text, '') FROM pg_class WHERE oid = %s::regclass",
        "function": "SELECT coalesce(proacl::text, '') FROM pg_proc WHERE oid = %s::regprocedure",
        "schema": "SELECT coalesce(nspacl::text, '') FROM pg_namespace WHERE nspname = %s",
        "database": "SELECT coalesce(datacl::text, '') FROM pg_database WHERE datname = %s",
    }[kind]
    return str(env.su.execute(query, (name,)).fetchone()[0])


def test_grant_and_revoke_each_object_kind(env: Env) -> None:
    target = env.role()
    cases: list[tuple[Any, Any, str, str]] = [
        ("table", ["SELECT", "INSERT", "UPDATE"], f"{env.schema}.t", "arw"),
        ("sequence", ["USAGE", "SELECT"], f"{env.schema}.counter", "rU"),
        ("function", ["EXECUTE"], f"{env.schema}.plain(integer)", "X"),
        ("schema", ["USAGE"], env.schema, "U"),
    ]
    for on, privileges, name, letters in cases:
        result = grant_privileges(env.adm, target, privileges=privileges, on=on, objects=[name])
        assert result.safe and result.role == target
        acl = _acl(env, "table" if on == "sequence" else on, name)
        assert f"{target}={letters}/{env.admin}" in acl, (on, acl)
        revoke_privileges(env.adm, target, privileges=privileges, on=on, objects=[name])
        assert target not in _acl(env, "table" if on == "sequence" else on, name)
    # A database the admin owns (the superuser's own databases are refused, P90-01: object_owner).
    database = env.name("_db")
    env.su_sql("CREATE DATABASE {} OWNER {}", database, env.admin)
    try:
        grant_privileges(env.adm, target, privileges=["CONNECT"], on="database", objects=[database])
        assert f"{target}=c/{env.admin}" in _acl(env, "database", database)
        revoke_privileges(env.adm, target, privileges=["CONNECT"], on="database", objects=[database])
        assert target not in _acl(env, "database", database)
    finally:
        env.su_sql("DROP DATABASE {}", database)


def test_grant_without_the_right_to_grant_is_refused(env: Env) -> None:
    """PostgreSQL only warns ("no privileges were granted"); Tabrion raises PermissionDenied and rolls back."""
    target = env.role()
    owner = env.role(by_superuser=True)
    env.su_sql("CREATE TABLE {}.foreign_t (id int)", env.schema)
    env.su_sql("ALTER TABLE {}.foreign_t OWNER TO {}", env.schema, owner)
    before = snapshot(env)
    # Without any privilege PostgreSQL itself refuses (42501), raised as the DatabaseError of its kind.
    with pytest.raises(PermissionDenied) as denied:
        grant_privileges(env.adm, target, privileges=["SELECT"], on="table", objects=[f"{env.schema}.foreign_t"])
    assert denied.value.code == "database.permission"
    # With the privilege but without its grant option PostgreSQL only warns.
    env.su_sql("GRANT SELECT ON {}.foreign_t TO {}", env.schema, env.admin)
    before = snapshot(env)
    with pytest.raises(PermissionDenied) as caught:
        grant_privileges(env.adm, target, privileges=["SELECT"], on="table", objects=[f"{env.schema}.foreign_t"])
    assert caught.value.code == "postgres.grant_not_allowed"
    assert caught.value.classification is not None and caught.value.classification.sqlstate == "42501"
    assert snapshot(env) == before


PRIVILEGE: dict[str, Any] = {
    "table": "SELECT",
    "sequence": "USAGE",
    "function": "EXECUTE",
    "schema": "USAGE",
    "database": "CONNECT",
}


def test_missing_and_mismatched_objects(env: Env) -> None:
    target = env.role()
    cases: list[tuple[Any, str]] = [
        ("table", f"{env.schema}.nope"),
        ("table", f"{env.schema}.counter"),  # a sequence is not a table here
        ("sequence", f"{env.schema}.t"),
        ("function", f"{env.schema}.plain(text)"),
        ("function", f"{env.schema}.plain(no_such_type)"),
        ("function", f"{env.schema}.plain(integer integer)"),
        ("schema", new_name()),
        ("database", new_name()),
    ]
    before = snapshot(env)
    for on, name in cases:
        with pytest.raises(UsageError) as caught:
            grant_privileges(
                env.adm,
                target,
                privileges=[PRIVILEGE[on]],
                on=on,
                objects=[f"{env.schema}.t", name] if on == "table" else [name],
            )
        assert caught.value.code == "postgres.object_missing", (on, name)
        assert caught.value.details["index"] == (1 if on == "table" else 0)
    assert snapshot(env) == before
    with pytest.raises(UsageError) as unqualified:
        grant_privileges(env.adm, target, privileges=["SELECT"], on="table", objects=["t"])
    assert unqualified.value.code == "postgres.invalid_argument"


def test_names_are_quoted_never_formatted(env: Env) -> None:
    """Injection-safe: names with quotes, semicolons, dots and spaces are identifiers, nothing else."""
    victim = env.role()
    hostile = env.name(f'"; DROP ROLE "{victim}"; --')
    assert len(hostile.encode()) <= 63
    created = create_role(env.adm, hostile)
    assert created.role == hostile
    count = env.su.execute("SELECT count(*) FROM pg_roles WHERE rolname IN (%s, %s)", (hostile, victim)).fetchone()
    assert count == (2,)
    table = 'we"ird.na me'
    env.adm_setup.execute(sql.SQL("CREATE TABLE {} (id int)").format(sql.Identifier(env.schema, table)))
    quoted = f'{env.schema}."we""ird.na me"'
    grant_privileges(env.adm, hostile, privileges=["SELECT"], on="table", objects=[quoted])
    acl = str(
        env.su.execute(
            "SELECT relacl::text FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE nspname = %s AND relname = %s",
            (env.schema, table),
        ).fetchone()[0]
    )
    assert "=r/" in acl
    group = env.role()
    grant_membership(env.adm, group, member=hostile)
    assert env.member_of(group, hostile)
    # An unquoted name is folded to lower case, as PostgreSQL does.
    upper = f"{env.schema.upper()}.T"
    grant_privileges(env.adm, hostile, privileges=["SELECT"], on="table", objects=[upper])
    revoke_membership(env.adm, group, member=hostile)
    revoke_privileges(env.adm, hostile, privileges=["SELECT"], on="table", objects=[quoted, f"{env.schema}.t"])
    drop_role(env.adm, hostile)
    assert env.su.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (victim,)).fetchone() == (1,)


# ---------------------------------------------------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------------------------------------------------


def _values(tb: TracebackType | None, *, every: bool = False) -> list[str]:
    texts: list[str] = []
    while tb is not None:
        if not every and not tb.tb_frame.f_code.co_filename.startswith(PACKAGE):  # the test's own frames
            tb = tb.tb_next
            continue
        for value in tb.tb_frame.f_locals.values():
            try:
                texts.append(repr(value))
            except Exception:  # noqa: S112 - a repr that fails holds nothing to show
                continue
            if isinstance(value, bytes | str):
                texts.append(value if isinstance(value, str) else value.decode("latin-1"))
        tb = tb.tb_next
    return texts


def _assert_no_secret(error: BaseException) -> None:
    texts = [str(error), repr(error), *(str(v) for v in getattr(error, "details", {}).values())]
    texts += _values(error.__traceback__)
    original = getattr(error, "original", None)
    if isinstance(original, BaseException):
        texts += [str(original), repr(original), *_values(original.__traceback__, every=True)]
    for text in texts:
        assert CANARY not in text
        assert "SCRAM-SHA-256$" not in text


@pytest.mark.skipif(sys.platform != "linux", reason="libpq tracing through psycopg is Linux only")
def test_password_sent_only_as_scram_verifier(env: Env, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    name = env.name()
    trace = tmp_path / "wire.trace"
    caplog.set_level(logging.DEBUG)
    with trace.open("w") as sink:
        env.adm.pgconn.trace(sink.fileno())
        try:
            create_role(env.adm, name, options=RoleOptions(login=True), password=CANARY)
            alter_role(env.adm, name, password=CANARY + "-2")
        finally:
            env.adm.pgconn.untrace()
    wire = trace.read_text(encoding="utf-8", errors="replace")
    assert CANARY not in wire and "SCRAM-SHA-256$" in wire
    assert CANARY not in caplog.text
    stored = env.su.execute("SELECT rolpassword FROM pg_authid WHERE rolname = %s", (name,)).fetchone()[0]
    assert str(stored).startswith("SCRAM-SHA-256$4096:")
    with psycopg.connect(with_params(env.url, user=name, password=CANARY + "-2")) as conn:
        assert conn.execute("SELECT current_user").fetchone() == (name,)


def test_password_never_in_errors_or_frames(env: Env) -> None:
    with pytest.raises(PermissionDenied) as denied:
        create_role(env.adm, env.name(), options=RoleOptions(create_db=True), password=CANARY)
    _assert_no_secret(denied.value)
    superuser = env.role("SUPERUSER", by_superuser=True)
    with pytest.raises(RoleUnsafe) as unsafe:
        alter_role(env.adm, superuser, password=CANARY)
    _assert_no_secret(unsafe.value)
    with pytest.raises(UsageError) as exists:
        create_role(env.adm, superuser, password=CANARY)
    _assert_no_secret(exists.value)
    for bad in ("", "a\x00b"):
        with pytest.raises(UsageError) as invalid:
            create_role(env.adm, env.name(), password=bad)
        assert dict(invalid.value.details) == {"parameter": "password"}
        _assert_no_secret(invalid.value)


# ---------------------------------------------------------------------------------------------------------------------
# T-NOTX
# ---------------------------------------------------------------------------------------------------------------------


def test_nested_transaction_refused_and_connection_left_as_it_was(env: Env) -> None:
    target = env.role()
    with env.adm.transaction():
        with pytest.raises(UsageError) as caught:
            alter_role(env.adm, target, options=RoleOptions(login=True))
        assert caught.value.code == "postgres.nested_transaction"
        # role_privileges reads in any transaction state.
        assert role_privileges(env.adm, target).role == target
    assert env.adm.info.transaction_status == IDLE
    # Autocommit connections work too and stay in autocommit mode.
    result = alter_role(env.adm_setup, target, options=RoleOptions(login=True))
    assert "login" in result.attributes and env.adm_setup.autocommit
    assert env.adm_setup.info.transaction_status == IDLE


def test_role_privileges_of_a_missing_role(env: Env) -> None:
    with pytest.raises(RoleMissing):
        role_privileges(env.adm, env.name())
    assert env.adm.info.transaction_status == IDLE


def test_result_is_a_role_privileges(env: Env) -> None:
    found = role_privileges(env.adm, env.admin)
    assert isinstance(found, RolePrivileges)
    assert "createrole" in found.attributes and found.dangerous == (f"createrole:{env.admin}",)
    assert found.owned_objects >= 1  # the schema
    assert isinstance(found.as_dict(), dict)
    with pytest.raises(TabrionError):
        role_privileges(env.adm, "")


# ---------------------------------------------------------------------------------------------------------------------
# Review of #90: P90-01 (the caller, objects of the system), P90-02 (server access), P90-03 (PUBLIC definers)
# ---------------------------------------------------------------------------------------------------------------------


def _every_change(env: Env, conn: Any) -> list[Callable[[], object]]:
    target = env.role()
    other = env.role()
    table = f"{env.schema}.t"
    return [
        lambda: create_role(conn, env.name(), options=RoleOptions(login=True), password=CANARY),
        lambda: alter_role(conn, target, options=RoleOptions(login=True), password=CANARY),
        lambda: drop_role(conn, target),
        lambda: grant_membership(conn, other, member=target),
        lambda: revoke_membership(conn, other, member=target),
        lambda: grant_privileges(conn, target, privileges=["SELECT"], on="table", objects=[table]),
        lambda: revoke_privileges(conn, target, privileges=["SELECT"], on="table", objects=[table]),
    ]


def test_superuser_caller_refused_for_every_change(env: Env, admin_url: str) -> None:
    """P90-01: a superuser session changes nothing, also after SET ROLE to the admin (SESSION_USER counts)."""
    for call in _every_change(env, env.su):
        _assert_no_secret(caller_refused(env, call, "superuser"))
    with psycopg.connect(admin_url) as su_as_admin:
        su_as_admin.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(env.admin)))
        su_as_admin.commit()
        assert su_as_admin.execute("SELECT current_user").fetchone() == (env.admin,)
        su_as_admin.commit()
        caller_refused(env, lambda: create_role(su_as_admin, env.name()), "superuser")
        assert su_as_admin.info.transaction_status == IDLE
    # role_privileges only reads: a superuser session may.
    assert role_privileges(env.su, env.admin).role == env.admin


CALLER_ATTRIBUTES = [
    ("BYPASSRLS", ("bypass_rls",)),
    ("REPLICATION", ("replication",)),
    ("BYPASSRLS REPLICATION", ("bypass_rls", "replication")),
]


@pytest.mark.parametrize(("attributes", "reasons"), CALLER_ATTRIBUTES)
def test_caller_with_bypass_rls_or_replication_refused(env: Env, attributes: str, reasons: tuple[str, ...]) -> None:
    """P90-01: an admin profile with BYPASSRLS or REPLICATION besides CREATEROLE changes nothing either."""
    caller = env.name()
    password = secrets.token_hex(16)
    env.su.execute(
        sql.SQL("CREATE ROLE {} LOGIN CREATEROLE {} PASSWORD {}").format(
            sql.Identifier(caller), sql.SQL(attributes), sql.Literal(password)
        )
    )
    with psycopg.connect(with_params(env.url, user=caller, password=password)) as conn:
        for call in _every_change(env, conn):
            assert caller_refused(env, call, *reasons).details["reasons"] == ",".join(reasons)
        assert conn.info.transaction_status == IDLE


SYSTEM_OBJECTS: list[tuple[Any, list[Any], str]] = [
    ("function", ["EXECUTE"], "pg_catalog.lo_export(oid, text)"),
    ("function", ["EXECUTE"], "pg_catalog.pg_read_file(text)"),
    ("function", ["EXECUTE"], "pg_catalog.pg_ls_dir(text)"),
    ("table", ["SELECT"], "pg_catalog.pg_authid"),
    ("table", ["SELECT", "UPDATE"], "pg_catalog.pg_authid"),
    ("table", ["SELECT"], "pg_catalog.pg_statistic"),
    ("table", ["SELECT"], "information_schema.tables"),
    ("schema", ["USAGE"], "pg_catalog"),
    ("schema", ["USAGE"], "public"),
    ("schema", ["USAGE"], "information_schema"),
    ("schema", ["USAGE"], "pg_toast"),
    ("database", ["CONNECT"], "postgres"),
    ("database", ["CONNECT"], "template1"),
]


def test_objects_of_the_system_refused(env: Env) -> None:
    """P90-01: EXECUTE on the server's file functions, SELECT on pg_authid and other objects of the system are
    refused before any statement, for any caller."""
    target = env.role()
    for on, privileges, name in SYSTEM_OBJECTS:
        refused(
            env,
            lambda on=on, privileges=privileges, name=name: grant_privileges(  # type: ignore[misc]
                env.adm, target, privileges=privileges, on=on, objects=[name]
            ),
            "system_object",
        )
    # One object of the system among others refuses the whole statement.
    refused(
        env,
        lambda: grant_privileges(
            env.adm, target, privileges=["SELECT"], on="table", objects=[f"{env.schema}.t", "pg_catalog.pg_authid"]
        ),
        "system_object",
    )
    found = role_privileges(env.adm, target)
    assert found.safe and found.member_of == () and found.broad == ()


def test_objects_of_a_superuser_owner_refused(env: Env) -> None:
    """P90-01: a view or function a superuser owns reads with or calls what only a superuser may grant."""
    target = env.role()
    env.su_sql("CREATE VIEW {}.su_view AS SELECT rolname FROM pg_catalog.pg_authid", env.schema)
    env.su_sql("CREATE FUNCTION {}.su_plain(integer) RETURNS int LANGUAGE sql AS 'SELECT 1'", env.schema)
    env.su_sql("GRANT SELECT ON {}.su_view TO {} WITH GRANT OPTION", env.schema, env.admin)
    env.su_sql("GRANT EXECUTE ON FUNCTION {}.su_plain(integer) TO {} WITH GRANT OPTION", env.schema, env.admin)
    cases: list[tuple[Any, list[Any], str]] = [
        ("table", ["SELECT"], f"{env.schema}.su_view"),
        ("function", ["EXECUTE"], f"{env.schema}.su_plain(integer)"),
    ]
    for on, privileges, name in cases:
        refused(
            env,
            lambda on=on, privileges=privileges, name=name: grant_privileges(  # type: ignore[misc]
                env.adm, target, privileges=privileges, on=on, objects=[name]
            ),
            "object_owner",
        )
    # The owner's BYPASSRLS counts too; CREATEROLE (the admin's own attribute) does not.
    bypass = env.role("BYPASSRLS", by_superuser=True)
    env.su_sql("ALTER VIEW {}.su_view OWNER TO {}", env.schema, bypass)
    env.su_sql("GRANT SELECT ON {}.su_view TO {} WITH GRANT OPTION", env.schema, env.admin)
    refused(
        env,
        lambda: grant_privileges(env.adm, target, privileges=["SELECT"], on="table", objects=[f"{env.schema}.su_view"]),
        "object_owner",
    )
    assert grant_privileges(env.adm, target, privileges=["SELECT"], on="table", objects=[f"{env.schema}.t"]).safe
    revoke_privileges(env.adm, target, privileges=["SELECT"], on="table", objects=[f"{env.schema}.t"])


@pytest.mark.parametrize("predefined", ["pg_execute_server_program", "pg_read_server_files", "pg_write_server_files"])
@pytest.mark.parametrize("depth", [1, 2])
def test_target_reaching_server_access_refused(env: Env, predefined: str, depth: int) -> None:
    """P90-02: no LOGIN, password, membership or privilege for a role that is or reaches a superuser-equivalent
    predefined role, directly or through a group (with any grant options)."""
    target = env.role()
    holder = target
    if depth == 2:
        holder = env.role()
        env.su_sql("GRANT {} TO {} WITH INHERIT FALSE, SET FALSE, ADMIN TRUE", holder, target)
    env.su_sql(f"GRANT {predefined} TO {{}}", holder)
    found = role_privileges(env.adm, target)
    assert f"server_access:{predefined}" in found.dangerous and not found.safe
    assert f"broad:{predefined}" in found.broad
    group = env.role()
    calls: list[Callable[[], object]] = [
        lambda: alter_role(env.adm, target, options=RoleOptions(login=True), password=CANARY),
        lambda: alter_role(env.adm, target, password=CANARY),
        lambda: grant_membership(env.adm, group, member=target),
        lambda: grant_privileges(env.adm, target, privileges=["SELECT"], on="table", objects=[f"{env.schema}.t"]),
    ]
    for call in calls:
        _assert_no_secret(refused(env, call, "reachable_server_access"))
    # A group that reaches it is not granted to anyone.
    member = env.role()
    refused(env, lambda: grant_membership(env.adm, holder, member=member), "reachable_server_access")
    # A removing change stays possible.
    drop_role(env.adm, member)


def test_public_security_definer_reported_not_refused(env: Env) -> None:
    """P90-03: SECURITY DEFINER functions PUBLIC may execute are a finding of the database, reported separately and
    with a superuser owner highlighted; they change neither ``safe`` nor a refusal."""
    env.su_sql(
        "CREATE FUNCTION {}.su_definer() RETURNS name LANGUAGE sql SECURITY DEFINER AS 'SELECT current_user'",
        env.schema,
    )
    target = create_role(env.adm, env.name())
    expected = {f"superuser_owner:{env.schema}.su_definer()", f"definer:{env.schema}.definer(integer)"}
    assert expected <= set(target.public_definers)
    assert target.safe and target.as_dict()["public_definers"] == list(target.public_definers)
    assert all(not entry.startswith(("superuser_owner", "definer")) for entry in target.dangerous)
    # The owner's own function is not reported for the owner (it can run it as itself anyway).
    assert f"definer:{env.schema}.definer(integer)" not in role_privileges(env.adm, env.admin).public_definers
    # A grant made only to some roles is not a PUBLIC one: after REVOKE ... FROM PUBLIC it is gone.
    env.su_sql("REVOKE EXECUTE ON FUNCTION {}.su_definer() FROM PUBLIC", env.schema)
    env.su_sql("REVOKE EXECUTE ON FUNCTION {}.definer(integer) FROM PUBLIC", env.schema)
    after = role_privileges(env.adm, target.role)
    assert not expected & set(after.public_definers)
    # Granting to the new role still works (not a refusal).
    assert grant_privileges(env.adm, target.role, privileges=["USAGE"], on="schema", objects=[env.schema]).safe


def test_new_role_name_with_bidi_override_refused(env: Env) -> None:
    """P90-05: a new name with a RIGHT-TO-LEFT OVERRIDE (or another Cc, Cf, Zl, Zp character) is refused."""
    before = snapshot(env)
    for suffix in ("\u202etxt", "\u200b", "\x1b"):
        with pytest.raises(UsageError) as caught:
            create_role(env.adm, new_name() + suffix)
        assert dict(caught.value.details) == {"parameter": "role"}
    assert snapshot(env) == before
