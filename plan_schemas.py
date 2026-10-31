from datetime import UTC
from typing import Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class PlanCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    title: str = Field(min_length=1, max_length=120)
    service_category: Literal["cleaning", "water_supply", "other"]
    plan_kind: Literal["routine", "special"] = "routine"
    requirements: str = Field(min_length=1, max_length=2000)
    owner_name: str = Field(min_length=1, max_length=120)
    location_ids: list[str] = Field(min_length=1)
    planned_start_at: AwareDatetime
    planned_end_at: AwareDatetime

    @field_validator("title", "requirements", "owner_name")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("不能仅包含空白字符")
        return value

    @field_validator("location_ids")
    @classmethod
    def validate_locations(cls, values: list[str]) -> list[str]:
        for value in values:
            if not value.strip() or len(value) > 64:
                raise ValueError("地点编号须为1至64个字符且不能仅含空白")
        if len(values) != len(set(values)):
            raise ValueError("计划覆盖地点不能重复")
        return values

    @model_validator(mode="after")
    def validate_period(self):
        start = self.planned_start_at.astimezone(UTC)
        end = self.planned_end_at.astimezone(UTC)
        if end <= start:
            raise ValueError("计划结束时间必须晚于开始时间")
        return self

class PlanRead(PlanCreate):
    id: UUID
    created_at: AwareDatetime
    version: int = Field(ge=1)