"""Fixtures of the PostgreSQL suites, built on the ``tabrion.testing`` plugin (dogfooding, contract section 6).

``TABRION_TEST_POSTGRES`` is a superuser conninfo (the CI jobs "pg (...)" and the local server), read by the plugin's
``tabrion_postgres_url``; ``TABRION_REQUIRE_POSTGRES=1`` turns a missing server into a failure. The suites also run
as roles **without** superuser: ``make_role`` creates LOGIN roles (NOSUPERUSER NOBYPASSRLS, a random password) and
drops them afterwards; ``scratch_database`` is a throwaway database for tests that change database-wide state.

The server may be shared (reviewers, other runs). Every schema, role and database this run creates starts with
``PREFIX`` (``t_<hex6>_``, new for each run and configured as the plugin's ``schema_prefix``); nothing else is ever
dropped. After each test, ``_no_leaks`` checks that none with the prefix is left (TAB-T-X10).
"""

# The SQL is built from names this file generates (uuid hex) and fixed text, never from outside input.

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
import secrets
from typing import Any
import uuid

import pytest

from tabrion.testing import PostgresSchema, configure

psycopg = pytest.importorskip("psycopg")

PREFIX = f"t_{secrets.token_hex(3)}_"
"""The prefix of everything this run creates on the server."""
configure(schema_prefix=PREFIX)


def with_params(conninfo: str, **changes: Any) -> str:
    params = psycopg.conninfo.conninfo_to_dict(conninfo)
    params.update(changes)
    return str(psycopg.conninfo.make_conninfo(**params))


def new_name(prefix: str = "") -> str:
    return f"{PREFIX}{prefix}{uuid.uuid4().hex[:12]}"


def leaked(conn: Any, prefix: str = PREFIX) -> list[str]:
    """Schemas (in the database of ``conn``), roles and databases whose name starts with ``prefix``."""
    rows = conn.execute(
        "SELECT 'schema ' || nspname FROM pg_namespace WHERE starts_with(nspname, %(p)s)"
        " UNION ALL SELECT 'role ' || rolname FROM pg_roles WHERE starts_with(rolname, %(p)s)"
        " UNION ALL SELECT 'database ' || datname FROM pg_database WHERE starts_with(datname, %(p)s)",
        {"p": prefix},
    ).fetchall()
    return sorted(str(row[0]) for row in rows)


@pytest.fixture(autouse=True)
def _no_leaks(request: pytest.FixtureRequest) -> Iterator[None]:
    """TAB-T-X10: after a test that used the server, nothing with ``PREFIX`` is left (runs after every teardown)."""
    yield
    if "tabrion_postgres_url" not in request.fixturenames:
        return
    url = request.getfixturevalue("tabrion_postgres_url")
    with psycopg.connect(url, autocommit=True, connect_timeout=10) as conn:
        left = leaked(conn)
    assert not left, f"left on the server after {request.node.nodeid}: {left}"


@pytest.fixture(scope="session")
def admin_url(tabrion_postgres_url: str) -> str:
    return tabrion_postgres_url


@pytest.fixture
def admin(admin_url: str) -> Iterator[Any]:
    with psycopg.connect(admin_url, autocommit=True, connect_timeout=10) as conn:
        yield conn


@pytest.fixture
def schema(admin_url: str, tabrion_postgres_schema: PostgresSchema) -> str:
    """A fresh schema (the plugin's ``tabrion_postgres_schema``) with one plain table; dropped by the plugin."""
    name = tabrion_postgres_schema.name
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(f"CREATE TABLE {name}.parts (id int PRIMARY KEY, label text)")
    return name


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
                if database == conn.info.dbname or database.startswith(PREFIX):
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
