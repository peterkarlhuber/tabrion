"""The grant functions of Axentra and Nodestro, verbatim, as the test oracles of TAB-031 (TAB-T-R11, R12).

Copied from peterkarlhuber/axentra ``src/axentra/data/roles.py`` (``grant_runtime_role``, ``inspect_role``,
``TABLE_PRIVILEGES``, ``SEQUENCE_PRIVILEGES``) at commit 1a59c84, and from peterkarlhuber/nodestro-runtime
``src/nodestro_runtime/storage_postgres.py`` (``grant_runtime_role``, ``inspect_role``, ``RUNTIME_TABLE_PRIVILEGES``,
``SCHEMA_VERSION_TABLES``) at commit 6698265 (both MIT, Copyright (c) 2026 Peter Karlhuber). The SQL and the order
of the statements are unchanged. Only the plumbing changed: both take a psycopg connection (Axentra's URL variant and
Nodestro's ``PostgresDatabase`` wrapper are gone; Nodestro's ``owner._transaction()`` is ``connection.transaction()``
and its ``_query`` a cursor), and the errors are plain ``RuntimeError``s. ``role_grants.json`` holds the catalog
snapshot both produce on the fixture schema of ``tests/postgres/test_grants.py``; that test checks that these
copies still reproduce it, so an edit here cannot go unnoticed.
"""

# The SQL below is the references' own; it is built only from psycopg.sql identifiers and fixed text.

from __future__ import annotations

from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import tuple_row

AXENTRA_COMMIT = "1a59c84237f77bf9b699187e25005b1fe28f2a2f"
NODESTRO_COMMIT = "6698265c134b17a337ad0568da02361900050c70"

# --- Axentra src/axentra/data/roles.py ------------------------------------------------------------------------------

TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE"
SEQUENCE_PRIVILEGES = "USAGE, SELECT"


def axentra_inspect_role(conn: psycopg.Connection[Any], role: str, schema: str | None = None) -> dict[str, Any]:
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute("SELECT current_user, coalesce(%s, current_schema())", (schema,))
        owner, schema_name = cur.fetchone() or ("", "")
        cur.execute("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = %s", (role,))
        attrs = cur.fetchone()
        if attrs is None:
            raise RuntimeError(f"the database role {role!r} does not exist")
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
    owned = [str(name) for name, owned in tables if owned]
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


def axentra_grant_runtime_role(conn: psycopg.Connection[Any], role: str, *, schema: str | None = None) -> None:
    before = axentra_inspect_role(conn, role, schema)
    if role == before["owner"]:
        raise RuntimeError(f"{role!r} is the connecting (owner) role; name the separate runtime role")
    if not before["restricted"]:
        raise RuntimeError(f"the runtime role {role!r}: row-level security would not apply to it")
    target = sql.Identifier(role)
    nsp = sql.Identifier(before["schema"])
    with conn.transaction():
        for statement in (
            sql.SQL("GRANT USAGE ON SCHEMA {} TO {}"),
            sql.SQL("GRANT " + TABLE_PRIVILEGES + " ON ALL TABLES IN SCHEMA {} TO {}"),
            sql.SQL("GRANT " + SEQUENCE_PRIVILEGES + " ON ALL SEQUENCES IN SCHEMA {} TO {}"),
            sql.SQL("GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA {} TO {}"),
            sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA {} GRANT " + TABLE_PRIVILEGES + " ON TABLES TO {}"),
            sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA {} GRANT " + SEQUENCE_PRIVILEGES + " ON SEQUENCES TO {}"),
            sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA {} GRANT EXECUTE ON FUNCTIONS TO {}"),
        ):
            conn.execute(statement.format(nsp, target))
        # The migration bookkeeping is the owner's: the runtime role may read it (schema gate), never fake it.
        row = conn.execute(
            "SELECT to_regclass(format('%%I.%%I', %s::text, 'axentra_schema_migrations')) IS NOT NULL",
            (before["schema"],),
        ).fetchone()
        if row and row[0]:
            conn.execute(
                sql.SQL("REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON {}.axentra_schema_migrations FROM {}").format(
                    nsp, target
                )
            )
    if not before["connect"]:
        try:
            with conn.transaction():
                conn.execute(
                    sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(str(conn.info.dbname)), target)
                )
        except psycopg.errors.InsufficientPrivilege:
            pass  # reported: connect=False; the database owner grants it


# --- Nodestro src/nodestro_runtime/storage_postgres.py --------------------------------------------------------------

RUNTIME_TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE"
SCHEMA_VERSION_TABLES = ("nodestro_schema_migrations", "axentra_schema_migrations")


def nodestro_inspect_role(connection: psycopg.Connection[Any], role: str) -> dict[str, Any]:
    with connection.cursor(row_factory=tuple_row) as cur:
        rows = cur.execute(
            "SELECT current_user::text, current_schema()::text, current_database()::text, r.rolsuper, r.rolbypassrls"
            " FROM pg_roles r WHERE r.rolname = %s",
            (role,),
        ).fetchall()
        if not rows:
            raise RuntimeError(f"The database role {role!r} does not exist")
        owner, schema, dbname, superuser, bypass = rows[0]
        tables = cur.execute(
            "SELECT c.relname, pg_has_role(%s, c.relowner, 'MEMBER') FROM pg_class c"
            " JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE n.nspname = current_schema() AND c.relkind IN ('r', 'p') ORDER BY c.relname",
            (role,),
        ).fetchall()
        privileges = cur.execute(
            "SELECT has_database_privilege(%s, current_database(), 'CONNECT'),"
            " has_schema_privilege(%s, current_schema(), 'CREATE')",
            (role, role),
        ).fetchall()
    connect, create = privileges[0] if privileges else (False, False)
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


def nodestro_grant_runtime_role(owner: psycopg.Connection[Any], role: str) -> None:
    report = nodestro_inspect_role(owner, role)
    problems = [
        reason
        for reason, flag in (
            ("is the owner role itself", role == report["owner"]),
            ("is a superuser", report["superuser"]),
            ("has BYPASSRLS", report["bypass_rls"]),
            (f"owns tables ({', '.join(report['owned_tables'][:5])})", bool(report["owned_tables"])),
        )
        if flag
    ]
    if problems:
        raise RuntimeError(f"The runtime role {role!r} {' and '.join(problems)}")
    target = sql.Identifier(role)
    schema = sql.Identifier(report["schema"])
    with owner.transaction():
        connection = owner
        for statement in (
            "GRANT USAGE ON SCHEMA {schema} TO {role}",
            "GRANT " + RUNTIME_TABLE_PRIVILEGES + " ON ALL TABLES IN SCHEMA {schema} TO {role}",
            "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {schema} TO {role}",
            "GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA {schema} TO {role}",
            "ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} GRANT " + RUNTIME_TABLE_PRIVILEGES + " ON TABLES TO {role}",
            "ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} GRANT USAGE, SELECT ON SEQUENCES TO {role}",
            "ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} GRANT EXECUTE ON FUNCTIONS TO {role}",
        ):
            connection.execute(sql.SQL(statement).format(schema=schema, role=target))
        # The schema versions are the migration runners' (owner role): the runtime role reads them for the schema
        # gate and cannot fake them.
        for table in SCHEMA_VERSION_TABLES:
            if connection.execute("SELECT to_regclass(%s) IS NOT NULL", (table,)).fetchone()[0]:  # type: ignore[index]
                connection.execute(
                    sql.SQL("REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON {} FROM {}").format(
                        sql.Identifier(table), target
                    )
                )
    if not report["connect"]:
        try:
            with owner.transaction():
                owner.execute(
                    sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(report["database"]), target)
                )
        except psycopg.errors.InsufficientPrivilege:
            pass  # reported as connect: false; the database's owner grants it
