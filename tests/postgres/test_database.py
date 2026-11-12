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
    PermissionDenied,
    ReadOnlyViolation,
    StatementError,
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


PARAMETER_CAST_HINT = "write CAST(:name AS type) instead of :name::type"


@pytest.mark.security
def test_t025_double_colon_cast_carries_a_hint_on_postgres(db: Database) -> None:
    """TAB-T-025 (planning document 07, probe P5; contract I-107): ``text()`` leaves ``:amount_7f`` before ``::``
    unbound, so ``:amount_7f::int`` reaches the server as written and fails with ``Kind.SYNTAX`` (SQLSTATE 42601).
    The error carries a fixed hint in ``details``, never the parameter, its value or the server's text. The form the
    guides recommend, ``CAST(:x AS int)``, works, and a cast without a parameter is unaffected."""
    with pytest.raises(StatementError) as info:
        db.fetch_scalar("SELECT :amount_7f::int", {"amount_7f": SECRET})
    error = info.value
    assert error.kind is Kind.SYNTAX
    assert error.classification is not None and error.classification.sqlstate == "42601"
    assert error.details == {"hint": PARAMETER_CAST_HINT}
    assert str(error) == f"database.syntax (hint={PARAMETER_CAST_HINT}): syntax error (SQLSTATE 42601)"
    text = str(error) + repr(error) + "".join(traceback.format_exception(error))
    assert SECRET not in text
    assert "amount_7f" not in str(error) + repr(error)  # the traceback's source lines show the test's own text
    assert db.fetch_scalar("SELECT CAST(:x AS int) + 1", {"x": "41"}) == 42
    assert db.fetch_scalar("SELECT '41'::int + :y", {"y": 1}) == 42
    with pytest.raises(StatementError) as other:
        db.fetch_scalar("SELEC :x", {"x": 1})
    assert other.value.kind is Kind.SYNTAX and other.value.details == {}


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


# Review of #12 (fix round): read-only lifted by the caller's SQL (H-1, contract I-56), session settings in the
# pool (M-1), a failed checkout (L-3, M28).

LIFTS = ["SET TRANSACTION READ WRITE", "SET transaction_read_only = off"]


@pytest.mark.security
@pytest.mark.parametrize("read_only_profile", [False, True], ids=["default", "readonly"])
@pytest.mark.parametrize("lift", LIFTS)
def test_b12_read_only_lifted_in_read_is_not_committed(
    open_db: Callable[..., Database], db: Database, schema: str, lift: str, read_only_profile: bool
) -> None:
    database = open_db(Profile(read_only=read_only_profile, pool_size=1))
    with pytest.raises(UsageError) as info, database.read() as tx:
        tx.execute(lift)
        tx.execute(f"INSERT INTO {schema}.items (id, n) VALUES (1, 1)")
    assert info.value.code == "database.read_only"
    assert db.fetch_scalar(f"SELECT count(*) FROM {schema}.items") == 0
    with database.read() as tx:
        assert tx.fetch_scalar("SHOW transaction_read_only") == "on"


@pytest.mark.security
def test_b12_session_default_lifted_in_read_is_rolled_back(
    open_db: Callable[..., Database], db: Database, schema: str
) -> None:
    """Review H-1 (``dtro.py``): the session default would stay off on the pooled connection; it is rolled back."""
    database = open_db(Profile(read_only=True, pool_size=1))
    with pytest.raises(UsageError, match=r"database\.read_only"), database.read() as tx:
        tx.execute("SET default_transaction_read_only = off")
    assert database.fetch_scalar("SHOW default_transaction_read_only") == "on"
    with database.engine.connect() as plain, pytest.raises(sqlalchemy.exc.InternalError):
        plain.exec_driver_sql(f"INSERT INTO {schema}.items (id, n) VALUES (1, 1)")


@pytest.mark.security
@pytest.mark.parametrize("read_only_profile", [False, True], ids=["default", "readonly"])
def test_b12_documents_the_gap_transaction_control_in_read(
    open_db: Callable[..., Database], db: Database, schema: str, read_only_profile: bool
) -> None:
    """Documents the gap of contract I-56 (plan 03:245-247): SQL that ends the transaction itself and commits a
    READ WRITE one writes. Only a role without write rights stops it (SR-03)."""
    database = open_db(Profile(read_only=read_only_profile, pool_size=1))
    with database.read() as tx:
        tx.execute("ROLLBACK")
        tx.execute("BEGIN READ WRITE")
        tx.execute(f"INSERT INTO {schema}.items (id, n) VALUES (1, 1)")
        tx.execute("COMMIT")
    assert db.fetch_scalar(f"SELECT count(*) FROM {schema}.items") == 1


def test_b12_documents_session_settings_stay_on_the_pooled_connection(open_db: Callable[..., Database]) -> None:
    """Documents contract I-56 and TM-08: a SET without LOCAL in a Tabrion transaction stays on the connection;
    SET LOCAL (or set_config(..., true)) ends with the transaction. Tabrion runs no RESET or DISCARD."""
    database = open_db(Profile(pool_size=1))
    with database.read() as tx:
        tx.execute("SET work_mem = '7MB'")
        tx.execute("SET LOCAL maintenance_work_mem = '9MB'")
    with database.transaction() as tx:
        assert tx.fetch_scalar("SHOW work_mem") == "7MB"
        assert tx.fetch_scalar("SHOW maintenance_work_mem") != "9MB"


def test_b09_failed_checkout_is_retryable_with_outcome_known(url: Any) -> None:
    unreachable = url.set(host="127.0.0.1", port=1)
    database = Database.from_url(unreachable, profile=Profile(connect_timeout=2))
    try:
        with pytest.raises(ConnectionFailed) as info:
            database.fetch_scalar("SELECT 1")
    finally:
        database.close()
    classification = info.value.classification
    assert classification is not None and classification.kind is Kind.CONNECTION
    assert classification.retryable and not classification.outcome_unknown
    assert info.value.__cause__ is None and info.value.__context__ is None


# Re-review of #12 (second fix round): N-1 (TB-061), N-3, N-4.


@pytest.mark.security
@pytest.mark.parametrize(
    "restore", ["SET transaction_read_only = on", "SELECT set_config('transaction_read_only', 'on', true)"]
)
def test_b12_documents_the_gap_lift_write_restore(
    open_db: Callable[..., Database], db: Database, schema: str, restore: str
) -> None:
    """Documents the gap of contract I-56 and TB-061: the read-back sees the state at the end only."""
    database = open_db(Profile(pool_size=1))
    with database.read() as tx:
        tx.execute("SET TRANSACTION READ WRITE")
        tx.execute(f"INSERT INTO {schema}.items (id, n) VALUES (1, 1)")
        tx.execute(restore)
    assert db.fetch_scalar(f"SELECT count(*) FROM {schema}.items") == 1


@pytest.mark.security
def test_b12_documents_the_gap_session_default_restored_locally(open_db: Callable[..., Database]) -> None:
    """Documents I-56/TB-061: SET LOCAL hides a session default switched off in the same read()."""
    database = open_db(Profile(read_only=True, pool_size=1))
    with database.read() as tx:
        tx.execute("SET default_transaction_read_only = off")
        tx.execute("SET LOCAL default_transaction_read_only = on")
    # Committed: the session default is off on the pooled connection. The next read() sees it, refuses, and
    # invalidates the connection, so the one after gets a fresh connection with the profile again.
    with pytest.raises(UsageError, match=r"database\.read_only"):
        database.fetch_scalar("SHOW default_transaction_read_only")
    assert database.fetch_scalar("SHOW default_transaction_read_only") == "on"


def test_b06_from_engine_read_only_without_listeners_reads(url: Any, schema: str) -> None:
    """Re-review N-3: without listeners nobody sets default_transaction_read_only; read() still works."""
    engine = sqlalchemy.create_engine(url.set(drivername="postgresql+psycopg"), pool_size=1, max_overflow=0)
    try:
        with Database.from_engine(engine, profile=Profile.readonly(), install_listeners=False) as database:
            assert database.fetch_scalar("SELECT 41 + 1") == 42
            with pytest.raises(UsageError, match=r"database\.read_only"), database.read() as tx:
                tx.execute("SET TRANSACTION READ WRITE")
            with pytest.raises(UsageError, match=r"database\.read_only"), database.transaction():
                pass  # pragma: no cover
    finally:
        engine.dispose()


@pytest.mark.security
def test_b12_read_back_ignores_the_callers_row_factory(db: Database, schema: str, admin: Any) -> None:
    with pytest.raises(UsageError, match=r"database\.read_only"), db.read() as tx:
        dbapi = tx.connection.connection.dbapi_connection
        assert dbapi is not None
        dbapi.row_factory = lambda cursor: lambda values: ("on", "on")
        tx.execute("SET TRANSACTION READ WRITE")
        tx.execute(f"INSERT INTO {schema}.items (id, n) VALUES (1, 1)")
    assert admin.execute(f"SELECT count(*) FROM {schema}.items").fetchone() == (0,)


def test_b12_a_failing_read_back_does_not_commit(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    import tabrion._sa.database

    monkeypatch.setattr(tabrion._sa.database, "_READ_ONLY_STATE_SQL", "SELECT no_such_column")
    with pytest.raises(UsageError, match=r"database\.read_only"), db.read() as tx:
        assert tx.fetch_scalar("SELECT 1") == 1
    monkeypatch.undo()
    assert db.fetch_scalar("SELECT 1") == 1  # the pool hands out a working connection again


# --- TAB-T-161 (planning document 07, TM-08): a connection with an open transaction is never reused ----------------


@pytest.mark.parametrize("failure", ["statement", "exception", "interrupt", "after-a-savepoint"])
def test_t161_next_checkout_after_a_failed_transaction_is_idle(
    open_db: Callable[..., Database], schema: str, admin: Any, failure: str
) -> None:
    """TM-08: a transaction that ends with a statement error, an exception, a ``KeyboardInterrupt`` or a block that
    breaks off after a write never leaves its transaction to the next user of the pooled connection. With one pooled
    connection, the backend is ``idle`` on the server (or gone, when the connection was discarded), and the next
    transaction starts fresh: no transaction ID assigned, no tenant setting, no uncommitted row."""
    database = open_db(pool_size=1, max_overflow=0)
    database.execute(f"CREATE TABLE {schema}.items (id int PRIMARY KEY)")
    errors: dict[str, type[BaseException]] = {
        "statement": StatementError,
        "exception": RuntimeError,
        "interrupt": KeyboardInterrupt,
        "after-a-savepoint": RuntimeError,
    }
    backend: list[int] = []
    with pytest.raises(errors[failure]), database.transaction() as tx:
        backend.append(int(tx.fetch_scalar("SELECT pg_backend_pid()")))
        tx.execute("SELECT set_config('app.tenant_id', 'tenant-a', true)")
        tx.execute(f"INSERT INTO {schema}.items VALUES (1)")
        if failure == "statement":
            tx.execute("SELECT * FROM t_does_not_exist")
        elif failure == "interrupt":
            raise KeyboardInterrupt
        else:
            if failure == "after-a-savepoint":
                tx.execute("SAVEPOINT s")
            raise RuntimeError("the block broke off")
    state = admin.execute("SELECT state FROM pg_stat_activity WHERE pid = %s", (backend[0],)).fetchone()
    assert state is None or state[0] == "idle"
    assert database.engine.pool.checkedout() == 0  # type: ignore[attr-defined]
    with database.transaction() as tx:
        pid = int(tx.fetch_scalar("SELECT pg_backend_pid()"))
        assert pid == backend[0] or state is None
        assert tx.fetch_scalar("SELECT pg_current_xact_id_if_assigned()") is None
        assert tx.fetch_scalar("SELECT current_setting('app.tenant_id', true)") in (None, "")
        assert tx.fetch_scalar(f"SELECT count(*) FROM {schema}.items") == 0
    assert admin.execute(f"SELECT count(*) FROM {schema}.items").fetchone() == (0,)


# --- TAB-T-162 (planning document 07, SR-10): row-level security, two tenants through transaction() ----------------


@pytest.mark.security
def test_t162_rls_two_tenants_through_transaction(
    admin: Any, tabrion_postgres_schema: Any, make_role: Callable[..., Any]
) -> None:
    """SR-10: a table with ``FORCE ROW LEVEL SECURITY`` owned by a role without superuser; the runtime role (granted
    with ``grant_runtime_role``, not the owner, ``NOBYPASSRLS``) works through ``Database.transaction()`` with
    ``set_config('app.tenant_id', ..., true)``. Each tenant sees, changes and adds only its own rows; the next
    transaction on the same pooled connection starts without a tenant and sees nothing (TM-08)."""
    from tabrion.postgres import grant_runtime_role

    schema = tabrion_postgres_schema.name
    owner = make_role()
    app = make_role()
    admin.execute(f'ALTER SCHEMA {schema} OWNER TO "{owner.name}"')
    with psycopg.connect(owner.url, autocommit=True, connect_timeout=10) as conn:
        conn.execute(f"CREATE TABLE {schema}.notes (id int PRIMARY KEY, tenant text NOT NULL, body text NOT NULL)")
        conn.execute(f"ALTER TABLE {schema}.notes ENABLE ROW LEVEL SECURITY")
        conn.execute(f"ALTER TABLE {schema}.notes FORCE ROW LEVEL SECURITY")
        conn.execute(
            f"CREATE POLICY tenant_isolation ON {schema}.notes"
            " USING (tenant = current_setting('app.tenant_id', true))"
            " WITH CHECK (tenant = current_setting('app.tenant_id', true))"
        )
        report = grant_runtime_role(conn, app.name, schema=schema)
    assert report.restricted  # row-level security fully applies to the runtime role

    def as_tenant(tx: Any, tenant: str) -> None:
        tx.execute("SELECT set_config('app.tenant_id', :t, true)", {"t": tenant})

    # One pooled connection: every transaction below runs on the same backend.
    with Database.from_url(
        sa_url(app.url),
        profile=Profile(application_name=new_name("t_rls_")),
        engine_options={"pool_size": 1, "max_overflow": 0},
    ) as database:
        with database.transaction() as tx:
            assert tx.fetch_scalar("SELECT current_user") == app.name
            backend = tx.fetch_scalar("SELECT pg_backend_pid()")
            as_tenant(tx, "tenant-a")
            tx.execute(f"INSERT INTO {schema}.notes VALUES (1, 'tenant-a', 'a1'), (2, 'tenant-a', 'a2')")
        with database.transaction() as tx:
            as_tenant(tx, "tenant-b")
            tx.execute(f"INSERT INTO {schema}.notes VALUES (3, 'tenant-b', 'b1')")

        for tenant, ids in (("tenant-a", [1, 2]), ("tenant-b", [3])):
            with database.transaction() as tx:
                assert tx.fetch_scalar("SELECT current_setting('app.tenant_id', true)") in (None, "")
                as_tenant(tx, tenant)
                assert tx.fetch_scalar("SELECT pg_backend_pid()") == backend
                assert [row.id for row in tx.fetch_all(f"SELECT id FROM {schema}.notes ORDER BY id")] == ids
                # The other tenant's rows can be neither changed nor deleted.
                assert tx.execute(f"UPDATE {schema}.notes SET body = 'x' WHERE tenant <> :t", {"t": tenant}) == 0
                assert tx.execute(f"DELETE FROM {schema}.notes WHERE tenant <> :t", {"t": tenant}) == 0

        # A row for the other tenant is refused by WITH CHECK, and the transaction rolls back.
        with pytest.raises(PermissionDenied) as refused, database.transaction() as tx:
            as_tenant(tx, "tenant-a")
            tx.execute(f"INSERT INTO {schema}.notes VALUES (4, 'tenant-b', 'planted')")
        assert refused.value.kind is Kind.PERMISSION

        # Without a tenant (the setting ended with the previous transaction), nothing is visible.
        with database.transaction() as tx:
            assert tx.fetch_scalar("SELECT pg_backend_pid()") == backend
            assert tx.fetch_scalar(f"SELECT count(*) FROM {schema}.notes") == 0

    # FORCE applies to the owner too; the superuser sees every row, and none was changed or planted.
    with psycopg.connect(owner.url, autocommit=True, connect_timeout=10) as conn:
        assert conn.execute(f"SELECT count(*) FROM {schema}.notes").fetchone() == (0,)
    rows = admin.execute(f"SELECT id, tenant, body FROM {schema}.notes ORDER BY id").fetchall()
    assert rows == [(1, "tenant-a", "a1"), (2, "tenant-a", "a2"), (3, "tenant-b", "b1")]


# --- TAB-T-025 (planning document 07, probe P5): ``:x::int`` collides with the parameter syntax ---------------------


def test_t025_double_colon_cast_is_a_syntax_error(db: Database) -> None:
    """Probe P5: SQLAlchemy's ``text()`` does not read ``:x`` before ``::`` as a parameter, so ``:x::int`` reaches the
    server as written and fails with ``Kind.SYNTAX`` (SQLSTATE 42601); the value is never bound. The form the guides
    recommend, ``CAST(:x AS int)``, works. The message is the classification and the fixed hint of contract I-107
    (TODO TB-139), without the server's text."""
    with pytest.raises(StatementError) as info:
        db.fetch_scalar("SELECT :x::int", {"x": "41"})
    error = info.value
    assert error.kind is Kind.SYNTAX
    assert error.classification is not None and error.classification.sqlstate == "42601"
    assert error.details == {"hint": PARAMETER_CAST_HINT}
    assert str(error) == f"database.syntax (hint={PARAMETER_CAST_HINT}): syntax error (SQLSTATE 42601)"
    assert "41" not in str(error) and "41" not in repr(error)
    assert db.fetch_scalar("SELECT CAST(:x AS int) + 1", {"x": "41"}) == 42
    # The connection is usable after the error; a literal cast without a parameter is unaffected.
    assert db.fetch_scalar("SELECT '41'::int + 1") == 42
