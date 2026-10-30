from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from database import get_db
from models import ServiceRequest, utc_now
from schemas import ServiceRequestCreate, ServiceRequestRead


router = APIRouter(prefix="/api/service-requests", tags=["reports"])


@router.post(
    "",
    response_model=ServiceRequestRead,
    status_code=201,
    responses={
        200: {
            "model": ServiceRequestRead,
            "description": "相同提交重试，返回原记录",
        },
        409: {"description": "同一提交编号用于不同内容"},
        422: {"description": "输入不合法、地点不存在或观察时间在未来"},
        503: {"description": "数据库暂时繁忙，可重试本次提交"},
    },
)
def create_report(
    data: ServiceRequestCreate,
    response: Response,
    db: Session = Depends(get_db),
):
    received_at = utc_now()
    try:
        with db.begin():
            statement = (
                insert(ServiceRequest)
                .values(**data.model_dump(), received_at=received_at)
                .on_conflict_do_nothing(index_elements=["request_id"])
                .returning(ServiceRequest.id)
            )
            inserted_id = db.scalar(statement)
            report = db.execute(
                select(ServiceRequest).where(
                    ServiceRequest.request_id == data.request_id
                )
            ).scalar_one()

            if inserted_id is None:
                original = ServiceRequestCreate.model_validate(
                    report, from_attributes=True
                )
                if original != data:
                    raise HTTPException(
                        status_code=409,
                        detail="同一提交编号已用于不同内容；原报事未修改。",
                    )
            elif data.observed_at is not None and data.observed_at > received_at:
                raise HTTPException(
                    status_code=422,
                    detail="观察时间不能晚于接收时间；不确定时可留空。",
                )

            result = ServiceRequestRead.model_validate(report)
    except IntegrityError as error:
        if error.orig.sqlite_errorname == "SQLITE_CONSTRAINT_FOREIGNKEY":
            raise HTTPException(
                status_code=422,
                detail="地点不存在，请选择已有地点；未确认时可暂不填写地点。",
            ) from error
        raise
    except OperationalError as error:
        if error.orig.sqlite_errorname.startswith(("SQLITE_BUSY", "SQLITE_LOCKED")):
            raise HTTPException(
                status_code=503,
                detail="数据库暂时繁忙，请稍后重试本次提交。",
            ) from error
        raise

    response.status_code = 201 if inserted_id is not None else 200
    return result


@router.get("", response_model=list[ServiceRequestRead])
def list_reports(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    statement = (
        select(ServiceRequest)
        .order_by(ServiceRequest.received_at.desc(), ServiceRequest.id.desc())
        .limit(limit)
        .offset(offset)
    )
    reports = []
    for report in db.scalars(statement):
        reports.append(ServiceRequestRead.model_validate(report))
    return reports


@router.get(
    "/{report_id}",
    response_model=ServiceRequestRead,
    responses={404: {"description": "报事不存在"}},
)
def get_report(report_id: UUID, db: Session = Depends(get_db)):
    report = db.get(ServiceRequest, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="报事不存在")
    return ServiceRequestRead.model_validate(report)