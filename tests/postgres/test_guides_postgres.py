"""TAB-041: the code examples of the PostgreSQL guides run against the test server (harness ``tests/docs``).

``DATABASE_URL`` is the owner URL of a fresh schema of the plugin (``tabrion_postgres_schema``), in URL form so both
``psycopg.connect`` and ``tabrion.Database.from_url`` take it. ``APP_ROLE`` and ``APP_DATABASE_URL`` are a LOGIN role
without privileges (``make_role``) and its URL with the same ``search_path``. The plugin drops the schema, ``make_role``
the role, and the leak check of ``tests/postgres/conftest.py`` runs after each page.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tabrion.testing import PostgresSchema
from tests.docs.harness import engines, guide_pages, run_page
from tests.postgres.conftest import with_params

pytestmark = pytest.mark.postgres

psycopg = pytest.importorskip("psycopg")
sqlalchemy = pytest.importorskip("sqlalchemy")

_URL_PARTS = {"host", "port", "user", "password", "dbname"}


def as_url(conninfo: str) -> str:
    """A libpq conninfo as a ``postgresql://`` URL (the other parameters go into the query)."""
    params = psycopg.conninfo.conninfo_to_dict(conninfo)
    # libpq does not read "+" in a URL query as a space: "-c name=value" is written "-cname=value".
    query = {key: str(value).replace("-c ", "-c") for key, value in params.items() if key not in _URL_PARTS}
    url = sqlalchemy.URL.create(
        "postgresql",
        username=params.get("user"),
        password=params.get("password"),
        host=params.get("host"),
        port=int(params["port"]) if params.get("port") else None,
        database=params.get("dbname"),
        query=query,
    )
    return str(url.render_as_string(hide_password=False))


@pytest.mark.parametrize("page", [p for p in guide_pages() if "postgres" in engines(p)], ids=lambda p: p.stem)
def test_examples_run(
    page: Path,
    tmp_path: Path,
    tabrion_postgres_schema: PostgresSchema,
    make_role: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    role = make_role()
    options = str(psycopg.conninfo.conninfo_to_dict(tabrion_postgres_schema.owner_url)["options"])
    monkeypatch.setenv("DATABASE_URL", as_url(tabrion_postgres_schema.owner_url))
    monkeypatch.setenv("APP_ROLE", role.name)
    monkeypatch.setenv("APP_DATABASE_URL", as_url(with_params(role.url, options=options)))
    assert run_page(page, tmp_path) > 0
