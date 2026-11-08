"""The 0.2 Studio APIs of layer B on PostgreSQL (TAB-033): ``query``/``Result``, ``stream``, ``cancel``,
``inspector``, ``on_begin``, ``savepoint`` and ``run_transaction`` (TAB-T-B15, B26..B40; contract I-100..I-106).

As on SQLite (``tests/unit/test_v02_database.py``), the staged classes of ``tabrion._sa._staged`` stand in for the
0.2 ``tabrion.Database`` (G-T1 condition C-6). Every table lives in the ``schema`` fixture; every engine has an
``application_name`` of its own.
"""

# The SQL is built from the schema fixture's generated name and fixed text, never from outside input.
# ruff: noqa: S608

from __future__ import annotations

from collections.abc import Callable, Iterator
import contextlib
import socket
import struct
import threading
import time
import types
from typing import Any

import pytest

from tests.postgres.conftest import new_name

pytestmark = pytest.mark.postgres

sqlalchemy = pytest.importorskip("sqlalchemy")
from sqlalchemy import Connection  # noqa: E402

psycopg = pytest.importorskip("psycopg")

from tabrion import Profile, Transaction  # noqa: E402
from tabrion._sa._staged import _StagedDatabase, _StagedTransaction  # noqa: E402
import tabrion._sa._v02  # noqa: E402
import tabrion._sa.database  # noqa: E402
from tabrion._sa.result import Column  # noqa: E402
from tabrion.errors import (  # noqa: E402
    Canceled,
    ConnectionFailed,
    Kind,
    ReadOnlyViolation,
    StatementError,
    StatementTimeout,
    TabrionError,
    TransientError,
    UsageError,
)
from tabrion.postgres import SessionSettings  # noqa: E402
from tabrion.sql import split_statements  # noqa: E402
from tabrion.testing._faults import FaultProxy  # noqa: E402
from tests.postgres.test_database import sa_url  # noqa: E402

SECRET = "SECRET-4711"


def staged(transaction: Transaction) -> _StagedTransaction:
    assert isinstance(transaction, _StagedTransaction)
    return transaction


@pytest.fixture
def open_db(admin_url: str) -> Iterator[Callable[..., _StagedDatabase]]:
    """``open_db(profile=None, url=None, **engine_options)``: a staged ``Database`` with its own application name."""
    opened: list[_StagedDatabase] = []

    def factory(profile: Profile | None = None, url: str | None = None, **options: Any) -> _StagedDatabase:
        fields = {name: getattr(profile or Profile(), name) for name in Profile.__dataclass_fields__}
        fields["application_name"] = new_name("t_app_")
        if url is not None:  # the fault proxy reads the plain protocol
            options.setdefault("connect_args", {}).update(sslmode="disable", gssencmode="disable")
        database = _StagedDatabase.from_url(
            sa_url(url or admin_url), profile=Profile(**fields), engine_options=options or None
        )
        assert isinstance(database, _StagedDatabase)
        opened.append(database)
        return database

    yield factory
    for database in opened:
        database.close()


@pytest.fixture
def db(open_db: Callable[..., _StagedDatabase], schema: str) -> _StagedDatabase:
    database = open_db()
    database.execute(f"INSERT INTO {schema}.parts (id, label) SELECT n, 'p' || n FROM generate_series(1, 25) n")
    return database


# ---------------------------------------------------------------------------------------------------------------------
# query and Result (TAB-T-B26..B30)
# ---------------------------------------------------------------------------------------------------------------------


def test_b26_query_columns_and_values(db: _StagedDatabase, schema: str, admin: Any) -> None:
    admin.execute(f"CREATE TYPE {schema}.mood AS ENUM ('ok', 'meh')")
    result = db.query(
        f"SELECT id, label, ARRAY[id, 2] AS pair, 1.50::numeric(5, 2) AS price, 'ok'::{schema}.mood AS mood,"
        " now() AS at, NULL AS nothing, id AS id FROM "
        f"{schema}.parts WHERE id <= :limit ORDER BY id",
        {"limit": 2},
    )
    assert [c.name for c in result.columns] == ["id", "label", "pair", "price", "mood", "at", "nothing", "id"]
    assert [c.type_name for c in result.columns] == [
        "integer",
        "text",
        "integer[]",
        "numeric",
        f"{schema}.mood",
        "timestamp with time zone",
        "text",
        "integer",
    ]
    assert all(c.nullable is None for c in result.columns)
    first = result.rows[0]
    assert first[:5] == (1, "p1", [1, 2], __import__("decimal").Decimal("1.50"), "ok")
    assert (result.row_count, result.truncated, result.statement_kind) == (2, False, "select")
    assert result.elapsed_ms > 0


def test_b26_query_dml_ddl_and_other(db: _StagedDatabase, schema: str) -> None:
    with db.transaction() as transaction:
        tx = staged(transaction)
        inserted = tx.query(f"INSERT INTO {schema}.parts (id, label) VALUES (100, 'x')")
        assert (inserted.columns, inserted.rows, inserted.statement_kind) == ((), (), "dml")
        returning = tx.query(f"UPDATE {schema}.parts SET label = 'y' WHERE id = 100 RETURNING id, label")
        assert returning.rows == ((100, "y"),)
        assert returning.columns == (Column("id", "integer", None), Column("label", "text", None))
        assert tx.query(f"CREATE TABLE {schema}.other (id int)").statement_kind == "ddl"
        shown = tx.query("SHOW search_path")
        assert (shown.statement_kind, shown.columns[0].name, shown.row_count) == ("other", "search_path", 1)
    assert db.query(f"SELECT label FROM {schema}.parts WHERE id = 100").rows == (("y",),)


def test_b27_max_rows(open_db: Callable[..., _StagedDatabase], db: _StagedDatabase, schema: str) -> None:
    result = db.query(f"SELECT id FROM {schema}.parts ORDER BY id", max_rows=10)
    assert (result.row_count, result.truncated, result.rows[-1]) == (10, True, (10,))
    exact = db.query(f"SELECT id FROM {schema}.parts", max_rows=25)
    assert (exact.row_count, exact.truncated) == (25, False)
    small = open_db(Profile(max_rows=3))
    assert small.query("SELECT generate_series(1, 1000)").rows == ((1,), (2,), (3,))


def test_b28_core_expressions(db: _StagedDatabase, schema: str) -> None:
    metadata = sqlalchemy.MetaData(schema=schema)
    parts = sqlalchemy.Table("parts", metadata, autoload_with=db.engine)
    result = db.query(sqlalchemy.select(parts.c.id, parts.c.label).where(parts.c.id > 23).order_by(parts.c.id))
    assert result.rows == ((24, "p24"), (25, "p25"))
    assert result.columns == (Column("id", "integer", None), Column("label", "text", None))


def test_b29_database_query_is_read_only(db: _StagedDatabase, schema: str) -> None:
    with pytest.raises(ReadOnlyViolation):
        db.query(f"INSERT INTO {schema}.parts (id) VALUES (500)")
    with pytest.raises(TabrionError) as info:
        db.query(f"SELECT * FROM {schema}.missing WHERE label = :v", {"v": SECRET})
    assert info.value.code == "database.undefined_object"
    assert SECRET not in str(info.value) + repr(info.value)
    assert info.value.__cause__ is None and info.value.__context__ is None


def test_b30_query_timeout(db: _StagedDatabase) -> None:
    started = time.monotonic()
    with pytest.raises(StatementTimeout):
        db.query("SELECT pg_sleep(5)", timeout=0.2)
    assert time.monotonic() - started < 2
    with db.read() as transaction:
        before = transaction.fetch_scalar("SHOW statement_timeout")
        with pytest.raises(StatementTimeout):
            staged(transaction).query("SELECT pg_sleep(5)", timeout=0.2)
    with db.read() as transaction:
        staged(transaction).query("SELECT 1", timeout=0.5)
        assert transaction.fetch_scalar("SHOW statement_timeout") == before  # the profile value is back


def test_b30_prepare_threshold_is_restored(db: _StagedDatabase) -> None:
    with db.read() as transaction:
        dbapi = transaction.connection.connection.dbapi_connection
        assert dbapi is not None
        before = dbapi.prepare_threshold
        staged(transaction).query("SELECT 1")
        assert dbapi.prepare_threshold == before
        with pytest.raises(StatementError):
            staged(transaction).query("SELECT 1; SELECT 2")
    with db.read() as transaction:
        assert transaction.connection.connection.dbapi_connection.prepare_threshold == before  # type: ignore[union-attr]


# ---------------------------------------------------------------------------------------------------------------------
# run_transaction (TAB-T-B31..B33)
# ---------------------------------------------------------------------------------------------------------------------


def test_b31_serialization_failure_is_retried(db: _StagedDatabase, schema: str, admin: Any) -> None:
    """Attempt 1 reads, another session updates the row, attempt 1 updates it too: 40001, retried; attempt 2 wins."""
    calls: list[int] = []

    def work(transaction: Transaction) -> int:
        calls.append(1)
        value = transaction.fetch_scalar(f"SELECT label FROM {schema}.parts WHERE id = 1")
        if len(calls) == 1:
            admin.execute(f"UPDATE {schema}.parts SET label = 'other' WHERE id = 1")
        transaction.execute(f"UPDATE {schema}.parts SET label = :label WHERE id = 1", {"label": f"{value}+"})
        return len(calls)

    assert db.run_transaction(work, isolation="repeatable_read", backoff=0.01) == 2
    assert db.fetch_scalar(f"SELECT label FROM {schema}.parts WHERE id = 1") == "other+"


def test_b32_errors_that_are_not_retried(db: _StagedDatabase, schema: str) -> None:
    calls: list[int] = []

    def unique(transaction: Transaction) -> None:
        calls.append(1)
        transaction.execute(f"INSERT INTO {schema}.parts (id) VALUES (1)")

    with pytest.raises(TabrionError) as info:
        db.run_transaction(unique, backoff=0)
    assert info.value.code == "database.unique"
    assert calls == [1]


@pytest.mark.slow
def test_b33_commit_cut_by_the_proxy_is_not_retried(
    open_db: Callable[..., _StagedDatabase], admin_url: str, schema: str, admin: Any
) -> None:
    """The COMMIT reaches the server, the reply is lost: outcome unknown, so run_transaction does not run fn again."""
    params = psycopg.conninfo.conninfo_to_dict(admin_url)
    if str(params.get("host") or "").startswith("/"):
        pytest.skip("the fault proxy needs a TCP server address")
    with FaultProxy.in_front_of(admin_url) as proxy:
        db = open_db(url=proxy.dsn(admin_url))
        calls: list[int] = []

        def work(transaction: Transaction) -> None:
            calls.append(1)
            transaction.execute(f"INSERT INTO {schema}.parts (id, label) VALUES (900, 'once')")
            proxy.arm(b"COMMIT", fault="reply")

        with pytest.raises(ConnectionFailed) as info:
            db.run_transaction(work, attempts=5, backoff=0)
        db.close()
    proxy.check()
    assert info.value.classification is not None
    assert info.value.classification.outcome_unknown
    assert calls == [1]
    assert admin.execute(f"SELECT count(*) FROM {schema}.parts WHERE id = 900").fetchone() == (1,)


@pytest.mark.slow
def test_b33_a_connection_lost_mid_transaction_is_not_retried(
    open_db: Callable[..., _StagedDatabase], admin_url: str, schema: str, admin: Any
) -> None:
    """The connection goes while a statement is on its way: the client cannot tell whether the server ran it, so the
    classification is ``outcome_unknown`` and ``fn`` runs once (R-NORETRY)."""
    params = psycopg.conninfo.conninfo_to_dict(admin_url)
    if str(params.get("host") or "").startswith("/"):
        pytest.skip("the fault proxy needs a TCP server address")
    with FaultProxy.in_front_of(admin_url) as proxy:
        db = open_db(url=proxy.dsn(admin_url))
        calls: list[int] = []

        def work(transaction: Transaction) -> None:
            calls.append(1)
            proxy.arm(b"INSERT INTO", fault="request")
            transaction.execute(f"INSERT INTO {schema}.parts (id, label) VALUES (901, 'x')")

        with pytest.raises(ConnectionFailed) as info:
            db.run_transaction(work, attempts=3, backoff=0)
        db.close()
    proxy.check()
    assert info.value.classification is not None
    assert info.value.classification.outcome_unknown
    assert calls == [1]
    assert admin.execute(f"SELECT count(*) FROM {schema}.parts WHERE id = 901").fetchone() == (0,)


# ---------------------------------------------------------------------------------------------------------------------
# stream (TAB-T-B34, B35)
# ---------------------------------------------------------------------------------------------------------------------


def _cursors(transaction: Transaction) -> int:
    """Named cursors of the session (the portal of a prepared statement is listed without a name)."""
    return int(transaction.fetch_scalar("SELECT count(*) FROM pg_cursors WHERE name <> ''"))


def test_b34_stream_uses_a_server_cursor(db: _StagedDatabase) -> None:
    with db.read() as transaction:
        tx = staged(transaction)
        assert _cursors(transaction) == 0
        seen = 0
        sizes: list[int] = []
        for batch in tx.stream("SELECT n, repeat('x', 100) FROM generate_series(1, 20000) n", batch_size=1000):
            assert _cursors(transaction) == 1  # DECLARE ... CURSOR: the rows stay on the server
            sizes.append(len(batch))
            seen += sum(1 for _ in batch)
        assert (seen, sizes) == (20000, [1000] * 20)
        assert _cursors(transaction) == 0  # closed after the last batch


def test_b34_stream_closed_early_and_at_the_end(db: _StagedDatabase) -> None:
    with db.read() as transaction:
        stream = staged(transaction).stream("SELECT generate_series(1, 1000000)", batch_size=100)
        assert next(stream)[0][0] == 1
        assert isinstance(stream, types.GeneratorType)
        stream.close()
        assert _cursors(transaction) == 0
        left_open = staged(transaction).stream("SELECT generate_series(1, 1000000)", batch_size=100)
        next(left_open)
        assert _cursors(transaction) == 1
    # The transaction's end closed it; the generator is dead.
    with pytest.raises(UsageError):
        next(left_open)
    assert isinstance(left_open, types.GeneratorType)
    left_open.close()
    with db.read() as transaction:
        assert _cursors(transaction) == 0


def test_b34_stream_needs_rows(db: _StagedDatabase, schema: str) -> None:
    with pytest.raises(StatementError), db.transaction() as transaction:
        next(staged(transaction).stream(f"UPDATE {schema}.parts SET label = label"))  # DECLARE takes a query only


def test_b35_stream_timeout_and_cancel(db: _StagedDatabase) -> None:
    with db.read() as transaction, pytest.raises(StatementTimeout):
        next(staged(transaction).stream("SELECT pg_sleep(5)", timeout=0.2))
    with pytest.raises(Canceled), db.read() as transaction:
        stream = staged(transaction).stream("SELECT generate_series(1, 100000)", batch_size=10)
        next(stream)
        staged(transaction).cancel()
        next(stream)


# ---------------------------------------------------------------------------------------------------------------------
# cancel (TAB-T-B15, B40)
# ---------------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("method", ["query", "fetch_scalar", "stream"])
def test_b15_cancel_from_a_second_thread(db: _StagedDatabase, schema: str, method: str) -> None:
    """``cancel_safe`` stops the statement within 1 s (TAB-033 acceptance); the transaction rolls back."""
    called: list[float] = []
    with pytest.raises(Canceled) as info, db.transaction() as transaction:
        transaction.execute(f"INSERT INTO {schema}.parts (id) VALUES (700)")

        def cancel() -> None:
            time.sleep(0.3)
            called.append(time.monotonic())
            staged(transaction).cancel()

        thread = threading.Thread(target=cancel)
        thread.start()
        try:
            if method == "query":
                staged(transaction).query("SELECT pg_sleep(30)")
            elif method == "stream":
                next(staged(transaction).stream("SELECT pg_sleep(30)"))
            else:
                transaction.fetch_scalar("SELECT pg_sleep(30)")
        finally:
            stopped = time.monotonic()
            thread.join()
    assert stopped - called[0] < 1.0
    assert info.value.classification is not None
    assert info.value.classification.kind is Kind.CANCELED
    assert info.value.classification.sqlstate in ("57014", None)
    assert db.query(f"SELECT count(*) FROM {schema}.parts WHERE id = 700").rows == ((0,),)


def test_b40_cancel_never_reaches_the_next_user_of_the_connection(open_db: Callable[..., _StagedDatabase]) -> None:
    db = open_db(Profile(pool_size=1))
    with db.read() as first:
        pid = first.fetch_scalar("SELECT pg_backend_pid()")
    with db.read() as second:
        assert second.fetch_scalar("SELECT pg_backend_pid()") == pid  # the same server session
        late = threading.Thread(target=staged(first).cancel)
        late.start()
        assert staged(second).query("SELECT pg_sleep(0.5), 1").rows[0][1] == 1
        late.join()


def test_b40_cancel_while_idle_refuses_the_rest(db: _StagedDatabase, schema: str) -> None:
    with pytest.raises(Canceled), db.transaction() as transaction:
        transaction.execute(f"INSERT INTO {schema}.parts (id) VALUES (701)")
        staged(transaction).cancel()
        with pytest.raises(Canceled):
            transaction.fetch_scalar("SELECT 1")
    assert db.query(f"SELECT count(*) FROM {schema}.parts WHERE id = 701").rows == ((0,),)


# ---------------------------------------------------------------------------------------------------------------------
# inspector (TAB-T-B36)
# ---------------------------------------------------------------------------------------------------------------------


def test_b36_inspector_is_read_only(db: _StagedDatabase, schema: str) -> None:
    with db.inspector() as inspector:
        assert inspector.get_table_names(schema=schema) == ["parts"]
        columns = inspector.get_columns("parts", schema=schema)
        assert [(c["name"], c["nullable"]) for c in columns] == [("id", False), ("label", True)]
        assert inspector.get_pk_constraint("parts", schema=schema)["constrained_columns"] == ["id"]
        connection = inspector.bind
        assert isinstance(connection, Connection)
        assert connection.exec_driver_sql("SHOW transaction_read_only").scalar() == "on"
    with pytest.raises(ReadOnlyViolation), db.inspector() as inspector:
        bound = inspector.bind
        assert isinstance(bound, Connection)
        bound.exec_driver_sql(f"DELETE FROM {schema}.parts")
    assert db.query(f"SELECT count(*) FROM {schema}.parts").rows == ((25,),)


def test_b36_inspector_catalog_of_500_tables(db: _StagedDatabase, schema: str, admin: Any) -> None:
    """STU-010: 500 tables; the catalog through ``get_multi_columns`` (one query per kind of object)."""
    admin.execute(
        "DO $$ BEGIN FOR i IN 1..500 LOOP EXECUTE format("
        f"'CREATE TABLE {schema}.%I (id int PRIMARY KEY, a text NOT NULL, b numeric)', 't_' || i); END LOOP; END $$"
    )
    started = time.monotonic()
    with db.inspector() as inspector:
        names = inspector.get_table_names(schema=schema)
        columns = inspector.get_multi_columns(schema=schema)
    elapsed = time.monotonic() - started
    assert len(names) == 501 and len(columns) == 501
    assert elapsed < 3.0, elapsed


# ---------------------------------------------------------------------------------------------------------------------
# on_begin, savepoint (TAB-T-B37, B38; experimental)
# ---------------------------------------------------------------------------------------------------------------------


def test_b37_on_begin_sets_transaction_local_state(db: _StagedDatabase) -> None:
    def tenant(connection: Connection, read_only: bool) -> None:
        mode = "read" if read_only else "write"
        connection.exec_driver_sql(f"SELECT set_config('app.mode', '{mode}', true)")

    unregister = db.on_begin(tenant)
    assert db.query("SELECT current_setting('app.mode', true)").rows == (("read",),)
    with db.transaction() as transaction:
        assert transaction.fetch_scalar("SELECT current_setting('app.mode', true)") == "write"
    unregister()
    assert db.query("SELECT coalesce(current_setting('app.mode', true), '')").rows == (("",),)


def test_b38_savepoint_after_an_error(db: _StagedDatabase, schema: str) -> None:
    with db.transaction() as transaction:
        tx = staged(transaction)
        tx.execute(f"INSERT INTO {schema}.parts (id) VALUES (800)")
        with pytest.raises(TabrionError), tx.savepoint():
            tx.execute(f"INSERT INTO {schema}.parts (id) VALUES (801)")
            tx.execute(f"INSERT INTO {schema}.parts (id) VALUES (1)")  # unique: the transaction would be aborted
        tx.execute(f"INSERT INTO {schema}.parts (id) VALUES (802)")  # usable again after ROLLBACK TO SAVEPOINT
    rows = db.query(f"SELECT id FROM {schema}.parts WHERE id >= 800 ORDER BY id").rows
    assert rows == ((800,), (802,))


# ---------------------------------------------------------------------------------------------------------------------
# One statement per call through the extended protocol; read-only (TAB-T-B39, I-104)
# ---------------------------------------------------------------------------------------------------------------------

ESCAPES = [
    "COMMIT; BEGIN READ WRITE; INSERT INTO {schema}.parts (id) VALUES (666)",
    "SELECT 1; COMMIT; BEGIN READ WRITE; INSERT INTO {schema}.parts (id) VALUES (666); COMMIT",
    "SET TRANSACTION READ WRITE; INSERT INTO {schema}.parts (id) VALUES (666)",
    "ROLLBACK; INSERT INTO {schema}.parts (id) VALUES (666)",
]


@pytest.mark.parametrize("text", ESCAPES)
def test_b39_several_commands_are_refused_by_the_server(
    db: _StagedDatabase, schema: str, admin: Any, monkeypatch: pytest.MonkeyPatch, text: str
) -> None:
    """The multi-statement escape of the Studio review: psycopg would send it as one simple query, which ends the
    read-only transaction and writes. ``query`` and ``stream`` prepare the statement, so the server refuses it.
    The refusal of a leading ``COMMIT``/``ROLLBACK`` before sending (N-1) is switched off: this pins the server's."""
    monkeypatch.setattr(tabrion._sa._v02, "_refuse_transaction_control", lambda transaction, executable: None)
    sql = text.format(schema=schema)
    with pytest.raises(StatementError) as info:
        db.query(sql)
    assert info.value.classification is not None
    assert info.value.classification.sqlstate == "42601"
    with pytest.raises(StatementError), db.read() as transaction:
        next(staged(transaction).stream(sql))
    with db.read() as transaction, pytest.raises(StatementError):
        staged(transaction).query(sql, max_rows=1)
    assert admin.execute(f"SELECT count(*) FROM {schema}.parts WHERE id = 666").fetchone() == (0,)


def test_b39_refused_whatever_the_lexer_thinks(
    open_db: Callable[..., _StagedDatabase], schema: str, admin: Any
) -> None:
    """H-1 of the Studio review: with ``standard_conforming_strings = off`` a client-side split sees one statement
    where the server sees several. The server's own parser decides here, so the text is refused as well."""
    db = open_db(connect_args={"options": "-c standard_conforming_strings=off"})
    # Verbatim from the Studio review (H-1): one statement to a split, six to the server under this setting.
    sql = f"SELECT '\\''; ROLLBACK; BEGIN READ WRITE; INSERT INTO {schema}.parts (id) VALUES (667); COMMIT; SELECT ''''"
    assert len(split_statements(sql)) == 1
    assert db.query("SHOW standard_conforming_strings").rows == (("off",),)
    with pytest.raises(StatementError):
        db.query(sql)
    assert admin.execute(f"SELECT count(*) FROM {schema}.parts WHERE id = 667").fetchone() == (0,)


def test_b39_lifting_read_only_in_one_statement(db: _StagedDatabase, schema: str) -> None:
    """One statement cannot lift read-only and write: the server or the read-back before COMMIT refuses (I-56)."""
    with pytest.raises(UsageError) as info:
        db.query("SET TRANSACTION READ WRITE")  # first statement, before any snapshot: allowed by the server
    assert info.value.code == "database.read_only"
    with pytest.raises(TabrionError):
        db.query("SELECT set_config('transaction_read_only', 'off', true)")
    with pytest.raises(ReadOnlyViolation):
        db.query(f"WITH w AS (INSERT INTO {schema}.parts (id) VALUES (668) RETURNING id) SELECT * FROM w")
    assert db.query(f"SELECT count(*) FROM {schema}.parts WHERE id = 668").rows == ((0,),)


def test_b39_read_only_profile(open_db: Callable[..., _StagedDatabase], schema: str) -> None:
    profile = Profile(read_only=True, postgres=SessionSettings(statement_timeout_ms=10_000))
    readonly = open_db(profile)
    assert readonly.query(f"SELECT count(*) FROM {schema}.parts").rows == ((0,),)
    with pytest.raises(ReadOnlyViolation):
        readonly.query(f"INSERT INTO {schema}.parts (id) VALUES (669)")
    with readonly.inspector() as inspector:
        assert inspector.get_table_names(schema=schema) == ["parts"]
    with pytest.raises(UsageError):
        readonly.run_transaction(lambda transaction: None)


def test_b39_transient_errors_are_typed(db: _StagedDatabase, schema: str, admin: Any) -> None:
    """A lock wait under ``lock_timeout`` in ``query`` is ``TransientError`` (LOCKED), as in the 0.1 methods."""
    with admin.transaction():
        admin.execute(f"LOCK TABLE {schema}.parts IN ACCESS EXCLUSIVE MODE")
        with db.read() as transaction:
            transaction.execute("SET LOCAL lock_timeout = '100ms'")
            with pytest.raises(TransientError):
                staged(transaction).query(f"SELECT * FROM {schema}.parts")


# ---------------------------------------------------------------------------------------------------------------------
# Review fixes of #32: a late cancel request (H-1), the caller's SQL ends the transaction (L-1), the running guard (L-3)
# ---------------------------------------------------------------------------------------------------------------------


class SlowCancelProxy:
    """A TCP proxy in front of the server that holds every CancelRequest back for ``delay`` seconds.

    It plays a server that is slow to accept or fork for the cancel connection: psycopg's ``cancel_safe`` gives up
    (``CancellationTimeout``) while the request is still on its way, and the server acts on it later.
    """

    CANCEL_REQUEST = 80877102

    def __init__(self, host: str, port: int, delay: float) -> None:
        self.upstream = (host, port)
        self.delay = delay
        self.delivered: list[float] = []
        self.listener = socket.create_server(("127.0.0.1", 0))
        self.port = self.listener.getsockname()[1]
        self.closed = False
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while not self.closed:
            try:
                client, _ = self.listener.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(client,), daemon=True).start()

    def _serve(self, client: socket.socket) -> None:
        with contextlib.suppress(OSError), client:
            first = client.recv(8, socket.MSG_WAITALL)
            if len(first) < 8:
                return
            length, code = struct.unpack("!ii", first)
            if length == 16 and code == self.CANCEL_REQUEST:
                first += client.recv(8, socket.MSG_WAITALL)
                time.sleep(self.delay)
                self.delivered.append(time.monotonic())
            with socket.create_connection(self.upstream) as server:
                server.sendall(first)
                pump = threading.Thread(target=self._pump, args=(server, client), daemon=True)
                pump.start()
                self._pump(client, server)
                pump.join(5)

    @staticmethod
    def _pump(source: socket.socket, target: socket.socket) -> None:
        with contextlib.suppress(OSError):
            while data := source.recv(65536):
                target.sendall(data)
            target.shutdown(socket.SHUT_WR)

    def dsn(self, base: str) -> str:
        params = psycopg.conninfo.conninfo_to_dict(base)
        params.pop("hostaddr", None)
        params.update(host="127.0.0.1", port=str(self.port), sslmode="disable", gssencmode="disable")
        return str(psycopg.conninfo.make_conninfo("", **params))

    def close(self) -> None:
        self.closed = True
        self.listener.close()


@pytest.mark.slow
def test_b40_a_late_cancel_request_never_reaches_the_next_user(
    open_db: Callable[..., _StagedDatabase], admin_url: str
) -> None:
    """H-1 of the review of #32: ``cancel_safe`` gives up after 2 s, the server receives the request after 3 s. The
    canceled transaction's connection must not go back to the pool, or the request cancels the next user's statement
    on the same server session."""
    params = psycopg.conninfo.conninfo_to_dict(admin_url)
    host = str(params.get("host") or "")
    if not host or host.startswith("/"):
        pytest.skip("the proxy needs a TCP server address")
    proxy = SlowCancelProxy(host, int(params.get("port") or 5432), delay=3.0)
    try:
        db = open_db(Profile(pool_size=1), url=proxy.dsn(admin_url))
        started = time.monotonic()
        with pytest.raises(Canceled), db.read() as first:
            pid = first.fetch_scalar("SELECT pg_backend_pid()")
            threading.Timer(0.15, staged(first).cancel).start()
            assert staged(first).query("SELECT pg_sleep(0.5), 1").rows[0][1] == 1  # the request is held back
            # L-2: the cancel connection did not hold the statement's end back.
            assert time.monotonic() - started < 1.5
        with db.read() as second:
            assert second.fetch_scalar("SELECT pg_backend_pid()") != pid  # a fresh server session
            result = staged(second).query("SELECT pg_sleep(4), 'ok'")
            assert result.rows[0][1] == "ok"
        assert proxy.delivered, "the held-back request reached the server meanwhile"
    finally:
        proxy.close()


def test_b40_every_sent_cancel_keeps_the_connection_out_of_the_pool(open_db: Callable[..., _StagedDatabase]) -> None:
    db = open_db(Profile(pool_size=1))
    with pytest.raises(Canceled), db.read() as first:
        pid = first.fetch_scalar("SELECT pg_backend_pid()")
        threading.Timer(0.2, staged(first).cancel).start()
        staged(first).query("SELECT pg_sleep(5)")
    with db.read() as second:
        assert second.fetch_scalar("SELECT pg_backend_pid()") != pid
    # A cancel while nothing ran sent no request: that connection stays in the pool.
    with pytest.raises(Canceled), db.read() as third:
        pid = third.fetch_scalar("SELECT pg_backend_pid()")
        staged(third).cancel()
    with db.read() as fourth:
        assert fourth.fetch_scalar("SELECT pg_backend_pid()") == pid


def test_l3_cancel_sends_only_while_a_statement_runs(db: _StagedDatabase, monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[str] = []
    original = tabrion._sa.database._send_cancel

    def counting(dialect: str, dbapi: Any) -> None:
        sent.append(dialect)
        original(dialect, dbapi)

    monkeypatch.setattr(tabrion._sa.database, "_send_cancel", counting)
    with pytest.raises(Canceled), db.read() as transaction:
        staged(transaction).cancel()  # idle: marked, nothing sent
        assert sent == []
    with pytest.raises(Canceled), db.read() as transaction:
        threading.Timer(0.2, staged(transaction).cancel).start()
        staged(transaction).query("SELECT pg_sleep(5)")
    assert 1 <= len(sent) <= 4


@pytest.mark.parametrize("first_line", [True, False], ids=["refused-before-sending", "status-check-only"])
@pytest.mark.parametrize(
    "steps",
    [
        ["COMMIT", "BEGIN READ WRITE", "INSERT INTO {schema}.parts (id) VALUES (670)", "COMMIT"],
        ["ROLLBACK", "BEGIN READ WRITE", "INSERT INTO {schema}.parts (id) VALUES (670)", "COMMIT", "BEGIN READ ONLY"],
        ["END", "START TRANSACTION READ WRITE", "INSERT INTO {schema}.parts (id) VALUES (670)", "END"],
    ],
)
def test_l1_the_callers_sql_cannot_end_the_transaction(
    open_db: Callable[..., _StagedDatabase],
    schema: str,
    admin: Any,
    monkeypatch: pytest.MonkeyPatch,
    steps: list[str],
    first_line: bool,
) -> None:
    """L-1 of the review of #32: one command per call, but ``COMMIT`` alone ends Tabrion's ``read()``. The call
    raises, every later statement is refused, the block does not end quietly, and the connection leaves the pool.
    ``status-check-only`` switches the refusal before sending off (N-1), so the status check after the statement,
    the second line, is pinned on its own."""
    if not first_line:
        monkeypatch.setattr(tabrion._sa._v02, "_refuse_transaction_control", lambda transaction, executable: None)
    db = open_db(Profile(pool_size=1))
    with pytest.raises(UsageError) as at_end, db.read() as transaction:
        pid = transaction.fetch_scalar("SELECT pg_backend_pid()")
        with pytest.raises(UsageError) as info:
            staged(transaction).query(steps[0])
        assert info.value.code == "database.transaction_ended"
        for step in steps[1:]:
            with pytest.raises(UsageError):
                staged(transaction).query(step.format(schema=schema))
        with pytest.raises(UsageError):
            transaction.fetch_scalar("SELECT 1")
    assert at_end.value.code == "database.transaction_ended"
    assert admin.execute(f"SELECT count(*) FROM {schema}.parts WHERE id = 670").fetchone() == (0,)
    with db.read() as transaction:
        assert transaction.fetch_scalar("SELECT pg_backend_pid()") != pid
    if first_line:
        with pytest.raises(UsageError) as info, db.read() as transaction:
            staged(transaction).stream("COMMIT")
        assert info.value.code == "database.transaction_ended"
    else:
        # stream cannot end it on PostgreSQL: DECLARE takes a query only.
        with pytest.raises(StatementError), db.read() as transaction:
            next(staged(transaction).stream("COMMIT"))


@pytest.mark.parametrize(
    ("block", "steps"),
    [
        (
            "read",
            [
                "COMMIT AND CHAIN",
                "SET TRANSACTION READ WRITE",
                "INSERT ({n})",
                "COMMIT AND CHAIN",
                "SET TRANSACTION READ ONLY",
            ],
        ),
        ("read", ["COMMIT AND CHAIN", "SET TRANSACTION READ WRITE", "INSERT ({n})", "COMMIT AND CHAIN"]),
        ("transaction", ["INSERT ({n})", "ROLLBACK AND CHAIN", "INSERT ({m})"]),
        ("transaction", ["INSERT ({n})", "COMMIT AND CHAIN", "SELECT 1/0"]),
    ],
    ids=["read-chain-read-only", "read-chain", "transaction-rollback-chain", "transaction-commit-chain"],
)
def test_n1_and_chain_cannot_end_the_transaction(
    db: _StagedDatabase, schema: str, admin: Any, block: str, steps: list[str]
) -> None:
    """N-1 of the re-review of #32: after ``... AND CHAIN`` PostgreSQL is in a new transaction at once, which the
    status check cannot tell from Tabrion's. The statement is refused before it is sent, nothing is committed, and
    the block fails with ``database.transaction_ended``."""
    texts = [
        step.replace("INSERT ({n})", f"INSERT INTO {schema}.parts (id) VALUES (681)").replace(
            "INSERT ({m})", f"INSERT INTO {schema}.parts (id) VALUES (682)"
        )
        for step in steps
    ]
    ended = next(i for i, text in enumerate(texts) if "AND CHAIN" in text)
    with pytest.raises(UsageError) as at_end, getattr(db, block)() as transaction:
        for i, text in enumerate(texts):
            if i < ended:
                staged(transaction).query(text)
                continue
            with pytest.raises(UsageError) as info:
                staged(transaction).query(text)
            assert info.value.code == "database.transaction_ended"
    assert at_end.value.code == "database.transaction_ended"
    assert admin.execute(f"SELECT count(*) FROM {schema}.parts WHERE id IN (681, 682)").fetchone() == (0,)


def test_info_on_begin_registration_from_many_threads(db: _StagedDatabase) -> None:
    """Info I-1 of the review of #32: registrations and unregistrations from many threads lose none."""
    seen: list[int] = []
    keep = [db.on_begin(lambda connection, read_only: seen.append(1)) for _ in range(3)]

    def churn() -> None:
        for _ in range(200):
            db.on_begin(lambda connection, read_only: None)()

    threads = [threading.Thread(target=churn) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    db.query("SELECT 1")
    assert len(seen) == 3
    assert len(db._begin_callbacks) == 3
    for unregister in keep:
        unregister()
