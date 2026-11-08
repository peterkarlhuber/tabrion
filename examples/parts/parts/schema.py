"""The parts schema (planning document 09 section 7, use case A): material, part, property, part_relation.

One ``MetaData`` for both engines; the Alembic migrations in ``parts/migrations`` create exactly this (the tests
compare them with ``alembic.autogenerate.compare_metadata`` on SQLite and PostgreSQL). Every constraint has a name of
its own, so both engines carry the same names. Mappings (specification section 6): integer IDs
``BigInteger().with_variant(Integer, "sqlite")`` with ``Identity(always=True)``, text with a ``CHECK`` on its length,
UTC timestamps and exact decimals through ``parts.types``, foreign keys always declared.
A reference that must block a delete uses the default ``NO ACTION``, not ``RESTRICT``: SQLite reports a
``RESTRICT`` violation as ``SQLITE_CONSTRAINT_TRIGGER``, which ``tabrion.errors`` does not classify as a foreign key
(TODO TB-110); ``NO ACTION`` gives ``SQLITE_CONSTRAINT_FOREIGNKEY`` and the same refusal on both engines.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    ForeignKey,
    Identity,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Table,
    UniqueConstraint,
    text,
)

from parts.types import ExactDecimal, UtcDateTime

STATUSES = ("draft", "released", "blocked", "obsolete")
"""The part's life cycle; RA-A-2 adds the allowed transitions."""

ID = BigInteger().with_variant(Integer, "sqlite")
"""``bigint`` identity on PostgreSQL, the ``rowid`` alias ``INTEGER PRIMARY KEY`` on SQLite (TAB-T-D03)."""

metadata = MetaData()


def _id() -> Column[int]:
    return Column("id", ID, Identity(always=True), primary_key=True)


material = Table(
    "material",
    metadata,
    _id(),
    Column("code", String(32), nullable=False),
    Column("name", String(200), nullable=False),
    Column("created_at", UtcDateTime(), nullable=False),
    UniqueConstraint("code", name="uq_material_code"),
    CheckConstraint("length(code) BETWEEN 1 AND 32", name="ck_material_code"),
    CheckConstraint("length(name) BETWEEN 1 AND 200", name="ck_material_name"),
)

part = Table(
    "part",
    metadata,
    _id(),
    Column("part_number", String(40), nullable=False),
    Column("name", String(200), nullable=False),
    Column("material_id", ID, ForeignKey("material.id", name="fk_part_material"), nullable=False),
    Column("status", String(16), nullable=False, server_default=text("'draft'")),
    Column("version", Integer, nullable=False, server_default=text("1")),
    Column("created_at", UtcDateTime(), nullable=False),
    Column("updated_at", UtcDateTime(), nullable=False),
    UniqueConstraint("part_number", name="uq_part_part_number"),
    CheckConstraint("length(part_number) BETWEEN 1 AND 40", name="ck_part_part_number"),
    CheckConstraint("length(name) BETWEEN 1 AND 200", name="ck_part_name"),
    CheckConstraint("status IN ('draft', 'released', 'blocked', 'obsolete')", name="ck_part_status"),
    CheckConstraint("version >= 1", name="ck_part_version"),
    Index("ix_part_material_id", "material_id"),
)

property_ = Table(
    "property",
    metadata,
    _id(),
    Column("part_id", ID, ForeignKey("part.id", name="fk_property_part", ondelete="CASCADE"), nullable=False),
    Column("key", String(64), nullable=False),
    Column("value", ExactDecimal(18, 6), nullable=False),
    Column("unit", String(16), nullable=False),
    UniqueConstraint("part_id", "key", name="uq_property_part_id_key"),
    CheckConstraint("length(key) BETWEEN 1 AND 64", name="ck_property_key"),
    CheckConstraint("length(unit) BETWEEN 1 AND 16", name="ck_property_unit"),
)

part_relation = Table(
    "part_relation",
    metadata,
    Column(
        "assembly_id", ID, ForeignKey("part.id", name="fk_part_relation_assembly", ondelete="CASCADE"), nullable=False
    ),
    Column(
        "component_id",
        ID,
        ForeignKey("part.id", name="fk_part_relation_component"),
        nullable=False,
    ),
    Column("quantity", ExactDecimal(12, 3), nullable=False),
    PrimaryKeyConstraint("assembly_id", "component_id", name="pk_part_relation"),
    # The quantity is decimal text on SQLite: compare it as a number on both engines.
    CheckConstraint("CAST(quantity AS NUMERIC) > 0", name="ck_part_relation_quantity"),
    CheckConstraint("assembly_id <> component_id", name="ck_part_relation_not_self"),
    Index("ix_part_relation_component_id", "component_id"),
)
