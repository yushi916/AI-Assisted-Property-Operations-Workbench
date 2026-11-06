from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from database import get_db
from models import PlanInstance, PlanLocation, RequestPlanLink, ServiceRequest, utc_now
from plan_link_schemas import RequestPlanLinkCreate, RequestPlanLinkRead


router = APIRouter(prefix="/api/service-requests/{report_id}/plan-links", tags=["plan-links"])


@router.post(
    "", response_model=RequestPlanLinkRead, status_code=201,
    responses={
        200: {"model": RequestPlanLinkRead, "description": "原样重试，返回同一关联的当前状态"},
        409: {"description": "提交内容冲突，或已有同一报事与计划的有效关联"},
        422: {"description": "输入、父记录、服务类别或明确地点范围不符合要求"},
        503: {"description": "数据库暂时繁忙，可原样重试"},
    },
)
def create_plan_link(
    report_id: UUID, data: RequestPlanLinkCreate,
    response: Response, db: Session = Depends(get_db),
):
    try:
        with db.begin():
            # 先争取写入；唯一约束负责并发，后续校验失败则整笔回滚。
            inserted_id = db.scalar(
                insert(RequestPlanLink).values(
                    **data.model_dump(), id=uuid4(), report_id=report_id,
                    linked_at=utc_now(), status="active", version=1,
                ).on_conflict_do_nothing().returning(RequestPlanLink.id)
            )
            link = db.scalar(select(RequestPlanLink).where(
                RequestPlanLink.request_id == data.request_id
            ))
            if link is None:
                raise HTTPException(status_code=409, detail="该报事与计划已有有效关联，请查询已有记录。")
            original = {name: getattr(link, name) for name in RequestPlanLinkCreate.model_fields}
            if link.report_id != report_id or original != data.model_dump():
                raise HTTPException(status_code=409, detail="同一提交编号已用于不同关联内容；原记录未修改。")
            if inserted_id is not None:
                report = db.get(ServiceRequest, report_id)
                plan = db.get(PlanInstance, data.plan_id)
                if report.service_category != plan.service_category:
                    raise HTTPException(status_code=422, detail="报事与计划的服务类别不一致。")
                if report.location_id is None:
                    raise HTTPException(status_code=422, detail="报事地点未知，请先核实地点；本次未建立关联。")
                covered = set(db.scalars(select(PlanLocation.location_id).where(
                    PlanLocation.plan_id == data.plan_id
                )))
                if report.location_id not in covered:
                    raise HTTPException(status_code=422, detail="报事地点不在计划明确覆盖范围内；当前不自动展开地点层级。")
            result = RequestPlanLinkRead.model_validate(link)
    except IntegrityError as error:
        if error.orig.sqlite_errorname == "SQLITE_CONSTRAINT_FOREIGNKEY":
            raise HTTPException(status_code=422, detail="报事或计划不存在，请核对编号。") from error
        raise
    except OperationalError as error:
        if error.orig.sqlite_errorname.startswith(("SQLITE_BUSY", "SQLITE_LOCKED")):
            raise HTTPException(status_code=503, detail="数据库暂时繁忙，请原样重试本次提交。") from error
        raise
    response.status_code = 201 if inserted_id is not None else 200
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get(
    "", response_model=list[RequestPlanLinkRead],
    responses={404: {"description": "报事不存在"}, 503: {"description": "查询未完成"}},
)
def list_plan_links(
    report_id: UUID, response: Response,
    limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    try:
        with db.begin():
            if db.get(ServiceRequest, report_id) is None:
                raise HTTPException(status_code=404, detail="报事不存在")
            records = db.scalars(select(RequestPlanLink).where(
                RequestPlanLink.report_id == report_id
            ).order_by(RequestPlanLink.linked_at.desc(), RequestPlanLink.id.desc())
              .limit(limit).offset(offset))
            result = [RequestPlanLinkRead.model_validate(item) for item in records]
    except OperationalError as error:
        raise HTTPException(status_code=503, detail="关联查询未完成，请稍后重试。") from error
    response.headers["Cache-Control"] = "no-store"
    return result