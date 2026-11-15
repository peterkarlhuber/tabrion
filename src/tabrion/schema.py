"""Schema fingerprint and comparison over a SQLAlchemy inspector (TAB-CR-2, CR-DB; layer B, extra ``sqlalchemy``).

Contract: ``docs/api/contract.md`` section 5.10 and implementation notes I-110 and I-111. A :class:`SchemaSnapshot`
holds the shape of some tables, never data; :func:`fingerprint` is a stable hash of it, and :func:`compare` names what
differs between an expected and an actual snapshot. Use it with ``Database.inspector()``::

    with db.inspector() as inspector:
        snapshot = tabrion.schema.snapshot(inspector, tables=["public.invoices"])

Level 1 (the default, since 0.2) holds columns with type and nullability and the primary key; its fingerprint starts
with ``v1:``. Level 2 (since 0.3, experimental, TAB-XW-2; ``snapshot(..., level=2)``) adds column defaults, foreign
keys, unique and check constraints and indexes; its fingerprint starts with ``v2:``. A level-1 fingerprint is
byte-identical to the one 0.2 computed (ADR 0015 D-4).

A consumer (Nodestro's ``database`` node, NOD-CR-5) stores the fingerprint (and the snapshot as JSON, for clear
messages) with a release and compares at start-up; drift is reported, never migrated. A level-2 snapshot is also the
target model of the Schema Designer (CR-DBX).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
import contextlib
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Literal, TypeVar
import warnings

try:
    import sqlalchemy
except ImportError:
    raise ImportError(
        'tabrion.schema needs the extra "sqlalchemy": '
        'pip install "tabrion[sqlalchemy] @ git+https://github.com/peterkarlhuber/tabrion@<commit-sha>"'
        " (Tabrion is not on PyPI: pin the commit you use)"
    ) from None

from tabrion.errors import UsageError

__all__ = [
    "CheckShape",
    "ColumnShape",
    "ForeignKeyShape",
    "IndexShape",
    "SchemaDifference",
    "SchemaSnapshot",
    "TableShape",
    "UniqueShape",
    "compare",
    "fingerprint",
    "snapshot",
]

SNAPSHOT_FORMAT = 1
"""The JSON format of a level-1 snapshot (``format`` key); a level-2 snapshot is format 2."""
_MAX_TABLES = 10_000

DifferenceKind = Literal[
    "table_missing",
    "table_added",
    "column_missing",
    "column_added",
    "column_type",
    "column_nullable",
    "column_order",
    "primary_key",
    "column_default",
    "foreign_key_missing",
    "foreign_key_added",
    "unique_missing",
    "unique_added",
    "check_missing",
    "check_added",
    "index_missing",
    "index_added",
]
_BREAKING = frozenset(
    {
        "table_missing",
        "column_missing",
        "column_type",
        "column_nullable",
        "primary_key",
        "column_default",
        "foreign_key_missing",
        "foreign_key_added",
        "unique_missing",
        "unique_added",
        "check_missing",
        "check_added",
        "index_missing",
    }
)
# column_order is not breaking: an application that names its columns does not notice it. Neither is an added table,
# column or index. An added constraint is breaking: it can refuse a write that worked before.

_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class ColumnShape:
    """One column: its name, its type as the dialect writes it (``INTEGER``, ``VARCHAR(20)``) and nullability.

    ``default`` (level 2, since 0.3) is the server default as the dialect reflects it (``0``, ``'x'::character
    varying``, ``nextval('s'::regclass)``, ``CURRENT_TIMESTAMP``), or ``None``; it is schema text, not data."""

    name: str
    type: str
    nullable: bool
    default: str | None = None


@dataclass(frozen=True, slots=True)
class ForeignKeyShape:
    """A foreign key (level 2, since 0.3). ``name`` is ``None`` for an unnamed SQLite constraint; ``referred_table``
    is ``schema.table``. ``on_delete`` and ``on_update`` are ``CASCADE``, ``SET NULL``, ``SET DEFAULT`` or
    ``RESTRICT``; ``None`` is the engine default ``NO ACTION`` (written or not)."""

    name: str | None
    columns: tuple[str, ...]
    referred_table: str
    referred_columns: tuple[str, ...]
    on_delete: str | None = None
    on_update: str | None = None


@dataclass(frozen=True, slots=True)
class UniqueShape:
    """A unique constraint (level 2, since 0.3); ``name`` is ``None`` for an unnamed SQLite constraint. A unique
    index that is not a constraint is an :class:`IndexShape` with ``unique=True``."""

    name: str | None
    columns: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CheckShape:
    """A check constraint (level 2, since 0.3): its name (``None`` for an unnamed SQLite constraint) and its
    expression as the dialect reflects it (PostgreSQL normalizes it, SQLite keeps the text as written)."""

    name: str | None
    sqltext: str


@dataclass(frozen=True, slots=True)
class IndexShape:
    """An index that is not the index of a primary key or unique constraint (level 2, since 0.3). ``columns`` holds
    a column name per key element, or the expression text for an expression element (``lower(note::text)``)."""

    name: str
    columns: tuple[str, ...]
    unique: bool = False


@dataclass(frozen=True, slots=True)
class TableShape:
    """One table: ``schema.table`` (SQLite: ``main.table``), its columns in table order and its primary key; at level
    2 (since 0.3) also its foreign keys, unique and check constraints and indexes (empty at level 1)."""

    name: str
    columns: tuple[ColumnShape, ...]
    primary_key: tuple[str, ...]
    foreign_keys: tuple[ForeignKeyShape, ...] = ()
    unique_constraints: tuple[UniqueShape, ...] = ()
    check_constraints: tuple[CheckShape, ...] = ()
    indexes: tuple[IndexShape, ...] = ()


@dataclass(frozen=True, slots=True)
class SchemaSnapshot:
    """The shape of the tables of one database, sorted by name. Holds no data and no connection details.

    ``level`` (since 0.3) is 1 or 2 (``UsageError("schema.invalid_level")`` otherwise); a level-1 snapshot must not
    hold level-2 detail (a default, a constraint or an index: ``UsageError("schema.level_mismatch")``)."""

    dialect: str
    tables: tuple[TableShape, ...]
    level: Literal[1, 2] = 1

    def __post_init__(self) -> None:
        _check_level(self.level)
        if self.level == 1 and any(_has_level2_detail(table) for table in self.tables):
            raise UsageError("schema.level_mismatch")

    def to_json(self) -> str:
        """Canonical JSON (sorted keys, no blanks); :meth:`from_json` reads it back. Level 1 is format 1, unchanged
        since 0.2; level 2 is format 2, with constraints and indexes in a fixed order (their order does not count)."""
        data = _as_dict(self) if self.level == 1 else _as_dict_v2(self)
        return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    @classmethod
    def from_json(cls, text: str) -> SchemaSnapshot:
        """A snapshot from :meth:`to_json` (format 1 or 2); ``UsageError("schema.invalid_snapshot")`` for anything
        else."""
        snapshot: SchemaSnapshot | None = None
        try:
            data = json.loads(text)
            snapshot = _from_dict_v2(data) if data.get("format") == 2 else _from_dict(data)
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError, UsageError):
            snapshot = None
        del text  # I-76
        if snapshot is None:
            raise UsageError("schema.invalid_snapshot")
        return snapshot

    def table(self, name: str) -> TableShape | None:
        """The table ``schema.table``, or ``None``."""
        for table in self.tables:
            if table.name == name:
                return table
        return None


@dataclass(frozen=True, slots=True)
class SchemaDifference:
    """One difference between an expected and an actual snapshot. ``breaking`` is ``False`` only for an added table,
    column or index, which an application that does not use it does not notice, and for a changed column order
    (``column_order``, named by table), which only a positional ``SELECT *`` notices.

    ``name`` (since 0.3) is the constraint or index name of a level-2 difference (``None`` for an unnamed SQLite
    constraint); ``column`` is set where the difference concerns one column. A changed constraint or index is
    reported as missing and added."""

    kind: DifferenceKind
    table: str
    column: str | None = None
    name: str | None = None

    @property
    def breaking(self) -> bool:
        return self.kind in _BREAKING

    def __str__(self) -> str:
        where = f"{self.table}.{self.column}" if self.column is not None else self.table
        return f"{self.kind}: {where}" if self.name is None else f"{self.kind}: {where} ({self.name})"


def snapshot(
    inspector: sqlalchemy.Inspector,
    *,
    tables: Iterable[str] | None = None,
    schemas: Iterable[str] | None = None,
    level: Literal[1, 2] = 1,
) -> SchemaSnapshot:
    """The shape of ``tables`` (``schema.table``), or of every table of ``schemas`` (default: the default schema).

    A table of ``tables`` that does not exist or is a view is left out, so :func:`compare` reports it as
    ``table_missing``. Views are not included. Raises ``UsageError("schema.too_many_tables")`` above 10 000 tables.
    ``level=2`` (since 0.3, experimental) also reads defaults, foreign keys, unique and check constraints and
    indexes; ``level=1`` stays the default (ADR 0015 D-4)."""
    _check_level(level)
    default = inspector.default_schema_name or "main"
    wanted: list[tuple[str, str]] = []
    if tables is not None:
        for name in tables:
            schema, dot, table = str(name).rpartition(".")
            wanted.append((schema if dot else default, table))
    else:
        for schema in schemas if schemas is not None else [default]:
            wanted.extend((schema, table) for table in inspector.get_table_names(schema=_arg(schema, default)))
    if len(wanted) > _MAX_TABLES:
        raise UsageError("schema.too_many_tables", details={"limit": _MAX_TABLES})
    dialect = inspector.dialect
    shapes: dict[str, TableShape] = {}
    views: dict[str, frozenset[str]] = {}
    for schema, table in wanted:
        if not inspector.has_table(table, schema=_arg(schema, default)):
            continue
        if schema not in views:
            views[schema] = _view_names(inspector, _arg(schema, default))
        if table in views[schema]:
            continue  # has_table is true for a view in SQLAlchemy 2
        reflected = inspector.get_columns(table, schema=_arg(schema, default))
        columns = tuple(
            ColumnShape(
                name=str(c["name"]),
                type=_type_name(c["type"], dialect),
                nullable=bool(c["nullable"]),
                default=_default_text(c.get("default")) if level == 2 else None,
            )
            for c in reflected
        )
        pk = inspector.get_pk_constraint(table, schema=_arg(schema, default))
        primary_key = tuple(str(column) for column in (pk.get("constrained_columns") or ()))
        shape = TableShape(name=f"{schema}.{table}", columns=columns, primary_key=primary_key)
        if level == 2:
            shape = _level2_table(inspector, shape, schema, table, default)
        shapes[shape.name] = shape
    return SchemaSnapshot(dialect=str(dialect.name), tables=tuple(shapes[name] for name in sorted(shapes)), level=level)


def fingerprint(snapshot: SchemaSnapshot) -> str:
    """``v1:sha256:<64 hex>`` (level 1) or ``v2:sha256:<64 hex>`` (level 2) of the snapshot's canonical JSON: equal
    shapes give equal fingerprints.

    Column order counts (it is part of the shape a ``SELECT *`` sees; :func:`compare` names it as ``column_order``);
    the order of constraints and indexes does not. The type text comes from SQLAlchemy's dialect compiler, so a
    SQLAlchemy upgrade that writes a type differently changes the fingerprint; :func:`compare` then names the
    column."""
    prefix = "v1:sha256:" if snapshot.level == 1 else "v2:sha256:"
    return prefix + hashlib.sha256(snapshot.to_json().encode("ascii")).hexdigest()


def compare(expected: SchemaSnapshot, actual: SchemaSnapshot) -> tuple[SchemaDifference, ...]:
    """What ``actual`` lacks or has in addition, compared with ``expected``, in table and column order.

    Each difference names a table and a column (and at level 2 a constraint or index name), never data. Different
    dialects raise ``UsageError("schema.dialect_mismatch")``, different levels ``UsageError("schema.level_mismatch")``.
    """
    if expected.dialect != actual.dialect:
        raise UsageError("schema.dialect_mismatch")
    if expected.level != actual.level:
        raise UsageError("schema.level_mismatch")
    level2 = expected.level == 2
    out: list[SchemaDifference] = []
    have = {table.name: table for table in actual.tables}
    want = {table.name: table for table in expected.tables}
    for name in sorted(set(want) | set(have)):
        old, new = want.get(name), have.get(name)
        if new is None:
            out.append(SchemaDifference("table_missing", name))
            continue
        if old is None:
            out.append(SchemaDifference("table_added", name))
            continue
        new_columns = {column.name: column for column in new.columns}
        old_names = {column.name for column in old.columns}
        for column in old.columns:
            found = new_columns.get(column.name)
            if found is None:
                out.append(SchemaDifference("column_missing", name, column.name))
                continue
            if found.type != column.type:
                out.append(SchemaDifference("column_type", name, column.name))
            elif found.nullable != column.nullable:
                out.append(SchemaDifference("column_nullable", name, column.name))
            if level2 and found.default != column.default:
                out.append(SchemaDifference("column_default", name, column.name))
        out.extend(
            SchemaDifference("column_added", name, column.name)
            for column in new.columns
            if column.name not in old_names
        )
        kept = [column.name for column in new.columns if column.name in old_names]
        if kept != [column.name for column in old.columns if column.name in new_columns]:
            out.append(SchemaDifference("column_order", name))
        if old.primary_key != new.primary_key:
            out.append(SchemaDifference("primary_key", name))
        if level2:
            out.extend(_constraint_differences(name, _canonical(old), _canonical(new), old_names | set(new_columns)))
    return tuple(out)


# --- level 2: reflection --------------------------------------------------------------------------------------------

_NO_ACTION = "NO ACTION"


def _check_level(level: object) -> None:
    if type(level) is not int or level not in (1, 2):
        raise UsageError("schema.invalid_level")


def _has_level2_detail(table: TableShape) -> bool:
    return bool(
        table.foreign_keys
        or table.unique_constraints
        or table.check_constraints
        or table.indexes
        or any(column.default is not None for column in table.columns)
    )


def _default_text(value: object) -> str | None:
    return None if value is None else str(value)


def _action(value: object) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).upper().split())
    return None if text in ("", _NO_ACTION) else text


def _level2_table(
    inspector: sqlalchemy.Inspector, shape: TableShape, schema: str, table: str, default: str
) -> TableShape:
    arg = _arg(schema, default)
    sqlite = inspector.dialect.name == "sqlite"
    actions = _sqlite_fk_actions(inspector, schema, table) if sqlite else []
    foreign_keys: list[ForeignKeyShape] = []
    for fk in _reflect(inspector.get_foreign_keys, table, arg):
        options = fk.get("options") or {}
        columns = tuple(str(c) for c in fk["constrained_columns"])
        referred_table = str(fk["referred_table"])
        referred_columns = tuple(str(c) for c in fk["referred_columns"])
        on_delete, on_update = _action(options.get("ondelete")), _action(options.get("onupdate"))
        if sqlite:
            # SQLAlchemy reads ON DELETE/ON UPDATE from the CREATE TABLE text and misses them on a column-level
            # REFERENCES clause; the pragma has them for every foreign key.
            on_delete, on_update = _take_actions(actions, columns, referred_table, referred_columns)
        foreign_keys.append(
            ForeignKeyShape(
                name=_optional_name(fk.get("name")),
                columns=columns,
                referred_table=f"{fk.get('referred_schema') or default}.{referred_table}",
                referred_columns=referred_columns,
                on_delete=on_delete,
                on_update=on_update,
            )
        )
    indexes, expression = (
        _sqlite_indexes(inspector, schema, table) if sqlite else (_indexes(inspector, table, arg), False)
    )
    with contextlib.ExitStack() as stack:
        if expression:
            # SQLAlchemy's SQLite unique reflection also reads the indexes and warns about each expression index,
            # which this module reads from the pragmas instead. catch_warnings changes the process-wide filters, so
            # it is used only for a SQLite table that has an expression index.
            stack.enter_context(warnings.catch_warnings())
            warnings.filterwarnings("ignore", _SQLITE_EXPRESSION_WARNING, sqlalchemy.exc.SAWarning)
        unique = [
            UniqueShape(name=_optional_name(u.get("name")), columns=tuple(str(c) for c in u["column_names"]))
            for u in _reflect(inspector.get_unique_constraints, table, arg)
        ]
    checks = [
        CheckShape(name=_optional_name(c.get("name")), sqltext=str(c["sqltext"]))
        for c in _reflect(inspector.get_check_constraints, table, arg)
    ]
    return _canonical(
        TableShape(
            name=shape.name,
            columns=shape.columns,
            primary_key=shape.primary_key,
            foreign_keys=tuple(foreign_keys),
            unique_constraints=tuple(unique),
            check_constraints=tuple(checks),
            indexes=tuple(indexes),
        )
    )


_SQLITE_EXPRESSION_WARNING = "Skipped unsupported reflection of expression-based index"


def _reflect(method: Callable[..., Sequence[Any]], table: str, schema: str | None) -> Sequence[Any]:
    with contextlib.suppress(NotImplementedError):  # a dialect without this kind of reflection
        return method(table, schema=schema)
    return ()


def _optional_name(value: object) -> str | None:
    return None if value is None else str(value)


def _indexes(inspector: sqlalchemy.Inspector, table: str, schema: str | None) -> list[IndexShape]:
    out = []
    for index in _reflect(inspector.get_indexes, table, schema):
        if index.get("duplicates_constraint"):
            continue  # the index of a unique constraint (PostgreSQL), already a UniqueShape
        names = list(index.get("column_names") or ())
        expressions = list(index.get("expressions") or ())
        elements = [
            str(expressions[i]) if name is None and i < len(expressions) else str(name) for i, name in enumerate(names)
        ]
        out.append(IndexShape(name=str(index["name"]), columns=tuple(elements), unique=bool(index.get("unique"))))
    return out


def _rows(inspector: sqlalchemy.Inspector, sql: str, params: Mapping[str, Any]) -> list[Any]:
    statement = sqlalchemy.text(sql)
    bind = inspector.bind
    if isinstance(bind, sqlalchemy.Engine):
        with bind.connect() as connection:
            return list(connection.execute(statement, dict(params)))
    return list(bind.execute(statement, dict(params)))


def _sqlite_fk_actions(
    inspector: sqlalchemy.Inspector, schema: str, table: str
) -> list[tuple[tuple[str, ...], str, tuple[str | None, ...], str | None, str | None]]:
    rows = _rows(
        inspector,
        'SELECT id, "table", "from", "to", on_update, on_delete FROM pragma_foreign_key_list(:t, :s) ORDER BY id, seq',
        {"t": table, "s": schema},
    )
    groups: dict[int, list[Any]] = {}
    for row in rows:
        groups.setdefault(int(row[0]), []).append(row)
    out = []
    for group in groups.values():
        first = group[0]
        out.append(
            (
                tuple(str(row[2]) for row in group),
                str(first[1]),
                tuple(None if row[3] is None else str(row[3]) for row in group),
                _action(first[5]),
                _action(first[4]),
            )
        )
    return out


def _take_actions(
    actions: list[tuple[tuple[str, ...], str, tuple[str | None, ...], str | None, str | None]],
    columns: tuple[str, ...],
    referred_table: str,
    referred_columns: tuple[str, ...],
) -> tuple[str | None, str | None]:
    for i, (cols, other, to, on_delete, on_update) in enumerate(actions):
        if cols == columns and other == referred_table and (all(t is None for t in to) or to == referred_columns):
            del actions[i]
            return on_delete, on_update
    return None, None


def _sqlite_indexes(inspector: sqlalchemy.Inspector, schema: str, table: str) -> tuple[list[IndexShape], bool]:
    # Read from the pragmas, because SQLAlchemy leaves out an expression index (with a warning). Only indexes made by
    # CREATE INDEX (origin "c"); those of a primary key or unique constraint are not indexes of the shape.
    quoted = inspector.dialect.identifier_preparer.quote_identifier(schema)
    out = []
    any_expression = False
    for name, unique, origin in _rows(
        inspector, 'SELECT name, "unique", origin FROM pragma_index_list(:t, :s)', {"t": table, "s": schema}
    ):
        if origin != "c":
            continue
        keys = _rows(
            inspector,
            "SELECT cid, name FROM pragma_index_xinfo(:i, :s) WHERE key = 1 ORDER BY seqno",
            {"i": name, "s": schema},
        )
        texts: list[str] = []
        if any(cid == -2 for cid, _ in keys):
            any_expression = True  # an expression: its text is only in the CREATE INDEX statement
            # The schema name is quoted by the dialect's preparer; the index name is a bound parameter.
            found = _rows(
                inspector,
                f"SELECT sql FROM {quoted}.sqlite_master WHERE type = 'index' AND name = :i",  # noqa: S608
                {"i": name},
            )
            sql = str(found[0][0]) if found and found[0][0] is not None else ""
            texts = _sqlite_index_elements(sql) or []
            if len(texts) != len(keys):  # not parsed: the whole statement stands for each expression
                texts = [sql] * len(keys)
        elements = [
            texts[position] if cid == -2 else "rowid" if cid == -1 else str(column)
            for position, (cid, column) in enumerate(keys)
        ]
        out.append(IndexShape(name=str(name), columns=tuple(elements), unique=bool(unique)))
    return out, any_expression


_ORDER_SUFFIX = re.compile(r"\s+(?:ASC|DESC)\s*$", re.IGNORECASE)


def _sqlite_index_elements(sql: str) -> list[str] | None:
    """The key elements of ``CREATE INDEX ... ON t (a, lower(b) DESC) WHERE ...`` as written, without ``ASC`` or
    ``DESC``; ``None`` when the text has no element list. Quotes and comments are skipped."""
    parts: list[str] = []
    depth, start, i = 0, -1, 0
    closing = {"'": "'", '"': '"', "`": "`", "[": "]"}
    while i < len(sql):
        ch = sql[i]
        if ch in closing:
            end = sql.find(closing[ch], i + 1)
            while end != -1 and ch != "[" and sql[end + 1 : end + 2] == ch:  # a doubled quote
                end = sql.find(ch, end + 2)
            if end == -1:
                return None
            i = end + 1
            continue
        if sql.startswith("--", i):
            end = sql.find("\n", i)
            i = len(sql) if end == -1 else end + 1
            continue
        if sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            i = len(sql) if end == -1 else end + 2
            continue
        if ch == "(":
            depth += 1
            if depth == 1 and start == -1:
                start = i + 1
        elif ch == ")":
            depth -= 1
            if depth == 0 and start != -1:
                parts.append(sql[start:i])
                return [_ORDER_SUFFIX.sub("", part.strip()) for part in parts]
        elif ch == "," and depth == 1 and start != -1:
            parts.append(sql[start:i])
            start = i + 1
        i += 1
    return None


# --- level 2: canonical order and comparison ------------------------------------------------------------------------


def _canonical(table: TableShape) -> TableShape:
    """The table with its constraints and indexes in a fixed order, so their order never counts."""
    return TableShape(
        name=table.name,
        columns=table.columns,
        primary_key=table.primary_key,
        foreign_keys=tuple(
            sorted(
                table.foreign_keys,
                key=lambda f: (
                    f.columns,
                    f.referred_table,
                    f.referred_columns,
                    f.name or "",
                    f.on_delete or "",
                    f.on_update or "",
                ),
            )
        ),
        unique_constraints=tuple(sorted(table.unique_constraints, key=lambda u: (u.columns, u.name or ""))),
        check_constraints=tuple(sorted(table.check_constraints, key=lambda c: (c.name or "", c.sqltext))),
        indexes=tuple(sorted(table.indexes, key=lambda x: (x.name, x.columns, x.unique))),
    )


def _unmatched(left: Sequence[_T], right: Sequence[_T]) -> list[_T]:
    """Items of ``left`` without an equal item in ``right`` (as multisets, in ``left``'s order)."""
    rest = list(right)
    out = []
    for item in left:
        if item in rest:
            rest.remove(item)
        else:
            out.append(item)
    return out


def _constraint_differences(
    table: str, old: TableShape, new: TableShape, column_names: set[str]
) -> list[SchemaDifference]:
    def column(columns: tuple[str, ...]) -> str | None:
        return columns[0] if len(columns) == 1 and columns[0] in column_names else None

    def pair(
        missing: DifferenceKind,
        added: DifferenceKind,
        expected: Sequence[ForeignKeyShape | UniqueShape | IndexShape | CheckShape],
        actual: Sequence[ForeignKeyShape | UniqueShape | IndexShape | CheckShape],
    ) -> list[SchemaDifference]:
        def difference(
            kind: DifferenceKind, item: ForeignKeyShape | UniqueShape | IndexShape | CheckShape
        ) -> SchemaDifference:
            columns = () if isinstance(item, CheckShape) else item.columns
            return SchemaDifference(kind, table, column(columns), item.name)

        return [difference(missing, item) for item in _unmatched(expected, actual)] + [
            difference(added, item) for item in _unmatched(actual, expected)
        ]

    return [
        *pair("foreign_key_missing", "foreign_key_added", old.foreign_keys, new.foreign_keys),
        *pair("unique_missing", "unique_added", old.unique_constraints, new.unique_constraints),
        *pair("check_missing", "check_added", old.check_constraints, new.check_constraints),
        *pair("index_missing", "index_added", old.indexes, new.indexes),
    ]


# --- level 1 helpers and JSON -----------------------------------------------------------------------------------------


def _arg(schema: str, default: str) -> str | None:
    return None if schema == default else schema


def _view_names(inspector: sqlalchemy.Inspector, schema: str | None) -> frozenset[str]:
    names = set(inspector.get_view_names(schema=schema))
    with contextlib.suppress(NotImplementedError):  # a dialect without materialized views
        names.update(inspector.get_materialized_view_names(schema=schema))
    return frozenset(names)


def _type_name(column_type: Any, dialect: sqlalchemy.Dialect) -> str:
    try:
        return str(column_type.compile(dialect=dialect))
    except Exception:
        return type(column_type).__name__.upper()


def _as_dict(snapshot: SchemaSnapshot) -> dict[str, Any]:
    # Format 1: the 0.2 layout. Changing a byte here changes every stored v1 fingerprint (ADR 0015 D-4).
    return {
        "format": SNAPSHOT_FORMAT,
        "dialect": snapshot.dialect,
        "tables": [
            {
                "name": table.name,
                "columns": [[column.name, column.type, column.nullable] for column in table.columns],
                "primary_key": list(table.primary_key),
            }
            for table in snapshot.tables
        ],
    }


def _as_dict_v2(snapshot: SchemaSnapshot) -> dict[str, Any]:
    tables = []
    for table in snapshot.tables:
        table = _canonical(table)
        tables.append(
            {
                "name": table.name,
                "columns": [[c.name, c.type, c.nullable, c.default] for c in table.columns],
                "primary_key": list(table.primary_key),
                "foreign_keys": [
                    {
                        "name": f.name,
                        "columns": list(f.columns),
                        "referred_table": f.referred_table,
                        "referred_columns": list(f.referred_columns),
                        "on_delete": f.on_delete,
                        "on_update": f.on_update,
                    }
                    for f in table.foreign_keys
                ],
                "unique_constraints": [{"name": u.name, "columns": list(u.columns)} for u in table.unique_constraints],
                "check_constraints": [{"name": c.name, "sqltext": c.sqltext} for c in table.check_constraints],
                "indexes": [{"name": x.name, "columns": list(x.columns), "unique": x.unique} for x in table.indexes],
            }
        )
    return {"format": 2, "dialect": snapshot.dialect, "tables": tables}


def _from_dict(data: Mapping[str, Any]) -> SchemaSnapshot | None:
    if data.get("format") != SNAPSHOT_FORMAT or not isinstance(data.get("dialect"), str):
        return None
    tables: list[TableShape] = []
    if not isinstance(data["tables"], list):
        return None
    for table in data["tables"]:
        if not isinstance(table["columns"], list) or not isinstance(table["primary_key"], list):
            return None
        columns = []
        for name, type_name, nullable in table["columns"]:
            if not (isinstance(name, str) and isinstance(type_name, str) and isinstance(nullable, bool)):
                return None
            columns.append(ColumnShape(name=name, type=type_name, nullable=nullable))
        primary_key = table["primary_key"]
        if not isinstance(table["name"], str) or not all(isinstance(c, str) for c in primary_key):
            return None
        tables.append(TableShape(name=table["name"], columns=tuple(columns), primary_key=tuple(primary_key)))
    return SchemaSnapshot(dialect=data["dialect"], tables=tuple(sorted(tables, key=lambda t: t.name)))


def _str(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("not a string")
    return value


def _opt_str(value: object) -> str | None:
    return None if value is None else _str(value)


def _strs(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise TypeError("not a list")
    return tuple(_str(item) for item in value)


def _list(value: object) -> list[Any]:
    if not isinstance(value, list):
        raise TypeError("not a list")
    return value


def _from_dict_v2(data: Mapping[str, Any]) -> SchemaSnapshot:
    tables: list[TableShape] = []
    for table in _list(data["tables"]):
        columns = []
        for name, type_name, nullable, default in _list(table["columns"]):
            if not isinstance(nullable, bool):
                raise TypeError("nullable")
            columns.append(ColumnShape(_str(name), _str(type_name), nullable, _opt_str(default)))
        foreign_keys = tuple(
            ForeignKeyShape(
                name=_opt_str(f["name"]),
                columns=_strs(f["columns"]),
                referred_table=_str(f["referred_table"]),
                referred_columns=_strs(f["referred_columns"]),
                on_delete=_opt_str(f["on_delete"]),
                on_update=_opt_str(f["on_update"]),
            )
            for f in _list(table["foreign_keys"])
        )
        unique = tuple(
            UniqueShape(name=_opt_str(u["name"]), columns=_strs(u["columns"]))
            for u in _list(table["unique_constraints"])
        )
        checks = tuple(
            CheckShape(name=_opt_str(c["name"]), sqltext=_str(c["sqltext"])) for c in _list(table["check_constraints"])
        )
        indexes = []
        for x in _list(table["indexes"]):
            if not isinstance(x["unique"], bool):
                raise TypeError("unique")
            indexes.append(IndexShape(name=_str(x["name"]), columns=_strs(x["columns"]), unique=x["unique"]))
        tables.append(
            _canonical(
                TableShape(
                    name=_str(table["name"]),
                    columns=tuple(columns),
                    primary_key=_strs(table["primary_key"]),
                    foreign_keys=foreign_keys,
                    unique_constraints=unique,
                    check_constraints=checks,
                    indexes=tuple(indexes),
                )
            )
        )
    return SchemaSnapshot(dialect=_str(data["dialect"]), tables=tuple(sorted(tables, key=lambda t: t.name)), level=2)
