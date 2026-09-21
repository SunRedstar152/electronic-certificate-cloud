from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from models import RecordStatus


class OrganizationBase(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    contact_name: str | None = Field(default=None, max_length=255)
    contact_email: str | None = Field(default=None, max_length=320)
    contact_phone: str | None = Field(default=None, max_length=50)

    model_config = ConfigDict(from_attributes=True, str_strip_whitespace=True)


class OrganizationCreate(OrganizationBase):
    username: str = Field(min_length=3, max_length=100)
    password: str = Field(min_length=12, max_length=128)

    model_config = ConfigDict(
        extra="forbid", from_attributes=True, str_strip_whitespace=True
    )


class OrganizationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    contact_name: str | None = Field(default=None, max_length=255)
    contact_email: str | None = Field(default=None, max_length=320)
    contact_phone: str | None = Field(default=None, max_length=50)
    status: RecordStatus | None = None

    model_config = ConfigDict(
        extra="forbid", from_attributes=True, str_strip_whitespace=True
    )


class OrganizationResponse(OrganizationBase):
    id: int
    status: RecordStatus = RecordStatus.active
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class OrganizationListItem(BaseModel):
    organization: OrganizationResponse
    personnel_count: int = Field(ge=0)
    active_personnel_count: int = Field(ge=0)
