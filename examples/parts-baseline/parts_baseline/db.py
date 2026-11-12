"""Database infrastructure of the plain SQLAlchemy baseline (RA-A-3): the engine, Alembic, the start-up gate and the
error classification that ``examples/parts`` gets from Tabrion.

All database plumbing lives here (metric MW-1 of planning document 09 section 7). It is what a careful application
writes with SQLAlchemy and Alembic alone, following their documentation, and it defines "plain SQLAlchemy" the same
way as the RA-B-4 baseline (``examples/measurements-baseline``):

- on SQLite, a ``connect`` event for ``PRAGMA foreign_keys=ON`` and the documented pysqlite recipe that takes
  ``BEGIN`` over from the driver (``isolation_level = None``, then a ``begin`` event), so DDL and reads are inside
  the transaction and a write transaction (``write_transaction``) starts with ``BEGIN IMMEDIATE``;
- on PostgreSQL, ``pool_pre_ping`` and the ``search_path`` and time zone through ``options``;
- the driver errors told apart by SQLSTATE or by ``sqlite3.Error.sqlite_errorname``.

It does not add read-only transactions or value-free error messages.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
import hashlib
from pathlib import Path
import sqlite3
from typing import Any

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
import sqlalchemy as sa

SCRIPT_LOCATION = Path(__file__).resolve().with_name("migrations")


class SchemaNotCurrent(Exception):
    """The database is not at the head revision of ``parts_baseline/migrations``."""


def _sqlite_on_connect(dbapi_connection: sqlite3.Connection, _record: Any) -> None:
    dbapi_connection.isolation_level = None  # pysqlite's own BEGIN off: SQLAlchemy's "begin" event below
    dbapi_connection.execute("PRAGMA foreign_keys = ON")


def _sqlite_on_begin(connection: sa.Connection) -> None:
    immediate = connection.get_execution_options().get("immediate", False)
    connection.exec_driver_sql("BEGIN IMMEDIATE" if immediate else "BEGIN")


def create_engine(url: str | sa.URL, *, search_path: str | None = None) -> sa.Engine:
    """An engine for ``sqlite:///...`` or ``postgresql://...`` (the psycopg 3 driver); on PostgreSQL in UTC and,
    when given, with ``search_path``."""
    url = sa.make_url(url)
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg")
    if url.get_backend_name() == "sqlite":
        engine = sa.create_engine(url)
        sa.event.listen(engine, "connect", _sqlite_on_connect)
        sa.event.listen(engine, "begin", _sqlite_on_begin)
        return engine
    options = "-c timezone=UTC" + (f" -c search_path={search_path}" if search_path else "")
    return sa.create_engine(url, pool_pre_ping=True, connect_args={"options": options})


@contextmanager
def write_transaction(engine: sa.Engine) -> Iterator[sa.Connection]:
    """A write transaction (``BEGIN IMMEDIATE`` on SQLite): committed at the end, rolled back on an error."""
    with engine.connect() as connection:
        connection = connection.execution_options(immediate=True)
        with connection.begin():
            yield connection


def _config() -> Config:
    config = Config()
    config.set_main_option("script_location", str(SCRIPT_LOCATION))
    return config


def upgrade(engine: sa.Engine, revision: str = "head") -> None:
    """``alembic upgrade`` in one ``write_transaction``: all of it or nothing."""
    config = _config()
    with write_transaction(engine) as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, revision)


def downgrade(engine: sa.Engine, revision: str) -> None:
    config = _config()
    with write_transaction(engine) as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, revision)


@dataclass(frozen=True, slots=True)
class SchemaStatus:
    current: tuple[str, ...]
    heads: tuple[str, ...]
    pending: tuple[str, ...]

    @property
    def is_current(self) -> bool:
        return not self.pending and set(self.current) <= set(self.heads)


def status(engine: sa.Engine) -> SchemaStatus:
    """The applied and pending revisions, read with Alembic's ``MigrationContext`` (no ``env.py``)."""
    script = ScriptDirectory(str(SCRIPT_LOCATION))
    with engine.connect() as connection:
        current = tuple(sorted(MigrationContext.configure(connection).get_current_heads()))
    heads = tuple(sorted(script.get_heads()))
    applied: set[str] = set()
    for revision in current:
        applied.update(r.revision for r in script.iterate_revisions(revision, "base"))
    pending = tuple(
        r.revision for r in reversed(list(script.iterate_revisions("heads", "base"))) if r.revision not in applied
    )
    return SchemaStatus(current, heads, pending)


def require_current(engine: sa.Engine) -> SchemaStatus:
    """The start-up gate: raises ``SchemaNotCurrent`` unless every migration is applied."""
    found = status(engine)
    if not found.is_current:
        raise SchemaNotCurrent("the database schema is not current; run the migrations")
    return found


def relations_lock() -> int:
    """The advisory lock key of ``catalog.add_component``: a stable signed 64-bit number from its name."""
    return int.from_bytes(hashlib.sha256(b"parts.part_relation").digest()[:8], "big", signed=True)


class Violation(Enum):
    UNIQUE = "unique"
    FOREIGN_KEY = "foreign_key"
    CHECK = "check"
    DATA = "data"
    OTHER = "other"


_SQLSTATES = {
    "23505": Violation.UNIQUE,
    "23503": Violation.FOREIGN_KEY,
    "23001": Violation.FOREIGN_KEY,
    "23514": Violation.CHECK,
    "22001": Violation.DATA,
}


_SQLITE_NAMES = {
    "SQLITE_CONSTRAINT_UNIQUE": Violation.UNIQUE,
    "SQLITE_CONSTRAINT_PRIMARYKEY": Violation.UNIQUE,
    "SQLITE_CONSTRAINT_FOREIGNKEY": Violation.FOREIGN_KEY,
    "SQLITE_CONSTRAINT_CHECK": Violation.CHECK,
}


def classify(error: sa.exc.DBAPIError) -> Violation:
    """What a ``DBAPIError`` broke: the SQLSTATE on PostgreSQL, ``sqlite_errorname`` on SQLite."""
    original = error.orig
    sqlstate = getattr(original, "sqlstate", None)
    if sqlstate is not None:
        return _SQLSTATES.get(sqlstate, Violation.OTHER)
    if isinstance(original, sqlite3.Error):
        return _SQLITE_NAMES.get(original.sqlite_errorname, Violation.OTHER)
    return Violation.OTHER
