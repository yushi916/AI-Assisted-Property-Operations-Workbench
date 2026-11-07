from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field

from execution_schemas import ExecutionRead
from inspection_schemas import InspectionRead
from plan_link_schemas import RequestPlanLinkRead
from plan_schemas import PlanRead
from schemas import ServiceRequestRead


class ReportExecutionComparison(BaseModel):
    execution_id: UUID
    relation: Literal["before", "same_time", "after", "unknown"]
    message: str


class LinkedPlanEvidence(BaseModel):
    link_id: UUID
    plan: PlanRead
    scope_matches: bool
    executions: list[ExecutionRead]
    inspections: list[InspectionRead]
    report_comparisons: list[ReportExecutionComparison]
    excluded_execution_count: int = Field(ge=0)
    excluded_inspection_count: int = Field(ge=0)
    warnings: list[str]


class ReportEvidenceRead(BaseModel):
    rule_version: Literal["report-evidence-v1"] = "report-evidence-v1"
    checked_at: AwareDatetime
    report: ServiceRequestRead
    links: list[RequestPlanLinkRead]
    linked_plans: list[LinkedPlanEvidence]