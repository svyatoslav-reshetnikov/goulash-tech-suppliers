"""категории-ингредиенты и связь с позициями

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-24
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("items", sa.Column("ingredients_hash", sa.String(64)))
    op.create_table(
        "categories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("normalized_name", sa.String(120), nullable=False, unique=True),
        sa.Column("parent_id", sa.Integer(), sa.ForeignKey("categories.id")),
        sa.Column("is_group", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("kind", sa.String(16), nullable=False, server_default="ingredient"),
        sa.Column("synonyms", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("origin", sa.String(16), nullable=False, server_default="derived"),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_categories_status", "categories", ["status"])
    op.create_table(
        "item_categories",
        sa.Column("item_id", sa.Integer(), sa.ForeignKey("items.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("category_id", sa.Integer(), sa.ForeignKey("categories.id", ondelete="CASCADE"), primary_key=True),
    )


def downgrade() -> None:
    op.drop_table("item_categories")
    op.drop_index("ix_categories_status", table_name="categories")
    op.drop_table("categories")
    op.drop_column("items", "ingredients_hash")
