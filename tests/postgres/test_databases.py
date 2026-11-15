"""TAB-XW-9 against a real PostgreSQL: ``create_database``, ``drop_database``, ``database_sessions``.

Contract section 4.8, notes I-190..I-193; ADR 0015 sections 1 and 7. The create and drop round trip, each refusal, a
concurrent session, a missing ``CREATEDB``, names that need quoting (``psycopg.sql.Identifier``, never formatting),
and the connection left as it was (T-NOTX: ``autocommit`` is never switched). Every database starts with the run's
``PREFIX``, so the leak check of ``conftest.py`` covers this file.
"""

# The SQL of the checks is built from names this file generates (uuid hex) and fixed text, never from outside input.

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest

from tabrion.errors import DatabaseInUse, Kind, PermissionDenied, RoleMissing, UsageError
from tabrion.postgres import create_database, database_sessions, drop_database
import tabrion.postgres._databases as databases

from .conftest import PREFIX, Role, new_name, with_params

psycopg = pytest.importorskip("psycopg")
from psycopg import pq, sql  # noqa: E402

pytestmark = pytest.mark.postgres


@pytest.fixture
def cleanup(admin_url: str) -> Iterator[list[str]]:
    """Names of databases a test may have created; dropped (``WITH (FORCE)``) afterwards."""
    names: list[str] = []
    yield names
    with psycopg.connect(admin_url, autocommit=True) as conn:
        for name in names:
            conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))


def _database(admin: Any, name: str) -> tuple[Any, ...] | None:
    row: tuple[Any, ...] | None = admin.execute(
        "SELECT pg_get_userbyid(datdba), pg_encoding_to_char(encoding), datcollate, datistemplate"
        " FROM pg_database WHERE datname = %s",
        (name,),
    ).fetchone()
    return row


def test_create_and_drop_round_trip(admin: Any, admin_url: str, cleanup: list[str]) -> None:
    name = new_name("db_")
    cleanup.append(name)
    create_database(admin, name)
    owner, encoding, _, template = _database(admin, name) or ()
    assert (owner, encoding, template) == (admin.info.user, "UTF8", False)
    assert database_sessions(admin, name) == 0
    with psycopg.connect(with_params(admin_url, dbname=name)) as other:
        other.execute("SELECT 1")
        assert database_sessions(admin, name) == 1
        with pytest.raises(DatabaseInUse) as caught:
            drop_database(admin, name)
        assert caught.value.code == "postgres.database_in_use"
        assert dict(caught.value.details) == {"sessions": 1}
        assert name not in str(caught.value) and name not in repr(caught.value)
        assert _database(admin, name) is not None
    drop_database(admin, name)
    assert _database(admin, name) is None
    assert admin.autocommit and admin.info.transaction_status == pq.TransactionStatus.IDLE


def test_create_with_owner_template0_encoding_and_locale(
    admin: Any, cleanup: list[str], make_role: Callable[..., Role]
) -> None:
    owner = make_role()
    name = new_name("db_")
    cleanup.append(name)
    create_database(admin, name, owner=owner.name, template="template0", encoding="SQL_ASCII", locale="C")
    assert _database(admin, name) == (owner.name, "SQL_ASCII", "C", False)
    drop_database(admin, name)


@pytest.mark.security
@pytest.mark.parametrize("suffix", ['Mixed "Quote"; DROP DATABASE postgres; --', "space name", "Ünïcödé"])
def test_names_are_identifiers_never_sql(admin: Any, cleanup: list[str], suffix: str) -> None:
    """``psycopg.sql.Identifier`` quotes the name: a name with quotes and a statement in it is just a name."""
    name = PREFIX + suffix
    cleanup.append(name)
    create_database(admin, name)
    assert _database(admin, name) is not None
    assert _database(admin, "postgres") is not None
    assert database_sessions(admin, name) == 0
    drop_database(admin, name)
    assert _database(admin, name) is None


@pytest.mark.parametrize(
    ("kwargs", "parameter"),
    [
        ({"name": ""}, "name"),
        ({"name": "x" * 64}, "name"),
        ({"name": "ä" * 32}, "name"),  # 64 bytes in UTF-8, 32 characters
        ({"name": "a\x00b"}, "name"),
        ({"name": 42}, "name"),
        ({"owner": ""}, "owner"),
        ({"template": "template2"}, "template"),
        ({"template": "postgres"}, "template"),
        ({"encoding": "UTF8'; DROP DATABASE postgres; --"}, "encoding"),
        ({"encoding": ""}, "encoding"),
        ({"locale": "C' LC_CTYPE 'C"}, "locale"),
        ({"locale": 1}, "locale"),
    ],
)
def test_create_refuses_invalid_arguments(admin: Any, kwargs: dict[str, Any], parameter: str) -> None:
    arguments: dict[str, Any] = {"name": new_name("db_"), **kwargs}
    before = admin.execute("SELECT count(*) FROM pg_database").fetchone()
    with pytest.raises(UsageError) as caught:
        create_database(admin, arguments.pop("name"), **arguments)
    assert caught.value.code == "postgres.invalid_argument"
    assert dict(caught.value.details) == {"parameter": parameter}
    assert admin.execute("SELECT count(*) FROM pg_database").fetchone() == before


def test_longest_name_is_accepted(admin: Any, cleanup: list[str]) -> None:
    name = PREFIX + "x" * (63 - len(PREFIX))
    cleanup.append(name)
    create_database(admin, name)
    assert _database(admin, name) is not None
    drop_database(admin, name)


@pytest.mark.parametrize(("kwargs", "parameter"), [({"name": ""}, "name"), ({"force": 1}, "force")])
def test_drop_refuses_invalid_arguments(admin: Any, kwargs: dict[str, Any], parameter: str) -> None:
    arguments: dict[str, Any] = {"name": new_name("db_"), **kwargs}
    with pytest.raises(UsageError) as caught:
        drop_database(admin, arguments.pop("name"), **arguments)
    assert dict(caught.value.details) == {"parameter": parameter}


def test_sessions_refuses_an_invalid_name(admin: Any) -> None:
    with pytest.raises(UsageError):
        database_sessions(admin, "")


def test_autocommit_required_and_never_switched(connect: Callable[..., Any], cleanup: list[str]) -> None:
    """``CREATE``/``DROP DATABASE`` cannot run in a transaction block; Tabrion never switches ``autocommit``."""
    conn = connect()
    name = new_name("db_")
    cleanup.append(name)
    calls: list[Callable[[], None]] = [lambda: create_database(conn, name), lambda: drop_database(conn, name)]
    for call in calls:
        with pytest.raises(UsageError) as caught:
            call()
        assert caught.value.code == "postgres.autocommit_required"
        assert conn.autocommit is False
        assert conn.info.transaction_status == pq.TransactionStatus.IDLE
    auto = connect(autocommit=True)
    with auto.transaction():
        nested: list[Callable[[], None]] = [lambda: create_database(auto, name), lambda: drop_database(auto, name)]
        for call in nested:
            with pytest.raises(UsageError) as caught:
                call()
            assert caught.value.code == "postgres.nested_transaction"
    assert auto.autocommit is True
    assert _database(auto, name) is None


def test_sessions_reads_in_any_transaction_state(connect: Callable[..., Any]) -> None:
    conn = connect()
    assert database_sessions(conn, new_name("db_")) == 0
    assert conn.info.transaction_status == pq.TransactionStatus.IDLE
    with conn.transaction():
        assert database_sessions(conn, "postgres") >= 0
        assert conn.info.transaction_status == pq.TransactionStatus.INTRANS


def test_only_other_sessions_are_counted(admin: Any, admin_url: str) -> None:
    current = admin.info.dbname
    reference = "SELECT count(*) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()"
    with psycopg.connect(admin_url) as other:
        other.execute("SELECT 1")
        counted = database_sessions(admin, current)
        assert counted >= 1
        assert counted == admin.execute(reference, (current,)).fetchone()[0]


@pytest.mark.parametrize("name", ["postgres", "template0", "template1"])
def test_drop_refuses_system_databases(admin: Any, name: str) -> None:
    for force in (False, True):
        with pytest.raises(UsageError) as caught:
            drop_database(admin, name, force=force)
        assert caught.value.code == "postgres.database_protected"
    assert _database(admin, name) is not None


def test_drop_refuses_the_connections_own_database(scratch_database: str, admin_url: str) -> None:
    """A session on ``postgres`` stays open: should the name guard ever be missing, that session makes the drop below
    fail as in use (no ``force``) instead of dropping the server's ``postgres`` database."""
    with (
        psycopg.connect(with_params(admin_url, dbname="postgres"), autocommit=True) as keeper,
        psycopg.connect(scratch_database, autocommit=True) as own,
    ):
        keeper.execute("SELECT 1")
        for force in (False, True):
            with pytest.raises(UsageError) as caught:
                drop_database(own, own.info.dbname, force=force)
            assert caught.value.code == "postgres.database_protected"
        assert own.execute("SELECT 1").fetchone() == (1,)
        # From another database, ``postgres`` is protected by name (never with force here; see the docstring).
        with pytest.raises(UsageError) as caught:
            drop_database(own, "postgres")
        assert caught.value.code == "postgres.database_protected"


def test_drop_refuses_a_template_database(admin: Any, cleanup: list[str]) -> None:
    name = new_name("tpl_")
    cleanup.append(name)
    create_database(admin, name)
    admin.execute(sql.SQL("ALTER DATABASE {} IS_TEMPLATE true").format(sql.Identifier(name)))
    with pytest.raises(UsageError) as caught:
        drop_database(admin, name, force=True)
    assert caught.value.code == "postgres.database_protected"
    admin.execute(sql.SQL("ALTER DATABASE {} IS_TEMPLATE false").format(sql.Identifier(name)))
    drop_database(admin, name)


def test_drop_of_a_missing_database(admin: Any) -> None:
    with pytest.raises(UsageError) as caught:
        drop_database(admin, new_name("db_"))
    assert caught.value.code == "postgres.database_missing"


def test_force_drops_a_database_with_sessions(admin: Any, admin_url: str, cleanup: list[str]) -> None:
    name = new_name("db_")
    cleanup.append(name)
    create_database(admin, name)
    other = psycopg.connect(with_params(admin_url, dbname=name))
    try:
        assert database_sessions(admin, name) == 1
        drop_database(admin, name, force=True)
        assert _database(admin, name) is None
        with pytest.raises(psycopg.OperationalError):
            other.execute("SELECT 1")
    finally:
        other.close()


def test_session_that_connects_after_the_check_is_in_use(
    admin: Any, admin_url: str, cleanup: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A session that connects between the session count and ``DROP DATABASE`` (simulated: the count does not see
    it) makes PostgreSQL refuse with 55006; that is the same ``DatabaseInUse``, the driver error as ``original``."""
    name = new_name("db_")
    cleanup.append(name)
    create_database(admin, name)
    monkeypatch.setattr(databases, "_SESSIONS_SQL", "SELECT 0 WHERE %s::text IS NOT NULL")
    with psycopg.connect(with_params(admin_url, dbname=name)) as other:
        other.execute("SELECT 1")
        with pytest.raises(DatabaseInUse) as caught:
            drop_database(admin, name)
    assert caught.value.code == "postgres.database_in_use"
    assert isinstance(caught.value.original, psycopg.errors.ObjectInUse)
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    assert _database(admin, name) is not None
    assert admin.info.transaction_status == pq.TransactionStatus.IDLE


def test_create_without_createdb_is_refused(
    connect: Callable[..., Any], make_role: Callable[..., Role], admin: Any
) -> None:
    role = make_role()
    conn = connect(role.url, autocommit=True)
    name = new_name("db_")
    with pytest.raises(PermissionDenied) as caught:
        create_database(conn, name)
    assert caught.value.code == "postgres.createdb_required"
    assert caught.value.kind is Kind.PERMISSION
    assert caught.value.classification is not None and caught.value.classification.sqlstate == "42501"
    assert _database(admin, name) is None


def test_owner_rules(
    connect: Callable[..., Any], make_role: Callable[..., Role], admin: Any, cleanup: list[str]
) -> None:
    """``OWNER`` needs a role the connecting role can ``SET ROLE`` to; an unknown owner is ``RoleMissing``."""
    creator = make_role()
    admin.execute(sql.SQL("ALTER ROLE {} CREATEDB").format(sql.Identifier(creator.name)))
    stranger = make_role()
    conn = connect(creator.url, autocommit=True)
    name = new_name("db_")
    cleanup.append(name)
    with pytest.raises(PermissionDenied) as denied:
        create_database(conn, name, owner=stranger.name)
    assert denied.value.code == "postgres.owner_not_allowed"
    with pytest.raises(RoleMissing):
        create_database(conn, name, owner=new_name("nobody_"))
    assert _database(admin, name) is None
    admin.execute(sql.SQL("GRANT {} TO {}").format(sql.Identifier(stranger.name), sql.Identifier(creator.name)))
    try:
        create_database(conn, name, owner=stranger.name)
        assert (_database(admin, name) or ())[0] == stranger.name
        drop_database(admin, name)
        create_database(conn, name, owner=creator.name)
        assert (_database(admin, name) or ())[0] == creator.name
        drop_database(conn, name)  # the owner may drop its database
        assert _database(admin, name) is None
    finally:
        admin.execute(sql.SQL("REVOKE {} FROM {}").format(sql.Identifier(stranger.name), sql.Identifier(creator.name)))


def test_driver_errors_propagate_unchanged(
    connect: Callable[..., Any], make_role: Callable[..., Role], admin: Any, cleanup: list[str]
) -> None:
    """An existing database and a drop without ownership: the driver's errors, not Tabrion's."""
    name = new_name("db_")
    cleanup.append(name)
    create_database(admin, name)
    with pytest.raises(psycopg.errors.DuplicateDatabase):
        create_database(admin, name)
    other = connect(make_role().url, autocommit=True)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        drop_database(other, name)
    assert _database(admin, name) is not None
    drop_database(admin, name)


def test_row_factory_is_left_alone(connect: Callable[..., Any], cleanup: list[str]) -> None:
    from psycopg.rows import dict_row

    conn = connect(autocommit=True, row_factory=dict_row)
    name = new_name("db_")
    cleanup.append(name)
    create_database(conn, name)
    assert database_sessions(conn, name) == 0
    drop_database(conn, name)
    assert conn.row_factory is dict_row
    assert conn.execute("SELECT 1 AS one").fetchone() == {"one": 1}


def test_force_needs_postgresql_13(admin: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(databases, "_FORCE_VERSION", admin.info.server_version + 1)
    with pytest.raises(UsageError) as caught:
        drop_database(admin, new_name("db_"), force=True)
    assert caught.value.code == "postgres.force_requires_13"


def test_owner_check_uses_membership_before_16(
    connect: Callable[..., Any], make_role: Callable[..., Role], admin: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before PostgreSQL 16 there is no ``SET`` option: membership decides (``pg_has_role(..., 'MEMBER')``)."""
    monkeypatch.setattr(databases, "_SET_OPTION_VERSION", admin.info.server_version + 1)
    creator = make_role()
    admin.execute(sql.SQL("ALTER ROLE {} CREATEDB").format(sql.Identifier(creator.name)))
    stranger = make_role()
    conn = connect(creator.url, autocommit=True)
    with pytest.raises(PermissionDenied):
        create_database(conn, new_name("db_"), owner=stranger.name)


def test_type_error_for_other_connections() -> None:
    with pytest.raises(TypeError):
        create_database(object(), "x")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        drop_database(object(), "x")  # type: ignore[arg-type]
