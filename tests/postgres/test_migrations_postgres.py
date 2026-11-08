"""``tabrion.migrations`` on PostgreSQL (TAB-019, TAB-T-M01..M03, M08): the version table in a schema of its own.

The script directories and the version table are written by ``tests/unit/test_migrations.py``'s helpers; nothing
runs ``env.py``. Everything lives in the ``schema`` fixture (``t_<hex>``, dropped afterwards).
"""

# The SQL is built from the schema fixture's generated name and fixed text, never from outside input.
# ruff: noqa: S608

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.postgres

pytest.importorskip("sqlalchemy")
pytest.importorskip("psycopg")
pytest.importorskip("alembic")

from tabrion import Database, Profile  # noqa: E402
from tabrion.errors import SchemaNotCurrent  # noqa: E402
from tabrion.migrations import require_current, status  # noqa: E402
from tests.postgres.test_database import sa_url  # noqa: E402
from tests.unit.test_migrations import CHAIN, scripts  # noqa: E402


@pytest.fixture
def db(admin_url: str) -> Iterator[Database]:
    database = Database.from_url(sa_url(admin_url), profile=Profile(read_only=True, pool_size=1))
    try:
        yield database
    finally:
        database.close()


def test_m01_m03_m08_version_table_in_a_schema(db: Database, schema: str, admin: Any, tmp_path: Path) -> None:
    location = scripts(tmp_path, CHAIN)
    fresh = status(db, script_location=location, version_table_schema=schema)
    assert fresh.current == () and fresh.pending == ("a1", "a2", "a3")
    admin.execute(f"CREATE TABLE {schema}.alembic_version (version_num varchar(32) PRIMARY KEY)")
    admin.execute(f"INSERT INTO {schema}.alembic_version VALUES ('a2')")
    pending = status(db, script_location=location, version_table_schema=schema)
    assert pending.current == ("a2",) and pending.pending == ("a3",)
    with pytest.raises(SchemaNotCurrent) as info:
        require_current(db, script_location=location, version_table_schema=schema)
    assert dict(info.value.details) == {"current": "a2", "head": "a3"}
    admin.execute(f"UPDATE {schema}.alembic_version SET version_num = 'a3'")
    assert require_current(db, script_location=location, version_table_schema=schema).is_current
    assert not (location / "env_was_run").exists()


def test_version_table_schema_typo_looks_fresh(db: Database, schema: str, admin: Any, tmp_path: Path) -> None:
    """A schema name that does not exist reads as a fresh database, not as an error (documented in I-57)."""
    location = scripts(tmp_path, CHAIN)
    admin.execute(f"CREATE TABLE {schema}.alembic_version (version_num varchar(32) PRIMARY KEY)")
    admin.execute(f"INSERT INTO {schema}.alembic_version VALUES ('a3')")
    assert status(db, script_location=location, version_table_schema=schema).is_current
    typo = status(db, script_location=location, version_table_schema=schema + "_typo")
    assert typo.current == () and typo.pending == ("a1", "a2", "a3") and not typo.is_current
