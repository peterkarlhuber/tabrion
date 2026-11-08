"""PostgreSQL fixtures and helpers of ``tabrion.testing``.

Contract section 6; implemented in TAB-020 (schema per test) and TAB-032 (database per session, roles without
superuser, ``truncate_tables``, runtime-role mode). Every schema and role it creates starts with the prefix ``t_``
and is removed afterwards.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import suppress
from dataclasses import dataclass
import secrets
from typing import TYPE_CHECKING, Any

import pytest

from tabrion.testing import _config

if TYPE_CHECKING:
    import psycopg

    from tabrion.postgres import ExtensionStatus

__all__ = [
    "PostgresDatabaseHandle",
    "PostgresSchema",
    "RoleSetup",
    "TestRoles",
    "postgres_server_version",
    "require_extension",
    "tabrion_postgres_database",
    "tabrion_postgres_roles",
    "tabrion_postgres_schema",
    "tabrion_postgres_url",
    "tabrion_role_setup",
    "truncate_tables",
]


@dataclass(frozen=True, slots=True)
class PostgresSchema:
    name: str
    owner_url: str
    runtime_url: str | None
    """Set in runtime-role mode."""


@dataclass(frozen=True, slots=True)
class RoleSetup:
    """Roles of the throwaway database; all ``NOSUPERUSER NOBYPASSRLS``."""

    app: str | None = None
    owner: str | None = None
    split: bool = True
    """Two LOGIN roles without superuser: owner ``t_own_<hex>`` and app ``t_app_<hex>``."""


@dataclass(frozen=True, slots=True)
class PostgresDatabaseHandle:
    url: str
    name: str
    owner_url: str
    app_url: str


@dataclass(frozen=True, slots=True)
class TestRoles:
    __test__ = False  # not a test class

    owner: str
    app: str
    owner_url: str
    app_url: str


@pytest.fixture(scope="session")
def tabrion_postgres_url() -> str:
    """Admin or owner URL from the environment; skipped without it, an error when the require variable is set.

    The variable names come from :func:`tabrion.testing.configure`. Without psycopg the fixture skips too (an error
    under the require variable). The URL is never shown in a message (contract I-36).
    """
    return _postgres_url()


class _Url(str):
    """A conninfo whose ``repr`` masks the password (``tabrion.config.safe_url``).

    pytest shows the values of a failing test's fixtures with ``repr``; the URL itself is unchanged (contract I-36).
    """

    __slots__ = ()

    def __repr__(self) -> str:
        from tabrion.config import safe_url

        return repr(safe_url(str(self)))


def _postgres_url() -> str:
    settings = _config.current
    found = settings.first(settings.postgres_env)
    required = settings.flag(settings.require_env)
    if found is None:
        names = ", ".join(settings.postgres_env)
        if required:
            pytest.fail(
                f"{', '.join(settings.require_env)} requires PostgreSQL but none of {names} is set", pytrace=False
            )
        pytest.skip(f"PostgreSQL tests need one of {names}")
    try:
        import psycopg  # noqa: F401
    except ImportError:
        if required:
            pytest.fail('PostgreSQL is required but psycopg is missing: pip install "tabrion[postgres]"', pytrace=False)
        pytest.skip('PostgreSQL tests need psycopg: pip install "tabrion[postgres]"')
    return _Url(found[1])


def _with_params(url: str, **changes: Any) -> str:
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    params = conninfo_to_dict(url)
    params.update(changes)
    return _Url(make_conninfo("", **params))


def _new_name(kind: str = "") -> str:
    """``<schema_prefix><kind><hex12>``: lowercase, never needs quoting."""
    return f"{_config.current.schema_prefix}{kind}{secrets.token_hex(6)}"


def _search_path_url(url: str, schema: str) -> str:
    from psycopg.conninfo import conninfo_to_dict

    options = str(conninfo_to_dict(url).get("options") or "")
    return _with_params(url, options=f"{options} -c search_path={schema}".strip())


_DROP_LOCK_TIMEOUT = "30s"
"""How long a teardown ``DROP`` waits for a lock another role's session holds (it is never ended): an error, not a
hang."""


def _drop_schema(url: str, name: str) -> None:
    """``DROP SCHEMA ... CASCADE`` of a schema this run created, but never into an extension (review H-1).

    An extension in the schema is database-wide: the ``CASCADE`` would drop it and every column of its types in
    other schemas. Then the schema is left in place and the teardown fails with the extension's name.
    """
    import psycopg
    from psycopg import sql

    with psycopg.connect(url, autocommit=True, connect_timeout=10) as conn:
        # A connection of the test may still hold locks in the schema (an open transaction of a failed test); it
        # would block the DROP until it closes, after this teardown. End those sessions: only sessions of this
        # URL's role (review L-1; another role's session, a backup for example, is waited for instead), in this
        # database, holding a lock on the schema itself (a transaction that created an object in it; its
        # uncommitted relations are invisible here) or on one of its relations.
        with suppress(psycopg.Error):
            conn.execute(
                "SELECT pg_catalog.pg_terminate_backend(p.pid) FROM (SELECT DISTINCT l.pid"
                " FROM pg_catalog.pg_locks l, pg_catalog.pg_namespace n, pg_catalog.pg_stat_activity a"
                " WHERE n.nspname::pg_catalog.text OPERATOR(pg_catalog.=) %s::pg_catalog.text"
                " AND a.pid OPERATOR(pg_catalog.=) l.pid"
                " AND a.usesysid OPERATOR(pg_catalog.=) (SELECT r.oid FROM pg_catalog.pg_roles r"
                "   WHERE r.rolname OPERATOR(pg_catalog.=) CURRENT_USER)"
                " AND l.pid OPERATOR(pg_catalog.<>) pg_catalog.pg_backend_pid()"
                " AND l.database OPERATOR(pg_catalog.=) (SELECT d.oid FROM pg_catalog.pg_database d"
                "   WHERE d.datname OPERATOR(pg_catalog.=) pg_catalog.current_database())"
                " AND ((l.locktype OPERATOR(pg_catalog.=) 'object'"
                "     AND l.classid OPERATOR(pg_catalog.=) 'pg_catalog.pg_namespace'::pg_catalog.regclass"
                "     AND l.objid OPERATOR(pg_catalog.=) n.oid)"
                "   OR l.relation IN (SELECT c.oid FROM pg_catalog.pg_class c"
                "     WHERE c.relnamespace OPERATOR(pg_catalog.=) n.oid))) p",
                (name,),
            )
        with conn.transaction():
            conn.execute(sql.SQL("SET LOCAL lock_timeout = {}").format(sql.Literal(_DROP_LOCK_TIMEOUT)))
            extensions = [
                str(row[0])
                for row in conn.execute(
                    "SELECT e.extname::pg_catalog.text FROM pg_catalog.pg_extension e, pg_catalog.pg_namespace n"
                    " WHERE e.extnamespace OPERATOR(pg_catalog.=) n.oid"
                    " AND n.nspname::pg_catalog.text OPERATOR(pg_catalog.=) %s::pg_catalog.text",
                    (name,),
                ).fetchall()
            ]
            if extensions:
                pytest.fail(
                    f"tabrion.testing: the schema {name!r} holds the extension(s) {', '.join(sorted(extensions))};"
                    " an extension is database-wide and DROP SCHEMA ... CASCADE would also drop the columns of its"
                    " types in other schemas, so the schema is left in place. Drop the extension yourself, or let"
                    " require_extension create it (in the schema public)",
                    pytrace=False,
                )
            conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(name)))


@pytest.fixture
def tabrion_postgres_schema(tabrion_postgres_url: str) -> Iterator[PostgresSchema]:
    """``CREATE SCHEMA <prefix><hex12>`` per test, dropped afterwards; runtime role in runtime-role mode.

    ``owner_url`` is ``tabrion_postgres_url`` with ``-c search_path=<schema>`` added to its ``options``. The schema
    is created and dropped as the role of ``tabrion_postgres_url``, which needs ``CREATE`` on the database.
    """
    import psycopg
    from psycopg import sql

    settings = _config.current
    if settings.flag(settings.runtime_role_env):
        raise NotImplementedError("tabrion.testing: the runtime-role mode is implemented in TAB-032")
    name = _new_name()
    with psycopg.connect(tabrion_postgres_url, autocommit=True, connect_timeout=10) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name)))
    try:
        yield PostgresSchema(name=name, owner_url=_search_path_url(tabrion_postgres_url, name), runtime_url=None)
    finally:
        _drop_schema(tabrion_postgres_url, name)


@pytest.fixture(scope="session")
def tabrion_role_setup() -> RoleSetup | None:
    """The roles of ``tabrion_postgres_database``; override this fixture in ``conftest.py`` to change them (G-10).

    Default ``RoleSetup()``; ``None`` creates no roles (the admin URL owns the database). Example::

        @pytest.fixture(scope="session")
        def tabrion_role_setup() -> RoleSetup | None:
            return RoleSetup(app="axentra_app", owner="axentra_owner")
    """
    return RoleSetup()


@pytest.fixture(scope="session")
def tabrion_postgres_database(
    tabrion_postgres_url: str, tabrion_role_setup: RoleSetup | None
) -> Iterator[PostgresDatabaseHandle]:
    """``CREATE DATABASE t_<hex>`` per session (admin needs CREATEDB), ``DROP DATABASE ... WITH (FORCE)`` at the end.

    The roles follow the fixture ``tabrion_role_setup`` (doc 04 section 6: "parameter ``roles``"; G-T1 condition
    C-4).
    """
    raise NotImplementedError("tabrion.testing.tabrion_postgres_database: implemented in TAB-032")


@pytest.fixture(scope="session")
def tabrion_postgres_roles(tabrion_postgres_url: str) -> Iterator[TestRoles]:
    """The same role setup in an existing database."""
    raise NotImplementedError("tabrion.testing.tabrion_postgres_roles: implemented in TAB-032")


@pytest.fixture(scope="session")
def postgres_server_version(tabrion_postgres_url: str) -> int:
    """``server_version_num`` of the test server."""
    import psycopg

    with psycopg.connect(tabrion_postgres_url, autocommit=True, connect_timeout=10) as conn:
        return int(conn.info.server_version)


def truncate_tables(
    target: psycopg.Connection[Any] | str,
    *,
    schema: str | None = None,
    like: str | None = None,
    exclude: Sequence[str] = (),
) -> int:
    """``TRUNCATE ... RESTART IDENTITY CASCADE`` of the schema's tables in one statement; returns the table count."""
    raise NotImplementedError("tabrion.testing.truncate_tables: implemented in TAB-032")


_EXTENSION_HOME = "public"
"""Where ``require_extension`` creates a missing extension: a schema no fixture drops (review H-1)."""


def require_extension(conn: psycopg.Connection[Any], name: str) -> ExtensionStatus:
    """Make an extension usable on ``conn``, else skip; an error when ``name`` is in the require variable.

    Never moves an extension and never creates one in a schema a fixture drops (review H-1): an extension is
    database-wide, and a ``DROP SCHEMA ... CASCADE`` of its schema would also drop the columns of its types in other
    schemas.

    * ``installable``: ``CREATE EXTENSION IF NOT EXISTS ... SCHEMA public``; the extension stays in the database
      (another run may already use it). Two runs racing for it: the loser finds it installed.
    * ``other_schema`` (also after that ``CREATE``): the extension's schema is appended to the ``search_path`` of
      ``conn`` (``set_config(..., false)``; unqualified new objects still land in the first schema).

    Both run in ``conn.transaction()``: a transaction of its own on an idle connection, committed; a savepoint inside
    the caller's transaction, so its rollback also undoes them. A failure (missing privilege, missing package, no
    schema ``public``) is rolled back to that point and the test is skipped, or fails when ``name`` is listed in the
    require variable; the message names only the error class and SQLSTATE (contract I-36).
    """
    import psycopg
    from psycopg import sql

    from tabrion.postgres import extension_status

    status = extension_status(conn, name)  # validates the name
    error = ""
    if status.state == "installable":
        try:
            with conn.transaction():
                conn.execute(
                    sql.SQL("CREATE EXTENSION IF NOT EXISTS {} SCHEMA {}").format(
                        sql.Identifier(name), sql.Identifier(_EXTENSION_HOME)
                    )
                )
        except psycopg.Error as exc:
            error = f" ({type(exc).__name__}, SQLSTATE {getattr(exc, 'sqlstate', None) or '-'})"
        status = extension_status(conn, name)
    if status.state == "other_schema" and status.extension_schema is not None:
        try:
            with conn.transaction():
                # SET search_path = '' reads back as "" (an empty name); either way only the schema remains.
                conn.execute(
                    "SELECT pg_catalog.set_config('search_path', CASE"
                    " WHEN pg_catalog.current_setting('search_path')"
                    "   OPERATOR(pg_catalog.=) ANY (ARRAY['', '\"\"']::pg_catalog.text[])"
                    "   THEN pg_catalog.quote_ident(%(s)s::pg_catalog.text)"
                    " ELSE pg_catalog.current_setting('search_path') OPERATOR(pg_catalog.||) ', '"
                    "   OPERATOR(pg_catalog.||) pg_catalog.quote_ident(%(s)s::pg_catalog.text) END, false)",
                    {"s": status.extension_schema},
                )
        except psycopg.Error as exc:
            error = f" ({type(exc).__name__}, SQLSTATE {getattr(exc, 'sqlstate', None) or '-'})"
        status = extension_status(conn, name)
    if status.state == "active":
        return status
    message = f"the PostgreSQL extension {name!r} is {status.state} and could not be activated{error}"
    if name in _config.current.required_extensions():
        pytest.fail(f"{message}; it is required by {', '.join(_config.current.require_extensions_env)}", pytrace=False)
    pytest.skip(message)
