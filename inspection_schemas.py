from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator


class InspectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    plan_id: UUID
    inspector_name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=2000)
    finding: Literal[
        "no_issue_observed", "issue_observed", "unable_to_verify"
    ]
    # 一条记录对应一个明确点位；None 表示具体点位待核实。
    location_id: str | None = Field(default=None, min_length=1, max_length=64)
    observed_at: AwareDatetime | None = None

    @field_validator("inspector_name", "description", "location_id")
    @classmethod
    def reject_blank_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("不能仅包含空白字符")
        return value

class InspectionRead(InspectionCreate):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    recorded_at: AwareDatetime
    version: int = Field(ge=1)