from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from models import RecordStatus


class FontResponse(BaseModel):
    source: Literal["builtin", "uploaded"]
    key: str | None = None
    id: int | None = None
    organization_id: int | None = None
    display_name: str = Field(min_length=1, max_length=255)
    supports_thai: bool
    status: RecordStatus
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="after")
    def identifier_matches_source(self):
        if self.source == "builtin" and (self.key is None or self.id is not None):
            raise ValueError("A built-in font requires only a key.")
        if self.source == "uploaded" and (self.id is None or self.key is not None):
            raise ValueError("An uploaded font requires only an id.")
        return self


__all__ = ["FontResponse"]
