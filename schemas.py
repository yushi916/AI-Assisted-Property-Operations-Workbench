from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator
from location_schemas import LocationKind


class ServiceRequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    service_category: Literal["cleaning", "water_supply", "other"]
    description: str = Field(min_length=1, max_length=2000)
    source: Literal["web", "phone", "on_site"]
    risk_status: Literal[
        "no_immediate_risk_reported", "urgent", "unknown"
    ]
    location_id: str | None = Field(default=None, min_length=1, max_length=64)
    observed_at: AwareDatetime | None = None

    @field_validator("description", "location_id")
    @classmethod
    def reject_blank_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("不能仅包含空白字符")
        return value

class ServiceRequestRead(ServiceRequestCreate):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    received_at: AwareDatetime
    version: int = Field(ge=1)

class LocationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    version: int = Field(ge=1)
    kind: LocationKind
    parent_id: str | None = Field(default=None, min_length=1, max_length=64)
    display_path: str = Field(min_length=1)