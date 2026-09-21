from pydantic import BaseModel, ConfigDict, Field


class ErrorDetail(BaseModel):
    code: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=2000)
    correlation_id: str | None = Field(default=None, min_length=1, max_length=100)

    model_config = ConfigDict(from_attributes=True)


class ErrorResponse(BaseModel):
    error: ErrorDetail

    model_config = ConfigDict(from_attributes=True)
