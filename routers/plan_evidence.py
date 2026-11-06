from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from database import get_db
from evidence_reader import read_plan_evidence
from evidence_rules import check_plan_evidence
from evidence_schemas import PlanEvidenceRead
from models import utc_now


router = APIRouter(prefix="/api/plans", tags=["plan-evidence"])


@router.get(
    "/{plan_id}/evidence-check",
    response_model=PlanEvidenceRead,
    responses={
        404: {"description": "计划不存在"},
        503: {"description": "证据读取未完成，不能作为空记录处理"},
    },
)
def get_plan_evidence(plan_id: UUID, response: Response, db: Session = Depends(get_db)):
    try:
        with db.begin():
            evidence = read_plan_evidence(db, plan_id)
            if evidence is None:
                raise HTTPException(status_code=404, detail="计划不存在")
            result = check_plan_evidence(
                evidence.plan, evidence.executions, evidence.inspections,
                utc_now(),
            )
    except OperationalError as error:
        raise HTTPException(status_code=503, detail="证据读取未完成，请稍后重试。") from error

    response.headers["Cache-Control"] = "no-store"
    return result