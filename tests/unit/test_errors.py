"""TAB-010: ``tabrion.errors`` without a server (contract section 1, ADR 0004).

PostgreSQL errors are built here as duck-typed stand-ins (a class from the module ``psycopg.errors`` with
``sqlstate`` and ``diag``), so every row of the mapping table is covered on every platform; the same rows against a
real PostgreSQL 16 are in ``tests/postgres/test_errors_postgres.py``. SQLite errors are real.

Test IDs: TAB-T-E01..E09 (mapping), E10 (canary), E11 (no ``__cause__``), E13 (never raises), E15 (``lc_messages``),
E16 (no driver import), E17..E20 (exception hierarchy, assigned in TAB-010), E21..E24 (redaction filter).
"""

from __future__ import annotations

from collections.abc import Mapping
import copy
import io
import json
import logging
from pathlib import Path
import pickle
import sqlite3
import sys
import time
import traceback
from types import SimpleNamespace
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st
import pytest

from tabrion import errors
from tabrion.errors import (
    Classification,
    ConfigurationError,
    DatabaseError,
    DbErrorRedactionFilter,
    IntegrityViolation,
    Kind,
    SchemaTooNew,
    TabrionError,
    UnknownDatabaseError,
    classify,
    safe_message,
)
from tests.support import run_python

CANARY = "SECRET-4711"


# ---------------------------------------------------------------------------------------------------------------------
# Stand-ins for driver errors (no driver imported)
# ---------------------------------------------------------------------------------------------------------------------


def _psycopg_class(name: str, base: type[Exception] = Exception, module: str = "psycopg.errors") -> type[Exception]:
    return type(name, (base,), {"__module__": module})


PgError = _psycopg_class("Error", module="psycopg")
PgOperationalError = _psycopg_class("OperationalError", PgError, "psycopg")
PgProgrammingError = _psycopg_class("ProgrammingError", PgError, "psycopg")
PgDataError = _psycopg_class("DataError", PgError, "psycopg")
PgIntegrityError = _psycopg_class("IntegrityError", PgError, "psycopg")


def pg_error(
    sqlstate: str | None,
    message: str = "",
    *,
    detail: str | None = None,
    base: type[Exception] = PgError,
    constraint: str | None = None,
    table: str | None = None,
    column: str | None = None,
) -> Exception:
    """A psycopg-like error: ``str`` holds the primary message plus psycopg's ``DETAIL`` line."""
    text = message + (f"\nDETAIL:  {detail}" if detail else "")
    exc = base(text)
    exc.sqlstate = sqlstate  # type: ignore[attr-defined]
    exc.diag = SimpleNamespace(  # type: ignore[attr-defined]
        message_primary=message,
        message_detail=detail,
        message_hint=None,
        context=None,
        constraint_name=constraint,
        table_name=table,
        column_name=column,
    )
    return exc


SADBAPIError = type("DBAPIError", (Exception,), {"__module__": "sqlalchemy.exc"})
SAIntegrityError = type("IntegrityError", (SADBAPIError,), {"__module__": "sqlalchemy.exc"})
SAStatementError = type("StatementError", (Exception,), {"__module__": "sqlalchemy.exc"})


def sa_error(orig: BaseException, *, invalidated: bool = False, cls: type[Exception] = SAIntegrityError) -> Exception:
    """A SQLAlchemy-like ``DBAPIError``: ``str`` holds the driver text, the statement and ``[parameters: ...]``."""
    statement = "INSERT INTO t VALUES (%(v)s)"
    exc = cls(
        f"({type(orig).__module__}.{type(orig).__name__}) {orig}\n[SQL: {statement}]\n"
        f"[parameters: {{'v': '{CANARY}'}}]\n(Background on this error at: https://sqlalche.me/e/20/gkpj)"
    )
    exc.orig = orig  # type: ignore[attr-defined]
    exc.statement = statement  # type: ignore[attr-defined]
    exc.params = {"v": CANARY}  # type: ignore[attr-defined]
    exc.connection_invalidated = invalidated  # type: ignore[attr-defined]
    return exc


def sqlite_error(sql: str, setup: str = "") -> sqlite3.Error:
    conn = sqlite3.connect(":memory:")
    try:
        if setup:
            conn.executescript(setup)
        conn.execute(sql)
    except sqlite3.Error as exc:
        return exc
    finally:
        conn.close()
    raise AssertionError(f"{sql!r} did not fail")


def sqlite_coded(name: str, cls: type[sqlite3.Error] = sqlite3.OperationalError, text: str = "") -> sqlite3.Error:
    exc = cls(text or name)
    exc.sqlite_errorname = name
    return exc


# ---------------------------------------------------------------------------------------------------------------------
# TAB-T-E01..E09: the mapping table, row by row
# ---------------------------------------------------------------------------------------------------------------------

# (sqlstate, message, kind, retryable when the outcome is known, connection_usable)
PG_ROWS = [
    ("08000", "", Kind.CONNECTION, True, False),
    ("08003", "", Kind.CONNECTION, True, False),
    ("08006", "", Kind.CONNECTION, True, False),
    ("08001", "", Kind.CONNECTION, True, False),
    ("08004", "", Kind.CONNECTION, True, False),
    ("57P01", "terminating connection due to administrator command", Kind.CONNECTION, True, False),
    ("57P02", "", Kind.CONNECTION, True, False),
    ("57P03", "the database system is starting up", Kind.CONNECTION, True, False),
    ("57014", "canceling statement due to statement timeout", Kind.TIMEOUT, False, True),
    ("57014", "canceling statement due to user request", Kind.CANCELED, False, True),
    ("25P03", "terminating connection due to idle-in-transaction timeout", Kind.TIMEOUT, False, False),
    ("55P03", "canceling statement due to lock timeout", Kind.LOCKED, True, True),
    ("40P01", "deadlock detected", Kind.DEADLOCK, True, True),
    ("40001", "could not serialize access", Kind.SERIALIZATION, True, True),
    ("23505", "", Kind.UNIQUE, False, True),
    ("23503", "", Kind.FOREIGN_KEY, False, True),
    ("23502", "", Kind.NOT_NULL, False, True),
    ("23514", "", Kind.CHECK, False, True),
    ("23P01", "", Kind.EXCLUSION, False, True),
    ("22012", "division by zero", Kind.DATA, False, True),
    ("22P02", "invalid input syntax", Kind.DATA, False, True),
    ("22003", "numeric field overflow", Kind.DATA, False, True),
    ("42P01", "", Kind.UNDEFINED_OBJECT, False, True),
    ("42703", "", Kind.UNDEFINED_OBJECT, False, True),
    ("42883", "", Kind.UNDEFINED_OBJECT, False, True),
    ("42704", "", Kind.UNDEFINED_OBJECT, False, True),
    ("3F000", "", Kind.UNDEFINED_OBJECT, False, True),
    ("42601", "", Kind.SYNTAX, False, True),
    ("42501", "", Kind.PERMISSION, False, True),
    ("25006", "", Kind.READ_ONLY, False, True),
    ("XX001", "", Kind.CORRUPT, False, True),
    ("XX002", "", Kind.CORRUPT, False, True),
    ("53100", "", Kind.DISK_FULL, False, True),
    ("53200", "", Kind.RESOURCE, False, True),
    ("54000", "", Kind.RESOURCE, False, True),
    ("54001", "statement too complex", Kind.RESOURCE, False, True),
    ("XX000", "internal error", Kind.UNKNOWN, False, True),
    ("0A000", "feature not supported", Kind.UNKNOWN, False, True),
]


@pytest.mark.parametrize(("sqlstate", "message", "kind", "retryable", "usable"), PG_ROWS)
def test_e01_e09_postgres_mapping(sqlstate: str, message: str, kind: Kind, retryable: bool, usable: bool) -> None:
    c = classify(pg_error(sqlstate, message), sent=False)
    assert (c.kind, c.retryable, c.connection_usable) == (kind, retryable, usable)
    assert c.sqlstate == sqlstate and c.driver == "psycopg" and c.sqlite_code is None
    assert not c.outcome_unknown


def test_e01_too_many_connections_is_retryable_however_it_was_called() -> None:
    """53300 comes only while connecting: nothing ran, so the outcome is known even with ``sent=True``."""
    calls: list[dict[str, bool]] = [{}, {"sent": False}, {"during_commit": True}]
    for kwargs in calls:
        c = classify(pg_error("53300", "sorry, too many clients already"), **kwargs)
        assert (c.kind, c.retryable, c.outcome_unknown, c.connection_usable) == (Kind.CONNECTION, True, False, False)


@pytest.mark.parametrize(
    ("sent", "during_commit", "outcome_unknown"),
    [(True, False, True), (False, False, False), (False, True, True), (True, True, True)],
)
def test_e01_outcome_unknown_and_no_retry_after_sending(sent: bool, during_commit: bool, outcome_unknown: bool) -> None:
    """R-NORETRY: a connection lost after sending (or during COMMIT) is never retryable."""
    for exc in (pg_error("57P01"), pg_error("08006"), pg_error(None, "server closed", base=PgOperationalError)):
        c = classify(exc, sent=sent, during_commit=during_commit)
        assert c.kind is Kind.CONNECTION
        assert c.outcome_unknown is outcome_unknown
        assert c.retryable is (not outcome_unknown)


def test_e01_outcome_unknown_only_for_connection_errors() -> None:
    for sqlstate, message, kind, _, _ in PG_ROWS:
        c = classify(pg_error(sqlstate, message), sent=True, during_commit=True)
        assert c.outcome_unknown is (kind is Kind.CONNECTION), sqlstate


def test_e02_psycopg_client_errors_without_sqlstate() -> None:
    lost = classify(pg_error(None, "the connection is closed", base=PgOperationalError))
    assert (lost.kind, lost.connection_usable, lost.driver) == (Kind.CONNECTION, False, "psycopg")
    placeholders = classify(pg_error(None, "the query has 2 placeholders", base=PgProgrammingError))
    assert (placeholders.kind, placeholders.retryable, placeholders.connection_usable) == (Kind.UNKNOWN, False, True)
    assert classify(pg_error(None, "cannot dump", base=PgDataError)).kind is Kind.DATA
    aborted = _psycopg_class("PipelineAborted", PgOperationalError)("pipeline aborted")
    assert classify(aborted).kind is Kind.UNKNOWN
    pool = type("PoolTimeout", (PgOperationalError,), {"__module__": "psycopg_pool"})("couldn't get a connection")
    checkout = classify(pool, sent=False)
    assert (checkout.kind, checkout.retryable, checkout.outcome_unknown) == (Kind.CONNECTION, True, False)


def test_e03_postgres_object_names() -> None:
    c = classify(
        pg_error(
            "23505", "dup", detail=f"Key (email)=({CANARY}) already exists.", constraint="parts_pkey", table="parts"
        )
    )
    assert (c.constraint, c.table, c.column) == ("parts_pkey", "parts", None)
    nn = classify(pg_error("23502", "null", table="parts", column="name"))
    assert (nn.table, nn.column) == ("parts", "name")
    # Names that are not printable or absurdly long are dropped, not passed on to logs.
    odd = classify(pg_error("23505", constraint="evil\nINJECTED", table="x" * 500))
    assert (odd.constraint, odd.table) == (None, None)


SQLITE_SETUP = """
CREATE TABLE p (id INTEGER PRIMARY KEY);
CREATE TABLE t (id INTEGER PRIMARY KEY, email TEXT UNIQUE, a TEXT, b TEXT, n INT NOT NULL CHECK (n > 0),
                p INT REFERENCES p (id), CONSTRAINT small CHECK (n < 100), UNIQUE (a, b));
INSERT INTO t (id, email, a, b, n) VALUES (1, 'SECRET-4711', 'x', 'y', 1);
PRAGMA foreign_keys = ON;
"""


@pytest.mark.parametrize(
    ("sql", "kind", "code", "constraint", "table", "column"),
    [
        (
            "INSERT INTO t (email, n) VALUES ('SECRET-4711', 1)",
            Kind.UNIQUE,
            "SQLITE_CONSTRAINT_UNIQUE",
            "t.email",
            "t",
            "email",
        ),
        ("INSERT INTO t (id, n) VALUES (1, 1)", Kind.UNIQUE, "SQLITE_CONSTRAINT_PRIMARYKEY", "t.id", "t", "id"),
        ("INSERT INTO t (a, b, n) VALUES ('x', 'y', 1)", Kind.UNIQUE, "SQLITE_CONSTRAINT_UNIQUE", "t.a,t.b", "t", None),
        ("INSERT INTO t (n) VALUES (NULL)", Kind.NOT_NULL, "SQLITE_CONSTRAINT_NOTNULL", "t.n", "t", "n"),
        ("INSERT INTO t (n) VALUES (-1)", Kind.CHECK, "SQLITE_CONSTRAINT_CHECK", None, None, None),
        ("INSERT INTO t (n) VALUES (500)", Kind.CHECK, "SQLITE_CONSTRAINT_CHECK", "small", None, None),
        ("INSERT INTO t (n, p) VALUES (1, 9)", Kind.FOREIGN_KEY, "SQLITE_CONSTRAINT_FOREIGNKEY", None, None, None),
        ("SELECT * FROM missing", Kind.UNDEFINED_OBJECT, "SQLITE_ERROR", None, None, None),
        ("SELECT missing FROM t", Kind.UNDEFINED_OBJECT, "SQLITE_ERROR", None, None, None),
        ("SELECT missing()", Kind.UNDEFINED_OBJECT, "SQLITE_ERROR", None, None, None),
        ("SELEC 1", Kind.SYNTAX, "SQLITE_ERROR", None, None, None),
    ],
)
def test_e04_e08_sqlite_real_errors(
    sql: str, kind: Kind, code: str, constraint: str | None, table: str | None, column: str | None
) -> None:
    c = classify(sqlite_error(sql, SQLITE_SETUP))
    assert (c.kind, c.sqlite_code, c.constraint, c.table, c.column) == (kind, code, constraint, table, column)
    assert c.driver == "sqlite3" and c.sqlstate is None and not c.retryable and c.connection_usable


@pytest.mark.parametrize(
    ("name", "kind", "retryable", "usable"),
    [
        ("SQLITE_BUSY", Kind.LOCKED, True, True),
        ("SQLITE_BUSY_SNAPSHOT", Kind.LOCKED, True, True),
        ("SQLITE_LOCKED", Kind.LOCKED, True, True),
        ("SQLITE_LOCKED_SHAREDCACHE", Kind.LOCKED, True, True),
        ("SQLITE_READONLY", Kind.READ_ONLY, False, True),
        ("SQLITE_READONLY_DBMOVED", Kind.READ_ONLY, False, True),
        ("SQLITE_CORRUPT", Kind.CORRUPT, False, False),
        ("SQLITE_CORRUPT_INDEX", Kind.CORRUPT, False, False),
        ("SQLITE_NOTADB", Kind.CORRUPT, False, False),
        ("SQLITE_FULL", Kind.DISK_FULL, False, True),
        ("SQLITE_INTERRUPT", Kind.CANCELED, False, True),
        ("SQLITE_AUTH", Kind.PERMISSION, False, True),
        ("SQLITE_CANTOPEN", Kind.CONNECTION, False, False),
        ("SQLITE_IOERR", Kind.CONNECTION, False, False),
        ("SQLITE_IOERR_SHORT_READ", Kind.CONNECTION, False, False),
        ("SQLITE_CONSTRAINT_TRIGGER", Kind.UNKNOWN, False, True),
        ("SQLITE_MISUSE", Kind.UNKNOWN, False, True),
    ],
)
def test_e04_e08_sqlite_codes(name: str, kind: Kind, retryable: bool, usable: bool) -> None:
    c = classify(sqlite_coded(name), sent=False)
    assert (c.kind, c.retryable, c.connection_usable, c.sqlite_code) == (kind, retryable, usable, name)


def test_e04_sqlite_busy_is_not_mistaken_for_a_prefix() -> None:
    """``SQLITE_BUSYX`` is no extended code of ``SQLITE_BUSY``; it is malformed and gets no special treatment."""
    assert classify(sqlite_coded("SQLITE_BUSYX")).kind is Kind.UNKNOWN


def test_e04_sqlite_code_from_the_number_when_unnamed() -> None:
    """I-75 (found by QA-2): sqlite3 names codes its build does not know "unknown"; the number decides then."""
    for number, name, kind in [
        (3091, "SQLITE_CONSTRAINT_DATATYPE", Kind.DATA),  # a STRICT table (SQLite 3.37), unnamed on Python 3.13
        (261, "SQLITE_BUSY", Kind.LOCKED),  # SQLITE_BUSY_RECOVERY: an extended code falls back to its primary
        (8458, "SQLITE_IOERR", Kind.CONNECTION),  # SQLITE_IOERR_CORRUPTFS (3.34)
        (1, "SQLITE_ERROR", Kind.UNKNOWN),
    ]:
        exc = sqlite3.IntegrityError("constraint failed")
        exc.sqlite_errorname = "unknown"
        exc.sqlite_errorcode = number
        c = classify(exc)
        assert (c.sqlite_code, c.kind) == (name, kind), number
    values: tuple[object, ...] = (None, "3091", 3091.0, True, 0, 12345678)
    for value in values:
        other = sqlite3.OperationalError("x")
        other.sqlite_errorname = "unknown"
        other.sqlite_errorcode = value  # type: ignore[assignment]
        assert classify(other).sqlite_code is None, value


@pytest.mark.skipif(sqlite3.sqlite_version_info < (3, 37), reason="STRICT tables need SQLite 3.37")
def test_e04_sqlite_strict_datatype_is_data() -> None:
    """A real ``SQLITE_CONSTRAINT_DATATYPE`` (whatever ``sqlite_errorname`` says on this Python) is ``DATA``."""
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE TABLE s (n INTEGER) STRICT")
        with pytest.raises(sqlite3.IntegrityError) as info:
            conn.execute("INSERT INTO s VALUES ('text')")
    finally:
        conn.close()
    c = classify(info.value)
    assert (c.kind, c.sqlite_code) == (Kind.DATA, "SQLITE_CONSTRAINT_DATATYPE")


def test_e04_sqlite_real_lock_and_readonly(tmp_path: Any) -> None:
    path = tmp_path / "db.sqlite"
    writer = sqlite3.connect(path, timeout=0, isolation_level=None)
    other = sqlite3.connect(path, timeout=0, isolation_level=None)
    try:
        writer.execute("CREATE TABLE t (v)")
        writer.execute("BEGIN IMMEDIATE")
        with pytest.raises(sqlite3.OperationalError) as busy:
            other.execute("BEGIN IMMEDIATE")
        assert classify(busy.value).kind is Kind.LOCKED and classify(busy.value).retryable
        writer.execute("ROLLBACK")
    finally:
        writer.close()
        other.close()
    readonly = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        with pytest.raises(sqlite3.OperationalError) as ro:
            readonly.execute("INSERT INTO t VALUES (1)")
        assert classify(ro.value).kind is Kind.READ_ONLY
    finally:
        readonly.close()
    garbage = tmp_path / "garbage.sqlite"
    garbage.write_bytes(b"this is not a database" * 100)
    broken = sqlite3.connect(garbage)
    try:
        with pytest.raises(sqlite3.DatabaseError) as notadb:
            broken.execute("SELECT * FROM sqlite_master")
        c = classify(notadb.value)
        assert (c.kind, c.connection_usable) == (Kind.CORRUPT, False)
    finally:
        broken.close()


def test_e04_sqlite_text_fallback_without_a_code() -> None:
    for text, kind in [
        ("database is locked", Kind.LOCKED),
        ("database disk image is malformed", Kind.CORRUPT),
        ("file is not a database", Kind.CORRUPT),
        ("attempt to write a readonly database", Kind.READ_ONLY),
        ("unable to open database file", Kind.CONNECTION),
        ("Cannot operate on a closed database.", Kind.UNKNOWN),
    ]:
        assert classify(sqlite3.OperationalError(text)).kind is kind, text
    # With SQLITE_ERROR only undefined objects and syntax are read from the text.
    assert classify(sqlite_coded("SQLITE_ERROR", text="user function failed: interrupted")).kind is Kind.UNKNOWN


def test_e09_sqlalchemy_wrappers_are_unwrapped() -> None:
    inner = pg_error("23505", "dup", constraint="t_email_key", base=PgIntegrityError)
    c = classify(sa_error(inner))
    assert (c.kind, c.sqlstate, c.constraint, c.driver) == (Kind.UNIQUE, "23505", "t_email_key", "psycopg")
    lost = classify(sa_error(pg_error(None, "server closed", base=PgOperationalError), invalidated=True))
    assert (lost.kind, lost.connection_usable, lost.outcome_unknown, lost.retryable) == (
        Kind.CONNECTION,
        False,
        True,
        False,
    )
    sqlite_inner = sqlite_error("INSERT INTO t (n) VALUES (NULL)", SQLITE_SETUP)
    assert classify(sa_error(sqlite_inner)).kind is Kind.NOT_NULL
    # A wrapper without a driver error that SQLAlchemy invalidated: the connection is gone.
    invalidated = sa_error(ValueError("x"), invalidated=True, cls=SADBAPIError)
    assert classify(invalidated).kind is Kind.CONNECTION


def test_chains_cause_and_context_up_to_depth_five() -> None:
    inner = pg_error("40001", "could not serialize")

    def wrap(depth: int, link: str) -> BaseException:
        exc: BaseException = inner
        for i in range(depth):
            outer = RuntimeError(f"layer {i}")
            setattr(outer, link, exc)
            exc = outer
        return exc

    for link in ("__cause__", "__context__", "orig"):
        assert classify(wrap(5, link)).kind is Kind.SERIALIZATION, link
        assert classify(wrap(6, link)).kind is Kind.UNKNOWN, link
    # The first error with a code wins over an outer driver error without one that is no connection loss.
    outer = pg_error(None, "outer", base=PgProgrammingError)
    outer.__cause__ = inner
    assert classify(outer).kind is Kind.SERIALIZATION


def test_cycles_end() -> None:
    a, b = RuntimeError("a"), RuntimeError("b")
    a.__context__, b.__context__ = b, a
    assert classify(a).kind is Kind.UNKNOWN
    assert safe_message(a) == "database error"


# ---------------------------------------------------------------------------------------------------------------------
# safe_message and TAB-T-E10 (canary)
# ---------------------------------------------------------------------------------------------------------------------


def test_safe_message_format() -> None:
    assert (
        safe_message(pg_error("23505", constraint="parts_pkey"))
        == "unique violation (SQLSTATE 23505, constraint parts_pkey)"
    )
    assert safe_message(pg_error("57P01")) == "connection error (SQLSTATE 57P01, outcome unknown)"
    assert safe_message(sqlite_coded("SQLITE_BUSY")) == "lock not available (SQLite SQLITE_BUSY)"
    assert safe_message(ValueError(CANARY)) == "database error"
    c = classify(pg_error("23502", table="t", column="c"))
    assert safe_message(c) == safe_message(pg_error("23502", table="t", column="c"))
    assert safe_message(c) == "not null violation (SQLSTATE 23502, table t, column c)"


def test_every_kind_has_a_text() -> None:
    for kind in Kind:
        assert errors._KIND_TEXT[kind]
        c = Classification(kind, False, False, True, "other", None, None, None, None, None)
        assert safe_message(c) == errors._KIND_TEXT[kind]


DETAILS = [
    f"Key (email)=({CANARY}@example.com) already exists.",
    f"Failing row contains (1, {CANARY}, null).",
    f'Key (part)=({CANARY}) is not present in table "parts".',
    f"Key (r)=([1,{CANARY})) conflicts with existing key (r)=([1,3)).",
]


@pytest.mark.parametrize("sqlstate", sorted({row[0] for row in PG_ROWS} | {"53300"}))
@pytest.mark.parametrize("detail", DETAILS)
def test_e10_canary_never_in_safe_message(sqlstate: str, detail: str) -> None:
    exc = pg_error(sqlstate, f"message with {CANARY}", detail=detail, constraint="c", table="t")
    exc.diag.message_hint = f"hint {CANARY}"  # type: ignore[attr-defined]
    exc.diag.context = f'SQL statement "INSERT ... {CANARY}"'  # type: ignore[attr-defined]
    for value in (exc, sa_error(exc), classify(exc)):
        assert CANARY not in safe_message(value)
    assert CANARY not in repr(classify(exc))


def test_e10_canary_never_in_sqlite_messages() -> None:
    for sql in ("INSERT INTO t (email, n) VALUES ('SECRET-4711', 1)", 'SELECT * FROM "SECRET-4711"'):
        exc = sqlite_error(sql, SQLITE_SETUP)
        assert CANARY not in safe_message(exc) and CANARY not in repr(classify(exc))


# ---------------------------------------------------------------------------------------------------------------------
# TAB-T-E13: classify and safe_message never raise
# ---------------------------------------------------------------------------------------------------------------------


class Hostile(Exception):
    """Every attribute access and ``str`` raise."""

    def __getattribute__(self, name: str) -> Any:
        if name in ("__class__", "__dict__") or (name.startswith("__") and name.endswith("__") and name != "__str__"):
            return super().__getattribute__(name)
        raise RuntimeError(f"no {name}")

    def __str__(self) -> str:
        raise RuntimeError("no str")


_VALUES = st.one_of(st.none(), st.text(max_size=8), st.integers(), st.sampled_from(["23505", "57014", "SQLITE_BUSY"]))
_MODULES = st.sampled_from(["builtins", "psycopg", "psycopg.errors", "psycopg2", "sqlalchemy.exc", "psycopg_pool"])


@st.composite
def random_exceptions(draw: st.DrawFn) -> BaseException:
    base = draw(st.sampled_from([Exception, ValueError, KeyboardInterrupt, sqlite3.Error, sqlite3.OperationalError]))
    cls = type(draw(st.sampled_from(["E", "OperationalError", "DataError"])), (base,), {"__module__": draw(_MODULES)})
    exc: BaseException = cls(draw(st.text(max_size=20)))
    for attr in draw(
        st.lists(
            st.sampled_from(["sqlstate", "pgcode", "sqlite_errorname", "orig", "connection_invalidated", "diag"]),
            max_size=4,
        )
    ):
        value: object = draw(_VALUES)
        if attr == "diag":
            value = SimpleNamespace(
                constraint_name=draw(_VALUES), table_name=draw(_VALUES), message_primary=draw(_VALUES)
            )
        if attr == "orig" and draw(st.booleans()):
            value = pg_error(draw(st.sampled_from(["23505", "08006", None])))
        setattr(exc, attr, value)
    if draw(st.booleans()):
        exc.__context__ = exc  # a cycle
    return exc


@settings(max_examples=1000)
@given(random_exceptions(), st.booleans(), st.booleans())
def test_e13_classify_never_raises(exc: BaseException, sent: bool, during_commit: bool) -> None:
    c = classify(exc, sent=sent, during_commit=during_commit)
    assert isinstance(c, Classification) and isinstance(c.kind, Kind)
    assert not (c.retryable and c.outcome_unknown), "R-NORETRY"
    expected = c.sqlstate in ("08007", "40003") or (
        c.kind is Kind.CONNECTION and (sent or during_commit) and c.sqlstate != "53300"
    )
    assert c.outcome_unknown is expected
    assert isinstance(safe_message(exc), str)


def test_e13_hostile_objects() -> None:
    for value in (Hostile(), "not an exception", None, 42):
        assert classify(value).kind is Kind.UNKNOWN  # type: ignore[arg-type]
        assert safe_message(value) == "database error"  # type: ignore[arg-type]


# ---------------------------------------------------------------------------------------------------------------------
# TAB-T-E15 (lc_messages) and E16 (no driver import)
# ---------------------------------------------------------------------------------------------------------------------


def test_e15_localized_cancel_message_is_canceled() -> None:
    german = pg_error("57014", "storniere Anfrage wegen Zeitüberschreitung der Anweisung")
    assert classify(german).kind is Kind.CANCELED


def test_e16_import_loads_no_driver() -> None:
    code = (
        "import sys, tabrion.errors, tabrion.sql, tabrion.config\n"
        "tabrion.errors.classify(ValueError('x')); tabrion.errors.safe_message(ValueError('x'))\n"
        "bad = sorted(n for n in sys.modules if n.split('.')[0] in ('psycopg', 'sqlalchemy', 'psycopg_pool'))\n"
        "assert not bad, bad\nprint('ok')\n"
    )
    result = run_python(code, path=Path(errors.__file__).parents[1])
    assert result.returncode == 0 and "ok" in result.stdout, result.stderr


# ---------------------------------------------------------------------------------------------------------------------
# TAB-T-E11, E17..E20: the exception hierarchy
# ---------------------------------------------------------------------------------------------------------------------


def test_e17_str_and_repr_hold_no_values() -> None:
    original = pg_error("23505", f"dup {CANARY}", detail=f"Key (e)=({CANARY})", constraint="t_e_key")
    err = IntegrityViolation(
        "db.unique",
        classification=classify(original),
        original=original,
        statement=f"INSERT INTO t VALUES ('{CANARY}')" + " " * 300,  # noqa: S608
        details={"table": "t"},
    )
    assert str(err) == "db.unique (table=t): unique violation (SQLSTATE 23505, constraint t_e_key)"
    assert repr(err) == "IntegrityViolation('db.unique')"
    assert err.args == ("db.unique",)
    assert CANARY not in str(err) and CANARY not in repr(err) and CANARY not in str(err.args)
    assert err.statement is not None and len(err.statement) == 200
    assert err.original is original
    formatted = "".join(traceback.format_exception(err))
    assert CANARY not in formatted


def test_e17_details_are_read_only_and_copied() -> None:
    details: dict[str, str | int | None] = {"found": 5, "supported": 4}
    err = SchemaTooNew("sqlite.schema_too_new", details=details)
    details["found"] = 99
    assert dict(err.details) == {"found": 5, "supported": 4}
    with pytest.raises(TypeError):
        err.details["found"] = 1  # type: ignore[index]
    assert str(err) == "sqlite.schema_too_new (found=5, supported=4)"
    assert str(ConfigurationError("config.missing")) == "config.missing"


def test_e18_pickle_and_copy_round_trip() -> None:
    original = sqlite_error("SELECT * FROM missing")
    err = UnknownDatabaseError(
        "db.unknown", classification=classify(original), original=original, statement="SELECT 1", details={"a": 1}
    )
    pickled = pickle.loads(pickle.dumps(err))  # noqa: S301 - our own bytes
    for clone in (pickled, copy.copy(err), copy.deepcopy(err)):
        assert type(clone) is UnknownDatabaseError
        assert (clone.code, clone.classification, clone.statement, dict(clone.details)) == (
            err.code,
            err.classification,
            err.statement,
            {"a": 1},
        )
        assert type(clone.original) is type(original)
        assert str(clone) == str(err) and repr(clone) == repr(err)


def test_e19_database_error_kind() -> None:
    c = classify(pg_error("40P01"))
    assert errors.TransientError("db.deadlock", classification=c).kind is Kind.DEADLOCK
    # F-12: no classification is no failure.
    assert DatabaseError("db.unknown").kind is Kind.UNKNOWN


def test_e20_every_kind_has_a_database_error_class() -> None:
    for kind in Kind:
        cls = errors._error_class(kind)
        assert issubclass(cls, DatabaseError)
    assert errors._error_class(Kind.UNIQUE) is IntegrityViolation
    assert errors._error_class(Kind.CONNECTION) is errors.ConnectionFailed
    assert errors._error_class(Kind.RESOURCE) is errors.ConnectionFailed
    assert errors._error_class(Kind.TIMEOUT) is errors.StatementTimeout
    assert errors._error_class(Kind.UNKNOWN) is UnknownDatabaseError
    assert set(errors._ERROR_CLASS) == set(Kind)


def test_e11_raising_from_none_leaves_no_chain() -> None:
    original = pg_error("23505", detail=f"Key (e)=({CANARY})")
    try:
        try:
            raise original
        except Exception as exc:
            raise IntegrityViolation("db.unique", classification=classify(exc), original=exc) from None
    except TabrionError as err:
        assert err.__cause__ is None and err.__suppress_context__
        assert CANARY not in "".join(traceback.format_exception(err))


# ---------------------------------------------------------------------------------------------------------------------
# TAB-T-E21..E24: DbErrorRedactionFilter
# ---------------------------------------------------------------------------------------------------------------------


class JsonFormatter(logging.Formatter):
    """A JSON formatter like python-json-logger: it formats ``exc_info`` itself when it is still set."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "message": record.getMessage(),
            "sqlstate": getattr(record, "db_sqlstate", None),
            "constraint": getattr(record, "db_constraint", None),
            "exc": self.formatException(record.exc_info) if record.exc_info else record.exc_text,
        }
        return json.dumps(payload)


@pytest.fixture
def capture() -> Any:
    """A logger with a text and a JSON handler, both behind the filter (on the logger)."""
    logger = logging.getLogger(f"tabrion.test.{id(object())}")
    logger.propagate = False
    text, js = io.StringIO(), io.StringIO()
    for stream, formatter in ((text, logging.Formatter("%(levelname)s %(message)s")), (js, JsonFormatter())):
        handler = logging.StreamHandler(stream)
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    redaction = DbErrorRedactionFilter()
    logger.addFilter(redaction)
    yield SimpleNamespace(logger=logger, text=text, json=js, filter=redaction)
    logger.filters.clear()
    logger.handlers.clear()


def _unique_violation() -> Exception:
    return pg_error(
        "23505",
        'duplicate key value violates unique constraint "users_email_key"',
        detail=f"Key (email)=({CANARY}@example.com) already exists.",
        constraint="users_email_key",
        table="users",
        base=PgIntegrityError,
    )


def _raise(exc: BaseException) -> None:
    raise exc


def test_e21_unique_violation_detail_is_gone_from_text_and_json(capture: Any) -> None:
    try:
        _raise(_unique_violation())
    except Exception:
        capture.logger.exception("insert failed")
    text, js = capture.text.getvalue(), capture.json.getvalue()
    assert CANARY not in text and CANARY not in js
    assert "SQLSTATE 23505" in text and "users_email_key" in text
    payload = json.loads(js)
    assert payload["sqlstate"] == "23505" and payload["constraint"] == "users_email_key"
    assert "SQLSTATE 23505" in payload["exc"]
    # Stack trace lines stay.
    assert "Traceback (most recent call last)" in text and "_raise" in text and "line " in text


def test_e21_exception_object_is_unchanged(capture: Any) -> None:
    exc = _unique_violation()
    before = (str(exc), exc.args, exc.diag.message_detail)  # type: ignore[attr-defined]
    capture.logger.error("failed: %s", exc, exc_info=exc)
    assert (str(exc), exc.args, exc.diag.message_detail) == before  # type: ignore[attr-defined]
    assert CANARY not in capture.text.getvalue()


def test_e21_filter_never_drops_a_record() -> None:
    redaction = DbErrorRedactionFilter("any.name")
    assert redaction.name == "any.name"
    records = [
        logging.LogRecord("x", logging.ERROR, __file__, 1, "plain", None, None),
        logging.LogRecord("x", logging.ERROR, __file__, 1, "%s %s", (_unique_violation(),), None),  # too few args
        logging.LogRecord("x", logging.ERROR, __file__, 1, _unique_violation(), None, None),
        logging.LogRecord("x", logging.ERROR, __file__, 1, "m", None, (None, None, None)),
        logging.LogRecord("x", logging.ERROR, __file__, 1, "m", None, (Hostile, Hostile(), None)),
    ]
    for record in records:
        assert redaction.filter(record) is True
        rendered = logging.Formatter().format(record)
        assert CANARY not in rendered


def test_e22_chained_exception(capture: Any) -> None:
    try:
        try:
            _raise(_unique_violation())
        except Exception as exc:
            raise RuntimeError(f"saving the user failed: {exc}") from exc
    except Exception:
        capture.logger.exception("handler failed")
    text = capture.text.getvalue()
    assert CANARY not in text and CANARY not in capture.json.getvalue()
    assert "The above exception was the direct cause" in text
    assert "RuntimeError: saving the user failed: unique violation (SQLSTATE 23505" in text


def test_e22_context_and_exception_groups(capture: Any) -> None:
    try:
        try:
            _raise(_unique_violation())
        except Exception:
            raise ExceptionGroup("batch", [ValueError("other"), ExceptionGroup("inner", [_unique_violation()])])  # noqa: B904
    except Exception:
        capture.logger.exception("batch failed")
    assert CANARY not in capture.text.getvalue() and CANARY not in capture.json.getvalue()
    assert "During handling of the above exception" in capture.text.getvalue()


def test_e22_deep_chain_is_still_redacted(capture: Any) -> None:
    """Redaction is not bound to the classification depth of 5 (Axentra review L-4)."""
    exc: BaseException = _unique_violation()
    for i in range(12):
        outer = RuntimeError(f"layer {i}")
        outer.__cause__ = exc
        exc = outer
    capture.logger.error("deep", exc_info=exc)
    assert CANARY not in capture.text.getvalue()


def test_e23_sqlalchemy_parameters(capture: Any) -> None:
    wrapped = sa_error(_unique_violation())
    try:
        _raise(wrapped)
    except Exception:
        capture.logger.exception("orm failed")
    capture.logger.error("args: %s / %r", wrapped, wrapped)
    capture.logger.error("mapping: %(e)s", {"e": wrapped})
    # A statement error without a driver error still carries [parameters: ...].
    statement_error = sa_error(TypeError("cannot adapt"), cls=SAStatementError)
    capture.logger.error("bind failed", exc_info=statement_error)
    text, js = capture.text.getvalue(), capture.json.getvalue()
    assert CANARY not in text and CANARY not in js
    assert "[parameters:" not in text
    assert text.count("SQLSTATE 23505") >= 3


def test_e23_preformatted_messages_lose_server_text(capture: Any) -> None:
    exc = _unique_violation()
    capture.logger.error(f"insert failed: {exc}")
    capture.logger.error("insert failed: %s", str(exc))
    capture.logger.error("sqlalchemy said: [parameters: {'v': '%s'}]", CANARY)
    assert CANARY not in capture.text.getvalue()


def test_e24_other_exceptions_are_unchanged(capture: Any) -> None:
    try:
        _raise(ValueError(f"value {CANARY} is wrong"))
    except Exception:
        capture.logger.exception("plain failure %s", 1)
    text = capture.text.getvalue()
    assert f"ValueError: value {CANARY} is wrong" in text
    assert "plain failure 1" in text
    record = logging.LogRecord("x", logging.ERROR, __file__, 1, "x %s", (ValueError("y"),), None)
    capture.filter.filter(record)
    assert not hasattr(record, "db_sqlstate") and record.args is not None and record.msg == "x %s"


def test_e24_record_attributes_for_sqlite(capture: Any) -> None:
    capture.logger.error(
        "sqlite", exc_info=sqlite_error("INSERT INTO t (email, n) VALUES ('SECRET-4711', 1)", SQLITE_SETUP)
    )
    payload = json.loads(capture.json.getvalue())
    assert payload["sqlstate"] is None and payload["constraint"] == "t.email"
    assert CANARY not in capture.text.getvalue()


def test_traceback_exception_keeps_the_private_text_attribute() -> None:
    """The filter rewrites ``TracebackException._str`` (Python 3.11 to 3.14); this fails if a version drops it."""
    te = traceback.TracebackException(ValueError, ValueError("x"), None)
    assert hasattr(te, "_str"), sys.version


def test_fallback_keeps_the_record_without_values(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("internal")

    monkeypatch.setattr(DbErrorRedactionFilter, "_redact", broken)
    try:
        _raise(_unique_violation())
    except Exception:
        record = logging.LogRecord("x", logging.ERROR, __file__, 1, "m", None, sys.exc_info())
    assert DbErrorRedactionFilter().filter(record) is True
    rendered = logging.Formatter().format(record)
    assert CANARY not in rendered and "SQLSTATE 23505" in rendered and record.exc_info is None


# ---------------------------------------------------------------------------------------------------------------------
# Review fixes (planning folder reviews/tab-010-012-review.md)
# ---------------------------------------------------------------------------------------------------------------------


def _raised_in_handler(handled: BaseException, raised: BaseException) -> BaseException:
    """``raised``, raised inside ``except`` for ``handled``: Python sets ``raised.__context__ = handled``."""
    try:
        try:
            raise handled
        except BaseException:
            raise raised  # noqa: B904 - the implicit context is the point
    except BaseException as exc:
        return exc


@pytest.mark.parametrize("handled_state", ["40001", "40P01", "55P03", "23505"])
def test_h1_connection_lost_during_commit_inside_a_handler(handled_state: str) -> None:
    """H-1: the retry (or upsert fallback) runs inside ``except``; the COMMIT then loses the connection."""
    lost = _raised_in_handler(pg_error(handled_state), pg_error(None, "server closed", base=PgOperationalError))
    assert lost.__context__ is not None
    c = classify(lost, during_commit=True)
    assert (c.kind, c.outcome_unknown, c.retryable, c.connection_usable) == (Kind.CONNECTION, True, False, False)
    assert c.sqlstate is None
    assert safe_message(lost) == "connection error (outcome unknown)"


def test_h1_sqlalchemy_upsert_fallback_loses_the_connection() -> None:
    integrity = sa_error(pg_error("23505", constraint="up_pkey", base=PgIntegrityError))
    sa_operational = type("OperationalError", (SADBAPIError,), {"__module__": "sqlalchemy.exc"})
    lost = _raised_in_handler(
        integrity, sa_error(pg_error(None, "SSL SYSCALL error: EOF", base=PgOperationalError), cls=sa_operational)
    )
    c = classify(lost, during_commit=True)
    assert (c.kind, c.outcome_unknown, c.retryable, c.connection_usable) == (Kind.CONNECTION, True, False, False)
    assert c.constraint is None


def test_h1_an_older_handled_error_never_classifies_a_driver_error() -> None:
    """A driver error raised while handling a 40001 is classified by itself, not by the 40001."""
    later = _raised_in_handler(pg_error("40001"), pg_error(None, "2 placeholders", base=PgProgrammingError))
    c = classify(later)
    assert (c.kind, c.sqlstate, c.retryable) == (Kind.UNKNOWN, None, False)


def test_h1_outermost_connection_loss_wins_over_a_cause() -> None:
    outer = pg_error(None, "server closed", base=PgOperationalError)
    outer.__cause__ = pg_error("40001")
    assert classify(outer).kind is Kind.CONNECTION
    terminated = pg_error("57P01")
    terminated.__cause__ = pg_error("23505")
    assert classify(terminated).kind is Kind.CONNECTION


def test_h1_context_is_a_fallback_only() -> None:
    # A plain wrapper raised in a handler: the context is the only database error, so it is used.
    wrapped = _raised_in_handler(pg_error("23505", constraint="c"), RuntimeError("save failed"))
    assert classify(wrapped).kind is Kind.UNIQUE
    # ``raise ... from None`` suppresses the context: nothing is followed.
    suppressed = RuntimeError("save failed")
    suppressed.__context__ = pg_error("23505")
    suppressed.__suppress_context__ = True
    assert classify(suppressed).kind is Kind.UNKNOWN
    # A driver error reached through the context brings its own cause along.
    inner = pg_error(None, "x", base=PgProgrammingError)
    inner.__cause__ = pg_error("40P01")
    assert classify(_raised_in_handler(inner, RuntimeError("wrap"))).kind is Kind.DEADLOCK


def test_h1_tabrion_error_keeps_its_classification() -> None:
    c = classify(pg_error("40001"))
    err = errors.TransientError("db.serialization", classification=c, original=pg_error("40001"))
    assert classify(err) == c


@pytest.mark.parametrize("sqlstate", ["08007", "40003"])
@pytest.mark.parametrize("sent", [True, False])
def test_l2_outcome_unknown_by_definition(sqlstate: str, sent: bool) -> None:
    c = classify(pg_error(sqlstate), sent=sent, during_commit=False)
    assert c.outcome_unknown and not c.retryable


@pytest.mark.parametrize(("sqlstate", "kind"), [("57P04", Kind.CONNECTION), ("57P05", Kind.CONNECTION)])
def test_m3_more_connection_rows(sqlstate: str, kind: Kind) -> None:
    c = classify(pg_error(sqlstate), sent=False)
    assert c.kind is kind and not c.connection_usable
    assert c.retryable is (sqlstate == "57P05")  # a dropped database is not worth a retry


def test_m3_wrapper_around_a_lost_connection_is_connection() -> None:
    c = classify(sa_error(pg_error(None, "server closed", base=PgOperationalError), cls=SADBAPIError), sent=False)
    assert (c.kind, c.connection_usable, c.retryable) == (Kind.CONNECTION, False, True)


def test_m3_invalidated_connection_is_not_usable_whatever_the_kind() -> None:
    c = classify(sa_error(pg_error("23505", base=PgIntegrityError), invalidated=True))
    assert c.kind is Kind.UNIQUE and not c.connection_usable


def test_i1_string_subclasses_cannot_steer_the_message() -> None:
    class Sneaky(str):
        def __format__(self, spec: str) -> str:
            return CANARY

        def __str__(self) -> str:
            return CANARY

    exc = pg_error("23505")
    exc.sqlstate = Sneaky("23505")  # type: ignore[attr-defined]
    exc.diag.constraint_name = Sneaky("c")  # type: ignore[attr-defined]
    assert CANARY not in safe_message(exc)
    assert safe_message(exc) == "unique violation (SQLSTATE 23505, constraint c)"


def _h2_calls(e: Exception, sa: Exception) -> list[tuple[str, tuple[Any, ...]]]:
    return [
        (f"x {e!r}", ()),
        ("x %s", (repr(e),)),
        ("x %r", (str(e),)),
        ("x %s", ([e],)),
        ("x %s", ((e,),)),
        ("x %s", (e.args,)),
        ("x %s", ({"k": [e]},)),
        ("x %s", (RuntimeError(e),)),
        ("x %s", ([sa],)),
        (f"x {sa!r}", ()),
    ]


def test_h2_repr_and_container_arguments(capture: Any) -> None:
    e, sa = _unique_violation(), sa_error(_unique_violation())
    for msg, args in _h2_calls(e, sa):
        capture.logger.error(msg, *args)
    capture.logger.error({"err": e})
    text, js = capture.text.getvalue(), capture.json.getvalue()
    assert CANARY not in text and CANARY not in js, text
    assert "[parameters:" not in text
    lines = [line for line in text.splitlines() if line.startswith("ERROR")]
    assert len(lines) == 11


def test_h2_found_errors_inside_containers_set_the_record_attributes() -> None:
    record = logging.LogRecord("x", logging.ERROR, __file__, 1, "x %s", ([_unique_violation()],), None)
    DbErrorRedactionFilter().filter(record)
    assert record.db_sqlstate == "23505"  # type: ignore[attr-defined]
    assert "SQLSTATE 23505" in record.getMessage() and CANARY not in record.getMessage()


def test_n2_container_whose_iteration_raises_keeps_the_record(capture: Any) -> None:
    """Review N-2: a container argument that raises while iterating is no internal error of the filter."""
    e = _unique_violation()

    class Broken(list[object]):
        def __iter__(self) -> Any:
            raise RuntimeError("lazy collection")

    class Half(list[object]):
        def __iter__(self) -> Any:
            yield e
            raise RuntimeError("lazy collection")

    class BrokenMapping(dict[str, object]):
        def items(self) -> Any:
            raise RuntimeError("proxy")

    capture.logger.error("hello %s", Broken([1]))
    capture.logger.error("mapping %s", BrokenMapping(a=1))
    capture.logger.error("half %s", Half([e]))
    text = capture.text.getvalue()
    assert "hello [1]" in text and "mapping {'a': 1}" in text, text
    assert "internal error" not in text
    assert CANARY not in text and "SQLSTATE 23505" in text


def test_n3_containers_are_not_copied_in_full() -> None:
    """Review N-3 (part): the walk reads at most 1,000 items of a container, lazily."""
    read = 0

    def items() -> Any:
        nonlocal read
        for i in range(10**6):
            read += 1
            yield i

    class Huge(list[int]):
        def __iter__(self) -> Any:
            return items()

    found: list[BaseException] = []
    errors._collect((Huge(),), found)
    assert read <= errors._MAX_COLLECT_ITEMS + 1


def test_m2_multi_line_detail_is_removed_completely(capture: Any) -> None:
    e = pg_error("23505", "duplicate key", detail=f"Key (email)=(l1\n{CANARY}-line2\n{CANARY}-3) already exists.")
    capture.logger.error("x %s" % e)  # noqa: UP031 - preformatted on purpose
    capture.logger.error("wrapped: %s", str(e))
    capture.logger.error("again", exc_info=_raised_in_handler(e, RuntimeError(f"quoting {e}")))
    assert CANARY not in capture.text.getvalue() and CANARY not in capture.json.getvalue()
    assert "Traceback (most recent call last)" in capture.text.getvalue()


def test_m3_inline_single_line_server_text_is_stripped() -> None:
    mark = errors._SERVER_TEXT_MARK
    assert errors._strip_server_text(f"dup key DETAIL:  Key (e)=({CANARY})") == f"dup key {mark}"
    assert errors._strip_server_text(f"a\nDETAIL:  {CANARY}\nHINT:  {CANARY}") == f"a\n{mark}"
    assert errors._strip_server_text(f"x ('dup\\nDETAIL:  {CANARY}')") == f"x ('dup {mark}"
    assert errors._strip_server_text("plain text") == "plain text"


@pytest.mark.parametrize("label", ["DETAIL", "HINT", "CONTEXT", "QUERY"])
@pytest.mark.parametrize("gap", [" ", "\t", "  ", " \t "])
def test_td056_inline_label_with_one_blank_is_stripped(label: str, gap: str) -> None:
    """TD-056 (review F-9 of tabrion-studio #78): a one-line message whose label has a single blank after it."""
    mark = errors._SERVER_TEXT_MARK
    text = f"insert failed {label}:{gap}Key (email)=({CANARY}) already exists."
    assert errors._strip_server_text(text) == f"insert failed {mark}"
    assert errors._strip_server_text(f"x={label}:{gap}{CANARY}") == f"x= {mark}"
    assert errors._strip_server_text(f"('dup {label}:{gap}{CANARY}',)") == f"('dup {mark}"


def test_td056_inline_label_without_a_database_error_object(capture: Any) -> None:
    """TD-056: the filter strips a mid-line label in a record that holds no database exception at all."""
    capture.logger.error("sync failed: duplicate key DETAIL: Key (email)=(%s) already exists.", CANARY)
    capture.logger.error("bulk: HINT: %s and CONTEXT: %s", CANARY, CANARY)
    capture.logger.error({"event": "failed", "text": f"dup DETAIL: ({CANARY})"})
    assert CANARY not in _out(capture)
    assert capture.text.getvalue().splitlines()[0] == f"ERROR sync failed: duplicate key {errors._SERVER_TEXT_MARK}"


@pytest.mark.parametrize(
    "text",
    [
        f"x DETAIL:  \n{CANARY}",
        f"x DETAIL:  \r\n{CANARY}",
        f"x DETAIL:   \n  {CANARY} more",
        f"x HINT:  \n{CANARY}",
    ],
)
def test_td056_two_blanks_with_the_value_on_the_next_line(text: str) -> None:
    """Review F-1 of the TD-056 fix: psycopg's two blanks start a removed part even when the value starts on the next
    line (a multi-line ``DETAIL``), in free text and inside a traceback."""
    assert CANARY not in errors._strip_server_text(text)
    traceback = f'Traceback (most recent call last):\n  File "a", line 1, in f\nE: {text}\n'
    assert CANARY not in errors._strip_server_text(traceback)


@pytest.mark.parametrize(
    "text",
    [
        "plain text",
        "see details: in the guide",
        "Detail: lower case is prose",
        "Hint: set the timeout",
        "context: worker 3",
        "HINTS: plural is another word",
        "MY_DETAIL: a longer identifier",
        "XDETAIL: glued to a word",
        "a label without a value DETAIL:",
        "a label with one blank only HINT: ",
        "a label with a tab only HINT:\t",
        "DETAILED: another word",
        "QUERYING: another word",
    ],
)
def test_td056_harmless_text_is_kept(text: str) -> None:
    """TD-056: the wider inline match does not touch prose or identifiers that merely contain a label word."""
    assert errors._strip_server_text(text) == text


def test_c02_escaped_line_echo_without_another_label_is_stripped() -> None:
    """Security assessment C-02: an escaped ``\\nLINE n:`` statement echo (a ``repr()`` of a syntax or cast error) is
    stripped although no other label is in the text."""
    mark = errors._SERVER_TEXT_MARK
    assert errors._strip_server_text(f"x\\nLINE 1: SELECT '{CANARY}'") == f"x {mark}"
    assert errors._strip_server_text(f"x\\nDETAIL:  a\\nLINE 1: SELECT '{CANARY}'") == f"x {mark}"
    text = f"[InvalidTextRepresentation('bad\\nLINE 1: SELECT \\'{CANARY}\\'::int\\n               ^')]"
    assert errors._strip_server_text(text) == f"[InvalidTextRepresentation('bad {mark}"


def test_c02_line_echo_alone_is_stripped_at_line_start() -> None:
    """C-02: a multi-line message whose only label is psycopg's ``LINE n:`` echo."""
    mark = errors._SERVER_TEXT_MARK
    assert errors._strip_server_text(f"syntax error at or near \"x\"\nLINE 1: INSERT '{CANARY}'\n  ^") == (
        f'syntax error at or near "x"\n{mark}'
    )


@pytest.mark.parametrize("gap", [" ", "\t", "  "])
def test_tb145_mid_line_line_echo_is_stripped(gap: str) -> None:
    """TB-145 (review F-3 of the TD-056 fix): a syntax error joined onto one line keeps no statement text."""
    mark = errors._SERVER_TEXT_MARK
    text = f"syntax error at or near \"x\" LINE 1:{gap}VALUES ('{CANARY}')"
    assert errors._strip_server_text(text) == f'syntax error at or near "x" {mark}'
    assert errors._strip_server_text(f"('bad LINE 12:{gap}{CANARY}',)") == f"('bad {mark}"


def test_c02_line_echo_in_a_log_record_without_a_database_error(capture: Any) -> None:
    """C-02: the filter strips a ``LINE n:`` echo in a record that holds no database exception, also as the
    ``repr`` of a list argument."""
    capture.logger.warning("cast failed: %s", [f"bad\nLINE 1: SELECT '{CANARY}'::int"])
    capture.logger.warning(f"joined: syntax error LINE 3: SELECT '{CANARY}'")
    try:
        raise RuntimeError(f"gave up: bad\nLINE 1: SELECT '{CANARY}'")
    except RuntimeError:
        capture.logger.exception("retry loop")
    assert CANARY not in _out(capture)


@pytest.mark.parametrize(
    "text",
    [
        "PIPELINE 3 failed",
        "PIPELINE 3: failed",
        "OFFLINE 2: mode",
        "line 3: lower case is prose",
        "Line 3: title case is prose",
        "LINE: no number",
        "LINE 3 without a colon",
        "a label without a value LINE 3:",
        "a label with one blank only LINE 3: ",
        "LINES 3: plural is another word",
    ],
)
def test_tb145_harmless_line_text_is_kept(text: str) -> None:
    """TB-145: the ``LINE n:`` label is a whole upper-case word followed by a value, like the other labels."""
    assert errors._strip_server_text(text) == text


def test_tb145_ordinary_text_does_not_reach_the_redaction_path() -> None:
    """The ``LINE n:`` gate is a pattern, not the substring ``LINE ``: ordinary text stays off the slow path."""
    assert not errors._may_hold_server_text("PIPELINE 3 failed, OFFLINE MODE")
    assert errors._may_hold_server_text("x\\nLINE 1: SELECT 1")
    assert errors._may_hold_server_text("x LINE 12: SELECT 1")


def test_m3_group_whose_only_database_error_is_a_member(capture: Any) -> None:
    group = ExceptionGroup("batch", [ValueError("other"), _unique_violation()])
    assert group.__context__ is None
    capture.logger.error("batch failed", exc_info=group)
    assert CANARY not in capture.text.getvalue() and "SQLSTATE 23505" in capture.text.getvalue()
    # Nested deeper than the argument walk reaches (3 levels): only the group-member link of the chain finds the
    # error, so the record names it (``db_sqlstate``).
    nested: Exception = _unique_violation()
    for name in "dcba":
        nested = ExceptionGroup(name, [nested])
    capture.logger.error("nested batch failed", exc_info=nested)
    last = json.loads(capture.json.getvalue().splitlines()[-1])
    assert last["sqlstate"] == "23505" and CANARY not in capture.json.getvalue()


def test_m3_notes_on_a_driver_error_are_dropped(capture: Any) -> None:
    e = _unique_violation()
    e.add_note(f"params={CANARY}")
    capture.logger.error("failed", exc_info=e)
    assert CANARY not in capture.text.getvalue()


def test_m3_mapping_arguments_are_replaced() -> None:
    record = logging.LogRecord("x", logging.ERROR, __file__, 1, "%(e)s", ({"e": _unique_violation()},), None)
    DbErrorRedactionFilter().filter(record)
    assert isinstance(record.args, Mapping) and isinstance(record.args["e"], str)
    assert record.args["e"].startswith("unique violation (SQLSTATE 23505")


# --- Port of Axentra's TB-031 review fixes (axe-tb031-review.md F-1..F-8, D-1..D-3, R2-1) ---------------------------


def _out(capture: Any) -> str:
    return str(capture.text.getvalue() + capture.json.getvalue())


def _retry_loop_error() -> RuntimeError:
    last: Exception | None = None
    for _ in range(2):
        try:
            raise _unique_violation()
        except PgError as exc:
            last = exc
    try:
        raise RuntimeError(f"gave up: {last}") from None
    except RuntimeError as exc:
        return exc


class _Holder(Exception):
    """A wrapper that keeps the error in an attribute and quotes it in ``__str__`` (no ``args``, no chain)."""

    def __init__(self, original: BaseException) -> None:
        super().__init__()
        self.original = original

    def __str__(self) -> str:
        return f"wrap: {self.original}"


def _noted() -> RuntimeError:
    wrapper = RuntimeError("wrap")
    wrapper.add_note(str(_unique_violation()))
    return wrapper


@pytest.mark.parametrize(
    ("make", "held"),
    [
        (_retry_loop_error, False),
        (lambda: RuntimeError(_unique_violation()), True),
        (lambda: RuntimeError("batch failed", [_unique_violation()]), True),
        (_noted, False),
        (lambda: _Holder(_unique_violation()), False),
    ],
    ids=["retry-loop", "error-as-arg", "error-in-list-arg", "note", "holder"],
)
def test_f1_traceback_of_a_non_database_exception_with_database_text(capture: Any, make: Any, held: bool) -> None:
    """Axentra F-1: the database text is in the exception's message, ``args`` or notes, not in its chain. An error
    object held in ``args`` is found as such (the record names it); text alone is stripped."""
    exc = make()
    capture.logger.error("job failed", exc_info=(type(exc), exc, exc.__traceback__))
    assert CANARY not in _out(capture)
    assert json.loads(capture.json.getvalue())["sqlstate"] == ("23505" if held else None)
    assert "Traceback" in capture.text.getvalue() or type(exc).__name__ in capture.text.getvalue()


def test_f2_broken_call_while_a_database_error_is_handled(capture: Any, capsys: pytest.CaptureFixture[str]) -> None:
    """Axentra F-2: ``logging`` reports a broken call with the formatting error's chain, whose ``__context__`` is the
    database error being handled; the filter rewrites the call, so nothing reaches ``handleError``."""
    try:
        raise _unique_violation()
    except PgError:
        capture.logger.error("user %s failed with %s", "bob")
    assert CANARY not in _out(capture) + capsys.readouterr().err
    assert "log message could not be formatted" in capture.text.getvalue()
    assert "SQLSTATE 23505" in capture.text.getvalue()  # the handled error is named by its safe message


@pytest.mark.parametrize(
    ("template", "args"),
    [
        ("user %s failed with %s", "text"),
        ("user %(a)s %(b)s", "mapping"),
        ("rows: %d", "text"),
    ],
    ids=["too-few-arguments", "missing-key", "wrong-type"],
)
def test_t1_broken_call_with_database_text_in_an_argument(
    capture: Any, capsys: pytest.CaptureFixture[str], template: str, args: str
) -> None:
    """Review T-1: ``logging``'s error report prints ``Arguments: %r``; server text in an argument is rewritten too,
    also outside an ``except`` block."""
    text = str(_unique_violation())
    if args == "mapping":
        capture.logger.error(template, {"a": text})
    else:
        capture.logger.error(template, text)
    assert CANARY not in _out(capture) + capsys.readouterr().err
    assert "log message could not be formatted" in capture.text.getvalue()


def test_td1_broken_call_with_database_text_in_the_template(capture: Any, capsys: pytest.CaptureFixture[str]) -> None:
    """Delta re-review TD-1: the template itself holds the server text (``logging`` prints it as ``Message: %r``)."""
    capture.logger.error(str(_unique_violation()) + " %s %s", 1)
    assert CANARY not in _out(capture) + capsys.readouterr().err
    assert "log message could not be formatted" in capture.text.getvalue()


def test_t2_broken_call_while_database_text_is_handled(capture: Any, capsys: pytest.CaptureFixture[str]) -> None:
    """Review T-2: the handled exception carries only the database text (no error object): still rewritten."""
    try:
        raise RuntimeError(f"gave up: {_unique_violation()}")
    except RuntimeError:
        capture.logger.error("user %s failed with %s", "bob")
    assert CANARY not in _out(capture) + capsys.readouterr().err
    assert "log message could not be formatted" in capture.text.getvalue()


def test_f3_long_run_of_blanks_is_linear() -> None:
    """Axentra F-3: a long run of blanks before an inline label is no longer scanned quadratically."""
    text = "a" + " " * 50_000 + " DETAIL:"  # a label without a value: no inline match, the worst case
    started = time.perf_counter()
    assert errors._strip_server_text(text) == text
    assert time.perf_counter() - started < 0.5  # quadratic: about 10 s
    text = "a" + " DETAIL:" + " " * 50_000  # blanks after the label but no value (TD-056): linear as well
    started = time.perf_counter()
    assert errors._strip_server_text(text) == "a " + errors._SERVER_TEXT_MARK  # psycopg's two blanks count
    assert errors._strip_server_text("a DETAIL:" + "\t" * 50_000) == "a DETAIL:" + "\t" * 50_000
    assert time.perf_counter() - started < 0.5
    assert errors._strip_server_text("a" + " " * 50_000 + " DETAIL:  x") == "a " + errors._SERVER_TEXT_MARK
    assert errors._strip_server_text("value \t DETAIL:  secret") == "value " + errors._SERVER_TEXT_MARK


def test_c02_line_label_scan_is_linear() -> None:
    """C-02/TB-145 keep the TD-056 property: a long input around a ``LINE n:`` label stays fast."""
    started = time.perf_counter()
    text = "a" + " " * 50_000 + " LINE 1:"  # a label without a value: no inline match
    assert errors._strip_server_text(text) == text
    text = "a LINE 1:" + "\t" * 50_000  # tabs after the label but no value
    assert errors._strip_server_text(text) == text
    text = "a LINE " + "1" * 50_000  # a long number and no colon: the gate fails once, linearly
    assert errors._strip_server_text(text) == text
    text = "LINE 1" * 20_000  # many label starts, none complete
    assert errors._strip_server_text(text) == text
    assert errors._strip_server_text("a" + " " * 50_000 + " LINE 1: x") == "a " + errors._SERVER_TEXT_MARK
    assert errors._strip_server_text("x\\n" * 20_000 + "LINE 1: y") == "x\\n" * 19_999 + "x " + errors._SERVER_TEXT_MARK
    assert time.perf_counter() - started < 0.5


@pytest.mark.parametrize("line", ["+------+", "+", "| +---", 'File "a", line 1', "Traceback (most recent call last):"])
def test_f4_structure_lines_end_a_removal_only_inside_a_traceback(capture: Any, line: str) -> None:
    """Axentra F-4: in free text a value line that looks like traceback structure no longer ends the removal."""
    e = pg_error("23514", "check violation", detail=f"Failing row contains (a\n{line}\n{CANARY}).")
    capture.logger.error("x %s" % e)  # noqa: UP031 - preformatted on purpose
    capture.logger.error("stored: %s", errors._strip_server_text(str(e)))
    assert CANARY not in _out(capture)


def test_f4_a_traceback_keeps_its_structure_after_a_removed_part(capture: Any) -> None:
    exc = RuntimeError(f"gave up\nDETAIL:  {CANARY}")
    try:
        raise exc
    except RuntimeError:
        capture.logger.error("x", exc_info=True)
    text = capture.text.getvalue()
    assert CANARY not in text and errors._SERVER_TEXT_MARK in text and 'File "' in text


def test_f5_mapping_message_stays_a_mapping(capture: Any) -> None:
    """Axentra F-5: structlog's ``ProcessorFormatter`` copies a mapping ``msg``; it must stay a mapping."""
    seen: list[logging.LogRecord] = []

    class Keep(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            seen.append(record)

    capture.logger.addHandler(Keep())
    e = _unique_violation()
    capture.logger.error({"event": "failed", "error": e, "text": str(e), "items": [e], "n": 1})
    msg = seen[-1].msg
    assert isinstance(msg, dict) and msg["event"] == "failed" and msg["n"] == 1
    assert msg["error"] == safe_message(e) and CANARY not in str(msg) and CANARY not in _out(capture)
    assert seen[-1].db_sqlstate == "23505"  # type: ignore[attr-defined]
    plain = {"event": "fine"}
    capture.logger.error(plain)
    assert seen[-1].msg is plain


def test_f7_removed_text_is_marked(capture: Any) -> None:
    """Axentra F-7: a value with a label cuts the rest of the message, visibly."""
    capture.logger.error("login failed user=%s ip=%s", "mallory  DETAIL:  ", "203.0.113.9")
    assert capture.text.getvalue().strip() == f"ERROR login failed user=mallory {errors._SERVER_TEXT_MARK}"


def test_f8_diag_value_is_replaced_when_the_label_was_rewritten(capture: Any) -> None:
    """Axentra F-8: the ``DETAIL`` value itself is replaced when only its label survived a rewrite."""
    e = _unique_violation()
    capture.logger.error("x %s / %s", e, " ".join(str(e).split()))
    # Joined onto one line, the label keeps one blank: the inline label search removes the value (TD-056).
    assert CANARY not in _out(capture) and errors._SERVER_TEXT_MARK in capture.text.getvalue()
    # The label rewritten to ``Detail -`` survives the inline label search (TD-056), so only the diag value
    # replacement removes the value.
    capture.logger.error("y %s / %s", e, " ".join(str(e).replace("DETAIL:", "Detail -").split()))
    assert CANARY not in _out(capture) and "[redacted]" in capture.text.getvalue()


def test_f8_string_args_of_an_error_are_replaced() -> None:
    """Axentra F-8: an error whose ``args`` text differs from its ``str``, quoted with its label rewritten."""

    quiet_class = type(
        PgIntegrityError.__name__, (PgIntegrityError,), {"__str__": lambda self: "quiet", "__module__": "psycopg"}
    )
    quiet = quiet_class(f"dup\nDetail -  Key (e)=({CANARY})")
    quiet.sqlstate = "23505"
    quiet.diag = SimpleNamespace(message_detail=None, message_hint=None, context=None)
    scrubbed = errors._scrub(f"held: {quiet.args[0]}", [quiet])
    assert CANARY not in scrubbed and "SQLSTATE 23505" in scrubbed


def test_d1_mapping_message_with_exc_info_is_redacted(capture: Any) -> None:
    """Axentra D-1: the mapping branch runs after the traceback is redacted."""
    try:
        raise _unique_violation()
    except PgError:
        capture.logger.error({"event": "failed"}, exc_info=True)
    try:
        raise RuntimeError("boom") from _unique_violation()
    except RuntimeError:
        capture.logger.exception({"event": "boom", "n": 1})
    assert CANARY not in _out(capture) and "SQLSTATE 23505" in capture.text.getvalue()


def test_d2_exc_info_inside_an_event_dict(capture: Any) -> None:
    """Axentra D-2: structlog's stdlib integration puts ``exc_info`` into the event dict and renders it itself."""
    seen: list[logging.LogRecord] = []

    class Keep(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            seen.append(record)

    capture.logger.addHandler(Keep())
    e = _unique_violation()
    for value in (RuntimeError(e), (type(e), e, None), e):
        capture.logger.error({"event": "x", "exc_info": value})
        msg = seen[-1].msg
        assert isinstance(msg, dict) and "exc_info" not in msg and CANARY not in msg["exception"]
    try:
        raise e
    except PgError:
        capture.logger.error({"event": "x", "exc_info": True})
    assert "exc_info" not in seen[-1].msg and CANARY not in str(seen[-1].msg)
    capture.logger.error({"event": "x", "exc_info": True})  # nothing handled: left as it is
    assert seen[-1].msg == {"event": "x", "exc_info": True}


def test_d2_processor_redacts_an_event_dict() -> None:
    e = _unique_violation()
    try:
        raise RuntimeError("wrap") from e
    except RuntimeError:
        out = errors._redact_db_errors_processor(None, "error", {"event": "boom", "exc_info": True, "err": [e]})
    assert out["event"] == "boom" and "exc_info" not in out and "SQLSTATE 23505" in out["exception"]
    assert CANARY not in str(out)
    for odd in (None, ["a"], "text", 3):
        assert errors._redact_db_errors_processor(None, "error", odd) is odd


def test_r2_1_processor_fallback_keeps_only_the_event() -> None:
    """Axentra re-review R2-1: when database text survives scrubbing (here in a key), only ``event`` is returned."""
    e = _unique_violation()
    out = errors._redact_db_errors_processor(None, "error", {"event": "x", str(e): 1, "other": "kept?"})
    assert out == {"event": "x"}

    class BadRepr:
        def __repr__(self) -> str:
            raise RuntimeError("repr")

    out = errors._redact_db_errors_processor(None, "error", {"event": f"y DETAIL:  {CANARY}", "v": BadRepr()})
    assert list(out) == ["event"] and CANARY not in str(out)


def test_d3_exception_whose_args_raise_keeps_the_message(capture: Any) -> None:
    """Axentra D-3: attribute access on the exception is guarded, so the message is not replaced."""

    class BadArgs(Exception):
        @property
        def args(self) -> Any:  # type: ignore[override]
            raise RuntimeError("no args")

    try:
        raise BadArgs()
    except BadArgs:
        capture.logger.error("payment %s failed", "ord-1", exc_info=True)
    assert capture.text.getvalue().startswith("ERROR payment ord-1 failed")
