"""DDL statements from a level-2 schema model (since 0.3, experimental, extra ``sqlalchemy``; TAB-XW-3, CR-DBX).

Contract section 5.12 and implementation note I-123; ADR 0015 sections 1 and 10. The functions turn the shapes of
:mod:`tabrion.schema` (level 2) into DDL for SQLite and PostgreSQL and return it as :class:`DdlStatement` values:
the SQL text, compiled by SQLAlchemy's dialect compiler from SQLAlchemy Core constructs (``CreateTable``,
``CreateIndex``, ``AddConstraint`` ...) and, for column changes, from Alembic's DDL constructs (``AddColumn``,
``ColumnType`` ...; extra ``migrations``). Identifiers are quoted by the dialect's preparer; nothing is executed and
no connection is used. Diff and plan are ``tabrion.plan`` (TAB-XW-4), execution is TAB-XW-5::

    for statement in tabrion.ddl.create_statements(model, dialect="sqlite"):
        print(statement.sql)

**Schema text.** A column default, a check expression, an index expression and a partial-index predicate are SQL
text taken from the model, which normally comes from reflection (``tabrion.schema.snapshot``). They are schema, not
data, and they are written into the statement as they are. Tabrion checks them so that they cannot leave their place
in the statement (no ``;``, no comment, balanced parentheses, no top-level comma; on PostgreSQL also no backslash and
no ``$`` outside a literal) and refuses them otherwise (``UsageError("ddl.unsafe_sql_text")``); beyond that they are
trusted. A consumer must show them in the preview and never take a model from an untrusted source.

**Fail closed.** A shape with a feature this module cannot generate is refused
(``UsageError("ddl.unsupported_feature")``) instead of being generated without it: identity and generated columns,
PostgreSQL exclusion constraints, ``NOT VALID`` constraints, invalid indexes, unknown index methods, and every shape
field this module does not know (ADR 0015 D-5.3), so a field added to the model later is refused until it is
generated.

**Names.** A table is ``schema.table`` with exactly one dot; a name with more (a dot in the schema or table name)
does not say which object it names and is refused (``UsageError("ddl.invalid_shape")``, review of #87, M-1).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
import dataclasses
from dataclasses import dataclass
import functools
from types import ModuleType
from typing import Any, Literal

try:
    import sqlalchemy
except ImportError:
    raise ImportError(
        'tabrion.ddl needs the extra "sqlalchemy": '
        'pip install "tabrion[sqlalchemy] @ git+https://github.com/peterkarlhuber/tabrion@<commit-sha>"'
        " (Tabrion is not on PyPI: pin the commit you use)"
    ) from None

from sqlalchemy.dialects.postgresql.base import PGDialect
from sqlalchemy.dialects.sqlite.base import SQLiteDialect
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.schema import AddConstraint, CreateIndex, CreateTable, DropConstraint, DropIndex, DropTable

from tabrion.errors import UsageError
from tabrion.schema import (
    CheckShape,
    ColumnShape,
    ForeignKeyShape,
    IndexShape,
    SchemaSnapshot,
    TableShape,
    UniqueShape,
)

__all__ = [
    "DdlStatement",
    "add_column_statement",
    "add_constraint_statement",
    "alter_column_statements",
    "create_index_statement",
    "create_statements",
    "drop_column_statement",
    "drop_constraint_statement",
    "drop_index_statement",
    "drop_table_statement",
    "table_statements",
]

_Dialect = Literal["sqlite", "postgresql"]

_PG_MAX_IDENTIFIER = 63
"""PostgreSQL's ``NAMEDATALEN - 1``, in bytes; a longer name is refused, never truncated (review L-2 of #8)."""
_MAX_SQL_TEXT = 100_000
"""Longest default, check, expression or predicate text that is accepted."""


@dataclass(frozen=True, slots=True)
class DdlStatement:
    """One DDL statement. ``sql`` is the dialect SQL compiled by SQLAlchemy (identifiers quoted by the dialect, no
    bound values; run it as it is, it is the text a preview shows and a plan hashes); ``target`` is ``schema.table``,
    ``schema.table.column`` or the index or constraint name; ``transactional`` is ``False`` for a statement PostgreSQL
    cannot run in a transaction (``CREATE INDEX CONCURRENTLY``, ``DROP INDEX CONCURRENTLY``)."""

    sql: str
    target: str
    transactional: bool = True


# --- public functions -----------------------------------------------------------------------------------------------


def create_statements(model: SchemaSnapshot, *, dialect: Literal["sqlite", "postgresql"]) -> tuple[DdlStatement, ...]:
    """``CREATE TABLE`` for every table of a level-2 ``model``, referred tables first, then every ``CREATE INDEX``.

    ``UsageError("ddl.level_required")`` for a level-1 model, ``ddl.dialect_mismatch`` when ``model.dialect`` is not
    ``dialect``. On PostgreSQL a foreign key of a cycle between tables is added after the tables
    (``ALTER TABLE ... ADD CONSTRAINT``); SQLite takes every foreign key inline, as it does not check the referred
    table when a table is created."""
    name = _dialect_name(dialect)
    if not isinstance(model, SchemaSnapshot):
        raise TypeError("model must be a SchemaSnapshot")
    if model.level != 2:
        raise UsageError("ddl.level_required")
    if model.dialect != name:
        raise UsageError("ddl.dialect_mismatch")
    _known(model, "")
    builder = _Builder(name)
    # Every table first, then the foreign keys, so a key can refer to a table that comes later in the model.
    tables = [builder.table(_table_shape(shape), foreign_keys=False) for shape in model.tables]
    for table, shape in zip(tables, model.tables, strict=True):
        builder.foreign_keys(shape, table)
    out: list[DdlStatement] = []
    if name == "sqlite":
        out.extend(
            builder.statement(CreateTable(table), shape.name) for table, shape in zip(tables, model.tables, strict=True)
        )
    else:
        shapes = {id(table): shape for table, shape in zip(tables, model.tables, strict=True)}
        deferred: list[sqlalchemy.ForeignKeyConstraint] = []
        order = sqlalchemy.sql.ddl.sort_tables_and_constraints(tables)  # type: ignore[no-untyped-call]
        for table, inline in order:
            if table is None:
                deferred.extend(inline)
                continue
            create = CreateTable(table, include_foreign_key_constraints=list(inline))
            out.append(builder.statement(create, shapes[id(table)].name))
        # SQLAlchemy collects these in a set: sort them, so the same model always gives the same statements.
        deferred.sort(key=lambda c: (str(c.table.fullname), str(c.name or ""), [str(k) for k in c.column_keys]))
        for constraint in deferred:
            target = constraint.name or shapes[id(constraint.table)].name
            out.append(builder.statement(AddConstraint(constraint), str(target)))
    for shape in model.tables:
        out.extend(builder.indexes(shape))
    return tuple(out)


def table_statements(table: TableShape, *, dialect: Literal["sqlite", "postgresql"]) -> tuple[DdlStatement, ...]:
    """``CREATE TABLE`` for one table (columns, defaults, primary key, foreign keys, unique and check constraints)
    and a ``CREATE INDEX`` for each of its indexes. The tables its foreign keys refer to must exist when it runs on
    PostgreSQL."""
    builder = _Builder(_dialect_name(dialect))
    created = builder.table(_table_shape(table))
    return (builder.statement(CreateTable(created), table.name), *builder.indexes(table))


def drop_table_statement(table: TableShape, *, dialect: Literal["sqlite", "postgresql"]) -> DdlStatement:
    """``DROP TABLE`` for ``table``."""
    builder = _Builder(_dialect_name(dialect))
    return builder.statement(DropTable(builder.bare(_table_shape(table), ())), table.name)


def add_column_statement(
    table: TableShape, column: ColumnShape, *, dialect: Literal["sqlite", "postgresql"]
) -> DdlStatement:
    """``ALTER TABLE ... ADD COLUMN`` for ``column`` (with its type, nullability and default) on ``table`` (extra
    ``migrations``: Alembic's ``AddColumn``). Whether the engine can add it (SQLite refuses a ``NOT NULL`` column
    without a default and a non-constant default) is the plan's concern (TAB-XW-4)."""
    builder = _Builder(_dialect_name(dialect))
    shape = _table_shape(table)
    builder.check_column(shape, _column_shape(column))
    alembic = _alembic()
    holder = builder.bare(shape, ())
    created = builder.column(shape, column)
    holder.append_column(created)
    schema, name = builder.split(shape.name)
    return builder.statement(alembic.AddColumn(name, created, schema=schema), f"{shape.name}.{column.name}")


def drop_column_statement(table: TableShape, column: str, *, dialect: Literal["sqlite", "postgresql"]) -> DdlStatement:
    """``ALTER TABLE ... DROP COLUMN`` (extra ``migrations``: Alembic's ``DropColumn``). SQLite can drop a column in
    place only from 3.35 and not one that is part of a key, an index or a constraint; the plan decides (TAB-XW-4)."""
    builder = _Builder(_dialect_name(dialect))
    shape = _table_shape(table)
    builder.ident(column, shape, "column")
    alembic = _alembic()
    holder = builder.bare(shape, ())
    dropped = sqlalchemy.Column(column, sqlalchemy.types.NullType())
    holder.append_column(dropped)
    schema, name = builder.split(shape.name)
    return builder.statement(alembic.DropColumn(name, dropped, schema=schema), f"{shape.name}.{column}")


def alter_column_statements(
    table: TableShape, old: ColumnShape, new: ColumnShape, *, dialect: Literal["sqlite", "postgresql"]
) -> tuple[DdlStatement, ...]:
    """The statements that turn column ``old`` of ``table`` into ``new``, in this order: rename, type, nullability,
    default (extra ``migrations``: Alembic's ``ColumnName``, ``ColumnType``, ``ColumnNullable``, ``ColumnDefault``);
    empty when nothing differs. The type is changed without ``USING``: a type PostgreSQL cannot cast by assignment
    fails when the statement runs. SQLite can only rename a column in place; any other change raises
    ``UsageError("ddl.rebuild_required")`` (the plan rebuilds the table, TAB-XW-4)."""
    name = _dialect_name(dialect)
    builder = _Builder(name)
    shape = _table_shape(table)
    old, new = _column_shape(old), _column_shape(new)
    builder.plain_column(shape, old)
    builder.check_column(shape, new)
    builder.ident(old.name, shape)
    changes = (old.type != new.type, old.nullable != new.nullable, old.default != new.default)
    if name == "sqlite" and any(changes):
        raise UsageError("ddl.rebuild_required", details={"table": shape.name, "column": old.name})
    if old.name == new.name and not any(changes):
        return ()
    alembic = _alembic()
    schema, table_name = builder.split(shape.name)
    target = f"{shape.name}.{new.name}"
    out = []
    if old.name != new.name:
        out.append(builder.statement(alembic.ColumnName(table_name, old.name, new.name, schema=schema), target))
    if changes[0]:
        type_text = builder.type_text(shape, new)[0]
        out.append(
            builder.statement(alembic.ColumnType(table_name, new.name, _TypeText(type_text), schema=schema), target)
        )
    if changes[1]:
        out.append(builder.statement(alembic.ColumnNullable(table_name, new.name, new.nullable, schema=schema), target))
    if changes[2]:
        default = None if new.default is None else builder.default(shape, new)
        out.append(builder.statement(alembic.ColumnDefault(table_name, new.name, default, schema=schema), target))
    return tuple(out)


def add_constraint_statement(
    table: TableShape,
    constraint: ForeignKeyShape | UniqueShape | CheckShape,
    *,
    dialect: Literal["sqlite", "postgresql"],
) -> DdlStatement:
    """``ALTER TABLE ... ADD CONSTRAINT`` for a foreign key, unique or check constraint on ``table`` (PostgreSQL;
    the columns it names are taken from ``table``). SQLite cannot add a constraint in place:
    ``UsageError("ddl.rebuild_required")``."""
    name = _dialect_name(dialect)
    builder = _Builder(name)
    shape = _table_shape(table)
    if not isinstance(constraint, ForeignKeyShape | UniqueShape | CheckShape):
        raise TypeError("constraint must be a ForeignKeyShape, UniqueShape or CheckShape")
    if name == "sqlite":
        raise UsageError("ddl.rebuild_required", details={"table": shape.name})
    created = builder.bare(shape, shape.columns)
    element = builder.constraint(shape, created, constraint)
    return builder.statement(AddConstraint(element), constraint.name or shape.name)


def drop_constraint_statement(
    table: TableShape, name: str, *, dialect: Literal["sqlite", "postgresql"]
) -> DdlStatement:
    """``ALTER TABLE ... DROP CONSTRAINT name`` (PostgreSQL). SQLite cannot drop a constraint in place:
    ``UsageError("ddl.rebuild_required")``."""
    dialect_name = _dialect_name(dialect)
    builder = _Builder(dialect_name)
    shape = _table_shape(table)
    builder.ident(name, shape)
    if dialect_name == "sqlite":
        raise UsageError("ddl.rebuild_required", details={"table": shape.name})
    holder = builder.bare(shape, ())
    element = sqlalchemy.CheckConstraint(sqlalchemy.true(), name=name)
    holder.append_constraint(element)
    return builder.statement(DropConstraint(element), name)


def create_index_statement(
    table: TableShape, index: IndexShape, *, dialect: Literal["sqlite", "postgresql"], concurrently: bool = False
) -> DdlStatement:
    """``CREATE INDEX`` for ``index`` on ``table``. ``concurrently=True`` (PostgreSQL only, else
    ``UsageError("ddl.unsupported_feature")``) writes ``CREATE INDEX CONCURRENTLY``, which is not transactional."""
    builder = _Builder(_dialect_name(dialect))
    shape = _table_shape(table)
    return builder.index(shape, _index_shape(index), concurrently=_flag(concurrently, builder, shape))


def drop_index_statement(
    table: TableShape, name: str, *, dialect: Literal["sqlite", "postgresql"], concurrently: bool = False
) -> DdlStatement:
    """``DROP INDEX`` for the index ``name`` of ``table`` (the index lives in the table's schema).
    ``concurrently=True`` as in :func:`create_index_statement`."""
    builder = _Builder(_dialect_name(dialect))
    shape = _table_shape(table)
    builder.ident(name, shape)
    concurrent = _flag(concurrently, builder, shape)
    holder = builder.bare(shape, ())
    holder.append_column(sqlalchemy.Column("_", sqlalchemy.types.NullType()))
    options: dict[str, Any] = {"postgresql_concurrently": True} if concurrent else {}
    element = sqlalchemy.Index(name, holder.c["_"], **options)
    return builder.statement(DropIndex(element), name, transactional=not concurrent)


# --- checks of the model ----------------------------------------------------------------------------------------


def _dialect_name(dialect: object) -> _Dialect:
    if dialect == "sqlite":
        return "sqlite"
    if dialect == "postgresql":
        return "postgresql"
    raise UsageError("ddl.unsupported_dialect")


def _table_shape(table: object) -> TableShape:
    if not isinstance(table, TableShape):
        raise TypeError("table must be a TableShape")
    return table


def _column_shape(column: object) -> ColumnShape:
    if not isinstance(column, ColumnShape):
        raise TypeError("column must be a ColumnShape")
    return column


def _index_shape(index: object) -> IndexShape:
    if not isinstance(index, IndexShape):
        raise TypeError("index must be an IndexShape")
    return index


def _flag(concurrently: object, builder: _Builder, shape: TableShape) -> bool:
    if not isinstance(concurrently, bool):
        raise TypeError("concurrently must be a bool")
    if concurrently and builder.name != "postgresql":
        raise UsageError("ddl.unsupported_feature", details={"table": shape.name, "feature": "concurrently"})
    return concurrently


_KNOWN_FIELDS: dict[type, frozenset[str]] = {
    SchemaSnapshot: frozenset({"dialect", "tables", "level"}),
    TableShape: frozenset(
        {
            "name",
            "columns",
            "primary_key",
            "foreign_keys",
            "unique_constraints",
            "check_constraints",
            "indexes",
            "exclusion_constraints",
        }
    ),
    # identity, generated and generation_expression are known in order to be refused (_Builder.plain_column)
    ColumnShape: frozenset({"name", "type", "nullable", "default", "identity", "generated", "generation_expression"}),
    ForeignKeyShape: frozenset(
        {
            "name",
            "columns",
            "referred_table",
            "referred_columns",
            "on_delete",
            "on_update",
            "deferrable",
            "initially_deferred",
            "match",
            "validated",
        }
    ),
    UniqueShape: frozenset(
        {"name", "columns", "include", "nulls_not_distinct", "deferrable", "initially_deferred", "collations"}
    ),
    CheckShape: frozenset({"name", "sqltext", "validated"}),
    IndexShape: frozenset(
        {
            "name",
            "columns",
            "unique",
            "ordering",
            "where",
            "include",
            "nulls_not_distinct",
            "expressions",
            "collations",
            "opclasses",
            "method",
            "valid",
        }
    ),
}
"""The fields of each shape this module generates or refuses by name (ADR 0015 D-5.3). A field that is not listed
and holds a value other than its default is refused (``ddl.unsupported_feature``), so a field added to the model
later fails closed until this module learns it."""


def _known(shape: object, table: str) -> None:
    known = _KNOWN_FIELDS.get(type(shape))
    if known is None:
        raise TypeError("not a schema shape")
    for field in dataclasses.fields(shape):  # type: ignore[arg-type]
        if field.name in known:
            continue
        value = getattr(shape, field.name)
        default = field.default_factory() if field.default_factory is not dataclasses.MISSING else field.default
        if value != default:
            feature = f"{type(shape).__name__}.{field.name}"
            raise UsageError("ddl.unsupported_feature", details={"table": table, "feature": feature})


def _unsupported(table: str, feature: str) -> UsageError:
    return UsageError("ddl.unsupported_feature", details={"table": table, "feature": feature})


def _invalid(table: str, field: str) -> UsageError:
    return UsageError("ddl.invalid_shape", details={"table": table, "field": field})


def _unsafe(table: str, element: str) -> UsageError:
    return UsageError("ddl.unsafe_sql_text", details={"table": table, "element": element})


def _strings(values: object, table: str, field: str) -> tuple[str, ...]:
    if not isinstance(values, tuple) or not all(isinstance(v, str) for v in values):
        raise _invalid(table, field)
    return values


def _check_sql_text(text: object, dialect: _Dialect, *, table: str, element: str) -> str:
    """``text`` when it is a non-empty schema text that cannot leave its place in a statement, else
    ``UsageError("ddl.unsafe_sql_text")``: no NUL, at most ``_MAX_SQL_TEXT`` characters, no ``;``, ``--`` or ``/*``
    outside a literal or quoted identifier, every quote closed, parentheses balanced and never below zero, no comma
    outside parentheses; on PostgreSQL no backslash at all (``E''`` escapes) and no ``$`` outside a literal (dollar
    quoting). The scan follows the dialect's quoting: PostgreSQL ``'`` and ``"``, SQLite also ``\\``` and ``[...]``.
    Linear in the length (each character is looked at a bounded number of times)."""
    if not isinstance(text, str) or not text.strip():
        raise _invalid(table, element)
    if len(text) > _MAX_SQL_TEXT or "\x00" in text or (dialect == "postgresql" and "\\" in text):
        raise _unsafe(table, element)
    quotes = "'\"" if dialect == "postgresql" else "'\"`["
    depth, i, n = 0, 0, len(text)
    while i < n:
        ch = text[i]
        if ch in quotes:
            close = "]" if ch == "[" else ch
            end = text.find(close, i + 1)
            while end != -1 and close != "]" and text[end + 1 : end + 2] == close:  # a doubled quote
                end = text.find(close, end + 2)
            if end == -1:
                raise _unsafe(table, element)
            i = end + 1
            continue
        if ch == ";" or text.startswith("--", i) or text.startswith("/*", i):
            raise _unsafe(table, element)
        if ch == "$" and dialect == "postgresql":
            raise _unsafe(table, element)
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                raise _unsafe(table, element)
        elif ch == "," and depth == 0:
            raise _unsafe(table, element)
        i += 1
    if depth:
        raise _unsafe(table, element)
    if dialect == "sqlite" and text.lstrip()[:7].upper() == "CREATE ":
        # tabrion.schema's fallback for a SQLite text it could not split: the whole statement stands for the part.
        raise _unsupported(table, "unparsed_sql_text")
    return text


_ORDERING = {
    "": (None, None),
    "ASC": (False, None),
    "DESC": (True, None),
    "NULLS FIRST": (None, "first"),
    "NULLS LAST": (None, "last"),
    "ASC NULLS FIRST": (False, "first"),
    "ASC NULLS LAST": (False, "last"),
    "DESC NULLS FIRST": (True, "first"),
    "DESC NULLS LAST": (True, "last"),
}
_ACTIONS = frozenset({"CASCADE", "SET NULL", "SET DEFAULT", "RESTRICT"})
_PG_METHODS = frozenset({"hash", "gist", "gin", "spgist", "brin"})
"""The PostgreSQL core access methods besides ``btree`` (``None`` in the shape); an extension's method is refused."""
_INTERVAL_FIELDS = (
    "YEAR",
    "MONTH",
    "DAY",
    "HOUR",
    "MINUTE",
    "SECOND",
    "YEAR TO MONTH",
    "DAY TO HOUR",
    "DAY TO MINUTE",
    "DAY TO SECOND",
    "HOUR TO MINUTE",
    "HOUR TO SECOND",
    "MINUTE TO SECOND",
)
_SERIAL = {"INTEGER": "SERIAL", "BIGINT": "BIGSERIAL", "SMALLINT": "SMALLSERIAL"}


@functools.cache
def _dialect(name: _Dialect) -> sqlalchemy.Dialect:
    # The named paramstyle: a pyformat dialect would double every % of a literal in the text, which is wrong for a
    # statement that runs without parameters.
    if name == "postgresql":
        return PGDialect(paramstyle="named")  # type: ignore[no-untyped-call]
    return SQLiteDialect()


@functools.cache
def _type_names(name: _Dialect) -> frozenset[str]:
    """The type names the dialect reflects and compiles (``ischema_names`` and what their types compile to,
    without arguments), upper case, single blanks."""
    dialect = _dialect(name)
    names: set[str] = set()
    for key, cls in getattr(dialect, "ischema_names", {}).items():
        if key.replace(" ", "").replace("_", "").isalnum() and key.isascii():
            names.add(" ".join(key.upper().split()))
        try:
            compiled = str(cls().compile(dialect=dialect))
        except Exception:  # noqa: S112 - a type that needs arguments (ARRAY): its name is in ischema_names
            continue
        head, _, tail = compiled.partition("(")
        names.add(" ".join((head + " " + tail.partition(")")[2]).upper().split()))
    if name == "postgresql":
        names.update(f"INTERVAL {field}" for field in _INTERVAL_FIELDS)
    return frozenset(names)


def _type_tokens(text: str) -> list[tuple[str, str]] | None:
    out: list[tuple[str, str]] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
        elif ch.isascii() and (ch.isalpha() or ch == "_"):
            end = i
            while end < n and text[end].isascii() and (text[end].isalnum() or text[end] == "_"):
                end += 1
            out.append(("word", text[i:end]))
            i = end
        elif ch.isascii() and ch.isdigit():
            end = i
            while end < n and text[end].isascii() and text[end].isdigit():
                end += 1
            out.append(("int", text[i:end]))
            i = end
        elif ch in "(),[]":
            out.append((ch, ch))
            i += 1
        elif ch == '"':
            end = text.find('"', i + 1)
            while end != -1 and text[end + 1 : end + 2] == '"':
                end = text.find('"', end + 2)
            if end == -1:
                return None
            out.append(("ident", text[i + 1 : end].replace('""', '"')))
            i = end + 1
        else:
            return None
    return out


def _parse_type(text: str, dialect: _Dialect) -> tuple[str, str | None] | None:
    """``(text, collation)`` when ``text`` is a type the dialect knows, written as SQLAlchemy compiles it: a name of
    :func:`_type_names`, one optional argument list of one or two integers, PostgreSQL array brackets and an
    optional ``COLLATE`` with one name; else ``None``."""
    if len(text) > 1000 or "\x00" in text:
        return None
    tokens = _type_tokens(text)
    if not tokens:
        return None
    words: list[str] = []
    collation: str | None = None
    pos, args, arrays = 0, False, False
    while pos < len(tokens):
        kind, value = tokens[pos]
        if kind == "word" and value.upper() == "COLLATE":
            if pos + 2 != len(tokens) or tokens[pos + 1][0] not in ("word", "ident"):
                return None
            collation = tokens[pos + 1][1]
            break
        if kind == "word" and not arrays:  # the name, also after the arguments: TIMESTAMP(3) WITH TIME ZONE
            words.append(value.upper())
            pos += 1
        elif kind == "(" and not args and not arrays and words:
            close = pos + 2 if tokens[pos + 1 : pos + 2] and tokens[pos + 1][0] == "int" else -1
            if close != -1 and tokens[close : close + 1] == [(",", ",")]:
                close = close + 2 if tokens[close + 1 : close + 2] and tokens[close + 1][0] == "int" else -1
            if close == -1 or tokens[close : close + 1] != [(")", ")")]:
                return None
            args, pos = True, close + 1
        elif kind == "[" and words and dialect == "postgresql":
            close = pos + 2 if tokens[pos + 1 : pos + 2] and tokens[pos + 1][0] == "int" else pos + 1
            if tokens[close : close + 1] != [("]", "]")]:
                return None
            arrays, pos = True, close + 1
        else:
            return None
    if " ".join(words) not in _type_names(dialect):
        return None
    return text.strip(), collation


# --- building the SQLAlchemy constructs -----------------------------------------------------------------------------


class _TypeText(sqlalchemy.types.UserDefinedType[Any]):
    """A column type written as the checked type text of the model (:func:`_parse_type`)."""

    cache_ok = True

    def __init__(self, text: str) -> None:
        self.text = text

    def get_col_spec(self, **kw: Any) -> str:
        return self.text


class _DefaultText(sqlalchemy.sql.expression.ClauseElement):
    """A checked default text, already in parentheses, written as it is. Not a ``ColumnElement`` and not a
    ``text()``: SQLAlchemy's SQLite compiler puts parentheses around a ``ColumnElement`` default in 2.0.30 and decides
    by the text's first character in 2.1, and ``text()`` would read ``:name`` as a bound parameter."""

    __visit_name__ = "tabrion_default_text"
    inherit_cache = False

    def __init__(self, text: str) -> None:
        self.text = text


@compiles(_DefaultText)
def _compile_default_text(element: _DefaultText, compiler: Any, **kw: Any) -> str:
    return element.text


def _alembic() -> ModuleType:
    try:
        from alembic.ddl import base
    except ImportError:
        raise ImportError(
            'tabrion.ddl column statements need the extra "migrations" (Alembic): '
            'pip install "tabrion[migrations] @ git+https://github.com/peterkarlhuber/tabrion@<commit-sha>"'
            " (Tabrion is not on PyPI: pin the commit you use)"
        ) from None
    return base


@functools.cache
def _unique_option_renders(option: str, marker: str) -> bool:
    """Whether this SQLAlchemy writes a PostgreSQL option of a unique constraint: 2.0.30 (the floor of the extra)
    refuses ``postgresql_include`` and accepts ``postgresql_nulls_not_distinct`` without writing it."""
    columns = [sqlalchemy.Column(name, sqlalchemy.Integer()) for name in ("a", "b")]
    table = sqlalchemy.Table("t", sqlalchemy.MetaData(), *columns)
    try:
        options: dict[str, Any] = {option: ["b"] if "include" in option else True}
        table.append_constraint(sqlalchemy.UniqueConstraint("a", name="u", **options))
    except sqlalchemy.exc.ArgumentError:
        return False
    return marker in str(CreateTable(table).compile(dialect=_dialect("postgresql")))


class _Builder:
    """Turns shapes into SQLAlchemy Core constructs of one dialect, in one private ``MetaData``."""

    def __init__(self, name: _Dialect) -> None:
        self.name: _Dialect = name
        self.dialect = _dialect(name)
        self.preparer = self.dialect.identifier_preparer
        self.metadata = sqlalchemy.MetaData()
        self.stand_ins: set[str] = set()

    # names ------------------------------------------------------------------------------------------------------

    def ident(self, name: object, shape: TableShape, field: str = "name") -> str:
        if not isinstance(name, str) or not name or "\x00" in name:
            raise _invalid(shape.name, field)
        if self.name == "postgresql" and len(name.encode("utf-8")) > _PG_MAX_IDENTIFIER:
            raise UsageError("ddl.identifier_too_long", details={"table": shape.name, "field": field})
        return name

    def split(self, qualified: str) -> tuple[str | None, str]:
        """``(schema, table)`` of ``schema.table``; SQLite's ``main`` becomes no schema, so the statements name the
        table unqualified. A name without exactly one dot is refused: ``s.x.y`` could be ``s."x.y"`` or
        ``"s.x".y`` (review of #87, M-1)."""
        schema, dot, table = qualified.rpartition(".")
        if not dot or not schema or not table or "." in schema:
            raise UsageError("ddl.invalid_shape", details={"table": qualified, "field": "name"})
        return (None if self.name == "sqlite" and schema == "main" else schema), table

    # tables -------------------------------------------------------------------------------------------------------

    def bare(self, shape: TableShape, columns: Iterable[ColumnShape]) -> sqlalchemy.Table:
        """A table of the private metadata with ``columns`` and nothing else; without columns, the table already
        built for ``shape`` when there is one."""
        if not isinstance(shape.name, str):
            raise _invalid("", "name")
        _known(shape, shape.name)
        schema, name = self.split(shape.name)
        self.ident(name, shape)
        if schema is not None:
            self.ident(schema, shape, "schema")
        created = [self.column(shape, column) for column in columns]
        key = f"{schema}.{name}" if schema is not None else name
        if key in self.metadata.tables:
            if created:
                raise _invalid(shape.name, "name")  # the same table twice in one model
            return self.metadata.tables[key]
        return sqlalchemy.Table(name, self.metadata, *created, schema=schema)

    def table(self, shape: TableShape, *, foreign_keys: bool = True) -> sqlalchemy.Table:
        shape = _table_shape(shape)
        columns = shape.columns
        if not isinstance(columns, tuple) or not columns:
            raise _invalid(shape.name, "columns")
        for column in columns:
            self.check_column(shape, _column_shape(column))
        names = [column.name for column in columns]
        if len(set(names)) != len(names):
            raise _invalid(shape.name, "columns")
        created = self.bare(shape, columns)
        primary_key = _strings(shape.primary_key, shape.name, "primary_key")
        if primary_key:
            self.columns_of(shape, primary_key, "primary_key")
            created.append_constraint(sqlalchemy.PrimaryKeyConstraint(*primary_key))
        if shape.exclusion_constraints:
            raise _unsupported(shape.name, "exclusion_constraint")
        items: tuple[UniqueShape | CheckShape, ...] = (*shape.unique_constraints, *shape.check_constraints)
        for item in items:
            self.constraint(shape, created, item)
        if foreign_keys:
            self.foreign_keys(shape, created)
        return created

    def foreign_keys(self, shape: TableShape, created: sqlalchemy.Table) -> None:
        if not isinstance(shape.foreign_keys, tuple):
            raise _invalid(shape.name, "foreign_keys")
        for item in shape.foreign_keys:
            self.constraint(shape, created, item)

    def columns_of(self, shape: TableShape, names: Sequence[str], field: str) -> None:
        known = {column.name for column in shape.columns}
        if not names or any(name not in known for name in names):
            raise _invalid(shape.name, field)

    # columns ------------------------------------------------------------------------------------------------------

    def plain_column(self, shape: TableShape, column: ColumnShape) -> None:
        """Refuse an identity or generated column: neither is generated yet, and a plain column in its place would
        have other semantics (review of #87, M-2; ADR 0015 D-5.3)."""
        if column.identity is not None:
            raise _unsupported(shape.name, "identity_column")
        if column.generated is not None or column.generation_expression is not None:
            raise _unsupported(shape.name, "generated_column")

    def check_column(self, shape: TableShape, column: ColumnShape) -> None:
        self.plain_column(shape, column)
        _known(column, shape.name)
        self.ident(column.name, shape, "column")
        if not isinstance(column.nullable, bool):
            raise _invalid(shape.name, "nullable")
        self.type_text(shape, column)
        if column.default is not None:
            _check_sql_text(column.default, self.name, table=shape.name, element="default")

    def type_text(self, shape: TableShape, column: ColumnShape) -> tuple[str, str | None]:
        parsed = _parse_type(column.type, self.name) if isinstance(column.type, str) else None
        if parsed is None:
            raise UsageError("ddl.unsupported_type", details={"table": shape.name, "column": str(column.name)})
        if parsed[1] is not None:
            self.ident(parsed[1], shape, "collation")
        return parsed

    def default(self, shape: TableShape, column: ColumnShape) -> _DefaultText:
        # Always in parentheses: SQLite takes only a literal or a parenthesized expression after DEFAULT and keeps
        # the text inside the outer parentheses as the default, so the text reflects back unchanged.
        text = _check_sql_text(column.default, self.name, table=shape.name, element="default")
        return _DefaultText(f"({text})")

    def serial(self, shape: TableShape, column: ColumnShape, type_text: str) -> str | None:
        """``SERIAL``, ``BIGSERIAL`` or ``SMALLSERIAL`` for a PostgreSQL ``NOT NULL`` integer column whose default is
        ``nextval`` of the sequence a serial column of that name gets (``<table>_<column>_seq``, plain lower-case
        names only), so the table does not depend on a sequence created elsewhere; else ``None``."""
        serial = _SERIAL.get(type_text.upper())
        if self.name != "postgresql" or serial is None or column.nullable or column.default is None:
            return None
        schema, table = self.split(shape.name)
        sequence = f"{table}_{column.name}_seq"
        plain = (schema or "") + sequence
        if not (plain.isascii() and plain.replace("_", "a").isalnum() and plain == plain.lower()):
            return None
        if len(sequence) > _PG_MAX_IDENTIFIER or sequence[0].isdigit() or (schema or "a")[0].isdigit():
            return None
        # PostgreSQL writes the sequence unqualified when its schema is on the search path, else qualified (the schema
        # name quoted or not, depending on the server's deparse).
        forms = (sequence, f"{schema}.{sequence}", f'"{schema}".{sequence}')
        if column.default not in {f"nextval('{form}'::regclass)" for form in forms}:
            return None
        return serial

    def column(self, shape: TableShape, column: ColumnShape) -> sqlalchemy.Column[Any]:
        self.check_column(shape, column)
        type_text = self.type_text(shape, column)[0]
        serial = self.serial(shape, column, type_text)
        default = None if column.default is None or serial else sqlalchemy.DefaultClause(self.default(shape, column))
        return sqlalchemy.Column(
            column.name,
            _TypeText(serial or type_text),
            nullable=column.nullable,
            server_default=default,
            autoincrement=False,
        )

    # constraints --------------------------------------------------------------------------------------------------

    def constraint(
        self, shape: TableShape, created: sqlalchemy.Table, item: ForeignKeyShape | UniqueShape | CheckShape
    ) -> sqlalchemy.Constraint:
        _known(item, shape.name)
        if item.name is not None:
            self.ident(item.name, shape, "constraint")
        element: sqlalchemy.Constraint
        if isinstance(item, CheckShape):
            if not item.validated:
                raise _unsupported(shape.name, "not_valid")
            text = _check_sql_text(item.sqltext, self.name, table=shape.name, element="check")
            element = sqlalchemy.CheckConstraint(sqlalchemy.literal_column(text), name=item.name)
        elif isinstance(item, UniqueShape):
            element = self.unique(shape, item)
        elif isinstance(item, ForeignKeyShape):
            element = self.foreign_key(shape, created, item)
        else:
            raise TypeError("not a constraint shape")
        created.append_constraint(element)
        return element

    def deferrable(self, shape: TableShape, deferrable: object, deferred: object) -> dict[str, Any]:
        if not isinstance(deferrable, bool) or not isinstance(deferred, bool) or (deferred and not deferrable):
            raise _invalid(shape.name, "deferrable")
        return {"deferrable": True, "initially": "DEFERRED" if deferred else None} if deferrable else {}

    def unique(self, shape: TableShape, item: UniqueShape) -> sqlalchemy.UniqueConstraint:
        columns = _strings(item.columns, shape.name, "unique_constraints")
        self.columns_of(shape, columns, "unique_constraints")
        include = _strings(item.include, shape.name, "unique_constraints")
        collations = _strings(item.collations, shape.name, "unique_constraints")
        options = self.deferrable(shape, item.deferrable, item.initially_deferred)
        if self.name == "sqlite":
            if include:
                raise _unsupported(shape.name, "include")
            if item.nulls_not_distinct:
                raise _unsupported(shape.name, "nulls_not_distinct")
            if options:
                raise _unsupported(shape.name, "deferrable_unique")
            if any(collations):
                # SQLAlchemy's UniqueConstraint names columns only, and a SQLite column type carries no collation.
                raise _unsupported(shape.name, "unique_collation")
            return sqlalchemy.UniqueConstraint(*columns, name=item.name)
        if collations:
            types = {column.name: column for column in shape.columns}
            implied = [self.type_text(shape, types[c])[1] or "" for c in columns]
            if len(collations) != len(columns) or list(collations) != implied:
                # PostgreSQL takes a unique constraint's collation from its columns' types.
                raise _unsupported(shape.name, "unique_collation")
        if include:
            self.columns_of(shape, include, "unique_constraints")
            if not _unique_option_renders("postgresql_include", "INCLUDE"):
                raise _unsupported(shape.name, "unique_include")
            options["postgresql_include"] = list(include)
        if item.nulls_not_distinct is not False:
            if item.nulls_not_distinct is not True:
                raise _invalid(shape.name, "unique_constraints")
            if not _unique_option_renders("postgresql_nulls_not_distinct", "NULLS NOT DISTINCT"):
                raise _unsupported(shape.name, "unique_nulls_not_distinct")
            options["postgresql_nulls_not_distinct"] = True
        return sqlalchemy.UniqueConstraint(*columns, name=item.name, **options)

    def foreign_key(
        self, shape: TableShape, created: sqlalchemy.Table, item: ForeignKeyShape
    ) -> sqlalchemy.ForeignKeyConstraint:
        columns = _strings(item.columns, shape.name, "foreign_keys")
        referred_columns = _strings(item.referred_columns, shape.name, "foreign_keys")
        self.columns_of(shape, columns, "foreign_keys")
        if len(referred_columns) != len(columns) or not isinstance(item.referred_table, str):
            raise _invalid(shape.name, "foreign_keys")
        if not item.validated:
            raise _unsupported(shape.name, "not_valid")
        actions: dict[str, Any] = {}
        for key, action in (("ondelete", item.on_delete), ("onupdate", item.on_update)):
            if action is not None:
                if action not in _ACTIONS:
                    raise _invalid(shape.name, "foreign_keys")
                actions[key] = action
        if item.match is not None:
            if self.name != "postgresql" or item.match != "FULL":
                raise _unsupported(shape.name, f"match_{str(item.match).lower()}")
            actions["match"] = "FULL"
        options = self.deferrable(shape, item.deferrable, item.initially_deferred)
        schema, table = self.split(item.referred_table)
        own_schema = self.split(shape.name)[0]
        if self.name == "sqlite" and schema != own_schema:
            # SQLite's REFERENCES takes no schema; SQLAlchemy would leave such a key out without a word.
            raise _unsupported(shape.name, "cross_schema_foreign_key")
        referred = self.referred(shape, schema, table, referred_columns)
        return sqlalchemy.ForeignKeyConstraint(
            list(columns), [referred.c[c] for c in referred_columns], name=item.name, **actions, **options
        )

    def referred(self, shape: TableShape, schema: str | None, table: str, columns: Sequence[str]) -> sqlalchemy.Table:
        """The referred table: a table of the model, or a stand-in with only the referred columns for a table outside
        it (one that exists already); compiling a foreign key needs only the names. Stand-ins are never created."""
        self.ident(table, shape, "referred_table")
        if schema is not None:
            self.ident(schema, shape, "referred_table")
        for column in columns:
            self.ident(column, shape, "referred_columns")
        key = f"{schema}.{table}" if schema is not None else table
        found = self.metadata.tables.get(key)
        if found is None:
            found = sqlalchemy.Table(table, self.metadata, schema=schema)
            self.stand_ins.add(key)
        for column in columns:
            if column not in found.c:
                if key not in self.stand_ins:
                    raise _invalid(shape.name, "foreign_keys")  # a column the model's table does not have
                found.append_column(sqlalchemy.Column(column, sqlalchemy.types.NullType()))
        return found

    # indexes ------------------------------------------------------------------------------------------------------

    def indexes(self, shape: TableShape) -> list[DdlStatement]:
        indexes = shape.indexes
        if not isinstance(indexes, tuple):
            raise _invalid(shape.name, "indexes")
        return [self.index(shape, _index_shape(index), concurrently=False) for index in indexes]

    def index(self, shape: TableShape, item: IndexShape, *, concurrently: bool) -> DdlStatement:
        _known(item, shape.name)
        name = self.ident(item.name, shape, "index")
        if not isinstance(item.valid, bool) or not item.valid:
            raise _unsupported(shape.name, "invalid_index")
        columns = _strings(item.columns, shape.name, "indexes")
        count = len(columns)
        expressions = item.expressions
        if (
            not count
            or not isinstance(expressions, tuple)
            or any(type(i) is not int or not 0 <= i < count for i in expressions)
        ):
            raise _invalid(shape.name, "indexes")
        ordering = self.per_element(shape, item.ordering, count)
        collations = self.per_element(shape, item.collations, count)
        opclasses = self.per_element(shape, item.opclasses, count)
        include = _strings(item.include, shape.name, "indexes")
        sqlite = self.name == "sqlite"
        if sqlite and (include or item.nulls_not_distinct or any(opclasses) or item.method is not None):
            feature = "include" if include else "nulls_not_distinct" if item.nulls_not_distinct else "index_method"
            raise _unsupported(shape.name, "opclass" if any(opclasses) else feature)
        if item.method is not None and item.method not in _PG_METHODS:
            raise _unsupported(shape.name, "index_method")
        holder = self.bare(shape, ())
        plain = [columns[i] for i in range(count) if i not in expressions]
        if plain:
            self.columns_of(shape, plain, "indexes")
        elements = []
        for i, element in enumerate(columns):
            if i in expressions:
                text = _check_sql_text(element, self.name, table=shape.name, element="index_expression")
                # PostgreSQL wants an expression element in parentheses (it deparses it its own way when it is
                # reflected); SQLite keeps the text as written, so it gets the text alone.
                clause: Any = sqlalchemy.literal_column(text if sqlite else f"({text})")
            else:
                clause = sqlalchemy.literal_column(self.preparer.quote(element))
            if collations[i]:
                clause = clause.collate(self.ident(collations[i], shape, "collations"))
            if opclasses[i]:
                opclass = self.ident(opclasses[i], shape, "opclasses")
                if not (opclass.isascii() and opclass.replace("_", "a").isalnum() and not opclass[0].isdigit()):
                    raise _invalid(shape.name, "opclasses")
                # The operator class goes after the collation and before the order; SQLAlchemy's postgresql_ops
                # drops it when the element has an order, so the element is compiled first and the class appended.
                compiled = str(clause.compile(dialect=self.dialect, compile_kwargs={"literal_binds": True}))
                clause = sqlalchemy.literal_column(f"{compiled} {self.preparer.quote(opclass)}")
            descending, nulls = _ORDERING.get(ordering[i], (None, "invalid"))
            if nulls == "invalid":
                raise _invalid(shape.name, "ordering")
            if sqlite and nulls is not None:
                raise _unsupported(shape.name, "nulls_order")
            if descending is not None:
                clause = clause.desc() if descending else clause.asc()
            if nulls is not None:
                clause = clause.nulls_first() if nulls == "first" else clause.nulls_last()
            elements.append(clause)
        options: dict[str, Any] = {}
        if item.where is not None:
            where = _check_sql_text(item.where, self.name, table=shape.name, element="index_where")
            options["sqlite_where" if sqlite else "postgresql_where"] = sqlalchemy.literal_column(
                where if sqlite else f"({where})"
            )
        if include:
            self.columns_of(shape, include, "indexes")
            options["postgresql_include"] = list(include)
        if item.nulls_not_distinct is not False:
            if item.nulls_not_distinct is not True:
                raise _invalid(shape.name, "indexes")
            options["postgresql_nulls_not_distinct"] = True
        if item.method is not None:
            options["postgresql_using"] = item.method
        if concurrently:
            options["postgresql_concurrently"] = True
        if not isinstance(item.unique, bool):
            raise _invalid(shape.name, "indexes")
        index = sqlalchemy.Index(name, *elements, unique=item.unique, **options)
        holder.append_constraint(index)  # an index of expressions only has no column that would attach it
        return self.statement(CreateIndex(index), name, transactional=not concurrently)

    def per_element(self, shape: TableShape, values: object, count: int) -> tuple[str, ...]:
        """``values`` with one entry per index element (``()`` means every element has the default)."""
        entries = _strings(values, shape.name, "indexes")
        if entries and len(entries) != count:
            raise _invalid(shape.name, "indexes")
        return entries or ("",) * count

    # output -------------------------------------------------------------------------------------------------------

    def statement(self, construct: Any, target: str, *, transactional: bool = True) -> DdlStatement:
        try:
            sql = str(construct.compile(dialect=self.dialect)).strip()
        except sqlalchemy.exc.SQLAlchemyError:
            raise UsageError("ddl.invalid_shape", details={"table": target, "field": "compile"}) from None
        return DdlStatement(sql=sql, target=target, transactional=transactional)
