"""Layer B on SQLite (TAB-018): ``Profile``, ``Database``, ``Transaction``, health (TAB-T-B01..B25).

The PostgreSQL side is ``tests/postgres/test_database.py``. Test numbers follow contract sections 5.2, 5.3 and 5.5;
B01..B06 and B23..B25 are named in implementation note I-37 (the plan gives only their ranges).
"""

from __future__ import annotations

from collections.abc import Iterator
import dataclasses
import gc
import os
from pathlib import Path
import sqlite3
import sys
import threading
import time
import traceback
import types
from typing import Any
import uuid

import pytest

sqlalchemy = pytest.importorskip("sqlalchemy")
pytest.importorskip("psycopg")  # from_url for PostgreSQL URLs builds the session configurator

from tabrion import Database, Profile, TLSMode, Transaction  # noqa: E402
import tabrion._sa.database  # noqa: E402
import tabrion._sa.events  # noqa: E402
import tabrion.check  # noqa: E402
from tabrion.errors import (  # noqa: E402
    Classification,
    ConfigurationError,
    ConnectionFailed,
    IntegrityViolation,
    Kind,
    MultipleRowsError,
    ReadOnlyViolation,
    StatementError,
    StatementTimeout,
    StorageCorrupt,
    TabrionError,
    UnsafePath,
    UsageError,
    classify,
)
from tabrion.postgres import SessionSettings  # noqa: E402
import tabrion.sqlite  # noqa: E402
from tabrion.sqlite import JournalMode, SQLiteProfile, Synchronous  # noqa: E402

POSIX = os.name == "posix"
SECRET = "SECRET-4711"
COUNT_FOREVER = "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT count(*) FROM c"


@pytest.fixture
def path(tmp_path: Path) -> Path:
    return tmp_path / "layer-b.db"


@pytest.fixture
def db(path: Path) -> Iterator[Database]:
    database = Database.from_url(f"sqlite:///{path}")
    database.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, n INTEGER NOT NULL, label TEXT)")
    try:
        yield database
    finally:
        database.close()


def checked_out(database: Database) -> int:
    pool = database.engine.pool
    assert isinstance(pool, sqlalchemy.pool.QueuePool)
    return int(pool.checkedout())


# ---------------------------------------------------------------------------------------------------------------------
# Profile (contract 5.1, specification 4.3)
# ---------------------------------------------------------------------------------------------------------------------


def test_profile_defaults_follow_the_specification() -> None:
    profile = Profile.default()
    assert profile == Profile()
    assert (profile.pool_size, profile.max_overflow, profile.pool_timeout) == (5, 0, 10.0)
    assert (profile.pool_pre_ping, profile.pool_recycle, profile.connect_timeout) == (True, 1800.0, 10.0)
    assert (profile.tls, profile.max_rows, profile.read_only, profile.application_name) == (
        TLSMode.AUTO,
        10_000,
        False,
        "tabrion",
    )
    assert profile.sqlite == SQLiteProfile()
    assert profile.postgres == SessionSettings()


def test_profile_presets() -> None:
    assert Profile.readonly() == Profile(read_only=True)
    edge = Profile.edge()
    assert edge.sqlite.synchronous is Synchronous.NORMAL
    assert edge.sqlite.secure_delete is False
    assert edge.sqlite.journal_mode is JournalMode.WAL
    testing = Profile.testing()
    assert testing.postgres.statement_timeout_ms == 5_000
    assert testing.postgres.lock_timeout_ms == 2_000
    assert testing.pool_size == 2
    assert testing.sqlite.busy_timeout == 2.0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sqlite", "WAL"),
        ("postgres", {}),
        ("application_name", "x" * 64),
        ("application_name", "naïve"),
        ("pool_size", 0),
        ("pool_size", True),
        ("max_overflow", -1),
        ("pool_timeout", 0),
        ("pool_recycle", float("nan")),
        ("connect_timeout", "10"),
        ("pool_pre_ping", 1),
        ("tls", "strict"),
        ("max_rows", 0),
        ("read_only", "yes"),
    ],
)
def test_profile_validation_names_the_field_only(field: str, value: object) -> None:
    with pytest.raises(ConfigurationError) as info:
        Profile(**{field: value})  # type: ignore[arg-type]
    assert info.value.code == "config.invalid_profile"
    assert dict(info.value.details) == {"field": field}
    assert repr(value) not in str(info.value)


def test_profile_tls_from_a_string() -> None:
    assert Profile(tls="verify-full").tls is TLSMode.VERIFY_FULL  # type: ignore[arg-type]


def test_f14_classes_have_no_public_constructor() -> None:
    with pytest.raises(TypeError, match="from_url"):
        Database()
    with pytest.raises(TypeError, match=r"Database.transaction\(\)"):
        Transaction()


# ---------------------------------------------------------------------------------------------------------------------
# TAB-T-B01..B06: creation and lifetime
# ---------------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("scheme", ["sqlite", "sqlite+pysqlite"])
def test_b01_sqlite_file_urls(path: Path, scheme: str) -> None:
    with Database.from_url(f"{scheme}:///{path}") as database:
        assert database.dialect == "sqlite"
        assert database.fetch_scalar("SELECT 1") == 1
        assert database.safe_url == f"sqlite+pysqlite:///{path}"
        assert repr(database) == f"<tabrion.Database sqlite+pysqlite:///{path}>"
    assert path.exists()


@pytest.mark.parametrize(
    ("database", "shown"),
    [
        (r"C:\Users\a b\layer-b.db", r"sqlite+pysqlite:///C:\Users\a b\layer-b.db?mode=ro"),  # a Windows path
        ("/srv/a@b/x%y.db", "sqlite+pysqlite:////srv/a@b/x%y.db?mode=ro"),  # "@" is no user name, "%" no escape
        (":memory:", "sqlite+pysqlite:///:memory:?mode=ro"),
        ("/srv/\u00fc.db", "sqlite+pysqlite:////srv/\u00fc.db?mode=ro"),  # non-ASCII stays readable
        # Review M-1 of #37: what is not printable is percent-encoded, so a file name cannot forge a log line.
        ("/srv/x\nERROR fake log line.db", "sqlite+pysqlite:////srv/x%0AERROR fake log line.db?mode=ro"),
        ("/srv/x\r\t.db", "sqlite+pysqlite:////srv/x%0D%09.db?mode=ro"),
        ("/srv/x\x1b[31mred\x07.db", "sqlite+pysqlite:////srv/x%1B[31mred%07.db?mode=ro"),
        ("/srv/x\x7f\x85.db", "sqlite+pysqlite:////srv/x%7F%C2%85.db?mode=ro"),
        ("/srv/x\u202edb.exe", "sqlite+pysqlite:////srv/x%E2%80%AEdb.exe?mode=ro"),
        ("/srv/x\udcff.db", "sqlite+pysqlite:////srv/x%ED%B3%BF.db?mode=ro"),  # os.fsdecode of a byte 0xff
        # Review M-2 of #37: ":", "@" and "?password=" in a path are no credentials, no host and no query.
        (r"C:\Users\john@corp\x.db", r"sqlite+pysqlite:///C:\Users\john@corp\x.db?mode=ro"),
        ("/srv/a:b@evil.example/x.db", "sqlite+pysqlite:////srv/a:b@evil.example/x.db?mode=ro"),
        ("/srv/x?password=leak.db", "sqlite+pysqlite:////srv/x?password=leak.db?mode=ro"),
    ],
)
def test_b01_safe_url_shows_the_sqlite_path_as_it_is(database: str, shown: str) -> None:
    """SQLAlchemy percent-encodes the path when it renders a URL (``C%3A%5CUsers...``); ``safe_url`` does not
    (Windows nightly of 546d254, TAB-040 readiness gap 2). Checked on every OS with a Windows-style path."""
    url = sqlalchemy.URL.create("sqlite+pysqlite", database=database, query={"mode": "ro"})
    assert tabrion._sa.database._safe_url(url) == shown


@pytest.mark.parametrize(
    ("username", "database", "shown"),
    [
        ("u", r"C:\a:b@c\x.db", r"sqlite+pysqlite://u:***@/C:\a:b@c\x.db"),
        # Review L-1 of #37: a user name that looks like the placeholder of the old code changes nothing.
        ("tabrion-database-slot", "/real.db", "sqlite+pysqlite://tabrion-database-slot:***@//real.db"),
    ],
)
def test_b01_safe_url_masks_first_and_then_shows_the_path(username: str, database: str, shown: str) -> None:
    """The credentials of a foreign engine's URL are masked before the path goes in (review M-2 of #37)."""
    url = sqlalchemy.URL.create("sqlite+pysqlite", username=username, password="pw-canary", database=database)
    assert tabrion._sa.database._safe_url(url) == shown


def test_b01_safe_url_placeholder_is_new_for_every_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review L-1 of #37: the path goes in at the one place of a per-call placeholder, and if that place is not
    unique the URL is rendered the old way rather than wrongly."""
    fixed = uuid.UUID(int=7)
    monkeypatch.setattr(uuid, "uuid4", lambda: fixed)  # _safe_url calls uuid.uuid4()
    url = sqlalchemy.URL.create(
        "sqlite+pysqlite", username=f"tabrion{fixed.hex}", password="pw-canary", database=r"C:\x.db"
    )
    shown = tabrion._sa.database._safe_url(url)
    assert "pw-canary" not in shown
    assert shown == url.render_as_string(hide_password=True)


def test_b01_url_object(path: Path) -> None:
    url = sqlalchemy.URL.create("sqlite", database=str(path))
    with Database.from_url(url) as database:
        assert database.fetch_scalar("SELECT 2") == 2


@pytest.mark.parametrize("url", ["sqlite://", "sqlite:///:memory:"])
def test_b01_memory_is_shared_by_every_thread(url: str) -> None:
    with Database.from_url(url) as database:
        assert isinstance(database.engine.pool, sqlalchemy.pool.StaticPool)
        database.execute("CREATE TABLE m (n INTEGER)")
        errors: list[BaseException] = []

        def worker() -> None:
            try:
                for _ in range(20):
                    with database.transaction() as tx:
                        n = tx.fetch_scalar("SELECT count(*) FROM m")
                        tx.execute("INSERT INTO m VALUES (:n)", {"n": n})
            except BaseException as exc:  # pragma: no cover - reported below
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert errors == []
        assert database.fetch_scalar("SELECT count(DISTINCT n) FROM m") == 80


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("sqlite+aiosqlite:///x.db", "config.driver"),
        ("mysql://u@h/db", "config.driver"),
        ("sqlite:///x.db?mode=ro", "config.url"),
        ("sqlite://host/x.db", "config.url"),
        ("not a url", "config.url"),
        ("postgresql+psycopg2://u:pw@localhost/db", "config.driver"),
        ("postgresql+asyncpg://u:pw@localhost/db", "config.driver"),
    ],
)
def test_b01_b02_unsupported_urls(url: str, code: str) -> None:
    with pytest.raises(ConfigurationError) as info:
        Database.from_url(url)
    assert info.value.code == code


def test_b01_unparseable_url_keeps_no_password() -> None:
    with pytest.raises(ConfigurationError) as info:
        Database.from_url("postgresql://u:hunter2@host:port/db")
    assert "hunter2" not in str(info.value) + repr(info.value)
    assert info.value.__context__ is None


def test_b01_wrong_argument_types(path: Path) -> None:
    with pytest.raises(TypeError):
        Database.from_url(42)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        Database.from_url(f"sqlite:///{path}", profile=SQLiteProfile())  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        Database.from_url(f"sqlite:///{path}", engine_options=[("echo", True)])  # type: ignore[arg-type]


@pytest.mark.parametrize("option", ["creator", "isolation_level", "pool", "poolclass"])
def test_b01_reserved_engine_options(path: Path, option: str) -> None:
    with pytest.raises(ConfigurationError) as info:
        Database.from_url(f"sqlite:///{path}", engine_options={option: None})
    assert info.value.code == "config.engine_option"
    assert dict(info.value.details) == {"option": option}


def test_b01_sqlite_takes_no_connect_args(path: Path) -> None:
    with pytest.raises(ConfigurationError) as info:
        Database.from_url(f"sqlite:///{path}", engine_options={"connect_args": {"timeout": 1}})
    assert dict(info.value.details) == {"option": "connect_args"}


def test_b01_engine_options_override_the_profile(path: Path) -> None:
    with Database.from_url(f"sqlite:///{path}", engine_options={"pool_size": 2}) as database:
        pool = database.engine.pool
        assert isinstance(pool, sqlalchemy.pool.QueuePool)
        assert pool.size() == 2


def test_b02_postgres_url_is_rewritten_and_masked() -> None:
    with Database.from_url("postgresql://app:hunter2@localhost:5432/app") as database:
        assert database.dialect == "postgresql"
        assert database.engine.url.drivername == "postgresql+psycopg"
        assert database.safe_url == "postgresql+psycopg://app:***@localhost:5432/app"
        assert "hunter2" not in repr(database)


class _Stop(Exception):
    pass


def connect_params(database: Database) -> dict[str, Any]:
    """The keyword arguments psycopg would get, captured in ``do_connect`` without connecting."""
    seen: dict[str, Any] = {}

    def capture(dialect: Any, record: Any, cargs: Any, cparams: dict[str, Any]) -> None:
        seen.update(cparams)
        raise _Stop

    sqlalchemy.event.listen(database.engine, "do_connect", capture)
    try:
        with pytest.raises(_Stop):
            database.engine.connect()
    finally:
        sqlalchemy.event.remove(database.engine, "do_connect", capture)
    return seen


def test_b02_postgres_connect_timeout_from_the_profile() -> None:
    profile = Profile(connect_timeout=2.5)
    with Database.from_url("postgresql://app@localhost/app", profile=profile) as database:
        assert connect_params(database)["connect_timeout"] == 3
    with Database.from_url("postgresql://app@localhost/app?connect_timeout=7") as database:
        assert connect_params(database)["connect_timeout"] == "7"
    options = {"connect_args": {"connect_timeout": 1}}
    with Database.from_url("postgresql://app@localhost/app", engine_options=options) as database:
        assert connect_params(database)["connect_timeout"] == 1


def test_b02_postgres_without_psycopg_names_the_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "psycopg", None)
    with pytest.raises(ImportError, match=r"tabrion\[postgres\]"):
        Database.from_url("postgresql://app@localhost/app")


def test_b02_invalid_session_settings_fail_in_from_url() -> None:
    profile = Profile(postgres=SessionSettings(search_path="bad name"))
    with pytest.raises(ConfigurationError) as info:
        Database.from_url("postgresql://app@localhost/app", profile=profile)
    assert info.value.code == "postgres.invalid_setting"


# TAB-T-B03: TLS rules (specification 5.3, contract I-41).

REMOTE = "postgresql://app:pw@db.example.com/app"


def sslmode(database: Database) -> str | None:
    value = database.engine.url.query.get("sslmode")
    assert value is None or isinstance(value, str)
    return value


@pytest.mark.parametrize(
    ("url", "tls", "expected"),
    [
        ("postgresql://app@localhost/app", TLSMode.AUTO, None),
        ("postgresql://app@127.0.0.1/app", TLSMode.AUTO, None),
        ("postgresql://app@[::1]/app", TLSMode.AUTO, None),
        ("postgresql://app@/app?host=/var/run/postgresql", TLSMode.AUTO, None),
        ("postgresql://app@localhost/app?sslmode=disable", TLSMode.AUTO, "disable"),
        (REMOTE, TLSMode.AUTO, "require"),
        (REMOTE + "?sslmode=verify-ca", TLSMode.AUTO, "verify-ca"),
        (REMOTE + "?sslmode=verify-full", TLSMode.AUTO, "verify-full"),
        ("postgresql://app@localhost/app?host=localhost:5432&host=db.example.com:5432", TLSMode.AUTO, "require"),
        ("postgresql://app@localhost/app?hostaddr=10.0.0.5", TLSMode.AUTO, "require"),
        ("postgresql://app@localhost/app", TLSMode.REQUIRE, "require"),
        (REMOTE + "?sslmode=verify-full", TLSMode.REQUIRE, "verify-full"),
        ("postgresql://app@localhost/app", TLSMode.VERIFY_FULL, "verify-full"),
        (REMOTE, TLSMode.VERIFY_FULL, "verify-full"),
        (REMOTE + "?sslmode=disable", TLSMode.INSECURE, "disable"),
        (REMOTE, TLSMode.INSECURE, None),
    ],
)
def test_b03_tls_rules(url: str, tls: TLSMode, expected: str | None) -> None:
    with Database.from_url(url, profile=Profile(tls=tls)) as database:
        assert sslmode(database) == expected


@pytest.mark.parametrize(
    ("url", "tls"),
    [
        (REMOTE + "?sslmode=disable", TLSMode.AUTO),
        (REMOTE + "?sslmode=allow", TLSMode.AUTO),
        (REMOTE + "?sslmode=prefer", TLSMode.AUTO),
        ("postgresql://app@localhost/app?sslmode=prefer", TLSMode.REQUIRE),
        (REMOTE + "?sslmode=require", TLSMode.VERIFY_FULL),
        (REMOTE + "?sslmode=verify-ca", TLSMode.VERIFY_FULL),
        (REMOTE + "?sslmode=require&sslmode=disable", TLSMode.AUTO),
    ],
)
def test_b03_tls_violations(url: str, tls: TLSMode) -> None:
    with pytest.raises(ConfigurationError) as info:
        Database.from_url(url, profile=Profile(tls=tls))
    assert info.value.code == "config.tls"
    assert "pw" not in str(info.value)


def test_b03_tls_sees_connect_args() -> None:
    with pytest.raises(ConfigurationError, match=r"config\.tls"):
        Database.from_url(REMOTE, engine_options={"connect_args": {"sslmode": "disable"}})
    options = {"connect_args": {"host": "db.example.com"}}
    with Database.from_url("postgresql://app@/app", engine_options=options) as database:
        assert sslmode(database) == "require"


def test_b03_tls_without_a_host_follows_pghost(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PGHOST", "db.example.com")
    with Database.from_url("postgresql://app@/app") as database:
        assert sslmode(database) == "require"
    monkeypatch.setenv("PGHOST", "/run/postgresql")
    with Database.from_url("postgresql://app@/app") as database:
        assert sslmode(database) is None


def test_b04_from_url_opens_the_file(tmp_path: Path) -> None:
    """The portable half of the path checks in ``from_url`` (review L-3 of #37): the file is opened, created and
    checked once in ``from_url``, before the engine's first connection, on every OS."""
    fresh = tmp_path / "fresh.db"
    database = Database.from_url(f"sqlite:///{fresh}")
    try:
        assert fresh.exists()
    finally:
        database.close()
    missing_parent = tmp_path / "no-such-directory" / "x.db"
    with pytest.raises(FileNotFoundError):  # raised by from_url itself, not by the first statement
        Database.from_url(f"sqlite:///{missing_parent}")


@pytest.mark.security
@pytest.mark.skipif(not POSIX, reason="symbolic links and modes")
def test_b04_sqlite_path_checks_run_in_from_url(tmp_path: Path) -> None:
    """The POSIX-only half: a symbolic link is refused and a new file is ``0600`` whatever the umask."""
    target = tmp_path / "target.db"
    link = tmp_path / "link.db"
    link.symlink_to(target)
    with pytest.raises(UnsafePath) as info:
        Database.from_url(f"sqlite:///{link}")
    assert info.value.code == "sqlite.symlink"
    assert not target.exists()
    fresh = tmp_path / "fresh.db"
    old = os.umask(0)
    try:
        Database.from_url(f"sqlite:///{fresh}").close()
    finally:
        os.umask(old)
    assert fresh.stat().st_mode & 0o777 == 0o600


def test_b04_read_only_profile_needs_the_file(tmp_path: Path) -> None:
    missing = tmp_path / "missing.db"
    with pytest.raises(FileNotFoundError):
        Database.from_url(f"sqlite:///{missing}", profile=Profile.readonly())
    assert not missing.exists()


def test_b04_a_file_that_is_no_database(tmp_path: Path) -> None:
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"this is not a database file" * 100)
    junk.chmod(0o600)
    with pytest.raises(StorageCorrupt) as info:
        Database.from_url(f"sqlite:///{junk}")
    assert info.value.kind is Kind.CORRUPT
    assert info.value.__context__ is None


def test_b05_from_env(path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    with Database.from_env() as database:
        assert database.fetch_scalar("SELECT 3") == 3
    monkeypatch.delenv("DATABASE_URL")
    secret_file = tmp_path / "url.txt"
    secret_file.write_text(f"sqlite:///{path}\n")
    monkeypatch.setenv("APP_DB_FILE", str(secret_file))
    with Database.from_env("APP_DB", profile=Profile.readonly()) as database:
        assert database.fetch_scalar("SELECT 4") == 4
    with pytest.raises(ConfigurationError) as info:
        Database.from_env("TABRION_NOT_SET_ANYWHERE")
    assert info.value.code == "config.missing"


def test_b06_from_engine_applies_the_profile_and_keeps_the_engine(path: Path) -> None:
    engine = sqlalchemy.create_engine(f"sqlite:///{path}")
    try:
        with engine.connect() as plain:
            assert plain.exec_driver_sql("PRAGMA foreign_keys").scalar() == 0
        database = Database.from_engine(engine)
        assert database.engine is engine
        with database.read() as tx:
            assert tx.fetch_scalar("PRAGMA foreign_keys") == 1
            assert tx.fetch_scalar("PRAGMA trusted_schema") == 0
            assert tx.connection.connection.dbapi_connection.isolation_level is None  # type: ignore[union-attr]
        database.execute("CREATE TABLE f (n INTEGER)")
        database.close()
        assert database.closed
        with engine.begin() as plain:
            plain.exec_driver_sql("INSERT INTO f VALUES (1)")
        listeners = sqlalchemy.event.contains(engine, "begin", tabrion._sa.events.on_begin)
        assert listeners is False
    finally:
        engine.dispose()


def test_b06_from_engine_without_listeners(path: Path) -> None:
    engine = sqlalchemy.create_engine(f"sqlite:///{path}")
    try:
        with Database.from_engine(engine, install_listeners=False) as database:
            with database.read() as tx:
                assert tx.fetch_scalar("PRAGMA foreign_keys") == 0
            database.execute("CREATE TABLE g (n INTEGER)")  # BEGIN IMMEDIATE still comes from the begin listener
            assert database.fetch_scalar("SELECT count(*) FROM g") == 0
    finally:
        engine.dispose()


def test_b06_from_engine_rejects_other_drivers() -> None:
    with pytest.raises(TypeError):
        Database.from_engine("sqlite://")  # type: ignore[arg-type]
    engine = sqlalchemy.create_engine("sqlite://")
    try:
        engine.dialect.driver = "pysqlcipher"
        with pytest.raises(ConfigurationError, match=r"config\.driver"):
            Database.from_engine(engine)
    finally:
        engine.dispose()


def test_b06_close_is_idempotent_and_final(db: Database) -> None:
    assert not db.closed
    db.close()
    db.close()
    assert db.closed
    assert repr(db).endswith(" closed>")
    calls: list[Any] = [
        lambda: db.execute("SELECT 1"),
        lambda: db.fetch_one("SELECT 1"),
        lambda: db.fetch_all("SELECT 1"),
        lambda: db.fetch_scalar("SELECT 1"),
        lambda: db.health(),
        lambda: db.check(),
        lambda: db.engine,
        lambda: db.__enter__(),
        lambda: db.transaction().__enter__(),
        lambda: db.read().__enter__(),
    ]
    for call in calls:
        with pytest.raises(UsageError) as info:
            call()
        assert info.value.code == "database.closed"


def test_b06_context_manager_closes(path: Path) -> None:
    with Database.from_url(f"sqlite:///{path}") as database:
        assert not database.closed
    assert database.closed


def test_b06_no_second_connection(path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Layer A runs on the DBAPI connection SQLAlchemy uses; there is no second connection (specification 3.1)."""
    opened: list[sqlite3.Connection] = []
    original = tabrion.sqlite._open

    def counting(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        connection = original(*args, **kwargs)
        opened.append(connection)
        return connection

    monkeypatch.setattr(tabrion.sqlite, "_open", counting)
    with Database.from_url(f"sqlite:///{path}") as database:
        assert len(opened) == 1  # the check-and-create connection of from_url, closed again
        database.execute("CREATE TABLE c (n INTEGER)")
        for n in range(5):
            database.execute("INSERT INTO c VALUES (:n)", {"n": n})
            with database.read() as tx:
                dbapi = tx.connection.connection.dbapi_connection
                assert tx.fetch_scalar("PRAGMA query_only") == 1
                assert tx.fetch_scalar("PRAGMA foreign_keys") == 1
        assert len(opened) == 2
        assert id(dbapi) == id(opened[1])


# ---------------------------------------------------------------------------------------------------------------------
# TAB-T-B07..B22: transactions
# ---------------------------------------------------------------------------------------------------------------------


@pytest.mark.slow
def test_b07_no_lost_update_with_five_threads(db: Database) -> None:
    db.execute("INSERT INTO items (id, n) VALUES (1, 0)")
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            with db.transaction() as tx:
                n = tx.fetch_scalar("SELECT n FROM items WHERE id = 1")
                tx.execute("UPDATE items SET n = :n WHERE id = 1", {"n": n + 1})
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert db.fetch_scalar("SELECT n FROM items WHERE id = 1") == 5


def test_b07_transaction_begins_immediate(db: Database) -> None:
    statements: list[str] = []

    def record(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        statements.append(statement)

    sqlalchemy.event.listen(db.engine, "before_cursor_execute", record)
    try:
        with db.transaction() as tx:
            tx.execute("INSERT INTO items (n) VALUES (1)")
        with db.read() as tx:
            tx.fetch_all("SELECT * FROM items")
    finally:
        sqlalchemy.event.remove(db.engine, "before_cursor_execute", record)
    assert statements == [
        "BEGIN IMMEDIATE",
        "INSERT INTO items (n) VALUES (1)",
        "PRAGMA query_only = ON",
        "BEGIN",
        "SELECT * FROM items",
    ]


def test_b07_plain_connections_stay_transactional(db: Database) -> None:
    """``db.engine.connect()`` gets the profile and a deferred ``BEGIN``, not Tabrion's modes (specification 3.1)."""
    with db.engine.connect() as plain:
        plain.exec_driver_sql("INSERT INTO items (n) VALUES (1)")
        plain.exec_driver_sql("INSERT INTO items (n) VALUES (2)")
        assert plain.connection.dbapi_connection.in_transaction  # type: ignore[union-attr]
        plain.rollback()
        assert plain.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
    assert db.fetch_scalar("SELECT count(*) FROM items") == 0
    with db.engine.begin() as plain:
        plain.exec_driver_sql("INSERT INTO items (n) VALUES (3)")
    assert db.fetch_scalar("SELECT count(*) FROM items") == 1


def test_b08_rollback_on_an_exception(db: Database) -> None:
    class Boom(Exception):
        pass

    with pytest.raises(Boom), db.transaction() as tx:
        tx.execute("INSERT INTO items (n) VALUES (1)")
        raise Boom
    assert db.fetch_scalar("SELECT count(*) FROM items") == 0
    with db.transaction() as tx:
        tx.execute("INSERT INTO items (n) VALUES (2)")
    assert db.fetch_scalar("SELECT count(*) FROM items") == 1


def test_b09_driver_errors_become_database_errors(db: Database) -> None:
    db.execute("INSERT INTO items (id, n) VALUES (1, 1)")
    with pytest.raises(IntegrityViolation) as info:
        db.execute("INSERT INTO items (id, n) VALUES (1, 2)")
    error = info.value
    assert error.code == "database.unique"
    assert error.kind is Kind.UNIQUE
    assert isinstance(error.original, sqlite3.IntegrityError)
    assert error.statement == "INSERT INTO items (id, n) VALUES (1, 2)"
    assert error.__cause__ is None and error.__context__ is None and error.__suppress_context__
    with pytest.raises(StatementError) as syntax:
        db.fetch_all("SELEC nonsense")
    assert syntax.value.kind is Kind.SYNTAX
    with pytest.raises(IntegrityViolation) as not_null:
        db.execute("INSERT INTO items (n) VALUES (NULL)")
    assert not_null.value.kind is Kind.NOT_NULL


def test_b09_long_statements_are_cut(db: Database) -> None:
    statement = "SELECT " + " + ".join(["1"] * 200) + " FROM nowhere"  # noqa: S608 - fixed text
    with pytest.raises(StatementError) as info:
        db.fetch_all(statement)
    assert info.value.statement == statement[:200]


def test_b09_errors_from_the_raw_connection_are_translated(db: Database) -> None:
    with pytest.raises(StatementError) as info, db.transaction() as tx:
        tx.connection.exec_driver_sql("SELECT * FROM missing_table")
    assert info.value.kind is Kind.UNDEFINED_OBJECT
    # Raised from the context manager's __exit__, so Python sets the caller's error as context; it is suppressed
    # (raise ... from None) and never shown (contract I-39).
    assert info.value.__cause__ is None and info.value.__suppress_context__
    assert checked_out(db) == 0


def test_b09_commit_error_is_translated(db: Database) -> None:
    db.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
    db.execute(
        "CREATE TABLE child (id INTEGER PRIMARY KEY,"
        " parent INTEGER REFERENCES parent (id) DEFERRABLE INITIALLY DEFERRED)"
    )
    with pytest.raises(IntegrityViolation) as info, db.transaction() as tx:
        tx.execute("INSERT INTO child VALUES (1, 99)")  # the deferred check fails at COMMIT
    assert info.value.kind is Kind.FOREIGN_KEY
    assert info.value.__context__ is None
    assert db.fetch_scalar("SELECT count(*) FROM child") == 0
    assert checked_out(db) == 0


def test_b09_statement_and_parameter_checks(db: Database) -> None:
    with pytest.raises(TypeError):
        db.execute(42)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        db.execute("INSERT INTO items (n) VALUES (:n)", "n")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        db.execute("INSERT INTO items (n) VALUES (:n)", [1, 2])  # type: ignore[list-item]
    with pytest.raises(TypeError):
        db.fetch_all("SELECT :n", [{"n": 1}])  # type: ignore[arg-type]
    with pytest.raises(UsageError) as missing:
        db.execute("INSERT INTO items (n) VALUES (:n)", {"m": SECRET})
    assert missing.value.code == "database.invalid_statement"
    assert SECRET not in str(missing.value) + repr(missing.value)
    with pytest.raises(UsageError) as no_rows, db.transaction() as tx:
        tx.fetch_one("UPDATE items SET n = 1")
    assert no_rows.value.code == "database.no_rows_returned"


@pytest.mark.parametrize(("timeout", "error"), [(0, ValueError), (-1, ValueError), ("1", TypeError), (True, TypeError)])
def test_b09_timeout_and_isolation_checks(db: Database, timeout: object, error: type[Exception]) -> None:
    with pytest.raises(error):
        db.execute("SELECT 1", timeout=timeout)  # type: ignore[arg-type]
    with pytest.raises(error), db.transaction(timeout=timeout):  # type: ignore[arg-type]
        pass  # pragma: no cover
    with pytest.raises(ValueError), db.transaction(isolation="snapshot"):  # type: ignore[arg-type]
        pass  # pragma: no cover


def test_b09_sqlite_accepts_every_isolation_level(db: Database) -> None:
    for level in ("read_committed", "repeatable_read", "serializable"):
        with db.transaction(isolation=level) as tx:
            tx.execute("INSERT INTO items (n) VALUES (1)")
    assert db.fetch_scalar("SELECT count(*) FROM items") == 3


def test_b10_nesting_is_refused(db: Database, path: Path) -> None:
    with db.transaction():
        for nested in (db.transaction, db.read):
            with pytest.raises(UsageError) as info, nested():
                pass  # pragma: no cover
            assert info.value.code == "database.nested_transaction"
        with pytest.raises(UsageError):
            db.execute("SELECT 1")
        with Database.from_url(f"sqlite:///{path}.other") as other:
            assert other.fetch_scalar("SELECT 1") == 1  # another Database is no nesting
    with db.read(), pytest.raises(UsageError), db.read():
        pass  # pragma: no cover
    # The marker is per thread and is cleared after an error.
    with pytest.raises(ZeroDivisionError), db.transaction():
        1 / 0  # noqa: B018
    with db.transaction() as tx:
        seen: list[int] = []
        thread = threading.Thread(target=lambda: seen.append(db.fetch_scalar("SELECT 5")))
        thread.start()
        thread.join()
        assert seen == [5]
        tx.execute("INSERT INTO items (n) VALUES (1)")


def test_b10_transaction_is_bound_to_its_thread_and_block(db: Database) -> None:
    with db.transaction() as tx:
        errors: list[BaseException] = []

        def other() -> None:
            try:
                tx.fetch_scalar("SELECT 1")
            except BaseException as exc:
                errors.append(exc)

        thread = threading.Thread(target=other)
        thread.start()
        thread.join()
        assert [getattr(e, "code", None) for e in errors] == ["database.wrong_thread"]
        assert not tx.read_only
        assert "active" in repr(tx)
    with pytest.raises(UsageError) as info:
        tx.execute("SELECT 1")
    assert info.value.code == "database.transaction_ended"
    with pytest.raises(UsageError):
        tx.connection  # noqa: B018


def test_b11_read_is_enforced_by_sqlite(db: Database) -> None:
    with db.read() as tx:
        assert tx.read_only
        with pytest.raises(ReadOnlyViolation) as info:
            tx.execute("INSERT INTO items (n) VALUES (1)")
        assert info.value.kind is Kind.READ_ONLY
    assert db.fetch_scalar("SELECT count(*) FROM items") == 0


def test_b11_read_only_profile(path: Path) -> None:
    Database.from_url(f"sqlite:///{path}").close()
    with Database.from_url(f"sqlite:///{path}", profile=Profile.readonly()) as database:
        with pytest.raises(UsageError) as info, database.transaction():
            pass  # pragma: no cover
        assert info.value.code == "database.read_only"
        with pytest.raises(UsageError):
            database.execute("CREATE TABLE x (n INTEGER)")
        assert database.fetch_scalar("SELECT 1") == 1
        with database.read():
            pass
        # Enforced in the database (mode=ro, query_only), not by a text filter: also on a plain connection.
        with database.engine.connect() as plain:
            assert plain.exec_driver_sql("PRAGMA query_only").scalar() == 1
            with pytest.raises(sqlalchemy.exc.OperationalError):
                plain.exec_driver_sql("CREATE TABLE x (n INTEGER)")


def test_b13_timeout_on_sqlite(db: Database) -> None:
    with pytest.raises(StatementTimeout) as info:
        db.fetch_scalar(COUNT_FOREVER, timeout=0.1)
    error = info.value
    assert error.code == "database.timeout"
    assert error.kind is Kind.TIMEOUT
    assert error.classification is not None and error.classification.sqlite_code == "SQLITE_INTERRUPT"
    assert error.__context__ is None
    with pytest.raises(StatementTimeout), db.transaction(timeout=0.1) as tx:
        tx.execute("INSERT INTO items (n) VALUES (1)")
        tx.fetch_scalar(COUNT_FOREVER)  # the transaction's timeout applies to each statement
    with db.transaction(timeout=0.1) as tx:
        assert tx.fetch_scalar("SELECT 1", timeout=5) == 1
    assert db.fetch_scalar("SELECT count(*) FROM items") == 0
    assert checked_out(db) == 0


def test_b13_no_deadline_stays_behind(path: Path) -> None:
    """After a statement with a timeout, the next one without runs past the old deadline (one pooled connection)."""
    with Database.from_url(f"sqlite:///{path}", profile=Profile(pool_size=1)) as database:
        assert database.fetch_scalar("SELECT 1", timeout=0.05) == 1
        time.sleep(0.1)
        counted = (
            "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c WHERE x < 200000) SELECT count(*) FROM c"
        )
        assert database.fetch_scalar(counted) == 200000


def test_b16_executemany(db: Database) -> None:
    rows = [{"n": n, "label": f"row {n}"} for n in range(10)]
    assert db.execute("INSERT INTO items (n, label) VALUES (:n, :label)", rows) == 10
    assert db.execute("INSERT INTO items (n, label) VALUES (:n, :label)", ()) == 0
    assert db.execute("UPDATE items SET n = n + 1 WHERE n < :limit", {"limit": 5}) == 5
    assert db.execute("CREATE INDEX items_n ON items (n)") == 0
    assert db.fetch_scalar("SELECT count(*) FROM items") == 10


def test_b17_multiple_rows(db: Database) -> None:
    assert db.fetch_one("SELECT * FROM items") is None
    assert db.fetch_scalar("SELECT n FROM items") is None
    db.execute("INSERT INTO items (n) VALUES (:n)", [{"n": 1}, {"n": 2}])
    for call in (db.fetch_one, db.fetch_scalar):
        with pytest.raises(MultipleRowsError) as info:
            call("SELECT n FROM items")
        assert info.value.code == "database.multiple_rows"
    row = db.fetch_one("SELECT n, label FROM items WHERE n = :n", {"n": 2})
    assert row is not None and row.n == 2 and row.label is None
    assert [r.n for r in db.fetch_all("SELECT n FROM items ORDER BY n")] == [1, 2]


@pytest.mark.security
def test_b18_no_parameter_value_in_errors(db: Database) -> None:
    db.execute("CREATE UNIQUE INDEX items_label ON items (label)")
    db.execute("INSERT INTO items (n, label) VALUES (1, :label)", {"label": SECRET})
    failures: list[TabrionError] = []
    for call in (
        lambda: db.execute("INSERT INTO items (n, label) VALUES (1, :label)", {"label": SECRET}),
        lambda: db.execute("INSERT INTO items (n, label) VALUES (:n, :label)", [{"n": None, "label": SECRET}]),
        lambda: db.fetch_all("SELECT * FROM items WHERE label = :label AND nonsense", {"label": SECRET}),
        lambda: db.fetch_scalar(COUNT_FOREVER + " WHERE :v IS NOT NULL", {"v": SECRET}, timeout=0.05),
    ):
        with pytest.raises(TabrionError) as info:
            call()
        failures.append(info.value)
    for error in failures:
        text = str(error) + repr(error) + repr(error.args) + "".join(traceback.format_exception(error))
        assert SECRET not in text
        assert SECRET not in (error.statement or "")
        assert error.__cause__ is None and error.__context__ is None


def test_b19_resource_warning_for_a_forgotten_close(path: Path) -> None:
    database = Database.from_url(f"sqlite:///{path}")
    engine = database.engine
    with pytest.warns(ResourceWarning, match="unclosed <tabrion.Database"):
        del database
        gc.collect()
    pool = engine.pool
    assert isinstance(pool, sqlalchemy.pool.QueuePool)
    assert pool.checkedin() == 0


def test_b20_core_expressions(db: Database) -> None:
    metadata = sqlalchemy.MetaData()
    items = sqlalchemy.Table(
        "items",
        metadata,
        sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True),
        sqlalchemy.Column("n", sqlalchemy.Integer),
        sqlalchemy.Column("label", sqlalchemy.Text),
    )
    assert db.execute(sqlalchemy.insert(items), [{"n": 1, "label": "a"}, {"n": 2, "label": "b"}]) == 2
    rows = db.fetch_all(sqlalchemy.select(items.c.n, items.c.label).order_by(items.c.n))
    assert [tuple(row) for row in rows] == [(1, "a"), (2, "b")]
    assert db.fetch_scalar(sqlalchemy.select(sqlalchemy.func.max(items.c.n))) == 2
    assert db.execute(sqlalchemy.delete(items).where(items.c.n == 1)) == 1


def test_b21_connections_return_after_errors(db: Database) -> None:
    for call in (
        lambda: db.execute("INSERT INTO nowhere VALUES (1)"),
        lambda: db.fetch_one("SELECT * FROM nowhere"),
        lambda: db.fetch_one("SELECT 1 UNION ALL SELECT 2"),
    ):
        with pytest.raises(TabrionError):
            call()
        assert checked_out(db) == 0
    with pytest.raises(KeyError), db.read():
        raise KeyError("x")
    assert checked_out(db) == 0
    health = db.health()
    assert health.pool == {"checked_out": 0, "size": 5, "overflow": 0}


def test_b21_pool_exhaustion_is_connection_failed(path: Path) -> None:
    profile = Profile(pool_size=1, pool_timeout=0.2)
    with Database.from_url(f"sqlite:///{path}", profile=profile) as database:
        holder = database.engine.connect()
        try:
            with pytest.raises(ConnectionFailed) as info:
                database.fetch_scalar("SELECT 1")
        finally:
            holder.close()
        classification = info.value.classification
        assert classification is not None
        assert (classification.kind, classification.retryable, classification.outcome_unknown) == (
            Kind.RESOURCE,
            True,
            False,
        )
        assert info.value.__context__ is None
        assert database.fetch_scalar("SELECT 1") == 1


def test_b22_query_only_does_not_stick(path: Path) -> None:
    with Database.from_url(f"sqlite:///{path}", profile=Profile(pool_size=1)) as database:
        database.execute("CREATE TABLE q (n INTEGER)")
        with pytest.raises(ReadOnlyViolation), database.read() as tx:
            first = tx.connection.connection.dbapi_connection
            tx.execute("INSERT INTO q VALUES (1)")
        with database.transaction() as tx:
            assert tx.connection.connection.dbapi_connection is first
            assert tx.fetch_scalar("PRAGMA query_only") == 0
            tx.execute("INSERT INTO q VALUES (2)")
        with database.engine.begin() as plain:
            plain.exec_driver_sql("INSERT INTO q VALUES (3)")
        assert database.fetch_scalar("SELECT count(*) FROM q") == 2


def test_b22_failed_reset_invalidates_the_connection() -> None:
    """A connection whose ``query_only = OFF`` fails must not go back to the pool with ``query_only`` on."""

    class Driver:
        def execute(self, statement: str) -> None:
            raise sqlite3.OperationalError("disk I/O error")

    class Connection:
        closed = False
        invalidated = False

        def __init__(self) -> None:
            self.connection = types.SimpleNamespace(dbapi_connection=Driver())

        def invalidate(self) -> None:
            self.invalidated = True

    connection = Connection()
    tabrion._sa.database._query_only_off(connection)  # type: ignore[arg-type]
    assert connection.invalidated


# ---------------------------------------------------------------------------------------------------------------------
# TAB-T-B23..B25: health and check
# ---------------------------------------------------------------------------------------------------------------------


def test_b23_health(db: Database) -> None:
    health = db.health()
    assert health.ok and health.dialect == "sqlite" and health.classification is None
    assert health.server_version == sqlite3.sqlite_version
    assert health.latency_ms is not None and health.latency_ms >= 0
    assert db.health(deep=True).ok
    with pytest.raises(TypeError):
        db.health(deep=1)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        db.health(timeout=0)


def test_b23_deep_health_finds_corruption(tmp_path: Path) -> None:
    path = tmp_path / "corrupt.db"
    raw = sqlite3.connect(path)
    raw.execute("CREATE TABLE t (a TEXT)")
    raw.execute("CREATE INDEX t_a ON t (a)")
    raw.executemany("INSERT INTO t VALUES (?)", [("x" * 100 + str(i),) for i in range(2000)])
    raw.commit()
    raw.close()
    with path.open("r+b") as handle:
        handle.seek(4096 * 20 + 100)
        handle.write(b"\xff" * 2000)
    path.chmod(0o600)
    profile = Profile(sqlite=SQLiteProfile(journal_mode=JournalMode.DELETE))
    with Database.from_url(f"sqlite:///{path}", profile=profile) as database:
        assert database.health().ok
        deep = database.health(deep=True)
        assert not deep.ok
        assert deep.classification is not None and deep.classification.kind is Kind.CORRUPT


def test_b24_health_never_raises_for_database_errors(tmp_path: Path) -> None:
    path = tmp_path / "gone.db"
    Database.from_url(f"sqlite:///{path}").close()
    with Database.from_url(f"sqlite:///{path}", profile=Profile.readonly()) as database:
        path.unlink()
        health = database.health()
        assert not health.ok
        assert health.latency_ms is None
    with Database.from_url("sqlite://") as memory:
        assert memory.health().ok
        assert memory.health().pool is None
        busy = threading.Event()
        release = threading.Event()

        def hold() -> None:
            with memory.transaction():
                busy.set()
                release.wait(5)

        thread = threading.Thread(target=hold)
        thread.start()
        busy.wait(5)
        try:
            waiting = memory.health(timeout=0.1)
        finally:
            release.set()
            thread.join()
        assert not waiting.ok
        assert waiting.classification is not None and waiting.classification.kind is Kind.TIMEOUT


def test_b24_health_timeout(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    original = sqlalchemy.engine.Connection.exec_driver_sql

    def slow(self: Any, statement: str, *args: Any, **kwargs: Any) -> Any:
        if statement == "SELECT 1":
            statement = COUNT_FOREVER
        return original(self, statement, *args, **kwargs)

    monkeypatch.setattr(sqlalchemy.engine.Connection, "exec_driver_sql", slow)
    health = db.health(timeout=0.1)
    assert not health.ok
    assert health.classification is not None and health.classification.kind is Kind.TIMEOUT


def test_b25_check_delegates_to_tabrion_check(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[object] = []

    def fake_check(target: object, **kwargs: object) -> list[object]:
        seen.append(target)
        return []

    monkeypatch.setattr(tabrion.check, "check", fake_check)
    assert db.check() == []
    assert seen == [db]


# ---------------------------------------------------------------------------------------------------------------------
# TB-040 (review N-4): R-NORETRY over a stored classification
# ---------------------------------------------------------------------------------------------------------------------


def test_tb040_during_commit_overrides_a_stored_classification() -> None:
    checkout = Classification(
        kind=Kind.CONNECTION,
        retryable=True,
        outcome_unknown=False,
        connection_usable=False,
        driver="psycopg",
        sqlstate="08006",
        sqlite_code=None,
        constraint=None,
        table=None,
        column=None,
    )
    error = ConnectionFailed("database.connection", classification=checkout)
    assert classify(error) == checkout
    assert classify(error, sent=False) == checkout
    committed = classify(error, during_commit=True)
    assert (committed.outcome_unknown, committed.retryable) == (True, False)
    assert committed.sqlstate == "08006"
    unique = dataclasses.replace(checkout, kind=Kind.UNIQUE, retryable=False)
    assert classify(IntegrityViolation("database.unique", classification=unique), during_commit=True) is unique
    unknown = dataclasses.replace(checkout, outcome_unknown=True, retryable=False)
    assert classify(ConnectionFailed("database.connection", classification=unknown), during_commit=True) is unknown


# Review of #12 (fix round): TLS with a libpq service or PGHOSTADDR (M-3), from_engine and read-only (M-2),
# read-only lifted by the caller's SQL (H-1, I-56), a failed rollback (L-3, M22).


@pytest.mark.security
@pytest.mark.parametrize("url", ["postgresql:///app?service=prod", "postgresql://app@localhost/app?service=prod"])
def test_b03_tls_a_service_is_not_local(url: str) -> None:
    with Database.from_url(url) as database:
        assert sslmode(database) == "require"
    with Database.from_url("postgresql:///app", engine_options={"connect_args": {"service": "prod"}}) as database:
        assert sslmode(database) == "require"


@pytest.mark.security
def test_b03_tls_pgservice_and_pghostaddr_are_not_local(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("PGHOST", "PGHOSTADDR", "PGSERVICE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PGSERVICE", "prod")
    with Database.from_url("postgresql:///app") as database:
        assert sslmode(database) == "require"
    monkeypatch.delenv("PGSERVICE")
    monkeypatch.setenv("PGHOSTADDR", "10.1.2.3")
    with Database.from_url("postgresql://app@localhost/app") as database:
        assert sslmode(database) == "require"
    with Database.from_url("postgresql://app@localhost/app?hostaddr=127.0.0.1") as database:
        assert sslmode(database) is None  # the URL's hostaddr takes precedence over PGHOSTADDR
    monkeypatch.setenv("PGHOSTADDR", "127.0.0.1")
    with Database.from_url("postgresql://app@localhost/app") as database:
        assert sslmode(database) is None


@pytest.mark.security
def test_b06_from_engine_refuses_a_read_only_profile_with_listeners(path: Path) -> None:
    engine = sqlalchemy.create_engine(f"sqlite:///{path}")
    try:
        with pytest.raises(ConfigurationError) as info:
            Database.from_engine(engine, profile=Profile.readonly())
        assert info.value.code == "config.invalid_profile" and info.value.details == {"field": "read_only"}
        assert sqlalchemy.event.contains(engine, "begin", tabrion._sa.events.on_begin) is False
        database = Database.from_engine(engine, profile=Profile.readonly(), install_listeners=False)
        with pytest.raises(UsageError, match=r"database\.read_only"), database.transaction():
            pass  # pragma: no cover
        database.close()
        with engine.begin() as plain:  # the host's own connections stay writable
            plain.exec_driver_sql("CREATE TABLE host (n INTEGER)")
    finally:
        engine.dispose()


@pytest.mark.security
def test_b11_query_only_lifted_in_read_is_not_committed(db: Database) -> None:
    with pytest.raises(UsageError) as info, db.read() as tx:
        tx.execute("PRAGMA query_only = OFF")
        tx.execute("INSERT INTO items (n) VALUES (1)")
    assert info.value.code == "database.read_only"
    assert db.fetch_scalar("SELECT count(*) FROM items") == 0
    assert checked_out(db) == 0
    with db.read() as tx:  # the next read() is read-only again
        assert tx.fetch_scalar("PRAGMA query_only") == 1


def test_b09_failed_rollback_invalidates_the_connection(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    invalidated: list[object] = []
    sqlalchemy.event.listen(db.engine, "invalidate", lambda *args: invalidated.append(args))

    def broken(self: object) -> None:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(sqlalchemy.Connection, "rollback", broken)
    with pytest.raises(RuntimeError), db.transaction():
        raise RuntimeError("caller")
    monkeypatch.undo()
    assert len(invalidated) == 1
    assert checked_out(db) == 0
    assert db.fetch_scalar("SELECT 1") == 1  # the pool opens a new connection


# Re-review of #12 (second fix round): ATTACH under a read-only profile (N-2), lift-write-restore (N-1, TB-061),
# the read-back's own row factory and its fail-closed branch (N-4).


def read_only_db(path: Path) -> Database:
    with Database.from_url(f"sqlite:///{path}") as writable:
        writable.execute("CREATE TABLE t (n INTEGER)")
    return Database.from_url(f"sqlite:///{path}", profile=Profile.readonly())


@pytest.mark.security
def test_b11_attach_of_the_own_file_is_refused_under_read_only(tmp_path: Path) -> None:
    """Re-review N-2: mode=ro covers the main file only; ATTACH would open the same file read-write."""
    path = tmp_path / "ro.db"
    with read_only_db(path) as database:
        with pytest.raises(TabrionError) as info, database.read() as tx:
            tx.execute("PRAGMA query_only = OFF")
            tx.execute("ATTACH DATABASE :p AS w", {"p": str(path)})
            # Unreachable while the limit holds; without it the restore below would satisfy the read-back, so this
            # test fails when the limit is removed (re-review 2 R-1 of #12).
            tx.execute("INSERT INTO w.t VALUES (1)")  # pragma: no cover
            tx.execute("PRAGMA query_only = ON")  # pragma: no cover
        assert info.value.code != "database.read_only"  # the ATTACH failed, not the read-back
        assert database.fetch_scalar("SELECT count(*) FROM t") == 0
    with Database.from_url(f"sqlite:///{path}") as writable:
        assert writable.fetch_scalar("SELECT count(*) FROM t") == 0


@pytest.mark.security
def test_b11_attach_limit_is_zero_under_read_only(tmp_path: Path) -> None:
    with read_only_db(tmp_path / "ro.db") as database, database.read() as tx:
        dbapi: Any = tx.connection.connection.dbapi_connection
        assert dbapi.getlimit(sqlite3.SQLITE_LIMIT_ATTACHED) == 0
    with Database.from_url(f"sqlite:///{tmp_path / 'rw.db'}") as writable, writable.read() as tx:
        dbapi = tx.connection.connection.dbapi_connection
        assert dbapi.getlimit(sqlite3.SQLITE_LIMIT_ATTACHED) > 0


@pytest.mark.security
def test_b11_refused_read_invalidates_the_sqlite_connection(tmp_path: Path) -> None:
    """Re-review 2 R-2: an attached database and a raised limit survive a rollback, so the connection is dropped."""
    path = tmp_path / "ro.db"
    with read_only_db(path) as database:
        with pytest.raises(UsageError, match=r"database\.read_only"), database.read() as tx:
            raw: Any = tx.connection.connection.dbapi_connection
            raw.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 10)  # deliberate code on the raw connection
            tx.execute("PRAGMA query_only = OFF")
            tx.execute("ATTACH DATABASE :p AS w", {"p": str(path)})
        with database.read() as tx:
            fresh: Any = tx.connection.connection.dbapi_connection
            assert fresh is not raw
            assert fresh.getlimit(sqlite3.SQLITE_LIMIT_ATTACHED) == 0
            assert [row[1] for row in fresh.execute("PRAGMA database_list")] == ["main"]


def test_b11_refused_read_keeps_the_in_memory_database(tmp_path: Path) -> None:
    with Database.from_url("sqlite://") as database:
        database.execute("CREATE TABLE t (n INTEGER)")
        database.execute("INSERT INTO t VALUES (1)")
        with pytest.raises(UsageError, match=r"database\.read_only"), database.read() as tx:
            tx.execute("PRAGMA query_only = OFF")
        assert database.fetch_scalar("SELECT count(*) FROM t") == 1  # the shared connection is the database


@pytest.mark.security
def test_b11_query_only_lifted_under_read_only_is_refused(tmp_path: Path) -> None:
    with read_only_db(tmp_path / "ro.db") as database:
        with pytest.raises(UsageError, match=r"database\.read_only"), database.read() as tx:
            tx.execute("PRAGMA query_only = OFF")
        with database.read() as tx:
            assert tx.fetch_scalar("PRAGMA query_only") == 1


@pytest.mark.security
def test_b11_documents_the_gap_lift_write_restore(db: Database) -> None:
    """Documents the gap of contract I-56 and TB-061: the read-back sees the state at the end only."""
    with db.read() as tx:
        tx.execute("PRAGMA query_only = OFF")
        tx.execute("INSERT INTO items (n) VALUES (1)")
        tx.execute("PRAGMA query_only = ON")
    assert db.fetch_scalar("SELECT count(*) FROM items") == 1


@pytest.mark.security
def test_b11_read_back_ignores_the_callers_row_factory(db: Database, path: Path) -> None:
    with pytest.raises(UsageError, match=r"database\.read_only"), db.read() as tx:
        dbapi = tx.connection.connection.dbapi_connection
        assert isinstance(dbapi, sqlite3.Connection)
        dbapi.row_factory = lambda cursor, row: (1,)  # would fake query_only = 1
        tx.execute("PRAGMA query_only = OFF")
        tx.execute("INSERT INTO items (n) VALUES (1)")
    raw = sqlite3.connect(path)  # the caller's row factory stays on the pooled connection
    try:
        assert raw.execute("SELECT count(*) FROM items").fetchone() == (0,)
    finally:
        raw.close()


def test_b11_a_failing_read_back_does_not_commit(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tabrion._sa.database, "_QUERY_ONLY_SQL", "SELECT * FROM no_such_table")
    with pytest.raises(UsageError, match=r"database\.read_only"), db.read() as tx:
        assert tx.fetch_scalar("SELECT count(*) FROM items") == 0
    assert checked_out(db) == 0
