"""Bounded, read-only report discovery; proximity is a clue, never a cause."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from location_hierarchy import build_location_paths
from models import Location, ServiceRequest
from schemas import LocationRead, ServiceRequestRead


@dataclass(frozen=True)
class RelatedReportPolicy:
    # Demo search configuration; independent of the plan window, not an SLA.
    lookback: timedelta = timedelta(hours=2)
    lookahead: timedelta = timedelta(hours=2)
    max_reports: int = 50

    def __post_init__(self):
        if self.lookback < timedelta(0) or self.lookahead < timedelta(0):
            raise ValueError("检索时间范围不能为负")
        if type(self.max_reports) is not int or self.max_reports < 1:
            raise ValueError("报事数量上限必须为正整数")


class RelatedReportCandidate(BaseModel):
    report: ServiceRequestRead
    location_relation: Literal["same_location", "same_building"]
    time_basis: Literal["observed_at", "received_at_search_only"]
    # Candidate observation relative to seed observation; never use receipt time here.
    observation_relation: Literal["before", "same_time", "after", "unknown"]
    reason_codes: list[str]


@dataclass(frozen=True)
class RelatedReportDiscovery:
    report: ServiceRequestRead
    locations: dict[str, LocationRead]
    window_start: datetime
    window_end: datetime
    time_basis: str
    location_scope: str
    building_id: str | None
    candidates: tuple[RelatedReportCandidate, ...]
    status: str
    warnings: tuple[str, ...]
    policy_version: str = "related-reports-v1"


class RelatedReportLimitExceeded(ValueError):
    pass


def require_read_transaction(db: Session):
    if not db.in_transaction():
        raise ValueError("取证必须在调用方的读取事务内执行")
    if db.new or db.dirty or db.deleted:
        raise ValueError("取证需使用没有待写入变更的读取事务")


def read_location_catalog(db: Session) -> dict[str, LocationRead]:
    require_read_transaction(db)
    rows = db.scalars(select(Location).order_by(Location.id)).all()
    paths = build_location_paths(rows)
    return {row.id: LocationRead(
        id=row.id, name=row.name, version=row.version, kind=row.kind,
        parent_id=row.parent_id, display_path=paths[row.id],
    ) for row in rows}


def building_for(location_id: str | None, locations: dict[str, LocationRead]) -> str | None:
    current = locations.get(location_id)
    while current is not None:
        if current.kind == "building":
            return current.id
        current = locations.get(current.parent_id)
    return None


def discover_related_reports(
    db: Session, report_id: UUID, policy: RelatedReportPolicy | None = None,
) -> RelatedReportDiscovery:
    require_read_transaction(db)
    policy = policy or RelatedReportPolicy()
    row = db.get(ServiceRequest, report_id)
    if row is None:
        raise LookupError("报事不存在")
    report = ServiceRequestRead.model_validate(row)
    locations = read_location_catalog(db)
    reference = (report.observed_at or report.received_at).astimezone(UTC)
    start, end = reference - policy.lookback, reference + policy.lookahead
    basis = "observed_at" if report.observed_at is not None else "received_at_search_only"
    warnings = [
        "时间和地点相近仅表示线索，不代表同一故障或已经恢复。",
        "本次不解析描述中的地址；未注明地点的其他报事不在地点匹配范围内。",
    ]
    if report.observed_at is None:
        warnings.append("起始报事观察时间未注明；接收时间仅用于检索，实际先后未知。")
    location = locations.get(report.location_id)
    if report.location_id is not None and location is None:
        raise LookupError("报事地点台账缺失")
    building = building_for(report.location_id, locations)
    known_location = location is not None and location.kind != "unknown"
    location_scope = "same_building" if building and report.service_category == "water_supply" else "same_location"
    if not known_location:
        warnings.append("地点待核实，未查询其他报事；请补充台账地点后再核查，未将全小区报事视为相关。")
        return RelatedReportDiscovery(report, locations, start, end, basis, "unknown", None, (),
                                      "not_queried", tuple(warnings))
    points = [report.location_id]
    if location_scope == "same_building":
        points = [point for point in locations if building_for(point, locations) == building]
        warnings.append("仅搜索台账中的同一楼栋；未接入设备或管网资料，不能确认共同设备及跨楼栋关系。")
    else:
        warnings.append("本次仅匹配同一台账地点；未扩大到同区域其他点位。")
    search_time = func.coalesce(ServiceRequest.observed_at, ServiceRequest.received_at)
    rows = db.scalars(select(ServiceRequest).where(
        ServiceRequest.id != report_id,
        ServiceRequest.service_category == report.service_category,
        ServiceRequest.location_id.in_(points),
        search_time.between(start, end),
    ).order_by(search_time, ServiceRequest.id).limit(policy.max_reports + 1)).all()
    if len(rows) > policy.max_reports:
        raise RelatedReportLimitExceeded("相关报事超过检索上限，请收窄范围；未返回截断的结果。")
    candidates = []
    for row in rows:
        other = ServiceRequestRead.model_validate(row)
        relation = "same_location" if other.location_id == report.location_id else "same_building"
        time_basis = "observed_at" if other.observed_at is not None else "received_at_search_only"
        if report.observed_at is None or other.observed_at is None:
            observation_relation = "unknown"
        else:
            seed_time, other_time = report.observed_at.astimezone(UTC), other.observed_at.astimezone(UTC)
            observation_relation = "before" if other_time < seed_time else "after" if other_time > seed_time else "same_time"
        candidates.append(RelatedReportCandidate(
            report=other, location_relation=relation, time_basis=time_basis,
            observation_relation=observation_relation,
            reason_codes=["same_service_category", relation, "within_report_search_window", time_basis],
        ))
    return RelatedReportDiscovery(
        report, locations, start, end, basis, location_scope, building, tuple(candidates),
        "found" if candidates else "empty", tuple(warnings),
    )
