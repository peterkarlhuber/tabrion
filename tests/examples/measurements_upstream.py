"""Shared fixtures of the measurements transfer tests (RA-B-2, RA-B-3): the local store and an empty upstream.

``upstream_url`` runs a test twice: against an upstream SQLite file and, with ``TABRION_TEST_POSTGRES``, against a
PostgreSQL schema of its own (``tabrion_postgres_schema``, marker ``postgres``), with the same code (metric MW-8).
A test module takes them over by assignment (``upstream_url = support.upstream_url``).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from measurements import Store, open_database
from measurements.upstream import create_upstream, open_upstream
import pytest
import sqlalchemy as sa

from tabrion import Database
from tabrion.testing import PostgresSchema

ENGINES = ["sqlite", pytest.param("postgresql", marks=pytest.mark.postgres)]


def postgres_url(conninfo: str) -> sa.URL:
    """A SQLAlchemy URL for a libpq conninfo, with ``options`` (the schema's ``search_path``), ``sslmode`` and
    ``gssencmode`` (the fault proxy's plain protocol)."""
    from psycopg.conninfo import conninfo_to_dict

    params = {key: str(value) for key, value in conninfo_to_dict(conninfo).items() if value is not None}
    port = params.get("port")
    query = {key: params[key] for key in ("options", "sslmode", "gssencmode") if params.get(key)}
    return sa.URL.create(
        "postgresql",
        username=params.get("user"),
        password=params.get("password"),
        host=params.get("host") or params.get("hostaddr"),
        port=int(port) if port else None,
        database=params.get("dbname"),
        query=query,
    )


@pytest.fixture(params=ENGINES)
def upstream_url(request: pytest.FixtureRequest, tmp_path: Path) -> sa.URL:
    """An empty upstream with the measurement table: a PostgreSQL schema of its own, or a SQLite file."""
    if request.param == "postgresql":
        schema: PostgresSchema = request.getfixturevalue("tabrion_postgres_schema")
        url = postgres_url(schema.owner_url)
    else:
        url = sa.make_url(f"sqlite:///{tmp_path / 'upstream.db'}")
    with open_upstream(url) as database:
        create_upstream(database)
        create_upstream(database)  # idempotent
    return url


@pytest.fixture
def upstream(upstream_url: sa.URL) -> Iterator[Database]:
    with open_upstream(upstream_url) as database:
        yield database


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    connection = open_database(tmp_path / "local.db", backup_dir=tmp_path / "backups")
    try:
        yield Store(connection)
    finally:
        connection.close()
