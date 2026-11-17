from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from agent_evidence import AgentEvidence, AgentSuggestion, AutomaticSuggestion
from agent_runner import EvidenceModel, FailureStage, run_agent
from database import get_db
from gemini_agent import GeminiEvidenceModel
from models import AgentRun, ServiceRequest, utc_now
from automatic_agent_evidence import read_automatic_agent_evidence


router = APIRouter(prefix="/api/service-requests", tags=["evidence-agent"])


class AgentRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID


class AgentRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    report_id: UUID
    status: Literal["running", "pending_review", "failed"]
    model_name: str
    created_at: AwareDatetime
    finished_at: AwareDatetime | None
    evidence: AgentEvidence | None
    suggestion: AutomaticSuggestion | AgentSuggestion | None
    failure_stage: FailureStage | None
    error_code: str | None


def get_agent_model() -> EvidenceModel:
    return GeminiEvidenceModel()


def require_report(db: Session, report_id: UUID):
    if db.get(ServiceRequest, report_id) is None:
        raise HTTPException(status_code=404, detail="报事不存在")


@router.post("/{report_id}/agent-runs", response_model=AgentRunRead, status_code=201)
def create_agent_run(
    report_id: UUID, body: AgentRunCreate, response: Response,
    db: Session = Depends(get_db), model: EvidenceModel = Depends(get_agent_model),
):
    response.headers["Cache-Control"] = "no-store"
    try:
        with db.begin():
            require_report(db, report_id)
        with db.begin():
            reserved = db.execute(insert(AgentRun).values(
                id=body.request_id, report_id=report_id, status="running",
                model_name=model.model_name, created_at=utc_now(),
            ).on_conflict_do_nothing(index_elements=[AgentRun.id])).rowcount == 1
            row = db.get(AgentRun, body.request_id)
            if row.report_id != report_id:
                raise HTTPException(status_code=409, detail="提交编号已用于另一条报事")
            if not reserved:
                response.status_code = 200
                return AgentRunRead.model_validate(row)

        # 在模型调用期间不持有数据库事务。取证工具自行打开并关闭读取事务。
        result = run_agent(model, lambda: read_automatic_agent_evidence(db, report_id))
        with db.begin():
            row.status = result.status
            row.finished_at = utc_now()
            row.evidence = result.evidence.model_dump(mode="json") if result.evidence is not None else None
            row.suggestion = result.suggestion.model_dump(mode="json") if result.suggestion is not None else None
            row.failure_stage = result.failure_stage
            row.error_code = result.error_code
            db.flush()
            return AgentRunRead.model_validate(row)
    except OperationalError as error:
        raise HTTPException(status_code=503, detail="运行记录暂不可用，请用同一提交编号重查") from error


@router.get("/{report_id}/agent-runs", response_model=list[AgentRunRead])
def list_agent_runs(report_id: UUID, response: Response, db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    try:
        with db.begin():
            require_report(db, report_id)
            rows = db.scalars(select(AgentRun).where(AgentRun.report_id == report_id)
                              .order_by(AgentRun.created_at.desc(), AgentRun.id.desc()).limit(10))
            return [AgentRunRead.model_validate(row) for row in rows]
    except OperationalError as error:
        raise HTTPException(status_code=503, detail="运行记录暂不可用，请稍后重查") from error
