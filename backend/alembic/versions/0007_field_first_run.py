"""запуск, в котором поле поставщика найдено впервые

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-26
"""
import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("supplier_fields", sa.Column("first_run_id", sa.Integer()))
    # для старых полей точнее не узнать: берём последний подтвердивший запуск
    op.execute("UPDATE supplier_fields SET first_run_id = run_id")
    op.create_index("ix_supplier_fields_first_run_id", "supplier_fields", ["first_run_id"])


def downgrade() -> None:
    op.drop_index("ix_supplier_fields_first_run_id", table_name="supplier_fields")
    op.drop_column("supplier_fields", "first_run_id")
