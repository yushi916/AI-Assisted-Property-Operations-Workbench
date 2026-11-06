from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from execution_schemas import ExecutionRead
from inspection_schemas import InspectionRead
from models import ExecutionRecord, InspectionRecord, PlanInstance
from plan_schemas import PlanRead


@dataclass(frozen=True)
class PlanEvidenceData:
    plan: PlanRead
    executions: tuple[ExecutionRead, ...]
    inspections: tuple[InspectionRead, ...]


def _read_with_locations(record, schema):
    values = {
        name: getattr(record, name)
        for name in schema.model_fields
        if name != "location_ids"
    }
    values["location_ids"] = sorted(point.location_id for point in record.locations)
    return schema.model_validate(values)


def read_plan_evidence(db: Session, plan_id: UUID) -> PlanEvidenceData | None:
    """在调用方的读取事务内取证；不提交、不回滚，不处理 HTTP。"""
    if not db.in_transaction():
        raise ValueError("证据读取必须在调用方事务中执行")

    plan = db.scalar(select(PlanInstance).where(
        PlanInstance.id == plan_id
    ).options(selectinload(PlanInstance.locations)))
    if plan is None:
        return None

    executions = db.scalars(select(ExecutionRecord).where(
        ExecutionRecord.plan_id == plan_id
    ).options(selectinload(ExecutionRecord.locations))).all()
    inspections = db.scalars(select(InspectionRecord).where(
        InspectionRecord.plan_id == plan_id
    )).all()

    return PlanEvidenceData(
        plan=_read_with_locations(plan, PlanRead),
        executions=tuple(_read_with_locations(item, ExecutionRead) for item in executions),
        inspections=tuple(InspectionRead.model_validate(item) for item in inspections),
    )