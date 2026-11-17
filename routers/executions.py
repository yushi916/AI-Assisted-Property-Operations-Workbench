from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import exists, literal, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from database import get_db
from execution_schemas import ExecutionCreate, ExecutionRead
from models import ExecutionLocation, ExecutionRecord, PlanLocation, utc_now
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session, selectinload
from models import ExecutionLocation, ExecutionRecord, PlanInstance, PlanLocation, utc_now


router = APIRouter(prefix="/api/executions", tags=["executions"])
SERVER_FIELDS = {"id", "recorded_at", "version"}


def read_execution(execution):
    values = {
        name: getattr(execution, name)
        for name in ExecutionCreate.model_fields
        if name != "location_ids"
    }
    return ExecutionRead.model_validate({
        **values,
        "location_ids": sorted(point.location_id for point in execution.locations),
        "id": execution.id,
        "recorded_at": execution.recorded_at,
        "version": execution.version,
    })


@router.post(
    "",
    response_model=ExecutionRead,
    status_code=201,
    responses={
        200: {"model": ExecutionRead, "description": "相同提交重试，返回原执行记录"},
        409: {"description": "同一提交编号用于不同执行内容"},
        422: {"description": "输入、计划、明确点位范围或执行时间不合法"},
        503: {"description": "数据库暂时繁忙，可重试本次提交"},
    },
)
def create_execution(data: ExecutionCreate, response: Response, db: Session = Depends(get_db)):
    try:
        with db.begin():
            values = {
                **data.model_dump(exclude={"location_ids"}),
                "id": uuid4(), "recorded_at": utc_now(), "version": 1,
            }
            columns = ExecutionRecord.__table__.c
            candidate = select(*[
                literal(value, type_=columns[name].type)
                for name, value in values.items()
            ]).where(~exists(
                select(ExecutionRecord.id).where(ExecutionRecord.request_id == data.request_id)
            ))
            # 同一条写语句仅在编号尚不存在时插入，已有提交不重复校验当前范围和时钟。
            statement = (
                insert(ExecutionRecord)
                .from_select(list(values), candidate, include_defaults=False)
                .on_conflict_do_nothing(index_elements=["request_id"])
                .returning(ExecutionRecord.id)
            )
            inserted_id = db.scalar(statement)
            execution = db.execute(
                select(ExecutionRecord).where(ExecutionRecord.request_id == data.request_id)
            ).scalar_one()
            if inserted_id is not None:
                covered = set(db.scalars(
                    select(PlanLocation.location_id).where(PlanLocation.plan_id == data.plan_id)
                ))
                outside = set(data.location_ids) - covered
                if outside:
                    raise HTTPException(
                        status_code=422,
                        detail="执行地点不在本次计划明确覆盖范围内：" + "、".join(sorted(outside)),
                    )
                db.add_all([
                    ExecutionLocation(execution_id=execution.id, location_id=key)
                    for key in sorted(data.location_ids)
                ])
                db.flush()

            result = read_execution(execution)
            incoming = data.model_dump()
            incoming["location_ids"] = sorted(data.location_ids)
            if inserted_id is None and result.model_dump(exclude=SERVER_FIELDS) != incoming:
                raise HTTPException(status_code=409, detail="同一提交编号已用于不同内容；原执行记录未修改。")
    except IntegrityError as error:
        if error.orig.sqlite_errorname == "SQLITE_CONSTRAINT_FOREIGNKEY":
            raise HTTPException(status_code=422, detail="关联的计划或地点不存在，请核对。") from error
        if any(name in str(error.orig) for name in (
            "ck_execution_start_recorded", "ck_execution_finish_recorded",
        )):
            raise HTTPException(status_code=422, detail="已知执行时间不能晚于本次录入时间。") from error
        raise
    except OperationalError as error:
        if error.orig.sqlite_errorname.startswith(("SQLITE_BUSY", "SQLITE_LOCKED")):
            raise HTTPException(status_code=503, detail="数据库暂时繁忙，请稍后重试本次提交。") from error
        raise

    response.status_code = 201 if inserted_id is not None else 200
    return result


@router.get(
    "",
    response_model=list[ExecutionRead],
    responses={404: {"description": "筛选的计划不存在"}},
)
def list_executions(
    plan_id: UUID | None = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    if plan_id is not None and db.get(PlanInstance, plan_id) is None:
        raise HTTPException(status_code=404, detail="计划不存在")

    statement = select(ExecutionRecord).options(
        selectinload(ExecutionRecord.locations)
    )
    if plan_id is not None:
        statement = statement.where(ExecutionRecord.plan_id == plan_id)
    statement = (
        statement.order_by(
            ExecutionRecord.recorded_at.desc(), ExecutionRecord.id.desc()
        )
        .limit(limit)
        .offset(offset)
    )
    return [read_execution(execution) for execution in db.scalars(statement)]


@router.get(
    "/{execution_id}",
    response_model=ExecutionRead,
    responses={404: {"description": "执行记录不存在"}},
)
def get_execution(execution_id: UUID, db: Session = Depends(get_db)):
    execution = db.get(ExecutionRecord, execution_id)
    if execution is None:
        raise HTTPException(status_code=404, detail="执行记录不存在")
    return read_execution(execution)