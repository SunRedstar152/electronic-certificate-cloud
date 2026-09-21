from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SignatureSubmission(BaseModel):
    png_data_url: str | None = None
    signer_name: str | None = Field(default=None, max_length=255)
    signer_position: str | None = Field(default=None, max_length=255)

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


__all__ = ["SignatureSubmission"]
