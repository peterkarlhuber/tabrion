"""Schema change plans: diff, risk classes, SQLite rebuild and Alembic revision text (since 0.3, experimental, extra
``sqlalchemy``; a plan with column steps also needs ``migrations``, as the column statements of :mod:`tabrion.ddl`
do; TAB-XW-4, CR-DBX).

Contract section 5.13 and implementation note I-124; ADR 0015 sections 2 to 4, 9 (K-1, K-3, N-2) and 10 (D-5).
:func:`plan` turns two level-2 snapshots of :mod:`tabrion.schema` (the database as it is and the target model) into
a :class:`MigrationPlan`: numbered steps, each with the DDL of :mod:`tabrion.ddl`, its risk classes and whether it
runs in a transaction, bound to both fingerprints by a ``plan_hash``. Nothing here connects or executes, except
:func:`migration_markers`, which only reads the catalog; the executor is ``execute_plan`` (TAB-XW-5)::

    with db.inspector() as inspector:
        current = tabrion.schema.snapshot(inspector, level=2)
    change = tabrion.plan.plan(current, target)
    for step in change.steps:
        print(step.index, step.operation, step.target, [r.value for r in step.risks])

**Fail closed (ADR 0015 D-5).** A plan never changes a table whose shape cannot be checked reliably: a PostgreSQL
table with a feature marker of the snapshot (partitioning, inheritance, rules, a foreign table, domain columns,
...), a table whose trigger or policy function is not written in PL/pgSQL or SQL, any change while the database has
an event trigger, and a SQLite rebuild of a table whose ``CREATE TABLE`` text holds what the shape does not
(``STRICT``, ``AUTOINCREMENT`` ...) refuse the plan (``PlanRefused``, reason ``unsupported_feature``).

**Schema SQL from the file (K-1).** A SQLite rebuild recreates the table's triggers from the text ``sqlite_schema``
holds, which in a hostile file is attacker data. Each text becomes exactly one statement, must be one complete
statement with nothing after it (``sqlite3.complete_statement``) and must start with ``CREATE TRIGGER`` and the
trigger's own name; otherwise ``plan`` refuses (reason ``unsafe_schema_sql``). The executor checks again.

**Revision text (K-3).** :func:`revision_text` writes the plan's statements into an Alembic revision as a string. The
module docstring is fixed, and ``revision``, ``down_revision``, the message, the fingerprints and every statement are
Python string literals, so no caller text can become code. Tabrion never writes the file, never reads a project and
never runs ``env.py``, Alembic commands or a revision (ADR 0015 section 2).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
import dataclasses
from dataclasses import dataclass
import enum
import hashlib
import json
import re
import sqlite3
from typing import TYPE_CHECKING, Any, Literal

try:
    import sqlalchemy
except ImportError:
    raise ImportError(
        'tabrion.plan needs the extra "sqlalchemy": '
        'pip install "tabrion[sqlalchemy] @ git+https://github.com/peterkarlhuber/tabrion@<commit-sha>"'
        " (Tabrion is not on PyPI: pin the commit you use)"
    ) from None

from sqlalchemy.dialects.sqlite.base import SQLiteDialect

from tabrion import ddl
from tabrion.ddl import DdlStatement
from tabrion.errors import PlanMismatch, PlanRefused, UsageError
from tabrion.schema import (
    CheckShape,
    ColumnShape,
    ExclusionShape,
    ForeignKeyShape,
    IndexShape,
    SchemaSnapshot,
    TableShape,
    UniqueShape,
    _canonical,
    fingerprint,
)

if TYPE_CHECKING:
    from tabrion._sa.database import Database

__all__ = [
    "MigrationPlan",
    "PlanStep",
    "RiskClass",
    "migration_markers",
    "plan",
    "revision_text",
]


class RiskClass(enum.StrEnum):
    """What a step may do to a database (contract I-124 c). A step has at least one class; an executor runs only the
    classes the user confirmed (TAB-XW-5)."""

    ADDITIVE = "additive"
    """Adds a table, column, constraint or index, or relaxes a ``NOT NULL``: existing data stays as it is."""
    DATA_CHANGING = "data_changing"
    """Changes stored values or their meaning: a type change, ``NOT NULL`` on existing rows, a new column with a
    default (backfill) or a changed default."""
    DESTRUCTIVE = "destructive"
    """Drops a table, column, constraint or index, or narrows a type: data or a guarantee can be lost."""
    REBUILD = "rebuild"
    """A SQLite table rebuild: the table is copied into a new one (foreign keys off, ADR 0015 section 4)."""
    LOCKING = "locking"
    """PostgreSQL: the step locks an existing table against writes while it runs (``ACCESS EXCLUSIVE`` for most
    ``ALTER TABLE`` forms, ``DROP TABLE`` and ``DROP INDEX``; ``SHARE`` and ``SHARE ROW EXCLUSIVE`` for ``CREATE
    INDEX`` and a foreign key)."""
    NON_TRANSACTIONAL = "non_transactional"
    """PostgreSQL ``CREATE``/``DROP INDEX CONCURRENTLY``: runs outside the transaction and is never part of a dry
    run."""


_OPERATIONS = frozenset(
    {
        "create_table",
        "drop_table",
        "add_column",
        "drop_column",
        "alter_column",
        "add_constraint",
        "drop_constraint",
        "create_index",
        "drop_index",
        "rebuild_table",
    }
)
_DIALECTS = frozenset({"sqlite", "postgresql"})
_HASH = re.compile(r"sha256:[0-9a-f]{64}")
_FINGERPRINT = re.compile(r"v2:sha256:[0-9a-f]{64}")
_PLAN_FORMAT = 1
_MAX_PLAN_JSON = 64 * 1024 * 1024
"""Longest text :meth:`MigrationPlan.from_json` reads (characters)."""


def _invalid(field: str) -> PlanMismatch:
    return PlanMismatch("plan.invalid", details={"field": field})


@dataclass(frozen=True, slots=True)
class PlanStep:
    """One step of a plan: one ``operation`` on the table ``target`` (``schema.table``), its ``statements`` in the
    order they run, its ``risks`` (sorted by value, at least one) and whether it runs inside the plan's transaction
    (``transactional``, ``False`` when a statement cannot). ``index`` is the step's position in the plan, from 0.

    The constructor checks the fields and raises ``PlanMismatch("plan.invalid")`` for a step that does not hold
    together."""

    index: int
    operation: str
    target: str
    statements: tuple[DdlStatement, ...]
    risks: tuple[RiskClass, ...]
    transactional: bool

    def __post_init__(self) -> None:
        if type(self.index) is not int or self.index < 0:
            raise _invalid("index")
        if not isinstance(self.operation, str) or self.operation not in _OPERATIONS:
            raise _invalid("operation")
        if not isinstance(self.target, str) or not self.target:
            raise _invalid("target")
        statements = self.statements
        if (
            not isinstance(statements, tuple)
            or not statements
            or not all(
                isinstance(s, DdlStatement)
                and isinstance(s.sql, str)
                and s.sql
                and isinstance(s.target, str)
                and isinstance(s.transactional, bool)
                for s in statements
            )
        ):
            raise _invalid("statements")
        risks = self.risks
        if (
            not isinstance(risks, tuple)
            or not risks
            or not all(isinstance(r, RiskClass) for r in risks)
            or risks != tuple(sorted(set(risks)))
        ):
            raise _invalid("risks")
        if not isinstance(self.transactional, bool) or self.transactional != all(s.transactional for s in statements):
            raise _invalid("transactional")


@dataclass(frozen=True, slots=True)
class MigrationPlan:
    """A schema change plan (contract section 5.13): the ``dialect``, the fingerprints of the snapshot it starts
    from (``source_fingerprint``) and of the target model (``target_fingerprint``), the ``steps`` and ``plan_hash``,
    ``sha256:<64 hex>`` over the canonical JSON of all other fields, every statement's exact SQL included.

    The constructor recomputes the hash and checks every field; a plan that does not match its own hash, or does not
    hold together, raises ``PlanMismatch("plan.invalid")``. The same two snapshots, server version and options always
    give the same plan and hash."""

    dialect: str
    source_fingerprint: str
    target_fingerprint: str
    steps: tuple[PlanStep, ...]
    plan_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.dialect, str) or self.dialect not in _DIALECTS:
            raise _invalid("dialect")
        for field in ("source_fingerprint", "target_fingerprint"):
            value = getattr(self, field)
            if not isinstance(value, str) or not _FINGERPRINT.fullmatch(value):
                raise _invalid(field)
        steps = self.steps
        if not isinstance(steps, tuple) or not all(isinstance(s, PlanStep) for s in steps):
            raise _invalid("steps")
        if any(step.index != position for position, step in enumerate(steps)):
            raise _invalid("index")
        payload = _payload(self.dialect, self.source_fingerprint, self.target_fingerprint, steps)
        if not isinstance(self.plan_hash, str) or self.plan_hash != _hash(payload):
            raise _invalid("plan_hash")

    @property
    def risks(self) -> tuple[RiskClass, ...]:
        """Every risk class of every step, sorted by value."""
        return tuple(sorted({risk for step in self.steps for risk in step.risks}))

    def to_json(self) -> str:
        """Canonical JSON (sorted keys, no blanks, ASCII) of every field, ``plan_hash`` included; :meth:`from_json`
        reads it back."""
        payload = _payload(self.dialect, self.source_fingerprint, self.target_fingerprint, self.steps)
        return _canonical_json({**payload, "plan_hash": self.plan_hash})

    @classmethod
    def from_json(cls, text: str) -> MigrationPlan:
        """A plan from :meth:`to_json`. The hash is recomputed over the content; a plan whose content was changed,
        or any text that is not such a plan, raises ``PlanMismatch("plan.invalid")``.

        This proves **integrity, not authenticity** (ADR 0015 N-2): a plan whose hash matches its own content can
        come from anyone, since anyone can compute the hash. Never take a plan from an untrusted source (a project
        file, a URL, an import); keep plans where only your own code writes them (Studio keeps them server-side) and
        bind the execution to the hash the user saw in the preview."""
        plan_: MigrationPlan | None = None
        try:
            if isinstance(text, str) and len(text) <= _MAX_PLAN_JSON:
                plan_ = _from_dict(json.loads(text))
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError, PlanMismatch):
            plan_ = None
        del text  # I-76
        if plan_ is None:
            raise _invalid("json")
        return plan_


def _payload(dialect: str, source: str, target: str, steps: Sequence[PlanStep]) -> dict[str, Any]:
    """Every field of a plan but its hash, as the canonical JSON hashes it."""
    return {
        "format": _PLAN_FORMAT,
        "dialect": dialect,
        "source_fingerprint": source,
        "target_fingerprint": target,
        "steps": [
            {
                "index": step.index,
                "operation": step.operation,
                "target": step.target,
                "statements": [
                    {"sql": s.sql, "target": s.target, "transactional": s.transactional} for s in step.statements
                ],
                "risks": [risk.value for risk in step.risks],
                "transactional": step.transactional,
            }
            for step in steps
        ],
    }


def _canonical_json(data: Mapping[str, Any]) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _hash(payload: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(payload).encode("ascii")).hexdigest()


def _exact(value: object, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("not the expected object")
    return value


def _from_dict(data: object) -> MigrationPlan:
    fields = _exact(data, {"format", "dialect", "source_fingerprint", "target_fingerprint", "steps", "plan_hash"})
    if type(fields["format"]) is not int or fields["format"] != _PLAN_FORMAT or not isinstance(fields["steps"], list):
        raise ValueError("format")
    steps = []
    for raw in fields["steps"]:
        step = _exact(raw, {"index", "operation", "target", "statements", "risks", "transactional"})
        if not isinstance(step["statements"], list) or not isinstance(step["risks"], list):
            raise ValueError("step")
        statements = []
        for item in step["statements"]:
            statement = _exact(item, {"sql", "target", "transactional"})
            statements.append(DdlStatement(statement["sql"], statement["target"], statement["transactional"]))
        risks = []
        for risk in step["risks"]:
            if not isinstance(risk, str):
                raise ValueError("risk")
            risks.append(RiskClass(risk))
        steps.append(
            PlanStep(
                index=step["index"],
                operation=step["operation"],
                target=step["target"],
                statements=tuple(statements),
                risks=tuple(risks),
                transactional=step["transactional"],
            )
        )
    return MigrationPlan(
        dialect=fields["dialect"],
        source_fingerprint=fields["source_fingerprint"],
        target_fingerprint=fields["target_fingerprint"],
        steps=tuple(steps),
        plan_hash=fields["plan_hash"],
    )


# --- plan -----------------------------------------------------------------------------------------------------------


def plan(
    current: SchemaSnapshot,
    target: SchemaSnapshot,
    *,
    server_version: tuple[int, ...] | None = None,
    concurrent_indexes: bool = False,
) -> MigrationPlan:
    """The steps that turn the database of the level-2 snapshot ``current`` into the level-2 model ``target``.

    Both must be level 2 (``UsageError("plan.level_required")``) and of the same dialect (``PlanRefused``, reason
    ``dialect_mismatch``), SQLite or PostgreSQL (reason ``engine_unsupported``). Tables are matched by
    ``schema.table``; a table of ``current`` that ``target`` lacks is dropped, one that only ``target`` has is
    created. A column is matched by name: a renamed column is a drop and an add. Steps are ordered so that
    dependencies hold (contract I-124 d). On SQLite a change it cannot make in place becomes one ``rebuild_table``
    step; ``server_version`` (``sqlite3.sqlite_version_info`` of the engine that will run the plan) allows
    ``DROP COLUMN`` in place from 3.35, ``None`` rebuilds instead. ``concurrent_indexes=True`` (PostgreSQL) creates
    and drops the indexes of existing tables ``CONCURRENTLY``, each in a step of its own that is not transactional.

    Refusals (nothing is planned): ``PlanRefused`` with reason ``unsupported_feature`` (ADR 0015 D-5; ``details``
    ``table`` and ``feature``) and ``unsafe_schema_sql`` (K-1), ``UsageError("plan.unsupported_change")`` for a
    change the plan cannot make (a PostgreSQL primary key or column order), ``UsageError("plan.invalid_target")`` for
    a target whose foreign key refers to a table the plan drops, and the ``UsageError`` of :mod:`tabrion.ddl` for a
    target it cannot generate."""
    for snapshot_ in (current, target):
        if not isinstance(snapshot_, SchemaSnapshot):
            raise TypeError("current and target must be SchemaSnapshot values")
    if current.level != 2 or target.level != 2:
        raise UsageError("plan.level_required")
    if current.dialect != target.dialect:
        raise _refused("dialect_mismatch")
    if current.dialect not in _DIALECTS:
        raise _refused("engine_unsupported")
    if server_version is not None and (
        not isinstance(server_version, tuple) or not all(type(part) is int and part >= 0 for part in server_version)
    ):
        raise TypeError("server_version must be a tuple of non-negative integers or None")
    if not isinstance(concurrent_indexes, bool):
        raise TypeError("concurrent_indexes must be a bool")
    if concurrent_indexes and current.dialect != "postgresql":
        raise UsageError("plan.unsupported_option", details={"option": "concurrent_indexes"})
    planner = _Planner(current, target, server_version, concurrent_indexes)
    steps = planner.steps()
    return _build(current.dialect, fingerprint(current), fingerprint(target), steps)


def _build(
    dialect: str,
    source: str,
    target: str,
    steps: Sequence[tuple[str, str, Sequence[DdlStatement], Iterable[RiskClass]]],
) -> MigrationPlan:
    numbered = tuple(
        PlanStep(
            index=index,
            operation=operation,
            target=table,
            statements=tuple(statements),
            risks=tuple(sorted(set(risks))),
            transactional=all(s.transactional for s in statements),
        )
        for index, (operation, table, statements, risks) in enumerate(steps)
    )
    return MigrationPlan(dialect, source, target, numbered, _hash(_payload(dialect, source, target, numbered)))


def _refused(reason: str, **details: str) -> PlanRefused:
    return PlanRefused("plan.refused", details={"reason": reason, **details})


def _unsupported_change(table: str, change: str) -> UsageError:
    return UsageError("plan.unsupported_change", details={"table": table, "change": change})


_LANGUAGES = frozenset({"plpgsql", "sql"})
"""The languages of a trigger or policy function whose body the fingerprint can check (ADR 0015 D-5.1)."""
_SQLITE_DROP_COLUMN = (3, 35)

# Phases (contract I-124 d): foreign keys are dropped first and added last, so every table and key they need exists.
_DROP_FOREIGN_KEYS, _DROP_OTHERS, _REBUILD, _DROP_TABLES, _COLUMNS, _ADD_OTHERS, _CREATE_TABLES, _ADD_FOREIGN_KEYS = (
    range(8)
)

_Step = tuple[str, str, Sequence[DdlStatement], Iterable[RiskClass]]


class _Planner:
    def __init__(
        self,
        current: SchemaSnapshot,
        target: SchemaSnapshot,
        server_version: tuple[int, ...] | None,
        concurrent: bool,
    ) -> None:
        self.dialect: Literal["sqlite", "postgresql"] = "sqlite" if current.dialect == "sqlite" else "postgresql"
        self.current = {t.name: _canonical(t) for t in current.tables}
        self.target = {t.name: _canonical(t) for t in target.tables}
        self.database_features = sorted(set(current.features) | set(target.features))
        self.server_version = server_version
        self.concurrent = concurrent
        self.phases: dict[int, list[_Step]] = {}

    @property
    def postgres(self) -> bool:
        return self.dialect == "postgresql"

    def add(
        self, phase: int, operation: str, table: str, statements: Sequence[DdlStatement], *risks: RiskClass
    ) -> None:
        self.phases.setdefault(phase, []).append((operation, table, tuple(statements), risks))

    def steps(self) -> list[_Step]:
        removed = sorted(set(self.current) - set(self.target))
        added = sorted(set(self.target) - set(self.current))
        changed = sorted(
            name for name in set(self.current) & set(self.target) if self.current[name] != self.target[name]
        )
        if (removed or added or changed) and self.database_features:
            raise _refused("unsupported_feature", table="", feature=self.database_features[0])
        for name in added:
            self.check_new(self.target[name])
        for name in removed:
            self.guard(self.current[name])
        self.check_references(removed)
        for name in changed:
            self.table(self.current[name], self.target[name])
        self.drop_tables(removed)
        for name in added:
            self.create_table(self.target[name])
        return [step for phase in sorted(self.phases) for step in self.phases[phase]]

    # checks --------------------------------------------------------------------------------------------------------

    def guard(self, table: TableShape) -> None:
        """ADR 0015 D-5: a PostgreSQL table with a feature marker, or with a trigger or policy function in another
        language than PL/pgSQL or SQL (or without a readable body), is never changed."""
        if self.postgres and table.features:
            raise _refused("unsupported_feature", table=table.name, feature=table.features[0])
        for function in table.functions:
            if function.language not in _LANGUAGES or not function.definition:
                raise _refused("unsupported_feature", table=table.name, feature="function_language")

    def check_new(self, table: TableShape) -> None:
        for field, feature in _EXTRAS:
            if getattr(table, field):
                raise _refused("unsupported_feature", table=table.name, feature=feature)

    def check_references(self, removed: Sequence[str]) -> None:
        gone = set(removed)
        for table in self.target.values():
            for key in table.foreign_keys:
                if key.referred_table in gone:
                    raise UsageError("plan.invalid_target", details={"table": table.name, "field": "foreign_keys"})

    # tables --------------------------------------------------------------------------------------------------------

    def table(self, old: TableShape, new: TableShape) -> None:
        for field, feature in _EXTRAS:
            if getattr(old, field) != getattr(new, field):
                raise _refused("unsupported_feature", table=old.name, feature=feature)
        self.guard(old)
        diff = _TableDiff(old, new)
        if self.postgres:
            self.postgres_table(diff)
        elif self.needs_rebuild(diff):
            self.rebuild(diff)
        else:
            self.sqlite_in_place(diff)

    def postgres_table(self, diff: _TableDiff) -> None:
        old, new = diff.old, diff.new
        if old.primary_key != new.primary_key:
            raise _unsupported_change(old.name, "primary_key")
        if diff.order_after_in_place != [c.name for c in new.columns]:
            raise _unsupported_change(old.name, "column_order")
        lock = (RiskClass.LOCKING,)
        for item in diff.removed_constraints:
            if item.name is None:
                raise _unsupported_change(old.name, "unnamed_constraint")
            statement = ddl.drop_constraint_statement(old, item.name, dialect="postgresql")
            phase = _DROP_FOREIGN_KEYS if isinstance(item, ForeignKeyShape) else _DROP_OTHERS
            self.add(phase, "drop_constraint", old.name, [statement], RiskClass.DESTRUCTIVE, *lock)
        for index in diff.removed_indexes:
            self.drop_index(old, index)
        self.columns(diff, lock)
        for item in diff.added_constraints:
            if isinstance(item, ExclusionShape):
                raise UsageError(
                    "ddl.unsupported_feature", details={"table": new.name, "feature": "exclusion_constraint"}
                )
            phase = _ADD_FOREIGN_KEYS if isinstance(item, ForeignKeyShape) else _ADD_OTHERS
            statement = ddl.add_constraint_statement(_plain(new), item, dialect="postgresql")
            self.add(phase, "add_constraint", new.name, [statement], RiskClass.ADDITIVE, *lock)
        for index in diff.added_indexes:
            self.create_index(new, index)

    def columns(self, diff: _TableDiff, lock: tuple[RiskClass, ...]) -> None:
        old = diff.old
        for column in diff.dropped:
            statement = ddl.drop_column_statement(old, column.name, dialect=self.dialect)
            self.add(_COLUMNS, "drop_column", old.name, [statement], RiskClass.DESTRUCTIVE, *lock)
        for before, after in diff.altered:
            statements = ddl.alter_column_statements(old, before, after, dialect=self.dialect)
            self.add(_COLUMNS, "alter_column", old.name, statements, *_column_risks(before, after), *lock)
        for column in diff.added:
            statement = ddl.add_column_statement(old, column, dialect=self.dialect)
            self.add(_COLUMNS, "add_column", old.name, [statement], *_added_column_risks(column), *lock)

    def drop_index(self, table: TableShape, index: IndexShape) -> None:
        concurrent = self.concurrent
        statement = ddl.drop_index_statement(table, index.name, dialect=self.dialect, concurrently=concurrent)
        risks = _index_risks(self.postgres, concurrent)
        self.add(_DROP_OTHERS, "drop_index", table.name, [statement], RiskClass.DESTRUCTIVE, *risks)

    def create_index(self, table: TableShape, index: IndexShape) -> None:
        concurrent = self.concurrent
        statement = ddl.create_index_statement(_plain(table), index, dialect=self.dialect, concurrently=concurrent)
        risks = _index_risks(self.postgres, concurrent)
        self.add(_ADD_OTHERS, "create_index", table.name, [statement], RiskClass.ADDITIVE, *risks)

    # SQLite --------------------------------------------------------------------------------------------------------

    def needs_rebuild(self, diff: _TableDiff) -> bool:
        old, new = diff.old, diff.new
        if old.primary_key != new.primary_key or diff.altered or diff.removed_constraints or diff.added_constraints:
            return True
        if diff.order_after_in_place != [c.name for c in new.columns]:
            return True
        if any(not _sqlite_addable(column) for column in diff.added):
            return True
        version = self.server_version
        return bool(diff.dropped) and (version is None or version[:2] < _SQLITE_DROP_COLUMN)

    def sqlite_in_place(self, diff: _TableDiff) -> None:
        for index in diff.removed_indexes:
            self.drop_index(diff.old, index)
        self.columns(diff, ())
        for index in diff.added_indexes:
            self.create_index(diff.new, index)

    def rebuild(self, diff: _TableDiff) -> None:
        old, new = diff.old, diff.new
        if old.features:
            # what the CREATE TABLE text holds beyond the shape would be lost (ADR 0015 D-5.3)
            raise _refused("unsupported_feature", table=old.name, feature=old.features[0])
        triggers = [DdlStatement(_check_schema_sql(t.name, t.definition, old.name), old.name) for t in old.triggers]
        schema, _, name = old.name.rpartition(".")
        taken = {n.rpartition(".")[2].casefold() for n in self.current} | {
            n.rpartition(".")[2].casefold() for n in self.target
        }
        holder = f"_tabrion_old_{name}"
        while holder.casefold() in taken:
            holder += "_"
        copy = TableShape(
            name=f"{schema}.{holder}",
            columns=tuple(ColumnShape(c.name, "BLOB", True) for c in old.columns),
            primary_key=(),
        )
        created = ddl.table_statements(_bare(new), dialect="sqlite")
        kept = [c.name for c in new.columns if c.name in {o.name for o in old.columns}]
        statements = [
            ddl.table_statements(copy, dialect="sqlite")[0],
            _copy_rows(old.name, copy.name, [c.name for c in old.columns]),
            ddl.drop_table_statement(_bare(old), dialect="sqlite"),
            created[0],
            _copy_rows(copy.name, new.name, kept),
            ddl.drop_table_statement(copy, dialect="sqlite"),
            *created[1:],
            *triggers,
        ]
        risks = {RiskClass.REBUILD}
        risks.update(RiskClass.DESTRUCTIVE for _ in diff.dropped)
        for before, after in diff.altered:
            risks.update(_column_risks(before, after))
        for column in diff.added:
            risks.update(_added_column_risks(column))
        if diff.removed_constraints or diff.removed_indexes or old.primary_key != new.primary_key:
            risks.add(RiskClass.DESTRUCTIVE)
        if diff.added_constraints or diff.added_indexes:
            risks.add(RiskClass.ADDITIVE)
        self.add(_REBUILD, "rebuild_table", old.name, statements, *risks)

    # whole tables --------------------------------------------------------------------------------------------------

    def drop_tables(self, removed: Sequence[str]) -> None:
        """Drop referencing tables before the tables they refer to; a cycle loses its foreign keys first."""
        left = list(removed)
        lock = (RiskClass.LOCKING,) if self.postgres else ()
        while left:
            referred = {
                key.referred_table
                for name in left
                for key in self.current[name].foreign_keys
                if key.referred_table != name and key.referred_table in left
            }
            free = [name for name in left if name not in referred]
            if not free:  # a cycle: drop the keys between the remaining tables, then the tables in name order
                for name in left:
                    table = self.current[name]
                    for key in table.foreign_keys:
                        if key.referred_table != name and key.referred_table in left and self.postgres:
                            if key.name is None:
                                raise _unsupported_change(name, "unnamed_constraint")
                            statement = ddl.drop_constraint_statement(table, key.name, dialect="postgresql")
                            self.add(
                                _DROP_FOREIGN_KEYS, "drop_constraint", name, [statement], RiskClass.DESTRUCTIVE, *lock
                            )
                free = list(left)
            for name in free:
                statement = ddl.drop_table_statement(_bare(self.current[name]), dialect=self.dialect)
                self.add(_DROP_TABLES, "drop_table", name, [statement], RiskClass.DESTRUCTIVE, *lock)
                left.remove(name)

    def create_table(self, table: TableShape) -> None:
        """``CREATE TABLE`` with its indexes. On PostgreSQL a foreign key to another new table is added after every
        table exists (its own step); one to an existing table or to itself stays inline. SQLite keeps every key
        inline: it does not check the referred table when a table is created."""
        new_tables = set(self.target) - set(self.current)
        inline, later = [], []
        for key in table.foreign_keys:
            if self.postgres and key.referred_table != table.name and key.referred_table in new_tables:
                later.append(key)
            else:
                inline.append(key)
        statements = ddl.table_statements(dataclasses.replace(table, foreign_keys=tuple(inline)), dialect=self.dialect)
        existing = any(key.referred_table in self.current for key in inline)
        lock = (RiskClass.LOCKING,) if self.postgres and existing else ()
        self.add(_CREATE_TABLES, "create_table", table.name, statements, RiskClass.ADDITIVE, *lock)
        for key in later:
            statement = ddl.add_constraint_statement(_plain(table), key, dialect="postgresql")
            self.add(_ADD_FOREIGN_KEYS, "add_constraint", table.name, [statement], RiskClass.ADDITIVE)


_EXTRAS = (
    ("triggers", "trigger"),
    ("policies", "policy"),
    ("functions", "function"),
    ("row_security", "row_security"),
    ("force_row_security", "row_security"),
    ("features", "table_feature"),
)
"""The D-5 parts of a table that a plan never creates or changes (contract I-124 e)."""


_Constraint = ForeignKeyShape | UniqueShape | CheckShape | ExclusionShape


@dataclass
class _TableDiff:
    old: TableShape
    new: TableShape
    dropped: list[ColumnShape] = dataclasses.field(default_factory=list)
    added: list[ColumnShape] = dataclasses.field(default_factory=list)
    altered: list[tuple[ColumnShape, ColumnShape]] = dataclasses.field(default_factory=list)
    order_after_in_place: list[str] = dataclasses.field(default_factory=list)
    removed_constraints: list[_Constraint] = dataclasses.field(default_factory=list)
    added_constraints: list[_Constraint] = dataclasses.field(default_factory=list)
    removed_indexes: list[IndexShape] = dataclasses.field(default_factory=list)
    added_indexes: list[IndexShape] = dataclasses.field(default_factory=list)

    def __post_init__(self) -> None:
        old, new = self.old, self.new
        before = {c.name: c for c in old.columns}
        after = {c.name: c for c in new.columns}
        self.dropped = [c for c in old.columns if c.name not in after]
        self.added = [c for c in new.columns if c.name not in before]
        self.altered = [(before[c.name], c) for c in new.columns if c.name in before and before[c.name] != c]
        self.order_after_in_place = [c.name for c in old.columns if c.name in after] + [c.name for c in self.added]
        old_items: list[_Constraint] = [
            *old.foreign_keys,
            *old.unique_constraints,
            *old.check_constraints,
            *old.exclusion_constraints,
        ]
        new_items: list[_Constraint] = [
            *new.foreign_keys,
            *new.unique_constraints,
            *new.check_constraints,
            *new.exclusion_constraints,
        ]
        self.removed_constraints = _unmatched(old_items, new_items)
        self.added_constraints = _unmatched(new_items, old_items)
        self.removed_indexes = _unmatched(list(old.indexes), list(new.indexes))
        self.added_indexes = _unmatched(list(new.indexes), list(old.indexes))


def _unmatched(left: list[Any], right: list[Any]) -> list[Any]:
    """Items of ``left`` without an equal item in ``right`` (as multisets, in ``left``'s order)."""
    rest = list(right)
    out = []
    for item in left:
        if item in rest:
            rest.remove(item)
        else:
            out.append(item)
    return out


def _bare(table: TableShape) -> TableShape:
    """The table without the D-5 parts, which ``tabrion.ddl`` does not create (a rebuild recreates the triggers)."""
    return dataclasses.replace(
        table, triggers=(), policies=(), functions=(), row_security=False, force_row_security=False, features=()
    )


def _plain(table: TableShape) -> TableShape:
    """The table with plain columns, for a statement that only names them (a constraint or an index): an identity or
    generated column is refused only where a column is written (``tabrion.ddl`` I-123 e)."""
    return dataclasses.replace(table, columns=tuple(ColumnShape(c.name, c.type, c.nullable) for c in table.columns))


def _index_risks(postgres: bool, concurrent: bool) -> tuple[RiskClass, ...]:
    if not postgres:
        return ()
    return (RiskClass.NON_TRANSACTIONAL,) if concurrent else (RiskClass.LOCKING,)


def _added_column_risks(column: ColumnShape) -> tuple[RiskClass, ...]:
    """A new column with a default fills every existing row (backfill); a ``NOT NULL`` one needs a value in each."""
    if column.default is not None or not column.nullable:
        return (RiskClass.ADDITIVE, RiskClass.DATA_CHANGING)
    return (RiskClass.ADDITIVE,)


def _column_risks(old: ColumnShape, new: ColumnShape) -> set[RiskClass]:
    risks: set[RiskClass] = set()
    if old.type != new.type:
        risks.add(RiskClass.DATA_CHANGING)
        if not _widens(old.type, new.type):
            risks.add(RiskClass.DESTRUCTIVE)
    if old.nullable and not new.nullable:
        risks.add(RiskClass.DATA_CHANGING)
    if (old.default, old.identity, old.generated, old.generation_expression) != (
        new.default,
        new.identity,
        new.generated,
        new.generation_expression,
    ):
        risks.add(RiskClass.DATA_CHANGING)
    return risks or {RiskClass.ADDITIVE}


_INTEGERS = ("SMALLINT", "INTEGER", "BIGINT")
_TEXTS = frozenset({"VARCHAR", "CHARACTER VARYING", "NVARCHAR", "TEXT"})
_TYPE = re.compile(r"([A-Z][A-Z ]*?)\s*(?:\(\s*(\d+)\s*(?:,\s*(\d+)\s*)?\))?")


def _widens(old: str, new: str) -> bool:
    """Whether a type change keeps every value (contract I-124 c): a longer or unbounded text, a wider integer, a
    wider ``NUMERIC``, ``REAL`` to ``DOUBLE PRECISION``. Anything else, a collation change included, narrows."""
    a, b = _TYPE.fullmatch(old.strip().upper()), _TYPE.fullmatch(new.strip().upper())
    if a is None or b is None:
        return False
    name_a, name_b = a.group(1).strip(), b.group(1).strip()
    if name_a in _TEXTS and name_b in _TEXTS:
        if name_b == "TEXT" or b.group(2) is None:
            return name_a != "TEXT" or name_b == "TEXT"
        return a.group(2) is not None and name_a != "TEXT" and int(b.group(2)) >= int(a.group(2))
    if name_a in _INTEGERS and name_b in _INTEGERS:
        return _INTEGERS.index(name_b) >= _INTEGERS.index(name_a)
    if name_a in _INTEGERS and name_b == "NUMERIC":
        return b.group(2) is None
    if name_a == "NUMERIC" and name_b == "NUMERIC":
        if b.group(2) is None:
            return True
        if a.group(2) is None:
            return False
        scale_a, scale_b = int(a.group(3) or 0), int(b.group(3) or 0)
        return scale_b >= scale_a and int(b.group(2)) - scale_b >= int(a.group(2)) - scale_a
    return (name_a, name_b) == ("REAL", "DOUBLE PRECISION") and a.group(2) is None and b.group(2) is None


_CONSTANT = re.compile(
    r"\(?\s*(?:[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?|'(?:[^']|'')*'|NULL|TRUE|FALSE|[xX]'[0-9A-Fa-f]*')\s*\)?",
    re.IGNORECASE,
)


def _sqlite_addable(column: ColumnShape) -> bool:
    """Whether SQLite's ``ALTER TABLE ADD COLUMN`` takes the column: nullable or with a non-NULL default, and the
    default a constant (SQLite refuses ``CURRENT_TIMESTAMP`` and expressions there); else the table is rebuilt."""
    default = column.default
    if default is not None and not _CONSTANT.fullmatch(default.strip()):
        return False
    if not column.nullable and (default is None or default.strip("() ").upper() == "NULL"):
        return False
    return column.identity is None and column.generated is None


def _copy_rows(source: str, target: str, columns: Sequence[str]) -> DdlStatement:
    """``INSERT INTO target (columns) SELECT columns FROM source``, compiled by SQLAlchemy's SQLite dialect
    (identifiers quoted by the dialect, no values)."""

    def table(name: str) -> sqlalchemy.TableClause:
        schema, _, bare = name.rpartition(".")
        return sqlalchemy.table(
            bare, *(sqlalchemy.column(c) for c in columns), schema=None if schema == "main" else schema
        )

    source_table, target_table = table(source), table(target)
    statement = sqlalchemy.insert(target_table).from_select(
        list(columns), sqlalchemy.select(*(source_table.c[c] for c in columns))
    )
    return DdlStatement(str(statement.compile(dialect=SQLiteDialect())).strip(), target)


# --- K-1: schema SQL from sqlite_schema -----------------------------------------------------------------------------

_MAX_SCHEMA_SQL = 100_000
"""Longest ``sqlite_schema`` text a rebuild recreates (characters)."""
_MAX_SEMICOLONS = 10_000
"""Most ``;`` in such a text (the one-statement check looks at each)."""


def _check_schema_sql(name: str, sql: str, table: str) -> str:
    """``sql`` when it is exactly one complete ``CREATE TRIGGER <name>`` statement, else ``PlanRefused`` (reason
    ``unsafe_schema_sql``, ADR 0015 K-1). Nothing of the text reaches the error."""
    refused = _refused("unsafe_schema_sql", table=table, object="trigger")
    if (
        not isinstance(sql, str)
        or not isinstance(name, str)
        or not sql.strip()
        or len(sql) > _MAX_SCHEMA_SQL
        or "\x00" in sql
        or sql.count(";") > _MAX_SEMICOLONS
    ):
        raise refused
    if not _one_statement(sql) or _trigger_name(sql) != name:
        raise refused
    return sql


def _one_statement(sql: str) -> bool:
    """Whether ``sql`` followed by ``;`` is one complete statement with nothing after it: the first prefix that
    ``sqlite3.complete_statement`` accepts must be the whole text."""
    text = sql + ";"
    position = text.find(";")
    while position != -1:
        if sqlite3.complete_statement(text[: position + 1]):
            return position == len(text) - 1
        position = text.find(";", position + 1)
    return False


_HEAD = re.compile(r"CREATE[ \t\r\n]+TRIGGER[ \t\r\n]+", re.IGNORECASE)


def _trigger_name(sql: str) -> str | None:
    """The name after ``CREATE TRIGGER`` (as ``sqlite_schema`` writes it: no ``TEMP``, no ``IF NOT EXISTS``, no
    schema), unquoted; ``None`` when the head is anything else. The name must be followed by a blank."""
    head = _HEAD.match(sql)
    if head is None:
        return None
    rest = sql[head.end() :]
    if rest[:1] in ('"', "`", "["):
        close = "]" if rest[0] == "[" else rest[0]
        end = rest.find(close, 1)
        while end != -1 and close != "]" and rest[end + 1 : end + 2] == close:  # a doubled quote
            end = rest.find(close, end + 2)
        if end == -1:
            return None
        quoted, after = rest[1:end], rest[end + 1 :]
        name = quoted if close == "]" else quoted.replace(close * 2, close)
    else:
        end = 0
        while end < len(rest) and (rest[end].isalnum() or rest[end] in "_$" or ord(rest[end]) > 127):
            end += 1
        name, after = rest[:end], rest[end:]
    if not name or after[:1] not in (" ", "\t", "\r", "\n"):
        return None  # a schema-qualified name (main.x) or no blank after the name
    return name


# --- revision text --------------------------------------------------------------------------------------------------

_REVISION_ID = re.compile(r"[0-9a-zA-Z_]{1,64}")
_DOCSTRING = (
    '"""Alembic revision written by Tabrion (tabrion.plan.revision_text, TAB-XW-4).\n'
    "\n"
    "It runs the statements of one reviewed schema change plan, in its order and as they were previewed. Read every\n"
    "statement before you run it with Alembic. The plan hash and the source fingerprint below bind it to the\n"
    "preview: run it only on a database whose schema still has that fingerprint. Tabrion wrote this text only; it\n"
    "never runs a revision. The message is the variable `message`.\n"
    '"""'
)
_FOREIGN_KEYS_OFF = (
    "    # SQLite: run this step with foreign keys off (PRAGMA foreign_keys = OFF outside the transaction) and\n"
    "    # check them afterwards (PRAGMA foreign_key_check); see the Tabrion migrations guide,\n"
    "    # 'Why the foreign keys are off'."
)


def revision_text(plan: MigrationPlan, *, revision: str, down_revision: str | None, message: str) -> str:
    """An Alembic revision file for ``plan``, as a string (ADR 0015 section 2, K-3).

    ``upgrade()`` first refuses an engine other than the plan's dialect, then runs every statement of every step in
    order with ``op.execute(sa.text(...))`` (a non-transactional step inside ``autocommit_block()``), each step
    preceded by a comment with its number, operation and risk classes; a SQLite rebuild or table drop gets a comment
    that its foreign keys must be off. ``downgrade()`` raises ``NotImplementedError``: a plan holds no reverse
    statements. The module docstring is a fixed text; ``revision``, ``down_revision``, ``message``, the plan hash,
    the fingerprints and every statement are written as Python string literals (``ascii``), so no text can become
    code. ``revision`` must match ``[0-9a-zA-Z_]{1,64}`` and ``down_revision`` the same or be ``None``
    (``UsageError("plan.invalid_revision")``).

    The function reads no file, imports no project module and runs nothing; writing the file into a project (and
    its name) is the caller's (Studio STU-XSD-3)."""
    if not isinstance(plan, MigrationPlan):
        raise TypeError("plan must be a MigrationPlan")
    if not isinstance(revision, str) or not _REVISION_ID.fullmatch(revision):
        raise UsageError("plan.invalid_revision", details={"field": "revision"})
    if down_revision is not None and (not isinstance(down_revision, str) or not _REVISION_ID.fullmatch(down_revision)):
        raise UsageError("plan.invalid_revision", details={"field": "down_revision"})
    if not isinstance(message, str):
        raise TypeError("message must be a str")
    lines = [
        _DOCSTRING,
        "",
        "from alembic import op",
        "import sqlalchemy as sa",
        "",
        f"revision = {revision!a}",
        f"down_revision = {down_revision!a}",
        "branch_labels = None",
        "depends_on = None",
        "",
        f"message = {message!a}",
        f"dialect = {plan.dialect!a}",
        f"plan_hash = {plan.plan_hash!a}",
        f"source_fingerprint = {plan.source_fingerprint!a}",
        f"target_fingerprint = {plan.target_fingerprint!a}",
        "",
        "",
        "def upgrade() -> None:",
        "    if op.get_context().dialect.name != dialect:",
        '        raise RuntimeError("this revision was planned for another database engine")',
    ]
    for step in plan.steps:
        risks = ", ".join(risk.value for risk in step.risks)
        lines.append(f"    # step {step.index}: {step.operation} ({risks})")
        if plan.dialect == "sqlite" and step.operation in ("rebuild_table", "drop_table"):
            lines.append(_FOREIGN_KEYS_OFF)
        for statement in step.statements:
            call = f"op.execute(sa.text({_escape_colons(statement.sql)!a}))"
            if statement.transactional:
                lines.append(f"    {call}")
            else:
                lines.extend(["    with op.get_context().autocommit_block():", f"        {call}"])
    lines.extend(
        [
            "",
            "",
            "def downgrade() -> None:",
            '    raise NotImplementedError("Tabrion writes no downgrade: plan the reverse change and review it")',
            "",
        ]
    )
    return "\n".join(lines)


def _escape_colons(sql: str) -> str:
    """Every ``:`` as ``\\:``, so ``sa.text`` reads none as a bound parameter (a literal ``'a:b'``, ``::type``) and
    writes the text back unchanged."""
    return sql.replace(":", "\\:")


# --- migration markers ----------------------------------------------------------------------------------------------

_MARKER_TABLES = ("alembic_version", "nodestro_schema_migrations", "axentra_schema_migrations")
_PG_MARKERS = (
    "SELECT DISTINCT pg_catalog.lower(c.relname) FROM pg_catalog.pg_class c "
    "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
    "WHERE pg_catalog.lower(c.relname) IN ('alembic_version', 'nodestro_schema_migrations', "
    "'axentra_schema_migrations') AND c.relkind IN ('r', 'p', 'v', 'm', 'f') "
    "AND n.nspname <> 'information_schema' AND n.nspname NOT LIKE 'pg\\_%'"
)


def migration_markers(database: Database) -> tuple[str, ...]:
    """The markers of migration management in ``database`` (ADR 0015 section 3), in this order: ``alembic_version``,
    ``nodestro_schema_migrations``, ``axentra_schema_migrations`` (a table or view of that name, any case, in any
    schema the session can see in the catalog; on SQLite in any attached database) and ``sqlite_user_version``
    (SQLite ``PRAGMA user_version`` above 0, ``tabrion.sqlite.migrate``). An empty tuple means none was found.

    It reads the catalog only, in a read transaction of ``database`` (a layer-B ``tabrion.Database``). A version
    table under another name (Alembic's ``version_table`` option) is not found: the caller knows its own projects."""
    dialect = getattr(database, "dialect", None)
    read: Callable[[], Any] | None = getattr(database, "read", None)
    if dialect not in _DIALECTS or read is None:
        raise TypeError("database must be a tabrion.Database")
    found: set[str] = set()
    with read() as transaction:
        if dialect == "postgresql":
            found.update(str(row[0]) for row in transaction.fetch_all(_PG_MARKERS))
        else:
            preparer = SQLiteDialect().identifier_preparer
            names = [str(row[0]) for row in transaction.fetch_all("SELECT name FROM pragma_database_list")]
            for name in names:
                quoted = preparer.quote_identifier(name)
                rows = transaction.fetch_all(
                    f"SELECT lower(name) FROM {quoted}.sqlite_master WHERE type IN ('table', 'view')",  # noqa: S608
                )
                found.update(str(row[0]) for row in rows if str(row[0]) in _MARKER_TABLES)
                if int(transaction.fetch_scalar(f"PRAGMA {quoted}.user_version") or 0) > 0:
                    found.add("sqlite_user_version")
    return tuple(marker for marker in (*_MARKER_TABLES, "sqlite_user_version") if marker in found)
