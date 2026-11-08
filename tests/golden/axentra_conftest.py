"""Axentra's ``tests/conftest.py`` fixtures, verbatim, as the oracle of TAB-032 (TAB-T-X17).

Copied from peterkarlhuber/axentra ``tests/conftest.py`` (``_with_db``, ``pg_urls``, ``truncate_all``) at commit
1a59c84 (MIT, Copyright (c) 2026 Peter Karlhuber), in the split-roles variant (``AXENTRA_TEST_SPLIT_ROLES=1``, the
production layout). The SQL and the order of the statements are unchanged. Only the plumbing changed: ``pg_urls`` is
a generator taking the admin URL, the two role names and the migration as arguments (Axentra's are module constants
and ``MigrationRunner``), the grant is the verbatim Axentra ``grant_runtime_role`` of ``role_grants.py`` (its
connection form), and at the end it also drops the two roles it created: on a shared server the fixed names
``axentra_test_app``/``axentra_test_owner`` would collide with other runs, so the test passes names with the run's
prefix. ``state`` reads what a test sees of the result; ``axentra_conftest.json`` holds it for the copy, and the
plugin version (``tests/postgres/test_testing_database.py``) must produce the same.
"""

# The SQL below is Axentra's own; it is built only from psycopg.sql identifiers and fixed text.

from __future__ import annotations

from collections.abc import Callable, Generator
import json
from typing import Any
import uuid

import psycopg
from psycopg import sql

from tests.golden.role_grants import AXENTRA_COMMIT, axentra_grant_runtime_role

APP_PASSWORD = "axentra-test-pw"

__all__ = ["AXENTRA_COMMIT", "MIGRATION", "migrate", "pg_urls", "state", "truncate_all"]


def _with_db(url: str, dbname: str, user: str | None = None, password: str | None = None) -> str:
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    params = conninfo_to_dict(url)
    params["dbname"] = dbname
    if user:
        params["user"] = user
        params["password"] = password
    return make_conninfo(**params)  # type: ignore[arg-type]  # Axentra's line; the values are libpq's


def pg_urls(
    admin_url: str, app_role: str, owner_role: str, migrate: Callable[[str], None], dbname_prefix: str
) -> Generator[dict[str, str], None, None]:
    # Axentra's module constants, as locals.
    ADMIN_URL = admin_url
    APP_ROLE = app_role
    OWNER_ROLE = owner_role
    SPLIT_ROLES = True
    dbname = f"{dbname_prefix}{uuid.uuid4().hex[:8]}"
    roles = [APP_ROLE, OWNER_ROLE] if SPLIT_ROLES else [APP_ROLE]
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        for role in roles:
            exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
            if not exists:
                conn.execute(
                    sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD {}").format(
                        sql.Identifier(role), sql.Literal(APP_PASSWORD)
                    )
                )
        conn.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(dbname), sql.Identifier(roles[-1])))
    urls = {
        "admin": _with_db(ADMIN_URL, dbname),
        "app": _with_db(ADMIN_URL, dbname, APP_ROLE, APP_PASSWORD),
        "owner": _with_db(ADMIN_URL, dbname, roles[-1], APP_PASSWORD),
        "owner_role": roles[-1],
        "server_admin": ADMIN_URL,
        "dbname": dbname,
    }
    if SPLIT_ROLES:
        with psycopg.connect(urls["admin"], autocommit=True) as conn:
            conn.execute(sql.SQL("ALTER SCHEMA public OWNER TO {}").format(sql.Identifier(OWNER_ROLE)))
            conn.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    migrate(urls["owner"])
    if SPLIT_ROLES:
        with psycopg.connect(urls["owner"], autocommit=True) as conn:
            axentra_grant_runtime_role(conn, APP_ROLE, schema="public")
    try:
        yield urls
    finally:
        with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(dbname)))
            for role in roles:  # plumbing: the copy drops the roles it created (shared server)
                conn.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))


def truncate_all(admin_url: str) -> None:
    with psycopg.connect(admin_url, autocommit=True) as conn:
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename LIKE 'axentra_%%'"
                " AND tablename <> 'axentra_schema_migrations'"
            ).fetchall()
        ]
        if tables:
            conn.execute(sql.SQL("TRUNCATE {} CASCADE").format(sql.SQL(", ").join(sql.Identifier(t) for t in tables)))


# --- The stand-in migration and the snapshot (not Axentra's code) -----------------------------------------------------

MIGRATION = (
    "CREATE TABLE axentra_schema_migrations (version int PRIMARY KEY)",
    "INSERT INTO axentra_schema_migrations VALUES (1)",
    "CREATE TABLE axentra_tenants (id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, name text)",
    "CREATE TABLE axentra_users (id serial PRIMARY KEY, tenant bigint REFERENCES axentra_tenants, email text)",
    "ALTER TABLE axentra_users ENABLE ROW LEVEL SECURITY",
    "CREATE POLICY tenant_rows ON axentra_users USING (tenant = 1)",
    "CREATE TABLE other_data (id int)",
)
"""Axentra-shaped tables in ``public``: bookkeeping, two related tables (identity, serial, RLS) and one outside the
``axentra_`` pattern."""


def migrate(owner_url: str) -> None:
    with psycopg.connect(owner_url, autocommit=True) as conn:
        for statement in MIGRATION:
            conn.execute(statement)


def _fill(conn: psycopg.Connection[Any]) -> None:
    conn.execute("INSERT INTO axentra_tenants (name) VALUES ('a'), ('b')")
    conn.execute("INSERT INTO axentra_users (tenant, email) VALUES (1, 'x'), (2, 'y')")
    conn.execute("INSERT INTO other_data VALUES (1)")


def state(urls: dict[str, str], truncate: Callable[[str], None]) -> dict[str, Any]:
    """What a consumer test sees: roles, ownership, privileges, RLS, and the effect of ``truncate(urls["admin"])``.

    Role names are replaced by ``<owner>``/``<app>``; rows are sorted here (``ORDER BY`` depends on the collation).
    """
    from psycopg.conninfo import conninfo_to_dict

    owner, app = urls["owner_role"], str(conninfo_to_dict(urls["app"])["user"])
    names = {owner: "<owner>", app: "<app>"}

    def rows(conn: psycopg.Connection[Any], query: str, *params: Any) -> list[list[Any]]:
        found = [[names.get(v, v) if isinstance(v, str) else v for v in row] for row in conn.execute(query, params)]
        return sorted(found, key=json.dumps)

    result: dict[str, Any] = {}
    with psycopg.connect(urls["admin"], autocommit=True) as admin:
        result["roles"] = rows(
            admin,
            "SELECT rolname::text, rolsuper, rolbypassrls, rolcanlogin, rolcreaterole, rolcreatedb, rolreplication"
            " FROM pg_roles WHERE rolname = ANY (%s)",
            [owner, app],
        )
        result["database_owner"] = rows(
            admin, "SELECT pg_get_userbyid(datdba)::text FROM pg_database WHERE datname = current_database()"
        )
        result["public_schema"] = rows(
            admin,
            "SELECT pg_get_userbyid(nspowner)::text, has_schema_privilege('public', 'public', 'CREATE'),"
            " has_schema_privilege(%s, 'public', 'USAGE'), has_schema_privilege(%s, 'public', 'CREATE')"
            " FROM pg_namespace WHERE nspname = 'public'",
            app,
            app,
        )
        result["connect"] = rows(admin, "SELECT has_database_privilege(%s, current_database(), 'CONNECT')", app)
        result["table_privileges"] = rows(
            admin,
            "SELECT table_name::text, privilege_type::text FROM information_schema.role_table_grants"
            " WHERE grantee = %s AND table_schema = 'public'",
            app,
        )
        result["table_owners"] = rows(
            admin, "SELECT tablename::text, tableowner::text FROM pg_tables WHERE schemaname = 'public'"
        )
    with psycopg.connect(urls["owner"], autocommit=True) as conn:
        _fill(conn)
    with psycopg.connect(urls["app"], autocommit=True) as conn:
        result["app_sees_users"] = rows(conn, "SELECT email FROM axentra_users")  # RLS holds for the app
        try:
            conn.execute("DELETE FROM axentra_schema_migrations")
            result["app_writes_bookkeeping"] = True
        except psycopg.errors.InsufficientPrivilege:
            result["app_writes_bookkeeping"] = False
    truncate(urls["admin"])
    with psycopg.connect(urls["admin"], autocommit=True) as admin:
        result["rows_after_truncate"] = rows(
            admin,
            "SELECT 'axentra_schema_migrations', count(*) FROM axentra_schema_migrations"
            " UNION ALL SELECT 'axentra_tenants', count(*) FROM axentra_tenants"
            " UNION ALL SELECT 'axentra_users', count(*) FROM axentra_users"
            " UNION ALL SELECT 'other_data', count(*) FROM other_data",
        )
    return result
