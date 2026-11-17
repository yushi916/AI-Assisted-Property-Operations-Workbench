from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator


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


class RequestPlanLinkRead(RequestPlanLinkCreate):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    report_id: UUID
    linked_at: AwareDatetime
    status: Literal["active", "revoked"]
    version: int = Field(ge=1)
    revocation_request_id: UUID | None
    revoked_at: AwareDatetime | None
    revoked_by: str | None
    revoke_reason: str | None

class RequestPlanLinkRevoke(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    expected_version: int = Field(ge=1, strict=True)
    revoked_by: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator("revoked_by", "reason")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("不能仅包含空白字符")
        return value