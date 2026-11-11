"""The parts catalog (RA-A-2): CRUD, status changes with an optimistic lock, and relation queries.

Every function takes the application's ``tabrion.Database`` and runs in one Tabrion transaction (``read()`` for
queries, ``transaction()`` for changes), so the same code runs on SQLite and PostgreSQL. Database errors arrive as
Tabrion's classified errors (``IntegrityViolation`` with a ``Kind``) and become this application's own errors.

The optimistic lock (``version``): every change of a part names the version the caller read and runs
``UPDATE ... WHERE part_number = ? AND version = ?`` with ``version = version + 1``. No row changed means another
writer was first: ``StaleVersion``, and the caller reads again. Nothing is held between the read and the write, and
no lost update is possible on either engine.

The cycle check of ``add_component`` reads the relations and then inserts one; two such transactions with
disjoint rows could each pass the check and together close a cycle. On SQLite ``BEGIN IMMEDIATE`` already runs one
writer at a time. On PostgreSQL every ``add_component`` first takes the transaction-level advisory lock
``RELATIONS_LOCK`` (``pg_advisory_xact_lock``, released by the commit or rollback, never held by an idle
connection). That serializes exactly the relation additions, without a retry loop: ``SERIALIZABLE`` would also be
correct, but it fails one of the two transactions with a serialization error that the caller must retry, and it
also fails on unrelated read-write conflicts.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import MAX_EMAX, MAX_PREC, MIN_EMIN, ROUND_HALF_EVEN, Context, Decimal, localcontext
from typing import Any, TypeVar

import sqlalchemy as sa

from parts.errors import (
    AlreadyExists,
    CycleFound,
    CycleRefused,
    InUse,
    InvalidTransition,
    InvalidValue,
    NotFound,
    PartsError,
    StaleVersion,
)
from parts.schema import STATUSES, material, part, part_relation, property_
from tabrion import Database
from tabrion.errors import DatabaseError, Kind
from tabrion.postgres import lock_key

TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"released", "obsolete"}),
    "released": frozenset({"blocked", "obsolete"}),
    "blocked": frozenset({"released", "obsolete"}),
    "obsolete": frozenset(),
}
"""The life cycle: a released part can be blocked and released again; obsolete is final."""

RELATIONS_LOCK = lock_key("parts.part_relation")
"""The advisory lock key that serializes ``add_component`` on PostgreSQL (``tabrion.postgres.lock_key``)."""

BOM_SCALE = 6
"""Decimal places of a ``bill_of_materials`` quantity: computed exactly, then rounded once (half even)."""

_EXACT = Context(prec=MAX_PREC, Emax=MAX_EMAX, Emin=MIN_EMIN)
"""A context in which ``+`` and ``*`` of finite decimals never round."""

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Part:
    id: int
    part_number: str
    name: str
    material_code: str
    status: str
    version: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class Component:
    part_number: str
    quantity: Decimal
    """Per assembly (direct) or per top-level assembly (``bill_of_materials``)."""
    depth: int = 1


def _now() -> datetime:
    return datetime.now(UTC)


Mapping = dict[Kind, tuple[type[PartsError], str]]


_INVALID: Mapping = {
    Kind.CHECK: (InvalidValue, "a value is out of range"),
    Kind.DATA: (InvalidValue, "a value is out of range"),  # PostgreSQL: a text longer than its varchar(n)
}


def _write(database: Database, work: Callable[[sa.Connection], T], mapping: Mapping) -> T:
    """``work`` in one write transaction; a Tabrion error whose ``Kind`` is mapped (``CHECK`` and ``DATA`` always,
    to ``InvalidValue``) becomes the application's error, raised outside the ``except`` block (the driver's message
    may quote a value)."""
    mapped: tuple[type[PartsError], str] | None = None
    try:
        with database.transaction() as transaction:
            return work(transaction.connection)
    except DatabaseError as error:
        mapped = {**_INVALID, **mapping}.get(error.kind)
        if mapped is None:
            raise
    raise mapped[0](mapped[1])


# --- materials and parts --------------------------------------------------------------------------------------------


def create_material(database: Database, code: str, name: str) -> int:
    def work(connection: sa.Connection) -> int:
        statement = sa.insert(material).values(code=code, name=name, created_at=_now()).returning(material.c.id)
        return int(connection.execute(statement).scalar_one())

    return _write(database, work, {Kind.UNIQUE: (AlreadyExists, "material code taken")})


def _material_id(connection: sa.Connection, code: str) -> int:
    found = connection.execute(sa.select(material.c.id).where(material.c.code == code)).scalar_one_or_none()
    if found is None:
        raise NotFound("no such material")
    return int(found)


def create_part(database: Database, part_number: str, name: str, material_code: str) -> Part:
    """A new part in status ``draft`` with version 1, as the same transaction stored it."""

    def work(connection: sa.Connection) -> Part:
        now = _now()
        part_id = connection.execute(
            sa.insert(part)
            .values(
                part_number=part_number,
                name=name,
                material_id=_material_id(connection, material_code),
                created_at=now,
                updated_at=now,
            )
            .returning(part.c.id)
        ).scalar_one()
        return _part(
            connection.execute(sa.select(*_PART_COLUMNS).join_from(part, material).where(part.c.id == part_id)).one()
        )

    return _write(database, work, {Kind.UNIQUE: (AlreadyExists, "part number taken")})


_PART_COLUMNS = (
    part.c.id,
    part.c.part_number,
    part.c.name,
    material.c.code,
    part.c.status,
    part.c.version,
    part.c.created_at,
    part.c.updated_at,
)


def _part(row: Any) -> Part:
    return Part(int(row[0]), row[1], row[2], row[3], row[4], int(row[5]), row[6], row[7])


def get_part(database: Database, part_number: str) -> Part:
    with database.read() as transaction:
        row = transaction.connection.execute(
            sa.select(*_PART_COLUMNS).join_from(part, material).where(part.c.part_number == part_number)
        ).one_or_none()
    if row is None:
        raise NotFound("no such part")
    return _part(row)


def list_parts(database: Database, *, status: str | None = None) -> list[Part]:
    """All parts by part number, optionally those in one status."""
    if status is not None and status not in STATUSES:
        raise ValueError("unknown status")
    statement = sa.select(*_PART_COLUMNS).join_from(part, material).order_by(part.c.part_number)
    if status is not None:
        statement = statement.where(part.c.status == status)
    with database.read() as transaction:
        return [_part(row) for row in transaction.connection.execute(statement)]


def _versioned_update(connection: sa.Connection, part_number: str, expected_version: int, **values: Any) -> int:
    """``UPDATE ... WHERE version = expected``; the new version, or ``StaleVersion``/``NotFound``."""
    result = connection.execute(
        sa.update(part)
        .where(part.c.part_number == part_number, part.c.version == expected_version)
        .values(version=part.c.version + 1, updated_at=_now(), **values)
    )
    if result.rowcount == 1:
        return expected_version + 1
    exists = connection.execute(sa.select(part.c.id).where(part.c.part_number == part_number)).first()
    raise StaleVersion("the part changed since it was read") if exists else NotFound("no such part")


def rename_part(database: Database, part_number: str, name: str, *, expected_version: int) -> int:
    return _write(database, lambda c: _versioned_update(c, part_number, expected_version, name=name), {})


def change_status(database: Database, part_number: str, status: str, *, expected_version: int) -> int:
    """Move the part to ``status`` if the life cycle allows it (``TRANSITIONS``) and nobody changed it since
    ``expected_version``; the new version. The version is checked before the transition, so a stale caller gets
    ``StaleVersion`` (read again) rather than ``InvalidTransition``; ``_versioned_update`` checks it once more."""
    if status not in STATUSES:
        raise ValueError("unknown status")

    def work(connection: sa.Connection) -> int:
        current = connection.execute(
            sa.select(part.c.status, part.c.version).where(part.c.part_number == part_number)
        ).one_or_none()
        if current is None:
            raise NotFound("no such part")
        if current.version != expected_version:
            raise StaleVersion("the part changed since it was read")
        if status not in TRANSITIONS[current.status]:
            raise InvalidTransition(f"{current.status} -> {status} is not allowed")
        return _versioned_update(connection, part_number, expected_version, status=status)

    return _write(database, work, {})


def delete_part(database: Database, part_number: str, *, expected_version: int) -> None:
    """Delete a part with its properties and its own components list; refused while it is a component."""

    def work(connection: sa.Connection) -> None:
        result = connection.execute(
            sa.delete(part).where(part.c.part_number == part_number, part.c.version == expected_version)
        )
        if result.rowcount != 1:
            exists = connection.execute(sa.select(part.c.id).where(part.c.part_number == part_number)).first()
            raise StaleVersion("the part changed since it was read") if exists else NotFound("no such part")

    _write(database, work, {Kind.FOREIGN_KEY: (InUse, "the part is a component of an assembly")})


def delete_material(database: Database, code: str) -> None:
    def work(connection: sa.Connection) -> None:
        if connection.execute(sa.delete(material).where(material.c.code == code)).rowcount != 1:
            raise NotFound("no such material")

    _write(database, work, {Kind.FOREIGN_KEY: (InUse, "the material is used by a part")})


# --- properties ----------------------------------------------------------------------------------------------------


def _part_id(connection: sa.Connection, part_number: str) -> int:
    found = connection.execute(sa.select(part.c.id).where(part.c.part_number == part_number)).scalar_one_or_none()
    if found is None:
        raise NotFound("no such part")
    return int(found)


def set_property(database: Database, part_number: str, key: str, value: Decimal, unit: str) -> None:
    """Insert or replace one property (exact ``Decimal``, with its unit). Two concurrent first inserts of one key:
    the second gets the ``UNIQUE`` violation and runs again, now as an update."""

    def work(connection: sa.Connection) -> None:
        part_id = _part_id(connection, part_number)
        changed = connection.execute(
            sa.update(property_)
            .where(property_.c.part_id == part_id, property_.c.key == key)
            .values(value=value, unit=unit)
        ).rowcount
        if not changed:
            connection.execute(sa.insert(property_).values(part_id=part_id, key=key, value=value, unit=unit))

    try:
        _write(database, work, {Kind.UNIQUE: (AlreadyExists, "the property was inserted concurrently")})
    except AlreadyExists:
        _write(database, work, {})


def properties(database: Database, part_number: str) -> dict[str, tuple[Decimal, str]]:
    with database.read() as transaction:
        part_id = _part_id(transaction.connection, part_number)
        rows = transaction.connection.execute(
            sa.select(property_.c.key, property_.c.value, property_.c.unit)
            .where(property_.c.part_id == part_id)
            .order_by(property_.c.key)
        )
        return {row.key: (row.value, row.unit) for row in rows}


# --- relations -----------------------------------------------------------------------------------------------------


def _descendants(connection: sa.Connection, part_id: int) -> set[int]:
    """Every part below ``part_id`` (a recursive CTE, the same SQL on both engines)."""
    below = (
        sa.select(part_relation.c.component_id.label("id"))
        .where(part_relation.c.assembly_id == part_id)
        .cte("below", recursive=True)
    )
    below = below.union(sa.select(part_relation.c.component_id).join(below, part_relation.c.assembly_id == below.c.id))
    return {int(row[0]) for row in connection.execute(sa.select(below.c.id))}


def add_component(database: Database, assembly: str, component: str, quantity: Decimal) -> None:
    """``component`` becomes part of ``assembly`` ``quantity`` times; a cycle is refused (``CycleRefused``)."""

    def work(connection: sa.Connection) -> None:
        if connection.dialect.name == "postgresql":
            connection.execute(sa.select(sa.func.pg_advisory_xact_lock(RELATIONS_LOCK)))
        assembly_id = _part_id(connection, assembly)
        component_id = _part_id(connection, component)
        if component_id == assembly_id or assembly_id in _descendants(connection, component_id):
            raise CycleRefused("the part would contain itself")
        connection.execute(
            sa.insert(part_relation).values(assembly_id=assembly_id, component_id=component_id, quantity=quantity)
        )

    _write(database, work, {Kind.UNIQUE: (AlreadyExists, "the component is in the assembly already")})


def components(database: Database, assembly: str) -> list[Component]:
    """The direct components, by part number."""
    with database.read() as transaction:
        assembly_id = _part_id(transaction.connection, assembly)
        rows = transaction.connection.execute(
            sa.select(part.c.part_number, part_relation.c.quantity)
            .join_from(part_relation, part, part_relation.c.component_id == part.c.id)
            .where(part_relation.c.assembly_id == assembly_id)
            .order_by(part.c.part_number)
        )
        return [Component(row.part_number, row.quantity) for row in rows]


def _reachable_edges(connection: sa.Connection, root: int) -> list[tuple[int, int, str, Decimal]]:
    """Every relation below ``root``, as ``(assembly_id, component_id, component part number, quantity)``: one
    recursive CTE, so one statement and one snapshot. ``UNION`` ends it on a cyclic graph too."""
    edge = sa.select(part_relation.c.assembly_id, part_relation.c.component_id, part_relation.c.quantity)
    reach = edge.where(part_relation.c.assembly_id == root).cte("reach", recursive=True)
    reach = reach.union(edge.join(reach, part_relation.c.assembly_id == reach.c.component_id))
    rows = connection.execute(
        sa.select(reach.c.assembly_id, reach.c.component_id, part.c.part_number, reach.c.quantity).join_from(
            reach, part, reach.c.component_id == part.c.id
        )
    )
    return [(int(row[0]), int(row[1]), str(row[2]), row[3]) for row in rows]


def bill_of_materials(database: Database, assembly: str) -> list[Component]:
    """Every part below ``assembly`` with its total quantity per assembly (quantities multiplied along each path,
    summed over paths) and its smallest depth; by part number.

    The parts are visited in topological order (Kahn's algorithm), so the time is linear in the relations and no
    depth is too deep. The arithmetic is exact (a ``decimal`` context that never rounds); each total is rounded
    once, half even, to ``BOM_SCALE`` decimal places. Stored relations with a cycle raise ``CycleFound``."""
    with database.read() as transaction:
        root = _part_id(transaction.connection, assembly)
        edges = _reachable_edges(transaction.connection, root)
    children: dict[int, list[tuple[int, Decimal]]] = {}
    waiting: dict[int, int] = {}
    numbers: dict[int, str] = {}
    for parent, child, number, quantity in edges:
        children.setdefault(parent, []).append((child, quantity))
        waiting[child] = waiting.get(child, 0) + 1
        numbers[child] = number
    if root in waiting:
        raise CycleFound("the assembly contains itself")
    totals: dict[int, Decimal] = {root: Decimal(1)}
    depths: dict[int, int] = {root: 0}
    ready = deque([root])
    done = 0
    with localcontext(_EXACT):
        while ready:
            node = ready.popleft()
            done += 1
            for child, quantity in children.get(node, []):
                totals[child] = totals.get(child, Decimal(0)) + totals[node] * quantity
                depths[child] = min(depths.get(child, depths[node] + 1), depths[node] + 1)
                waiting[child] -= 1
                if waiting[child] == 0:
                    ready.append(child)
        if done != len(waiting) + 1:
            raise CycleFound("the assembly contains a cycle")
        step = Decimal(1).scaleb(-BOM_SCALE)
        result = [
            Component(numbers[node], totals[node].quantize(step, rounding=ROUND_HALF_EVEN), depths[node])
            for node in waiting
        ]
    return sorted(result, key=lambda component: component.part_number)


def where_used(database: Database, component: str) -> list[str]:
    """Every assembly that contains ``component``, directly or further up, by part number."""
    with database.read() as transaction:
        connection = transaction.connection
        component_id = _part_id(connection, component)
        above = (
            sa.select(part_relation.c.assembly_id.label("id"))
            .where(part_relation.c.component_id == component_id)
            .cte("above", recursive=True)
        )
        above = above.union(
            sa.select(part_relation.c.assembly_id).join(above, part_relation.c.component_id == above.c.id)
        )
        rows = connection.execute(
            sa.select(part.c.part_number).where(part.c.id.in_(sa.select(above.c.id))).order_by(part.c.part_number)
        )
        return [row[0] for row in rows]
