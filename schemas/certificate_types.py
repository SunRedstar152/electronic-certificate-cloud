from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from models import RecordStatus


class CertificateTypeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=4000)
    status: RecordStatus = RecordStatus.active

    model_config = ConfigDict(str_strip_whitespace=True)


class CertificateTypeResponse(CertificateTypeCreate):
    id: int
    organization_id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)
