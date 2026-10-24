"""охват изучения сайта поставщика

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-24
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("suppliers", sa.Column("research", postgresql.JSONB()))


def downgrade() -> None:
    op.drop_column("suppliers", "research")
