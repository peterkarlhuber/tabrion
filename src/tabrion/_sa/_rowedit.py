"""Row statements of the guided write transaction (TAB-XW-6). Private: use ``tabrion.rowedit`` (since 0.3).

The functions build SQLAlchemy Core statements for one row; they never execute anything. Run them with
``Transaction.execute_checked(statement, expect_rowcount=1)``: an update or delete whose optimistic predicate matched no
row (the row changed or is gone) then raises ``RowCountMismatch`` with ``actual`` 0 and the transaction rolls back.
Contract section 5.11, implementation note I-121; ADR 0015 section 9 N-5.

Identifiers are quoted by the dialect's compiler (SQLAlchemy), and every value is a bound parameter
(``bindparam`` with the column's type) or the SQL keyword ``NULL`` for ``None``: no value is ever written into the SQL
text, and a SQL expression passed as a value is bound as a parameter, never compiled (the driver refuses it).

The optimistic predicate compares each column of ``old`` NULL-safe and exactly (:class:`_Unchanged`): PostgreSQL
``IS NOT DISTINCT FROM``, SQLite ``IS``; text columns under a binary collation (PostgreSQL ``"C"``, SQLite
``BINARY``), so a case-insensitive or otherwise non-deterministic collation cannot hide a concurrent change; PostgreSQL
``json`` columns (which have no equality operator) as ``jsonb``; on PostgreSQL a floating-point value cast to the
column's type, so a ``real`` value is compared as ``real``. Floating-point and JSON values compare exactly as
stored, so a value that was rounded or re-serialized on its way through the client reports a false conflict (N-5):
the safe side, nothing is written.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import sqlalchemy
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.compiler import SQLCompiler
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.visitors import InternalTraversal

from tabrion.errors import UsageError

__all__: list[str] = []


class _Unchanged(ColumnElement[bool]):
    """``column`` still holds ``value`` (NULL-safe, exact): the optimistic predicate of one column (I-121 b)."""

    __visit_name__ = "tabrion_unchanged"
    inherit_cache = True
    _traverse_internals = [  # noqa: RUF012 - SQLAlchemy's own class attribute (the cache key of the construct)
        ("column", InternalTraversal.dp_clauseelement),
        ("value", InternalTraversal.dp_clauseelement),
    ]
    type = sqlalchemy.Boolean()
    _is_implicitly_boolean = True  # a comparison: never rendered as "<predicate> = 1" where booleans are integers

    def __init__(self, column: sqlalchemy.Column[Any], value: ColumnElement[Any]) -> None:
        self.column = column
        self.value = value


def _is_text(column: sqlalchemy.Column[Any]) -> bool:
    # An enum is a String in SQLAlchemy but a type of its own on PostgreSQL, which takes no collation.
    return isinstance(column.type, sqlalchemy.String) and not isinstance(column.type, sqlalchemy.Enum)


@compiles(_Unchanged)
def _compile_default(element: _Unchanged, compiler: SQLCompiler, **kw: Any) -> str:
    return compiler.process(element.column.is_not_distinct_from(element.value), **kw)


@compiles(_Unchanged, "sqlite")
def _compile_sqlite(element: _Unchanged, compiler: SQLCompiler, **kw: Any) -> str:
    column = element.column
    left = column.collate("BINARY") if _is_text(column) else column
    return compiler.process(left.is_not_distinct_from(element.value), **kw)  # SQLite renders IS


@compiles(_Unchanged, "postgresql")
def _compile_postgresql(element: _Unchanged, compiler: SQLCompiler, **kw: Any) -> str:
    column = element.column
    if isinstance(column.type, sqlalchemy.JSON):
        # json has no equality operator; jsonb compares the parsed values (key order and blanks do not count).
        predicate = sqlalchemy.cast(column, JSONB).is_not_distinct_from(sqlalchemy.cast(element.value, JSONB))
    elif _is_text(column):
        predicate = column.collate("C").is_not_distinct_from(element.value)
    elif isinstance(column.type, sqlalchemy.Float):
        # psycopg sends a Python float as float8; a real column would compare its float4 value with the float8 one
        # and never match 0.1. The value is cast to the column's type first.
        predicate = column.is_not_distinct_from(sqlalchemy.cast(element.value, column.type))
    else:
        predicate = column.is_not_distinct_from(element.value)
    return compiler.process(predicate, **kw)


def _table(table: object) -> sqlalchemy.Table:
    if not isinstance(table, sqlalchemy.Table):
        raise TypeError(f"table must be a sqlalchemy.Table, not {type(table).__name__}")
    return table


def _columns(table: sqlalchemy.Table, values: object, name: str) -> list[tuple[sqlalchemy.Column[Any], Any]]:
    """``(column, value)`` for each entry of ``values``; the message names neither a value nor a key."""
    if not isinstance(values, Mapping):
        raise TypeError(f"{name} must be a mapping of column names to values, not {type(values).__name__}")
    pairs: list[tuple[sqlalchemy.Column[Any], Any]] = []
    for key, value in values.items():
        if not isinstance(key, str):
            raise TypeError(f"the keys of {name} must be column names (str)")
        if key not in table.c:
            raise ValueError(f"{name} names a column the table does not have")
        pairs.append((table.c[key], value))
    return pairs


def _bound(column: sqlalchemy.Column[Any], value: Any) -> ColumnElement[Any]:
    """``value`` as a bound parameter of the column's type; ``None`` as SQL ``NULL`` (also for JSON columns)."""
    if value is None:
        return sqlalchemy.null()
    return sqlalchemy.bindparam(None, value, type_=column.type, unique=True)


def _key(table: sqlalchemy.Table, key: object) -> list[ColumnElement[bool]]:
    """``<pk> = :key`` for every primary key column; ``key`` must name exactly the primary key (I-121 a)."""
    primary = {column.key for column in table.primary_key.columns}
    if not isinstance(key, Mapping):
        raise TypeError(f"key must be a mapping of column names to values, not {type(key).__name__}")
    if not primary or set(key) != primary:
        raise UsageError("rowedit.primary_key_required")
    pairs = _columns(table, key, "key")
    if any(value is None for _column, value in pairs):
        raise ValueError("a primary key value must not be None")
    return [column == _bound(column, value) for column, value in pairs]


def _unchanged(table: sqlalchemy.Table, old: object) -> list[ColumnElement[bool]]:
    """The optimistic predicate; ``old`` must name at least one column (I-121 a, review L-1 of #78): without it an
    update or delete would win over every concurrent change."""
    pairs = _columns(table, old, "old")
    if not pairs:
        raise UsageError("rowedit.old_required")
    return [_Unchanged(column, _bound(column, value)) for column, value in pairs]


def insert_row(table: sqlalchemy.Table, values: Mapping[str, Any]) -> sqlalchemy.Insert:
    """Since 0.3, stable. ``INSERT INTO <table> (<columns>) VALUES (<bound values>)``; an empty mapping inserts
    the defaults. Run it with ``expect_rowcount=1``."""
    table = _table(table)
    pairs = _columns(table, values, "values")
    return sqlalchemy.insert(table).values({column: _bound(column, value) for column, value in pairs})


def update_row(
    table: sqlalchemy.Table, key: Mapping[str, Any], old: Mapping[str, Any], new: Mapping[str, Any]
) -> sqlalchemy.Update:
    """Since 0.3, stable. ``UPDATE <table> SET <new> WHERE <pk> = :key AND <column> IS NOT DISTINCT FROM :old``
    (SQLite ``IS``) for every column of ``old``. ``key`` is exactly the primary key, else
    ``UsageError("rowedit.primary_key_required")``; ``old`` names at least one column, else
    ``UsageError("rowedit.old_required")`` (pass every column the user was shown); ``new`` names at least one column.
    Run it with ``expect_rowcount=1``: 0 rows is an optimistic conflict."""
    table = _table(table)
    where = _key(table, key) + _unchanged(table, old)
    pairs = _columns(table, new, "new")
    if not pairs:
        raise ValueError("new must name at least one column")
    return sqlalchemy.update(table).where(*where).values({column: _bound(column, value) for column, value in pairs})


def delete_row(table: sqlalchemy.Table, key: Mapping[str, Any], old: Mapping[str, Any]) -> sqlalchemy.Delete:
    """Since 0.3, stable. ``DELETE FROM <table> WHERE <pk> = :key AND <column> IS NOT DISTINCT FROM :old``
    (SQLite ``IS``) for every column of ``old``; ``key`` and ``old`` as for :func:`update_row`. Run it with
    ``expect_rowcount=1``: 0 rows is an optimistic conflict."""
    table = _table(table)
    return sqlalchemy.delete(table).where(*_key(table, key), *_unchanged(table, old))
