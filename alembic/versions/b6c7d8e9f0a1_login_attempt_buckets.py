"""Persist shared login rate-limit counters without storing account or IP data."""

from alembic import op
import sqlalchemy as sa


revision = "b6c7d8e9f0a1"
down_revision = "a2b3c4d5e6f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "login_attempt_buckets",
        sa.Column("bucket_key", sa.String(64), nullable=False),
        sa.Column("failed_attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("failed_attempts >= 0", name="ck_login_attempt_buckets_failures"),
        sa.PrimaryKeyConstraint("bucket_key"),
    )
    op.create_index("ix_login_attempt_buckets_expires_at", "login_attempt_buckets", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_login_attempt_buckets_expires_at", table_name="login_attempt_buckets")
    op.drop_table("login_attempt_buckets")
