"""Work status of a supplier within a search

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-26
"""
import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # idempotent: safe to run again on a database where the table already exists
    if sa.inspect(op.get_bind()).has_table("supplier_statuses"):
        return
    op.create_table(
        "supplier_statuses",
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("supplier_id", sa.Integer(), sa.ForeignKey("suppliers.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("supplier_statuses")
