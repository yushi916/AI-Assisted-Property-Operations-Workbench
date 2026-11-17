from datetime import UTC
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from database import get_db
from evidence_reader import read_plan_evidence
from models import RequestPlanLink, ServiceRequest, utc_now
from plan_link_schemas import RequestPlanLinkRead
from report_evidence_schemas import LinkedPlanEvidence, ReportEvidenceRead, ReportExecutionComparison
from schemas import ServiceRequestRead


router = APIRouter(prefix="/api/service-requests", tags=["report-evidence"])


def compare_report(report, execution, scope_matches):
    if (not scope_matches or report.location_id not in execution.location_ids
            or report.observed_at is None or execution.claimed_finished_at is None):
        relation = "unknown"
        message = "地点或观察/填报的完成时间尚不明确，不能判断本条报事与该次完成填报的先后关系。"
    else:
        observed = report.observed_at.astimezone(UTC)
        finished = execution.claimed_finished_at.astimezone(UTC)
        relation = "before" if observed < finished else "after" if observed > finished else "same_time"
        message = {
            "before": "居民看到问题的时间早于执行人员填报的完成时间，不能据此判断后续工作没有完成。",
            "same_time": "居民观察时间与执行人员填报的完成时间相同，仍需核实当时现场情况。",
            "after": "居民看到问题的时间晚于执行人员填报的完成时间。可能是后来再次出现，也需核实此前处理效果，不能直接认定未完成或虚报。",
        }[relation]
    return ReportExecutionComparison(execution_id=execution.id, relation=relation, message=message)


def read_report_evidence(report_id: UUID, db: Session) -> ReportEvidenceRead:
    """取证由后端绑定报事，在同一个读取事务中完成。"""
    with db.begin():
        item = db.get(ServiceRequest, report_id)
        if item is None:
            raise HTTPException(status_code=404, detail="报事不存在")
        report = ServiceRequestRead.model_validate(item)
        links = [RequestPlanLinkRead.model_validate(link) for link in db.scalars(
            select(RequestPlanLink).where(RequestPlanLink.report_id == report_id)
            .order_by(RequestPlanLink.linked_at.desc(), RequestPlanLink.id.desc())
        )]
        plans = []
        for link in links:
            if link.status != "active":
                continue
            evidence = read_plan_evidence(db, link.plan_id)
            if evidence is None:
                raise HTTPException(status_code=503, detail="关联计划未能读全，请核对数据。")
            matches = (report.service_category == evidence.plan.service_category
                       and report.location_id is not None
                       and report.location_id in evidence.plan.location_ids)
            warnings = []
            if not matches:
                warnings.append("这项计划的服务类别或地点与本条报事未能对应，记录保留供参考，暂不比较时间先后。")
            executions = sorted((x for x in evidence.executions
                if not matches or not x.location_ids or report.location_id in x.location_ids),
                key=lambda x: str(x.id))
            inspections = sorted((x for x in evidence.inspections
                if not matches or x.location_id is None or x.location_id == report.location_id),
                key=lambda x: str(x.id))
            if report.observed_at is None:
                warnings.append("报事观察时间待核实，不能使用接收时间代替。")
            if any(not x.location_ids for x in executions) or any(x.location_id is None for x in inspections):
                warnings.append("地点未知的记录已保留，不能当作本地点已执行或已现场检查的证明。")
            if matches and not any(report.location_id in x.location_ids for x in executions):
                warnings.append("本次读取未查到该地点的明确执行记录，请对照计划要求决定是否补查。")
            if matches and not any(x.location_id == report.location_id for x in inspections):
                warnings.append("本次读取未查到该地点的明确现场检查记录，请对照计划要求决定是否补查。")
            plans.append(LinkedPlanEvidence(
                link_id=link.id, plan=evidence.plan, scope_matches=matches,
                executions=executions, inspections=inspections,
                report_comparisons=[compare_report(report, x, matches)
                                    for x in executions if x.completion_claim == "completed"],
                excluded_execution_count=len(evidence.executions) - len(executions),
                excluded_inspection_count=len(evidence.inspections) - len(inspections),
                warnings=warnings,
            ))
        return ReportEvidenceRead(
            checked_at=utc_now().astimezone(UTC), report=report, links=links, linked_plans=plans,
        )


@router.get(
    "/{report_id}/evidence-check", response_model=ReportEvidenceRead,
    responses={404: {"description": "报事不存在"}, 503: {"description": "证据读取未完成"}},
)
def get_report_evidence(report_id: UUID, response: Response, db: Session = Depends(get_db)):
    try:
        result = read_report_evidence(report_id, db)
    except OperationalError as error:
        raise HTTPException(status_code=503, detail="联合证据读取未完成，请稍后重试。") from error
    response.headers["Cache-Control"] = "no-store"
    return result
