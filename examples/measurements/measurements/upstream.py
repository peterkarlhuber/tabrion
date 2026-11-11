"""Database infrastructure of the transfer (RA-B-2): the upstream database through Tabrion's layer B.

Like ``db.py`` for the local file, this module holds every line of upstream plumbing (metric MW-1 counts it):

- ``open_upstream`` opens a ``tabrion.Database`` with this application's ``tabrion.Profile`` (pool, timeouts,
  ``application_name``, TLS rules). The URL comes from the environment (``tabrion.config.url_from_env``, which also
  reads ``<NAME>_FILE``), never from the command line, and no error ever shows it.
- ``UPSTREAM`` is the portable server table, created by ``create_upstream``. The natural key
  ``(device_id, channel, ts_utc)`` is the primary key; ``ts_utc`` is a ``timestamptz`` on PostgreSQL.
- ``insert_new`` is one ``INSERT ... ON CONFLICT (device_id, channel, ts_utc) DO NOTHING`` per batch: a reading
  sent twice is stored once, which is what makes a repeated transfer harmless.

PostgreSQL is the target; a ``sqlite:///`` URL works unchanged (metric MW-8), which the core test suite uses.
Needs the extras ``sqlalchemy`` and, for PostgreSQL, ``postgres``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql, sqlite

from tabrion import Database, Profile

URL_ENV = "MEASUREMENTS_UPSTREAM_URL"
"""The environment variable with the upstream URL (or ``MEASUREMENTS_UPSTREAM_URL_FILE``, a file holding it)."""

PROFILE = Profile(application_name="measurements-transfer", pool_size=1, max_overflow=0, connect_timeout=10.0)
"""One connection is enough for the transfer loop; the server-side timeouts are Tabrion's defaults."""

MAX_BATCH = 4_000
"""Rows per ``INSERT``: 7 parameters each stays below SQLite's 32 766 and PostgreSQL's 65 535 bind parameters."""

METADATA = sa.MetaData()
UPSTREAM = sa.Table(
    "measurement",
    METADATA,
    sa.Column("device_id", sa.String(64), primary_key=True),
    sa.Column("channel", sa.String(64), primary_key=True),
    sa.Column("ts_utc", sa.DateTime(timezone=True), primary_key=True),
    sa.Column("value", sa.Double(), nullable=False),
    sa.Column("unit", sa.String(16), nullable=False),
    sa.Column("quality", sa.SmallInteger(), nullable=False),
    sa.Column("source_seq", sa.BigInteger(), nullable=False),
)
"""``source_seq`` is the device's ``seq`` of the first copy that arrived: a diagnostic, not part of the key."""


def open_upstream(url: str | sa.URL | None = None, *, env: str = URL_ENV, profile: Profile = PROFILE) -> Database:
    """``tabrion.Database`` for ``url``, or for the URL in ``env`` (``ConfigurationError("config.missing")``)."""
    if url is None:
        return Database.from_env(env, profile=profile)
    return Database.from_url(url, profile=profile)


def create_upstream(database: Database) -> None:
    """Create the upstream table if it is missing (one write transaction; needs the CREATE privilege)."""
    with database.transaction() as transaction:
        METADATA.create_all(transaction.connection, checkfirst=True)


def insert_new(dialect: str, rows: Sequence[Mapping[str, Any]]) -> sa.Insert:
    """One multi-row ``INSERT ... ON CONFLICT (natural key) DO NOTHING RETURNING source_seq``: one row per new row.

    ``RETURNING`` rather than the row count, which SQLAlchemy does not report for a multi-row ``INSERT`` on every
    driver."""
    if not 1 <= len(rows) <= MAX_BATCH:
        raise ValueError(f"a batch holds 1 to {MAX_BATCH} rows")
    insert = postgresql.insert if dialect == "postgresql" else sqlite.insert
    statement = insert(UPSTREAM).values(list(rows))
    return statement.on_conflict_do_nothing(index_elements=["device_id", "channel", "ts_utc"]).returning(
        UPSTREAM.c.source_seq
    )
