"""Test fixtures and helpers (extra ``testing``). Activate explicitly: ``pytest_plugins = ["tabrion.testing"]``.

There is no ``pytest11`` entry point, so the fixtures never appear in foreign test runs. Contract section 6; stub
for the API freeze G-T1, implemented in TAB-020, TAB-021 and TAB-032.
"""

from __future__ import annotations

from collections.abc import Sequence

try:
    import pytest as _pytest  # noqa: F401  # the extra "testing"
except ImportError:
    raise ImportError('tabrion.testing needs the extra "testing": pip install "tabrion[testing]"') from None

from tabrion.testing import profiles
from tabrion.testing.postgres import (
    PostgresDatabaseHandle,
    PostgresSchema,
    RoleSetup,
    TestRoles,
    postgres_server_version,
    require_extension,
    tabrion_postgres_database,
    tabrion_postgres_roles,
    tabrion_postgres_schema,
    tabrion_postgres_url,
    tabrion_role_setup,
    truncate_tables,
)
from tabrion.testing.sqlite import tabrion_sqlite, tabrion_sqlite_path

__all__ = [
    "PostgresDatabaseHandle",
    "PostgresSchema",
    "RoleSetup",
    "TestRoles",
    "configure",
    "postgres_server_version",
    "profiles",
    "require_extension",
    "tabrion_postgres_database",
    "tabrion_postgres_roles",
    "tabrion_postgres_schema",
    "tabrion_postgres_url",
    "tabrion_role_setup",
    "tabrion_sqlite",
    "tabrion_sqlite_path",
    "truncate_tables",
]


def configure(
    *,
    env_prefix: str = "TABRION",
    postgres_env: Sequence[str] | None = None,
    require_env: Sequence[str] | None = None,
    runtime_role_env: Sequence[str] | None = None,
    require_extensions_env: Sequence[str] | None = None,
    schema_prefix: str = "t_",
) -> None:
    """Choose the environment variable names (call in ``conftest.py`` before the fixtures are used).

    Defaults: ``<prefix>_TEST_POSTGRES``, ``<prefix>_REQUIRE_POSTGRES``, ``<prefix>_TEST_RUNTIME_ROLE`` and
    ``<prefix>_REQUIRE_EXTENSIONS``.
    """
    raise NotImplementedError("tabrion.testing.configure: implemented in TAB-020")
