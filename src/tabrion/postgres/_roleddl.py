"""Role DDL with the attribute ban, also indirect (since 0.3, stable; TAB-XW-8).

Contract section 4.9 and notes I-180..I-186; ADR 0015 section 5 and the conditions K-2, D-1, D-2 and N-3 of the
security review of #76. Re-exported by ``tabrion.postgres``; this module is private.

Owner decision E-X6: role management runs only through a separate admin profile with minimal rights, and Tabrion never
grants SUPERUSER, BYPASSRLS, CREATEROLE or REPLICATION, neither directly nor through membership. No parameter can
request one of these attributes, ``WITH ADMIN OPTION`` or ``WITH GRANT OPTION``. Every change runs in a transaction of
its own: the target's privileges (attributes, memberships, every role reachable through membership with INHERIT, the
SET option or the ADMIN option, predefined roles, the escalation grants of K-2) are read in that transaction right
before the statement and again right after it, so a refusal or a finding that appears in between rolls everything
back. Every identifier is composed with ``psycopg.sql.Identifier`` and every value with ``psycopg.sql.Literal``, never
with string formatting; catalog names are qualified with ``pg_catalog`` (contract I-16).

The promise holds for the admin profile E-X6 describes, and Tabrion enforces that part (review P90-01 of #90): every
change refuses a session whose ``CURRENT_USER`` or ``SESSION_USER`` has SUPERUSER, BYPASSRLS or REPLICATION
(``RoleUnsafe("postgres.caller_unsafe")``), and ``grant_privileges`` refuses objects of the system and objects whose
owner has one of these attributes. A role that is or reaches ``pg_execute_server_program``, ``pg_read_server_files`` or
``pg_write_server_files`` counts as one with a banned attribute (P90-02), as a target and as an object's owner.

A password is hashed on the client (SCRAM-SHA-256, ``PGconn.encrypt_password``) and only the verifier is sent. It is
never part of an error, a ``repr`` or a log record, and the frames of a traceback do not hold it (I-76): the public
functions keep it in a :class:`_Secret` that is cleared before they return or raise, and the statement that carries the
verifier runs in a helper that drops its locals and the driver's traceback on any error. A driver error of a role
statement is raised as the :class:`tabrion.errors.DatabaseError` subclass of its kind (code ``database.<kind>``, the
safe message of contract section 1.4), never with the server's text.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
import datetime
import re
from typing import TYPE_CHECKING, Any, Literal, TypeVar
import unicodedata

from tabrion.errors import (
    Classification,
    Kind,
    PermissionDenied,
    RoleMissing,
    RoleUnsafe,
    TabrionError,
    UsageError,
    _error_class,
    classify,
)
from tabrion.postgres._common import one_row, reading, require_connection, settle

if TYPE_CHECKING:
    from collections.abc import Callable

    import psycopg

__all__ = [
    "RoleOptions",
    "RolePrivileges",
    "alter_role",
    "create_role",
    "drop_role",
    "grant_membership",
    "grant_privileges",
    "revoke_membership",
    "revoke_privileges",
    "role_privileges",
]

_T = TypeVar("_T")

_ROLE_DDL_VERSION = 160_000
"""PostgreSQL 16: before it CREATEROLE can grant membership in almost any non-superuser role (D-1)."""
_MAX_NAME_BYTES = 63
"""``NAMEDATALEN - 1``: PostgreSQL truncates a longer name, which could then name another object."""
_FIRST_NORMAL_OID = 16384
"""``FirstNormalObjectId``: a predefined role has a smaller oid and a name that starts with ``pg_`` (D-2)."""
_MAX_CONNECTION_LIMIT = 2_147_483_647
_MAX_OBJECT_CHARS = 4096
_MAX_PASSWORD_CHARS = 1024
"""A longer password is refused (P90-07): SASLprep and hashing a megabyte cost time for nothing."""
_HIDDEN_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp"})
"""Control, format (bidirectional overrides among them) and separator characters: refused in a new role name
(P90-05), they make a name look like another one in a preview or a list."""

_ATTRIBUTES = ("login", "inherit", "createdb", "createrole", "replication", "superuser", "bypass_rls")
"""``RolePrivileges.attributes`` in this order; the last four are the banned ones (E-X6)."""
_BANNED = ("superuser", "bypass_rls", "createrole", "replication")
_CALLER_BANNED = ("superuser", "bypass_rls", "replication")
"""Attributes of the connecting role (``CURRENT_USER`` or ``SESSION_USER``) that make every change refuse (P90-01);
and of an object's owner that make ``grant_privileges`` refuse the object. CREATEROLE is the admin profile's own
attribute (E-X6) and gives no access to data or files on PostgreSQL 16."""
_SERVER_ACCESS = ("pg_execute_server_program", "pg_read_server_files", "pg_write_server_files")
"""Predefined roles that are superuser-equivalent (programs and files of the server's operating system user): a role
that is or reaches one is treated as one with a banned attribute (P90-02), finding ``server_access:<role>``."""
_UNSAFE_KINDS = (*_BANNED, "server_access")

# The privileges each object kind takes (PostgreSQL's GRANT). TRIGGER is never grantable (K-2); revoking it is allowed,
# so an existing finding can be removed. No ALL, no TEMPORARY, no MAINTAIN.
_GRANTABLE: dict[str, frozenset[str]] = {
    "table": frozenset({"SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES"}),
    "sequence": frozenset({"USAGE", "SELECT", "UPDATE"}),
    "function": frozenset({"EXECUTE"}),
    "schema": frozenset({"USAGE", "CREATE"}),
    "database": frozenset({"CONNECT", "CREATE"}),
}
_REVOCABLE: dict[str, frozenset[str]] = {**_GRANTABLE, "table": _GRANTABLE["table"] | {"TRIGGER"}}
_PRIVILEGE_ORDER = (
    "SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER", "USAGE", "CREATE", "CONNECT", "EXECUTE"
)  # fmt: skip
_KEYWORD = {
    "table": "TABLE",
    "sequence": "SEQUENCE",
    "function": "FUNCTION",
    "schema": "SCHEMA",
    "database": "DATABASE",
}
_RELKINDS = {"table": ("r", "p", "v", "m", "f"), "sequence": ("S",)}

# One part of a qualified name in SQL syntax: a quoted identifier ("" for a quote) or an unquoted one (folded to lower
# case for ASCII letters, as PostgreSQL does in a UTF-8 database).
_PART = r'(?:"(?:[^"\x00]|"")+"|[A-Za-z_\x80-\U0010ffff][A-Za-z0-9_$\x80-\U0010ffff]*)'
_QUALIFIED = re.compile(rf"({_PART})\.({_PART})")
_FUNCTION = re.compile(rf"({_PART})\.({_PART})\((?:[^\x00()]|\([0-9, ]*\))*\)")


@dataclass(frozen=True, slots=True)
class RoleOptions:
    """The attributes Tabrion sets; no field for SUPERUSER, BYPASSRLS, CREATEROLE or REPLICATION, by design (E-X6)."""

    login: bool = False
    inherit: bool = True
    create_db: bool = False
    connection_limit: int | None = None
    """``None`` is no limit (``-1``); otherwise 0 or more."""
    valid_until: datetime.datetime | None = None
    """A time zone aware ``datetime``; ``None`` is no expiry (``'infinity'``)."""


@dataclass(frozen=True, slots=True)
class RolePrivileges:
    """What a role can do, read in the current database (contract section 4.9, I-181)."""

    role: str
    attributes: tuple[str, ...]
    """Of ``login``, ``inherit``, ``createdb``, ``createrole``, ``replication``, ``superuser``, ``bypass_rls``."""
    member_of: tuple[str, ...]
    """Direct memberships (``pg_auth_members``), sorted."""
    reachable: tuple[str, ...]
    """Every role reachable through membership with INHERIT, the SET option or the ADMIN option, sorted."""
    dangerous: tuple[str, ...]
    """``superuser:<role>``, ``bypass_rls:<role>``, ``createrole:<role>``, ``replication:<role>``,
    ``server_access:<predefined role>``, ``createrole_pre16``, ``trigger:<table>``, ``security_definer:<function>``,
    ``schema_create:<schema>``; sorted."""
    broad: tuple[str, ...]
    """``broad:<role>`` for each reachable predefined role, sorted (a finding, D-2)."""
    public_definers: tuple[str, ...]
    """SECURITY DEFINER functions of the current database that PUBLIC may execute (every role, a new one too) and
    whose owner the role does not reach: ``superuser_owner:<function>`` when the owner has SUPERUSER, else
    ``definer:<function>``; sorted. A finding of the database, not of the role: not in ``dangerous`` (P90-03)."""
    owned_objects: int
    """Objects the role owns in any database of the cluster (``pg_shdepend``), default privileges not counted."""
    default_privileges: int
    """Default privileges (``ALTER DEFAULT PRIVILEGES``) of the role or for it, in any database of the cluster."""

    @property
    def safe(self) -> bool:
        """``not dangerous``; ``broad`` is a finding of its own."""
        return not self.dangerous

    def as_dict(self) -> dict[str, object]:
        return {
            "role": self.role,
            "attributes": list(self.attributes),
            "member_of": list(self.member_of),
            "reachable": list(self.reachable),
            "dangerous": list(self.dangerous),
            "broad": list(self.broad),
            "public_definers": list(self.public_definers),
            "owned_objects": self.owned_objects,
            "default_privileges": self.default_privileges,
            "safe": self.safe,
        }


@dataclass(frozen=True, slots=True)
class _State:
    privileges: RolePrivileges
    oid: int
    predefined: bool
    current: bool
    """The role is ``CURRENT_USER`` or ``SESSION_USER``."""


class _Secret:
    """A password while a function runs; never shown, cleared before the function returns or raises (I-76)."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value: str | None = value

    def __repr__(self) -> str:
        return "<secret>"

    def encoded(self) -> bytes:
        if self._value is None:
            raise RuntimeError("the secret was cleared")
        return self._value.encode("utf-8")

    def clear(self) -> None:
        self._value = None


# ---------------------------------------------------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------------------------------------------------


def _invalid(parameter: str) -> UsageError:
    # The parameter's name only, never its value.
    return UsageError("postgres.invalid_argument", details={"parameter": parameter})


def _check_name(value: object, parameter: str) -> str:
    """A role, schema or database name: a non-empty ``str`` of at most 63 bytes (UTF-8) without NUL."""
    if not isinstance(value, str) or not value or "\x00" in value:
        raise _invalid(parameter)
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError:  # an unpaired surrogate
        raise _invalid(parameter) from None
    if size > _MAX_NAME_BYTES:
        raise _invalid(parameter)
    return value


def _check_new_name(value: object) -> str:
    """A name for ``create_role``: as :func:`_check_name`, not ``pg_...`` (reserved for predefined roles) and without
    a character of the categories Cc, Cf, Zl and Zp (P90-05)."""
    name = _check_name(value, "role")
    if name.startswith("pg_") or any(unicodedata.category(ch) in _HIDDEN_CATEGORIES for ch in name):
        raise _invalid("role")
    return name


def _check_options(options: object) -> RoleOptions:
    if not isinstance(options, RoleOptions):
        raise _invalid("options")
    for field in ("login", "inherit", "create_db"):
        if not isinstance(getattr(options, field), bool):
            raise _invalid(f"options.{field}")
    limit = options.connection_limit
    if limit is not None and (type(limit) is not int or not 0 <= limit <= _MAX_CONNECTION_LIMIT):
        raise _invalid("options.connection_limit")
    until = options.valid_until
    if until is not None and (not isinstance(until, datetime.datetime) or until.utcoffset() is None):
        raise _invalid("options.valid_until")
    return options


def _secret(password: object) -> tuple[_Secret | None, UsageError | None]:
    """The password in a :class:`_Secret`, or the error to raise; this frame never raises, so no traceback holds it."""
    if password is None:
        return None, None
    if not isinstance(password, str) or not password or "\x00" in password or len(password) > _MAX_PASSWORD_CHARS:
        return None, _invalid("password")
    try:
        password.encode("utf-8")
    except UnicodeEncodeError:
        return None, _invalid("password")
    return _Secret(password), None


def _unquote(part: str) -> str:
    if part.startswith('"'):
        return part[1:-1].replace('""', '"')
    return "".join(ch.lower() if "A" <= ch <= "Z" else ch for ch in part)


def _check_privileges(privileges: object, on: object, allowed: dict[str, frozenset[str]]) -> tuple[str, ...]:
    if not isinstance(on, str) or on not in allowed:
        raise _invalid("on")
    if isinstance(privileges, str | bytes) or not isinstance(privileges, Sequence) or not privileges:
        raise _invalid("privileges")
    chosen: set[str] = set()
    for privilege in privileges:
        if not isinstance(privilege, str) or privilege not in allowed[on]:
            raise _invalid("privileges")  # TRIGGER, ALL and lower case included (K-2)
        chosen.add(privilege)
    return tuple(name for name in _PRIVILEGE_ORDER if name in chosen)


def _check_objects(objects: object, on: str) -> tuple[tuple[str, ...], ...]:
    """Each object as its parts: ``(schema, name)`` for a relation, ``(schema, name, text)`` for a function (the
    whole text goes to ``to_regprocedure``), ``(name,)`` for a schema or a database."""
    if isinstance(objects, str | bytes) or not isinstance(objects, Sequence) or not objects:
        raise _invalid("objects")
    parsed: list[tuple[str, ...]] = []
    for item in objects:
        if not isinstance(item, str) or not item or "\x00" in item or len(item) > _MAX_OBJECT_CHARS:
            raise _invalid("objects")
        try:
            item.encode("utf-8")
        except UnicodeEncodeError:
            raise _invalid("objects") from None
        if on in ("schema", "database"):
            parsed.append((_check_name(item, "objects"),))
            continue
        match = (_FUNCTION if on == "function" else _QUALIFIED).fullmatch(item)
        if match is None:
            raise _invalid("objects")  # not schema-qualified, or not one name
        parts = tuple(_unquote(match.group(index)) for index in (1, 2))
        for part in parts:
            _check_name(part, "objects")
        parsed.append(parts + ((item,) if on == "function" else ()))
    return tuple(parsed)


# ---------------------------------------------------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------------------------------------------------

# The role and the session's roles. A fixed statement; the name is a bound parameter.
_ROLE_SQL = (
    "SELECT r.oid, r.rolcanlogin, r.rolinherit, r.rolcreatedb, r.rolcreaterole, r.rolreplication, r.rolsuper,"
    " r.rolbypassrls,"
    " pg_catalog.left(r.rolname::pg_catalog.text, 3) OPERATOR(pg_catalog.=) 'pg_'"
    "   AND r.oid OPERATOR(pg_catalog.<) %(first)s::pg_catalog.oid,"
    " r.rolname OPERATOR(pg_catalog.=) CURRENT_USER OR r.rolname OPERATOR(pg_catalog.=) SESSION_USER"
    " FROM pg_catalog.pg_roles r WHERE r.rolname::pg_catalog.text OPERATOR(pg_catalog.=) %(role)s::pg_catalog.text"
)

# Every role reachable from the role through pg_auth_members, whatever the INHERIT, SET or ADMIN option (N-3: with
# ADMIN a role can grant the membership to itself), at any depth, the role itself included; plus the database owner's
# implicit membership in pg_database_owner (for the current database). A superuser is not "member" of every role here
# (pg_has_role would say so): it is reported by its own attribute. Each row: oid, name, the four banned attributes,
# predefined.
_REACH_SQL = (
    "WITH RECURSIVE edges(member, roleid) AS ("
    "   SELECT m.member, m.roleid FROM pg_catalog.pg_auth_members m"
    "   UNION ALL SELECT d.datdba, o.oid FROM pg_catalog.pg_database d, pg_catalog.pg_roles o"
    "   WHERE d.datname OPERATOR(pg_catalog.=) pg_catalog.current_database()"
    "   AND o.rolname::pg_catalog.text OPERATOR(pg_catalog.=) 'pg_database_owner'),"
    " reach(oid) AS (SELECT %(oid)s::pg_catalog.oid"
    "   UNION SELECT e.roleid FROM edges e JOIN reach ON e.member OPERATOR(pg_catalog.=) reach.oid)"
    " SELECT r.oid, r.rolname::pg_catalog.text, r.rolsuper, r.rolbypassrls, r.rolcreaterole, r.rolreplication,"
    " pg_catalog.left(r.rolname::pg_catalog.text, 3) OPERATOR(pg_catalog.=) 'pg_'"
    "   AND r.oid OPERATOR(pg_catalog.<) %(first)s::pg_catalog.oid"
    " FROM reach JOIN pg_catalog.pg_roles r ON r.oid OPERATOR(pg_catalog.=) reach.oid"
)

_MEMBER_OF_SQL = (
    "SELECT DISTINCT r.rolname::pg_catalog.text FROM pg_catalog.pg_auth_members m"
    " JOIN pg_catalog.pg_roles r ON r.oid OPERATOR(pg_catalog.=) m.roleid"
    " WHERE m.member OPERATOR(pg_catalog.=) %s::pg_catalog.oid"
)

# K-2 in the current database: grants to the role or a role it can reach (``%(reach)s``) of TRIGGER on a relation or
# CREATE on a schema (also to PUBLIC, which every role holds) and of EXECUTE on a SECURITY DEFINER function (not to
# PUBLIC: PostgreSQL grants EXECUTE on every function to PUBLIC by default, I-181). An object the role can become the
# owner of is not an escalation and not counted, except CREATE on ``public``, which is counted whoever owns it unless
# the grant is the owner's own entry. Each row: kind, name (quoted where SQL needs it).
_ESCALATION_SQL = (
    "SELECT 'trigger', pg_catalog.format('%%I.%%I', n.nspname, c.relname)"
    " FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid OPERATOR(pg_catalog.=) c.relnamespace,"
    " LATERAL pg_catalog.aclexplode(c.relacl) a"
    " WHERE a.privilege_type OPERATOR(pg_catalog.=) 'TRIGGER'"
    " AND (a.grantee OPERATOR(pg_catalog.=) ANY (%(reach)s::pg_catalog.oid[])"
    "   OR a.grantee OPERATOR(pg_catalog.=) 0::pg_catalog.oid)"
    " AND NOT c.relowner OPERATOR(pg_catalog.=) ANY (%(reach)s::pg_catalog.oid[])"
    " UNION SELECT 'security_definer', pg_catalog.format('%%I.%%I(%%s)', n.nspname, p.proname,"
    "   pg_catalog.pg_get_function_identity_arguments(p.oid))"
    " FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid OPERATOR(pg_catalog.=) p.pronamespace,"
    " LATERAL pg_catalog.aclexplode(p.proacl) a"
    " WHERE p.prosecdef AND a.privilege_type OPERATOR(pg_catalog.=) 'EXECUTE'"
    " AND a.grantee OPERATOR(pg_catalog.=) ANY (%(reach)s::pg_catalog.oid[])"
    " AND NOT p.proowner OPERATOR(pg_catalog.=) ANY (%(reach)s::pg_catalog.oid[])"
    " UNION SELECT 'schema_create', pg_catalog.format('%%I', n.nspname)"
    " FROM pg_catalog.pg_namespace n, LATERAL pg_catalog.aclexplode(n.nspacl) a"
    " WHERE a.privilege_type OPERATOR(pg_catalog.=) 'CREATE'"
    " AND (a.grantee OPERATOR(pg_catalog.=) ANY (%(reach)s::pg_catalog.oid[])"
    "   OR a.grantee OPERATOR(pg_catalog.=) 0::pg_catalog.oid)"
    " AND a.grantee OPERATOR(pg_catalog.<>) n.nspowner"
    " AND (n.nspname OPERATOR(pg_catalog.=) 'public'"
    "   OR NOT n.nspowner OPERATOR(pg_catalog.=) ANY (%(reach)s::pg_catalog.oid[]))"
)

# P90-03: SECURITY DEFINER functions of the current database that PUBLIC may execute (the default ACL when ``proacl``
# is NULL grants EXECUTE to PUBLIC), owned by a role the role does not reach. Each row: name, the owner is superuser.
_PUBLIC_DEFINER_SQL = (
    "SELECT pg_catalog.format('%%I.%%I(%%s)', n.nspname, p.proname,"
    "   pg_catalog.pg_get_function_identity_arguments(p.oid)), o.rolsuper"
    " FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid OPERATOR(pg_catalog.=) p.pronamespace"
    " JOIN pg_catalog.pg_roles o ON o.oid OPERATOR(pg_catalog.=) p.proowner,"
    " LATERAL pg_catalog.aclexplode(COALESCE(p.proacl, pg_catalog.acldefault('f', p.proowner))) a"
    " WHERE p.prosecdef AND a.privilege_type OPERATOR(pg_catalog.=) 'EXECUTE'"
    " AND a.grantee OPERATOR(pg_catalog.=) 0::pg_catalog.oid"
    " AND NOT p.proowner OPERATOR(pg_catalog.=) ANY (%(reach)s::pg_catalog.oid[])"
)

# P90-01: the banned attributes of the session's roles (CURRENT_USER and SESSION_USER).
_CALLER_SQL = (
    "SELECT r.rolsuper, r.rolbypassrls, r.rolreplication FROM pg_catalog.pg_roles r"
    " WHERE r.rolname OPERATOR(pg_catalog.=) CURRENT_USER OR r.rolname OPERATOR(pg_catalog.=) SESSION_USER"
)

# Dependencies on the role in every database of the cluster (pg_shdepend): owned objects (deptype 'o') without the
# default privileges, and the default privileges of the role or for it.
_DEPENDENCIES_SQL = (
    "SELECT pg_catalog.count(*) FILTER (WHERE s.deptype OPERATOR(pg_catalog.=) 'o'"
    "   AND s.classid OPERATOR(pg_catalog.<>) 'pg_catalog.pg_default_acl'::pg_catalog.regclass),"
    " pg_catalog.count(*) FILTER"
    "   (WHERE s.classid OPERATOR(pg_catalog.=) 'pg_catalog.pg_default_acl'::pg_catalog.regclass)"
    " FROM pg_catalog.pg_shdepend s"
    " WHERE s.refclassid OPERATOR(pg_catalog.=) 'pg_catalog.pg_authid'::pg_catalog.regclass"
    " AND s.refobjid OPERATOR(pg_catalog.=) %s::pg_catalog.oid"
)


def _read_state(
    cursor: psycopg.Cursor[tuple[Any, ...]], role: str, server_version: int, parameter: str = "role"
) -> _State:
    cursor.execute(_ROLE_SQL, {"role": role, "first": _FIRST_NORMAL_OID})
    row = cursor.fetchone()
    if row is None:
        # The name stays out of the error: the caller has it.
        raise RoleMissing("postgres.role_missing", details={"parameter": parameter})
    oid = int(row[0])
    attributes = tuple(name for name, flag in zip(_ATTRIBUTES, row[1:8], strict=True) if flag)
    cursor.execute(_REACH_SQL, {"oid": oid, "first": _FIRST_NORMAL_OID})
    reach = cursor.fetchall()
    dangerous: set[str] = set()
    reachable: list[str] = []
    broad: list[str] = []
    createrole = False
    for other, name, superuser, bypass_rls, create_role, replication, predefined in reach:
        for flag, kind in ((superuser, "superuser"), (bypass_rls, "bypass_rls"), (create_role, "createrole"),
                           (replication, "replication")):  # fmt: skip
            if flag:
                dangerous.add(f"{kind}:{name}")
        createrole = createrole or bool(create_role)
        if int(other) != oid:
            reachable.append(str(name))
            if predefined:
                broad.append(f"broad:{name}")
        if predefined and name in _SERVER_ACCESS:
            dangerous.add(f"server_access:{name}")  # P90-02: superuser-equivalent
    if createrole and server_version < _ROLE_DDL_VERSION:
        dangerous.add("createrole_pre16")  # D-1: CREATEROLE before 16 is superuser-equivalent
    reach_oids = [int(other) for other, *_ in reach]
    cursor.execute(_ESCALATION_SQL, {"reach": reach_oids})
    dangerous.update(f"{kind}:{name}" for kind, name in cursor.fetchall())
    cursor.execute(_PUBLIC_DEFINER_SQL, {"reach": reach_oids})
    public_definers = sorted(
        f"{'superuser_owner' if superuser else 'definer'}:{name}" for name, superuser in cursor.fetchall()
    )
    cursor.execute(_MEMBER_OF_SQL, (oid,))
    member_of = sorted(str(name) for (name,) in cursor.fetchall())
    cursor.execute(_DEPENDENCIES_SQL, (oid,))
    owned, defaults = one_row(cursor)
    privileges = RolePrivileges(
        role=role,
        attributes=attributes,
        member_of=tuple(member_of),
        reachable=tuple(sorted(reachable)),
        dangerous=tuple(sorted(dangerous)),
        broad=tuple(sorted(broad)),
        public_definers=tuple(public_definers),
        owned_objects=int(owned),
        default_privileges=int(defaults),
    )
    return _State(privileges=privileges, oid=oid, predefined=bool(row[8]), current=bool(row[9]))


def role_privileges(connection: psycopg.Connection[Any], role: str) -> RolePrivileges:
    """The privileges of ``role`` (read-only, any server version): attributes, memberships, reachable roles, findings.

    ``RoleMissing`` for an unknown role, ``UsageError("postgres.invalid_argument")`` for a name that is not a non-empty
    ``str`` of at most 63 bytes without NUL. Reads through its own cursor in any transaction state (contract section 4,
    T-NOTX); on PostgreSQL 15 and earlier a reachable CREATEROLE is also reported as ``createrole_pre16`` (D-1).
    Contract section 4.9, I-181.
    """
    name = _check_name(role, "role")
    with reading(connection, "role_privileges") as cursor:
        version = connection.info.server_version
        return _read_state(cursor, name, version).privileges


# ---------------------------------------------------------------------------------------------------------------------
# Changing
# ---------------------------------------------------------------------------------------------------------------------


def _start(connection: psycopg.Connection[Any], function: str) -> None:
    """PostgreSQL 16 or later (D-1) and no transaction of the caller's (T-NOTX, as ``grant_runtime_role``)."""
    require_connection(connection, function)
    from psycopg import pq

    settle(connection)  # pipeline mode: the status is known only after a sync (contract I-34)
    if connection.info.server_version < _ROLE_DDL_VERSION:
        raise UsageError("postgres.role_ddl_requires_16")
    if connection.info.transaction_status != pq.TransactionStatus.IDLE:
        raise UsageError("postgres.nested_transaction")


def _driver_error(error: BaseException) -> TabrionError:
    """The ``DatabaseError`` subclass of the error's kind, code ``database.<kind>``; ``str`` is the safe message."""
    classification = classify(error)
    error.__traceback__ = None  # the driver's frames hold the statement
    return _error_class(classification.kind)(
        f"database.{classification.kind.value}", classification=classification, original=error
    )


def _translated(body: Callable[[], _T]) -> _T:
    """Run ``body``; a driver error is raised as a ``TabrionError`` here, outside the ``except`` block (no
    ``__cause__``, no ``__context__``)."""
    import psycopg

    try:
        return body()
    except psycopg.Error as exc:
        failure: BaseException = exc
    raise _driver_error(failure)


@contextmanager
def _transaction(connection: psycopg.Connection[Any]) -> Iterator[psycopg.Cursor[tuple[Any, ...]]]:
    """A transaction of its own (committed at the end, rolled back on any error) and a ``tuple_row`` cursor."""
    import psycopg
    from psycopg.rows import tuple_row

    with connection.transaction(), psycopg.Cursor(connection, row_factory=tuple_row) as cursor:
        yield cursor


def _refuse(reasons: Sequence[str]) -> None:
    if reasons:
        unique = list(dict.fromkeys(reasons))
        raise RoleUnsafe("postgres.role_unsafe", details={"reasons": ",".join(unique)})


def _check_caller(cursor: psycopg.Cursor[tuple[Any, ...]]) -> None:
    """P90-01: no change by a session whose ``CURRENT_USER`` or ``SESSION_USER`` has SUPERUSER, BYPASSRLS or
    REPLICATION. Such a session can grant what the checks of a target cannot see (EXECUTE on a server file function,
    SELECT on ``pg_authid``); the admin profile is a role with CREATEROLE only (E-X6). Raises
    ``RoleUnsafe("postgres.caller_unsafe")``, ``details["reasons"]`` the attributes found."""
    cursor.execute(_CALLER_SQL)
    found: set[str] = set()
    for row in cursor.fetchall():
        found.update(kind for kind, flag in zip(_CALLER_BANNED, row, strict=True) if flag)
    if found:
        reasons = ",".join(kind for kind in _CALLER_BANNED if kind in found)
        raise RoleUnsafe("postgres.caller_unsafe", details={"reasons": reasons})


def _attribute_reasons(privileges: RolePrivileges, *, own: bool = True) -> list[str]:
    """``superuser`` (the role's own attribute) or ``reachable_superuser`` (a role it can reach), and so on; also
    ``server_access``/``reachable_server_access`` for the superuser-equivalent predefined roles (P90-02)."""
    reasons: list[str] = []
    for entry in privileges.dangerous:
        kind, _, name = entry.partition(":")
        if kind not in _UNSAFE_KINDS:
            continue
        reasons.append(kind if own and name == privileges.role else f"reachable_{kind}")
    return reasons


def _escalation_reasons(entries: Sequence[str]) -> list[str]:
    """``trigger``, ``security_definer`` and ``schema_create`` for the K-2 entries among ``entries``."""
    return [
        kind
        for kind in ("trigger", "security_definer", "schema_create")
        if any(e.startswith(kind + ":") for e in entries)
    ]


def _target_reasons(state: _State, *, increase: bool) -> list[str]:
    """A role Tabrion does not change: a predefined role, the session's own role and, for a change that adds
    privileges (LOGIN, a password, a membership, an object privilege), a role with a banned attribute, one that can
    reach such a role or a superuser-equivalent predefined role (P90-02)."""
    reasons: list[str] = []
    if state.predefined:
        reasons.append("predefined_role")
    if state.current:
        reasons.append("current_user")
    if increase:
        reasons.extend(_attribute_reasons(state.privileges))
    return reasons


def _new_findings(before: RolePrivileges, after: RolePrivileges) -> list[str]:
    """The reasons for findings ``after`` has and ``before`` had not (read again after the change, I-182)."""
    added = [entry for entry in after.dangerous if entry not in before.dangerous]
    reasons: list[str] = []
    for entry in added:
        kind, _, name = entry.partition(":")
        if kind in _UNSAFE_KINDS:
            reasons.append(kind if name == after.role else f"reachable_{kind}")
    reasons.extend(_escalation_reasons(added))
    if any(entry not in before.broad for entry in after.broad):
        reasons.append("predefined_role")
    return reasons


def _execute_secret(
    cursor: psycopg.Cursor[tuple[Any, ...]],
    statement: psycopg.sql.Composable,
    secret: _Secret,
    role: str,
) -> None:
    """``statement PASSWORD '<SCRAM verifier>'``; on any error the verifier leaves no frame local behind (I-76)."""
    import psycopg
    from psycopg import sql
    from psycopg.rows import tuple_row

    connection = cursor.connection
    verifier: Any = None
    full: Any = None
    error: BaseException | None = None
    try:
        verifier = connection.pgconn.encrypt_password(secret.encoded(), role.encode("utf-8"), b"scram-sha-256")
        full = sql.SQL("{} PASSWORD {}").format(statement, sql.Literal(verifier.decode("ascii")))
        with psycopg.Cursor(connection, row_factory=tuple_row) as own:
            own.execute(full)
    except BaseException as exc:
        error = exc
    finally:
        verifier = full = None
    if error is None:
        return
    error.__traceback__ = None  # the driver's frames and this one held the verifier
    raise error


def _run_secret(secret: _Secret | None, body: Callable[[], _T]) -> _T:
    """Run ``body`` and clear ``secret`` whatever happens; an error is raised from here with a fresh traceback."""
    error: BaseException | None = None
    try:
        return _translated(body)
    except BaseException as exc:
        error = exc
    finally:
        if secret is not None:
            secret.clear()
    if error is None:  # pragma: no cover - body returned or raised
        raise RuntimeError("unreachable")
    raise error.with_traceback(None)


def _option_clauses(options: RoleOptions, current: tuple[Any, ...] | None) -> list[psycopg.sql.Composable]:
    """The attribute clauses: all of them for ``CREATE ROLE`` (``current`` ``None``), else only those that differ.

    ``ALTER ROLE`` names an attribute only when it changes: PostgreSQL 16 refuses a CREATEDB clause, even NOCREATEDB,
    from a role without CREATEDB."""
    from psycopg import sql

    clauses: list[sql.Composable] = []
    for value, index, yes, no in (
        (options.login, 0, "LOGIN", "NOLOGIN"),
        (options.inherit, 1, "INHERIT", "NOINHERIT"),
        (options.create_db, 2, "CREATEDB", "NOCREATEDB"),
    ):
        if current is None or bool(current[index]) != value:
            clauses.append(sql.SQL(yes if value else no))
    limit = -1 if options.connection_limit is None else options.connection_limit
    if current is None or int(current[3]) != limit:
        clauses.append(sql.SQL("CONNECTION LIMIT {}").format(sql.Literal(limit)))
    until = "infinity" if options.valid_until is None else options.valid_until.isoformat()
    if current is None or bool(current[4]):
        clauses.append(sql.SQL("VALID UNTIL {}").format(sql.Literal(until)))
    return clauses


def create_role(
    connection: psycopg.Connection[Any],
    role: str,
    *,
    options: RoleOptions = RoleOptions(),
    password: str | None = None,
) -> RolePrivileges:
    """``CREATE ROLE role WITH NOSUPERUSER NOCREATEROLE NOREPLICATION NOBYPASSRLS <options> [PASSWORD <verifier>]``.

    PostgreSQL 16 or later (``UsageError("postgres.role_ddl_requires_16")``), a transaction of its own
    (``UsageError("postgres.nested_transaction")``), a session without SUPERUSER, BYPASSRLS and REPLICATION
    (``RoleUnsafe("postgres.caller_unsafe")``, as every change). ``UsageError("postgres.role_exists")`` for an existing
    role; ``UsageError("postgres.invalid_argument")`` for a name that starts with ``pg_`` or holds a character of the
    Unicode categories Cc, Cf, Zl or Zp, and for a password longer than 1024 characters. The password is hashed on the
    client and sent as a SCRAM-SHA-256 verifier; it is never logged or part of an error.
    Returns the new role's :func:`role_privileges`, read in the same transaction. Contract section 4.9, I-180..I-183.
    """
    secret, problem = _secret(password)
    del password  # I-76: not in the frames of a traceback
    try:
        if problem is not None:
            raise problem
        name = _check_new_name(role)
        chosen = _check_options(options)
        _start(connection, "create_role")
    except BaseException:
        if secret is not None:
            secret.clear()
        raise
    version = connection.info.server_version
    from psycopg import sql

    def body() -> RolePrivileges:
        with _transaction(connection) as cursor:
            _check_caller(cursor)
            cursor.execute(
                "SELECT 1 FROM pg_catalog.pg_roles r"
                " WHERE r.rolname::pg_catalog.text OPERATOR(pg_catalog.=) %s::pg_catalog.text",
                (name,),
            )
            if cursor.fetchone() is not None:
                raise UsageError("postgres.role_exists")
            statement = sql.SQL(" ").join(
                [
                    sql.SQL("CREATE ROLE {} WITH NOSUPERUSER NOCREATEROLE NOREPLICATION NOBYPASSRLS").format(
                        sql.Identifier(name)
                    ),
                    *_option_clauses(chosen, None),
                ]
            )
            if secret is not None:
                _execute_secret(cursor, statement, secret, name)
            else:
                cursor.execute(statement)
            after = _read_state(cursor, name, version)
            _refuse(_attribute_reasons(after.privileges))  # a new role reaches nothing; read to be sure (I-182)
            return after.privileges

    return _run_secret(secret, body)


def alter_role(
    connection: psycopg.Connection[Any],
    role: str,
    *,
    options: RoleOptions | None = None,
    password: str | None = None,
) -> RolePrivileges:
    """``ALTER ROLE role WITH <the options that differ> [PASSWORD <verifier>]``; ``options`` sets every field of it.

    At least one of ``options`` and ``password`` (``UsageError("postgres.invalid_argument")``). Refused before the
    statement (``RoleUnsafe``): a predefined role (``predefined_role``), the session's own role (``current_user``), a
    role with SUPERUSER, BYPASSRLS, CREATEROLE or REPLICATION (``superuser``, ``bypass_rls``, ``createrole``,
    ``replication``) or that can reach one (``reachable_*``), a role that is or reaches ``pg_execute_server_program``,
    ``pg_read_server_files`` or ``pg_write_server_files`` (``reachable_server_access``, P90-02). ``RoleMissing`` for
    an unknown role. Returns the role's
    :func:`role_privileges` after the change. Contract section 4.9, I-180..I-183.
    """
    secret, problem = _secret(password)
    del password  # I-76
    try:
        if problem is not None:
            raise problem
        name = _check_name(role, "role")
        if options is None and secret is None:
            raise _invalid("options")
        chosen = None if options is None else _check_options(options)
        _start(connection, "alter_role")
    except BaseException:
        if secret is not None:
            secret.clear()
        raise
    version = connection.info.server_version
    from psycopg import sql

    def body() -> RolePrivileges:
        with _transaction(connection) as cursor:
            _check_caller(cursor)
            before = _read_state(cursor, name, version)
            _refuse(_target_reasons(before, increase=True))
            clauses: list[sql.Composable] = []
            if chosen is not None:
                cursor.execute(
                    "SELECT r.rolcanlogin, r.rolinherit, r.rolcreatedb, r.rolconnlimit,"
                    " COALESCE(r.rolvaliduntil, 'infinity'::pg_catalog.timestamptz)"
                    "   IS DISTINCT FROM COALESCE(%s::pg_catalog.timestamptz, 'infinity'::pg_catalog.timestamptz)"
                    " FROM pg_catalog.pg_roles r WHERE r.oid OPERATOR(pg_catalog.=) %s::pg_catalog.oid",
                    (chosen.valid_until, before.oid),
                )
                clauses = _option_clauses(chosen, one_row(cursor))
            head = sql.SQL("ALTER ROLE {} WITH").format(sql.Identifier(name))
            if secret is not None:
                _execute_secret(cursor, sql.SQL(" ").join([head, *clauses]), secret, name)
            elif clauses:
                cursor.execute(sql.SQL(" ").join([head, *clauses]))
            after = _read_state(cursor, name, version)
            _refuse(_new_findings(before.privileges, after.privileges))
            return after.privileges

    return _run_secret(secret, body)


def drop_role(connection: psycopg.Connection[Any], role: str) -> None:
    """``DROP ROLE role``; never ``REASSIGN OWNED`` or ``DROP OWNED``.

    Refused before the statement (``RoleUnsafe``): a role that owns objects or has default privileges in any database
    (``owns_objects``), the session's own role (``current_user``), a predefined role (``predefined_role``).
    ``RoleMissing`` for an unknown role. Privileges granted to the role make PostgreSQL refuse the drop: that driver
    error is raised as ``tabrion.errors.DatabaseError`` (code ``database.<kind>``). Contract section 4.9, I-184.
    """
    name = _check_name(role, "role")
    _start(connection, "drop_role")
    version = connection.info.server_version
    from psycopg import sql

    def body() -> None:
        with _transaction(connection) as cursor:
            _check_caller(cursor)
            state = _read_state(cursor, name, version)
            reasons = _target_reasons(state, increase=False)
            if state.privileges.owned_objects or state.privileges.default_privileges:
                reasons.append("owns_objects")
            _refuse(reasons)
            cursor.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(name)))

    _translated(body)


def grant_membership(connection: psycopg.Connection[Any], role: str, *, member: str) -> RolePrivileges:
    """``GRANT role TO member`` (no ``ADMIN`` option); returns ``member``'s :func:`role_privileges` after it.

    Refused before the statement (``RoleUnsafe``): ``role`` has, or can reach a role with, SUPERUSER, BYPASSRLS,
    CREATEROLE or REPLICATION (``reachable_*``; N-3: the ADMIN option counts); ``role`` is or can reach a predefined
    role (``predefined_role``, D-2); ``role`` holds an escalation grant of K-2 (``trigger``, ``security_definer``,
    ``schema_create``); ``role`` or ``member`` is the session's own role (``current_user``); ``member`` is predefined
    or has, or can reach, a banned attribute (as :func:`alter_role`). Read again after the statement in the same
    transaction: a new finding of ``member`` rolls it back. Contract section 4.9, I-182, I-185.
    """
    name = _check_name(role, "role")
    grantee = _check_name(member, "member")
    if name == grantee:
        raise _invalid("member")
    _start(connection, "grant_membership")
    version = connection.info.server_version
    from psycopg import sql

    def body() -> RolePrivileges:
        with _transaction(connection) as cursor:
            _check_caller(cursor)
            granted = _read_state(cursor, name, version)
            before = _read_state(cursor, grantee, version, "member")
            reasons = _attribute_reasons(granted.privileges, own=False)
            if granted.predefined or granted.privileges.broad:
                reasons.append("predefined_role")
            # K-2 grants the member would gain (a grant to PUBLIC, which both hold already, is not new).
            reasons.extend(
                _escalation_reasons([e for e in granted.privileges.dangerous if e not in before.privileges.dangerous])
            )
            if granted.current:
                reasons.append("current_user")
            reasons.extend(_target_reasons(before, increase=True))
            _refuse(reasons)
            cursor.execute(sql.SQL("GRANT {} TO {}").format(sql.Identifier(name), sql.Identifier(grantee)))
            after = _read_state(cursor, grantee, version, "member")
            _refuse(_new_findings(before.privileges, after.privileges))
            return after.privileges

    return _translated(body)


def revoke_membership(connection: psycopg.Connection[Any], role: str, *, member: str) -> RolePrivileges:
    """``REVOKE role FROM member``; returns ``member``'s :func:`role_privileges` after it.

    Removes the membership granted by the connecting role (PostgreSQL 16 records the grantor; one granted by another
    role stays and PostgreSQL only warns: check ``member_of`` in the result). Refused (``RoleUnsafe``): ``member`` is
    predefined (``predefined_role``) or the session's own role (``current_user``). Revoking a membership in a
    predefined or dangerous role is allowed: it removes a finding. Contract section 4.9, I-185.
    """
    name = _check_name(role, "role")
    grantee = _check_name(member, "member")
    if name == grantee:
        raise _invalid("member")
    _start(connection, "revoke_membership")
    version = connection.info.server_version
    from psycopg import sql

    def body() -> RolePrivileges:
        with _transaction(connection) as cursor:
            _check_caller(cursor)
            _read_state(cursor, name, version)
            before = _read_state(cursor, grantee, version, "member")
            _refuse(_target_reasons(before, increase=False))
            cursor.execute(sql.SQL("REVOKE {} FROM {}").format(sql.Identifier(name), sql.Identifier(grantee)))
            return _read_state(cursor, grantee, version, "member").privileges

    return _translated(body)


@dataclass(frozen=True, slots=True)
class _Resolved:
    target: psycopg.sql.Composable
    oid: int
    owner: int
    security_definer: bool
    public: bool
    namespace: str | None
    """The object's schema; for a schema its own name; ``None`` for a database."""


def _denied(code: str) -> PermissionDenied:
    """A refusal classified as SQLSTATE 42501 would be (as ``tabrion.postgres.create_database``)."""
    classification = Classification(
        kind=Kind.PERMISSION,
        retryable=False,
        outcome_unknown=False,
        connection_usable=True,
        driver="psycopg",
        sqlstate="42501",
        sqlite_code=None,
        constraint=None,
        table=None,
        column=None,
    )
    return PermissionDenied(code, classification=classification)


def _missing(index: int) -> UsageError:
    return UsageError("postgres.object_missing", details={"parameter": "objects", "index": index})


def _resolve(
    connection: psycopg.Connection[Any],
    cursor: psycopg.Cursor[tuple[Any, ...]],
    on: str,
    objects: tuple[tuple[str, ...], ...],
) -> list[_Resolved]:
    """Each object by an exact catalog lookup of its parsed parts (a function through ``to_regprocedure`` with an
    empty ``search_path``, so its argument types resolve in ``pg_catalog`` or by their schema); the statement names
    them with ``psycopg.sql.Identifier`` from the catalog's own names (I-183)."""
    import psycopg
    from psycopg import sql

    resolved: list[_Resolved] = []
    for index, parts in enumerate(objects):
        if on in ("table", "sequence"):
            cursor.execute(
                "SELECT c.oid, c.relowner FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n"
                " ON n.oid OPERATOR(pg_catalog.=) c.relnamespace"
                " WHERE n.nspname::pg_catalog.text OPERATOR(pg_catalog.=) %s::pg_catalog.text"
                " AND c.relname::pg_catalog.text OPERATOR(pg_catalog.=) %s::pg_catalog.text"
                " AND c.relkind::pg_catalog.text OPERATOR(pg_catalog.=) ANY (%s::pg_catalog.text[])",
                (parts[0], parts[1], list(_RELKINDS[on])),
            )
            row = cursor.fetchone()
            if row is None:
                raise _missing(index)
            resolved.append(
                _Resolved(sql.Identifier(parts[0], parts[1]), int(row[0]), int(row[1]), False, False, parts[0])
            )
        elif on == "function":
            try:
                with connection.transaction():  # a savepoint: a text the server cannot parse raises (42601 and others)
                    cursor.execute(
                        "SELECT p.oid, n.nspname::pg_catalog.text, p.proname::pg_catalog.text, p.proowner,"
                        " p.prosecdef, p.prokind::pg_catalog.text"
                        " FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n"
                        " ON n.oid OPERATOR(pg_catalog.=) p.pronamespace"
                        " WHERE p.oid OPERATOR(pg_catalog.=) pg_catalog.to_regprocedure(%s::pg_catalog.text)",
                        (parts[2],),
                    )
                    row = cursor.fetchone()
            except (psycopg.ProgrammingError, psycopg.DataError):
                row = None
            if row is None or (row[1], row[2]) != (parts[0], parts[1]) or row[5] != "f":
                raise _missing(index)
            cursor.execute(
                "SELECT n.nspname::pg_catalog.text, t.typname::pg_catalog.text"
                " FROM pg_catalog.pg_proc p, pg_catalog.unnest(p.proargtypes::pg_catalog.oid[]) WITH ORDINALITY"
                "   AS a(oid, position)"
                " JOIN pg_catalog.pg_type t ON t.oid OPERATOR(pg_catalog.=) a.oid"
                " JOIN pg_catalog.pg_namespace n ON n.oid OPERATOR(pg_catalog.=) t.typnamespace"
                " WHERE p.oid OPERATOR(pg_catalog.=) %s::pg_catalog.oid ORDER BY a.position",
                (row[0],),
            )
            arguments = sql.SQL(", ").join(sql.Identifier(schema, name) for schema, name in cursor.fetchall())
            target = sql.SQL("{}({})").format(sql.Identifier(parts[0], parts[1]), arguments)
            resolved.append(_Resolved(target, int(row[0]), int(row[3]), bool(row[4]), False, parts[0]))
        elif on == "schema":
            cursor.execute(
                "SELECT n.oid, n.nspowner FROM pg_catalog.pg_namespace n"
                " WHERE n.nspname::pg_catalog.text OPERATOR(pg_catalog.=) %s::pg_catalog.text",
                (parts[0],),
            )
            row = cursor.fetchone()
            if row is None:
                raise _missing(index)
            resolved.append(
                _Resolved(sql.Identifier(parts[0]), int(row[0]), int(row[1]), False, parts[0] == "public", parts[0])
            )
        else:
            cursor.execute(
                "SELECT d.oid, d.datdba FROM pg_catalog.pg_database d"
                " WHERE d.datname::pg_catalog.text OPERATOR(pg_catalog.=) %s::pg_catalog.text",
                (parts[0],),
            )
            row = cursor.fetchone()
            if row is None:
                raise _missing(index)
            resolved.append(_Resolved(sql.Identifier(parts[0]), int(row[0]), int(row[1]), False, False, None))
    return resolved


def _system_object(target: _Resolved) -> bool:
    """An object of the system (P90-01): an oid below ``FirstNormalObjectId`` (made by ``initdb``: ``pg_catalog``,
    ``public``, ``postgres``, the template databases) or in ``information_schema`` or a ``pg_`` schema
    (``pg_catalog``, ``pg_toast``, ``pg_temp_*``; PostgreSQL reserves the prefix)."""
    if target.oid < _FIRST_NORMAL_OID:
        return True
    namespace = target.namespace
    return namespace is not None and (namespace.startswith("pg_") or namespace == "information_schema")


# P90-01 and P90-02 for the owners of the objects of ``grant_privileges``: some owner has SUPERUSER, BYPASSRLS or
# REPLICATION, or is or reaches (as ``_REACH_SQL``: any grant option, any depth, the database owner's implicit
# ``pg_database_owner`` too) a superuser-equivalent predefined role. A membership in a role with a banned attribute is
# not counted: attributes are not inherited, the rights of a predefined role are.
_UNSAFE_OWNER_SQL = (
    "WITH RECURSIVE edges(member, roleid) AS ("
    "   SELECT m.member, m.roleid FROM pg_catalog.pg_auth_members m"
    "   UNION ALL SELECT d.datdba, o.oid FROM pg_catalog.pg_database d, pg_catalog.pg_roles o"
    "   WHERE d.datname OPERATOR(pg_catalog.=) pg_catalog.current_database()"
    "   AND o.rolname::pg_catalog.text OPERATOR(pg_catalog.=) 'pg_database_owner'),"
    " reach(owner, oid) AS (SELECT o, o FROM pg_catalog.unnest(%(owners)s::pg_catalog.oid[]) AS u(o)"
    "   UNION SELECT reach.owner, e.roleid FROM edges e JOIN reach ON e.member OPERATOR(pg_catalog.=) reach.oid)"
    " SELECT pg_catalog.bool_or("
    "   (reach.oid OPERATOR(pg_catalog.=) reach.owner AND (r.rolsuper OR r.rolbypassrls OR r.rolreplication))"
    "   OR (r.rolname::pg_catalog.text OPERATOR(pg_catalog.=) ANY (%(server)s::pg_catalog.text[])"
    "     AND r.oid OPERATOR(pg_catalog.<) %(first)s::pg_catalog.oid))"
    " FROM reach JOIN pg_catalog.pg_roles r ON r.oid OPERATOR(pg_catalog.=) reach.oid"
)


def _unsafe_owners(cursor: psycopg.Cursor[tuple[Any, ...]], targets: list[_Resolved]) -> bool:
    """Some object is owned by a role with SUPERUSER, BYPASSRLS or REPLICATION (P90-01), or by a role that is or
    reaches ``pg_execute_server_program``, ``pg_read_server_files`` or ``pg_write_server_files`` (P90-02): a view of
    it reads with the owner's rights, a function of it may call what only a superuser may grant."""
    owners = sorted({target.owner for target in targets})
    cursor.execute(_UNSAFE_OWNER_SQL, {"owners": owners, "server": list(_SERVER_ACCESS), "first": _FIRST_NORMAL_OID})
    return bool(one_row(cursor)[0])


# The catalog and ACL column of each object kind (fixed names, composed with psycopg.sql.Identifier).
_ACL = {
    "table": ("pg_class", "relacl"),
    "sequence": ("pg_class", "relacl"),
    "function": ("pg_proc", "proacl"),
    "schema": ("pg_namespace", "nspacl"),
    "database": ("pg_database", "datacl"),
}


def _require_granted(
    cursor: psycopg.Cursor[tuple[Any, ...]], on: str, targets: list[_Resolved], privileges: tuple[str, ...], oid: int
) -> None:
    """After ``GRANT``: each privilege is in each object's ACL for the role. PostgreSQL only warns when the connecting
    role may not grant it ("no privileges were granted"); that is raised as ``PermissionDenied`` (I-186)."""
    from psycopg import sql

    catalog, column = _ACL[on]
    statement = sql.SQL(
        "SELECT pg_catalog.count(DISTINCT a.privilege_type)::int"
        " FROM {catalog} x, LATERAL pg_catalog.aclexplode(x.{acl}) a"
        " WHERE x.oid OPERATOR(pg_catalog.=) %s::pg_catalog.oid AND a.grantee OPERATOR(pg_catalog.=) %s::pg_catalog.oid"
        " AND a.privilege_type OPERATOR(pg_catalog.=) ANY (%s::pg_catalog.text[])"
    ).format(catalog=sql.Identifier("pg_catalog", catalog), acl=sql.Identifier(column))
    for target in targets:
        cursor.execute(statement, (target.oid, oid, list(privileges)))
        if int(one_row(cursor)[0]) != len(privileges):
            raise _denied("postgres.grant_not_allowed")


def _privilege_statement(
    verb: Literal["GRANT", "REVOKE"], privileges: tuple[str, ...], on: str, targets: list[_Resolved], role: str
) -> psycopg.sql.Composed:
    from psycopg import sql

    return sql.SQL("{verb} {privileges} ON {kind} {objects} {direction} {role}").format(
        verb=sql.SQL(verb),
        privileges=sql.SQL(", ").join(sql.SQL(privilege) for privilege in privileges),
        kind=sql.SQL(_KEYWORD[on]),
        objects=sql.SQL(", ").join(target.target for target in targets),
        direction=sql.SQL("TO" if verb == "GRANT" else "FROM"),
        role=sql.Identifier(role),
    )


def _local_search_path(cursor: psycopg.Cursor[tuple[Any, ...]]) -> None:
    """An empty ``search_path`` for this transaction only (``SET LOCAL``): ``to_regprocedure`` resolves the argument
    types in ``pg_catalog`` or by their schema, never in a schema the caller put first (I-16)."""
    cursor.execute("SELECT pg_catalog.set_config('search_path', '', true)")


def grant_privileges(
    connection: psycopg.Connection[Any],
    role: str,
    *,
    privileges: Sequence[
        Literal[
            "SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "USAGE", "CREATE", "CONNECT", "EXECUTE"
        ]
    ],
    on: Literal["table", "sequence", "function", "schema", "database"],
    objects: Sequence[str],
) -> RolePrivileges:
    """``GRANT <privileges> ON <kind> <objects> TO role`` (no ``WITH GRANT OPTION``); returns ``role``'s privileges.

    ``privileges`` must suit ``on`` (``table``: SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES; ``sequence``:
    USAGE, SELECT, UPDATE; ``function``: EXECUTE; ``schema``: USAGE, CREATE; ``database``: CONNECT, CREATE); TRIGGER is
    never grantable (K-2). ``objects`` of ``table``, ``sequence`` and ``function`` are schema-qualified names in SQL
    syntax (``app.orders``, ``app."Mixed Case"``, ``app.total(integer)``); of ``schema`` and ``database`` plain names.
    ``UsageError("postgres.invalid_argument")``, ``UsageError("postgres.object_missing")`` (details ``index``).
    Refused (``RoleUnsafe``): the target reasons of :func:`alter_role`; ``security_definer`` for EXECUTE on a
    SECURITY DEFINER function ``role`` does not own; ``schema_create`` for CREATE on ``public`` or on a schema ``role``
    does not own; ``system_object`` for an object of the system (an oid below 16384, ``information_schema`` or a
    ``pg_`` schema); ``object_owner`` for an object whose owner has SUPERUSER, BYPASSRLS or REPLICATION or is or
    reaches ``pg_execute_server_program``, ``pg_read_server_files`` or ``pg_write_server_files``. Contract section 4.9,
    I-183, I-186.
    """
    name = _check_name(role, "role")
    chosen = _check_privileges(privileges, on, _GRANTABLE)
    parsed = _check_objects(objects, on)
    _start(connection, "grant_privileges")
    version = connection.info.server_version

    def body() -> RolePrivileges:
        with _transaction(connection) as cursor:
            _check_caller(cursor)
            _local_search_path(cursor)
            before = _read_state(cursor, name, version)
            reasons = _target_reasons(before, increase=True)
            targets = _resolve(connection, cursor, on, parsed)
            if "EXECUTE" in chosen and any(t.security_definer and t.owner != before.oid for t in targets):
                reasons.append("security_definer")
            if on == "schema" and "CREATE" in chosen and any(t.public or t.owner != before.oid for t in targets):
                reasons.append("schema_create")
            if any(_system_object(t) for t in targets):
                reasons.append("system_object")
            if _unsafe_owners(cursor, targets):
                reasons.append("object_owner")
            _refuse(reasons)
            cursor.execute(_privilege_statement("GRANT", chosen, on, targets, name))
            _require_granted(cursor, on, targets, chosen, before.oid)
            after = _read_state(cursor, name, version)
            _refuse(_new_findings(before.privileges, after.privileges))
            return after.privileges

    return _translated(body)


def revoke_privileges(
    connection: psycopg.Connection[Any],
    role: str,
    *,
    privileges: Sequence[str],
    on: Literal["table", "sequence", "function", "schema", "database"],
    objects: Sequence[str],
) -> RolePrivileges:
    """``REVOKE <privileges> ON <kind> <objects> FROM role``; returns ``role``'s privileges after it.

    As :func:`grant_privileges`, and ``TRIGGER`` may be revoked on a table (it removes a K-2 finding). Removes the
    grants made by the connecting role (PostgreSQL's rule; check the result). Refused (``RoleUnsafe``): a predefined
    role (``predefined_role``), the session's own role (``current_user``). Contract section 4.9, I-186.
    """
    name = _check_name(role, "role")
    chosen = _check_privileges(privileges, on, _REVOCABLE)
    parsed = _check_objects(objects, on)
    _start(connection, "revoke_privileges")
    version = connection.info.server_version

    def body() -> RolePrivileges:
        with _transaction(connection) as cursor:
            _check_caller(cursor)
            _local_search_path(cursor)
            before = _read_state(cursor, name, version)
            _refuse(_target_reasons(before, increase=False))
            targets = _resolve(connection, cursor, on, parsed)
            cursor.execute(_privilege_statement("REVOKE", chosen, on, targets, name))
            return _read_state(cursor, name, version).privileges

    return _translated(body)
