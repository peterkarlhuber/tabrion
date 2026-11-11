"""The initial parts schema: material, part, property, part_relation (RA-A-1).

Revision ID: 0001
Revises:
"""

from __future__ import annotations

from alembic import op
from parts_baseline.schema import ID
from parts_baseline.types import ExactDecimal, UtcDateTime
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def _id() -> sa.Column[int]:
    return sa.Column("id", ID, sa.Identity(always=True), nullable=False)


def upgrade() -> None:
    op.create_table(
        "material",
        _id(),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_material"),
        sa.UniqueConstraint("code", name="uq_material_code"),
        sa.CheckConstraint("length(code) BETWEEN 1 AND 32", name="ck_material_code"),
        sa.CheckConstraint("length(name) BETWEEN 1 AND 200", name="ck_material_name"),
    )
    op.create_table(
        "part",
        _id(),
        sa.Column("part_number", sa.String(40), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column(
            "material_id",
            ID,
            sa.ForeignKey("material.id", name="fk_part_material"),
            nullable=False,
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'draft'")),
        sa.Column("version", sa.Integer, nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.Column("updated_at", UtcDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_part"),
        sa.UniqueConstraint("part_number", name="uq_part_part_number"),
        sa.CheckConstraint("length(part_number) BETWEEN 1 AND 40", name="ck_part_part_number"),
        sa.CheckConstraint("length(name) BETWEEN 1 AND 200", name="ck_part_name"),
        sa.CheckConstraint("status IN ('draft', 'released', 'blocked', 'obsolete')", name="ck_part_status"),
        sa.CheckConstraint("version >= 1", name="ck_part_version"),
    )
    op.create_index("ix_part_material_id", "part", ["material_id"])
    op.create_table(
        "property",
        _id(),
        sa.Column("part_id", ID, sa.ForeignKey("part.id", name="fk_property_part", ondelete="CASCADE"), nullable=False),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("value", ExactDecimal(18, 6), nullable=False),
        sa.Column("unit", sa.String(16), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_property"),
        sa.UniqueConstraint("part_id", "key", name="uq_property_part_id_key"),
        sa.CheckConstraint("length(key) BETWEEN 1 AND 64", name="ck_property_key"),
        sa.CheckConstraint("length(unit) BETWEEN 1 AND 16", name="ck_property_unit"),
    )
    op.create_table(
        "part_relation",
        sa.Column(
            "assembly_id",
            ID,
            sa.ForeignKey("part.id", name="fk_part_relation_assembly", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "component_id",
            ID,
            sa.ForeignKey("part.id", name="fk_part_relation_component"),
            nullable=False,
        ),
        sa.Column("quantity", ExactDecimal(12, 3), nullable=False),
        sa.PrimaryKeyConstraint("assembly_id", "component_id", name="pk_part_relation"),
        sa.CheckConstraint("quantity > 0", name="ck_part_relation_quantity"),
        sa.CheckConstraint("assembly_id <> component_id", name="ck_part_relation_not_self"),
    )
    op.create_index("ix_part_relation_component_id", "part_relation", ["component_id"])


def downgrade() -> None:
    op.drop_index("ix_part_relation_component_id", table_name="part_relation")
    op.drop_table("part_relation")
    op.drop_table("property")
    op.drop_index("ix_part_material_id", table_name="part")
    op.drop_table("part")
    op.drop_table("material")
