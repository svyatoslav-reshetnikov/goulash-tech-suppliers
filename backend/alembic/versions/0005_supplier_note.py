"""заметка о поставщике

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-24
"""
import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("suppliers", sa.Column("note", sa.Text()))


def downgrade() -> None:
    op.drop_column("suppliers", "note")
