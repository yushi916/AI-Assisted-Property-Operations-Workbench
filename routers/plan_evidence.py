from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, selectinload

from database import get_db
from evidence_rules import check_plan_evidence
from evidence_schemas import PlanEvidenceRead
from execution_schemas import ExecutionRead
from inspection_schemas import InspectionRead
from models import ExecutionRecord, InspectionRecord, PlanInstance, utc_now
from plan_schemas import PlanRead


router = APIRouter(prefix="/api/plans", tags=["plan-evidence"])


def read_with_locations(record, schema):
    values = {
        name: getattr(record, name)
        for name in schema.model_fields
        if name != "location_ids"
    }
    values["location_ids"] = sorted(point.location_id for point in record.locations)
    return schema.model_validate(values)


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
            plan = db.scalar(select(PlanInstance).where(
                PlanInstance.id == plan_id
            ).options(selectinload(PlanInstance.locations)))
            if plan is None:
                raise HTTPException(status_code=404, detail="计划不存在")
            executions = db.scalars(select(ExecutionRecord).where(
                ExecutionRecord.plan_id == plan_id
            ).options(selectinload(ExecutionRecord.locations))).all()
            inspections = db.scalars(select(InspectionRecord).where(
                InspectionRecord.plan_id == plan_id
            )).all()
            result = check_plan_evidence(
                read_with_locations(plan, PlanRead),
                [read_with_locations(item, ExecutionRead) for item in executions],
                [InspectionRead.model_validate(item) for item in inspections],
                utc_now(),
            )
    except OperationalError as error:
        raise HTTPException(status_code=503, detail="证据读取未完成，请稍后重试。") from error

    response.headers["Cache-Control"] = "no-store"
    return result