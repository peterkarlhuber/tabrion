"""Profile and portability test cases for new applications (``tabrion.testing.profiles``).

Contract section 6 and implementation note I-58; implemented in TAB-021. The case types are not specified in doc
04; G-T1 rejected the first proposal (a zero-argument connection factory, G-3) and condition C-2 fixed this shape
(``docs/api/contract.md``, "Open points"):

* ``sqlite_profile_cases()`` tests file mode and symlink refusal of *new* files, so each case chooses the path and
  calls the application's ``SQLiteConnect`` with it.
* ``portability_cases()`` must run on both engines (spec section 6), so each case takes a layer-B ``Database``.

A case raises ``AssertionError`` on a violation, also under ``python -O`` (no ``assert`` statements).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
import contextlib
from dataclasses import dataclass
import datetime as dt
from decimal import Decimal
import math
import os
from pathlib import Path
import sqlite3
import stat
from typing import TYPE_CHECKING, Any
import uuid

if TYPE_CHECKING:
    import sqlalchemy

    from tabrion._sa.database import Database

__all__ = ["PortabilityCase", "ProfileCase", "SQLiteConnect", "portability_cases", "sqlite_profile_cases"]

SQLiteConnect = Callable[[Path], sqlite3.Connection]
"""Opens a connection to the given path under the application's own settings (e.g. its wrapper of
``tabrion.sqlite.connect``)."""


@dataclass(frozen=True, slots=True)
class ProfileCase:
    id: str
    """Test ID, e.g. ``TAB-T-S01``."""
    title: str
    check: Callable[[SQLiteConnect, Path], None]
    """Called with the application's connect function and a fresh, empty directory; the case picks the file path
    inside it. Raises ``AssertionError`` when the connect function violates the rule."""


@dataclass(frozen=True, slots=True)
class PortabilityCase:
    id: str
    """Test ID, e.g. ``TAB-T-D01``."""
    title: str
    check: Callable[[Database], None]
    """Called with an open ``Database`` on an empty schema, SQLite or PostgreSQL. Raises ``AssertionError`` when
    the engine breaks a data type rule."""


_MIN_BUSY_TIMEOUT_MS = 1_000
_SIDE_FILES = ("-wal", "-shm", "-journal")


def _expect(condition: object, message: str) -> None:
    """``assert`` that also runs under ``python -O``: a case raises ``AssertionError`` on a violation."""
    if not condition:
        raise AssertionError(message)


def _close(connection: object) -> None:
    close = getattr(connection, "close", None)
    if callable(close):
        close()


@contextlib.contextmanager
def _opened(connect: SQLiteConnect, path: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(path)
    _expect(isinstance(connection, sqlite3.Connection), "the connect function must return a sqlite3.Connection")
    try:
        yield connection
    finally:
        connection.close()


def _pragma(connection: sqlite3.Connection, name: str) -> object:
    cursor = connection.cursor()
    try:
        cursor.row_factory = None
        row = cursor.execute(f"PRAGMA {name}").fetchone()
    finally:
        cursor.close()
    return None if row is None else row[0]


def _write(connection: sqlite3.Connection) -> None:
    """One committed write, whatever the connection's transaction mode."""
    connection.execute("CREATE TABLE IF NOT EXISTS tabrion_case (n INTEGER)")
    connection.execute("INSERT INTO tabrion_case (n) VALUES (1)")
    if connection.in_transaction:
        connection.commit()


def _f01_foreign_keys(connect: SQLiteConnect, directory: Path) -> None:
    with _opened(connect, directory / "f01.db") as connection:
        _expect(_pragma(connection, "foreign_keys") == 1, "PRAGMA foreign_keys is not ON")


def _f02_wal(connect: SQLiteConnect, directory: Path) -> None:
    with _opened(connect, directory / "f02.db") as connection:
        mode = str(_pragma(connection, "journal_mode")).lower()
        _expect(mode == "wal", f"journal_mode is {mode}, not wal")


def _f03_busy_timeout(connect: SQLiteConnect, directory: Path) -> None:
    with _opened(connect, directory / "f03.db") as connection:
        timeout = _pragma(connection, "busy_timeout")
        _expect(isinstance(timeout, int) and timeout >= _MIN_BUSY_TIMEOUT_MS, f"busy_timeout is {timeout} ms")


def _f04_file_mode(connect: SQLiteConnect, directory: Path) -> None:
    if os.name != "posix":
        return  # POSIX modes say nothing on Windows (contract I-30)
    path = directory / "f04.db"
    previous = os.umask(0o022)
    try:
        with _opened(connect, path) as connection:
            _write(connection)
            names = [path, *(Path(f"{path}{suffix}") for suffix in _SIDE_FILES)]
            for name in names:
                if name.exists():
                    mode = stat.S_IMODE(os.lstat(name).st_mode)
                    _expect(mode & 0o077 == 0, f"{name.name} has mode {mode:o}, not 0600")
    finally:
        os.umask(previous)


def _f05_symlinks(connect: SQLiteConnect, directory: Path) -> None:
    target = directory / "f05-target.db"
    link = directory / "f05-link.db"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        return  # no symlinks here (Windows without the privilege)
    _refused(connect, link, "a symlinked database path")
    _expect(not target.exists(), "the connect function created the symlink's target")
    path = directory / "f05.db"
    with _opened(connect, path) as connection:
        _write(connection)
    for suffix in _SIDE_FILES:
        side = Path(f"{path}{suffix}")
        if side.exists() or side.is_symlink():
            side.unlink()
        side.symlink_to(directory / f"f05-elsewhere{suffix}")
        _refused(connect, path, f"a symlinked {suffix} file")
        side.unlink()


def _refused(connect: SQLiteConnect, path: Path, what: str) -> None:
    try:
        connection = connect(path)
    except Exception:
        return
    _close(connection)
    raise AssertionError(f"the connect function accepted {what}")


def _f06_immediate(connect: SQLiteConnect, directory: Path) -> None:
    with _opened(connect, directory / "f06.db") as connection:
        # No implicit DEFERRED transaction: sqlite3 must not open BEGIN (deferred) before DML on its own, so the
        # application begins writes with BEGIN IMMEDIATE (tabrion.sqlite.write_transaction).
        _expect(
            connection.isolation_level in (None, "IMMEDIATE", "EXCLUSIVE"),
            f"isolation_level {connection.isolation_level!r} begins writes DEFERRED",
        )
        _expect(getattr(connection, "autocommit", -1) is not False, "autocommit=False begins writes DEFERRED")


def sqlite_profile_cases() -> list[ProfileCase]:
    """FK, WAL, busy timeout, permissions, symlinks and IMMEDIATE for any SQLite connect function."""
    return [
        ProfileCase("TAB-T-F01", "foreign_keys is ON", _f01_foreign_keys),
        ProfileCase("TAB-T-F02", "a file database uses WAL", _f02_wal),
        ProfileCase("TAB-T-F03", "busy_timeout is at least 1000 ms", _f03_busy_timeout),
        ProfileCase("TAB-T-F04", "a new file and its side files are 0600 despite umask 022 (POSIX)", _f04_file_mode),
        ProfileCase("TAB-T-F05", "a symlinked main, -wal, -shm or -journal file is refused", _f05_symlinks),
        ProfileCase("TAB-T-F06", "writes do not begin DEFERRED", _f06_immediate),
    ]


# Portability cases (specification section 6). Each case creates its own tables (prefix tabrion_d0N), checks the
# rule with the column mapping of the specification and drops its tables again.


def _sa() -> Any:
    import sqlalchemy

    return sqlalchemy


@contextlib.contextmanager
def _tables(database: Database, *tables: sqlalchemy.Table) -> Iterator[None]:
    metadata = tables[0].metadata
    with database.transaction() as transaction:
        metadata.create_all(transaction.connection, tables=list(tables))
    try:
        yield
    finally:
        with database.transaction() as transaction:
            metadata.drop_all(transaction.connection, tables=list(tables))


def _rejected(database: Database, statement: Any, params: dict[str, Any] | None = None, *, what: str) -> None:
    from tabrion.errors import DatabaseError

    try:
        database.execute(statement, params)
    except DatabaseError:
        return
    raise AssertionError(f"the engine accepted {what}")


def _sqlite(database: Database) -> bool:
    return database.dialect == "sqlite"


def _d01_timestamps(database: Database) -> None:
    sa = _sa()
    column = sa.String(32) if _sqlite(database) else sa.DateTime(timezone=True)
    table = sa.Table(
        "tabrion_d01", sa.MetaData(), sa.Column("id", sa.Integer, primary_key=True), sa.Column("at", column)
    )
    early = dt.datetime(2024, 1, 2, 3, 4, 5, 678901, tzinfo=dt.UTC)
    late = dt.datetime(2024, 1, 2, 23, 0, 0, tzinfo=dt.timezone(dt.timedelta(hours=-5)))  # 04:00 UTC next day
    with _tables(database, table):
        if _sqlite(database):
            values = [v.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ") for v in (late, early)]
            database.execute(table.insert(), [{"id": 1, "at": values[0]}, {"id": 2, "at": values[1]}])
            rows = database.fetch_all(sa.select(table.c.at).order_by(table.c.at))
            _expect([r[0] for r in rows] == ["2024-01-02T03:04:05.678901Z", "2024-01-03T04:00:00.000000Z"], repr(rows))
            parsed = dt.datetime.fromisoformat(rows[0][0].replace("Z", "+00:00"))
            _expect(parsed == early and parsed.utcoffset() == dt.timedelta(0), "the text does not parse back as UTC")
        else:
            database.execute(table.insert(), [{"id": 1, "at": late}, {"id": 2, "at": early}])
            rows = database.fetch_all(sa.select(table.c.at).order_by(table.c.at))
            _expect([r[0] for r in rows] == [early, late], repr(rows))
            _expect(all(r[0].utcoffset() == dt.timedelta(0) for r in rows), "session TimeZone is not UTC")


def _d02_decimal(database: Database) -> None:
    sa = _sa()
    amount = sa.String(40) if _sqlite(database) else sa.Numeric(20, 2, asdecimal=True)
    table = sa.Table(
        "tabrion_d02",
        sa.MetaData(),
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("amount", amount),
        sa.Column("cents", sa.BigInteger),
    )
    exact = Decimal("123456789012345678.91")
    with _tables(database, table):
        rows = [{"id": i, "amount": str(Decimal("0.10")), "cents": 10} for i in (1, 2, 3)]
        rows.append({"id": 4, "amount": str(exact), "cents": 0})
        database.execute(table.insert(), rows)
        stored = database.fetch_scalar(sa.select(table.c.amount).where(table.c.id == 4))
        _expect(Decimal(str(stored)) == exact, f"{stored!r} is not exact")
        _expect(database.fetch_scalar(sa.select(sa.func.sum(table.c.cents))) == 30, "the sum of cents is not exact")
        if not _sqlite(database):
            total = database.fetch_scalar(sa.select(sa.func.sum(table.c.amount)).where(table.c.id < 4))
            _expect(total == Decimal("0.30"), repr(total))


def _d03_integer_ids(database: Database) -> None:
    sa = _sa()
    table = sa.Table(
        "tabrion_d03",
        sa.MetaData(),
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer, "sqlite"), sa.Identity(), primary_key=True),
        sa.Column("label", sa.String(10)),
    )
    with _tables(database, table):
        database.execute(table.insert(), [{"label": "a"}, {"label": "b"}])
        ids = [r[0] for r in database.fetch_all(sa.select(table.c.id).order_by(table.c.id))]
        _expect(len(ids) == 2 and all(type(i) is int for i in ids) and ids[0] < ids[1], repr(ids))
        if _sqlite(database):
            aliased = database.fetch_scalar(sa.text("SELECT count(*) FROM tabrion_d03 WHERE rowid = id"))
            _expect(aliased == 2, "the id is not an alias of rowid")


def _d04_uuid(database: Database) -> None:
    sa = _sa()
    column = sa.String(36) if _sqlite(database) else sa.Uuid(as_uuid=True)
    table = sa.Table(
        "tabrion_d04", sa.MetaData(), sa.Column("id", sa.Integer, primary_key=True), sa.Column("u", column)
    )
    value = uuid.UUID("12345678-1234-5678-1234-567812345678")
    with _tables(database, table):
        database.execute(table.insert(), {"id": 1, "u": str(value) if _sqlite(database) else value})
        stored = database.fetch_scalar(sa.select(table.c.u))
        if _sqlite(database):
            _expect(isinstance(stored, str) and len(stored) == 36 and uuid.UUID(stored) == value, repr(stored))
        else:
            _expect(stored == value, repr(stored))
            _expect(
                database.fetch_scalar(sa.text("SELECT pg_typeof(u)::text FROM tabrion_d04")) == "uuid",
                "the column is not of type uuid",
            )


def _d05_boolean(database: Database) -> None:
    sa = _sa()
    table = sa.Table(
        "tabrion_d05",
        sa.MetaData(),
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("flag", sa.Boolean(create_constraint=True, name="tabrion_d05_flag")),
    )
    with _tables(database, table):
        database.execute(table.insert(), [{"id": 1, "flag": True}, {"id": 2, "flag": False}])
        _expect(
            [r[0] for r in database.fetch_all(sa.select(table.c.flag).order_by(table.c.id))] == [True, False],
            "the booleans do not round trip",
        )
        _rejected(database, sa.text("INSERT INTO tabrion_d05 (id, flag) VALUES (3, 2)"), what="2 as a boolean")


def _d06_text_length(database: Database) -> None:
    sa = _sa()
    table = sa.Table(
        "tabrion_d06",
        sa.MetaData(),
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(5)),
        sa.CheckConstraint("length(name) <= 5", name="tabrion_d06_name_length"),
    )
    with _tables(database, table):
        database.execute(table.insert(), [{"id": 1, "name": "abcde"}, {"id": 2, "name": "äöüßé"}])
        _rejected(database, table.insert(), {"id": 3, "name": "abcdef"}, what="six characters in String(5)")


def _d07_json(database: Database) -> None:
    sa = _sa()
    from sqlalchemy.dialects import postgresql

    table = sa.Table(
        "tabrion_d07",
        sa.MetaData(),
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("doc", sa.JSON().with_variant(postgresql.JSONB(), "postgresql")),
    )
    document = {"a": [1, 2.5, True, None], "ü": "x", "nested": {"k": "v"}}
    with _tables(database, table):
        database.execute(table.insert(), {"id": 1, "doc": document})
        _expect(database.fetch_scalar(sa.select(table.c.doc)) == document, "the JSON document changed")


def _d08_foreign_keys(database: Database) -> None:
    sa = _sa()
    from tabrion.errors import IntegrityViolation, Kind

    metadata = sa.MetaData()
    parent = sa.Table("tabrion_d08_parent", metadata, sa.Column("id", sa.Integer, primary_key=True))
    child = sa.Table(
        "tabrion_d08_child",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("parent_id", sa.Integer, sa.ForeignKey("tabrion_d08_parent.id"), nullable=False),
    )
    with _tables(database, parent, child):
        database.execute(parent.insert(), {"id": 1})
        database.execute(child.insert(), {"id": 1, "parent_id": 1})
        try:
            database.execute(child.insert(), {"id": 2, "parent_id": 99})
        except IntegrityViolation as exc:
            _expect(exc.kind is Kind.FOREIGN_KEY, repr(exc.kind))
        else:
            raise AssertionError("the engine accepted a row without its parent (foreign_keys off?)")


def _d09_floats(database: Database) -> None:
    sa = _sa()
    table = sa.Table(
        "tabrion_d09",
        sa.MetaData(),
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("value", sa.Double),
        sa.Column("unit", sa.String(16)),
    )
    values = [0.1, -2.5, 1e-300, 1.7976931348623157e308]
    with _tables(database, table):
        database.execute(table.insert(), [{"id": i, "value": v, "unit": "m"} for i, v in enumerate(values)])
        stored = [r[0] for r in database.fetch_all(sa.select(table.c.value).order_by(table.c.id))]
        _expect(stored == values and all(math.isfinite(v) for v in stored), repr(stored))


def portability_cases() -> list[PortabilityCase]:
    """The data type rules of the specification, section 6 (TAB-T-D01..D09), for SQLite and PostgreSQL."""
    return [
        PortabilityCase("TAB-T-D01", "timestamps are UTC (ISO 8601 text with Z, or timestamptz)", _d01_timestamps),
        PortabilityCase("TAB-T-D02", "money as exact Decimal (TEXT or cents on SQLite)", _d02_decimal),
        PortabilityCase("TAB-T-D03", "integer IDs (rowid alias on SQLite, bigint identity)", _d03_integer_ids),
        PortabilityCase("TAB-T-D04", "UUID as 36-character text or uuid", _d04_uuid),
        PortabilityCase("TAB-T-D05", "booleans with a CHECK constraint", _d05_boolean),
        PortabilityCase("TAB-T-D06", "text length enforced by a CHECK constraint", _d06_text_length),
        PortabilityCase("TAB-T-D07", "JSON round trip (TEXT or jsonb)", _d07_json),
        PortabilityCase("TAB-T-D08", "foreign keys are enforced", _d08_foreign_keys),
        PortabilityCase("TAB-T-D09", "finite doubles round trip exactly", _d09_floats),
    ]
