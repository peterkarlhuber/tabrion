"""Database infrastructure of the plain SQLAlchemy baseline (RA-A-3): the engine, Alembic, the start-up gate and the
error classification that ``examples/parts`` gets from Tabrion.

All database plumbing lives here (metric MW-1 of planning document 09 section 7). It is what a careful application
writes with SQLAlchemy and Alembic alone, following their documentation: ``PRAGMA foreign_keys=ON`` on every SQLite
connection, ``pool_pre_ping``, the PostgreSQL ``search_path`` and time zone through ``options``, and the driver
errors told apart by SQLSTATE or by the SQLite message. It does not set WAL, ``BEGIN IMMEDIATE`` or read-only
transactions; RA-EVAL measures what that changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
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


def _sqlite_on_connect(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


def create_engine(url: str | sa.URL, *, search_path: str | None = None) -> sa.Engine:
    """An engine for ``sqlite:///...`` or ``postgresql://...`` (the psycopg 3 driver); on PostgreSQL in UTC and,
    when given, with ``search_path``."""
    url = sa.make_url(url)
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg")
    if url.get_backend_name() == "sqlite":
        engine = sa.create_engine(url, pool_pre_ping=True)
        sa.event.listen(engine, "connect", _sqlite_on_connect)
        return engine
    options = "-c timezone=UTC" + (f" -c search_path={search_path}" if search_path else "")
    return sa.create_engine(url, pool_pre_ping=True, connect_args={"options": options})


def _config() -> Config:
    config = Config()
    config.set_main_option("script_location", str(SCRIPT_LOCATION))
    return config


def upgrade(engine: sa.Engine, revision: str = "head") -> None:
    """``alembic upgrade`` in one transaction on a connection of ``engine``."""
    config = _config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, revision)


def downgrade(engine: sa.Engine, revision: str) -> None:
    config = _config()
    with engine.begin() as connection:
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


class Violation(Enum):
    UNIQUE = "unique"
    FOREIGN_KEY = "foreign_key"
    OTHER = "other"


_SQLSTATES = {"23505": Violation.UNIQUE, "23503": Violation.FOREIGN_KEY, "23001": Violation.FOREIGN_KEY}


def classify(error: sa.exc.IntegrityError) -> Violation:
    """Which constraint an ``IntegrityError`` broke: the SQLSTATE on PostgreSQL, the message on SQLite."""
    original = error.orig
    sqlstate = getattr(original, "sqlstate", None)
    if sqlstate is not None:
        return _SQLSTATES.get(sqlstate, Violation.OTHER)
    if isinstance(original, sqlite3.IntegrityError):
        message = str(original)
        if message.startswith(("UNIQUE constraint failed", "PRIMARY KEY")):
            return Violation.UNIQUE
        if message.startswith("FOREIGN KEY constraint failed"):
            return Violation.FOREIGN_KEY
    return Violation.OTHER
