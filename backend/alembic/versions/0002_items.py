"""позиции меню каталога

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-24
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_id", sa.Integer(), nullable=False, unique=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("weight", sa.String(64)),
        sa.Column("url", sa.Text(), nullable=False, server_default=""),
        sa.Column("sections", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("first_seen_sync_id", sa.Integer(), sa.ForeignKey("catalog_syncs.id")),
        sa.Column("last_seen_sync_id", sa.Integer(), sa.ForeignKey("catalog_syncs.id")),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_items_status", "items", ["status"])


def downgrade() -> None:
    op.drop_index("ix_items_status", table_name="items")
    op.drop_table("items")
