from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field


class EvidenceRef(BaseModel):
    kind: Literal["plan", "execution", "inspection"]
    id: UUID
    version: int = Field(ge=1)


class EvidenceFinding(BaseModel):
    code: Literal[
        "missing_execution", "missing_inspection", "completion_not_claimed", "unknown_execution_scope",
        "unknown_completion_time", "unknown_inspection_location", "unknown_inspection_time",
        "issue_observed", "completion_after_deadline", "completion_before_plan_start",
        "execution_outside_scope", "inspection_outside_scope",
    ]
    location_id: str | None = None
    message: str
    evidence: list[EvidenceRef]


class LocationCoverage(BaseModel):
    location_id: str
    execution_ids: list[UUID]
    inspection_ids: list[UUID]


class ObservationComparison(BaseModel):
    location_id: str
    execution_id: UUID
    inspection_id: UUID
    finding: Literal["no_issue_observed", "issue_observed", "unable_to_verify"]
    relation: Literal["before", "same_time", "after", "unknown"]


class PlanEvidenceRead(BaseModel):
    rule_version: Literal["plan-evidence-v1"] = "plan-evidence-v1"
    plan_id: UUID
    plan_version: int = Field(ge=1)
    checked_at: AwareDatetime
    due: bool
    coverage: list[LocationCoverage]
    findings: list[EvidenceFinding]
    comparisons: list[ObservationComparison]
    evidence: list[EvidenceRef]