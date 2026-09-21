from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from models import ActivityStatus


class ActivityCreate(BaseModel):
    activity_name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=4000)
    activity_date: date
    activity_type: str = Field(min_length=1, max_length=100)

    model_config = ConfigDict(str_strip_whitespace=True)


class ActivityResponse(ActivityCreate):
    id: int
    organization_id: int
    department_id: int
    proposed_by_id: int
    approval_status: ActivityStatus
    approved_by_id: int | None = None
    approved_at: datetime | None = None
    approval_comment: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class ActivityDecision(BaseModel):
    comment: str | None = Field(default=None, max_length=2000)

    model_config = ConfigDict(str_strip_whitespace=True)
