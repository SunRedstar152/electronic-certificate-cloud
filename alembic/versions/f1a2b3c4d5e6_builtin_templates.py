"""Identify the permanent system template for each organization."""

from alembic import op
import sqlalchemy as sa

revision = "f1a2b3c4d5e6"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("templates", sa.Column("builtin_key", sa.String(64), nullable=True))
    op.create_unique_constraint(
        "uq_templates_organization_builtin", "templates", ["organization_id", "builtin_key"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_templates_organization_builtin", "templates", type_="unique")
    op.drop_column("templates", "builtin_key")
