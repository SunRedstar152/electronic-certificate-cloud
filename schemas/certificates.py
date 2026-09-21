from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from models import CertificateRecipientType, CertificateStatus
from schemas.signatures import SignatureSubmission


class CertificateFields(BaseModel):
    template_id: int = Field(gt=0)
    recipient_name: str = Field(min_length=1, max_length=255)
    topic: str = Field(min_length=1, max_length=500)
    issue_date: date
    custom_fields: dict[str, Any] = Field(default_factory=dict)
    activity_id: int | None = Field(default=None, gt=0)
    certificate_type_id: int | None = Field(default=None, gt=0)
    recipient_type: CertificateRecipientType = CertificateRecipientType.INTERNAL
    external_organization: str | None = Field(default=None, max_length=255)

    model_config = ConfigDict(from_attributes=True, str_strip_whitespace=True)

    @model_validator(mode="after")
    def validate_recipient(self):
        if self.recipient_type is CertificateRecipientType.EXTERNAL and not self.external_organization:
            raise ValueError("External recipients require an external organization.")
        return self


class CertificateBase(CertificateFields):
    recipient_reference: str = Field(min_length=1, max_length=255)


class CertificateCreate(CertificateFields):
    recipient_reference: str | None = Field(default=None, max_length=255)
    batch_id: int | None = Field(default=None, gt=0)


class CertificateIssuanceRequest(CertificateCreate):
    """One-time request data used to render a transient signature block."""

    signature: SignatureSubmission | None = None
    signatures: dict[str, SignatureSubmission] | None = None


class CertificateRevokeRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)

    model_config = ConfigDict(from_attributes=True, str_strip_whitespace=True)


class CertificateResponse(CertificateBase):
    id: int
    organization_id: int
    batch_id: int | None
    verification_code: str = Field(min_length=1, max_length=64)
    status: CertificateStatus
    image_path: str | None = Field(default=None, max_length=1024)
    revoked_at: datetime | None = None
    revocation_reason: str | None = Field(default=None, max_length=2000)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class PublicCertificateResponse(BaseModel):
    verification_code: str = Field(min_length=1, max_length=64)
    recipient_name: str = Field(min_length=1, max_length=255)
    topic: str = Field(min_length=1, max_length=500)
    issue_date: date
    status: CertificateStatus

    model_config = ConfigDict(from_attributes=True)
