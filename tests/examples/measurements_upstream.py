"""Shared fixtures of the measurements transfer tests (RA-B-2, RA-B-3): the local store and an empty upstream.

``upstream_url`` runs a test twice: against an upstream SQLite file and, with ``TABRION_TEST_POSTGRES``, against a
PostgreSQL schema of its own (``tabrion_postgres_schema``, marker ``postgres``), with the same code (metric MW-8).
Each fixture works for both variants of the application (``app``, ``measurements_variants.py``). A test module takes
them over by assignment (``upstream_url = support.upstream_url``).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from tabrion.testing import PostgresSchema

from .measurements_variants import Upstream, Variant

ENGINES = ["sqlite", pytest.param("postgresql", marks=pytest.mark.postgres)]


def postgres_url(conninfo: str) -> sa.URL:
    """A SQLAlchemy URL for a libpq conninfo, with ``options`` (the schema's ``search_path``), ``sslmode`` and
    ``gssencmode`` (the fault proxy's plain protocol)."""
    from psycopg.conninfo import conninfo_to_dict

    params = {key: str(value) for key, value in conninfo_to_dict(conninfo).items() if value is not None}
    port = params.get("port")
    query = {key: params[key] for key in ("options", "sslmode", "gssencmode") if params.get(key)}
    host = params.get("host") or params.get("hostaddr")
    if host is not None and host.startswith("/"):
        # A Unix-socket directory is no URL host; libpq takes it as the ``host`` query parameter (review L-2 of #23).
        query["host"], host = host, None
    return sa.URL.create(
        "postgresql",
        username=params.get("user"),
        password=params.get("password"),
        host=host,
        port=int(port) if port else None,
        database=params.get("dbname"),
        query=query,
    )


@pytest.fixture(params=ENGINES)
def upstream_url(app: Variant, request: pytest.FixtureRequest, tmp_path: Path) -> sa.URL:
    """An empty upstream with the measurement table: a PostgreSQL schema of its own, or a SQLite file."""
    if request.param == "postgresql":
        schema: PostgresSchema = request.getfixturevalue("tabrion_postgres_schema")
        url = postgres_url(schema.owner_url)
    else:
        url = sa.make_url(f"sqlite:///{tmp_path / 'upstream.db'}")
    with app.upstream(url) as upstream:
        app.create_upstream(upstream.handle)
        app.create_upstream(upstream.handle)  # idempotent
    return url


@pytest.fixture
def upstream(app: Variant, upstream_url: sa.URL) -> Iterator[Upstream]:
    with app.upstream(upstream_url) as opened:
        yield opened


@pytest.fixture
def store(app: Variant, tmp_path: Path) -> Iterator[Any]:
    local = app.open(tmp_path / "local.db")
    try:
        yield local.store
    finally:
        local.close()


def count(upstream: Upstream) -> int:
    return int(upstream.scalar(sa.select(sa.func.count()).select_from(table(upstream))))


def table(upstream: Upstream) -> sa.Table:
    table: sa.Table = upstream.variant.UPSTREAM
    return table


def snapshot(upstream: Upstream) -> list[tuple[Any, ...]]:
    """Every upstream row, in key order, ``ts_utc`` as UTC (SQLite returns it naive)."""
    rows = upstream.rows(sa.select(table(upstream)).order_by(*table(upstream).primary_key.columns))
    return [
        (r.device_id, r.channel, r.ts_utc.replace(tzinfo=UTC) if r.ts_utc.tzinfo is None else r.ts_utc, *r[3:])
        for r in rows
    ]


def keys(upstream: Upstream) -> list[tuple[str, str, Any]]:
    """The natural keys of every upstream row (with repetitions, if there were any)."""
    return [(row[0], row[1], row[2]) for row in snapshot(upstream)]
