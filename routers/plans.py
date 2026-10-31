from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from database import get_db
from models import PlanInstance, PlanLocation
from plan_schemas import PlanCreate, PlanRead
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session, selectinload


router = APIRouter(prefix="/api/plans", tags=["plans"])
SERVER_FIELDS = {"id", "created_at", "version"}


def read_plan(plan):
    values = {
        name: getattr(plan, name)
        for name in PlanCreate.model_fields
        if name != "location_ids"
    }
    return PlanRead.model_validate({
        **values,
        "location_ids": sorted(point.location_id for point in plan.locations),
        "id": plan.id,
        "created_at": plan.created_at,
        "version": plan.version,
    })


@router.post(
    "",
    response_model=PlanRead,
    status_code=201,
    responses={
        200: {"model": PlanRead, "description": "相同提交重试，返回原计划"},
        409: {"description": "同一提交编号用于不同计划内容"},
        422: {"description": "输入不合法或覆盖地点不存在"},
        503: {"description": "数据库暂时繁忙，可重试本次提交"},
    },
)
def create_plan(data: PlanCreate, response: Response, db: Session = Depends(get_db)):
    try:
        with db.begin():
            statement = (
                insert(PlanInstance)
                .values(**data.model_dump(exclude={"location_ids"}))
                .on_conflict_do_nothing(index_elements=["request_id"])
                .returning(PlanInstance.id)
            )
            inserted_id = db.scalar(statement)
            plan = db.execute(
                select(PlanInstance).where(PlanInstance.request_id == data.request_id)
            ).scalar_one()
            if inserted_id is not None:
                db.add_all([
                    PlanLocation(plan_id=plan.id, location_id=location_id)
                    for location_id in data.location_ids
                ])
                db.flush()

            result = read_plan(plan)
            incoming = data.model_dump()
            incoming["location_ids"] = sorted(data.location_ids)
            if inserted_id is None and result.model_dump(exclude=SERVER_FIELDS) != incoming:
                raise HTTPException(
                    status_code=409,
                    detail="同一提交编号已用于不同内容；原计划未修改。",
                )
    except IntegrityError as error:
        if error.orig.sqlite_errorname == "SQLITE_CONSTRAINT_FOREIGNKEY":
            raise HTTPException(
                status_code=422, detail="覆盖地点不存在，请选择地点台账中的地点。"
            ) from error
        raise
    except OperationalError as error:
        if error.orig.sqlite_errorname.startswith(("SQLITE_BUSY", "SQLITE_LOCKED")):
            raise HTTPException(
                status_code=503, detail="数据库暂时繁忙，请稍后重试本次提交。"
            ) from error
        raise

    response.status_code = 201 if inserted_id is not None else 200
    return result


@router.get("", response_model=list[PlanRead])
def list_plans(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    statement = (
        select(PlanInstance)
        .options(selectinload(PlanInstance.locations))
        .order_by(PlanInstance.created_at.desc(), PlanInstance.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return [read_plan(plan) for plan in db.scalars(statement)]


@router.get(
    "/{plan_id}",
    response_model=PlanRead,
    responses={404: {"description": "计划不存在"}},
)
def get_plan(plan_id: UUID, db: Session = Depends(get_db)):
    plan = db.get(PlanInstance, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="计划不存在")
    return read_plan(plan)