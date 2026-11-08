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
    """Admin or owner URL from the environment; skipped without it, an error when the require variable is ``1``.

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
            pytest.fail(f"{', '.join(settings.require_env)}=1 but none of {names} is set", pytrace=False)
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


def _drop_schema(url: str, name: str) -> None:
    import psycopg
    from psycopg import sql

    with psycopg.connect(url, autocommit=True, connect_timeout=10) as conn:
        # A connection of the test may still hold locks in the schema (an open transaction of a failed test); it
        # would block the DROP until it closes, after this teardown. End those sessions (only ones holding locks in
        # this schema, which this fixture created).
        with suppress(psycopg.Error):
            # A lock on a relation of the schema, or on the schema itself (a transaction that created an object in
            # it; its uncommitted relations are invisible here).
            conn.execute(
                "SELECT pg_catalog.pg_terminate_backend(p.pid) FROM (SELECT DISTINCT l.pid"
                " FROM pg_catalog.pg_locks l, pg_catalog.pg_namespace n"
                " WHERE n.nspname::pg_catalog.text OPERATOR(pg_catalog.=) %s::pg_catalog.text"
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


def require_extension(conn: psycopg.Connection[Any], name: str) -> ExtensionStatus:
    """Try ``CREATE EXTENSION`` when not active, else skip; an error when ``name`` is in the require variable.

    ``other_schema`` and ``installable`` run the ``command`` of :func:`tabrion.postgres.extension_status` (``ALTER
    EXTENSION ... SET SCHEMA`` or ``CREATE EXTENSION ... SCHEMA <current schema>``) in ``conn.transaction()``: a
    transaction of its own on an idle connection, committed; a savepoint inside the caller's transaction. A failure
    (missing privilege, missing package) is rolled back to that point and the test is skipped, or fails when ``name``
    is listed in the require variable (contract I-36).
    """
    import psycopg

    from tabrion.postgres import extension_status

    status = extension_status(conn, name)  # validates the name
    error = ""
    if status.state in ("other_schema", "installable") and status.command is not None:
        try:
            with conn.transaction():
                conn.execute(status.command.rstrip(";").encode())
        except psycopg.Error as exc:
            error = f" ({type(exc).__name__}, SQLSTATE {getattr(exc, 'sqlstate', None) or '-'})"
        status = extension_status(conn, name)
    if status.state == "active":
        return status
    message = f"the PostgreSQL extension {name!r} is {status.state} and could not be activated{error}"
    if name in _config.current.required_extensions():
        pytest.fail(f"{message}; it is required by {', '.join(_config.current.require_extensions_env)}", pytrace=False)
    pytest.skip(message)
