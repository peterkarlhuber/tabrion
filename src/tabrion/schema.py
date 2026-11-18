"""Schema fingerprint and comparison over a SQLAlchemy inspector (TAB-CR-2, CR-DB; layer B, extra ``sqlalchemy``).

Contract: ``docs/api/contract.md`` section 5.10 and implementation notes I-110 and I-111. A :class:`SchemaSnapshot`
holds the shape of some tables, never data; :func:`fingerprint` is a stable hash of it, and :func:`compare` names what
differs between an expected and an actual snapshot. Use it with ``Database.inspector()``::

    with db.inspector() as inspector:
        snapshot = tabrion.schema.snapshot(inspector, tables=["public.invoices"])

Level 1 (the default, since 0.2) holds columns with type and nullability and the primary key; its fingerprint starts
with ``v1:``. Level 2 (since 0.3, TAB-XW-2; ``snapshot(..., level=2)``) adds column defaults, foreign
keys, unique and check constraints and indexes; its fingerprint starts with ``v2:``. A level-1 fingerprint is
byte-identical to the one 0.2 computed (ADR 0015 D-4). Since TAB-XW-4 level 2 also holds triggers, row-level
security policies and the functions they call directly, and marks table features the shape does not describe
(partitioning, inheritance, rules, domain columns, ...; ADR 0015 D-5), so a ``v2:`` fingerprint sees them.

A consumer (Nodestro's ``database`` node, NOD-CR-5) stores the fingerprint (and the snapshot as JSON, for clear
messages) with a release and compares at start-up; drift is reported, never migrated. A level-2 snapshot is also the
target model of the Schema Designer (CR-DBX).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
import contextlib
import dataclasses
from dataclasses import dataclass
import hashlib
import json
from typing import Any, Literal, TypeVar

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
    "ExclusionShape",
    "ForeignKeyShape",
    "FunctionShape",
    "IndexShape",
    "PolicyShape",
    "SchemaDifference",
    "SchemaSnapshot",
    "TableShape",
    "TriggerShape",
    "UniqueShape",
    "ViewShape",
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
    "column_identity",
    "column_generated",
    "foreign_key_missing",
    "foreign_key_added",
    "unique_missing",
    "unique_added",
    "check_missing",
    "check_added",
    "exclusion_missing",
    "exclusion_added",
    "index_missing",
    "index_added",
    "trigger_missing",
    "trigger_added",
    "policy_missing",
    "policy_added",
    "function_missing",
    "function_added",
    "row_security",
    "feature_missing",
    "feature_added",
    "view_missing",
    "view_added",
]
_BREAKING = frozenset(
    {
        "table_missing",
        "column_missing",
        "column_type",
        "column_nullable",
        "primary_key",
        "column_default",
        "column_identity",
        "column_generated",
        "foreign_key_missing",
        "foreign_key_added",
        "unique_missing",
        "unique_added",
        "check_missing",
        "check_added",
        "exclusion_missing",
        "exclusion_added",
        "index_missing",
        "trigger_missing",
        "trigger_added",
        "policy_missing",
        "policy_added",
        "function_missing",
        "function_added",
        "row_security",
        "feature_missing",
        "feature_added",
        "view_missing",
        "view_added",
    }
)
# column_order is not breaking: an application that names its columns does not notice it. Neither is an added table,
# column or index. An added constraint is breaking: it can refuse a write that worked before; so is every change of a
# trigger, policy, function, row-level security or table feature (it changes what a write does or who sees a row).

_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class ColumnShape:
    """One column: its name, its type as the dialect writes it (``INTEGER``, ``VARCHAR(20)``) and nullability.

    ``default`` (level 2, since 0.3) is the server default as the dialect reflects it (``0``, ``'x'::character
    varying``, ``nextval('s'::regclass)``, ``CURRENT_TIMESTAMP``), or ``None``; it is schema text, not data.

    ``identity`` (level 2) is ``ALWAYS`` or ``BY DEFAULT`` for a PostgreSQL identity column (``attidentity``; its
    sequence options are not in the shape), else ``None``. ``generated`` (level 2) is ``STORED`` or ``VIRTUAL`` for a
    generated column (PostgreSQL ``attgenerated``, SQLite ``pragma_table_xinfo`` ``hidden`` 3 or 2), else ``None``;
    ``generation_expression`` is then its expression (PostgreSQL as ``pg_get_expr`` returns it, SQLite as written, or
    the whole ``CREATE TABLE`` text when it cannot be read alone)."""

    name: str
    type: str
    nullable: bool
    default: str | None = None
    identity: str | None = None
    generated: str | None = None
    generation_expression: str | None = None


@dataclass(frozen=True, slots=True)
class ForeignKeyShape:
    """A foreign key (level 2, since 0.3). ``name`` is ``None`` for an unnamed SQLite constraint; ``referred_table``
    is ``schema.table``. ``on_delete`` and ``on_update`` are ``CASCADE``, ``SET NULL``, ``SET DEFAULT`` or
    ``RESTRICT``; ``None`` is the engine default ``NO ACTION`` (written or not). ``deferrable`` and
    ``initially_deferred`` are ``DEFERRABLE`` and ``INITIALLY DEFERRED``; ``match`` is PostgreSQL's ``FULL`` or
    ``PARTIAL`` (``None`` is ``MATCH SIMPLE``); ``validated`` is ``False`` for a PostgreSQL ``NOT VALID`` key."""

    name: str | None
    columns: tuple[str, ...]
    referred_table: str
    referred_columns: tuple[str, ...]
    on_delete: str | None = None
    on_update: str | None = None
    deferrable: bool = False
    initially_deferred: bool = False
    match: str | None = None
    validated: bool = True


@dataclass(frozen=True, slots=True)
class UniqueShape:
    """A unique constraint (level 2, since 0.3); ``name`` is ``None`` on SQLite, which keeps no name it can address. A
    unique index that is not a constraint is an :class:`IndexShape` with ``unique=True``. ``include`` holds the
    PostgreSQL ``INCLUDE`` columns; ``nulls_not_distinct`` is PostgreSQL's ``NULLS NOT DISTINCT``; ``collations`` is
    empty when every column compares with the default collation, else one entry per column, ``""`` for the default
    (SQLite ``BINARY``) or the collation name (``NOCASE``, ``und-x-icu``)."""

    name: str | None
    columns: tuple[str, ...]
    include: tuple[str, ...] = ()
    nulls_not_distinct: bool = False
    deferrable: bool = False
    initially_deferred: bool = False
    collations: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CheckShape:
    """A check constraint (level 2, since 0.3): its name (``None`` for an unnamed SQLite constraint) and its
    expression as the dialect reflects it (PostgreSQL normalizes it, SQLite keeps the text as written).
    ``validated`` is ``False`` for a PostgreSQL ``NOT VALID`` constraint."""

    name: str | None
    sqltext: str
    validated: bool = True


@dataclass(frozen=True, slots=True)
class ExclusionShape:
    """A PostgreSQL exclusion constraint (level 2, since 0.3): its name and its definition as
    ``pg_get_constraintdef`` writes it (``EXCLUDE USING gist (k WITH =, r WITH &&)``)."""

    name: str
    definition: str


@dataclass(frozen=True, slots=True)
class IndexShape:
    """An index that is not the index of a primary key, unique or exclusion constraint (level 2, since 0.3).
    ``columns`` holds a column name per key element, or the expression text for an expression element
    (``lower(note::text)``); ``expressions`` holds the positions (from 0) of the expression elements, so a column
    named ``lower(a)`` and the expression ``lower(a)`` differ.

    ``ordering``, ``collations`` and ``opclasses`` are empty when every element has the default; otherwise they have
    one entry per element, ``""`` for the default. ``ordering`` is ``DESC`` and a ``NULLS FIRST``/``NULLS LAST`` that
    differs from the engine's default for that direction (``DESC NULLS LAST``); ``collations`` the collation an element
    compares with, when it is not the database default (SQLite ``BINARY``); ``opclasses`` a PostgreSQL operator class
    that is not the default for the element's type (``text_pattern_ops``). ``where`` is the predicate of a partial
    index (PostgreSQL as ``pg_get_expr`` returns it, SQLite as written); ``include`` holds the PostgreSQL ``INCLUDE``
    columns; ``nulls_not_distinct`` is PostgreSQL's ``NULLS NOT DISTINCT``; ``method`` the PostgreSQL access method
    when it is not ``btree`` (``hash``, ``gin``); ``valid`` is ``False`` for a PostgreSQL index that is not valid
    (``indisvalid``, a failed ``CREATE INDEX CONCURRENTLY``)."""

    name: str
    columns: tuple[str, ...]
    unique: bool = False
    ordering: tuple[str, ...] = ()
    where: str | None = None
    include: tuple[str, ...] = ()
    nulls_not_distinct: bool = False
    expressions: tuple[int, ...] = ()
    collations: tuple[str, ...] = ()
    opclasses: tuple[str, ...] = ()
    method: str | None = None
    valid: bool = True


@dataclass(frozen=True, slots=True)
class TriggerShape:
    """A trigger of the table (level 2, since 0.3; ADR 0015 D-5.1). ``definition`` is PostgreSQL's
    ``pg_get_triggerdef`` or the ``CREATE TRIGGER`` text SQLite keeps in ``sqlite_schema``. ``enabled`` is ``None``
    for a trigger that fires as usual (PostgreSQL ``O``; always on SQLite), else ``DISABLED``, ``REPLICA`` or
    ``ALWAYS``. The internal triggers PostgreSQL makes for foreign keys are not listed."""

    name: str
    definition: str
    enabled: str | None = None


@dataclass(frozen=True, slots=True)
class PolicyShape:
    """A PostgreSQL row-level security policy (level 2, since 0.3; ADR 0015 D-5.1). ``command`` is ``ALL``,
    ``SELECT``, ``INSERT``, ``UPDATE`` or ``DELETE``; ``permissive`` is ``False`` for ``AS RESTRICTIVE``; ``roles``
    holds the role names, sorted (``PUBLIC`` for every role); ``using`` and ``check`` are the ``USING`` and ``WITH
    CHECK`` expressions as ``pg_get_expr`` writes them, or ``None``."""

    name: str
    command: str
    permissive: bool
    roles: tuple[str, ...]
    using: str | None = None
    check: str | None = None


@dataclass(frozen=True, slots=True)
class FunctionShape:
    """A PostgreSQL function that a trigger or policy of the table calls directly (level 2, since 0.3; ADR 0015
    D-5.1): the trigger function and every function a ``WHEN`` condition or a policy expression depends on
    (``pg_depend``; built-in functions are not recorded there). ``signature`` is ``schema.name(argument types)``,
    ``language`` the language name (``plpgsql``, ``sql``, ``c``, ``internal`` ...), ``definition`` the whole
    ``pg_get_functiondef`` text with the body, ``""`` when PostgreSQL cannot write one (an aggregate). Functions
    that these functions call in turn are not followed (a documented residual risk)."""

    signature: str
    language: str
    definition: str


@dataclass(frozen=True, slots=True)
class ViewShape:
    """A SQLite view (level 2, since 0.3; review of #89, P89-02): ``name`` is ``schema.view``, ``definition`` the
    ``CREATE VIEW`` text ``sqlite_schema`` keeps. A schema change plan refuses to drop a column or a table that the
    text of a view or trigger names, and checks every view after a SQLite rebuild. PostgreSQL views are not read: the
    server refuses to drop a column or table a view depends on."""

    name: str
    definition: str


@dataclass(frozen=True, slots=True)
class TableShape:
    """One table: ``schema.table`` (SQLite: ``main.table``), its columns in table order and its primary key; at level
    2 (since 0.3) also its foreign keys, unique, check and exclusion constraints and indexes (empty at level 1).

    Since TAB-XW-4 (ADR 0015 D-5), level 2 also holds the table's ``triggers``, its PostgreSQL row-level security
    ``policies`` and whether row-level security is enabled (``row_security``) and forced (``force_row_security``),
    the ``functions`` the triggers and policies call directly, and ``features``: sorted markers of what the shape
    does not describe and a schema change plan therefore refuses (``partitioned``, ``partition``, ``inheritance``,
    ``foreign_table``, ``rule:<name>``, ``domain:<column>`` ...; contract I-111 i)."""

    name: str
    columns: tuple[ColumnShape, ...]
    primary_key: tuple[str, ...]
    foreign_keys: tuple[ForeignKeyShape, ...] = ()
    unique_constraints: tuple[UniqueShape, ...] = ()
    check_constraints: tuple[CheckShape, ...] = ()
    indexes: tuple[IndexShape, ...] = ()
    exclusion_constraints: tuple[ExclusionShape, ...] = ()
    triggers: tuple[TriggerShape, ...] = ()
    policies: tuple[PolicyShape, ...] = ()
    functions: tuple[FunctionShape, ...] = ()
    row_security: bool = False
    force_row_security: bool = False
    features: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SchemaSnapshot:
    """The shape of the tables of one database, sorted by name. Holds no data and no connection details.

    ``level`` (since 0.3) is 1 or 2 (``UsageError("schema.invalid_level")`` otherwise); a level-1 snapshot must not
    hold level-2 detail (a default, an identity or generated column, a constraint or an index:
    ``UsageError("schema.level_mismatch")``). In a level-2 snapshot a table name or the referred table of a foreign
    key with more than one dot is refused (``UsageError("schema.unsupported_name")``): ``s.x.y`` could be table
    ``x.y`` of schema ``s`` or table ``y`` of schema ``s.x`` (review of #87, M-1).

    ``features`` (level 2, since 0.3, TAB-XW-4) holds the database-wide markers a schema change plan refuses: on
    PostgreSQL ``event_trigger:<name>`` for every event trigger (ADR 0015 D-5.3). ``views`` (level 2, since 0.3,
    review of #89 P89-02) holds the SQLite views of the snapshot's schemas, sorted by name."""

    dialect: str
    tables: tuple[TableShape, ...]
    level: Literal[1, 2] = 1
    features: tuple[str, ...] = ()
    views: tuple[ViewShape, ...] = ()

    def __post_init__(self) -> None:
        _check_level(self.level)
        if self.level == 1 and (self.features or self.views or any(_has_level2_detail(table) for table in self.tables)):
            raise UsageError("schema.level_mismatch")
        if self.level == 2:
            for table in self.tables:
                names = [table.name, *(getattr(f, "referred_table", None) for f in table.foreign_keys)]
                if any(isinstance(name, str) and name.count(".") > 1 for name in names):
                    raise UsageError("schema.unsupported_name", details={"table": str(table.name)})

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
            form = data.get("format")
            if type(form) is int:  # not True, 1.0 or 2.0 (review of #85, F6)
                snapshot = _from_dict_v2(data) if form == 2 else _from_dict(data)
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
    ``level=2`` (since 0.3) also reads defaults, identity and generated columns, foreign keys, unique,
    check and exclusion constraints, indexes, triggers, policies, the functions they call and the table features
    of contract I-111 i; ``level=1`` stays the default (ADR 0015 D-4). At
    level 2 a schema or table name with a dot raises ``UsageError("schema.unsupported_name")``, as ``schema.table``
    would not name it unambiguously; level 1 keeps the behavior of 0.2 (I-111 g)."""
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
    if level == 2:
        for schema, table in wanted:
            if "." in schema or "." in table:
                raise UsageError("schema.unsupported_name", details={"schema": schema, "table": table})
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
    features: tuple[str, ...] = ()
    view_shapes: tuple[ViewShape, ...] = ()
    if level == 2 and dialect.name == "sqlite":
        # The view texts of every schema the snapshot read (review of #89, P89-02): a plan refuses to drop what they
        # name, and a rebuild checks them. Names are quoted by the dialect's preparer.
        preparer = dialect.identifier_preparer
        found: list[ViewShape] = []
        for schema in sorted({schema for schema, _ in wanted}):
            rows = _rows(
                inspector,
                f"SELECT name, sql FROM {preparer.quote_identifier(schema)}.sqlite_master "  # noqa: S608
                "WHERE type = 'view'",
                {},
            )
            found.extend(ViewShape(f"{schema}.{name}", str(sql or "")) for name, sql in rows)
        view_shapes = tuple(sorted(found, key=lambda v: v.name))
    if level == 2 and dialect.name == "postgresql":
        # Every event trigger, enabled or not: it runs on any DDL statement (ADR 0015 D-5.3).
        rows = _rows(inspector, "SELECT evtname FROM pg_catalog.pg_event_trigger", {})
        features = tuple(sorted({f"event_trigger:{name}" for (name,) in rows}))
    return SchemaSnapshot(
        dialect=str(dialect.name),
        tables=tuple(shapes[name] for name in sorted(shapes)),
        level=level,
        features=features,
        views=view_shapes,
    )


def fingerprint(snapshot: SchemaSnapshot) -> str:
    """``v1:sha256:<64 hex>`` (level 1) or ``v2:sha256:<64 hex>`` (level 2) of the snapshot's canonical JSON: equal
    shapes give equal fingerprints.

    Column order counts (it is part of the shape a ``SELECT *`` sees; :func:`compare` names it as ``column_order``);
    the order of constraints and indexes does not. The type text comes from SQLAlchemy's dialect compiler, so a
    SQLAlchemy upgrade that writes a type differently changes the fingerprint; :func:`compare` then names the
    column."""
    prefix = "v1:sha256:" if snapshot.level == 1 else "v2:sha256:"
    return prefix + hashlib.sha256(snapshot.to_json().encode("ascii")).hexdigest()


def compare(*, expected: SchemaSnapshot, actual: SchemaSnapshot) -> tuple[SchemaDifference, ...]:
    """What ``actual`` lacks or has in addition, compared with ``expected``, in table and column order. Both are
    keyword-only (since 0.3): swapped, a missing name would read as an added one and lose its ``breaking`` flag.

    Each difference names a table and a column (and at level 2 a constraint or index name), never data. Different
    dialects raise ``UsageError("schema.dialect_mismatch")``, different levels ``UsageError("schema.level_mismatch")``.
    """
    if expected.dialect != actual.dialect:
        raise UsageError("schema.dialect_mismatch")
    if expected.level != actual.level:
        raise UsageError("schema.level_mismatch")
    level2 = expected.level == 2
    out: list[SchemaDifference] = []
    if level2:  # the database-wide features (ADR 0015 D-5.3) first, with an empty table name
        out.extend(
            SchemaDifference("feature_missing", "", None, f)
            for f in sorted(set(expected.features) - set(actual.features))
        )
        out.extend(
            SchemaDifference("feature_added", "", None, f)
            for f in sorted(set(actual.features) - set(expected.features))
        )
        # a changed view is missing and added, as a changed constraint (review of #89, P89-02)
        out.extend(
            SchemaDifference("view_missing", v.name) for v in sorted(set(expected.views) - set(actual.views), key=str)
        )
        out.extend(
            SchemaDifference("view_added", v.name) for v in sorted(set(actual.views) - set(expected.views), key=str)
        )
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
            if level2 and found.identity != column.identity:
                out.append(SchemaDifference("column_identity", name, column.name))
            if level2 and (found.generated, found.generation_expression) != (
                column.generated,
                column.generation_expression,
            ):
                out.append(SchemaDifference("column_generated", name, column.name))
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
_MAX_INDEX_SQL = 100_000
"""Longest SQLite ``CREATE INDEX`` text that is split into elements; a longer one stands whole (I-111 c)."""


def _check_level(level: object) -> None:
    if type(level) is not int or level not in (1, 2):
        raise UsageError("schema.invalid_level")


def _has_level2_detail(table: TableShape) -> bool:
    return bool(
        table.foreign_keys
        or table.unique_constraints
        or table.check_constraints
        or table.indexes
        or table.exclusion_constraints
        or table.triggers
        or table.policies
        or table.functions
        or table.row_security
        or table.force_row_security
        or table.features
        or any(
            column.default is not None
            or column.identity is not None
            or column.generated is not None
            or column.generation_expression is not None
            for column in table.columns
        )
    )


def _default_text(value: object) -> str | None:
    return None if value is None else str(value)


def _action(value: object) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).upper().split())
    return None if text in ("", _NO_ACTION) else text


def _optional_name(value: object) -> str | None:
    return None if value is None else str(value)


def _defaults(entries: Sequence[str]) -> tuple[str, ...]:
    """``()`` when every entry is the default ``""``, else the entries."""
    return tuple(entries) if any(entries) else ()


@dataclass(frozen=True, slots=True)
class _PgConstraint:
    deferrable: bool
    initially_deferred: bool
    validated: bool
    match: str | None
    index: str | None


@dataclass(frozen=True, slots=True)
class _PgIndex:
    method: str | None
    valid: bool
    collations: tuple[str, ...]
    opclasses: tuple[str, ...]
    include: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Column:
    identity: str | None
    generated: str | None
    expression: str | None
    collation: str | None
    """The column's collation as SQLAlchemy 2.1 writes it after ``COLLATE`` when it is not the default of the type."""


@dataclass(frozen=True, slots=True)
class _Catalog:
    """What PostgreSQL's catalog adds to SQLAlchemy's reflection; empty for another dialect."""

    constraints: Mapping[str, _PgConstraint]
    indexes: Mapping[str, _PgIndex]
    exclusions: tuple[ExclusionShape, ...]
    columns: Mapping[str, _Column] = dataclasses.field(default_factory=dict)
    triggers: tuple[TriggerShape, ...] = ()
    policies: tuple[PolicyShape, ...] = ()
    functions: tuple[FunctionShape, ...] = ()
    row_security: bool = False
    force_row_security: bool = False
    features: tuple[str, ...] = ()


_EMPTY = _Catalog({}, {}, ())
_MATCH = {"f": "FULL", "p": "PARTIAL"}


def _level2_table(
    inspector: sqlalchemy.Inspector,
    shape: TableShape,
    schema: str,
    table: str,
    default: str,
) -> TableShape:
    arg = _arg(schema, default)
    if inspector.dialect.name == "sqlite":
        sql = _sqlite_table_sql(inspector, schema, table)
        catalog = _Catalog(
            {},
            {},
            (),
            _sqlite_columns(inspector, schema, table),
            triggers=_sqlite_triggers(inspector, schema, table),
        )
        foreign_keys = _sqlite_foreign_keys(inspector, schema, table, arg)
        indexes, unique = _sqlite_indexes(inspector, schema, table)
        features = _sqlite_features(sql, sum(1 for f in foreign_keys if f.deferrable))
        catalog = dataclasses.replace(catalog, features=features)
    else:
        catalog = _postgres_catalog(inspector, schema, table) if inspector.dialect.name == "postgresql" else _EMPTY
        foreign_keys = _foreign_keys(inspector, table, arg, default, catalog)
        unique = _unique_constraints(inspector, table, arg, catalog)
        indexes = _indexes(inspector, table, arg, catalog)
    checks = []
    reflected = (
        _sqlite_checks(inspector, schema, table)
        if inspector.dialect.name == "sqlite"
        else _reflect(inspector.get_check_constraints, table, arg)
    )
    for c in reflected:
        name = _optional_name(c.get("name"))
        found = catalog.constraints.get(name or "")
        checks.append(CheckShape(name=name, sqltext=str(c["sqltext"]), validated=found is None or found.validated))
    columns = []
    for column in shape.columns:
        extra = catalog.columns.get(column.name)
        if extra is None:
            columns.append(column)
            continue
        type_text = column.type
        if extra.collation is not None and " COLLATE " not in type_text:
            # SQLAlchemy 2.0.30 (the floor) reflects no PostgreSQL column collation; 2.1 writes it into the type text
            # as below, so the shape is the same on both (review of #87, L-1).
            type_text = f"{type_text} COLLATE {extra.collation}"
        columns.append(
            dataclasses.replace(
                column,
                type=type_text,
                identity=extra.identity,
                generated=extra.generated,
                generation_expression=extra.expression,
            )
        )
    return _canonical(
        TableShape(
            name=shape.name,
            columns=tuple(columns),
            primary_key=shape.primary_key,
            foreign_keys=tuple(foreign_keys),
            unique_constraints=tuple(unique),
            check_constraints=tuple(checks),
            indexes=tuple(indexes),
            exclusion_constraints=catalog.exclusions,
            triggers=catalog.triggers,
            policies=catalog.policies,
            functions=catalog.functions,
            row_security=catalog.row_security,
            force_row_security=catalog.force_row_security,
            features=catalog.features,
        )
    )


def _reflect(method: Callable[..., Sequence[Any]], table: str, schema: str | None) -> Sequence[Any]:
    with contextlib.suppress(NotImplementedError):  # a dialect without this kind of reflection
        return method(table, schema=schema)
    return ()


def _rows(inspector: sqlalchemy.Inspector, sql: str, params: Mapping[str, Any]) -> list[Any]:
    statement = sqlalchemy.text(sql)
    bind = inspector.bind
    if isinstance(bind, sqlalchemy.Engine):
        with bind.connect() as connection:
            return list(connection.execute(statement, dict(params)))
    return list(bind.execute(statement, dict(params)))


# PostgreSQL ----------------------------------------------------------------------------------------------------------

_PG_CONSTRAINTS = (
    "SELECT c.conname, c.contype, c.condeferrable, c.condeferred, c.convalidated, c.confmatchtype, "
    "pg_catalog.pg_get_constraintdef(c.oid, true), ci.relname "
    "FROM pg_catalog.pg_constraint c "
    "JOIN pg_catalog.pg_class r ON r.oid = c.conrelid "
    "JOIN pg_catalog.pg_namespace n ON n.oid = r.relnamespace "
    "LEFT JOIN pg_catalog.pg_class ci ON ci.oid = c.conindid "
    "WHERE n.nspname = :s AND r.relname = :t AND c.contype IN ('c', 'f', 'u', 'x')"
)
# One row per index of the table: its access method, indisvalid, per key element the collation (when it is not the
# database default, oid 100, or none) and the operator class (when it is not the default for its type), and the
# INCLUDE columns (SQLAlchemy 2.0.30 does not report them for a unique constraint).
_PG_INDEXES = (
    "SELECT i.relname, am.amname, x.indisvalid, "
    "ARRAY(SELECT CASE WHEN x.indcollation[k] IN (0, 100) THEN '' ELSE "
    "(SELECT co.collname FROM pg_catalog.pg_collation co WHERE co.oid = x.indcollation[k]) END "
    "FROM generate_series(0, x.indnkeyatts - 1) k ORDER BY k), "
    "ARRAY(SELECT CASE WHEN oc.opcdefault THEN '' ELSE oc.opcname END "
    "FROM generate_series(0, x.indnkeyatts - 1) k JOIN pg_catalog.pg_opclass oc ON oc.oid = x.indclass[k] "
    "ORDER BY k), "
    "ARRAY(SELECT a.attname FROM generate_series(x.indnkeyatts, x.indnatts - 1) k "
    "JOIN pg_catalog.pg_attribute a ON a.attrelid = x.indrelid AND a.attnum = x.indkey[k] ORDER BY k) "
    "FROM pg_catalog.pg_index x "
    "JOIN pg_catalog.pg_class i ON i.oid = x.indexrelid "
    "JOIN pg_catalog.pg_class r ON r.oid = x.indrelid "
    "JOIN pg_catalog.pg_namespace n ON n.oid = r.relnamespace "
    "JOIN pg_catalog.pg_am am ON am.oid = i.relam "
    "WHERE n.nspname = :s AND r.relname = :t"
)

# One row per column: identity, generated and its expression, and the collation when it is not the default of the
# column's type, with its schema when the collation is not visible on the search path (as SQLAlchemy 2.1 reflects it).
_PG_COLUMNS = (
    "SELECT a.attname, a.attidentity, a.attgenerated, "
    "CASE WHEN a.attgenerated <> '' THEN pg_catalog.pg_get_expr(d.adbin, d.adrelid) END, "
    "CASE WHEN a.attcollation <> 0 AND a.attcollation <> t.typcollation THEN co.collname END, "
    "CASE WHEN a.attcollation <> 0 AND a.attcollation <> t.typcollation "
    "AND NOT pg_catalog.pg_collation_is_visible(co.oid) THEN cn.nspname END "
    "FROM pg_catalog.pg_attribute a "
    "JOIN pg_catalog.pg_class r ON r.oid = a.attrelid "
    "JOIN pg_catalog.pg_namespace n ON n.oid = r.relnamespace "
    "JOIN pg_catalog.pg_type t ON t.oid = a.atttypid "
    "LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum "
    "LEFT JOIN pg_catalog.pg_collation co ON co.oid = a.attcollation "
    "LEFT JOIN pg_catalog.pg_namespace cn ON cn.oid = co.collnamespace "
    "WHERE n.nspname = :s AND r.relname = :t AND a.attnum > 0 AND NOT a.attisdropped"
)
_IDENTITY = {"a": "ALWAYS", "d": "BY DEFAULT"}
_GENERATED = {"s": "STORED", "v": "VIRTUAL"}

# ADR 0015 D-5 (TAB-XW-4): triggers, policies, the functions they call directly, and the table features the shape does
# not describe. Names are bound as parameters; every catalog name is qualified.
_PG_TRIGGERS = (
    "WITH rel AS (SELECT r.oid FROM pg_catalog.pg_class r "
    "JOIN pg_catalog.pg_namespace n ON n.oid = r.relnamespace WHERE n.nspname = :s AND r.relname = :t) "
    "SELECT g.tgname, pg_catalog.pg_get_triggerdef(g.oid, true), g.tgenabled FROM pg_catalog.pg_trigger g "
    "WHERE g.tgrelid IN (SELECT oid FROM rel) AND NOT g.tgisinternal"
)
_PG_POLICIES = (
    "WITH rel AS (SELECT r.oid FROM pg_catalog.pg_class r "
    "JOIN pg_catalog.pg_namespace n ON n.oid = r.relnamespace WHERE n.nspname = :s AND r.relname = :t) "
    "SELECT p.polname, p.polcmd, p.polpermissive, "
    "ARRAY(SELECT CASE WHEN o = 0 THEN 'PUBLIC' ELSE COALESCE("
    "(SELECT a.rolname FROM pg_catalog.pg_roles a WHERE a.oid = o), o::text) END "
    "FROM pg_catalog.unnest(p.polroles) o ORDER BY 1), "
    "pg_catalog.pg_get_expr(p.polqual, p.polrelid), pg_catalog.pg_get_expr(p.polwithcheck, p.polrelid) "
    "FROM pg_catalog.pg_policy p WHERE p.polrelid IN (SELECT oid FROM rel)"
)
# The trigger functions (by tgfoid, so a built-in one is found too) and every function a trigger's WHEN condition or a
# policy expression depends on (pg_depend; PostgreSQL records no dependency on a built-in, pinned function).
_PG_FUNCTIONS = (
    "WITH rel AS (SELECT r.oid FROM pg_catalog.pg_class r "
    "JOIN pg_catalog.pg_namespace n ON n.oid = r.relnamespace WHERE n.nspname = :s AND r.relname = :t), "
    "trg AS (SELECT g.oid, g.tgfoid FROM pg_catalog.pg_trigger g "
    "WHERE g.tgrelid IN (SELECT oid FROM rel) AND NOT g.tgisinternal), "
    "pol AS (SELECT p.oid FROM pg_catalog.pg_policy p WHERE p.polrelid IN (SELECT oid FROM rel)) "
    "SELECT pg_catalog.quote_ident(n.nspname) || '.' || pg_catalog.quote_ident(f.proname) || '(' "
    "|| pg_catalog.pg_get_function_identity_arguments(f.oid) || ')', l.lanname, "
    "CASE WHEN f.prokind IN ('f', 'p') THEN pg_catalog.pg_get_functiondef(f.oid) END "
    "FROM pg_catalog.pg_proc f "
    "JOIN pg_catalog.pg_namespace n ON n.oid = f.pronamespace "
    "JOIN pg_catalog.pg_language l ON l.oid = f.prolang "
    "WHERE f.oid IN (SELECT tgfoid FROM trg UNION SELECT d.refobjid FROM pg_catalog.pg_depend d "
    "WHERE d.refclassid = 'pg_catalog.pg_proc'::pg_catalog.regclass AND ("
    "(d.classid = 'pg_catalog.pg_trigger'::pg_catalog.regclass AND d.objid IN (SELECT oid FROM trg)) "
    "OR (d.classid = 'pg_catalog.pg_policy'::pg_catalog.regclass AND d.objid IN (SELECT oid FROM pol))))"
)
_PG_TABLE = (
    "SELECT r.relkind, r.relpersistence, r.relispartition, r.reloftype <> 0, r.reloptions IS NOT NULL, "
    "r.relreplident, r.relrowsecurity, r.relforcerowsecurity, am.amname, "
    "EXISTS (SELECT 1 FROM pg_catalog.pg_inherits i WHERE i.inhrelid = r.oid OR i.inhparent = r.oid), "
    "ARRAY(SELECT w.rulename FROM pg_catalog.pg_rewrite w WHERE w.ev_class = r.oid ORDER BY 1), "
    "ARRAY(SELECT a.attname FROM pg_catalog.pg_attribute a "
    "JOIN pg_catalog.pg_type y ON y.oid = a.atttypid "
    "LEFT JOIN pg_catalog.pg_type e ON e.oid = y.typelem AND y.typcategory = 'A' "
    "WHERE a.attrelid = r.oid AND a.attnum > 0 AND NOT a.attisdropped "
    "AND (y.typtype = 'd' OR e.typtype = 'd') ORDER BY a.attnum), "
    "ARRAY(SELECT c.conname FROM pg_catalog.pg_constraint c WHERE c.conrelid = r.oid AND c.contype = 'c' "
    "AND c.connoinherit ORDER BY 1) "
    "FROM pg_catalog.pg_class r "
    "JOIN pg_catalog.pg_namespace n ON n.oid = r.relnamespace "
    "LEFT JOIN pg_catalog.pg_am am ON am.oid = r.relam "
    "WHERE n.nspname = :s AND r.relname = :t"
)
_ENABLED = {"D": "DISABLED", "R": "REPLICA", "A": "ALWAYS"}
_COMMAND = {"*": "ALL", "r": "SELECT", "a": "INSERT", "w": "UPDATE", "d": "DELETE"}
_RELKIND = {"r": None, "p": "partitioned", "f": "foreign_table"}
_PERSISTENCE = {"p": None, "u": "unlogged", "t": "temporary"}


def _postgres_features(row: Sequence[Any]) -> tuple[bool, bool, tuple[str, ...]]:
    """``(row_security, force_row_security, features)`` from a row of ``_PG_TABLE``: an allowlist of the relation
    kind and the table properties the shape describes; anything else becomes a marker (ADR 0015 D-5.2, D-5.3)."""
    kind, persistence, partition, typed, options, replica, rls, force, method, inherits, rules, domains, noinherit = row
    out: set[str] = set()
    kind_marker = _RELKIND.get(str(kind), f"relkind:{kind}")
    if kind_marker:
        out.add(kind_marker)
    persistence_marker = _PERSISTENCE.get(str(persistence), f"persistence:{persistence}")
    if persistence_marker:
        out.add(persistence_marker)
    if partition:
        out.add("partition")
    if inherits and not partition and str(kind) != "p":
        out.add("inheritance")
    if typed:
        out.add("typed_table")
    if options:
        out.add("storage_parameters")
    if str(replica) != "d":
        out.add(f"replica_identity:{replica}")
    if method is not None and str(method) != "heap":
        out.add(f"access_method:{method}")
    out.update(f"rule:{name}" for name in rules)
    out.update(f"domain:{name}" for name in domains)
    out.update(f"no_inherit:{name}" for name in noinherit)
    return bool(rls), bool(force), tuple(sorted(out))


def _collation_text(preparer: Any, name: str, schema: str | None) -> str:
    """The collation as SQLAlchemy 2.1's ``format_collation`` writes it (2.0.30 has no schema argument)."""
    text = preparer.quote(name) if getattr(preparer, "quote_case_sensitive_collations", True) else name
    return text if schema is None else f"{preparer.quote_schema(schema)}.{text}"


def _postgres_catalog(inspector: sqlalchemy.Inspector, schema: str, table: str) -> _Catalog:
    # SQLAlchemy reflects neither DEFERRABLE of a unique constraint, NOT VALID, MATCH, exclusion constraints, nor the
    # collations and operator classes of index elements; the catalog has them. Names are bound as parameters.
    params = {"s": schema, "t": table}
    constraints: dict[str, _PgConstraint] = {}
    exclusions = []
    for name, kind, deferrable, deferred, validated, match, definition, index in _rows(
        inspector, _PG_CONSTRAINTS, params
    ):
        constraints[str(name)] = _PgConstraint(
            bool(deferrable), bool(deferred), bool(validated), _MATCH.get(str(match)), _optional_name(index)
        )
        if kind == "x":
            exclusions.append(ExclusionShape(name=str(name), definition=str(definition)))
    indexes = {
        str(name): _PgIndex(
            method=None if method == "btree" else str(method),
            valid=bool(valid),
            collations=_defaults([str(c or "") for c in collations]),
            opclasses=_defaults([str(o or "") for o in opclasses]),
            include=tuple(str(c) for c in include),
        )
        for name, method, valid, collations, opclasses, include in _rows(inspector, _PG_INDEXES, params)
    }
    preparer = inspector.dialect.identifier_preparer
    columns = {
        str(name): _Column(
            identity=_IDENTITY.get(str(identity or "")),
            generated=_GENERATED.get(str(generated or "")),
            expression=_optional_name(expression),
            collation=None if collation is None else _collation_text(preparer, str(collation), _optional_name(where)),
        )
        for name, identity, generated, expression, collation, where in _rows(inspector, _PG_COLUMNS, params)
    }
    triggers = tuple(
        TriggerShape(name=str(name), definition=str(definition), enabled=_ENABLED.get(str(enabled)))
        for name, definition, enabled in _rows(inspector, _PG_TRIGGERS, params)
    )
    policies = tuple(
        PolicyShape(
            name=str(name),
            command=_COMMAND.get(str(command), str(command)),
            permissive=bool(permissive),
            roles=tuple(sorted(str(role) for role in roles)),
            using=_optional_name(using),
            check=_optional_name(check),
        )
        for name, command, permissive, roles, using, check in _rows(inspector, _PG_POLICIES, params)
    )
    functions = tuple(
        FunctionShape(signature=str(signature), language=str(language), definition=str(definition or ""))
        for signature, language, definition in _rows(inspector, _PG_FUNCTIONS, params)
    )
    table_rows = _rows(inspector, _PG_TABLE, params)
    rls, force, features = _postgres_features(table_rows[0]) if table_rows else (False, False, ())
    return _Catalog(
        constraints,
        indexes,
        tuple(exclusions),
        columns,
        triggers=triggers,
        policies=policies,
        functions=functions,
        row_security=rls,
        force_row_security=force,
        features=features,
    )


_PG_SORTING = {"desc": "DESC", "nulls_first": "NULLS FIRST", "nulls_last": "NULLS LAST"}


def _foreign_keys(
    inspector: sqlalchemy.Inspector, table: str, arg: str | None, default: str, catalog: _Catalog
) -> list[ForeignKeyShape]:
    out = []
    for fk in _reflect(inspector.get_foreign_keys, table, arg):
        options = fk.get("options") or {}
        name = _optional_name(fk.get("name"))
        found = catalog.constraints.get(name or "")
        out.append(
            ForeignKeyShape(
                name=name,
                columns=tuple(str(c) for c in fk["constrained_columns"]),
                referred_table=f"{fk.get('referred_schema') or default}.{fk['referred_table']}",
                referred_columns=tuple(str(c) for c in fk["referred_columns"]),
                on_delete=_action(options.get("ondelete")),
                on_update=_action(options.get("onupdate")),
                deferrable=found.deferrable if found else bool(options.get("deferrable")),
                initially_deferred=(
                    found.initially_deferred if found else str(options.get("initially") or "").upper() == "DEFERRED"
                ),
                match=found.match if found else None,
                validated=found.validated if found else True,
            )
        )
    return out


def _unique_constraints(
    inspector: sqlalchemy.Inspector, table: str, arg: str | None, catalog: _Catalog
) -> list[UniqueShape]:
    out = []
    for u in _reflect(inspector.get_unique_constraints, table, arg):
        options = u.get("dialect_options") or {}
        name = _optional_name(u.get("name"))
        found = catalog.constraints.get(name or "")
        index = catalog.indexes.get(found.index or "") if found else None
        out.append(
            UniqueShape(
                name=name,
                columns=tuple(str(c) for c in u["column_names"]),
                include=index.include if index else tuple(str(c) for c in options.get("postgresql_include") or ()),
                nulls_not_distinct=bool(options.get("postgresql_nulls_not_distinct")),
                deferrable=found.deferrable if found else False,
                initially_deferred=found.initially_deferred if found else False,
                collations=index.collations if index else (),
            )
        )
    return out


def _indexes(inspector: sqlalchemy.Inspector, table: str, arg: str | None, catalog: _Catalog) -> list[IndexShape]:
    out = []
    for index in _reflect(inspector.get_indexes, table, arg):
        if index.get("duplicates_constraint"):
            continue  # the index of a unique or exclusion constraint (PostgreSQL), already in the shape
        names = list(index.get("column_names") or ())
        expressions = list(index.get("expressions") or ())
        elements = [
            str(expressions[i]) if name is None and i < len(expressions) else str(name) for i, name in enumerate(names)
        ]
        # column_sorting is keyed by the element text (the expression when SQLAlchemy reports expressions)
        keys = [str(expressions[i]) if i < len(expressions) else element for i, element in enumerate(elements)]
        sorting = index.get("column_sorting") or {}
        options = index.get("dialect_options") or {}
        where = options.get("postgresql_where")
        found = catalog.indexes.get(str(index["name"]))
        out.append(
            IndexShape(
                name=str(index["name"]),
                columns=tuple(elements),
                unique=bool(index.get("unique")),
                ordering=_defaults(
                    [" ".join(_PG_SORTING.get(str(o), str(o)) for o in sorting.get(k, ())) for k in keys]
                ),
                where=None if where is None else str(where),
                include=found.include if found else tuple(str(c) for c in options.get("postgresql_include") or ()),
                nulls_not_distinct=bool(options.get("postgresql_nulls_not_distinct")),
                expressions=tuple(i for i, name in enumerate(names) if name is None),
                collations=found.collations if found else (),
                opclasses=found.opclasses if found else (),
                method=found.method if found else None,
                valid=found.valid if found else True,
            )
        )
    return out


# SQLite --------------------------------------------------------------------------------------------------------------


def _sqlite_foreign_keys(
    inspector: sqlalchemy.Inspector, schema: str, table: str, arg: str | None
) -> list[ForeignKeyShape]:
    # Every foreign key comes from pragma_foreign_key_list, with its actions: SQLAlchemy reads ON DELETE/ON UPDATE from
    # the CREATE TABLE text, misses them on a column-level REFERENCES and reports only one of two keys with the same
    # columns. Its parse supplies the name and DEFERRABLE only where exactly one key of the pragma and one of its own
    # have the same columns and referred table and columns; otherwise the key is unnamed and not deferrable.
    rows = _rows(
        inspector,
        'SELECT id, "table", "from", "to", on_update, on_delete FROM pragma_foreign_key_list(:t, :s) ORDER BY id, seq',
        {"t": table, "s": schema},
    )
    groups: dict[int, list[Any]] = {}
    for row in rows:
        groups.setdefault(int(row[0]), []).append(row)
    keys = []
    for group in groups.values():
        referred = str(group[0][1])
        to = [row[3] for row in group]
        if all(t is None for t in to):  # REFERENCES t without columns: the primary key of t
            try:
                pk = inspector.get_pk_constraint(referred, schema=arg)
                referred_columns = tuple(str(c) for c in pk.get("constrained_columns") or ())
            except sqlalchemy.exc.NoSuchTableError:
                referred_columns = ()
        else:
            referred_columns = tuple(str(t) for t in to)
        keys.append(
            (
                tuple(str(row[2]) for row in group),
                referred,
                referred_columns,
                _action(group[0][5]),
                _action(group[0][4]),
            )
        )
    parsed: dict[tuple[Any, ...], list[Any]] = {}
    for fk in _reflect(inspector.get_foreign_keys, table, arg):
        signature = (
            tuple(str(c) for c in fk["constrained_columns"]),
            str(fk["referred_table"]),
            tuple(str(c) for c in fk["referred_columns"]),
        )
        parsed.setdefault(signature, []).append(fk)
    signatures = [key[:3] for key in keys]
    out = []
    for columns, referred, referred_columns, on_delete, on_update in keys:
        signature = (columns, referred, referred_columns)
        matches = parsed.get(signature, [])
        fk = matches[0] if len(matches) == 1 and signatures.count(signature) == 1 else None
        options = (fk.get("options") or {}) if fk else {}
        out.append(
            ForeignKeyShape(
                name=_optional_name(fk.get("name")) if fk else None,
                columns=columns,
                referred_table=f"{schema}.{referred}",
                referred_columns=referred_columns,
                on_delete=on_delete,
                on_update=on_update,
                deferrable=bool(options.get("deferrable")),
                initially_deferred=str(options.get("initially") or "").upper() == "DEFERRED",
            )
        )
    return out


def _sqlite_indexes(
    inspector: sqlalchemy.Inspector, schema: str, table: str
) -> tuple[list[IndexShape], list[UniqueShape]]:
    # Indexes and unique constraints from the pragmas: SQLAlchemy leaves out an expression index (with a warning, also
    # from its unique reflection), and SQLite keeps no unique constraint name it can address. An index made by CREATE
    # INDEX has origin "c", the index of a UNIQUE constraint origin "u", that of a primary key "pk". The expression
    # texts and the WHERE predicate are only in the CREATE INDEX statement. SQLite refuses NULLS FIRST/LAST in an
    # index, so DESC (pragma_index_xinfo) is the whole order of an element.
    quoted = inspector.dialect.identifier_preparer.quote_identifier(schema)
    indexes, unique = [], []
    for name, is_unique, origin, partial in _rows(
        inspector, 'SELECT name, "unique", origin, partial FROM pragma_index_list(:t, :s)', {"t": table, "s": schema}
    ):
        if origin not in ("c", "u"):
            continue
        keys = _rows(
            inspector,
            'SELECT cid, name, "desc", coll FROM pragma_index_xinfo(:i, :s) WHERE key = 1 ORDER BY seqno',
            {"i": name, "s": schema},
        )
        collations = _defaults(["" if str(coll).upper() == "BINARY" else str(coll) for _, _, _, coll in keys])
        if origin == "u":
            unique.append(
                UniqueShape(name=None, columns=tuple(str(column) for _, column, _, _ in keys), collations=collations)
            )
            continue
        texts: list[str] = []
        where = None
        if partial or any(cid == -2 for cid, _, _, _ in keys):
            # The schema name is quoted by the dialect's preparer; the index name is a bound parameter.
            found = _rows(
                inspector,
                f"SELECT sql FROM {quoted}.sqlite_master WHERE type = 'index' AND name = :i",  # noqa: S608
                {"i": name},
            )
            sql = str(found[0][0]) if found and found[0][0] is not None else ""
            parsed = _sqlite_index_parts(sql)
            if parsed is not None and len(parsed[0]) == len(keys):
                texts = [_strip_order(element) for element in parsed[0]]
                where = _where(parsed[1]) if partial else None
            else:  # not parsed (or too long): the whole statement stands for each expression and the predicate
                texts = [sql] * len(keys)
            if partial and where is None:
                where = sql
        indexes.append(
            IndexShape(
                name=str(name),
                columns=tuple(
                    texts[position] if cid == -2 else "rowid" if cid == -1 else str(column)
                    for position, (cid, column, _, _) in enumerate(keys)
                ),
                unique=bool(is_unique),
                ordering=_defaults(["DESC" if desc else "" for _, _, desc, _ in keys]),
                where=where,
                expressions=tuple(position for position, (cid, _, _, _) in enumerate(keys) if cid == -2),
                collations=collations,
            )
        )
    return indexes, unique


_MAX_TABLE_SQL = 1_000_000
"""Longest SQLite ``CREATE TABLE`` text whose CHECK constraints and generated columns are read one by one; a longer
one stands whole."""


def _sqlite_table_sql(inspector: sqlalchemy.Inspector, schema: str, table: str) -> str:
    # The schema name is quoted by the dialect's preparer; the table name is a bound parameter.
    quoted = inspector.dialect.identifier_preparer.quote_identifier(schema)
    found = _rows(
        inspector,
        f"SELECT sql FROM {quoted}.sqlite_master WHERE type = 'table' AND name = :t",  # noqa: S608
        {"t": table},
    )
    return str(found[0][0]) if found and found[0][0] is not None else ""


def _sqlite_columns(inspector: sqlalchemy.Inspector, schema: str, table: str) -> dict[str, _Column]:
    # Generated columns from pragma_table_xinfo (hidden 2 is VIRTUAL, 3 STORED) and their expressions from the CREATE
    # TABLE text with the tokenizer: SQLAlchemy's regular expression reads them wrongly when a table has two of them.
    rows = _rows(inspector, "SELECT name, hidden FROM pragma_table_xinfo(:t, :s)", {"t": table, "s": schema})
    generated = {str(name): "STORED" if hidden == 3 else "VIRTUAL" for name, hidden in rows if hidden in (2, 3)}
    if not generated:
        return {}
    sql = _sqlite_table_sql(inspector, schema, table)
    expressions = _sqlite_generated_expressions(sql) or {}
    folded = {name.casefold(): text for name, text in expressions.items()}
    return {
        name: _Column(
            identity=None,
            generated=kind,
            # not read alone: the whole statement stands for the expression (as for a CHECK, I-111 c)
            expression=expressions.get(name, folded.get(name.casefold(), sql)),
            collation=None,
        )
        for name, kind in generated.items()
    }


def _sqlite_triggers(inspector: sqlalchemy.Inspector, schema: str, table: str) -> tuple[TriggerShape, ...]:
    # The CREATE TRIGGER text as sqlite_schema keeps it (ADR 0015 D-5.1); a rebuild recreates the trigger from it under
    # the checks of K-1 (tabrion.plan). The schema name is quoted by the dialect's preparer; the table name is bound.
    quoted = inspector.dialect.identifier_preparer.quote_identifier(schema)
    rows = _rows(
        inspector,
        f"SELECT name, sql FROM {quoted}.sqlite_master "  # noqa: S608
        "WHERE type = 'trigger' AND tbl_name = :t COLLATE NOCASE",
        {"t": table},
    )
    return tuple(TriggerShape(name=str(name), definition=str(sql or "")) for name, sql in rows)


def _sqlite_features(sql: str, deferrable_keys: int) -> tuple[str, ...]:
    """Markers of what a SQLite ``CREATE TABLE`` text holds beyond the shape, which a table rebuild from the shape
    would lose (ADR 0015 D-5.3, contract I-111 i): ``virtual_table``, ``strict``, ``without_rowid``,
    ``autoincrement``, ``on_conflict``, ``collation`` (a ``COLLATE`` in a column definition or a table-level
    ``PRIMARY KEY``),
    ``deferrable_reference`` (more ``DEFERRABLE`` clauses than deferrable foreign keys in the shape: a column-level
    ``REFERENCES ... DEFERRABLE``) and ``unparsed_table_sql`` (a text that is too long or cannot be tokenized).
    Linear in the length."""
    if not sql or len(sql) > _MAX_TABLE_SQL:
        return ("unparsed_table_sql",)
    tokens = _sql_tokens(sql)
    if tokens is None:
        return ("unparsed_table_sql",)
    out: set[str] = set()
    words = [text.upper() if kind == "word" else "" for kind, text, _, _ in tokens]
    if words[1:3] == ["VIRTUAL", "TABLE"] or words[2:4] == ["VIRTUAL", "TABLE"]:
        out.add("virtual_table")
    depth, closed, deferrable, opener = 0, -1, 0, ""
    for i, (kind, _, _, _) in enumerate(tokens):
        word = words[i]
        if kind == "(":
            depth += 1
            if depth == 2:
                opener = words[i - 1]  # KEY of PRIMARY KEY (...), UNIQUE, CHECK, a type name ...
        elif kind == ")":
            depth -= 1
            if depth == 0 and closed == -1:
                closed = i
        elif word == "AUTOINCREMENT":
            out.add("autoincrement")
        elif word == "CONFLICT" and i > 0 and words[i - 1] == "ON":
            out.add("on_conflict")
        elif word == "COLLATE" and (depth == 1 or (depth == 2 and opener == "KEY")):
            # a column collation or one in a table-level PRIMARY KEY; UNIQUE and CHECK keep theirs in the shape
            out.add("collation")
        elif word == "DEFERRABLE" and not (i > 0 and words[i - 1] == "NOT"):
            deferrable += 1
    if depth != 0 or closed == -1:
        return ("unparsed_table_sql",)
    tail = words[closed + 1 :]
    if "STRICT" in tail:
        out.add("strict")
    if any(word == "WITHOUT" and tail[j + 1 : j + 2] == ["ROWID"] for j, word in enumerate(tail)):
        out.add("without_rowid")
    if deferrable > deferrable_keys:
        out.add("deferrable_reference")
    return tuple(sorted(out))


def _sqlite_generated_expressions(sql: str) -> dict[str, str] | None:
    """Column name to the expression of each ``AS (...)`` of a column definition of a ``CREATE TABLE`` text, as
    written between its parentheses; ``None`` when the text is too long or cannot be tokenized. Linear in the
    length."""
    if len(sql) > _MAX_TABLE_SQL:
        return None
    tokens = _sql_tokens(sql)
    if tokens is None:
        return None
    out: dict[str, str] = {}
    depth, first, i = 0, True, 0
    name: str | None = None
    while i < len(tokens):
        kind, text = tokens[i][0], tokens[i][1]
        if kind == "(":
            depth += 1
            if depth == 1:
                first = True
        elif kind == ")":
            depth -= 1
            if depth < 0:
                return None
        elif kind == "," and depth == 1:
            first = True
        elif depth == 1 and first:
            name, first = (_unquote(text) if kind in ("word", "ident") else None), False
        elif (
            depth == 1
            and kind == "word"
            and text.upper() == "AS"
            and name is not None
            and i + 1 < len(tokens)
            and tokens[i + 1][0] == "("
        ):
            level, j = 0, i + 1
            while j < len(tokens):
                if tokens[j][0] == "(":
                    level += 1
                elif tokens[j][0] == ")":
                    level -= 1
                    if level == 0:
                        break
                j += 1
            if j == len(tokens):
                return None
            out.setdefault(name, sql[tokens[i + 1][3] : tokens[j][2]].strip())
            i = j + 1
            continue
        i += 1
    return out if depth == 0 else None


def _sqlite_checks(inspector: sqlalchemy.Inspector, schema: str, table: str) -> list[dict[str, Any]]:
    # SQLAlchemy reads SQLite CHECK constraints with a regular expression over the CREATE TABLE text, and up to at
    # least 2.0.30 (the floor of the extra) it is greedy: two checks became one text that ran to the last ")". This
    # reads them with a linear tokenizer instead, the same on every SQLAlchemy version.
    sql = _sqlite_table_sql(inspector, schema, table)
    checks = _sqlite_check_constraints(sql)
    if checks is None:  # too long or not tokenized: the whole statement stands for its checks
        return [{"name": None, "sqltext": sql}] if "check" in sql.lower() else []
    return [{"name": name, "sqltext": text} for name, text in checks]


def _sql_tokens(sql: str) -> list[tuple[str, str, int, int]] | None:
    """SQLite tokens ``(kind, text, start, end)``: ``word``, ``ident`` (quoted), ``string``, ``(``, ``)``, ``,`` or
    ``other``; blanks and comments are skipped. ``None`` for an unterminated quote. Linear in the length."""
    out: list[tuple[str, str, int, int]] = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch.isspace():
            i += 1
        elif sql.startswith("--", i):
            end = sql.find("\n", i)
            i = n if end == -1 else end + 1
        elif sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            i = n if end == -1 else end + 2
        elif ch in "'\"`[":
            close = "]" if ch == "[" else ch
            end = sql.find(close, i + 1)
            while end != -1 and close != "]" and sql[end + 1 : end + 2] == close:  # a doubled quote
                end = sql.find(close, end + 2)
            if end == -1:
                return None
            out.append(("string" if ch == "'" else "ident", sql[i : end + 1], i, end + 1))
            i = end + 1
        elif ch in "(),":
            out.append((ch, ch, i, i + 1))
            i += 1
        elif ch.isalnum() or ch in "_$":
            end = i
            while end < n and (sql[end].isalnum() or sql[end] in "_$"):
                end += 1
            out.append(("word", sql[i:end], i, end))
            i = end
        else:
            out.append(("other", ch, i, i + 1))
            i += 1
    return out


def _unquote(text: str) -> str:
    if text[:1] == "[":
        return text[1:-1]
    if text[:1] in ('"', "`", "'"):
        return text[1:-1].replace(text[0] * 2, text[0])
    return text


def _sqlite_check_constraints(sql: str) -> list[tuple[str | None, str]] | None:
    """The ``(name, expression)`` of every CHECK of a ``CREATE TABLE`` text, column and table level, in order; the
    expression as written between its parentheses. ``None`` when the text is longer than ``_MAX_TABLE_SQL``, has an
    unterminated quote or unbalanced parentheses."""
    if len(sql) > _MAX_TABLE_SQL:
        return None
    tokens = _sql_tokens(sql)
    if tokens is None:
        return None
    out: list[tuple[str | None, str]] = []
    depth, i = 0, 0
    while i < len(tokens):
        kind, text = tokens[i][0], tokens[i][1]
        if kind == "(":
            depth += 1
        elif kind == ")":
            depth -= 1
            if depth < 0:
                return None
        elif (
            kind == "word"
            and text.upper() == "CHECK"
            and depth >= 1
            and i + 1 < len(tokens)
            and tokens[i + 1][0] == "("
        ):
            level, j = 0, i + 1
            while j < len(tokens):
                if tokens[j][0] == "(":
                    level += 1
                elif tokens[j][0] == ")":
                    level -= 1
                    if level == 0:
                        break
                j += 1
            if j == len(tokens):
                return None
            name = None
            if i >= 2 and tokens[i - 2][0] == "word" and tokens[i - 2][1].upper() == "CONSTRAINT":
                name = _unquote(tokens[i - 1][1])
            out.append((name, sql[tokens[i + 1][3] : tokens[j][2]].strip()))
            i = j + 1
            continue
        i += 1
    return out if depth == 0 else None


def _strip_order(element: str) -> str:
    """An index element without a trailing ``ASC`` or ``DESC`` (linear: no regular expression, review of #85 F1)."""
    text = element.rstrip()
    head = text.rsplit(None, 1)
    if len(head) == 2 and head[1].upper() in ("ASC", "DESC"):
        return head[0].rstrip()
    return text


def _where(rest: str) -> str | None:
    """The predicate after ``WHERE`` in the text after the element list, or ``None`` (linear, review of #85 F1)."""
    text = rest.strip()
    if text[:5].upper() == "WHERE" and len(text) > 5 and not (text[5].isalnum() or text[5] == "_"):
        return text[5:].strip() or None
    return None


def _sqlite_index_parts(sql: str) -> tuple[list[str], str] | None:
    """The key elements of ``CREATE INDEX ... ON t (a, lower(b) DESC) WHERE ...`` as written and the text after the
    element list; ``None`` when the text has no element list or is longer than ``_MAX_INDEX_SQL``. Quotes and comments
    are skipped; each character is looked at a bounded number of times."""
    if len(sql) > _MAX_INDEX_SQL:
        return None
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
                return [part.strip() for part in parts], sql[i + 1 :]
        elif ch == "," and depth == 1 and start != -1:
            parts.append(sql[start:i])
            start = i + 1
        i += 1
    return None


# --- level 2: canonical order and comparison ------------------------------------------------------------------------


def _canonical_index(index: IndexShape) -> IndexShape:
    return dataclasses.replace(
        index,
        ordering=_defaults(index.ordering),
        collations=_defaults(index.collations),
        opclasses=_defaults(index.opclasses),
        expressions=tuple(sorted(set(index.expressions))),
    )


def _canonical(table: TableShape) -> TableShape:
    """The table with its constraints and indexes in a fixed order and their defaults written one way, so neither
    the order nor the spelling of a default counts."""
    return TableShape(
        name=table.name,
        columns=table.columns,
        primary_key=table.primary_key,
        foreign_keys=tuple(
            sorted(
                table.foreign_keys,
                key=lambda f: (f.columns, f.referred_table, f.referred_columns, f.name or "", repr(f)),
            )
        ),
        unique_constraints=tuple(
            sorted(
                (dataclasses.replace(u, collations=_defaults(u.collations)) for u in table.unique_constraints),
                key=lambda u: (u.columns, u.name or "", repr(u)),
            )
        ),
        check_constraints=tuple(sorted(table.check_constraints, key=lambda c: (c.name or "", c.sqltext, repr(c)))),
        indexes=tuple(sorted((_canonical_index(x) for x in table.indexes), key=lambda x: (x.name, repr(x)))),
        exclusion_constraints=tuple(sorted(table.exclusion_constraints, key=lambda x: (x.name, x.definition))),
        triggers=tuple(sorted(table.triggers, key=lambda t: (t.name, t.definition, repr(t)))),
        policies=tuple(
            sorted(
                (dataclasses.replace(p, roles=tuple(sorted(p.roles))) for p in table.policies),
                key=lambda p: (p.name, repr(p)),
            )
        ),
        functions=tuple(sorted(table.functions, key=lambda f: (f.signature, f.language, f.definition))),
        row_security=table.row_security,
        force_row_security=table.force_row_security,
        features=tuple(sorted(set(table.features))),
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


_Constraint = ForeignKeyShape | UniqueShape | CheckShape | IndexShape | ExclusionShape


def _constraint_differences(
    table: str, old: TableShape, new: TableShape, column_names: set[str]
) -> list[SchemaDifference]:
    def column(item: _Constraint) -> str | None:
        if isinstance(item, CheckShape | ExclusionShape):
            return None
        if isinstance(item, IndexShape) and item.expressions:
            return None
        columns = item.columns
        return columns[0] if len(columns) == 1 and columns[0] in column_names else None

    def pair(
        missing: DifferenceKind, added: DifferenceKind, expected: Sequence[_Constraint], actual: Sequence[_Constraint]
    ) -> list[SchemaDifference]:
        return [SchemaDifference(missing, table, column(x), x.name) for x in _unmatched(expected, actual)] + [
            SchemaDifference(added, table, column(x), x.name) for x in _unmatched(actual, expected)
        ]

    def named(
        missing: DifferenceKind, added: DifferenceKind, expected: Sequence[Any], actual: Sequence[Any], key: str
    ) -> list[SchemaDifference]:
        return [SchemaDifference(missing, table, None, getattr(x, key)) for x in _unmatched(expected, actual)] + [
            SchemaDifference(added, table, None, getattr(x, key)) for x in _unmatched(actual, expected)
        ]

    out = [
        *pair("foreign_key_missing", "foreign_key_added", old.foreign_keys, new.foreign_keys),
        *pair("unique_missing", "unique_added", old.unique_constraints, new.unique_constraints),
        *pair("check_missing", "check_added", old.check_constraints, new.check_constraints),
        *pair("exclusion_missing", "exclusion_added", old.exclusion_constraints, new.exclusion_constraints),
        *pair("index_missing", "index_added", old.indexes, new.indexes),
        *named("trigger_missing", "trigger_added", old.triggers, new.triggers, "name"),
        *named("policy_missing", "policy_added", old.policies, new.policies, "name"),
        *named("function_missing", "function_added", old.functions, new.functions, "signature"),
    ]
    if (old.row_security, old.force_row_security) != (new.row_security, new.force_row_security):
        out.append(SchemaDifference("row_security", table))
    out.extend(SchemaDifference("feature_missing", table, None, f) for f in old.features if f not in new.features)
    out.extend(SchemaDifference("feature_added", table, None, f) for f in new.features if f not in old.features)
    return out


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
    # Format 2 writes every field of every shape (dataclasses.asdict: tuples become lists); new fields change it.
    tables = []
    for table in snapshot.tables:
        table = _canonical(table)
        entry: dict[str, Any] = {
            "name": table.name,
            "columns": [
                [c.name, c.type, c.nullable, c.default, c.identity, c.generated, c.generation_expression]
                for c in table.columns
            ],
            "primary_key": list(table.primary_key),
            "foreign_keys": [dataclasses.asdict(f) for f in table.foreign_keys],
            "unique_constraints": [dataclasses.asdict(u) for u in table.unique_constraints],
            "check_constraints": [dataclasses.asdict(c) for c in table.check_constraints],
            "exclusion_constraints": [dataclasses.asdict(x) for x in table.exclusion_constraints],
            "indexes": [dataclasses.asdict(x) for x in table.indexes],
        }
        # The D-5 parts (TAB-XW-4) are written only when they are not empty, so a table without them keeps the v2
        # fingerprint it had before they were added.
        if table.triggers:
            entry["triggers"] = [dataclasses.asdict(t) for t in table.triggers]
        if table.policies:
            entry["policies"] = [dataclasses.asdict(p) for p in table.policies]
        if table.functions:
            entry["functions"] = [dataclasses.asdict(f) for f in table.functions]
        if table.row_security or table.force_row_security:
            entry["row_security"] = [table.row_security, table.force_row_security]
        if table.features:
            entry["features"] = list(table.features)
        tables.append(entry)
    out: dict[str, Any] = {"format": 2, "dialect": snapshot.dialect, "tables": tables}
    if snapshot.features:
        out["features"] = sorted(set(snapshot.features))
    if snapshot.views:  # review of #89, P89-02; written only when not empty, as the D-5 keys
        out["views"] = [
            dataclasses.asdict(v) for v in sorted(set(snapshot.views), key=lambda v: (v.name, v.definition))
        ]
    return out


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


_ShapeT = TypeVar("_ShapeT")


def _shape(cls: type[_ShapeT], value: object) -> _ShapeT:
    """One shape from its format-2 object: exactly the dataclass's fields, each of its annotated type."""
    if not isinstance(value, dict) or set(value) != {f.name for f in dataclasses.fields(cls)}:  # type: ignore[arg-type]
        raise ValueError("not a shape")
    for field in dataclasses.fields(cls):  # type: ignore[arg-type]
        _check(field.type, value[field.name])
    return cls(**{k: tuple(v) if isinstance(v, list) else v for k, v in value.items()})


def _check(annotation: object, value: object) -> None:
    kinds = {
        "str": lambda v: isinstance(v, str),
        "str | None": lambda v: v is None or isinstance(v, str),
        "bool": lambda v: isinstance(v, bool),
        "tuple[str, ...]": lambda v: isinstance(v, list) and all(isinstance(i, str) for i in v),
        "tuple[int, ...]": lambda v: isinstance(v, list) and all(type(i) is int and i >= 0 for i in v),
    }
    if not kinds[str(annotation)](value):
        raise TypeError("wrong type")


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
        for name, type_name, nullable, default, identity, generated, expression in _list(table["columns"]):
            if not isinstance(nullable, bool):
                raise TypeError("nullable")
            columns.append(
                ColumnShape(
                    _str(name),
                    _str(type_name),
                    nullable,
                    _opt_str(default),
                    _opt_str(identity),
                    _opt_str(generated),
                    _opt_str(expression),
                )
            )
        indexes = tuple(_shape(IndexShape, x) for x in _list(table["indexes"]))
        if any(i >= len(x.columns) for x in indexes for i in x.expressions):
            raise ValueError("expression position")
        row_security = _list(table.get("row_security", [False, False]))
        if len(row_security) != 2 or not all(isinstance(flag, bool) for flag in row_security):
            raise TypeError("row_security")
        tables.append(
            _canonical(
                TableShape(
                    name=_str(table["name"]),
                    columns=tuple(columns),
                    primary_key=_strs(table["primary_key"]),
                    foreign_keys=tuple(_shape(ForeignKeyShape, f) for f in _list(table["foreign_keys"])),
                    unique_constraints=tuple(_shape(UniqueShape, u) for u in _list(table["unique_constraints"])),
                    check_constraints=tuple(_shape(CheckShape, c) for c in _list(table["check_constraints"])),
                    indexes=indexes,
                    exclusion_constraints=tuple(
                        _shape(ExclusionShape, x) for x in _list(table["exclusion_constraints"])
                    ),
                    triggers=tuple(_shape(TriggerShape, t) for t in _list(table.get("triggers", []))),
                    policies=tuple(_shape(PolicyShape, p) for p in _list(table.get("policies", []))),
                    functions=tuple(_shape(FunctionShape, f) for f in _list(table.get("functions", []))),
                    row_security=row_security[0],
                    force_row_security=row_security[1],
                    features=_strs(table.get("features", [])),
                )
            )
        )
    return SchemaSnapshot(
        dialect=_str(data["dialect"]),
        tables=tuple(sorted(tables, key=lambda t: t.name)),
        level=2,
        features=_strs(data.get("features", [])),
        views=tuple(
            sorted((_shape(ViewShape, v) for v in _list(data.get("views", []))), key=lambda v: (v.name, v.definition))
        ),
    )
