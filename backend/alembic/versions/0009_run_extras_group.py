"""Optional search requirements and grouping of multi-product searches

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-26
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # idempotent: columns that already exist are left alone
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("runs")}
    if "extras" not in columns:
        op.add_column("runs", sa.Column("extras", JSONB(), nullable=True))
    if "group_id" not in columns:
        op.add_column("runs", sa.Column("group_id", sa.Integer(), nullable=True))
        op.create_index("ix_runs_group_id", "runs", ["group_id"])


def downgrade() -> None:
    op.drop_index("ix_runs_group_id", table_name="runs")
    op.drop_column("runs", "group_id")
    op.drop_column("runs", "extras")
