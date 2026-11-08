"""SQLAlchemy ``connect``/``checkout``/``begin`` listeners that apply layer A to the same DBAPI connection.

Private. Specification section 3.1, contract I-37 (TAB-018). There is no second connection, no own pool and no
adapter between the layers: each listener calls a layer-A function on the DBAPI connection SQLAlchemy hands it.

- An engine that ``Database.from_url`` creates for SQLite opens every connection through ``tabrion.sqlite.connect``
  (its ``creator``), which applies the profile, the path checks and ``mode=ro``; it needs no ``connect`` listener.
- For PostgreSQL the ``connect`` listener runs ``tabrion.postgres.session_configurator`` (one round trip, then
  ``commit()``) and, under ``Profile.read_only``, sets ``default_transaction_read_only``.
- A foreign engine (``Database.from_engine``) gets a ``checkout`` listener instead, which applies the same once per
  DBAPI connection (marked in ``connection_record.info``), so connections already in its pool are covered too.
- The ``begin`` listener reads Tabrion's execution option ``tabrion_mode``: on SQLite ``write`` sends ``BEGIN
  IMMEDIATE``, ``read`` ``PRAGMA query_only = ON`` and ``BEGIN``, and a plain transaction a deferred ``BEGIN``.
  PostgreSQL needs no ``begin`` listener: ``postgresql_readonly`` and ``isolation_level`` are SQLAlchemy's own
  execution options; a ``checkin`` listener gives psycopg's ``read_only`` back to the server default after a
  ``read()``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
import sqlite3
from typing import Any

import sqlalchemy

from tabrion._sa.profile import Profile
from tabrion.postgres import session_configurator
from tabrion.sqlite import apply_profile

__all__: list[str] = []

MODE = "tabrion_mode"
"""The execution option that marks a Tabrion transaction: ``"write"`` or ``"read"``."""

READ_USED = "tabrion_read_only_used"
"""``connection_record.info`` key: a Tabrion ``read()`` ran on this DBAPI connection (PostgreSQL)."""

_APPLIED = "tabrion_profile_applied"
"""``connection_record.info`` key: the checkout listener applied the profile to this DBAPI connection."""

Listener = tuple[Any, str, Callable[..., None]]
"""``(target, event name, function)``, as ``sqlalchemy.event.remove`` takes them."""


def on_begin(connection: sqlalchemy.Connection) -> None:
    """SQLite: ``BEGIN IMMEDIATE`` for ``write``, ``query_only`` plus ``BEGIN`` for ``read`` (specification 3.1).

    A plain ``engine.connect()`` transaction gets ``BEGIN`` (deferred) when the driver would not begin one itself
    (``isolation_level=None``, no transaction open), so it stays one transaction, as specification 3.1 says.
    """
    if connection.dialect.name != "sqlite":
        return
    mode = connection.get_execution_options().get(MODE)
    if mode is None:
        dbapi_connection = connection.connection.dbapi_connection
        if (
            isinstance(dbapi_connection, sqlite3.Connection)
            and dbapi_connection.isolation_level is None
            and not dbapi_connection.in_transaction
        ):
            connection.exec_driver_sql("BEGIN")
        return
    if mode == "read":
        connection.exec_driver_sql("PRAGMA query_only = ON")
        connection.exec_driver_sql("BEGIN")
    else:
        connection.exec_driver_sql("BEGIN IMMEDIATE")


def on_checkin(dbapi_connection: Any, connection_record: Any) -> None:
    """PostgreSQL: after a Tabrion ``read()``, give psycopg's ``read_only`` back to the server default (I-41).

    SQLAlchemy resets ``postgresql_readonly`` by setting ``read_only = False``, which makes psycopg send ``BEGIN READ
    WRITE`` for every later transaction on the connection and so overrides ``default_transaction_read_only`` of a
    read-only profile. ``None`` lets the session default decide again. If that fails, the connection is invalidated.
    """
    info = connection_record.info
    if not info.pop(READ_USED, False) or dbapi_connection is None:
        return
    try:
        if dbapi_connection.read_only is not None:
            dbapi_connection.read_only = None
    except Exception as exc:
        connection_record.invalidate(exc)


def postgres_setup(profile: Profile) -> Callable[[Any], None]:
    """Configure a new psycopg connection: the session settings, then ``default_transaction_read_only``.

    ``Profile.application_name`` replaces the one of ``Profile.postgres`` (specification 4.3). The settings are
    validated here, once, so a bad profile fails in ``from_url``/``from_engine`` (``ConfigurationError``).
    """
    configure = session_configurator(replace(profile.postgres, application_name=profile.application_name))
    read_only = profile.read_only

    def setup(dbapi_connection: Any) -> None:
        configure(dbapi_connection)
        if read_only:
            import psycopg
            from psycopg import pq
            from psycopg.rows import tuple_row

            opened = (
                not dbapi_connection.autocommit
                and dbapi_connection.info.transaction_status == pq.TransactionStatus.IDLE
            )
            with psycopg.Cursor(dbapi_connection, row_factory=tuple_row) as cursor:
                cursor.execute("SELECT pg_catalog.set_config('default_transaction_read_only', 'on', false)")
            if opened:
                dbapi_connection.commit()

    return setup


def sqlite_setup(profile: Profile) -> Callable[[Any], None]:
    """For a foreign engine's ``sqlite3`` connection: driver autobegin off, the profile, ``query_only``."""
    sqlite_profile = profile.sqlite
    read_only = profile.read_only

    def setup(dbapi_connection: Any) -> None:
        if not isinstance(dbapi_connection, sqlite3.Connection):
            raise TypeError(f"expected a sqlite3.Connection, not {type(dbapi_connection).__name__}")
        dbapi_connection.isolation_level = None
        apply_profile(dbapi_connection, sqlite_profile)
        if read_only:
            dbapi_connection.execute("PRAGMA query_only = ON")

    return setup


def install(engine: sqlalchemy.Engine, profile: Profile, *, owned: bool, apply: bool) -> list[Listener]:
    """Add the listeners; returns them for ``sqlalchemy.event.remove`` (``Database.close`` of a foreign engine)."""
    listeners: list[Listener] = [(engine, "begin", on_begin)]
    dialect = engine.dialect.name
    if dialect == "postgresql":
        listeners.append((engine, "checkin", on_checkin))
    if apply:
        if dialect == "postgresql":
            setup = postgres_setup(profile)
        elif not owned:
            setup = sqlite_setup(profile)
        else:
            setup = None  # tabrion.sqlite.connect is the creator
        if setup is not None and owned:
            listeners.append((engine, "connect", _on_connect(setup)))
        elif setup is not None:
            listeners.append((engine, "checkout", _on_checkout(setup)))
    for target, name, function in listeners:
        sqlalchemy.event.listen(target, name, function)
    return listeners


def remove(listeners: list[Listener]) -> None:
    for target, name, function in listeners:
        if sqlalchemy.event.contains(target, name, function):
            sqlalchemy.event.remove(target, name, function)


def _on_connect(setup: Callable[[Any], None]) -> Callable[..., None]:
    def connect(dbapi_connection: Any, connection_record: Any) -> None:
        setup(dbapi_connection)

    return connect


def _on_checkout(setup: Callable[[Any], None]) -> Callable[..., None]:
    def checkout(dbapi_connection: Any, connection_record: Any, connection_proxy: Any) -> None:
        info = connection_record.info
        if info.get(_APPLIED) is not setup:
            setup(dbapi_connection)
            info[_APPLIED] = setup

    return checkout
