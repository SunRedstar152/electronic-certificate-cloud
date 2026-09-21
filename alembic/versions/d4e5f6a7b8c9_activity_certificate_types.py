"""add activities, approval history, certificate types and recipient metadata"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "d4e5f6a7b8c9"
down_revision = "a7c1e9f4b2d8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    activity_status = postgresql.ENUM(
        "DRAFT", "PENDING_APPROVAL", "APPROVED", "REJECTED", "CANCELLED",
        name="activity_status", create_type=False,
    )
    recipient_type = postgresql.ENUM(
        "INTERNAL", "EXTERNAL", name="certificate_recipient_type", create_type=False
    )
    activity_status.create(op.get_bind(), checkfirst=True)
    recipient_type.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "activities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("department_id", sa.Integer(), nullable=False),
        sa.Column("activity_name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("activity_date", sa.Date(), nullable=False),
        sa.Column("activity_type", sa.String(100), nullable=False),
        sa.Column("proposed_by_id", sa.Integer(), nullable=False),
        sa.Column("approval_status", activity_status, nullable=False),
        sa.Column("approved_by_id", sa.Integer()),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("approval_comment", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["department_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["proposed_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["approved_by_id"], ["users.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_activities_organization_id", "activities", ["organization_id"])
    op.create_index("ix_activities_department_id", "activities", ["department_id"])
    op.create_index("ix_activities_organization_status", "activities", ["organization_id", "approval_status"])
    op.create_index("ix_activities_approval_status", "activities", ["approval_status"])
    op.create_index("ix_activities_proposed_by_id", "activities", ["proposed_by_id"])

    op.create_table(
        "activity_approval_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("activity_id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("decision", activity_status, nullable=False),
        sa.Column("decided_by_id", sa.Integer(), nullable=False),
        sa.Column("comment", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["activity_id"], ["activities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["decided_by_id"], ["users.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_activity_approval_logs_activity_id", "activity_approval_logs", ["activity_id"])
    op.create_index("ix_activity_approval_logs_organization_id", "activity_approval_logs", ["organization_id"])
    op.create_index("ix_activity_approval_logs_decided_by_id", "activity_approval_logs", ["decided_by_id"])

    op.create_table(
        "certificate_types",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer()),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("status", postgresql.ENUM(name="record_status", create_type=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("organization_id", "name", name="uq_certificate_types_org_name"),
    )
    op.create_index("ix_certificate_types_organization_id", "certificate_types", ["organization_id"])

    op.add_column("batches", sa.Column("activity_id", sa.Integer(), nullable=True))
    op.add_column("batches", sa.Column("certificate_type_id", sa.Integer(), nullable=True))
    op.add_column("batches", sa.Column("recipient_type", recipient_type, nullable=True))
    op.add_column("batches", sa.Column("external_organization", sa.String(255), nullable=True))
    op.create_foreign_key("fk_batches_activity", "batches", "activities", ["activity_id"], ["id"], ondelete="RESTRICT")
    op.create_foreign_key("fk_batches_certificate_type", "batches", "certificate_types", ["certificate_type_id"], ["id"], ondelete="RESTRICT")
    op.execute("UPDATE batches SET recipient_type = 'INTERNAL' WHERE recipient_type IS NULL")
    op.alter_column("batches", "recipient_type", nullable=False, server_default="INTERNAL")
    op.create_index("ix_batches_activity_id", "batches", ["activity_id"])
    op.create_index("ix_batches_certificate_type_id", "batches", ["certificate_type_id"])

    op.add_column("batch_rows", sa.Column("recipient_type", recipient_type, nullable=True))
    op.add_column("batch_rows", sa.Column("external_organization", sa.String(255), nullable=True))
    op.execute("UPDATE batch_rows SET recipient_type = 'INTERNAL' WHERE recipient_type IS NULL")
    op.alter_column("batch_rows", "recipient_type", nullable=False, server_default="INTERNAL")

    op.add_column("certificates", sa.Column("activity_id", sa.Integer(), nullable=True))
    op.add_column("certificates", sa.Column("certificate_type_id", sa.Integer(), nullable=True))
    op.add_column("certificates", sa.Column("recipient_type", recipient_type, nullable=True))
    op.add_column("certificates", sa.Column("external_organization", sa.String(255), nullable=True))
    op.create_foreign_key("fk_certificates_activity", "certificates", "activities", ["activity_id"], ["id"], ondelete="RESTRICT")
    op.create_foreign_key("fk_certificates_certificate_type", "certificates", "certificate_types", ["certificate_type_id"], ["id"], ondelete="RESTRICT")
    op.execute("UPDATE certificates SET recipient_type = 'INTERNAL' WHERE recipient_type IS NULL")
    op.alter_column("certificates", "recipient_type", nullable=False, server_default="INTERNAL")
    op.create_index("ix_certificates_activity_id", "certificates", ["activity_id"])
    op.create_index("ix_certificates_certificate_type_id", "certificates", ["certificate_type_id"])


def downgrade() -> None:
    op.drop_index("ix_certificates_certificate_type_id", table_name="certificates")
    op.drop_index("ix_certificates_activity_id", table_name="certificates")
    op.drop_constraint("fk_certificates_certificate_type", "certificates", type_="foreignkey")
    op.drop_constraint("fk_certificates_activity", "certificates", type_="foreignkey")
    for column in ("external_organization", "recipient_type", "certificate_type_id", "activity_id"):
        op.drop_column("certificates", column)
    for column in ("external_organization", "recipient_type"):
        op.drop_column("batch_rows", column)
    op.drop_index("ix_batches_certificate_type_id", table_name="batches")
    op.drop_index("ix_batches_activity_id", table_name="batches")
    op.drop_constraint("fk_batches_certificate_type", "batches", type_="foreignkey")
    op.drop_constraint("fk_batches_activity", "batches", type_="foreignkey")
    for column in ("external_organization", "recipient_type", "certificate_type_id", "activity_id"):
        op.drop_column("batches", column)
    op.drop_index("ix_certificate_types_organization_id", table_name="certificate_types")
    op.drop_table("certificate_types")
    for name in ("ix_activity_approval_logs_decided_by_id", "ix_activity_approval_logs_organization_id", "ix_activity_approval_logs_activity_id"):
        op.drop_index(name, table_name="activity_approval_logs")
    op.drop_table("activity_approval_logs")
    for name in ("ix_activities_proposed_by_id", "ix_activities_approval_status", "ix_activities_organization_status", "ix_activities_department_id", "ix_activities_organization_id"):
        op.drop_index(name, table_name="activities")
    op.drop_table("activities")
    postgresql.ENUM(name="certificate_recipient_type").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="activity_status").drop(op.get_bind(), checkfirst=True)
