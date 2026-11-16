from collections.abc import Sequence
from datetime import UTC, datetime

from evidence_schemas import (
    EvidenceFinding, EvidenceRef, LocationCoverage,
    ObservationComparison, PlanEvidenceRead,
)
from execution_schemas import ExecutionRead
from inspection_schemas import InspectionRead
from plan_schemas import PlanRead


MESSAGES = {
    "missing_execution": "本次核对未查到该地点的执行记录。请核实是否已开展工作、是否遗漏填报，再决定是否需要现场核查。",
    "missing_inspection": "截至核对时刻，未查到该地点的明确现场检查记录；请先核实是否已检查、是否遗漏填报，再对照计划要求决定是否需要现场检查。",
    "completion_not_claimed": "执行人员尚未填报全部完成，需结合其他记录核对。",
    "unknown_execution_scope": "执行记录的具体地点待核实，不能当作覆盖全部计划。",
    "unknown_completion_time": "执行人员填报已完成，但填报的完成时间待核实。",
    "unknown_inspection_location": "现场检查地点待核实，不能作为具体地点的覆盖证据。",
    "unknown_inspection_time": "现场检查观察时间待核实，不能用录入时间代替。",
    "issue_observed": "现场检查填报发现异常；现场情况与原因需要核实。",
    "completion_after_deadline": "填报的完成时间晚于计划截止；需要核对情况。",
    "completion_before_plan_start": "填报的完成时间早于计划开始；需要核对关联和时间。",
    "execution_outside_scope": "执行地点不在当前计划明确范围内；需要核对范围变化或关联。",
    "inspection_outside_scope": "现场检查地点不在当前计划明确范围内；需要核对范围变化或关联。",
}


def check_plan_evidence(
    plan: PlanRead,
    executions: Sequence[ExecutionRead],
    inspections: Sequence[InspectionRead],
    checked_at: datetime,
) -> PlanEvidenceRead:
    """只核对传入的完整计划证据集合，不写库，不作履约批准。"""
    if checked_at.utcoffset() is None:
        raise ValueError("核对时间必须带时区")
    for label, records in (("执行", executions), ("现场检查", inspections)):
        if len({item.id for item in records}) != len(records):
            raise ValueError("重复" + label + "记录")
        if any(item.plan_id != plan.id for item in records):
            raise ValueError("证据不属于当前计划")

    executions = sorted(executions, key=lambda item: str(item.id))
    inspections = sorted(inspections, key=lambda item: str(item.id))
    points = set(plan.location_ids)
    now = checked_at.astimezone(UTC)
    start = plan.planned_start_at.astimezone(UTC)
    end = plan.planned_end_at.astimezone(UTC)
    due = now >= end

    def ref(kind, item):
        return EvidenceRef(kind=kind, id=item.id, version=item.version)

    plan_ref = ref("plan", plan)
    findings = []
    coverage = []
    comparisons = []

    def add(code, source, location_id=None):
        findings.append(EvidenceFinding(
            code=code, location_id=location_id,
            message=MESSAGES[code], evidence=[source],
        ))

    for item in executions:
        source = ref("execution", item)
        if not item.location_ids:
            add("unknown_execution_scope", source)
        for point in sorted(set(item.location_ids) - points):
            add("execution_outside_scope", source, point)
        if item.completion_claim == "completed":
            if item.claimed_finished_at is None:
                add("unknown_completion_time", source)
            else:
                finished = item.claimed_finished_at.astimezone(UTC)
                if finished > end:
                    add("completion_after_deadline", source)
                if finished < start:
                    add("completion_before_plan_start", source)
        else:
            add("completion_not_claimed", source)

    for item in inspections:
        source = ref("inspection", item)
        if item.location_id is None:
            add("unknown_inspection_location", source)
        elif item.location_id not in points:
            add("inspection_outside_scope", source, item.location_id)
        if item.observed_at is None:
            add("unknown_inspection_time", source, item.location_id)
        if item.finding == "issue_observed":
            add("issue_observed", source, item.location_id)

    for point in sorted(points):
        point_executions = [item for item in executions if point in item.location_ids]
        point_inspections = [item for item in inspections if item.location_id == point]
        coverage.append(LocationCoverage(
            location_id=point,
            execution_ids=[item.id for item in point_executions],
            inspection_ids=[item.id for item in point_inspections],
        ))
        if due and not point_executions:
            add("missing_execution", plan_ref, point)
        if due and not point_inspections:
            add("missing_inspection", plan_ref, point)

        for execution in point_executions:
            if execution.completion_claim != "completed":
                continue
            for inspection in point_inspections:
                observed = inspection.observed_at
                finished = execution.claimed_finished_at
                if observed is None or finished is None:
                    relation = "unknown"
                else:
                    observed = observed.astimezone(UTC)
                    finished = finished.astimezone(UTC)
                    relation = "before" if observed < finished else "after" if observed > finished else "same_time"
                comparisons.append(ObservationComparison(
                    location_id=point, execution_id=execution.id,
                    inspection_id=inspection.id, finding=inspection.finding,
                    relation=relation,
                ))

    return PlanEvidenceRead(
        plan_id=plan.id, plan_version=plan.version, checked_at=now, due=due,
        coverage=coverage, findings=findings, comparisons=comparisons,
        evidence=[plan_ref]
        + [ref("execution", item) for item in executions]
        + [ref("inspection", item) for item in inspections],
    )
