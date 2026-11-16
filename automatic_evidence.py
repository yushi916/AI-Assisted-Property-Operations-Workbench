"""One read transaction, multiple independent evidence sources, no business writes."""

from datetime import UTC
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field, JsonValue
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from evidence_reader import PlanEvidenceData, read_plan_evidence
from evidence_rules import check_plan_evidence
from evidence_schemas import PlanEvidenceRead
from execution_schemas import ExecutionRead
from inspection_schemas import InspectionRead
from models import ExecutionRecord, InspectionRecord, RequestPlanLink, ServiceRequest, utc_now
from plan_link_schemas import RequestPlanLinkRead
from plan_schemas import PlanRead
from related_reports import (
    RelatedReportCandidate, RelatedReportPolicy, discover_related_reports, require_read_transaction,
)
from report_candidates import CandidatePolicy, discover_report_candidates
from schemas import LocationRead, ServiceRequestRead


Scope = Literal["same_location", "location_unknown", "other_location", "report_location_unknown"]
Relation = Literal["before", "same_time", "after", "unknown"]


class SourceQuery(BaseModel):
    source: str
    status: Literal["found", "empty", "not_queried", "unsupported", "failed"]
    scope: dict[str, JsonValue] = Field(default_factory=dict)
    record_refs: list[str] = Field(default_factory=list)
    count: int | None = None
    error_code: str | None = None
    note: str = ""


class ExecutionEvidence(BaseModel):
    record: ExecutionRead
    location_relation: Scope
    # Resident observation relative to claimed finish, as in the existing comparison.
    report_relation_to_completion: Relation


class InspectionEvidence(BaseModel):
    record: InspectionRead
    location_relation: Scope
    # Inspection observation relative to resident observation.
    observation_relation_to_report: Relation


class AutomaticPlanEvidence(BaseModel):
    plan: PlanRead
    automatic: bool
    reason_codes: list[str]
    manual_link_ids: list[UUID]
    has_revoked_manual_link: bool
    category_matches: bool
    plan_location_matches: bool | None
    executions: list[ExecutionEvidence]
    inspections: list[InspectionEvidence]
    plan_checks: PlanEvidenceRead
    warnings: list[str]


class AutomaticEvidenceRead(BaseModel):
    rule_version: Literal["automatic-evidence-v1"] = "automatic-evidence-v1"
    checked_at: AwareDatetime
    report: ServiceRequestRead
    related_reports: list[RelatedReportCandidate]
    plans: list[AutomaticPlanEvidence]
    manual_links: list[RequestPlanLinkRead]
    locations: list[LocationRead]
    queries: list[SourceQuery]
    warnings: list[str]


class AutomaticEvidenceReadError(RuntimeError):
    """No normal partial evidence returned; the runner can persist this failed trace."""
    def __init__(self, source, code, queries):
        super().__init__(f"自动取证未完成：{source}（{code}）")
        self.source, self.code = source, code
        seen = {query.source for query in queries}
        self.queries = [*queries, *[
            SourceQuery(source=name, status="not_queried", note="前序读取失败，本次未继续查询。")
            for name in ("report", "related_reports", "plans", "manual_links", "plan_records", "locations")
            if name not in seen
        ]]


class EvidenceRecordLimitExceeded(ValueError):
    pass


def _relation(left, right) -> Relation:
    if left is None or right is None:
        return "unknown"
    left, right = left.astimezone(UTC), right.astimezone(UTC)
    return "before" if left < right else "after" if left > right else "same_time"


def _scope(report_location, record_locations, locations) -> Scope:
    if report_location is None:
        return "report_location_unknown"
    known_points = [point for point in record_locations
                    if point in locations and locations[point].kind != "unknown"]
    if not known_points:
        return "location_unknown"
    return "same_location" if report_location in known_points else "other_location"


def read_automatic_evidence(
    db: Session, report_id: UUID, *, plan_policy: CandidatePolicy | None = None,
    report_policy: RelatedReportPolicy | None = None, max_child_records: int = 500,
) -> AutomaticEvidenceRead:
    """Caller owns the transaction. Every source must succeed before analysis may start."""
    require_read_transaction(db)
    if type(max_child_records) is not int or max_child_records < 1:
        raise ValueError("执行与检查记录上限必须为正整数")
    plan_policy, report_policy = plan_policy or CandidatePolicy(), report_policy or RelatedReportPolicy()
    checked_at = utc_now().astimezone(UTC)
    queries = [SourceQuery(source=source, status="unsupported", note=note) for source, note in (
        ("assets", "尚未接入设备台账或管网资料，不能确认共同设备及故障原因。"),
        ("work_orders", "尚未接入维修工单，不能说没有开展维修。"),
        ("notices", "尚未接入通知，不能说没有发布通知。"),
    )]

    def attempt(source, scope, read, references):
        try:
            value = read()
            refs = references(value)
        except Exception as error:
            trace = SourceQuery(source=source, status="failed", scope=scope,
                                error_code=type(error).__name__, note="读取未完成；未生成正常的部分取证结果。")
            raise AutomaticEvidenceReadError(source, type(error).__name__, [*queries, trace]) from error
        queries.append(SourceQuery(source=source, status="found" if refs else "empty",
                                   scope=scope, record_refs=refs, count=len(refs)))
        return value

    def root():
        row = db.get(ServiceRequest, report_id)
        if row is None:
            raise LookupError("报事不存在")
        return ServiceRequestRead.model_validate(row)

    report = attempt("report", {"report_id": str(report_id)}, root, lambda row: [f"report:{row.id}"])
    search_basis = (report.observed_at or report.received_at).astimezone(UTC)
    related_scope = {
        "window_start": (search_basis - report_policy.lookback).isoformat(),
        "window_end": (search_basis + report_policy.lookahead).isoformat(),
        "max_reports": report_policy.max_reports, "category": report.service_category,
        "report_location_id": report.location_id,
        "time_basis": "observed_at" if report.observed_at else "received_at_search_only",
        "policy_version": "related-reports-v1",
    }
    related = attempt("related_reports", related_scope,
                      lambda: discover_related_reports(db, report_id, report_policy),
                      lambda result: [f"report:{row.report.id}" for row in result.candidates])
    queries[-1].status = related.status
    queries[-1].scope.update(location_scope=related.location_scope, building_id=related.building_id)
    queries[-1].note = "；".join(related.warnings)
    if related.status == "not_queried":
        queries[-1].count = None

    plan_scope = {
        "window_start": (search_basis - plan_policy.lookback).isoformat(),
        "window_end": (search_basis + plan_policy.lookahead).isoformat(),
        "time_basis": "observed_at" if report.observed_at else "received_at_search_only",
        "report_location_id": report.location_id, "category": report.service_category,
        "max_candidates": plan_policy.max_candidates, "policy_version": "report-candidates-v1",
        "location_rule": "exact_point_or_unknown_report_fallback",
        "manual_supplements": "active_links_included_outside_automatic_window",
    }
    discovery = attempt("plans", plan_scope, lambda: discover_report_candidates(db, report_id, plan_policy),
                        lambda result: [f"plan:{row.plan.id}" for row in result.candidates])
    link_ids = [id_ for row in discovery.candidates for id_ in row.manual_link_ids]
    links = attempt("manual_links", {"report_id": str(report_id), "status": "active"}, lambda: [
        RequestPlanLinkRead.model_validate(row) for row in db.scalars(select(RequestPlanLink).where(
            RequestPlanLink.report_id == report_id, RequestPlanLink.id.in_(link_ids),
        ).order_by(RequestPlanLink.id))
    ], lambda rows: [f"link:{row.id}" for row in rows])

    report_location = report.location_id
    if report_location in related.locations and related.locations[report_location].kind == "unknown":
        report_location = None
    plans, child_count = [], 0
    for candidate in discovery.candidates:
        plan_id = candidate.plan.id
        def children():
            # Bound the complete child collection before fetching; no UI pagination or truncation.
            count = sum(db.scalar(select(func.count()).select_from(model).where(model.plan_id == plan_id))
                        for model in (ExecutionRecord, InspectionRecord))
            if child_count + count > max_child_records:
                raise EvidenceRecordLimitExceeded("执行与检查记录超过上限，未返回截断材料。")
            result = read_plan_evidence(db, plan_id)
            if result is None:
                raise LookupError("候选计划未能读全")
            return result
        evidence: PlanEvidenceData = attempt("plan_records", {
            "plan_ref": f"plan:{plan_id}", "record_scope": "all_saved_children_of_candidate_plan",
            "max_total_child_records": max_child_records,
        }, children, lambda result: [f"execution:{row.id}" for row in result.executions]
            + [f"inspection:{row.id}" for row in result.inspections])
        child_count += len(evidence.executions) + len(evidence.inspections)
        warnings = ["计划为检索候选；是否对应居民所述问题，需要结合工作要求和原文分析。"]
        if report_location is None or not candidate.category_matches or candidate.plan_location_matches is not True:
            warnings.append("当前计划类别或地点未能明确对应；历史记录按各自地点判断，不能直接跳过。")
        executions = []
        for row in sorted(evidence.executions, key=lambda row: str(row.id)):
            scope = _scope(report_location, row.location_ids, related.locations)
            can_compare = candidate.category_matches and scope == "same_location" and row.completion_claim == "completed"
            executions.append(ExecutionEvidence(
                record=row, location_relation=scope,
                report_relation_to_completion=_relation(report.observed_at, row.claimed_finished_at) if can_compare else "unknown",
            ))
        inspections = []
        for row in sorted(evidence.inspections, key=lambda row: str(row.id)):
            scope = _scope(report_location, [] if row.location_id is None else [row.location_id], related.locations)
            inspections.append(InspectionEvidence(
                record=row, location_relation=scope,
                observation_relation_to_report=_relation(row.observed_at, report.observed_at)
                    if candidate.category_matches and scope == "same_location" else "unknown",
            ))
        if not any(row.location_relation == "same_location" for row in executions):
            warnings.append("本次未查到本报事地点的明确执行记录，不代表没有开展工作。")
        if not any(row.location_relation == "same_location" for row in inspections):
            warnings.append("本次未查到本报事地点的明确现场检查记录，其他地点正常不能代替本地点确认。")
        if any(row.location_relation == "location_unknown" for row in [*executions, *inspections]):
            warnings.append("地点未注明的记录已保留，其适用地点仍需核实。")
        plans.append(AutomaticPlanEvidence(
            plan=evidence.plan, automatic=candidate.automatic, reason_codes=list(candidate.reason_codes),
            manual_link_ids=list(candidate.manual_link_ids), has_revoked_manual_link=candidate.has_revoked_manual_link,
            category_matches=candidate.category_matches,
            plan_location_matches=candidate.plan_location_matches if report_location is not None else None,
            executions=executions, inspections=inspections,
            plan_checks=check_plan_evidence(evidence.plan, evidence.executions, evidence.inspections, checked_at),
            warnings=warnings,
        ))
    if not plans:
        queries.append(SourceQuery(source="plan_records", status="not_queried",
                                   note="没有候选计划，未读取计划所属执行和检查；其他报事查询仍独立完成。"))

    # Keep the exact ledger paths and versions used for matching and reading records, including ancestors.
    used_locations = {report.location_id}
    used_locations.update(row.report.location_id for row in related.candidates)
    for plan in plans:
        used_locations.update(plan.plan.location_ids)
        for row in plan.executions:
            used_locations.update(row.record.location_ids)
        used_locations.update(row.record.location_id for row in plan.inspections)
    for point in list(used_locations):
        while point is not None:
            if point not in related.locations:
                error = SourceQuery(source="locations", status="failed", error_code="MissingLocation")
                raise AutomaticEvidenceReadError("locations", "MissingLocation", [*queries, error])
            point = related.locations[point].parent_id
            used_locations.add(point)
    locations = [related.locations[point] for point in sorted(used_locations - {None})]
    queries.append(SourceQuery(source="locations", status="found" if locations else "empty", count=len(locations),
                               record_refs=[f"location:{row.id}" for row in locations],
                               note="保存所用地点及上级台账的名称、层级、路径和版本。"))
    return AutomaticEvidenceRead(
        checked_at=checked_at, report=report, related_reports=list(related.candidates), plans=plans,
        manual_links=links, locations=locations, queries=queries,
        warnings=[*related.warnings, *discovery.warnings,
                  "没有查到记录不等于没有开展工作；候选不等于人工确认关联或已确认原因。",
                  "本次只读取现有数据，不建立关联、不结案、不发布通知。"],
    )
