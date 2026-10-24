"""запуски агента, поставщики и кэш страниц

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-24
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("category_id", sa.Integer(), sa.ForeignKey("categories.id", ondelete="SET NULL")),
        sa.Column("category_name", sa.String(120), nullable=False),
        sa.Column("location_kind", sa.String(16)),
        sa.Column("location_key", sa.String(200)),
        sa.Column("location_label", sa.String(200)),
        sa.Column("include_neighbors", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("regions", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("supplier_type", sa.String(32)),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("stop_reason", sa.String(32)),
        sa.Column("summary", sa.Text()),
        sa.Column("error", sa.Text()),
        sa.Column("stats", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "run_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_run_events_run_seq", "run_events", ["run_id", "seq"], unique=True)
    op.create_table(
        "suppliers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("domain", sa.String(255), nullable=False, unique=True),
        sa.Column("website", sa.Text(), nullable=False),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("inn", sa.String(12)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_suppliers_inn", "suppliers", ["inn"])
    op.create_table(
        "supplier_fields",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("supplier_id", sa.Integer(), sa.ForeignKey("suppliers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("field", sa.String(32), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("runs.id", ondelete="SET NULL")),
        sa.Column("verified_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_supplier_fields_supplier", "supplier_fields", ["supplier_id", "field"])
    op.create_table(
        "run_suppliers",
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("supplier_id", sa.Integer(), sa.ForeignKey("suppliers.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("found_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("score", sa.Float()),
        sa.Column("rank", sa.Integer()),
        sa.Column("location_tier", sa.String(16)),
        sa.Column("explanation", sa.Text()),
    )
    op.create_table(
        "fetched_pages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("url", sa.Text(), nullable=False, unique=True),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("links", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    for table in ["fetched_pages", "run_suppliers", "supplier_fields", "suppliers", "run_events", "runs"]:
        op.drop_table(table)
