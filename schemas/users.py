from datetime import datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from models import UserRole


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=100)
    password: str = Field(min_length=12, max_length=128)
    role: UserRole = UserRole.organization
    organization_id: int | None = Field(default=None, gt=0)

    model_config = ConfigDict(from_attributes=True, str_strip_whitespace=True)

    @model_validator(mode="after")
    def validate_role_organization(self) -> Self:
        if self.role == UserRole.admin and self.organization_id is not None:
            raise ValueError("Admin users must not belong to an organization.")
        if self.role == UserRole.organization and self.organization_id is None:
            raise ValueError("Organization users must belong to an organization.")
        return self


class PasswordResetRequest(BaseModel):
    password: str = Field(min_length=12, max_length=128)

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class PersonnelCreate(BaseModel):
    username: str = Field(min_length=3, max_length=100)
    password: str = Field(min_length=12, max_length=128)

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PersonnelStatusUpdate(BaseModel):
    is_active: bool

    model_config = ConfigDict(extra="forbid")


class UserResponse(BaseModel):
    id: int
    username: str = Field(min_length=3, max_length=100)
    role: UserRole
    organization_id: int | None
    is_active: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)
