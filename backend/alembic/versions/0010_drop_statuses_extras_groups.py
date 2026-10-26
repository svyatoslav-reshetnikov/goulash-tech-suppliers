"""убраны статусы поставщиков, дополнительные требования и поиск по нескольким товарам

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-26
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("supplier_statuses"):
        op.drop_table("supplier_statuses")
    columns = {c["name"] for c in inspector.get_columns("runs")}
    if "group_id" in columns:
        op.drop_index("ix_runs_group_id", table_name="runs", if_exists=True)
        op.drop_column("runs", "group_id")
    if "extras" in columns:
        op.drop_column("runs", "extras")


def downgrade() -> None:
    op.add_column("runs", sa.Column("extras", JSONB(), nullable=True))
    op.add_column("runs", sa.Column("group_id", sa.Integer(), nullable=True))
    op.create_index("ix_runs_group_id", "runs", ["group_id"])
    op.create_table(
        "supplier_statuses",
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("supplier_id", sa.Integer(), sa.ForeignKey("suppliers.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
