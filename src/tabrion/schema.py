"""Schema fingerprint and comparison over a SQLAlchemy inspector (TAB-CR-2, CR-DB; layer B, extra ``sqlalchemy``).

Contract: ``docs/api/contract.md`` section 5.8 and implementation note I-108. A :class:`SchemaSnapshot` holds the
shape of some tables (columns with type and nullability, primary key), never data; :func:`fingerprint` is a stable
hash of it, and :func:`compare` names what differs between an expected and an actual snapshot. Use it with
``Database.inspector()``::

    with db.inspector() as inspector:
        snapshot = tabrion.schema.snapshot(inspector, tables=["public.invoices"])

A consumer (Nodestro's ``database`` node, NOD-CR-5) stores the fingerprint (and the snapshot as JSON, for clear
messages) with a release and compares at start-up; drift is reported, never migrated.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import hashlib
import json
from typing import Any, Literal

try:
    import sqlalchemy
except ImportError:
    raise ImportError('tabrion.schema needs the extra "sqlalchemy": pip install "tabrion[sqlalchemy]"') from None

from tabrion.errors import UsageError

__all__ = [
    "ColumnShape",
    "SchemaDifference",
    "SchemaSnapshot",
    "TableShape",
    "compare",
    "fingerprint",
    "snapshot",
]

SNAPSHOT_FORMAT = 1
_MAX_TABLES = 10_000

DifferenceKind = Literal[
    "table_missing", "table_added", "column_missing", "column_added", "column_type", "column_nullable", "primary_key"
]
_BREAKING = frozenset({"table_missing", "column_missing", "column_type", "column_nullable", "primary_key"})


@dataclass(frozen=True, slots=True)
class ColumnShape:
    """One column: its name, its type as the dialect writes it (``INTEGER``, ``VARCHAR(20)``) and nullability."""

    name: str
    type: str
    nullable: bool


@dataclass(frozen=True, slots=True)
class TableShape:
    """One table: ``schema.table`` (SQLite: ``main.table``), its columns in table order and its primary key."""

    name: str
    columns: tuple[ColumnShape, ...]
    primary_key: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SchemaSnapshot:
    """The shape of the tables of one database, sorted by name. Holds no data and no connection details."""

    dialect: str
    tables: tuple[TableShape, ...]

    def to_json(self) -> str:
        """Canonical JSON (sorted keys, no blanks); :meth:`from_json` reads it back."""
        return json.dumps(_as_dict(self), sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    @classmethod
    def from_json(cls, text: str) -> SchemaSnapshot:
        """A snapshot from :meth:`to_json`; ``UsageError("schema.invalid_snapshot")`` for anything else."""
        snapshot: SchemaSnapshot | None = None
        try:
            snapshot = _from_dict(json.loads(text))
        except (ValueError, TypeError, KeyError, AttributeError):
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
    """One difference between an expected and an actual snapshot. ``breaking`` is ``False`` only for an added table
    or column, which an application that does not use it does not notice."""

    kind: DifferenceKind
    table: str
    column: str | None = None

    @property
    def breaking(self) -> bool:
        return self.kind in _BREAKING

    def __str__(self) -> str:
        where = f"{self.table}.{self.column}" if self.column is not None else self.table
        return f"{self.kind}: {where}"


def snapshot(
    inspector: sqlalchemy.Inspector,
    *,
    tables: Iterable[str] | None = None,
    schemas: Iterable[str] | None = None,
) -> SchemaSnapshot:
    """The shape of ``tables`` (``schema.table``), or of every table of ``schemas`` (default: the default schema).

    A table of ``tables`` that does not exist is left out, so :func:`compare` reports it as ``table_missing``. Views
    are not included. Raises ``UsageError("schema.too_many_tables")`` above 10 000 tables."""
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
    for schema, table in wanted:
        if not inspector.has_table(table, schema=_arg(schema, default)):
            continue
        columns = tuple(
            ColumnShape(name=str(c["name"]), type=_type_name(c["type"], dialect), nullable=bool(c["nullable"]))
            for c in inspector.get_columns(table, schema=_arg(schema, default))
        )
        pk = inspector.get_pk_constraint(table, schema=_arg(schema, default))
        primary_key = tuple(str(column) for column in (pk.get("constrained_columns") or ()))
        shapes[f"{schema}.{table}"] = TableShape(name=f"{schema}.{table}", columns=columns, primary_key=primary_key)
    return SchemaSnapshot(dialect=str(dialect.name), tables=tuple(shapes[name] for name in sorted(shapes)))


def fingerprint(snapshot: SchemaSnapshot) -> str:
    """``v1:sha256:<64 hex>`` of the snapshot's canonical JSON: equal shapes give equal fingerprints.

    Column order counts (it is part of the shape a ``SELECT *`` sees). The type text comes from SQLAlchemy's dialect
    compiler, so a SQLAlchemy upgrade that writes a type differently changes the fingerprint; :func:`compare` then
    names the column."""
    return "v1:sha256:" + hashlib.sha256(snapshot.to_json().encode("ascii")).hexdigest()


def compare(expected: SchemaSnapshot, actual: SchemaSnapshot) -> tuple[SchemaDifference, ...]:
    """What ``actual`` lacks or has in addition, compared with ``expected``, in table and column order.

    Each difference names a table and a column, never data. Different dialects raise
    ``UsageError("schema.dialect_mismatch")``."""
    if expected.dialect != actual.dialect:
        raise UsageError("schema.dialect_mismatch")
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
            elif found.type != column.type:
                out.append(SchemaDifference("column_type", name, column.name))
            elif found.nullable != column.nullable:
                out.append(SchemaDifference("column_nullable", name, column.name))
        out.extend(
            SchemaDifference("column_added", name, column.name)
            for column in new.columns
            if column.name not in old_names
        )
        if old.primary_key != new.primary_key:
            out.append(SchemaDifference("primary_key", name))
    return tuple(out)


def _arg(schema: str, default: str) -> str | None:
    return None if schema == default else schema


def _type_name(column_type: Any, dialect: sqlalchemy.Dialect) -> str:
    try:
        return str(column_type.compile(dialect=dialect))
    except Exception:
        return type(column_type).__name__.upper()


def _as_dict(snapshot: SchemaSnapshot) -> dict[str, Any]:
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


def _from_dict(data: Mapping[str, Any]) -> SchemaSnapshot | None:
    if data.get("format") != SNAPSHOT_FORMAT or not isinstance(data.get("dialect"), str):
        return None
    tables: list[TableShape] = []
    for table in data["tables"]:
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
