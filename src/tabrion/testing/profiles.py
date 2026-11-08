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
import secrets
import sqlite3
import stat
from typing import TYPE_CHECKING, Any
import uuid

from tabrion.errors import DatabaseError, IntegrityViolation, Kind, UnsafePath

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
    """Called with an open ``Database`` on an empty or dedicated schema, SQLite or PostgreSQL (never one with
    application data). Raises ``AssertionError`` when the engine breaks a data type rule, or when a table name the
    case picked exists already; it drops only the tables it created (contract I-58)."""


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
    """The modes after the connect function returned and a write committed: a file created 0644 and narrowed with
    ``chmod`` right afterwards passes, although others could open it for that moment (review L-3). Creating the
    file 0600 before SQLite opens it, as ``tabrion.sqlite.connect`` does, is the rule."""
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
    _refused(connect, link, "a symlink to a missing database file")
    _expect(not target.exists(), "the connect function created the symlink's target")
    path = directory / "f05.db"
    with _opened(connect, path) as connection:
        _write(connection)
    # Positive control: the plain file reopens, so a refusal below is caused by the link, not by an existing file.
    _reopens(connect, path)
    alias = directory / "f05-alias.db"
    alias.symlink_to(path)
    _refused(connect, alias, "a symlink to an existing database file")
    for suffix in _SIDE_FILES:
        side = Path(f"{path}{suffix}")
        missing = directory / f"f05-missing{suffix}"
        existing = directory / f"f05-existing{suffix}"
        existing.write_bytes(b"")
        for destination, kind in ((missing, "a missing"), (existing, "an existing")):
            _unlink(side)
            _reopens(connect, path)
            _unlink(side)  # closing the last WAL connection removes -wal and -shm; a DELETE journal leaves none
            side.symlink_to(destination)
            _refused(connect, path, f"a {suffix} symlink to {kind} file")
            side.unlink()
        _expect(not missing.exists(), f"the connect function created the target of the {suffix} symlink")


def _unlink(path: Path) -> None:
    if path.exists() or path.is_symlink():
        path.unlink()


def _reopens(connect: SQLiteConnect, path: Path) -> None:
    """Positive control of F05: the existing file, without any link next to it, opens and reads."""
    try:
        with _opened(connect, path) as connection:
            connection.execute("SELECT count(*) FROM tabrion_case").fetchone()
    except AssertionError:
        raise
    except Exception as exc:
        raise AssertionError(
            f"the connect function cannot reopen an existing database file ({type(exc).__name__}); F05 cannot tell"
            " a symlink refusal from that"
        ) from exc


def _refused(connect: SQLiteConnect, path: Path, what: str) -> None:
    """Only ``UnsafePath("sqlite.symlink")`` counts as a refusal (what ``tabrion.sqlite.connect`` raises)."""
    try:
        connection = connect(path)
    except UnsafePath as exc:
        _expect(exc.code == "sqlite.symlink", f"{what} was refused with {exc.code}, not sqlite.symlink")
        return
    except Exception as exc:
        raise AssertionError(f"{what} was refused with {type(exc).__name__}, not UnsafePath(sqlite.symlink)") from exc
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


# Portability cases (specification section 6). Each call creates its own tables under fresh names
# (tabrion_d0N_<random hex>) with CREATE TABLE without IF NOT EXISTS, checks the rule with the column mapping of the
# specification and drops exactly the tables it created. A name that exists already makes the case fail without
# touching that table. The mappings are small functions of their own, so the tests can break each one (TAB-021 M-3).


def _sa() -> Any:
    import sqlalchemy

    return sqlalchemy


def _suffix() -> str:
    """Random per call, so two runs in one schema never meet (review M-2)."""
    return secrets.token_hex(4)


@contextlib.contextmanager
def _tables(database: Database, *tables: sqlalchemy.Table) -> Iterator[None]:
    """Create ``tables`` in one transaction (all or none), yield, then drop exactly those tables."""
    sa = _sa()
    try:
        with database.transaction() as transaction:
            for table in tables:
                transaction.execute(sa.schema.CreateTable(table))
    except DatabaseError as exc:
        raise AssertionError(
            f"could not create the case's tables ({exc.code}); a table of that name may exist already, the"
            " portability cases need an empty or dedicated schema"
        ) from exc
    try:
        yield
    finally:
        with database.transaction() as transaction:
            for table in reversed(tables):
                transaction.execute(sa.schema.DropTable(table))


def _rejected(database: Database, statement: Any, params: dict[str, Any] | None = None, *, what: str) -> None:
    try:
        database.execute(statement, params)
    except DatabaseError:
        return
    raise AssertionError(f"the engine accepted {what}")


def _sqlite(database: Database) -> bool:
    return database.dialect == "sqlite"


def _pg_type(database: Database, column: Any) -> object:
    """``pg_typeof`` of ``column`` in its table's first row, as text (PostgreSQL only)."""
    sa = _sa()
    return database.fetch_scalar(sa.select(sa.cast(sa.func.pg_typeof(column), sa.Text)).limit(1))


def _d01_type(sa: Any, sqlite: bool) -> Any:
    return sa.String(32) if sqlite else sa.DateTime(timezone=True)


def _d01_text(value: dt.datetime) -> str:
    """ISO 8601 UTC text with ``Z``, the SQLite form of the rule."""
    return value.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _d01_timestamps(database: Database) -> None:
    sa = _sa()
    sqlite = _sqlite(database)
    table = sa.Table(
        f"tabrion_d01_{_suffix()}",
        sa.MetaData(),
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("at", _d01_type(sa, sqlite)),
    )
    early = dt.datetime(2024, 1, 2, 3, 4, 5, 678901, tzinfo=dt.UTC)
    late = dt.datetime(2024, 1, 2, 23, 0, 0, tzinfo=dt.timezone(dt.timedelta(hours=-5)))  # 04:00 UTC next day
    with _tables(database, table):
        if sqlite:
            values = [_d01_text(v) for v in (late, early)]
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


def _d02_type(sa: Any, sqlite: bool) -> Any:
    return sa.String(40) if sqlite else sa.Numeric(20, 2, asdecimal=True)


def _d02_decimal(database: Database) -> None:
    sa = _sa()
    table = sa.Table(
        f"tabrion_d02_{_suffix()}",
        sa.MetaData(),
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("amount", _d02_type(sa, _sqlite(database))),
        sa.Column("cents", sa.BigInteger),
    )
    exact = Decimal("123456789012345678.91")
    with _tables(database, table):
        rows = [{"id": i, "amount": str(Decimal("0.10")), "cents": 10} for i in (1, 2, 3)]
        rows.append({"id": 4, "amount": str(exact), "cents": 0})
        database.execute(table.insert(), rows)
        if not _sqlite(database):
            total = database.fetch_scalar(sa.select(sa.func.sum(table.c.amount)).where(table.c.id < 4))
            _expect(total == Decimal("0.30"), f"the sum {total!r} is not exactly 0.30")
        stored = database.fetch_scalar(sa.select(table.c.amount).where(table.c.id == 4))
        _expect(Decimal(str(stored)) == exact, f"{stored!r} is not exact")
        _expect(database.fetch_scalar(sa.select(sa.func.sum(table.c.cents))) == 30, "the sum of cents is not exact")


def _d03_id(sa: Any) -> tuple[Any, Any]:
    """Type and identity of the ID column: ``bigint generated always as identity``, ``INTEGER`` on SQLite."""
    return sa.BigInteger().with_variant(sa.Integer, "sqlite"), sa.Identity(always=True)


def _d03_integer_ids(database: Database) -> None:
    sa = _sa()
    kind, identity = _d03_id(sa)
    table = sa.Table(
        f"tabrion_d03_{_suffix()}",
        sa.MetaData(),
        sa.Column("id", kind, identity, primary_key=True),
        sa.Column("label", sa.String(10)),
    )
    with _tables(database, table):
        try:
            database.execute(table.insert(), [{"label": "a"}, {"label": "b"}])
        except IntegrityViolation as exc:
            raise AssertionError(f"the engine did not generate the ids ({exc.code})") from exc
        if _sqlite(database):
            rowid = sa.literal_column("rowid")
            aliased = database.fetch_scalar(sa.select(sa.func.count()).select_from(table).where(rowid == table.c.id))
            _expect(aliased == 2, "the id is not an alias of rowid")
        ids = [r[0] for r in database.fetch_all(sa.select(table.c.id).order_by(table.c.id))]
        _expect(len(ids) == 2 and all(type(i) is int for i in ids) and ids[0] < ids[1], repr(ids))
        if not _sqlite(database):
            pg_kind = _pg_type(database, table.c.id)
            _expect(pg_kind == "bigint", f"the id is {pg_kind}, not bigint")
            always = database.fetch_scalar(
                sa.text(
                    "SELECT a.attidentity FROM pg_catalog.pg_attribute a"
                    " WHERE a.attrelid = to_regclass(:t) AND a.attname = 'id'"
                ),
                {"t": table.name},
            )
            _expect(always == "a", "the id is not GENERATED ALWAYS AS IDENTITY")


def _d04_type(sa: Any, sqlite: bool) -> Any:
    return sa.String(36) if sqlite else sa.Uuid(as_uuid=True)


def _d04_uuid(database: Database) -> None:
    sa = _sa()
    sqlite = _sqlite(database)
    table = sa.Table(
        f"tabrion_d04_{_suffix()}",
        sa.MetaData(),
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("u", _d04_type(sa, sqlite)),
    )
    value = uuid.UUID("12345678-1234-5678-1234-567812345678")
    with _tables(database, table):
        database.execute(table.insert(), {"id": 1, "u": str(value) if sqlite else value})
        stored = database.fetch_scalar(sa.select(table.c.u))
        if sqlite:
            _expect(isinstance(stored, str) and len(stored) == 36 and uuid.UUID(stored) == value, repr(stored))
            length = database.fetch_scalar(sa.select(sa.func.length(table.c.u)))
            _expect(length == 36, f"the stored UUID text has {length} characters, not 36")
        else:
            _expect(stored == value, repr(stored))
            kind = _pg_type(database, table.c.u)
            _expect(kind == "uuid", f"the column is {kind}, not uuid")


def _d05_boolean(database: Database) -> None:
    sa = _sa()
    suffix = _suffix()
    table = sa.Table(
        f"tabrion_d05_{suffix}",
        sa.MetaData(),
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("flag", sa.Boolean(create_constraint=True, name=f"tabrion_d05_flag_{suffix}")),
    )
    with _tables(database, table):
        database.execute(table.insert(), [{"id": 1, "flag": True}, {"id": 2, "flag": False}])
        _expect(
            [r[0] for r in database.fetch_all(sa.select(table.c.flag).order_by(table.c.id))] == [True, False],
            "the booleans do not round trip",
        )
        two = table.insert().values(id=3, flag=sa.literal_column("2"))
        _rejected(database, two, what="2 as a boolean")


def _d06_text_length(database: Database) -> None:
    sa = _sa()
    suffix = _suffix()
    table = sa.Table(
        f"tabrion_d06_{suffix}",
        sa.MetaData(),
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(5)),
        sa.CheckConstraint("length(name) <= 5", name=f"tabrion_d06_length_{suffix}"),
    )
    with _tables(database, table):
        database.execute(table.insert(), [{"id": 1, "name": "abcde"}, {"id": 2, "name": "äöüßé"}])
        _rejected(database, table.insert(), {"id": 3, "name": "abcdef"}, what="six characters in String(5)")


def _d07_type(sa: Any) -> Any:
    from sqlalchemy.dialects import postgresql

    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _d07_json(database: Database) -> None:
    sa = _sa()
    table = sa.Table(
        f"tabrion_d07_{_suffix()}",
        sa.MetaData(),
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("doc", _d07_type(sa)),
    )
    document = {"a": [1, 2.5, True, None], "ü": "x", "nested": {"k": "v"}}
    with _tables(database, table):
        database.execute(table.insert(), {"id": 1, "doc": document})
        _expect(database.fetch_scalar(sa.select(table.c.doc)) == document, "the JSON document changed")
        if not _sqlite(database):
            kind = _pg_type(database, table.c.doc)
            _expect(kind == "jsonb", f"the column is {kind}, not jsonb")


def _d08_foreign_keys(database: Database) -> None:
    sa = _sa()
    suffix = _suffix()
    metadata = sa.MetaData()
    parent = sa.Table(f"tabrion_d08_parent_{suffix}", metadata, sa.Column("id", sa.Integer, primary_key=True))
    child = sa.Table(
        f"tabrion_d08_child_{suffix}",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("parent_id", sa.Integer, sa.ForeignKey(parent.c.id), nullable=False),
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


def _d09_type(sa: Any) -> Any:
    return sa.Double()


def _d09_floats(database: Database) -> None:
    sa = _sa()
    table = sa.Table(
        f"tabrion_d09_{_suffix()}",
        sa.MetaData(),
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("value", _d09_type(sa)),
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
