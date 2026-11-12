"""Actions of the LDP vectors that need no PostgreSQL server (SPEC.md appendix A; engines ``sqlite`` and ``any``).

Each handler runs the action against Tabrion and returns the observed values for the keys of ``expect``; the test
compares them. The PostgreSQL actions live in ``tests/postgres/test_conformance_vectors.py`` (they need the fixtures
of that suite).
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import closing
import io
import logging
import os
from pathlib import Path
import sqlite3
import stat
import threading
from typing import Any

import pytest

from tabrion import sqlite as tsqlite
from tabrion.errors import DbErrorRedactionFilter, TabrionError, classify, safe_message
from tabrion.sql import split_statements
from tests.support.conformance import Vector

Observed = dict[str, Any]
Handler = Callable[[Vector, Path], Observed]

PROFILES: dict[str, Callable[[], tsqlite.SQLiteProfile]] = {
    "default": tsqlite.SQLiteProfile,
    "orkexa": tsqlite.SQLiteProfile.orkexa,
    "nodestro": tsqlite.SQLiteProfile.nodestro,
}


def _mode(path: Path) -> str:
    return f"{stat.S_IMODE(os.stat(path).st_mode):04o}"


def _pragmas(connection: sqlite3.Connection) -> Observed:
    d = tsqlite.diagnose(connection)
    return {
        "journal_mode": d.journal_mode,
        "foreign_keys": d.foreign_keys,
        "busy_timeout_ms": d.busy_timeout_ms,
        "synchronous": d.synchronous,
        "secure_delete": d.secure_delete,
        "trusted_schema": d.trusted_schema,
    }


def _min_check(observed: Observed, expect: dict[str, Any]) -> Observed:
    """``busy_timeout_ms_min``: report the expected minimum when it holds, the real value when not."""
    if "busy_timeout_ms_min" in expect:
        value = observed["busy_timeout_ms"]
        observed["busy_timeout_ms_min"] = (
            expect["busy_timeout_ms_min"] if value >= expect["busy_timeout_ms_min"] else value
        )
    return observed


def _posix_only(vector: Vector) -> None:
    """File modes and symlinks hold on POSIX only (SPEC appendix A; as ``posix_only`` of TAB-T-S12)."""
    if os.name != "posix":
        pytest.skip(f"{vector.id}: POSIX only (file modes and symlinks; Windows has neither 0600 nor plain symlinks)")


def open_new_file(vector: Vector, tmp: Path) -> Observed:
    args = vector.args
    if "symlink" in args or "file_mode" in vector.expect:
        _posix_only(vector)
    path = tmp / "ldp.db"
    side = args.get("symlink")
    if side is not None:
        target = tmp / "elsewhere"
        if args.get("target_exists"):
            target.write_bytes(b"")
        link = path if side == "main" else Path(f"{path}{side}")
        link.symlink_to(target)
        try:
            tsqlite.connect(path, profile=PROFILES[args["profile"]]()).close()
        except TabrionError as exc:
            return {"error": exc.code}
        return {"error": None}
    connection = tsqlite.connect(path, profile=PROFILES[args["profile"]]())
    try:
        observed = {"file_mode": _mode(path), **_pragmas(connection)}
    finally:
        connection.close()
    return _min_check(observed, vector.expect)


def open_existing(vector: Vector, tmp: Path) -> Observed:
    _posix_only(vector)
    path = tmp / "ldp.db"
    sqlite3.connect(path).close()
    os.chmod(path, int(vector.args["mode"], 8))
    connection = tsqlite.connect(path, profile=PROFILES[vector.args["profile"]]())
    try:
        return {"file_mode": _mode(path), **_pragmas(connection)}
    finally:
        connection.close()


def inspect_pragmas(vector: Vector, tmp: Path) -> Observed:
    target: str | Path = ":memory:" if vector.args.get("memory") else tmp / "ldp.db"
    connection = tsqlite.connect(target, profile=PROFILES[vector.args["profile"]]())
    try:
        return _min_check(_pragmas(connection), vector.expect)
    finally:
        connection.close()


def concurrent_read_modify_write(vector: Vector, tmp: Path) -> Observed:
    path = tmp / "ldp.db"
    with closing(tsqlite.connect(path)) as setup:
        setup.execute("CREATE TABLE counter (n INTEGER NOT NULL)")
        setup.execute("INSERT INTO counter VALUES (0)")
        setup.commit()
    errors: list[BaseException] = []
    barrier = threading.Barrier(vector.args["threads"])

    def work() -> None:
        connection = tsqlite.connect(path)
        try:
            barrier.wait()
            with tsqlite.write_transaction(connection) as tx:
                (value,) = tx.execute("SELECT n FROM counter").fetchone()
                tx.execute("UPDATE counter SET n = ?", (value + 1,))
        except BaseException as exc:  # counted and reported by the vector
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=work) for _ in range(vector.args["threads"])]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    with closing(tsqlite.connect(path)) as check:
        (final,) = check.execute("SELECT n FROM counter").fetchone()
    return {"final": final, "lock_errors": len(errors)}


def write_in_read_transaction(vector: Vector, tmp: Path) -> Observed:
    path = tmp / "ldp.db"
    connection = tsqlite.connect(path)
    try:
        with tsqlite.write_transaction(connection) as tx:
            for statement in vector.setup:
                tx.execute(statement)
        kind: str | None = None
        try:
            with tsqlite.read_transaction(connection) as tx:
                tx.execute(vector.args["statement"])
        except sqlite3.Error as exc:
            kind = classify(exc).kind.value
        (rows,) = connection.execute("SELECT count(*) FROM t").fetchone()
        return {"kind": kind, "rows": rows}
    finally:
        connection.close()


def migrate(vector: Vector, tmp: Path) -> Observed:
    args = vector.args
    path = tmp / "ldp.db"
    connection = tsqlite.connect(path)
    try:
        for statement in vector.setup:
            connection.execute(statement)
        connection.execute(f"PRAGMA user_version = {int(args['initial_version'])}")
        connection.commit()

        def step(statements: list[str]) -> Callable[[sqlite3.Connection], None]:
            def run(c: sqlite3.Connection) -> None:
                for statement in statements:
                    c.execute(statement)

            return run

        steps = {int(k): step(v) for k, v in args["steps"].items()}
        backups: list[tuple[int, int]] = []

        def backup(previous: int, target: int) -> None:
            if args["backup"] == "fail":
                raise OSError("LDP: backup failed on purpose")
            backups.append((previous, target))

        observed: Observed = {"error": None}
        if args.get("in_transaction"):
            connection.execute("BEGIN")
        try:
            result = tsqlite.migrate(
                connection, steps, target=args["target"], backup=None if args["backup"] == "none" else backup
            )
            observed["backed_up"] = result.backed_up
        except TabrionError as exc:
            observed["error"] = exc.code
        observed["in_transaction_after"] = connection.in_transaction
        if connection.in_transaction:
            connection.rollback()
        observed["user_version"] = connection.execute("PRAGMA user_version").fetchone()[0]
        observed["tables"] = sorted(
            r[0]
            for r in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite%'"
            )
        )
        return observed
    finally:
        connection.close()


def _expect_error(vector: Vector, action: Callable[[], object]) -> sqlite3.Error:
    try:
        action()
    except sqlite3.Error as exc:
        return exc
    raise AssertionError(f"{vector.id}: the scenario did not fail")


def _sqlite_scenario(vector: Vector, tmp: Path) -> sqlite3.Error:
    """A real SQLite error that needs more than one statement on a memory database (SPEC appendix A)."""
    scenario = vector.args["scenario"]
    path = tmp / "ldp.db"
    if scenario == "busy":  # another connection holds the write lock; no busy wait
        with closing(sqlite3.connect(path, isolation_level=None)) as holder:
            holder.execute("CREATE TABLE t (a INTEGER)")
            holder.execute("BEGIN IMMEDIATE")
            with closing(sqlite3.connect(path, timeout=0, isolation_level=None)) as other:
                error = _expect_error(vector, lambda: other.execute("BEGIN IMMEDIATE"))
            holder.execute("ROLLBACK")
        return error
    if scenario == "readonly":
        with closing(sqlite3.connect(path)) as setup:
            setup.execute("CREATE TABLE t (a INTEGER)")
        with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as reader:
            return _expect_error(vector, lambda: reader.execute("INSERT INTO t VALUES (1)"))
    if scenario == "notadb":
        path.write_bytes(b"LDP: not a database file. " * 200)
        with closing(sqlite3.connect(path)) as broken:
            return _expect_error(vector, lambda: broken.execute("SELECT * FROM sqlite_master").fetchall())
    if scenario == "cantopen":
        return _expect_error(
            vector, lambda: sqlite3.connect(f"{(tmp / 'missing' / 'x.db').as_uri()}?mode=rw", uri=True)
        )
    with closing(sqlite3.connect(":memory:")) as connection:
        if scenario == "full":
            connection.execute("CREATE TABLE t (a BLOB)")
            connection.execute("PRAGMA max_page_count = 3")
            return _expect_error(vector, lambda: connection.execute("INSERT INTO t VALUES (randomblob(100000))"))
        if scenario == "interrupt":
            connection.set_progress_handler(lambda: 1, 1)
            return _expect_error(vector, lambda: connection.execute("SELECT 1 UNION SELECT 2").fetchall())
        if scenario == "auth":
            connection.execute("CREATE TABLE t (a INTEGER)")
            connection.set_authorizer(lambda *_: sqlite3.SQLITE_DENY)
            return _expect_error(vector, lambda: connection.execute("SELECT * FROM t"))
    raise AssertionError(f"{vector.id}: unknown scenario {scenario!r}")


def _error(vector: Vector, tmp: Path) -> BaseException:
    """The driver error of a ``classify_error``/``redact_error`` vector: a real one, never a stand-in."""
    args = vector.args
    if args["driver"] == "psycopg":
        import psycopg.errors

        text = args.get("message", "")
        if args.get("detail"):
            text += f"\nDETAIL:  {args['detail']}"
        return psycopg.errors.lookup(args["sqlstate"])(text)
    if "scenario" in args:
        return _sqlite_scenario(vector, tmp)
    connection = sqlite3.connect(":memory:")
    try:
        for statement in vector.setup:
            connection.execute(statement)
        try:
            connection.execute(args["statement"], tuple(args.get("params", ())))
        except sqlite3.Error as exc:
            return exc
    finally:
        connection.close()
    raise AssertionError(f"{vector.id}: the statement did not fail")


def classify_error(vector: Vector, tmp: Path) -> Observed:
    c = classify(
        _error(vector, tmp), sent=vector.args.get("sent", True), during_commit=vector.args.get("during_commit", False)
    )
    return {
        "kind": c.kind.value,
        "retryable": c.retryable,
        "outcome_unknown": c.outcome_unknown,
        "connection_usable": c.connection_usable,
        "driver": c.driver,
        "sqlstate": c.sqlstate,
        "sqlite_code": c.sqlite_code,
    }


def redact_error(vector: Vector, tmp: Path) -> Observed:
    error = _error(vector, tmp)
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.addFilter(DbErrorRedactionFilter())
    logger = logging.getLogger(f"tabrion.ldp.{vector.id}")
    logger.propagate = False
    logger.addHandler(handler)
    try:
        logger.error("operation failed: %s (%r)", error, error, exc_info=error)
    finally:
        logger.removeHandler(handler)
    texts = safe_message(error) + "\n" + buffer.getvalue()
    return {
        "absent": [t for t in vector.expect.get("absent", ()) if t not in texts],
        "contains": [t for t in vector.expect.get("contains", ()) if t in texts],
    }


def split_sql(vector: Vector, tmp: Path) -> Observed:
    return {"statements": split_statements(vector.args["sql"])}


HANDLERS: dict[str, Handler] = {
    "open_new_file": open_new_file,
    "open_existing": open_existing,
    "inspect_pragmas": inspect_pragmas,
    "concurrent_read_modify_write": concurrent_read_modify_write,
    "write_in_read_transaction": write_in_read_transaction,
    "migrate": migrate,
    "classify_error": classify_error,
    "redact_error": redact_error,
    "split_sql": split_sql,
}
