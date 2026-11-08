"""Layer B on PostgreSQL (TAB-018): ``Database`` and ``Transaction`` against a server (TAB-T-B02..B24).

Every table lives in the ``schema`` fixture (``t_<hex>``, dropped afterwards); every engine uses an
``application_name`` of its own, so the counts in ``pg_stat_activity`` see only this test's sessions.
"""

# The SQL is built from the schema fixture's generated name and fixed text, never from outside input.
# ruff: noqa: S608

from __future__ import annotations

from collections.abc import Callable, Iterator
import threading
import traceback
from typing import Any

import pytest

from tests.postgres.conftest import new_name

pytestmark = pytest.mark.postgres

sqlalchemy = pytest.importorskip("sqlalchemy")
psycopg = pytest.importorskip("psycopg")

from tabrion import Database, Profile  # noqa: E402
from tabrion.errors import (  # noqa: E402
    ConnectionFailed,
    IntegrityViolation,
    Kind,
    ReadOnlyViolation,
    StatementTimeout,
    TabrionError,
    UsageError,
)

SECRET = "SECRET-4711"


def sa_url(conninfo: str) -> Any:
    params = psycopg.conninfo.conninfo_to_dict(conninfo)
    port = params.get("port")
    return sqlalchemy.URL.create(
        "postgresql",
        username=params.get("user"),
        password=params.get("password"),
        host=params.get("host"),
        port=int(port) if port else None,
        database=params.get("dbname"),
    )


@pytest.fixture
def url(admin_url: str) -> Any:
    return sa_url(admin_url)


@pytest.fixture
def open_db(url: Any) -> Iterator[Callable[..., Database]]:
    """``open_db(profile=None, **engine_options)``: a ``Database`` with an application name of its own."""
    opened: list[Database] = []

    def factory(profile: Profile | None = None, **options: Any) -> Database:
        profile = profile or Profile()
        if profile.application_name == "tabrion":
            profile = Profile(**{**_fields(profile), "application_name": new_name("t_app_")})
        database = Database.from_url(url, profile=profile, engine_options=options or None)
        opened.append(database)
        return database

    yield factory
    for database in opened:
        database.close()


def _fields(profile: Profile) -> dict[str, Any]:
    return {name: getattr(profile, name) for name in Profile.__dataclass_fields__}


@pytest.fixture
def db(open_db: Callable[..., Database], schema: str) -> Database:
    database = open_db()
    database.execute(f"CREATE TABLE {schema}.items (id int PRIMARY KEY, n int NOT NULL, label text UNIQUE)")
    return database


def sessions(admin: Any, application_name: str) -> int:
    row = admin.execute(
        "SELECT count(*) FROM pg_stat_activity WHERE application_name = %s", (application_name,)
    ).fetchone()
    return int(row[0])


def test_b02_session_settings_and_application_name(open_db: Callable[..., Database]) -> None:
    database = open_db(Profile.testing())
    assert database.dialect == "postgresql"
    with database.read() as tx:
        assert tx.fetch_scalar("SHOW application_name") == database._profile.application_name
        assert tx.fetch_scalar("SHOW statement_timeout") == "5s"
        assert tx.fetch_scalar("SHOW lock_timeout") == "2s"
        assert tx.fetch_scalar("SHOW TimeZone") == "UTC"
        assert tx.fetch_scalar("SHOW transaction_read_only") == "on"
    with database.transaction() as tx:
        assert tx.fetch_scalar("SHOW transaction_read_only") == "off"
        assert tx.fetch_scalar("SHOW transaction_isolation") == "read committed"


def test_b02_no_second_connection(open_db: Callable[..., Database], admin: Any) -> None:
    """The session settings run on the pool's own connection; there is no second session (specification 3.1)."""
    database = open_db(Profile(pool_size=1))
    name = database._profile.application_name
    connects: list[object] = []
    sqlalchemy.event.listen(database.engine, "connect", lambda dbapi, record: connects.append(dbapi))
    for _ in range(5):
        database.fetch_scalar("SELECT 1")
        with database.transaction() as tx:
            tx.execute("SELECT 1")
        database.health()
    assert len(connects) == 1
    assert sessions(admin, name) == 1


def test_b06_from_engine(url: Any, admin: Any) -> None:
    engine = sqlalchemy.create_engine(url.set(drivername="postgresql+psycopg"), pool_size=1, max_overflow=0)
    name = new_name("t_app_")
    try:
        with engine.connect() as plain:  # a connection already in the pool before the Database exists
            assert plain.exec_driver_sql("SHOW statement_timeout").scalar() == "0"
        with Database.from_engine(engine, profile=Profile(application_name=name)) as database:
            assert database.fetch_scalar("SHOW application_name") == name
            assert database.fetch_scalar("SHOW statement_timeout") == "30s"
        with engine.connect() as plain:
            assert plain.exec_driver_sql("SELECT 1").scalar() == 1  # not disposed
        assert sessions(admin, name) == 1
    finally:
        engine.dispose()


@pytest.mark.slow
def test_b07_no_lost_update_with_for_update(db: Database, schema: str) -> None:
    db.execute(f"INSERT INTO {schema}.items (id, n) VALUES (1, 0)")
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            with db.transaction() as tx:
                n = tx.fetch_scalar(f"SELECT n FROM {schema}.items WHERE id = 1 FOR UPDATE")
                tx.execute(f"UPDATE {schema}.items SET n = :n WHERE id = 1", {"n": n + 1})
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert db.fetch_scalar(f"SELECT n FROM {schema}.items WHERE id = 1") == 5


def test_b08_rollback(db: Database, schema: str) -> None:
    with pytest.raises(RuntimeError), db.transaction() as tx:
        tx.execute(f"INSERT INTO {schema}.items (id, n) VALUES (1, 1)")
        raise RuntimeError
    assert db.fetch_scalar(f"SELECT count(*) FROM {schema}.items") == 0


def test_b09_errors_and_commit_errors(db: Database, schema: str) -> None:
    db.execute(f"INSERT INTO {schema}.items (id, n) VALUES (1, 1)")
    with pytest.raises(IntegrityViolation) as info:
        db.execute(f"INSERT INTO {schema}.items (id, n) VALUES (1, 2)")
    assert info.value.kind is Kind.UNIQUE
    assert info.value.classification is not None and info.value.classification.sqlstate == "23505"
    assert isinstance(info.value.original, psycopg.errors.UniqueViolation)
    assert info.value.__context__ is None
    db.execute(
        f"CREATE TABLE {schema}.deferred (id int, CONSTRAINT deferred_id UNIQUE (id) DEFERRABLE INITIALLY DEFERRED)"
    )
    with pytest.raises(IntegrityViolation) as commit, db.transaction() as tx:
        tx.execute(f"INSERT INTO {schema}.deferred VALUES (1), (1)")  # the check runs at COMMIT
    assert commit.value.classification is not None
    assert commit.value.classification.constraint == "deferred_id"
    assert db.fetch_scalar(f"SELECT count(*) FROM {schema}.deferred") == 0


def test_b09_connection_lost_before_commit_is_outcome_unknown(open_db: Callable[..., Database], admin: Any) -> None:
    database = open_db(Profile(pool_size=1))
    with pytest.raises(ConnectionFailed) as info, database.transaction() as tx:
        pid = tx.fetch_scalar("SELECT pg_backend_pid()")
        admin.execute("SELECT pg_terminate_backend(%s)", (pid,))
    classification = info.value.classification
    assert classification is not None
    assert classification.kind is Kind.CONNECTION
    assert classification.outcome_unknown and not classification.retryable
    assert database.fetch_scalar("SELECT 1") == 1  # the pool replaced the dead connection
    assert database.engine.pool.checkedout() == 0  # type: ignore[attr-defined]


def test_b10_nesting(db: Database) -> None:
    with db.read():
        with pytest.raises(UsageError) as info, db.transaction():
            pass  # pragma: no cover
        assert info.value.code == "database.nested_transaction"


def test_b12_read_is_enforced_by_postgres(db: Database, schema: str) -> None:
    with db.read() as tx:
        with pytest.raises(ReadOnlyViolation) as info:
            tx.execute(f"INSERT INTO {schema}.items (id, n) VALUES (1, 1)")
        assert info.value.classification is not None and info.value.classification.sqlstate == "25006"
    assert db.fetch_scalar(f"SELECT count(*) FROM {schema}.items") == 0


def test_b12_read_only_profile(open_db: Callable[..., Database], db: Database, schema: str) -> None:
    database = open_db(Profile(read_only=True, pool_size=1))
    with pytest.raises(UsageError) as info, database.transaction():
        pass  # pragma: no cover
    assert info.value.code == "database.read_only"
    assert database.fetch_scalar("SHOW default_transaction_read_only") == "on"
    assert database.fetch_scalar(f"SELECT count(*) FROM {schema}.items") == 0
    # In the database, also on a plain connection, and still after a read() (SQLAlchemy's reset of
    # postgresql_readonly would send BEGIN READ WRITE; contract I-42).
    with database.engine.connect() as plain:
        with pytest.raises(sqlalchemy.exc.InternalError) as plain_error:
            plain.exec_driver_sql(f"INSERT INTO {schema}.items (id, n) VALUES (1, 1)")
        assert plain_error.value.orig.sqlstate == "25006"


def test_b12_read_leaves_no_read_write_override(open_db: Callable[..., Database]) -> None:
    database = open_db(Profile(pool_size=1))
    with database.read() as tx:
        driver = tx.connection.connection.dbapi_connection
        assert tx.fetch_scalar("SHOW transaction_read_only") == "on"
    assert driver is not None and driver.read_only is None


def test_b14_timeout_on_postgres(db: Database) -> None:
    with pytest.raises(StatementTimeout) as info:
        db.fetch_scalar("SELECT pg_sleep(5)", timeout=0.2)
    assert info.value.kind is Kind.TIMEOUT
    assert info.value.classification is not None and info.value.classification.sqlstate == "57014"
    with db.transaction() as tx:
        assert tx.fetch_scalar("SHOW statement_timeout", timeout=3) == "3s"
        assert tx.fetch_scalar("SHOW statement_timeout") == "30s"  # the profile value is back
        assert tx.fetch_scalar("SHOW statement_timeout", timeout=0.0001) == "1ms"
    with db.transaction(timeout=2) as tx:
        assert tx.fetch_scalar("SHOW statement_timeout") == "2s"
        assert tx.fetch_scalar("SHOW statement_timeout", timeout=4) == "4s"
        assert tx.fetch_scalar("SHOW statement_timeout") == "2s"
    with db.read() as tx:
        assert tx.fetch_scalar("SHOW statement_timeout") == "30s"  # SET LOCAL ended with its transaction


def test_isolation_levels(db: Database) -> None:
    for level, shown in (("repeatable_read", "repeatable read"), ("serializable", "serializable")):
        with db.transaction(isolation=level) as tx:  # type: ignore[arg-type]
            assert tx.fetch_scalar("SHOW transaction_isolation") == shown
    with db.transaction() as tx:
        assert tx.fetch_scalar("SHOW transaction_isolation") == "read committed"


def test_b16_executemany(db: Database, schema: str) -> None:
    rows = [{"id": n, "n": n} for n in range(10)]
    assert db.execute(f"INSERT INTO {schema}.items (id, n) VALUES (:id, :n)", rows) == 10
    assert db.execute(f"UPDATE {schema}.items SET n = 0 WHERE id < :limit", {"limit": 4}) == 4
    assert db.execute(f"CREATE INDEX ON {schema}.items (n)") == 0


@pytest.mark.security
def test_b18_no_parameter_value_in_errors(db: Database, schema: str) -> None:
    db.execute(f"INSERT INTO {schema}.items (id, n, label) VALUES (1, 1, :label)", {"label": SECRET})
    failures: list[TabrionError] = []
    for call in (
        lambda: db.execute(f"INSERT INTO {schema}.items (id, n, label) VALUES (2, 1, :label)", {"label": SECRET}),
        lambda: db.fetch_all(f"SELECT * FROM {schema}.items WHERE label = :v::int", {"v": SECRET}),
        lambda: db.fetch_all("SELECT pg_sleep(2), :v", {"v": SECRET}, timeout=0.1),
    ):
        with pytest.raises(TabrionError) as info:
            call()
        failures.append(info.value)
    for error in failures:
        text = str(error) + repr(error) + "".join(traceback.format_exception(error))
        assert SECRET not in text
        assert error.__cause__ is None and error.__context__ is None


def test_b21_connections_return_after_errors(db: Database, schema: str) -> None:
    pool = db.engine.pool
    for call in (
        lambda: db.execute(f"INSERT INTO {schema}.items (id, n) VALUES (NULL, 1)"),
        lambda: db.fetch_one("SELECT * FROM t_does_not_exist"),
        lambda: db.fetch_one("SELECT pg_sleep(1)", timeout=0.05),
    ):
        with pytest.raises(TabrionError):
            call()
        assert pool.checkedout() == 0  # type: ignore[attr-defined]
    assert db.health().pool == {"checked_out": 0, "size": 5, "overflow": 0}


def test_b24_health(db: Database) -> None:
    health = db.health()
    assert health.ok and health.dialect == "postgresql" and health.classification is None
    assert health.server_version is not None and health.server_version.isdigit()
    assert health.latency_ms is not None
    assert db.health(deep=True).ok


def test_b24_health_never_raises_when_unreachable(url: Any) -> None:
    unreachable = url.set(host="127.0.0.1", port=1)
    with Database.from_url(unreachable, profile=Profile(connect_timeout=2)) as database:
        health = database.health(timeout=1)
    assert not health.ok
    assert health.classification is not None and health.classification.kind is Kind.CONNECTION
    assert not health.classification.outcome_unknown
    password = url.password
    assert password is None or password not in repr(health)
