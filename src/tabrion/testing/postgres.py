"""PostgreSQL fixtures and helpers of ``tabrion.testing``.

Contract section 6; implemented in TAB-020 (schema per test) and TAB-032 (database per session, roles without
superuser, ``truncate_tables``, runtime-role mode). Every schema and role it creates starts with the prefix ``t_``
and is removed afterwards.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import suppress
from dataclasses import dataclass
import secrets
from typing import TYPE_CHECKING, Any

import pytest

from tabrion.testing import _config

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
    """Admin or owner URL from the environment; skipped without it, an error when the require variable is set.

    The variable names come from :func:`tabrion.testing.configure`. Without psycopg the fixture skips too (an error
    under the require variable). The URL is never shown in a message (contract I-36).
    """
    return _postgres_url()


class _Url(str):
    """A conninfo whose ``repr`` masks the password (``tabrion.config.safe_url``).

    pytest shows the values of a failing test's fixtures with ``repr``; the URL itself is unchanged (contract I-36).
    """

    __slots__ = ()

    def __repr__(self) -> str:
        from tabrion.config import safe_url

        return repr(safe_url(str(self)))


def _postgres_url() -> str:
    settings = _config.current
    found = settings.first(settings.postgres_env)
    required = settings.flag(settings.require_env)
    if found is None:
        names = ", ".join(settings.postgres_env)
        if required:
            pytest.fail(
                f"{', '.join(settings.require_env)} requires PostgreSQL but none of {names} is set", pytrace=False
            )
        pytest.skip(f"PostgreSQL tests need one of {names}")
    try:
        import psycopg  # noqa: F401
    except ImportError:
        if required:
            pytest.fail('PostgreSQL is required but psycopg is missing: pip install "tabrion[postgres]"', pytrace=False)
        pytest.skip('PostgreSQL tests need psycopg: pip install "tabrion[postgres]"')
    return _Url(found[1])


def _with_params(url: str, **changes: Any) -> str:
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    params = conninfo_to_dict(url)
    params.update(changes)
    return _Url(make_conninfo("", **params))


def _new_name(kind: str = "") -> str:
    """``<schema_prefix><kind><hex12>``: lowercase, never needs quoting."""
    return f"{_config.current.schema_prefix}{kind}{secrets.token_hex(6)}"


def _search_path_url(url: str, schema: str) -> str:
    from psycopg.conninfo import conninfo_to_dict

    options = str(conninfo_to_dict(url).get("options") or "")
    return _with_params(url, options=f"{options} -c search_path={schema}".strip())


_DROP_LOCK_TIMEOUT = "30s"
"""How long a teardown ``DROP`` waits for a lock another role's session holds (it is never ended): an error, not a
hang."""


def _drop_schema(url: str, name: str, *, also: Sequence[str] = ()) -> None:
    """``DROP SCHEMA ... CASCADE`` of a schema this run created, but never into an extension (review H-1).

    An extension in the schema is database-wide: the ``CASCADE`` would drop it and every column of its types in
    other schemas. Then the schema is left in place and the teardown fails with the extension's name. ``also``
    names further roles of this run (the runtime role) whose sessions are ended like the URL role's.
    """
    import psycopg
    from psycopg import sql

    with psycopg.connect(url, autocommit=True, connect_timeout=10) as conn:
        # A connection of the test may still hold locks in the schema (an open transaction of a failed test); it
        # would block the DROP until it closes, after this teardown. End those sessions: only sessions of this
        # URL's role (review L-1; another role's session, a backup for example, is waited for instead), in this
        # database, holding a lock on the schema itself (a transaction that created an object in it; its
        # uncommitted relations are invisible here) or on one of its relations.
        with suppress(psycopg.Error):
            conn.execute(
                "SELECT pg_catalog.pg_terminate_backend(p.pid) FROM (SELECT DISTINCT l.pid"
                " FROM pg_catalog.pg_locks l, pg_catalog.pg_namespace n, pg_catalog.pg_stat_activity a"
                " WHERE n.nspname::pg_catalog.text OPERATOR(pg_catalog.=) %s::pg_catalog.text"
                " AND a.pid OPERATOR(pg_catalog.=) l.pid"
                " AND (a.usesysid OPERATOR(pg_catalog.=) (SELECT r.oid FROM pg_catalog.pg_roles r"
                "   WHERE r.rolname OPERATOR(pg_catalog.=) CURRENT_USER)"
                "   OR a.usename::pg_catalog.text OPERATOR(pg_catalog.=) ANY (%s::pg_catalog.text[]))"
                " AND l.pid OPERATOR(pg_catalog.<>) pg_catalog.pg_backend_pid()"
                " AND l.database OPERATOR(pg_catalog.=) (SELECT d.oid FROM pg_catalog.pg_database d"
                "   WHERE d.datname OPERATOR(pg_catalog.=) pg_catalog.current_database())"
                " AND ((l.locktype OPERATOR(pg_catalog.=) 'object'"
                "     AND l.classid OPERATOR(pg_catalog.=) 'pg_catalog.pg_namespace'::pg_catalog.regclass"
                "     AND l.objid OPERATOR(pg_catalog.=) n.oid)"
                "   OR l.relation IN (SELECT c.oid FROM pg_catalog.pg_class c"
                "     WHERE c.relnamespace OPERATOR(pg_catalog.=) n.oid))) p",
                (name, list(also)),
            )
        with conn.transaction():
            conn.execute(sql.SQL("SET LOCAL lock_timeout = {}").format(sql.Literal(_DROP_LOCK_TIMEOUT)))
            extensions = [
                str(row[0])
                for row in conn.execute(
                    "SELECT e.extname::pg_catalog.text FROM pg_catalog.pg_extension e, pg_catalog.pg_namespace n"
                    " WHERE e.extnamespace OPERATOR(pg_catalog.=) n.oid"
                    " AND n.nspname::pg_catalog.text OPERATOR(pg_catalog.=) %s::pg_catalog.text",
                    (name,),
                ).fetchall()
            ]
            if extensions:
                pytest.fail(
                    f"tabrion.testing: the schema {name!r} holds the extension(s) {', '.join(sorted(extensions))};"
                    " an extension is database-wide and DROP SCHEMA ... CASCADE would also drop the columns of its"
                    " types in other schemas, so the schema is left in place. Drop the extension yourself, or let"
                    " require_extension create it (in the schema public)",
                    pytrace=False,
                )
            conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(name)))


@pytest.fixture
def tabrion_postgres_schema(tabrion_postgres_url: str) -> Iterator[PostgresSchema]:
    """``CREATE SCHEMA <prefix><hex12>`` per test, dropped afterwards; runtime role in runtime-role mode.

    ``owner_url`` is ``tabrion_postgres_url`` with ``-c search_path=<schema>`` added to its ``options``. The schema
    is created and dropped as the role of ``tabrion_postgres_url``, which needs ``CREATE`` on the database.

    Runtime-role mode (the runtime-role variable, default ``TABRION_TEST_RUNTIME_ROLE``, set): also a LOGIN role
    ``<prefix>rt_<hex12>`` (random password, ``NOSUPERUSER NOBYPASSRLS``, created like the roles of
    ``tabrion_postgres_database``; the URL's role needs ``CREATEROLE``), and :func:`tabrion.postgres.grant_runtime_role`
    for it on the schema, run as the URL's role: ``USAGE``, DML on the tables and the default privileges, so the
    tables the test creates through ``owner_url`` are usable through ``runtime_url`` (its URL, same ``search_path``).
    Afterwards the schema is dropped, then the role (``DROP OWNED``, ``DROP ROLE``; contract I-60).
    """
    import psycopg
    from psycopg import sql

    settings = _config.current
    runtime = settings.flag(settings.runtime_role_env)
    name = _new_name()
    roles = _RoleSet(tabrion_postgres_url)
    created = False
    try:
        with psycopg.connect(tabrion_postgres_url, autocommit=True, connect_timeout=10) as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name)))
        created = True
        runtime_url: str | None = None
        if runtime:
            from tabrion.postgres import grant_runtime_role

            role = roles.create(_new_name("rt_"))
            with psycopg.connect(tabrion_postgres_url, autocommit=True, connect_timeout=10) as conn:
                grant_runtime_role(conn, role.name, schema=name)
            runtime_url = _search_path_url(role.url(tabrion_postgres_url), name)
        yield PostgresSchema(name=name, owner_url=_search_path_url(tabrion_postgres_url, name), runtime_url=runtime_url)
    finally:
        try:
            if created:
                _drop_schema(tabrion_postgres_url, name, also=roles.names)
        finally:
            try:
                roles.drop_owned(tabrion_postgres_url)
            finally:
                roles.drop_all()


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
    """``CREATE DATABASE <prefix><hex12>`` per session, ``DROP DATABASE ... WITH (FORCE)`` at the end.

    The roles follow the fixture ``tabrion_role_setup`` (doc 04 section 6: "parameter ``roles``"; G-T1 condition
    C-4). With ``split=True`` the owner role owns the database and its schema ``public``, and the app role gets
    ``CONNECT``; with ``split=False`` one role does both; ``None`` creates no role and ``owner_url`` and ``app_url``
    are the admin's. Every role is created here as ``LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE
    NOREPLICATION`` with a random password and dropped at the end; a name from ``RoleSetup`` must not exist yet
    (a role this fixture did not create is never changed or dropped; contract I-60). The admin needs ``CREATEDB``
    and, for the roles, ``CREATEROLE``; it becomes a member of the roles it creates, which ends with their ``DROP
    ROLE``.
    """
    import psycopg
    from psycopg import sql

    setup = tabrion_role_setup
    if setup is not None and not isinstance(setup, RoleSetup):
        raise TypeError("tabrion_role_setup must return a RoleSetup or None")
    name = _new_name()
    roles = _RoleSet(tabrion_postgres_url)
    created = False
    try:
        owner = app = None
        if setup is not None:
            owner_name, app_name = _role_names(setup)
            owner = roles.create(owner_name)
            app = owner if app_name == owner_name else roles.create(app_name)
        with psycopg.connect(tabrion_postgres_url, autocommit=True, connect_timeout=10) as conn:
            statement = sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name))
            if owner is not None:
                statement = sql.SQL("{} OWNER {}").format(statement, sql.Identifier(owner.name))
            conn.execute(statement)
            created = True
            if app is not None:
                conn.execute(
                    sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(name), sql.Identifier(app.name))
                )
        url = _with_params(tabrion_postgres_url, dbname=name)
        with psycopg.connect(url, autocommit=True, connect_timeout=10) as conn:
            if owner is not None:
                conn.execute(sql.SQL("ALTER SCHEMA public OWNER TO {}").format(sql.Identifier(owner.name)))
            conn.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        owner_url = url if owner is None else owner.url(url)
        app_url = url if app is None else app.url(url)
        yield PostgresDatabaseHandle(url=url, name=name, owner_url=owner_url, app_url=app_url)
    finally:
        try:
            if created:
                with psycopg.connect(tabrion_postgres_url, autocommit=True, connect_timeout=10) as conn:
                    conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
        finally:
            roles.drop_all()


def _role_names(setup: RoleSetup) -> tuple[str, str]:
    for label, value in (("owner", setup.owner), ("app", setup.app)):
        if value is not None and (not isinstance(value, str) or not value):
            raise TypeError(f"RoleSetup.{label} must be a non-empty str or None")
        if value is not None and len(value.encode()) > _NAME_BYTES:
            raise ValueError(
                f"RoleSetup.{label}: a name longer than {_NAME_BYTES} bytes (PostgreSQL would truncate it)"
            )
    if setup.split:
        owner = setup.owner or _new_name("own_")
        app = setup.app or _new_name("app_")
        if owner == app:
            raise ValueError("RoleSetup(split=True) needs two different roles")
        return owner, app
    if setup.owner is not None and setup.app is not None and setup.owner != setup.app:
        raise ValueError("RoleSetup(split=False) takes one role: owner and app must not differ")
    one = setup.owner or setup.app or _new_name("own_")
    return one, one


_NAME_BYTES = 63


@dataclass(frozen=True, slots=True)
class _Role:
    name: str
    password: str

    def url(self, base: str) -> str:
        return _with_params(base, user=self.name, password=self.password)


class _RoleSet:
    """LOGIN roles this run created, dropped again in reverse order; nothing else is touched (contract I-60)."""

    def __init__(self, admin_url: str) -> None:
        self._admin_url = admin_url
        self._roles: list[_Role] = []

    def create(self, name: str) -> _Role:
        import psycopg
        from psycopg import sql

        password = secrets.token_urlsafe(24)
        with psycopg.connect(self._admin_url, autocommit=True, connect_timeout=10) as conn:
            try:
                conn.execute(
                    sql.SQL(
                        "CREATE ROLE {} LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD {}"
                    ).format(sql.Identifier(name), sql.Literal(password))
                )
            except psycopg.errors.DuplicateObject:
                pytest.fail(
                    f"tabrion.testing: the role {name!r} already exists; the fixture creates its roles itself and"
                    " never changes or drops a role it did not create",
                    pytrace=False,
                )
            role = _Role(name, password)
            self._roles.append(role)
            # Membership lets a non-superuser admin (CREATEROLE) create the database for the role and drop what
            # it owns; DROP ROLE ends it.
            conn.execute(sql.SQL("GRANT {} TO CURRENT_USER").format(sql.Identifier(name)))
        return role

    @property
    def names(self) -> list[str]:
        return [role.name for role in self._roles]

    def drop_owned(self, url: str) -> None:
        """``DROP OWNED BY`` the roles in the database of ``url``, after ending their sessions there."""
        import psycopg
        from psycopg import sql

        if not self._roles:
            return
        identifiers = sql.SQL(", ").join(sql.Identifier(name) for name in self.names)
        with psycopg.connect(url, autocommit=True, connect_timeout=10) as conn:
            _terminate(conn, self.names)
            conn.execute("SET lock_timeout = '10s'")  # an error rather than a teardown that never ends
            conn.execute(sql.SQL("DROP OWNED BY {}").format(identifiers))

    def drop_all(self) -> None:
        import psycopg
        from psycopg import sql

        if not self._roles:
            return
        with psycopg.connect(self._admin_url, autocommit=True, connect_timeout=10) as conn:
            _terminate(conn, self.names, everywhere=True)
            for role in reversed(self._roles):
                conn.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role.name)))
        self._roles.clear()


def _terminate(conn: psycopg.Connection[Any], names: Sequence[str], *, everywhere: bool = False) -> None:
    """End the sessions of the roles (in this database, or in all with ``everywhere``); only roles of this run."""
    import psycopg

    with suppress(psycopg.Error):
        conn.execute(
            "SELECT pg_catalog.pg_terminate_backend(a.pid) FROM pg_catalog.pg_stat_activity a"
            " WHERE a.usename::pg_catalog.text OPERATOR(pg_catalog.=) ANY (%s::pg_catalog.text[])"
            " AND a.pid OPERATOR(pg_catalog.<>) pg_catalog.pg_backend_pid()"
            " AND (%s OR a.datname OPERATOR(pg_catalog.=) pg_catalog.current_database())",
            (list(names), everywhere),
        )


@pytest.fixture(scope="session")
def tabrion_postgres_roles(tabrion_postgres_url: str) -> Iterator[TestRoles]:
    """The same role setup in an existing database: the database of ``tabrion_postgres_url``.

    Owner ``<prefix>own_<hex12>`` and app ``<prefix>app_<hex12>``, both created like the roles of
    ``tabrion_postgres_database``; both get ``CONNECT`` on the database and the owner gets ``CREATE`` (for its
    schemas). The URLs are ``tabrion_postgres_url`` with the role's name and password. At the end ``DROP OWNED BY``
    in that database (it also revokes the grants) and ``DROP ROLE``. The admin needs ``CREATEROLE`` and the grant
    option on the database (its owner, or a superuser); a grant that did not take effect is an error.
    """
    import psycopg
    from psycopg import sql

    roles = _RoleSet(tabrion_postgres_url)
    try:
        owner = roles.create(_new_name("own_"))
        app = roles.create(_new_name("app_"))
        with psycopg.connect(tabrion_postgres_url, autocommit=True, connect_timeout=10) as conn:
            database = sql.Identifier(str(conn.info.dbname))
            conn.execute(
                sql.SQL("GRANT CONNECT, CREATE ON DATABASE {} TO {}").format(database, sql.Identifier(owner.name))
            )
            conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(database, sql.Identifier(app.name)))
            # A grantor without the grant option gets only a WARNING; check that the privileges are there (one
            # privilege per call: a list is true when any one of them is held, and PUBLIC may hold CONNECT).
            row = conn.execute(
                "SELECT pg_catalog.has_database_privilege(%(o)s, pg_catalog.current_database(), 'CONNECT'),"
                " pg_catalog.has_database_privilege(%(o)s, pg_catalog.current_database(), 'CREATE'),"
                " pg_catalog.has_database_privilege(%(a)s, pg_catalog.current_database(), 'CONNECT')",
                {"o": owner.name, "a": app.name},
            ).fetchone()
            if row is None or not all(row):
                pytest.fail(
                    "tabrion.testing: the admin role could not grant CONNECT and CREATE on the database"
                    " (it needs to own the database or hold the grant option)",
                    pytrace=False,
                )
        yield TestRoles(
            owner=owner.name,
            app=app.name,
            owner_url=owner.url(tabrion_postgres_url),
            app_url=app.url(tabrion_postgres_url),
        )
    finally:
        try:
            roles.drop_owned(tabrion_postgres_url)
        finally:
            roles.drop_all()


@pytest.fixture(scope="session")
def postgres_server_version(tabrion_postgres_url: str) -> int:
    """``server_version_num`` of the test server."""
    import psycopg

    with psycopg.connect(tabrion_postgres_url, autocommit=True, connect_timeout=10) as conn:
        return int(conn.info.server_version)


def truncate_tables(
    target: psycopg.Connection[Any] | str,
    *,
    schema: str | None = None,
    like: str | None = None,
    exclude: Sequence[str] = (),
) -> int:
    """``TRUNCATE ... RESTART IDENTITY CASCADE`` of the schema's tables in one statement; returns the table count.

    ``target`` is a connection (the statement runs in ``conn.transaction()``: committed on an idle connection, a
    savepoint inside the caller's transaction) or a URL (a connection of its own). ``schema`` defaults to
    ``current_schema()``. The tables are the ordinary and partitioned tables of the schema (a partition goes with its
    parent), optionally only those whose name matches the ``LIKE`` pattern ``like``, without the names in
    ``exclude``; the identifiers are quoted. No table: no statement, ``0``. ``CASCADE`` also empties tables that
    reference a truncated table through a foreign key, an excluded one included (contract I-60).
    """
    import psycopg

    if isinstance(exclude, str) or not all(isinstance(item, str) for item in exclude):
        raise TypeError("truncate_tables: exclude must be a sequence of table names, not a str")
    if schema is not None and (not isinstance(schema, str) or not schema):
        raise TypeError("truncate_tables: schema must be a non-empty str or None")
    if like is not None and not isinstance(like, str):
        raise TypeError("truncate_tables: like must be a str or None")
    if isinstance(target, str):
        with psycopg.connect(target, autocommit=True, connect_timeout=10) as conn:
            return _truncate(conn, schema, like, exclude)
    return _truncate(target, schema, like, exclude)


def _truncate(conn: psycopg.Connection[Any], schema: str | None, like: str | None, exclude: Sequence[str]) -> int:
    from psycopg import sql

    with conn.transaction():
        row = conn.execute(
            "SELECT n.nspname::pg_catalog.text FROM pg_catalog.pg_namespace n"
            " WHERE n.nspname::pg_catalog.text OPERATOR(pg_catalog.=)"
            " COALESCE(%s::pg_catalog.text, pg_catalog.current_schema()::pg_catalog.text)",
            (schema,),
        ).fetchone()
        if row is None:
            raise ValueError(
                "truncate_tables: no current schema; pass schema="
                if schema is None
                else f"truncate_tables: the schema {schema!r} does not exist"
            )
        name = str(row[0])
        tables = [
            str(table)
            for (table,) in conn.execute(
                "SELECT c.relname::pg_catalog.text FROM pg_catalog.pg_class c, pg_catalog.pg_namespace n"
                " WHERE c.relnamespace OPERATOR(pg_catalog.=) n.oid"
                " AND n.nspname::pg_catalog.text OPERATOR(pg_catalog.=) %s::pg_catalog.text"
                " AND c.relkind OPERATOR(pg_catalog.=) ANY ('{r,p}'::pg_catalog.\"char\"[]) AND NOT c.relispartition"
                " AND (%s::pg_catalog.text IS NULL"
                "   OR c.relname::pg_catalog.text OPERATOR(pg_catalog.~~) %s::pg_catalog.text)"
                " AND NOT c.relname::pg_catalog.text OPERATOR(pg_catalog.=) ANY (%s::pg_catalog.text[])",
                (name, like, like, list(exclude)),
            ).fetchall()
        ]
        if not tables:
            return 0
        tables.sort()
        conn.execute(
            sql.SQL("TRUNCATE TABLE {} RESTART IDENTITY CASCADE").format(
                sql.SQL(", ").join(sql.Identifier(name, table) for table in tables)
            )
        )
    return len(tables)


_EXTENSION_HOME = "public"
"""Where ``require_extension`` creates a missing extension: a schema no fixture drops (review H-1)."""


def require_extension(conn: psycopg.Connection[Any], name: str) -> ExtensionStatus:
    """Make an extension usable on ``conn``, else skip; an error when ``name`` is in the require variable.

    Never moves an extension and never creates one in a schema a fixture drops (review H-1): an extension is
    database-wide, and a ``DROP SCHEMA ... CASCADE`` of its schema would also drop the columns of its types in other
    schemas.

    * ``installable``: ``CREATE EXTENSION IF NOT EXISTS ... SCHEMA public``; the extension stays in the database
      (another run may already use it). Two runs racing for it: the loser finds it installed.
    * ``other_schema`` (also after that ``CREATE``): the extension's schema is appended to the ``search_path`` of
      ``conn`` (``set_config(..., false)``; unqualified new objects still land in the first schema).

    Both run in ``conn.transaction()``: a transaction of its own on an idle connection, committed; a savepoint inside
    the caller's transaction, so its rollback also undoes them. A failure (missing privilege, missing package, no
    schema ``public``) is rolled back to that point and the test is skipped, or fails when ``name`` is listed in the
    require variable; the message names only the error class and SQLSTATE (contract I-36).
    """
    import psycopg
    from psycopg import sql

    from tabrion.postgres import extension_status

    status = extension_status(conn, name)  # validates the name
    error = ""
    if status.state == "installable":
        try:
            with conn.transaction():
                conn.execute(
                    sql.SQL("CREATE EXTENSION IF NOT EXISTS {} SCHEMA {}").format(
                        sql.Identifier(name), sql.Identifier(_EXTENSION_HOME)
                    )
                )
        except psycopg.Error as exc:
            error = f" ({type(exc).__name__}, SQLSTATE {getattr(exc, 'sqlstate', None) or '-'})"
        status = extension_status(conn, name)
    if status.state == "other_schema" and status.extension_schema is not None:
        try:
            with conn.transaction():
                # SET search_path = '' reads back as "" (an empty name); either way only the schema remains.
                conn.execute(
                    "SELECT pg_catalog.set_config('search_path', CASE"
                    " WHEN pg_catalog.current_setting('search_path')"
                    "   OPERATOR(pg_catalog.=) ANY (ARRAY['', '\"\"']::pg_catalog.text[])"
                    "   THEN pg_catalog.quote_ident(%(s)s::pg_catalog.text)"
                    " ELSE pg_catalog.current_setting('search_path') OPERATOR(pg_catalog.||) ', '"
                    "   OPERATOR(pg_catalog.||) pg_catalog.quote_ident(%(s)s::pg_catalog.text) END, false)",
                    {"s": status.extension_schema},
                )
        except psycopg.Error as exc:
            error = f" ({type(exc).__name__}, SQLSTATE {getattr(exc, 'sqlstate', None) or '-'})"
        status = extension_status(conn, name)
    if status.state == "active":
        return status
    message = f"the PostgreSQL extension {name!r} is {status.state} and could not be activated{error}"
    if name in _config.current.required_extensions():
        pytest.fail(f"{message}; it is required by {', '.join(_config.current.require_extensions_env)}", pytrace=False)
    pytest.skip(message)
