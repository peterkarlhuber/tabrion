"""Database role checks and grants (layer A, extra ``postgres``).

Contract: ``docs/api/contract.md`` sections 4.5 and 4.6. ``role_info``, ``check_role`` and ``inspect_role`` are
implemented (TAB-016); ``grant_runtime_role`` is still a stub (TAB-031). Database roles only (owner, runtime,
backup); no application, tenant or user model (TAB-ADR-0010).

Every function here only reads. On a handed-in connection it follows rule T-NOTX: its own ``tuple_row`` cursor, no
change of the row factory or of ``autocommit``, and a rollback only of the implicit transaction it opened itself.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from tabrion.errors import RoleMissing, RoleUnsafe
from tabrion.postgres._common import one_row, reading

if TYPE_CHECKING:
    import psycopg

__all__ = ["RoleInfo", "RoleReport", "check_role", "grant_runtime_role", "inspect_role", "role_info"]


@dataclass(frozen=True, slots=True)
class RoleInfo:
    role: str
    superuser: bool
    bypass_rls: bool
    table_owner: bool

    @property
    def restricted(self) -> bool:
        """``not (superuser or bypass_rls or table_owner)``."""
        return not (self.superuser or self.bypass_rls or self.table_owner)


# Tables (relkind r, p) the current role owns or may act as the owner of (pg_has_role ... 'MEMBER'). A superuser is
# a member of every role, so it owns every table in scope. Two fixed statements; nothing is formatted into them.
_ROLE_INFO_HEAD = (
    "SELECT u.name::text, COALESCE(r.rolsuper, false), COALESCE(r.rolbypassrls, false),"
    " EXISTS (SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace"
    "   WHERE c.relkind IN ('r', 'p') AND "
)
_ROLE_INFO_TAIL = (
    "   AND pg_catalog.pg_has_role(u.name, c.relowner, 'MEMBER'))"
    " FROM (SELECT CURRENT_USER) AS u (name)"
    " LEFT JOIN pg_catalog.pg_roles r ON r.rolname = u.name"
)
_ROLE_INFO_SQL = {
    # Nodestro storage_postgres.py:757-768: every table of current_schema().
    "schema": _ROLE_INFO_HEAD + "n.nspname = pg_catalog.current_schema()" + _ROLE_INFO_TAIL,
    # Axentra database.py:359-382: tables with row-level security in current_schemas(false).
    "rls": _ROLE_INFO_HEAD
    + "c.relrowsecurity AND n.nspname = ANY (pg_catalog.current_schemas(false))"
    + _ROLE_INFO_TAIL,
}


def _role_info_sql(owner_scope: object) -> str:
    if owner_scope not in ("schema", "rls"):
        raise ValueError('owner_scope must be "schema" or "rls"')
    return _ROLE_INFO_SQL[str(owner_scope)]


def _read_role_info(cursor: psycopg.Cursor[tuple[Any, ...]], owner_scope: str) -> RoleInfo:
    cursor.execute(_role_info_sql(owner_scope))
    row = one_row(cursor)
    return RoleInfo(role=str(row[0]), superuser=bool(row[1]), bypass_rls=bool(row[2]), table_owner=bool(row[3]))


def role_info(
    connection: psycopg.Connection[Any],
    *,
    owner_scope: Literal["schema", "rls"] = "schema",
) -> RoleInfo:
    """The current role's privileges; ``owner_scope="schema"`` (Nodestro) or ``"rls"`` (Axentra)."""
    _role_info_sql(owner_scope)  # validate before touching the connection
    with reading(connection, "role_info") as cursor:
        return _read_role_info(cursor, owner_scope)


def check_role(
    connection: psycopg.Connection[Any],
    *,
    allow_superuser: bool = False,
    allow_table_owner: bool = False,
    owner_scope: Literal["schema", "rls"] = "schema",
) -> RoleInfo:
    """Start check for applications: ``RoleUnsafe`` (details ``reasons``) unless explicitly allowed.

    ``allow_superuser`` allows a superuser or ``BYPASSRLS`` role and implies ``allow_table_owner`` (Axentra
    ``database.py:384-405``). ``details["reasons"]`` lists what is not allowed, comma separated, from
    ``superuser``, ``bypass_rls`` and ``table_owner``.
    """
    for name, value in (("allow_superuser", allow_superuser), ("allow_table_owner", allow_table_owner)):
        if not isinstance(value, bool):
            raise TypeError(f"{name} must be a bool")
    info = role_info(connection, owner_scope=owner_scope)
    reasons: list[str] = []
    if not allow_superuser:
        if info.superuser:
            reasons.append("superuser")
        if info.bypass_rls:
            reasons.append("bypass_rls")
    if info.table_owner and not (allow_table_owner or allow_superuser):
        reasons.append("table_owner")
    if reasons:
        raise RoleUnsafe("postgres.role_unsafe", details={"reasons": ",".join(reasons)})
    return info


@dataclass(frozen=True, slots=True)
class RoleReport:
    role: str
    owner: str
    """``current_user`` of the owner connection."""
    schema: str
    database: str
    tables: int
    connect: bool
    superuser: bool
    bypass_rls: bool
    owned_tables: tuple[str, ...]
    can_create: bool

    @property
    def restricted(self) -> bool:
        """``not (superuser or bypass_rls or owned_tables)``: row-level security fully applies to the role."""
        return not (self.superuser or self.bypass_rls or self.owned_tables)

    def as_dict(self) -> dict[str, object]:
        """Keys of both existing formats (Nodestro dict and Axentra ``as_dict``)."""
        return {
            "role": self.role,
            "owner": self.owner,
            "schema": self.schema,
            "database": self.database,
            "tables": self.tables,
            "superuser": self.superuser,
            "bypass_rls": self.bypass_rls,
            "owned_tables": list(self.owned_tables),
            "connect": self.connect,
            "can_create": self.can_create,
            "restricted": self.restricted,
        }


def inspect_role(connection: psycopg.Connection[Any], role: str, *, schema: str | None = None) -> RoleReport:
    """Read-only report on ``role``; ``RoleMissing`` when it does not exist.

    ``schema`` defaults to ``current_schema()`` of the connection (Axentra ``roles.py:72-104``); ``owner`` is the
    connection's ``current_user`` and ``database`` its ``current_database()`` (Nodestro
    ``storage_postgres.py:947-984``). Without a current schema and without ``schema`` the report names the schema
    ``""`` and finds no tables.
    """
    if not isinstance(role, str):
        raise TypeError("role must be a str")
    if schema is not None and not isinstance(schema, str):
        raise TypeError("schema must be a str or None")
    with reading(connection, "inspect_role") as cursor:
        cursor.execute(
            "SELECT CURRENT_USER::text, COALESCE(%s, pg_catalog.current_schema())::text,"
            " pg_catalog.current_database()::text",
            (schema,),
        )
        owner, schema_name, database = one_row(cursor)
        cursor.execute("SELECT r.rolsuper, r.rolbypassrls FROM pg_catalog.pg_roles r WHERE r.rolname = %s", (role,))
        attributes = cursor.fetchone()
        if attributes is None:
            # The role name stays out of the error: the caller has it (Axentra and Nodestro map the code to their
            # own text).
            raise RoleMissing("postgres.role_missing")
        cursor.execute(
            "SELECT c.relname::text, pg_catalog.pg_has_role(%s, c.relowner, 'MEMBER')"
            " FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace"
            " WHERE n.nspname = %s AND c.relkind IN ('r', 'p') ORDER BY c.relname",
            (role, schema_name),
        )
        tables = cursor.fetchall()
        cursor.execute(
            "SELECT pg_catalog.has_database_privilege(%s, pg_catalog.current_database(), 'CONNECT'),"
            " pg_catalog.has_schema_privilege(%s, %s, 'CREATE')",
            (role, role, schema_name),
        )
        privileges = one_row(cursor)
    return RoleReport(
        role=role,
        owner=str(owner),
        schema=str(schema_name) if schema_name is not None else "",
        database=str(database),
        tables=len(tables),
        connect=bool(privileges[0]),
        superuser=bool(attributes[0]),
        bypass_rls=bool(attributes[1]),
        owned_tables=tuple(str(name) for name, owned in tables if owned),
        can_create=bool(privileges[1]),
    )


def grant_runtime_role(
    connection: psycopg.Connection[Any],
    role: str,
    *,
    schema: str | None = None,
    protected_tables: Sequence[str] = (),
    grant_connect: bool = True,
) -> RoleReport:
    """Grant DML-only runtime privileges in one transaction (idempotent); ``RoleMissing``, ``RoleUnsafe``.

    Needs a transaction of its own: ``UsageError("...nested_transaction")`` inside a caller's transaction.
    """
    raise NotImplementedError("tabrion.postgres.grant_runtime_role: implemented in TAB-031")
