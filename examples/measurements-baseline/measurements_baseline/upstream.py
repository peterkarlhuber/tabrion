"""Database infrastructure of the baseline transfer: the upstream database with plain SQLAlchemy (RA-B-4).

The counterpart of ``examples/measurements/measurements/upstream.py`` for metric MW-1. Same table, same insert;
the engine is SQLAlchemy's own, configured by hand: the ``psycopg`` driver (SQLAlchemy's default for
``postgresql://`` is psycopg2), one pooled connection with ``pool_pre_ping``, a connect timeout and an
``application_name``. The URL comes from ``MEASUREMENTS_UPSTREAM_URL`` or a file named by
``MEASUREMENTS_UPSTREAM_URL_FILE``.

Not added (Tabrion's part, counted by RA-EVAL): the TLS rule for remote hosts, server-side statement, lock and idle
timeouts, value-free errors with a classification (``retryable``, ``outcome_unknown``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import os
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql, sqlite

URL_ENV = "MEASUREMENTS_UPSTREAM_URL"
"""The environment variable with the upstream URL (or ``MEASUREMENTS_UPSTREAM_URL_FILE``, a file holding it)."""

APPLICATION_NAME = "measurements-transfer"
CONNECT_TIMEOUT = 10

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


def url_from_env(env: str = URL_ENV) -> str:
    """The URL from ``env`` or from the file named by ``<env>_FILE`` (``LookupError`` if neither is set)."""
    direct, path = os.environ.get(env) or None, os.environ.get(f"{env}_FILE") or None
    if direct is not None and path is not None:
        raise LookupError(f"set {env} or {env}_FILE, not both")
    value = Path(path).read_text(encoding="utf-8").rstrip("\n") if path is not None else direct
    if not value:
        raise LookupError(f"{env} is not set")
    return value


def open_upstream(url: str | sa.URL | None = None, *, env: str = URL_ENV) -> sa.Engine:
    """An ``Engine`` for ``url``, or for the URL in ``env``."""
    parsed = sa.make_url(url if url is not None else url_from_env(env))
    if parsed.get_backend_name() != "postgresql":
        return sa.create_engine(parsed)
    if parsed.drivername == "postgresql":
        parsed = parsed.set(drivername="postgresql+psycopg")
    return sa.create_engine(
        parsed,
        pool_size=1,
        max_overflow=0,
        pool_pre_ping=True,
        connect_args={"connect_timeout": CONNECT_TIMEOUT, "application_name": APPLICATION_NAME},
    )


def create_upstream(engine: sa.Engine) -> None:
    """Create the upstream table if it is missing (one transaction; needs the CREATE privilege)."""
    with engine.begin() as connection:
        METADATA.create_all(connection, checkfirst=True)


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
