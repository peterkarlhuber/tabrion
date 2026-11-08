"""The PostgreSQL service of the CI jobs "pg (...)": reachable, the expected major version, pgvector available.

These tests check the test infrastructure that the PostgreSQL suites of M2 (TAB-015, TAB-016, TAB-030, TAB-031)
build on. Without ``TABRION_TEST_POSTGRES`` they skip, unless ``TABRION_REQUIRE_POSTGRES=1`` makes that an error.
"""

from __future__ import annotations

from collections.abc import Iterator
import os
from typing import Any

import pytest

pytestmark = pytest.mark.postgres


@pytest.fixture(scope="module")
def conn() -> Iterator[Any]:
    conninfo = os.environ.get("TABRION_TEST_POSTGRES")
    if not conninfo:
        if os.environ.get("TABRION_REQUIRE_POSTGRES") == "1":
            pytest.fail("TABRION_REQUIRE_POSTGRES=1 but TABRION_TEST_POSTGRES is not set")
        pytest.skip("TABRION_TEST_POSTGRES is not set")
    import psycopg

    with psycopg.connect(conninfo, autocommit=True, connect_timeout=10) as connection:
        yield connection


def test_server_answers(conn: Any) -> None:
    assert conn.execute("SELECT 1").fetchone() == (1,)


def test_server_major_version(conn: Any) -> None:
    version = int(conn.execute("SELECT current_setting('server_version_num')").fetchone()[0])
    assert version >= 160000, "Tabrion supports PostgreSQL 16 or later"
    expected = os.environ.get("TABRION_EXPECT_SERVER_MAJOR")
    if expected:
        assert version // 10000 == int(expected)


def test_pgvector_available_when_required(conn: Any) -> None:
    row = conn.execute("SELECT count(*) FROM pg_available_extensions WHERE name = 'vector'").fetchone()
    if os.environ.get("TABRION_REQUIRE_PGVECTOR") == "1":
        assert row[0] == 1, "the pgvector image must offer the extension 'vector'"
    else:
        assert row[0] in (0, 1)
