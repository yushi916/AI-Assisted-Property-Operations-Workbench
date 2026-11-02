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


class ExecutionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    plan_id: UUID
    executor_name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=2000)
    completion_claim: Literal[
        "completed", "partially_completed", "not_completed", "unknown"
    ]
    # 空列表明确表示具体点位待核实，不表示覆盖整个计划。
    location_ids: list[str]
    claimed_started_at: AwareDatetime | None = None
    claimed_finished_at: AwareDatetime | None = None

    @field_validator("executor_name", "description")
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
            raise ValueError("执行记录的地点不能重复")
        return values

    @model_validator(mode="after")
    def validate_claimed_times(self):
        start = self.claimed_started_at
        finish = self.claimed_finished_at
        if self.completion_claim != "completed" and finish is not None:
            raise ValueError("未声称已完成时，不能填写声称完成时间")
        if start is not None and finish is not None:
            if finish.astimezone(UTC) < start.astimezone(UTC):
                raise ValueError("声称完成时间不能早于声称开始时间")
        return self