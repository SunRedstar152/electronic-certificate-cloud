from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from models import BatchStatus, CertificateRecipientType
from schemas.signatures import SignatureSubmission


class BatchCreate(BaseModel):
    template_id: int = Field(gt=0)
    topic: str = Field(min_length=1, max_length=500)
    issue_date: date
    source_type: Literal["xlsx"] = "xlsx"
    event_metadata: dict[str, Any] = Field(default_factory=dict)
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


class BatchRowCreate(BaseModel):
    row_number: int = Field(gt=0)
    recipient_reference: str | None = Field(default=None, max_length=255)
    recipient_name: str | None = Field(default=None, max_length=255)
    normalized_data: dict[str, Any] = Field(default_factory=dict)
    recipient_type: CertificateRecipientType = CertificateRecipientType.INTERNAL
    external_organization: str | None = Field(default=None, max_length=255)

    model_config = ConfigDict(from_attributes=True, str_strip_whitespace=True)


class BatchRowResponse(BatchRowCreate):
    id: int
    batch_id: int
    status: str = Field(min_length=1, max_length=32)
    error_code: str | None = Field(default=None, max_length=100)
    error_message: str | None = Field(default=None, max_length=2000)
    certificate_id: int | None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class BatchResponse(BatchCreate):
    # Historical batches retain their original source without enabling new imports.
    source_type: str = Field(min_length=1, max_length=32)
    id: int
    organization_id: int
    status: BatchStatus
    total_count: int = Field(ge=0)
    success_count: int = Field(ge=0)
    failure_count: int = Field(ge=0)
    created_by_id: int
    created_at: datetime | None = None
    updated_at: datetime | None = None
    rows: list[BatchRowResponse] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class BatchSummary(BaseModel):
    id: int
    status: BatchStatus
    total_count: int = Field(ge=0)
    success_count: int = Field(ge=0)
    failure_count: int = Field(ge=0)

    model_config = ConfigDict(from_attributes=True)


class BatchProcessRequest(BaseModel):
    signature: SignatureSubmission | None = None
    signatures: dict[str, SignatureSubmission] | None = None

    model_config = ConfigDict(extra="forbid")
