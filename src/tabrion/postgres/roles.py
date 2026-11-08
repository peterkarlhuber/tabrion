"""Database role checks and grants (layer A, extra ``postgres``).

Contract: ``docs/api/contract.md`` sections 4.5 and 4.6. ``role_info``, ``check_role`` and ``inspect_role``
(TAB-016) and ``grant_runtime_role`` (TAB-031). Database roles only (owner, runtime, backup); no application, tenant
or user model (TAB-ADR-0010).

Every function here except ``grant_runtime_role`` only reads. On a handed-in connection it follows rule T-NOTX:
its own ``tuple_row`` cursor, no change of the row factory or of ``autocommit``, a rollback only of the implicit
transaction it opened itself, and a savepoint in the caller's running transaction, so a failing statement does not
abort it. ``grant_runtime_role`` needs a transaction of its own and refuses to run inside the caller's.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from tabrion.errors import RoleMissing, RoleUnsafe, UsageError
from tabrion.postgres._common import one_row, reading, require_connection, settle

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


# Contract I-20 (review finding H-1): a role is as privileged as every role it can become. ``rolsuper`` and
# ``rolbypassrls`` are not inherited, but a member of a role may ``SET ROLE`` to it (NOINHERIT members too), and a
# session may ``RESET ROLE`` to its ``SESSION_USER``. So ``superuser``, ``bypass_rls`` and ``table_owner`` hold when
# CURRENT_USER or SESSION_USER is a member (pg_has_role ... 'MEMBER', direct or indirect) of a superuser role, a
# BYPASSRLS role or the owner of a table (relkind r, p) in scope. A superuser is a member of every role.
#
# Every type and operator is qualified (contract I-16, review finding M-2). Two fixed statements; nothing is
# formatted into them.
_ROLE_INFO_HEAD = (
    "SELECT CURRENT_USER::pg_catalog.text,"
    " EXISTS (SELECT 1 FROM pg_catalog.pg_roles r WHERE r.rolsuper"
    "   AND (pg_catalog.pg_has_role(CURRENT_USER, r.oid, 'MEMBER')"
    "     OR pg_catalog.pg_has_role(SESSION_USER, r.oid, 'MEMBER'))),"
    " EXISTS (SELECT 1 FROM pg_catalog.pg_roles r WHERE r.rolbypassrls"
    "   AND (pg_catalog.pg_has_role(CURRENT_USER, r.oid, 'MEMBER')"
    "     OR pg_catalog.pg_has_role(SESSION_USER, r.oid, 'MEMBER'))),"
    " EXISTS (SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n"
    "   ON n.oid OPERATOR(pg_catalog.=) c.relnamespace"
    "   WHERE (c.relkind OPERATOR(pg_catalog.=) 'r' OR c.relkind OPERATOR(pg_catalog.=) 'p') AND "
)
_ROLE_INFO_TAIL = (
    "   AND (pg_catalog.pg_has_role(CURRENT_USER, c.relowner, 'MEMBER')"
    "     OR pg_catalog.pg_has_role(SESSION_USER, c.relowner, 'MEMBER')))"
)
_ROLE_INFO_SQL = {
    # Nodestro storage_postgres.py:757-768: every table of current_schema().
    "schema": _ROLE_INFO_HEAD + "n.nspname OPERATOR(pg_catalog.=) pg_catalog.current_schema()" + _ROLE_INFO_TAIL,
    # Axentra database.py:359-382: tables with row-level security in current_schemas(false).
    "rls": _ROLE_INFO_HEAD
    + "c.relrowsecurity AND n.nspname OPERATOR(pg_catalog.=) ANY (pg_catalog.current_schemas(false))"
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
    """The current role's privileges; ``owner_scope="schema"`` (Nodestro) or ``"rls"`` (Axentra).

    Each flag covers every role ``CURRENT_USER`` or ``SESSION_USER`` can become with ``SET ROLE`` (contract I-20).
    """
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
    ``""`` and finds no tables; a ``schema`` that does not exist is named as given, with no tables and
    ``can_create=False``. ``superuser`` and ``bypass_rls`` hold when ``role`` is a member of a superuser or
    ``BYPASSRLS`` role (contract I-20).
    """
    if not isinstance(role, str):
        raise TypeError("role must be a str")
    if schema is not None and not isinstance(schema, str):
        raise TypeError("schema must be a str or None")
    with reading(connection, "inspect_role") as cursor:
        # The schema is looked up by its exact name; a schema that does not exist has no oid, no tables and no CREATE
        # privilege (review finding M-1: has_schema_privilege would raise on its name).
        cursor.execute(
            "SELECT CURRENT_USER::pg_catalog.text,"
            " COALESCE(%(schema)s::pg_catalog.text, pg_catalog.current_schema()::pg_catalog.text),"
            " pg_catalog.current_database()::pg_catalog.text,"
            " (SELECT n.oid FROM pg_catalog.pg_namespace n WHERE n.nspname::pg_catalog.text OPERATOR(pg_catalog.=)"
            "   COALESCE(%(schema)s::pg_catalog.text, pg_catalog.current_schema()::pg_catalog.text))",
            {"schema": schema},
        )
        owner, schema_name, database, schema_oid = one_row(cursor)
        # Superuser and BYPASSRLS over every role ``role`` can become (contract I-20, review finding H-1).
        cursor.execute(
            "SELECT r.oid,"
            " EXISTS (SELECT 1 FROM pg_catalog.pg_roles s WHERE s.rolsuper"
            "   AND pg_catalog.pg_has_role(r.oid, s.oid, 'MEMBER')),"
            " EXISTS (SELECT 1 FROM pg_catalog.pg_roles s WHERE s.rolbypassrls"
            "   AND pg_catalog.pg_has_role(r.oid, s.oid, 'MEMBER'))"
            " FROM pg_catalog.pg_roles r WHERE r.rolname::pg_catalog.text OPERATOR(pg_catalog.=) %s::pg_catalog.text",
            (role,),
        )
        attributes = cursor.fetchone()
        if attributes is None:
            # The role name stays out of the error: the caller has it (Axentra and Nodestro map the code to their
            # own text).
            raise RoleMissing("postgres.role_missing")
        role_oid = attributes[0]
        tables: list[tuple[Any, ...]] = []
        if schema_oid is not None:
            cursor.execute(
                "SELECT c.relname::pg_catalog.text, pg_catalog.pg_has_role(%s::pg_catalog.oid, c.relowner, 'MEMBER')"
                " FROM pg_catalog.pg_class c WHERE c.relnamespace OPERATOR(pg_catalog.=) %s::pg_catalog.oid"
                " AND (c.relkind OPERATOR(pg_catalog.=) 'r' OR c.relkind OPERATOR(pg_catalog.=) 'p')"
                " ORDER BY c.relname",
                (role_oid, schema_oid),
            )
            tables = cursor.fetchall()
        cursor.execute(
            "SELECT pg_catalog.has_database_privilege(%s::pg_catalog.oid,"
            " pg_catalog.current_database()::pg_catalog.text, 'CONNECT'),"
            " COALESCE(pg_catalog.has_schema_privilege(%s::pg_catalog.oid, %s::pg_catalog.oid, 'CREATE'), false)",
            (role_oid, role_oid, schema_oid),
        )
        privileges = one_row(cursor)
    return RoleReport(
        role=role,
        owner=str(owner),
        schema=str(schema_name) if schema_name is not None else "",
        database=str(database),
        tables=len(tables),
        connect=bool(privileges[0]),
        superuser=bool(attributes[1]),
        bypass_rls=bool(attributes[2]),
        owned_tables=tuple(str(name) for name, owned in tables if owned),
        can_create=bool(privileges[1]),
    )


_TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE"
_SEQUENCE_PRIVILEGES = "USAGE, SELECT"
# Axentra roles.py:31-32 and Nodestro storage_postgres.py:162 (the same privileges); the statements are those of
# both references (Axentra roles.py:150-167, Nodestro storage_postgres.py:917-928), in their order.
_GRANTS = (
    "GRANT USAGE ON SCHEMA {schema} TO {role}",
    "GRANT " + _TABLE_PRIVILEGES + " ON ALL TABLES IN SCHEMA {schema} TO {role}",
    "GRANT " + _SEQUENCE_PRIVILEGES + " ON ALL SEQUENCES IN SCHEMA {schema} TO {role}",
    "GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA {schema} TO {role}",
    "ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} GRANT " + _TABLE_PRIVILEGES + " ON TABLES TO {role}",
    "ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} GRANT " + _SEQUENCE_PRIVILEGES + " ON SEQUENCES TO {role}",
    "ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} GRANT EXECUTE ON FUNCTIONS TO {role}",
)


_NAME_BYTES = 63
"""``NAMEDATALEN - 1``: PostgreSQL truncates a longer name, so the grants would name another object (review L-2)."""


def _grant_arguments(role: object, schema: object, protected_tables: object, grant_connect: object) -> tuple[str, ...]:
    if not isinstance(role, str) or not role:
        raise TypeError("role must be a non-empty str")
    if schema is not None and (not isinstance(schema, str) or not schema):
        raise TypeError("schema must be a non-empty str or None")
    if isinstance(protected_tables, str | bytes) or not isinstance(protected_tables, Sequence):
        raise TypeError("protected_tables must be a sequence of table names (not a str)")
    tables = tuple(protected_tables)
    if not all(isinstance(table, str) and table for table in tables):
        raise TypeError("protected_tables must hold non-empty str table names")
    if not isinstance(grant_connect, bool):
        raise TypeError("grant_connect must be a bool")
    # The names stay out of the messages (the caller has them).
    for argument, names in (("role", (role,)), ("schema", (schema,) if schema else ()), ("protected_tables", tables)):
        if any(len(name.encode("utf-8")) > _NAME_BYTES for name in names):
            raise ValueError(f"{argument}: a name longer than {_NAME_BYTES} bytes (PostgreSQL would truncate it)")
    return tables


def grant_runtime_role(
    connection: psycopg.Connection[Any],
    role: str,
    *,
    schema: str | None = None,
    protected_tables: Sequence[str] = (),
    grant_connect: bool = True,
) -> RoleReport:
    """Grant DML-only runtime privileges in one transaction (idempotent); ``RoleMissing``, ``RoleUnsafe``.

    Run it as the owner role (the role that runs the migrations). Unites Axentra ``grant_runtime_role``
    (``roles.py:107-190``) and Nodestro's (``storage_postgres.py:888-944``): ``USAGE`` on the schema, ``SELECT,
    INSERT, UPDATE, DELETE`` on every table, ``USAGE, SELECT`` on every sequence and ``EXECUTE`` on every function of
    the schema, the same as the connecting role's default privileges in the schema, and ``REVOKE INSERT, UPDATE,
    DELETE, TRUNCATE`` on each of ``protected_tables`` that exists in the schema (the migration bookkeeping stays
    read-only for the runtime role). Then ``GRANT CONNECT ON DATABASE`` in a transaction of its own when
    ``grant_connect`` holds and the role may not connect yet; an owner without the right to grant it leaves
    ``connect=False`` in the result. Returns a fresh :func:`inspect_role`.

    Refused before anything is granted (``RoleUnsafe``, details ``reasons``): ``table_owner`` when ``role`` is the
    connecting role or can become it (``SET ROLE``, contract I-20), owns the schema or can become its owner, or owns
    tables of the schema; ``superuser`` and ``bypass_rls`` as :func:`inspect_role` reports them (also through
    membership). ``RoleMissing`` for an unknown role, ``UsageError("postgres.schema_missing")`` for a schema that
    does not exist. Needs a transaction of its own: ``UsageError("postgres.nested_transaction")`` while the caller's
    transaction runs (contract T-NOTX, I-35). ``ValueError`` when neither ``schema`` nor a current schema names one,
    and for a name longer than 63 bytes (PostgreSQL would truncate it to another object's name).

    Check the result, not only for exceptions: ``connect`` is ``False`` when the owner may not grant ``CONNECT``
    (PostgreSQL only warns), and ``can_create=True`` (``CREATE`` on the schema, granted by someone else) is not
    restricted, although ``restricted`` does not include it: such a role can plant objects in the schema.
    """
    tables = _grant_arguments(role, schema, protected_tables, grant_connect)
    require_connection(connection, "grant_runtime_role")
    import psycopg
    from psycopg import pq, sql
    from psycopg.rows import tuple_row

    settle(connection)  # pipeline mode: the status is known only after a sync (contract I-34)
    if connection.info.transaction_status != pq.TransactionStatus.IDLE:
        raise UsageError("postgres.nested_transaction")
    before = inspect_role(connection, role, schema=schema)
    if not before.schema:
        raise ValueError("grant_runtime_role needs a schema: pass schema= or connect with a search_path")
    with reading(connection, "grant_runtime_role") as cursor:
        # Whether the role is, or can become (NOINHERIT members too), the connecting (owner) role, which owns or will
        # own the tables (D-46), or the owner of the schema, which may drop or alter every object in it (review M-1,
        # D-49); and whether the schema exists (review L-4).
        cursor.execute(
            "SELECT pg_catalog.pg_has_role(r.oid, CURRENT_USER, 'MEMBER'), n.oid IS NOT NULL,"
            " COALESCE(pg_catalog.pg_has_role(r.oid, n.nspowner, 'MEMBER'), false)"
            " FROM pg_catalog.pg_roles r LEFT JOIN pg_catalog.pg_namespace n"
            "   ON n.nspname::pg_catalog.text OPERATOR(pg_catalog.=) %s::pg_catalog.text"
            " WHERE r.rolname::pg_catalog.text OPERATOR(pg_catalog.=) %s::pg_catalog.text",
            (before.schema, role),
        )
        row = cursor.fetchone()
    if row is None:
        raise RoleMissing("postgres.role_missing")  # dropped since inspect_role
    if not row[1]:
        raise UsageError("postgres.schema_missing")  # the name stays out of the error (D-50)
    reasons = [
        reason
        for reason, flag in (
            ("superuser", before.superuser),
            ("bypass_rls", before.bypass_rls),
            ("table_owner", role == before.owner or bool(row[0]) or bool(row[2]) or bool(before.owned_tables)),
        )
        if flag
    ]
    if reasons:
        raise RoleUnsafe("postgres.role_unsafe", details={"reasons": ",".join(reasons)})
    target = sql.Identifier(role)
    namespace = sql.Identifier(before.schema)
    with connection.transaction(), psycopg.Cursor(connection, row_factory=tuple_row) as cursor:
        for statement in _GRANTS:
            cursor.execute(sql.SQL(statement).format(schema=namespace, role=target))
        for table in tables:
            # Looked up by exact name in the schema (no search_path, no identifier parsing).
            cursor.execute(
                "SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n"
                " ON n.oid OPERATOR(pg_catalog.=) c.relnamespace"
                " WHERE n.nspname::pg_catalog.text OPERATOR(pg_catalog.=) %s::pg_catalog.text"
                " AND c.relname::pg_catalog.text OPERATOR(pg_catalog.=) %s::pg_catalog.text",
                (before.schema, table),
            )
            if cursor.fetchone() is not None:
                cursor.execute(
                    sql.SQL("REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON {}.{} FROM {}").format(
                        namespace, sql.Identifier(table), target
                    )
                )
    if grant_connect and not before.connect:
        try:
            with connection.transaction(), psycopg.Cursor(connection, row_factory=tuple_row) as cursor:
                cursor.execute(
                    sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(before.database), target)
                )
        except psycopg.errors.InsufficientPrivilege:
            pass  # reported as connect=False; the database's owner grants it
    return inspect_role(connection, role, schema=before.schema)
