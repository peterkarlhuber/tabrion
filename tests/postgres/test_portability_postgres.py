"""``tabrion.testing.profiles.portability_cases()`` on PostgreSQL (TAB-021, TAB-T-D01..D09 on the second engine).

Each case runs on a ``Database`` whose ``search_path`` is the ``schema`` fixture (``t_<hex>``, dropped afterwards),
so its tables never meet another run's.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

pytestmark = pytest.mark.postgres

pytest.importorskip("sqlalchemy")
pytest.importorskip("psycopg")

from tabrion import Database, Profile  # noqa: E402
from tabrion.postgres import SessionSettings  # noqa: E402
from tabrion.testing.profiles import PortabilityCase, portability_cases  # noqa: E402
from tests.postgres.test_database import sa_url  # noqa: E402


@pytest.fixture
def db(admin_url: str, schema: str) -> Iterator[Database]:
    profile = Profile(pool_size=1, postgres=SessionSettings(search_path=schema))
    database = Database.from_url(sa_url(admin_url), profile=profile)
    try:
        yield database
    finally:
        database.close()


@pytest.mark.parametrize("portability_case", portability_cases(), ids=lambda c: c.id)
def test_portability_cases_pass_on_postgres(portability_case: PortabilityCase, db: Database, schema: str) -> None:
    portability_case.check(db)
    left = db.fetch_scalar(
        "SELECT count(*) FROM pg_catalog.pg_tables WHERE schemaname = :s AND tablename LIKE 'tabrion_d%'", {"s": schema}
    )
    assert left == 0  # each case drops its tables
