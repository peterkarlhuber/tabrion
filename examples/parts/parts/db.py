"""Database infrastructure of the parts application: the ``Database``, Alembic upgrades and the start-up gate.

All database plumbing lives here (metric MW-1 of planning document 09 section 7); the schema is ``schema.py``.
Migrations run through Alembic's ``command.upgrade`` inside ``Database.transaction()`` of the application's own
``Database`` (one pool, the profile's session settings, the ``search_path`` on PostgreSQL, ``BEGIN IMMEDIATE`` on
SQLite, so a concurrent writer is waited for instead of failing on a stale snapshot, and Tabrion's errors);
``env.py`` takes that connection from ``config.attributes`` and never commits it. ``require_current`` reads the
version table without running ``env.py``.
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
import sqlalchemy

from tabrion import Database, Profile
from tabrion import migrations as tmigrations

SCRIPT_LOCATION = Path(__file__).resolve().with_name("migrations")


def open_database(url: str | sqlalchemy.URL, *, profile: Profile | None = None) -> Database:
    """``Database.from_url`` with the application's profile (the default unless one is given)."""
    return Database.from_url(url, profile=profile)


def _config() -> Config:
    config = Config()
    config.set_main_option("script_location", str(SCRIPT_LOCATION))
    return config


def upgrade(database: Database, revision: str = "head") -> None:
    """``alembic upgrade`` in one ``database.transaction()``: all of it or nothing."""
    config = _config()
    with database.transaction() as transaction:
        config.attributes["connection"] = transaction.connection
        command.upgrade(config, revision)


def downgrade(database: Database, revision: str) -> None:
    """``alembic downgrade`` in one ``database.transaction()``: all of it or nothing."""
    config = _config()
    with database.transaction() as transaction:
        config.attributes["connection"] = transaction.connection
        command.downgrade(config, revision)


def status(database: Database) -> tmigrations.SchemaStatus:
    return tmigrations.status(database, script_location=SCRIPT_LOCATION)


def require_current(database: Database) -> tmigrations.SchemaStatus:
    """The start-up gate: raises ``tabrion.errors.SchemaNotCurrent`` unless every migration is applied."""
    return tmigrations.require_current(database, script_location=SCRIPT_LOCATION)
