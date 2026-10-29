"""Retain historical references when removing accounts and organizations."""
from alembic import op
import sqlalchemy as sa

revision = "c8d9e0f1a2b3"
down_revision = "b6c7d8e9f0a1"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("organizations", "users"):
        op.add_column(table, sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    for table in ("users", "organizations"):
        op.drop_column(table, "deleted_at")
