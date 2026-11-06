from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RequestPlanLinkCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    plan_id: UUID
    linked_by: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator("linked_by", "reason")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("不能仅包含空白字符")
        return value