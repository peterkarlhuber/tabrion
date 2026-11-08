"""PostgreSQL fixtures and helpers of ``tabrion.testing``.

Contract section 6; implemented in TAB-020 (schema per test) and TAB-032 (database per session, roles without
superuser, ``truncate_tables``, runtime-role mode). Every schema and role it creates starts with the prefix ``t_``
and is removed afterwards.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest

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
    """Admin or owner URL from the environment; skipped without it, an error when the require variable is ``1``."""
    raise NotImplementedError("tabrion.testing.tabrion_postgres_url: implemented in TAB-020")


@pytest.fixture
def tabrion_postgres_schema(tabrion_postgres_url: str) -> Iterator[PostgresSchema]:
    """``CREATE SCHEMA t_<hex12>`` per test, dropped afterwards; runtime role in runtime-role mode."""
    raise NotImplementedError("tabrion.testing.tabrion_postgres_schema: implemented in TAB-020")


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
    raise NotImplementedError("tabrion.testing.postgres_server_version: implemented in TAB-020")


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
    """Try ``CREATE EXTENSION`` when not active, else skip; an error when ``name`` is in the require variable."""
    raise NotImplementedError("tabrion.testing.require_extension: implemented in TAB-020")
