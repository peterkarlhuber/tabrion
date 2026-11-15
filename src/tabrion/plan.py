"""Schema change plans: diff, risk classes, SQLite rebuild and Alembic revision text (since 0.3, experimental, extra
``sqlalchemy``; a plan with column steps also needs ``migrations``, as the column statements of :mod:`tabrion.ddl`
do; TAB-XW-4, CR-DBX).

Contract section 5.13 and implementation note I-124; ADR 0015 sections 2 to 4, 9 (K-1, K-3, N-2) and 10 (D-5).
:func:`plan` turns two level-2 snapshots of :mod:`tabrion.schema` (the database as it is and the target model) into
a :class:`MigrationPlan`: numbered steps, each with the DDL of :mod:`tabrion.ddl`, its risk classes and whether it
runs in a transaction, bound to both fingerprints by a ``plan_hash``. :func:`plan` and :func:`revision_text` only
compute and :func:`migration_markers` only reads the catalog; :func:`execute_plan` (TAB-XW-5) is the one function
that runs a change, on a declared DEV or TEST database without migration management, after it planned again under
its locks and found the hash the user confirmed::

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

**What SQLite does not check (review of #89).** A SQLite ``DROP TABLE`` with foreign keys on deletes child rows
through ``ON DELETE CASCADE``, and neither a rebuild nor a ``DROP TABLE`` checks views or triggers. So a plan
refuses to drop a column or table that the text of a view or trigger names (reason ``dependent_object``), every
rebuild and SQLite table drop ends with ``SELECT * FROM <view> LIMIT 0`` for each view, and the revision text
refuses to run with foreign keys on and checks them before the commit.

**Schema SQL from the file (K-1).** A SQLite rebuild recreates the table's triggers from the text ``sqlite_schema``
holds, which in a hostile file is attacker data. Each text becomes exactly one statement, must be one complete
statement with nothing after it (``sqlite3.complete_statement``) and must start with ``CREATE TRIGGER`` and the
trigger's own name; otherwise ``plan`` refuses (reason ``unsafe_schema_sql``). The executor plans again in its
transaction, so the check runs again there, and runs each text as one statement under the caller's authorizer with
``trusted_schema = OFF`` and defensive mode.

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
import math
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

from sqlalchemy.dialects.postgresql.base import PGDialect
from sqlalchemy.dialects.sqlite.base import SQLiteDialect

from tabrion import ddl
from tabrion.ddl import DdlStatement
from tabrion.errors import (
    PlanFailed,
    PlanMismatch,
    PlanRefused,
    SchemaDrift,
    TabrionError,
    TransientError,
    UsageError,
)
from tabrion.schema import (
    CheckShape,
    ColumnShape,
    ExclusionShape,
    ForeignKeyShape,
    IndexShape,
    SchemaSnapshot,
    TableShape,
    UniqueShape,
    ViewShape,
    _canonical,
    compare,
    fingerprint,
    snapshot,
)

if TYPE_CHECKING:
    from tabrion._sa.database import Database

__all__ = [
    "ExecutionResult",
    "MigrationPlan",
    "PlanStep",
    "RiskClass",
    "execute_plan",
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
        if type(self.operation) is not str or self.operation not in _OPERATIONS:
            raise _invalid("operation")
        if type(self.target) is not str or not self.target:
            raise _invalid("target")
        statements = self.statements
        if (
            not isinstance(statements, tuple)
            or not statements
            or not all(
                type(s) is DdlStatement
                and type(s.sql) is str  # review of #89, P89-07: no str subclass with its own __repr__
                and s.sql
                and type(s.target) is str
                and type(s.transactional) is bool
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
        if type(self.dialect) is not str or self.dialect not in _DIALECTS:
            raise _invalid("dialect")
        for field in ("source_fingerprint", "target_fingerprint"):
            value = getattr(self, field)
            if type(value) is not str or not _FINGERPRINT.fullmatch(value):
                raise _invalid(field)
        steps = self.steps
        if not isinstance(steps, tuple) or not all(isinstance(s, PlanStep) for s in steps):
            raise _invalid("steps")
        if any(step.index != position for position, step in enumerate(steps)):
            raise _invalid("index")
        for step in steps:
            _check_step(self.dialect, step)
        payload = _payload(self.dialect, self.source_fingerprint, self.target_fingerprint, steps)
        if type(self.plan_hash) is not str or self.plan_hash != _hash(payload):
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
        bind the execution to the hash the user saw in the preview. Every step's risk classes must include those its
        operation implies and its statements must begin as its operation's do (review of #89, P89-03), but nothing
        here can tell whether the statements are those a plan of the same snapshots would have: before a plan runs,
        plan again from a fresh snapshot and the target held server-side, compare the new ``plan_hash`` with the one
        the user confirmed, and take the risk classes from the new plan (contract I-124 a)."""
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


# Review of #89, P89-03: what a step's operation and statements imply, checked on every plan (from_json included), so a
# plan cannot label a DROP as additive. The statement heads each operation may have (after blanks, case-insensitive).
_HEADS: dict[str, tuple[str, ...]] = {
    "create_table": ("CREATE TABLE", "CREATE INDEX", "CREATE UNIQUE INDEX"),
    "drop_table": ("DROP TABLE", "SELECT * FROM"),
    "add_column": ("ALTER TABLE",),
    "drop_column": ("ALTER TABLE",),
    "alter_column": ("ALTER TABLE",),
    "add_constraint": ("ALTER TABLE",),
    "drop_constraint": ("ALTER TABLE",),
    "create_index": ("CREATE INDEX", "CREATE UNIQUE INDEX"),
    "drop_index": ("DROP INDEX",),
    "rebuild_table": (
        "CREATE TABLE",
        "INSERT INTO",
        "DROP TABLE",
        "CREATE INDEX",
        "CREATE UNIQUE INDEX",
        "CREATE TRIGGER",
        "SELECT * FROM",
    ),
}
_IMPLIED: dict[str, RiskClass] = {
    "drop_table": RiskClass.DESTRUCTIVE,
    "drop_column": RiskClass.DESTRUCTIVE,
    "drop_constraint": RiskClass.DESTRUCTIVE,
    "drop_index": RiskClass.DESTRUCTIVE,
    "rebuild_table": RiskClass.REBUILD,
}
_LOCKING_OPERATIONS = frozenset({"drop_table", "add_column", "drop_column", "alter_column", "drop_constraint"})
_BLANKS = re.compile(r"\s+")


def _check_step(dialect: str, step: PlanStep) -> None:
    """``PlanMismatch("plan.invalid")`` when a step's risk classes lack one its operation implies, or a statement does
    not begin as its operation's statements do (review of #89, P89-03). This keeps a plan from labeling a drop as
    additive; it cannot prove that the statements are those a plan of the same snapshots would have: a consumer
    plans again before it runs a plan (contract I-124 a)."""
    heads = _HEADS[step.operation]
    for statement in step.statements:
        text = _BLANKS.sub(" ", statement.sql.strip()).upper()
        if not text.startswith(heads):
            raise _invalid("statements")
    implied = set()
    if step.operation in _IMPLIED:
        implied.add(_IMPLIED[step.operation])
    if step.operation == "rebuild_table" and dialect != "sqlite":
        raise _invalid("operation")
    if not step.transactional:
        if step.operation not in ("create_index", "drop_index") or dialect != "postgresql":
            raise _invalid("transactional")
        implied.add(RiskClass.NON_TRANSACTIONAL)
    elif dialect == "postgresql" and step.operation in _LOCKING_OPERATIONS | {"create_index", "drop_index"}:
        implied.add(RiskClass.LOCKING)
    if not implied <= set(step.risks):
        raise _invalid("risks")


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
    ``table`` and ``feature``; also a view that differs between the snapshots), ``dependent_object`` (SQLite: a
    column or table to be dropped is named by the text of a view or trigger, ``details`` ``table`` and ``object``;
    review of #89, P89-02) and ``unsafe_schema_sql`` (K-1), ``UsageError("plan.unsupported_change")`` for a
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
# A CONCURRENTLY index step commits what ran before it (review of #89, P89-06): the drops come first, before any
# transactional step, and the creations last, after every transactional step.
(
    _DROP_CONCURRENTLY,
    _DROP_FOREIGN_KEYS,
    _DROP_OTHERS,
    _REBUILD,
    _DROP_TABLES,
    _COLUMNS,
    _ADD_OTHERS,
    _CREATE_TABLES,
    _ADD_FOREIGN_KEYS,
    _CREATE_CONCURRENTLY,
) = range(10)

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
        self.views = sorted(set(current.views), key=lambda v: v.name)
        self.target_views = sorted(set(target.views), key=lambda v: v.name)
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
        if self.views != self.target_views:  # a plan never creates, changes or drops a view (P89-02)
            changed_views = sorted({v.name for v in set(self.views) ^ set(self.target_views)})
            raise _refused("unsupported_feature", table=changed_views[0], feature="view")
        if not self.postgres:
            for name in removed:
                self.check_dependents(name, (), removed)
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

    def check_dependents(self, table: str, columns: Sequence[str], removed: Sequence[str]) -> None:
        """SQLite (review of #89, P89-02): refuse to drop ``columns`` of ``table`` (or, without columns, the table)
        while the text of a view or trigger names them, since SQLite checks neither after a rebuild or a ``DROP
        TABLE``. A conservative token match (identifiers, quoted or not, and words in literals, case-insensitive): a
        text that names the table and one of the columns, or for a dropped table any text of another object that
        names the table, refuses the plan (``PlanRefused``, reason ``dependent_object``). Triggers of a table that the
        plan drops go with it."""
        bare = table.rpartition(".")[2].casefold()
        wanted = {c.casefold() for c in columns}
        gone = set(removed)
        texts: list[tuple[str, str]] = [(view.name, view.definition) for view in self.views]
        for shape in self.current.values():
            if shape.name in gone:
                continue
            texts.extend((shape.name, trigger.definition) for trigger in shape.triggers)
        for owner, text in texts:
            tokens = _tokens(text)
            if bare not in tokens:
                continue
            if (wanted and wanted & tokens) or (not wanted and owner != table):
                raise _refused("dependent_object", table=table, object=owner)

    # tables --------------------------------------------------------------------------------------------------------

    def table(self, old: TableShape, new: TableShape) -> None:
        for field, feature in _EXTRAS:
            if getattr(old, field) != getattr(new, field):
                raise _refused("unsupported_feature", table=old.name, feature=feature)
        self.guard(old)
        diff = _TableDiff(old, new)
        if not self.postgres and diff.dropped:
            self.check_dependents(old.name, [c.name for c in diff.dropped], ())
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
        phase = _DROP_CONCURRENTLY if concurrent else _DROP_OTHERS
        self.add(phase, "drop_index", table.name, [statement], RiskClass.DESTRUCTIVE, *risks)

    def create_index(self, table: TableShape, index: IndexShape) -> None:
        concurrent = self.concurrent
        statement = ddl.create_index_statement(_plain(table), index, dialect=self.dialect, concurrently=concurrent)
        risks = _index_risks(self.postgres, concurrent)
        phase = _CREATE_CONCURRENTLY if concurrent else _ADD_OTHERS
        self.add(phase, "create_index", table.name, [statement], RiskClass.ADDITIVE, *risks)

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
            *self.view_checks(),
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

    def view_checks(self) -> list[DdlStatement]:
        """``SELECT * FROM <view> LIMIT 0`` for every view of the snapshot (review of #89, P89-02): SQLite checks a
        view only when it is used, so a rebuild or a table drop that broke one fails here, inside the transaction."""
        return [_view_check(view) for view in self.views]

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
                checks = [] if self.postgres else self.view_checks()
                self.add(_DROP_TABLES, "drop_table", name, [statement, *checks], RiskClass.DESTRUCTIVE, *lock)
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


_TOKEN = re.compile(r'"((?:[^"]|"")*)"|`((?:[^`]|``)*)`|\[([^\]]*)\]|([^\W\d][\w$]*)')


_WORD = re.compile(r"[^\W\d][\w$]*")


def _tokens(sql: str) -> set[str]:
    """Every identifier of ``sql``, quoted or not, casefolded; words inside string literals count too (conservative:
    a false match refuses a plan, a missed one could break a view)."""
    out = {word.casefold() for word in _WORD.findall(sql)}  # every bare word, whatever quote it stands in
    for match in _TOKEN.finditer(sql):
        double, back, bracket, word = match.groups()
        if double is not None:
            out.add(double.replace('""', '"').casefold())
        elif back is not None:
            out.add(back.replace("``", "`").casefold())
        elif bracket is not None:
            out.add(bracket.casefold())
        else:
            out.add(word.casefold())
    return out


def _view_check(view: ViewShape) -> DdlStatement:
    schema, _, name = view.name.rpartition(".")
    preparer = SQLiteDialect().identifier_preparer
    quoted = preparer.quote_identifier(name)
    if schema and schema != "main":
        quoted = f"{preparer.quote_identifier(schema)}.{quoted}"
    return DdlStatement(f"SELECT * FROM {quoted} LIMIT 0", view.name)  # noqa: S608 - quoted identifiers only


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
_FOREIGN_KEYS_GUARD = (
    "    # SQLite: a table rebuild or drop deletes the rows of child tables (ON DELETE CASCADE) while foreign keys\n"
    "    # are on, and PRAGMA foreign_keys cannot change inside a transaction: refuse to start with them on (set\n"
    "    # PRAGMA foreign_keys = OFF before the migration transaction; see the Tabrion migrations guide, 'Why the\n"
    "    # foreign keys are off'), and check every reference before the commit.\n"
    '    if op.get_bind().exec_driver_sql("PRAGMA foreign_keys").scalar():\n'
    '        raise RuntimeError("foreign keys are on: turn them off before the migration transaction begins")'
)
_FOREIGN_KEY_CHECK = (
    '    if op.get_bind().exec_driver_sql("PRAGMA foreign_key_check").first() is not None:\n'
    '        raise RuntimeError("a foreign key refers to a missing row: the migration is rolled back")'
)
_GUARDED = frozenset({"rebuild_table", "drop_table"})


def revision_text(plan: MigrationPlan, *, revision: str, down_revision: str | None, message: str) -> str:
    """An Alembic revision file for ``plan``, as a string (ADR 0015 section 2, K-3).

    ``upgrade()`` first refuses an engine other than the plan's dialect, then runs every statement of every step in
    order with ``op.execute(sa.text(...))`` (a non-transactional step inside ``autocommit_block()``, which commits the
    steps before it), each step preceded by a comment with its number, operation and risk classes. A SQLite plan with
    a rebuild or a table drop refuses to start while ``PRAGMA foreign_keys`` is on (a ``DROP TABLE`` would delete
    child rows through ``ON DELETE CASCADE``, and the pragma cannot change inside a transaction) and ends with
    ``PRAGMA foreign_key_check``, raising ``RuntimeError`` on any row (review of #89, P89-01). The revision does not
    check the source fingerprint: apply it only to the database it was planned for (contract I-124 j).
    ``downgrade()`` raises ``NotImplementedError``: a plan holds no reverse statements. The module docstring is a
    fixed text; ``revision``, ``down_revision``, ``message``, the plan hash, the fingerprints and every statement are
    written as Python string literals (``ascii``), so no text can become code. ``revision`` must match
    ``[0-9a-zA-Z_]{1,64}`` and ``down_revision`` the same or be ``None``
    (``UsageError("plan.invalid_revision")``).

    The function reads no file, imports no project module and runs nothing; writing the file into a project (and
    its name) is the caller's (Studio STU-XSD-3)."""
    if not isinstance(plan, MigrationPlan):
        raise TypeError("plan must be a MigrationPlan")
    if type(revision) is not str or not _REVISION_ID.fullmatch(revision):
        raise UsageError("plan.invalid_revision", details={"field": "revision"})
    if down_revision is not None and (type(down_revision) is not str or not _REVISION_ID.fullmatch(down_revision)):
        raise UsageError("plan.invalid_revision", details={"field": "down_revision"})
    if type(message) is not str:  # review of #89, P89-07: a str subclass could bring its own __repr__
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
    guarded = plan.dialect == "sqlite" and any(step.operation in _GUARDED for step in plan.steps)
    if guarded:  # review of #89, P89-01
        lines.append(_FOREIGN_KEYS_GUARD)
    for step in plan.steps:
        risks = ", ".join(risk.value for risk in step.risks)
        lines.append(f"    # step {step.index}: {step.operation} ({risks})")
        if not step.transactional:  # review of #89, P89-06
            lines.append("    # not transactional: autocommit_block() first commits every step before it")
        for statement in step.statements:
            call = f"op.execute(sa.text({_escape_colons(statement.sql)!a}))"
            if statement.transactional:
                lines.append(f"    {call}")
            else:
                lines.extend(["    with op.get_context().autocommit_block():", f"        {call}"])
    if guarded:
        lines.append(_FOREIGN_KEY_CHECK)
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
    with read() as transaction:
        return _read_markers(transaction, dialect)


# --- execution (TAB-XW-5) -------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """What :func:`execute_plan` did (contract I-125): the ``plan_hash`` of the plan it ran (the one the user
    confirmed), whether it was a ``dry_run``, how many steps ran (``steps_run``; in a dry run the transactional steps,
    rolled back afterwards), the indexes of the non-transactional steps a dry run did not run (``untested_steps``)
    and the level-2 fingerprint of the schema afterwards (``fingerprint_after``, ``None`` for a dry run)."""

    plan_hash: str
    dry_run: bool
    steps_run: int
    untested_steps: tuple[int, ...]
    fingerprint_after: str | None


_ENVIRONMENTS = frozenset({"dev", "test"})
_MAX_LOCK_TIMEOUT = 3600.0
"""Longest ``lock_timeout`` (seconds)."""
_ACCESS_EXCLUSIVE = "ACCESS EXCLUSIVE"
_SHARE_ROW_EXCLUSIVE = "SHARE ROW EXCLUSIVE"
_DEFENSIVE = "SQLITE_DBCONFIG_DEFENSIVE"


class _Rollback(Exception):
    """Ends a dry run's transaction without a commit (a signal, not an error); carries the result."""

    def __init__(self, result: ExecutionResult) -> None:
        super().__init__()
        self.result = result


def execute_plan(
    database: Database,
    target: SchemaSnapshot,
    *,
    plan_hash: str,
    expected_fingerprint: str,
    environment: Literal["dev", "test"],
    allow: Iterable[RiskClass] = (RiskClass.ADDITIVE,),
    source: SchemaSnapshot | None = None,
    schemas: Iterable[str] | None = None,
    server_version: tuple[int, ...] | None = None,
    concurrent_indexes: bool = False,
    dry_run: bool = False,
    lock_timeout: float = 5.0,
    timeout: float | None = None,
) -> ExecutionResult:
    """Run the schema change that turns ``database`` into the level-2 model ``target``, exactly as the user confirmed
    it in the preview, or nothing (ADR 0015 sections 3, 4 and 10; contract I-125).

    The plan is never taken from the caller (review of #89, P89-03): inside the execution transaction the executor
    takes a level-2 snapshot of ``schemas`` (as :func:`tabrion.schema.snapshot`; ``None`` is the default schema),
    plans again with :func:`plan` (``server_version`` and ``concurrent_indexes`` as in the preview) and runs that
    plan only when its hash is ``plan_hash``, the hash the user confirmed. Every check comes before the first
    statement:

    1. ``environment`` is ``"dev"`` or ``"test"`` (``PlanRefused``, reason ``environment``; a guard rail, not a
       boundary: pass the value from the user's own stored profile, never from a request; D-3).
    2. ``target`` is of the database's dialect (reason ``dialect_mismatch``); ``source``, when given, is the preview's
       snapshot and has the fingerprint ``expected_fingerprint`` (``PlanMismatch("plan.fingerprint_mismatch")``).
    3. Inside the transaction: no migration management (:func:`migration_markers`, reason ``migration_managed``,
       details ``markers``); SQLite: ``PRAGMA foreign_keys`` was turned off before ``BEGIN`` (reason
       ``foreign_keys_on`` when it is still on).
    4. The snapshot has the fingerprint ``expected_fingerprint`` (``SchemaDrift``, with the differences from
       ``source`` when given), the new plan's hash is ``plan_hash`` (``PlanMismatch("plan.hash_mismatch")``) and
       every risk class of every step is in ``allow`` (reason ``risk_not_allowed``).
    5. PostgreSQL (D-5.4, N-1): ``SET LOCAL lock_timeout``, ``LOCK TABLE`` of every existing table a step changes
       (``ACCESS EXCLUSIVE``; ``SHARE ROW EXCLUSIVE`` for one that only gets an index, and for the tables their
       foreign keys connect them with), then the snapshot, the drift check and the plan again under the locks. A
       lock not granted within ``lock_timeout`` raises ``TransientError`` with ``details`` ``step``. Functions cannot
       be locked: one changed after this check is a residual risk (contract I-124 j).

    SQLite runs under ``BEGIN IMMEDIATE`` with foreign keys off (set before ``BEGIN``), ``trusted_schema = OFF`` and
    defensive mode (``SQLITE_DBCONFIG_DEFENSIVE``, Python 3.12 or later; without it a plan that recreates a trigger
    is refused, reason ``engine_unsupported``), each statement on its own under the caller's authorizer (K-1), and
    ends with ``PRAGMA foreign_key_check`` and ``PRAGMA integrity_check``; the settings are restored afterwards.
    PostgreSQL runs the transactional steps in one ``READ COMMITTED`` transaction; a ``CREATE``/``DROP INDEX
    CONCURRENTLY`` step runs alone outside it, in plan order (drops before, creations after), and a failure there
    leaves the database partly changed. ``dry_run=True`` runs the transactional steps and rolls them back; the
    non-transactional steps are reported in ``untested_steps``.

    A failing step or check raises ``PlanFailed`` (``details`` ``step``, ``completed``, ``state``). The executor never
    retries a statement (R-NORETRY). ``database`` is a layer-B :class:`tabrion.Database` the caller owns; an open
    Tabrion transaction of the same thread raises ``UsageError("database.nested_transaction")``. Tabrion does not
    ask the user: passing ``plan_hash``, ``expected_fingerprint`` and ``allow`` is the confirmation (ADR 0015
    section 4)."""
    runner = _Executor(
        database,
        target,
        plan_hash=plan_hash,
        expected_fingerprint=expected_fingerprint,
        environment=environment,
        allow=allow,
        source=source,
        schemas=schemas,
        server_version=server_version,
        concurrent_indexes=concurrent_indexes,
        dry_run=dry_run,
        lock_timeout=lock_timeout,
        timeout=timeout,
    )
    return runner.run()


def _allowed(allow: object) -> frozenset[RiskClass]:
    if isinstance(allow, str | bytes) or not isinstance(allow, Iterable):
        raise TypeError("allow must be an iterable of RiskClass values")
    out = set()
    for item in allow:
        if not isinstance(item, str):
            raise TypeError("allow must be an iterable of RiskClass values")
        out.add(RiskClass(item))  # ValueError for an unknown class
    return frozenset(out)


def _schemas(schemas: object) -> tuple[str, ...] | None:
    if schemas is None:
        return None
    if isinstance(schemas, str | bytes) or not isinstance(schemas, Iterable):
        raise TypeError("schemas must be an iterable of schema names or None")
    names = tuple(schemas)
    if not all(type(name) is str for name in names):
        raise TypeError("schemas must be an iterable of schema names or None")
    return names


def _seconds(value: object, name: str, limit: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{name} must be a number of seconds")
    seconds = float(value)
    if not math.isfinite(seconds) or seconds <= 0 or (limit is not None and seconds > limit):
        raise ValueError(
            f"{name} must be a positive, finite number of seconds" + (f", at most {limit:g}" if limit else "")
        )
    return seconds


class _Executor:
    """One run of :func:`execute_plan`."""

    def __init__(
        self,
        database: Database,
        target: SchemaSnapshot,
        *,
        plan_hash: object,
        expected_fingerprint: object,
        environment: object,
        allow: object,
        source: object,
        schemas: object,
        server_version: tuple[int, ...] | None,
        concurrent_indexes: bool,
        dry_run: object,
        lock_timeout: object,
        timeout: object,
    ) -> None:
        if type(environment) is not str or environment not in _ENVIRONMENTS:
            raise _refused("environment")  # D-3: any other value, also one of another type
        dialect = getattr(database, "dialect", None)
        if dialect not in _DIALECTS or not callable(getattr(database, "_schema_transaction", None)):
            raise TypeError("database must be a tabrion.Database")
        if not isinstance(target, SchemaSnapshot):
            raise TypeError("target must be a SchemaSnapshot")
        if target.level != 2:
            raise UsageError("plan.level_required")
        if type(plan_hash) is not str:
            raise TypeError("plan_hash must be a str")
        if type(expected_fingerprint) is not str:
            raise TypeError("expected_fingerprint must be a str")
        if not isinstance(dry_run, bool):
            raise TypeError("dry_run must be a bool")
        if source is not None and not isinstance(source, SchemaSnapshot):
            raise TypeError("source must be a SchemaSnapshot or None")
        self.allowed = _allowed(allow)
        self.schemas = _schemas(schemas)
        self.lock_timeout = _seconds(lock_timeout, "lock_timeout", _MAX_LOCK_TIMEOUT)
        self.timeout = None if timeout is None else _seconds(timeout, "timeout")
        if target.dialect != dialect:
            raise _refused("dialect_mismatch")
        if source is not None and (source.dialect != dialect or fingerprint(source) != expected_fingerprint):
            raise PlanMismatch("plan.fingerprint_mismatch", details={"field": "source"})
        if not _FINGERPRINT.fullmatch(expected_fingerprint):
            raise PlanMismatch("plan.fingerprint_mismatch", details={"field": "expected_fingerprint"})
        self.database = database
        self.dialect: str = dialect
        self.target = target
        self.plan_hash = plan_hash
        self.expected = expected_fingerprint
        self.source: SchemaSnapshot | None = source
        self.server_version = server_version
        self.concurrent = concurrent_indexes
        self.dry_run = dry_run
        self.completed = 0  # steps that ran and stay applied
        self.sqlite_state: dict[str, int | None] = {}

    @property
    def postgres(self) -> bool:
        return self.dialect == "postgresql"

    # the run ---------------------------------------------------------------------------------------------------------

    def run(self) -> ExecutionResult:
        if self.postgres:
            return self.run_postgres()
        return self.run_sqlite()

    def run_sqlite(self) -> ExecutionResult:
        committing: MigrationPlan | None = None
        try:
            with self.transaction() as transaction:
                change = self.verify(transaction)
                self.run_steps(transaction, change.steps)
                self.sqlite_checks(transaction, change)
                if self.dry_run:
                    raise _Rollback(ExecutionResult(change.plan_hash, True, len(change.steps), (), None))
                after = fingerprint(self.snapshot(transaction))
                committing = change
                # the COMMIT runs when the block ends: an error there is a commit failure
                return ExecutionResult(change.plan_hash, False, len(change.steps), (), after)
        except _Rollback as rollback:
            return rollback.result
        except TabrionError as exc:
            if committing is None:
                raise
            raise self.commit_failed(committing, exc) from None

    def run_postgres(self) -> ExecutionResult:
        committing: MigrationPlan | None = None
        after: str | None = None
        try:
            with self.transaction() as transaction:
                change = self.verify(transaction)
                leading, middle, trailing = _split(change)
                if self.dry_run:
                    self.run_steps(transaction, middle)
                    untested = tuple(step.index for step in (*leading, *trailing))
                    raise _Rollback(ExecutionResult(change.plan_hash, True, len(middle), untested, None))
                if not leading:
                    self.run_steps(transaction, middle)
                    if not trailing:
                        after = fingerprint(self.snapshot(transaction))
                    committing = change
                # with leading CONCURRENTLY steps, this transaction only checked: it ran nothing
        except _Rollback as rollback:
            return rollback.result
        except TabrionError as exc:
            if committing is None:
                raise
            raise self.commit_failed(committing, exc) from None
        if committing is not None:
            self.completed += len(middle)
        else:
            for step in leading:
                self.run_alone(step)
            self.run_rest(change, middle)
        for step in trailing:
            self.run_alone(step)
        if after is None:
            with self.database.inspector() as inspector:
                after = fingerprint(snapshot(inspector, schemas=self.schemas, level=2))
        return ExecutionResult(change.plan_hash, False, len(change.steps), (), after)

    def run_rest(self, change: MigrationPlan, middle: list[PlanStep]) -> None:
        """After the leading ``DROP INDEX CONCURRENTLY`` steps: the transactional steps in a transaction of their own,
        after the locks, a new snapshot and a new plan, which must be exactly the steps left of the confirmed plan
        (else ``PlanFailed``, state ``partial``: the leading steps stay)."""
        first = change.steps[self.completed]
        finished = False
        try:
            with self.transaction() as transaction:
                self.set_lock_timeout(transaction)
                current = self.snapshot(transaction)
                self.lock(transaction, middle, current)
                current = self.snapshot(transaction)
                again = plan(
                    current, self.target, server_version=self.server_version, concurrent_indexes=self.concurrent
                )
                left = change.steps[self.completed :]
                if [_content(s) for s in again.steps] != [_content(s) for s in left]:
                    raise self.failed(first, None, state="partial", check="drift")
                self.run_steps(transaction, middle)
                finished = True
            self.completed += len(middle)
        except TabrionError as exc:
            if isinstance(exc, PlanFailed):
                raise
            if not finished:
                raise self.failed(first, exc, state="partial") from None
            raise self.commit_failed(change, exc) from None

    # checks ----------------------------------------------------------------------------------------------------------

    def transaction(self) -> Any:
        """The execution transaction (SQLite: foreign keys off, ``trusted_schema`` off and defensive mode set before
        ``BEGIN IMMEDIATE`` and restored afterwards)."""
        hooks: Any = self.database
        if self.postgres:
            return hooks._schema_transaction(timeout=self.timeout, prepare=_nothing, restore=_nothing)
        return hooks._schema_transaction(timeout=self.timeout, prepare=self.sqlite_prepare, restore=self.sqlite_restore)

    def verify(self, transaction: Any) -> MigrationPlan:
        markers = _read_markers(transaction, self.dialect)
        if markers:
            raise _refused("migration_managed", markers=",".join(markers))
        if not self.postgres and self.sqlite_state.get("foreign_keys_now") != 0:
            raise _refused("foreign_keys_on")  # review of #89, P89-01: never start with them on
        if self.postgres:
            self.set_lock_timeout(transaction)
        current = self.snapshot(transaction)
        change = self.check(current)
        if self.postgres:
            # ADR 0015 D-5.4, N-1: lock, then fingerprint and plan again under the locks
            self.lock(transaction, [s for s in change.steps if s.transactional], current)
            change = self.check(self.snapshot(transaction))
        elif self.sqlite_state.get("defensive_now") != 1 and any(
            _HEAD.match(statement.sql) for step in change.steps for statement in step.statements
        ):
            # K-1 at execution: a trigger text from sqlite_schema runs only in defensive mode
            raise _refused("engine_unsupported", feature="sqlite_defensive")
        return change

    def check(self, current: SchemaSnapshot) -> MigrationPlan:
        actual = fingerprint(current)
        if actual != self.expected:
            differences = compare(self.source, current) if self.source is not None else ()
            raise SchemaDrift(
                "plan.drift",
                differences=differences,
                details={"expected": self.expected, "actual": actual, "differences": len(differences)},
            )
        change = plan(current, self.target, server_version=self.server_version, concurrent_indexes=self.concurrent)
        if change.plan_hash != self.plan_hash:
            raise PlanMismatch("plan.hash_mismatch", details={"expected": self.plan_hash, "actual": change.plan_hash})
        refused = sorted(set(change.risks) - self.allowed)
        if refused:
            raise _refused("risk_not_allowed", risks=",".join(risk.value for risk in refused))
        return change

    def snapshot(self, transaction: Any) -> SchemaSnapshot:
        """A level-2 snapshot on the transaction's connection (a new inspector each time: an inspector caches)."""
        return snapshot(sqlalchemy.inspect(transaction.connection), schemas=self.schemas, level=2)

    def set_lock_timeout(self, transaction: Any) -> None:
        milliseconds = max(1, round(self.lock_timeout * 1000))
        transaction.execute(sqlalchemy.text(f"SET LOCAL lock_timeout = {milliseconds}"))

    def lock(self, transaction: Any, steps: Sequence[PlanStep], current: SchemaSnapshot) -> None:
        """``LOCK TABLE`` in name order (ADR 0015 D-5.4): ``ACCESS EXCLUSIVE`` on every existing table a step changes,
        ``SHARE ROW EXCLUSIVE`` on one that only gets an index and on the existing tables a foreign key of such a
        table (before or after) refers to or that refer to it. A lock not granted within ``lock_timeout`` raises
        ``TransientError`` with ``details`` ``step`` (the first step that needed it)."""
        for table, (mode, step) in sorted(_locks(steps, current, self.target).items()):
            schema_name, _, name = table.rpartition(".")
            quoted = f"{_PG_PREPARER.quote_identifier(schema_name)}.{_PG_PREPARER.quote_identifier(name)}"
            try:
                transaction.execute(sqlalchemy.text(f"LOCK TABLE {quoted} IN {mode} MODE"))
            except TransientError as exc:
                raise _with_step(exc, step) from None

    # steps -----------------------------------------------------------------------------------------------------------

    def run_steps(self, transaction: Any, steps: Sequence[PlanStep]) -> None:
        """Each statement on its own through the transaction (K-1: one statement per call, the caller's authorizer
        stays; ``:`` escaped so nothing binds, ``%`` written as the driver needs)."""
        for step in steps:
            for statement in step.statements:
                try:
                    transaction.execute(sqlalchemy.text(_escape_colons(statement.sql)))
                except TabrionError as exc:
                    if isinstance(exc, TransientError) and self.completed == 0:
                        raise _with_step(exc, step.index) from None
                    state = "rolled_back" if self.completed == 0 else "partial"
                    raise self.failed(step, exc, state=state) from None

    def run_alone(self, step: PlanStep) -> None:
        """A non-transactional step (``CREATE``/``DROP INDEX CONCURRENTLY``): one statement outside any transaction.
        A failure leaves the database partly changed (an invalid index can stay behind)."""
        hooks: Any = self.database
        for statement in step.statements:
            try:
                hooks._autocommit(statement.sql, timeout=self.timeout, lock_timeout=self.lock_timeout)
            except TabrionError as exc:
                raise self.failed(step, exc, state="partial") from None
        self.completed += 1

    def sqlite_checks(self, transaction: Any, change: MigrationPlan) -> None:
        """Before the commit (review of #89, P89-01): no foreign key may refer to a missing row and the file must be
        intact; else ``PlanFailed`` (state ``rolled_back``, ``check``, ``violations``)."""
        last = change.steps[-1] if change.steps else None
        for check, sql in (
            ("foreign_key_check", "SELECT count(*) FROM pragma_foreign_key_check"),
            ("integrity_check", "SELECT count(*) FROM pragma_integrity_check WHERE integrity_check <> 'ok'"),
        ):
            count = int(transaction.fetch_scalar(sql) or 0)
            if count:
                raise self.failed(last, None, state="rolled_back", check=check, violations=count)

    # errors ----------------------------------------------------------------------------------------------------------

    def failed(
        self,
        step: PlanStep | None,
        error: TabrionError | None,
        *,
        state: str,
        check: str | None = None,
        violations: int | None = None,
    ) -> PlanFailed:
        details: dict[str, str | int | None] = {
            "step": step.index if step is not None else None,
            "completed": self.completed,
            "state": state,
        }
        if check is not None:
            details["check"] = check
        if violations is not None:
            details["violations"] = violations
        return PlanFailed(
            "plan.failed",
            classification=error.classification if error is not None else None,
            original=error,
            details=details,
        )

    def commit_failed(self, change: MigrationPlan, error: TabrionError) -> PlanFailed:
        """The ``COMMIT`` failed: rolled back, or ``unknown`` when it may have run (the connection was lost)."""
        unknown = error.classification is not None and error.classification.outcome_unknown
        state = "unknown" if unknown else ("rolled_back" if self.completed == 0 else "partial")
        transactional = [step for step in change.steps if step.transactional]
        return self.failed(transactional[-1] if transactional else None, error, state=state)

    # SQLite connection settings ------------------------------------------------------------------------------------

    def sqlite_prepare(self, connection: sqlite3.Connection) -> None:
        """Before ``BEGIN``: foreign keys off (read back: ``verify`` refuses to start while they are on),
        ``trusted_schema`` off and defensive mode on where Python offers it (3.12 and later)."""
        state = self.sqlite_state
        state["foreign_keys"] = _pragma(connection, "foreign_keys")
        state["trusted_schema"] = _pragma(connection, "trusted_schema")
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("PRAGMA trusted_schema = OFF")
        state["foreign_keys_now"] = _pragma(connection, "foreign_keys")
        flag = getattr(sqlite3, _DEFENSIVE, None)
        getconfig = getattr(connection, "getconfig", None)
        setconfig = getattr(connection, "setconfig", None)
        state["defensive"] = state["defensive_now"] = None
        if flag is not None and getconfig is not None and setconfig is not None:
            state["defensive"] = int(bool(getconfig(flag)))
            setconfig(flag, True)
            state["defensive_now"] = int(bool(getconfig(flag)))

    def sqlite_restore(self, connection: sqlite3.Connection) -> None:
        """After the transaction: the settings as they were before (R-CR-5)."""
        state = self.sqlite_state
        if state.get("defensive") is not None:
            setconfig = getattr(connection, "setconfig")  # noqa: B009 - Python 3.12 and later; typeshed for 3.11 lacks it
            setconfig(getattr(sqlite3, _DEFENSIVE), bool(state["defensive"]))
        if state.get("trusted_schema") is not None:
            connection.execute(f"PRAGMA trusted_schema = {'ON' if state['trusted_schema'] else 'OFF'}")
        if state.get("foreign_keys") is not None:
            connection.execute(f"PRAGMA foreign_keys = {'ON' if state['foreign_keys'] else 'OFF'}")


_PG_PREPARER = PGDialect().identifier_preparer  # type: ignore[no-untyped-call]


def _nothing(connection: object) -> None:
    """PostgreSQL needs no session change before ``BEGIN``."""


def _pragma(connection: sqlite3.Connection, name: str) -> int:
    row = connection.execute(f"PRAGMA {name}").fetchone()
    return int(row[0]) if row is not None else 0


def _content(step: PlanStep) -> tuple[Any, ...]:
    """A step without its index: what it does."""
    return (step.operation, step.target, step.statements, step.risks, step.transactional)


def _split(change: MigrationPlan) -> tuple[list[PlanStep], list[PlanStep], list[PlanStep]]:
    """The leading non-transactional steps (``DROP INDEX CONCURRENTLY``), the transactional steps and the trailing
    non-transactional steps (``CREATE INDEX CONCURRENTLY``); :func:`plan` orders them so (contract I-124 d). Any
    other order is not a plan of :func:`plan` and is refused (``PlanMismatch("plan.invalid")``)."""
    steps = list(change.steps)
    first = next((i for i, step in enumerate(steps) if step.transactional), len(steps))
    last = max((i for i, step in enumerate(steps) if step.transactional), default=first - 1)
    leading, middle, trailing = steps[:first], steps[first : last + 1], steps[last + 1 :]
    if not all(step.transactional for step in middle):
        raise _invalid("transactional")
    if not leading and not middle:
        return [], [], trailing
    if leading and not middle:  # only CONCURRENTLY steps: drops first, then creations
        drops = [s for s in leading if s.operation == "drop_index"]
        return drops, [], leading[len(drops) :]
    return leading, middle, trailing


def _locks(steps: Sequence[PlanStep], current: SchemaSnapshot, target: SchemaSnapshot) -> dict[str, tuple[str, int]]:
    """``{table: (mode, first step)}`` for the existing tables the transactional ``steps`` change or depend on."""
    existing = {table.name: table for table in current.tables}
    wanted = {table.name: table for table in target.tables}
    locks: dict[str, tuple[str, int]] = {}

    def put(name: str, mode: str, step: int) -> None:
        if name not in existing:
            return
        held = locks.get(name)
        if held is None:
            locks[name] = (mode, step)
        elif held[0] != _ACCESS_EXCLUSIVE and mode == _ACCESS_EXCLUSIVE:
            locks[name] = (mode, held[1])

    for step in steps:
        name = step.target
        put(name, _SHARE_ROW_EXCLUSIVE if step.operation == "create_index" else _ACCESS_EXCLUSIVE, step.index)
        related: set[str] = set()
        for shape in (existing.get(name), wanted.get(name)):
            if shape is not None:
                related.update(key.referred_table for key in shape.foreign_keys)
        related.update(t.name for t in current.tables if any(k.referred_table == name for k in t.foreign_keys))
        for other in sorted(related - {name}):
            put(other, _SHARE_ROW_EXCLUSIVE, step.index)
    return locks


def _with_step(error: TransientError, step: int) -> TransientError:
    """The same lock error with ``details`` ``step`` (contract I-125)."""
    return TransientError(
        error.code,
        classification=error.classification,
        original=error.original,
        statement=error.statement,
        details={**error.details, "step": step},
    )


def _read_markers(transaction: Any, dialect: str) -> tuple[str, ...]:
    """The markers of :func:`migration_markers`, read in ``transaction``."""
    found: set[str] = set()
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
