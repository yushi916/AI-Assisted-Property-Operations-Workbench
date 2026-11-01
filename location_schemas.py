from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


LocationKind = Literal[
    "community", "area", "building", "unit", "room", "public_space", "unknown"
]


class LocationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    kind: LocationKind
    parent_id: str | None = Field(default=None, min_length=1, max_length=64)

    @field_validator("id", "name", "parent_id")
    @classmethod
    def reject_blank_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("不能仅包含空白字符")
        return value

    @model_validator(mode="after")
    def validate_parent(self):
        if self.parent_id == self.id:
            raise ValueError("地点不能以自身作为父地点")

        if self.kind in ("community", "unknown"):
            if self.parent_id is not None:
                raise ValueError("小区或未分类地点不能填写父地点")
        elif self.parent_id is None:
            raise ValueError("此类地点必须填写父地点")

        return self