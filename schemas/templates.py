from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from models import RecordStatus


class TemplateBase(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    positions: dict[str, Any]
    dimensions: dict[str, Any] = Field(default_factory=dict)
    layout: dict[str, Any] | None = None

    model_config = ConfigDict(from_attributes=True, str_strip_whitespace=True)


class TemplateCreate(TemplateBase):
    pass


class TemplateUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    positions: dict[str, Any] | None = None
    dimensions: dict[str, Any] | None = None
    layout: dict[str, Any] | None = None
    status: RecordStatus | None = None

    model_config = ConfigDict(from_attributes=True, str_strip_whitespace=True)


class TemplateResponse(TemplateBase):
    id: int
    is_builtin: bool = False
    deleted_at: datetime | None = None
    organization_id: int
    background_image_path: str = Field(min_length=1, max_length=1024)
    status: RecordStatus
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)
