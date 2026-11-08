"""Fixtures of the PostgreSQL suites of TAB-015 and TAB-016 (``tabrion.testing`` arrives with TAB-020/TAB-032).

``TABRION_TEST_POSTGRES`` is a superuser conninfo (the CI jobs "pg (...)" and the local server). The suites also run
as roles **without** superuser: ``make_role`` creates LOGIN roles ``t_<hex>`` (NOSUPERUSER NOBYPASSRLS, a random
password) and drops them afterwards; ``scratch_database`` is a throwaway database for tests that change
database-wide state (extensions, revoked catalog privileges). Every schema, role and database starts with ``t_`` and
is removed afterwards.
"""

# The SQL is built from names this file generates (uuid hex) and fixed text, never from outside input.

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
import os
import secrets
from typing import Any
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")


def admin_conninfo() -> str:
    conninfo = os.environ.get("TABRION_TEST_POSTGRES")
    if not conninfo:
        if os.environ.get("TABRION_REQUIRE_POSTGRES") == "1":
            pytest.fail("TABRION_REQUIRE_POSTGRES=1 but TABRION_TEST_POSTGRES is not set")
        pytest.skip("TABRION_TEST_POSTGRES is not set")
    return conninfo


def with_params(conninfo: str, **changes: Any) -> str:
    params = psycopg.conninfo.conninfo_to_dict(conninfo)
    params.update(changes)
    return str(psycopg.conninfo.make_conninfo(**params))


def new_name(prefix: str = "t_") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


@pytest.fixture(scope="session")
def admin_url() -> str:
    return admin_conninfo()


@pytest.fixture
def admin(admin_url: str) -> Iterator[Any]:
    with psycopg.connect(admin_url, autocommit=True, connect_timeout=10) as conn:
        yield conn


@pytest.fixture
def schema(admin_url: str) -> Iterator[str]:
    """A fresh schema with one plain table, owned by the admin; dropped afterwards."""
    name = new_name()
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(f"CREATE SCHEMA {name}")
        conn.execute(f"CREATE TABLE {name}.parts (id int PRIMARY KEY, label text)")
    try:
        yield name
    finally:
        with psycopg.connect(admin_url, autocommit=True) as conn:
            # A failed test may leave a connection of its own in a transaction that locks a table here; the
            # connections close only after this teardown, so end those sessions instead of waiting for them.
            conn.execute(
                "SELECT pg_terminate_backend(l.pid) FROM pg_locks l JOIN pg_class c ON c.oid = l.relation"
                " JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = %s AND l.pid <> pg_backend_pid()",
                (name,),
            )
            conn.execute(f"DROP SCHEMA {name} CASCADE")


@dataclass(frozen=True)
class Role:
    name: str
    url: str


@pytest.fixture
def make_role(admin_url: str) -> Iterator[Callable[..., Role]]:
    """``make_role(attributes="", dbname=None)``: a LOGIN role without superuser and its conninfo.

    ``attributes`` replaces the default ``NOBYPASSRLS`` (for example ``"BYPASSRLS"``)."""
    created: list[str] = []

    def factory(attributes: str = "", *, dbname: str | None = None) -> Role:
        name = new_name()
        password = secrets.token_hex(16)
        with psycopg.connect(admin_url, autocommit=True) as conn:
            conn.execute(
                psycopg.sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE {} PASSWORD {}").format(
                    psycopg.sql.Identifier(name),
                    psycopg.sql.SQL(attributes or "NOBYPASSRLS"),
                    psycopg.sql.Literal(password),
                )
            )
        created.append(name)
        changes: dict[str, Any] = {"user": name, "password": password}
        if dbname is not None:
            changes["dbname"] = dbname
        return Role(name, with_params(admin_url, **changes))

    yield factory
    with psycopg.connect(admin_url, autocommit=True) as conn:
        databases = [r[0] for r in conn.execute("SELECT datname FROM pg_database WHERE datallowconn").fetchall()]
        for name in created:
            # Privileges the role holds in other databases block DROP ROLE; drop them where they can be.
            for database in databases:
                if database == conn.info.dbname or database.startswith("t_"):
                    # Another run on a shared server may drop its database meanwhile (it then holds nothing of ours).
                    try:
                        other = psycopg.connect(with_params(admin_url, dbname=database), autocommit=True)
                    except psycopg.OperationalError:
                        continue
                    with other:
                        other.execute(f'DROP OWNED BY "{name}"')
            conn.execute(f'DROP ROLE IF EXISTS "{name}"')


@pytest.fixture
def scratch_database(admin_url: str) -> Iterator[str]:
    """``CREATE DATABASE t_<hex>``; ``DROP DATABASE ... WITH (FORCE)`` afterwards. Returns its admin conninfo."""
    name = new_name()
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(f"CREATE DATABASE {name}")
    try:
        yield with_params(admin_url, dbname=name)
    finally:
        with psycopg.connect(admin_url, autocommit=True) as conn:
            conn.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


@pytest.fixture
def connect(admin_url: str) -> Iterator[Callable[..., Any]]:
    """``connect(url=None, **kwargs)``: a psycopg connection (admin by default), closed after the test."""
    opened: list[Any] = []

    def factory(url: str | None = None, **kwargs: Any) -> Any:
        kwargs.setdefault("connect_timeout", 10)
        conn = psycopg.connect(url or admin_url, **kwargs)
        opened.append(conn)
        return conn

    yield factory
    for conn in opened:
        conn.close()
