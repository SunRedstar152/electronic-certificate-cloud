from __future__ import annotations

import enum
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class UserRole(str, enum.Enum):
    admin = "admin"
    organization = "organization"


class RecordStatus(str, enum.Enum):
    active = "active"
    inactive = "inactive"


class BatchStatus(str, enum.Enum):
    draft = "draft"
    validated = "validated"
    processing = "processing"
    completed = "completed"
    partial = "partial"
    failed = "failed"


class CertificateStatus(str, enum.Enum):
    active = "active"
    revoked = "revoked"


class ActivityStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class CertificateRecipientType(str, enum.Enum):
    INTERNAL = "INTERNAL"
    EXTERNAL = "EXTERNAL"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class Organization(TimestampMixin, Base):
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    code: Mapped[str] = mapped_column(String(32))
    contact_name: Mapped[str | None] = mapped_column(String(255))
    contact_email: Mapped[str | None] = mapped_column(String(320))
    contact_phone: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[RecordStatus] = mapped_column(
        Enum(RecordStatus, name="record_status"), default=RecordStatus.active
    )

    __table_args__ = (
        UniqueConstraint("code", name="uq_organizations_code"),
        Index("uq_organizations_name_ci", func.lower(name), unique=True),
    )

    users: Mapped[list[User]] = relationship(back_populates="organization")
    templates: Mapped[list[Template]] = relationship(back_populates="organization")
    font_assets: Mapped[list[FontAsset]] = relationship(back_populates="organization")
    batches: Mapped[list[Batch]] = relationship(back_populates="organization")
    certificates: Mapped[list[Certificate]] = relationship(back_populates="organization")
    activities: Mapped[list[Activity]] = relationship(
        back_populates="organization", foreign_keys="Activity.organization_id"
    )
    certificate_types: Mapped[list[CertificateType]] = relationship(back_populates="organization")
    audit_logs: Mapped[list[AuditLog]] = relationship(back_populates="organization")


class User(TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "(role = 'admin' AND organization_id IS NULL) OR "
            "(role = 'organization' AND organization_id IS NOT NULL)",
            name="ck_users_role_organization",
        ),
        UniqueConstraint("username", name="uq_users_username"),
        UniqueConstraint(
            "id", "organization_id", name="uq_users_id_organization_id"
        ),
        Index("ix_users_username", "username"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(Enum(UserRole, name="user_role"))
    organization_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    organization: Mapped[Organization | None] = relationship(back_populates="users")
    batches_created: Mapped[list[Batch]] = relationship(
        back_populates="created_by", foreign_keys="Batch.created_by_id"
    )
    certificates_created: Mapped[list[Certificate]] = relationship(
        back_populates="created_by", foreign_keys="Certificate.created_by_id"
    )
    certificates_revoked: Mapped[list[Certificate]] = relationship(
        back_populates="revoked_by", foreign_keys="Certificate.revoked_by_id"
    )
    activities_proposed: Mapped[list[Activity]] = relationship(
        back_populates="proposed_by", foreign_keys="Activity.proposed_by_id"
    )
    activities_approved: Mapped[list[Activity]] = relationship(
        back_populates="approved_by", foreign_keys="Activity.approved_by_id"
    )
    activity_approval_logs: Mapped[list[ActivityApprovalLog]] = relationship(
        back_populates="decided_by", foreign_keys="ActivityApprovalLog.decided_by_id"
    )
    audit_logs: Mapped[list[AuditLog]] = relationship(back_populates="actor")

class LoginAttemptBucket(Base):
    """Short-lived shared login counters; keys contain only keyed HMAC digests."""

    __tablename__ = "login_attempt_buckets"
    __table_args__ = (
        CheckConstraint("failed_attempts >= 0", name="ck_login_attempt_buckets_failures"),
        Index("ix_login_attempt_buckets_expires_at", "expires_at"),
    )

    bucket_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Template(TimestampMixin, Base):
    __tablename__ = "templates"
    __table_args__ = (
        UniqueConstraint(
            "id", "organization_id", name="uq_templates_id_organization_id"
        ),
        UniqueConstraint("organization_id", "builtin_key", name="uq_templates_organization_builtin"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), index=True
    )
    name: Mapped[str] = mapped_column(String(255))
    background_image_path: Mapped[str] = mapped_column(String(1024))
    builtin_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    positions: Mapped[dict[str, Any]] = mapped_column(JSONB)
    dimensions: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    layout: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[RecordStatus] = mapped_column(
        Enum(RecordStatus, name="record_status"), default=RecordStatus.active
    )

    organization: Mapped[Organization] = relationship(back_populates="templates")
    batches: Mapped[list[Batch]] = relationship(
        back_populates="template", foreign_keys="Batch.template_id"
    )
    certificates: Mapped[list[Certificate]] = relationship(
        back_populates="template", foreign_keys="Certificate.template_id"
    )

    @property
    def is_builtin(self) -> bool:
        return self.builtin_key is not None


class FontAsset(TimestampMixin, Base):
    __tablename__ = "font_assets"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "sha256",
            name="uq_font_assets_organization_sha256",
        ),
        UniqueConstraint(
            "id",
            "organization_id",
            name="uq_font_assets_id_organization_id",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), index=True
    )
    display_name: Mapped[str] = mapped_column(String(255))
    original_filename: Mapped[str] = mapped_column(String(255))
    file_path: Mapped[str] = mapped_column(String(1024))
    sha256: Mapped[str] = mapped_column(String(64))
    format: Mapped[str] = mapped_column(String(8))
    supports_thai: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[RecordStatus] = mapped_column(
        Enum(RecordStatus, name="record_status"), default=RecordStatus.active
    )

    organization: Mapped[Organization] = relationship(back_populates="font_assets")


class Activity(TimestampMixin, Base):
    __tablename__ = "activities"
    __table_args__ = (Index("ix_activities_organization_status", "organization_id", "approval_status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"), index=True)
    department_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        default=lambda context: context.get_current_parameters()["organization_id"],
        index=True,
    )
    activity_name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    activity_date: Mapped[date] = mapped_column(Date)
    activity_type: Mapped[str] = mapped_column(String(100))
    proposed_by_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    approval_status: Mapped[ActivityStatus] = mapped_column(Enum(ActivityStatus, name="activity_status"), default=ActivityStatus.DRAFT, index=True)
    approved_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approval_comment: Mapped[str | None] = mapped_column(Text)

    organization: Mapped[Organization] = relationship(back_populates="activities", foreign_keys=[organization_id])
    proposed_by: Mapped[User] = relationship(back_populates="activities_proposed", foreign_keys=[proposed_by_id])
    approved_by: Mapped[User | None] = relationship(back_populates="activities_approved", foreign_keys=[approved_by_id])
    approval_logs: Mapped[list[ActivityApprovalLog]] = relationship(back_populates="activity", cascade="all, delete-orphan")


class ActivityApprovalLog(Base):
    __tablename__ = "activity_approval_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    activity_id: Mapped[int] = mapped_column(ForeignKey("activities.id", ondelete="CASCADE"), index=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"), index=True)
    decision: Mapped[ActivityStatus] = mapped_column(Enum(ActivityStatus, name="activity_status", create_type=False))
    decided_by_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    activity: Mapped[Activity] = relationship(back_populates="approval_logs")
    decided_by: Mapped[User] = relationship(back_populates="activity_approval_logs", foreign_keys=[decided_by_id])


class CertificateType(TimestampMixin, Base):
    __tablename__ = "certificate_types"
    __table_args__ = (UniqueConstraint("organization_id", "name", name="uq_certificate_types_org_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[RecordStatus] = mapped_column(Enum(RecordStatus, name="record_status", create_type=False), default=RecordStatus.active)

    organization: Mapped[Organization | None] = relationship(back_populates="certificate_types")


class Batch(TimestampMixin, Base):
    __tablename__ = "batches"
    __table_args__ = (
        CheckConstraint("total_count >= 0", name="ck_batches_total_count"),
        CheckConstraint("success_count >= 0", name="ck_batches_success_count"),
        CheckConstraint("failure_count >= 0", name="ck_batches_failure_count"),
        UniqueConstraint(
            "id", "organization_id", name="uq_batches_id_organization_id"
        ),
        ForeignKeyConstraint(
            ["template_id", "organization_id"],
            ["templates.id", "templates.organization_id"],
            name="fk_batches_template_organization",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["created_by_id", "organization_id"],
            ["users.id", "users.organization_id"],
            name="fk_batches_creator_organization",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), index=True
    )
    template_id: Mapped[int] = mapped_column(index=True)
    topic: Mapped[str] = mapped_column(String(500))
    issue_date: Mapped[date] = mapped_column(Date)
    event_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    source_type: Mapped[str] = mapped_column(String(32))
    status: Mapped[BatchStatus] = mapped_column(
        Enum(BatchStatus, name="batch_status"), default=BatchStatus.draft
    )
    total_count: Mapped[int] = mapped_column(Integer, default=0)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    created_by_id: Mapped[int] = mapped_column(index=True)
    activity_id: Mapped[int | None] = mapped_column(ForeignKey("activities.id", name="fk_batches_activity", ondelete="RESTRICT"), index=True)
    certificate_type_id: Mapped[int | None] = mapped_column(ForeignKey("certificate_types.id", name="fk_batches_certificate_type", ondelete="RESTRICT"), index=True)
    recipient_type: Mapped[CertificateRecipientType] = mapped_column(
        Enum(CertificateRecipientType, name="certificate_recipient_type"),
        default=CertificateRecipientType.INTERNAL,
        server_default="INTERNAL",
    )
    external_organization: Mapped[str | None] = mapped_column(String(255))

    organization: Mapped[Organization] = relationship(back_populates="batches")
    template: Mapped[Template] = relationship(
        back_populates="batches", foreign_keys=[template_id]
    )
    created_by: Mapped[User] = relationship(
        back_populates="batches_created", foreign_keys=[created_by_id]
    )
    rows: Mapped[list[BatchRow]] = relationship(
        back_populates="batch", cascade="all, delete-orphan"
    )
    certificates: Mapped[list[Certificate]] = relationship(
        back_populates="batch", foreign_keys="Certificate.batch_id"
    )
    activity: Mapped[Activity | None] = relationship()
    certificate_type: Mapped[CertificateType | None] = relationship()


class BatchRow(TimestampMixin, Base):
    __tablename__ = "batch_rows"
    __table_args__ = (
        CheckConstraint("row_number > 0", name="ck_batch_rows_row_number"),
        UniqueConstraint("batch_id", "row_number", name="uq_batch_rows_batch_row_number"),
        ForeignKeyConstraint(
            ["certificate_id", "batch_id"],
            ["certificates.id", "certificates.batch_id"],
            name="fk_batch_rows_certificate_batch",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("batches.id", ondelete="CASCADE"), index=True
    )
    row_number: Mapped[int] = mapped_column(Integer)
    recipient_reference: Mapped[str | None] = mapped_column(String(255))
    recipient_name: Mapped[str | None] = mapped_column(String(255))
    normalized_data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    certificate_id: Mapped[int | None] = mapped_column(unique=True)
    recipient_type: Mapped[CertificateRecipientType] = mapped_column(Enum(CertificateRecipientType, name="certificate_recipient_type"), default=CertificateRecipientType.INTERNAL, server_default="INTERNAL")
    external_organization: Mapped[str | None] = mapped_column(String(255))

    batch: Mapped[Batch] = relationship(back_populates="rows")
    certificate: Mapped[Certificate | None] = relationship(
        back_populates="batch_row", foreign_keys=[certificate_id]
    )


class Certificate(TimestampMixin, Base):
    __tablename__ = "certificates"
    __table_args__ = (
        UniqueConstraint(
            "batch_id",
            "recipient_reference",
            name="uq_certificates_batch_recipient_reference",
        ),
        UniqueConstraint("verification_code", name="uq_certificates_verification_code"),
        UniqueConstraint(
            "organization_id", "idempotency_key", name="uq_certificates_org_idempotency_key"
        ),
        UniqueConstraint("id", "batch_id", name="uq_certificates_id_batch_id"),
        ForeignKeyConstraint(
            ["batch_id", "organization_id"],
            ["batches.id", "batches.organization_id"],
            name="fk_certificates_batch_organization",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["template_id", "organization_id"],
            ["templates.id", "templates.organization_id"],
            name="fk_certificates_template_organization",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["created_by_id", "organization_id"],
            ["users.id", "users.organization_id"],
            name="fk_certificates_creator_organization",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["revoked_by_id", "organization_id"],
            ["users.id", "users.organization_id"],
            name="fk_certificates_revoker_organization",
            ondelete="RESTRICT",
        ),
        Index("ix_certificates_verification_code", "verification_code"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), index=True
    )
    batch_id: Mapped[int | None] = mapped_column(index=True)
    template_id: Mapped[int] = mapped_column(index=True)
    verification_code: Mapped[str] = mapped_column(String(64))
    recipient_reference: Mapped[str] = mapped_column(String(255))
    recipient_name: Mapped[str] = mapped_column(String(255))
    topic: Mapped[str] = mapped_column(String(500))
    custom_fields: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    issue_date: Mapped[date] = mapped_column(Date)
    status: Mapped[CertificateStatus] = mapped_column(
        Enum(CertificateStatus, name="certificate_status"),
        default=CertificateStatus.active,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revocation_reason: Mapped[str | None] = mapped_column(Text)
    revoked_by_id: Mapped[int | None] = mapped_column()
    image_path: Mapped[str | None] = mapped_column(String(1024))
    idempotency_key: Mapped[str | None] = mapped_column(String(255))
    created_by_id: Mapped[int] = mapped_column(index=True)
    activity_id: Mapped[int | None] = mapped_column(ForeignKey("activities.id", name="fk_certificates_activity", ondelete="RESTRICT"), index=True)
    certificate_type_id: Mapped[int | None] = mapped_column(ForeignKey("certificate_types.id", name="fk_certificates_certificate_type", ondelete="RESTRICT"), index=True)
    recipient_type: Mapped[CertificateRecipientType] = mapped_column(Enum(CertificateRecipientType, name="certificate_recipient_type"), default=CertificateRecipientType.INTERNAL, server_default="INTERNAL")
    external_organization: Mapped[str | None] = mapped_column(String(255))

    organization: Mapped[Organization] = relationship(back_populates="certificates")
    batch: Mapped[Batch | None] = relationship(
        back_populates="certificates", foreign_keys=[batch_id]
    )
    template: Mapped[Template] = relationship(
        back_populates="certificates", foreign_keys=[template_id]
    )
    created_by: Mapped[User] = relationship(
        back_populates="certificates_created", foreign_keys=[created_by_id]
    )
    revoked_by: Mapped[User | None] = relationship(
        back_populates="certificates_revoked", foreign_keys=[revoked_by_id]
    )
    batch_row: Mapped[BatchRow | None] = relationship(
        back_populates="certificate", foreign_keys="BatchRow.certificate_id"
    )
    activity: Mapped[Activity | None] = relationship()
    certificate_type: Mapped[CertificateType | None] = relationship()


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    organization_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="SET NULL"), index=True
    )
    action: Mapped[str] = mapped_column(String(100), index=True)
    target_type: Mapped[str] = mapped_column(String(100))
    target_id: Mapped[str | None] = mapped_column(String(255))
    correlation_id: Mapped[str | None] = mapped_column(String(100), index=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    actor: Mapped[User | None] = relationship(back_populates="audit_logs")
    organization: Mapped[Organization | None] = relationship(back_populates="audit_logs")
