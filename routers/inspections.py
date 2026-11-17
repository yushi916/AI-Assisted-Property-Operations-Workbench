from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import exists, literal, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from database import get_db
from inspection_schemas import InspectionCreate, InspectionRead
from models import InspectionRecord, PlanInstance, PlanLocation, utc_now


router = APIRouter(prefix="/api/inspections", tags=["inspections"])
SERVER_FIELDS = {"id", "recorded_at", "version"}


@router.post(
    "",
    response_model=InspectionRead,
    status_code=201,
    responses={
        200: {"model": InspectionRead, "description": "相同提交重试，返回原巡查记录"},
        409: {"description": "同一提交编号用于不同巡查内容"},
        422: {"description": "输入、计划、明确地点范围或观察时间不合法"},
        503: {"description": "数据库暂时繁忙，可重试本次提交"},
    },
)
def create_inspection(data: InspectionCreate, response: Response, db: Session = Depends(get_db)):
    try:
        with db.begin():
            values = {
                **data.model_dump(),
                "id": uuid4(), "recorded_at": utc_now(), "version": 1,
            }
            columns = InspectionRecord.__table__.c
            candidate = select(*[
                literal(value, type_=columns[name].type)
                for name, value in values.items()
            ]).where(~exists(
                select(InspectionRecord.id).where(InspectionRecord.request_id == data.request_id)
            ))
            # 编号已存在时不再插入，也不重新套用当前时钟和计划范围。
            statement = (
                insert(InspectionRecord)
                .from_select(list(values), candidate, include_defaults=False)
                .on_conflict_do_nothing(index_elements=["request_id"])
                .returning(InspectionRecord.id)
            )
            inserted_id = db.scalar(statement)
            record = db.execute(
                select(InspectionRecord).where(InspectionRecord.request_id == data.request_id)
            ).scalar_one()

            if inserted_id is not None and data.location_id is not None:
                covered = db.scalar(select(PlanLocation.location_id).where(
                    PlanLocation.plan_id == data.plan_id,
                    PlanLocation.location_id == data.location_id,
                ))
                if covered is None:
                    raise HTTPException(
                        status_code=422,
                        detail="巡查地点不在本次计划明确覆盖范围内：" + data.location_id,
                    )

            result = InspectionRead.model_validate(record)
            if inserted_id is None and result.model_dump(exclude=SERVER_FIELDS) != data.model_dump():
                raise HTTPException(status_code=409, detail="同一提交编号已用于不同内容；原巡查记录未修改。")
    except IntegrityError as error:
        if error.orig.sqlite_errorname == "SQLITE_CONSTRAINT_FOREIGNKEY":
            raise HTTPException(status_code=422, detail="关联的计划或地点不存在，请核对。") from error
        if "ck_inspection_observed_recorded" in str(error.orig):
            raise HTTPException(status_code=422, detail="已知观察时间不能晚于本次录入时间。") from error
        raise
    except OperationalError as error:
        if error.orig.sqlite_errorname.startswith(("SQLITE_BUSY", "SQLITE_LOCKED")):
            raise HTTPException(status_code=503, detail="数据库暂时繁忙，请稍后重试本次提交。") from error
        raise

    response.status_code = 201 if inserted_id is not None else 200
    return result


@router.get(
    "",
    response_model=list[InspectionRead],
    responses={404: {"description": "筛选的计划不存在"}},
)
def list_inspections(
    plan_id: UUID | None = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    if plan_id is not None and db.get(PlanInstance, plan_id) is None:
        raise HTTPException(status_code=404, detail="计划不存在")

    statement = select(InspectionRecord)
    if plan_id is not None:
        statement = statement.where(InspectionRecord.plan_id == plan_id)
    statement = (
        statement.order_by(
            InspectionRecord.recorded_at.desc(), InspectionRecord.id.desc()
        )
        .limit(limit)
        .offset(offset)
    )
    return [InspectionRead.model_validate(record) for record in db.scalars(statement)]


@router.get(
    "/{inspection_id}",
    response_model=InspectionRead,
    responses={404: {"description": "巡查记录不存在"}},
)
def get_inspection(inspection_id: UUID, db: Session = Depends(get_db)):
    record = db.get(InspectionRecord, inspection_id)
    if record is None:
        raise HTTPException(status_code=404, detail="巡查记录不存在")
    return InspectionRead.model_validate(record)