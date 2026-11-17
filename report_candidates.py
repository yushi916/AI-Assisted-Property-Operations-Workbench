"""Read-only discovery. A candidate is a search result, not a confirmed relationship."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, exists, or_, select, true
from sqlalchemy.orm import Session, selectinload

from models import (
    ExecutionLocation, ExecutionRecord, InspectionRecord, PlanInstance,
    PlanLocation, RequestPlanLink, ServiceRequest,
)
from plan_schemas import PlanRead
from schemas import ServiceRequestRead


@dataclass(frozen=True)
class CandidatePolicy:
    # Initial demo search bounds, not a service deadline or proof of completeness.
    lookback: timedelta = timedelta(days=7)
    lookahead: timedelta = timedelta(days=1)
    max_candidates: int = 50

    def __post_init__(self):
        if self.lookback < timedelta(0) or self.lookahead < timedelta(0):
            raise ValueError("检索时间范围不能为负")
        if type(self.max_candidates) is not int or self.max_candidates < 1:
            raise ValueError("候选数量上限必须为正整数")


@dataclass(frozen=True)
class CandidatePlan:
    plan: PlanRead
    automatic: bool
    reason_codes: tuple[str, ...]
    manual_link_ids: tuple[UUID, ...]
    plan_location_matches: bool | None
    category_matches: bool
    has_revoked_manual_link: bool


@dataclass(frozen=True)
class CandidateDiscovery:
    report: ServiceRequestRead
    window_start: datetime
    window_end: datetime
    time_basis: str
    max_candidates: int
    candidates: tuple[CandidatePlan, ...]
    warnings: tuple[str, ...]
    policy_version: str = "report-candidates-v1"


class CandidateLimitExceeded(ValueError):
    pass


def discover_report_candidates(
    db: Session, report_id: UUID, policy: CandidatePolicy | None = None,
) -> CandidateDiscovery:
    """Caller owns the read transaction, so later evidence reads can share its snapshot."""
    if not db.in_transaction():
        raise ValueError("候选检索必须在调用方的读取事务内执行")
    if db.new or db.dirty or db.deleted:
        raise ValueError("候选检索需使用没有待写入变更的读取事务")
    policy = policy or CandidatePolicy()
    item = db.get(ServiceRequest, report_id)
    if item is None:
        raise LookupError("报事不存在")
    report = ServiceRequestRead.model_validate(item)
    basis = report.observed_at or report.received_at
    start = basis.astimezone(UTC) - policy.lookback
    end = basis.astimezone(UTC) + policy.lookahead
    warnings = ["候选只表示值得进一步查看，不代表已确认相关或问题已解决。"]
    if report.observed_at is None:
        warnings.append("观察时间未注明：接收时间仅作为检索参考，不能用于判断事情发生的先后。")
    if report.location_id is None:
        warnings.append("报事地点未注明：仅按类别和时间查找候选，尚未匹配具体地点。")

    location = report.location_id
    plan_place = true() if location is None else exists().where(
        PlanLocation.plan_id == PlanInstance.id, PlanLocation.location_id == location,
    )
    execution_place = true() if location is None else exists().where(
        ExecutionLocation.execution_id == ExecutionRecord.id,
        ExecutionLocation.location_id == location,
    )
    inspection_place = true() if location is None else InspectionRecord.location_id == location
    plan_period = and_(plan_place, PlanInstance.planned_start_at <= end, PlanInstance.planned_end_at >= start)
    execution_recorded = and_(
        ExecutionRecord.claimed_started_at.is_(None),
        ExecutionRecord.claimed_finished_at.is_(None),
        ExecutionRecord.recorded_at.between(start, end),
    )
    execution_time = or_(
        ExecutionRecord.claimed_started_at.between(start, end),
        ExecutionRecord.claimed_finished_at.between(start, end),
        and_(ExecutionRecord.claimed_started_at <= start, ExecutionRecord.claimed_finished_at >= end),
        execution_recorded,
    )
    execution_signal = exists().where(
        ExecutionRecord.plan_id == PlanInstance.id, execution_place, execution_time,
    )
    inspection_recorded = and_(InspectionRecord.observed_at.is_(None), InspectionRecord.recorded_at.between(start, end))
    inspection_signal = exists().where(
        InspectionRecord.plan_id == PlanInstance.id, inspection_place,
        or_(InspectionRecord.observed_at.between(start, end), inspection_recorded),
    )
    recorded_signal = or_(
        exists().where(ExecutionRecord.plan_id == PlanInstance.id, execution_place, execution_recorded),
        exists().where(InspectionRecord.plan_id == PlanInstance.id, inspection_place, inspection_recorded),
    )
    automatic = and_(
        PlanInstance.service_category == report.service_category,
        or_(plan_period, execution_signal, inspection_signal),
    )
    active_links = db.execute(select(RequestPlanLink.plan_id, RequestPlanLink.id).where(
        RequestPlanLink.report_id == report_id, RequestPlanLink.status == "active",
    ).order_by(RequestPlanLink.plan_id, RequestPlanLink.id).limit(policy.max_candidates + 1)).all()
    if len(active_links) > policy.max_candidates:
        raise CandidateLimitExceeded("人工补充计划已超过候选上限，请收窄范围；没有返回不完整候选。")
    manual = {}
    for plan_id, link_id in active_links:
        manual.setdefault(plan_id, []).append(link_id)
    revoked = exists().where(
        RequestPlanLink.report_id == report_id, RequestPlanLink.plan_id == PlanInstance.id,
        RequestPlanLink.status == "revoked",
    )
    rows = db.execute(select(
        PlanInstance, automatic, plan_period, execution_signal, inspection_signal, recorded_signal, revoked,
    ).where(or_(automatic, PlanInstance.id.in_(manual))).options(
        selectinload(PlanInstance.locations),
    ).order_by(PlanInstance.planned_start_at.desc(), PlanInstance.id).limit(policy.max_candidates + 1)).all()
    if len(rows) > policy.max_candidates:
        raise CandidateLimitExceeded("候选超过检索上限，请收窄范围；没有静默截取前几条。")
    candidates = []
    for plan, auto, by_plan, by_execution, by_inspection, by_recorded, was_revoked in rows:
        ids = sorted(point.location_id for point in plan.locations)
        reasons = []
        if auto:
            for applies, reason in ((by_plan, "plan_period"), (by_execution, "execution_record"),
                                    (by_inspection, "inspection_record"), (by_recorded, "recorded_time_search_only")):
                if applies:
                    reasons.append(reason)
            if location is None:
                reasons.append("report_location_unknown")
        if plan.id in manual:
            reasons.append("manual_supplement")
        if was_revoked:
            reasons.append("previous_manual_link_revoked")
        values = {name: getattr(plan, name) for name in PlanRead.model_fields if name != "location_ids"}
        candidates.append(CandidatePlan(
            plan=PlanRead.model_validate({**values, "location_ids": ids}), automatic=bool(auto),
            reason_codes=tuple(reasons), manual_link_ids=tuple(manual.get(plan.id, ())),
            plan_location_matches=None if location is None else location in ids,
            category_matches=plan.service_category == report.service_category,
            has_revoked_manual_link=bool(was_revoked),
        ))
    if any("recorded_time_search_only" in candidate.reason_codes for candidate in candidates):
        warnings.append("部分记录缺少工作或观察时间，录入时间仅用于发现线索，不能代替实际发生时间。")
    return CandidateDiscovery(
        report=report, window_start=start, window_end=end,
        time_basis="observed_at" if report.observed_at is not None else "received_at_search_only",
        max_candidates=policy.max_candidates, candidates=tuple(candidates), warnings=tuple(warnings),
    )
