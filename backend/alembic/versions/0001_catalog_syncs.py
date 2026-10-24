"""журнал синхронизаций каталога

Revision ID: 0001
Revises:
Create Date: 2026-10-24
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "catalog_syncs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("trigger", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("partial", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("stats", postgresql.JSONB()),
        sa.Column("error", sa.Text()),
    )


def downgrade() -> None:
    op.drop_table("catalog_syncs")
