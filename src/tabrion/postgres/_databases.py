"""``create_database``, ``drop_database`` and ``database_sessions`` (since 0.3, stable; TAB-XW-9).

Contract section 4.8 and notes I-190..I-193; ADR 0015 sections 1 and 7. Re-exported by ``tabrion.postgres``; this
module is private. ``CREATE DATABASE`` and ``DROP DATABASE`` cannot run inside a transaction block, so both functions
need a connection in autocommit mode that is idle; Tabrion never switches ``autocommit`` (T-NOTX). Every identifier is
composed with ``psycopg.sql.Identifier`` and every option value with ``psycopg.sql.Literal`` from a validated value,
never with string formatting. Catalog names are qualified with ``pg_catalog`` (contract I-16). Tabrion asks nothing
and confirms nothing: the typed name, the backup before a drop and the decision to call are the consumer's (E-X5,
E-X9).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Literal

from tabrion.errors import Classification, DatabaseInUse, Kind, PermissionDenied, RoleMissing, UsageError
from tabrion.postgres._common import one_row, reading, require_connection, settle

if TYPE_CHECKING:
    import psycopg

__all__ = ["create_database", "database_sessions", "drop_database"]

_MAX_NAME_BYTES = 63
"""PostgreSQL's ``NAMEDATALEN - 1``: a longer name would be truncated to another object's name."""
_PROTECTED = frozenset({"postgres", "template0", "template1"})
_TEMPLATES = ("template0", "template1")
_ENCODING = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,31}")
"""An encoding name such as ``UTF8``, ``LATIN1`` or ``SQL_ASCII`` (validated, then sent as a literal)."""
_LOCALE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.@-]{0,62}")
"""A locale name such as ``C``, ``C.UTF-8`` or ``de_AT.utf8@euro`` (validated, then sent as a literal)."""
_SET_OPTION_VERSION = 160_000
"""PostgreSQL 16: ``CREATE DATABASE ... OWNER`` needs the ``SET`` option of the membership in the owner."""
_FORCE_VERSION = 130_000
"""PostgreSQL 13: ``DROP DATABASE ... WITH (FORCE)``."""

_SESSIONS_SQL = (
    "SELECT pg_catalog.count(*)::int FROM pg_catalog.pg_stat_activity a"
    " WHERE a.datname::pg_catalog.text OPERATOR(pg_catalog.=) %s::pg_catalog.text"
    " AND a.pid OPERATOR(pg_catalog.<>) pg_catalog.pg_backend_pid()"
)
"""Other sessions on the database. ``pg_stat_activity`` shows ``datname`` of every session to every role."""


def _denied(code: str) -> PermissionDenied:
    """A refusal of the privilege check before the statement: classified as SQLSTATE 42501 would be."""
    classification = Classification(
        kind=Kind.PERMISSION,
        retryable=False,
        outcome_unknown=False,
        connection_usable=True,
        driver="psycopg",
        sqlstate="42501",
        sqlite_code=None,
        constraint=None,
        table=None,
        column=None,
    )
    return PermissionDenied(code, classification=classification)


def _invalid(parameter: str) -> UsageError:
    # The parameter's name only, never its value.
    return UsageError("postgres.invalid_argument", details={"parameter": parameter})


def _check_name(value: object, parameter: str) -> str:
    """A database or role name: a non-empty ``str`` of at most 63 bytes (UTF-8) without NUL."""
    if not isinstance(value, str) or not value or "\x00" in value:
        raise _invalid(parameter)
    if len(value.encode("utf-8")) > _MAX_NAME_BYTES:
        raise _invalid(parameter)
    return value


def _require_autocommit(connection: psycopg.Connection[Any], function: str) -> None:
    """``CREATE``/``DROP DATABASE`` run outside a transaction block: autocommit and idle, never switched (T-NOTX)."""
    require_connection(connection, function)
    from psycopg import pq

    settle(connection)
    if not connection.autocommit:
        raise UsageError("postgres.autocommit_required")
    if connection.info.transaction_status != pq.TransactionStatus.IDLE:
        raise UsageError("postgres.nested_transaction")  # a `with connection.transaction():` block of the caller


def database_sessions(connection: psycopg.Connection[Any], name: str) -> int:
    """The number of sessions on the database ``name`` other than this connection's (read-only, for a preview).

    ``0`` for a database without sessions and for one that does not exist. Reads through its own cursor in any
    transaction state (contract section 4, T-NOTX). ``UsageError("postgres.invalid_argument")`` for a name that is not
    a non-empty ``str`` of at most 63 bytes without NUL.
    """
    database = _check_name(name, "name")
    with reading(connection, "database_sessions") as cursor:
        cursor.execute(_SESSIONS_SQL, (database,))
        return int(one_row(cursor)[0])


def create_database(
    connection: psycopg.Connection[Any],
    name: str,
    *,
    owner: str | None = None,
    template: Literal["template0", "template1"] = "template1",
    encoding: str = "UTF8",
    locale: str | None = None,
) -> None:
    """``CREATE DATABASE name [OWNER owner] TEMPLATE template ENCODING encoding [LOCALE locale]``.

    Needs a connection in autocommit mode that is idle (``UsageError("postgres.autocommit_required")``, inside the
    caller's ``transaction()`` block ``UsageError("postgres.nested_transaction")``); ``autocommit`` is never switched.
    Checked first, before the statement: the connecting role (``CURRENT_USER``) must have ``CREATEDB`` or be a
    superuser (``PermissionDenied("postgres.createdb_required")``); ``owner`` must exist (``RoleMissing``) and the
    connecting role must be able to ``SET ROLE`` to it (PostgreSQL 16 and later; a member of it before)
    (``PermissionDenied("postgres.owner_not_allowed")``). The names must be non-empty ``str`` values of at most 63
    bytes without NUL, ``template`` ``"template0"`` or ``"template1"``, ``encoding`` an encoding name and ``locale``
    a locale name (letters, digits, ``_ . @ -``); otherwise ``UsageError("postgres.invalid_argument")``, details
    ``parameter``. A driver error of the statement (an existing database, an unknown encoding or locale, a locale that
    needs ``template0``) propagates unchanged. Identifiers are quoted by ``psycopg.sql.Identifier``, option values sent
    as ``psycopg.sql.Literal`` (contract section 4.8, I-190, I-191).
    """
    database = _check_name(name, "name")
    owner_name = None if owner is None else _check_name(owner, "owner")
    if template not in _TEMPLATES:
        raise _invalid("template")
    if not isinstance(encoding, str) or _ENCODING.fullmatch(encoding) is None:
        raise _invalid("encoding")
    if locale is not None and (not isinstance(locale, str) or _LOCALE.fullmatch(locale) is None):
        raise _invalid("locale")
    _require_autocommit(connection, "create_database")
    import psycopg
    from psycopg import sql
    from psycopg.rows import tuple_row

    option = "SET" if connection.info.server_version >= _SET_OPTION_VERSION else "MEMBER"
    with psycopg.Cursor(connection, row_factory=tuple_row) as cursor:
        cursor.execute(
            "SELECT r.rolcreatedb OR r.rolsuper FROM pg_catalog.pg_roles r"
            " WHERE r.rolname::pg_catalog.text OPERATOR(pg_catalog.=) CURRENT_USER::pg_catalog.text"
        )
        row = cursor.fetchone()
        if row is None or not row[0]:
            raise _denied("postgres.createdb_required")
        if owner_name is not None:
            cursor.execute(
                "SELECT pg_catalog.pg_has_role(CURRENT_USER, r.oid, %s) FROM pg_catalog.pg_roles r"
                " WHERE r.rolname::pg_catalog.text OPERATOR(pg_catalog.=) %s::pg_catalog.text",
                (option, owner_name),
            )
            found = cursor.fetchone()
            if found is None:
                raise RoleMissing("postgres.role_missing")
            if not found[0]:
                raise _denied("postgres.owner_not_allowed")
        parts = [sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database))]
        if owner_name is not None:
            parts.append(sql.SQL("OWNER {}").format(sql.Identifier(owner_name)))
        parts.append(sql.SQL("TEMPLATE {}").format(sql.Identifier(template)))
        parts.append(sql.SQL("ENCODING {}").format(sql.Literal(encoding)))
        if locale is not None:
            parts.append(sql.SQL("LOCALE {}").format(sql.Literal(locale)))
        cursor.execute(sql.SQL(" ").join(parts))


def drop_database(connection: psycopg.Connection[Any], name: str, *, force: bool = False) -> None:
    """``DROP DATABASE name`` (``WITH (FORCE)`` with ``force=True``, PostgreSQL 13 and later).

    Needs a connection in autocommit mode that is idle, as :func:`create_database`. Refused before the statement:
    ``postgres``, ``template0``, ``template1``, any template database (``datistemplate``) and the connection's own
    database (``UsageError("postgres.database_protected")``); a database that does not exist
    (``UsageError("postgres.database_missing")``); a database with other sessions unless ``force``
    (``DatabaseInUse("postgres.database_in_use")``, details ``sessions``). A session that connects between the
    check and the statement makes PostgreSQL refuse the drop (SQLSTATE 55006), which is raised as the same
    ``DatabaseInUse`` with the driver error as ``original``. ``force=True`` terminates the other sessions; the
    connecting role needs the right to signal them, otherwise the driver error propagates. ``force`` must be a
    ``bool`` (``UsageError("postgres.invalid_argument")``). The typed confirmation and the backup before a drop are
    the consumer's (D-13, E-X5; contract section 4.8, I-192).
    """
    database = _check_name(name, "name")
    if not isinstance(force, bool):
        raise _invalid("force")
    _require_autocommit(connection, "drop_database")
    import psycopg
    from psycopg import sql
    from psycopg.rows import tuple_row

    if force and connection.info.server_version < _FORCE_VERSION:
        raise UsageError("postgres.force_requires_13")
    if database in _PROTECTED:
        raise UsageError("postgres.database_protected")
    with psycopg.Cursor(connection, row_factory=tuple_row) as cursor:
        cursor.execute(
            "SELECT d.datistemplate, d.datname OPERATOR(pg_catalog.=) pg_catalog.current_database()"
            " FROM pg_catalog.pg_database d"
            " WHERE d.datname::pg_catalog.text OPERATOR(pg_catalog.=) %s::pg_catalog.text",
            (database,),
        )
        row = cursor.fetchone()
        if row is None:
            raise UsageError("postgres.database_missing")
        if row[0] or row[1]:
            raise UsageError("postgres.database_protected")
        if not force:
            cursor.execute(_SESSIONS_SQL, (database,))
            sessions = int(one_row(cursor)[0])
            if sessions:
                raise DatabaseInUse("postgres.database_in_use", details={"sessions": sessions})
        statement = sql.SQL("DROP DATABASE {}").format(sql.Identifier(database))
        if force:
            statement = sql.SQL("{} WITH (FORCE)").format(statement)
        failure: psycopg.errors.ObjectInUse | None = None
        try:
            cursor.execute(statement)
        except psycopg.errors.ObjectInUse as exc:
            failure = exc
        if failure is None:
            return
        cursor.execute(_SESSIONS_SQL, (database,))
        sessions = int(one_row(cursor)[0])
    # Raised outside the except block: no __cause__ and no __context__ (contract section 1.4).
    raise DatabaseInUse("postgres.database_in_use", original=failure, details={"sessions": sessions})
