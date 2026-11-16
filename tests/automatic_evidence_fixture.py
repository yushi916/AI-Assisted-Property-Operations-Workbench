"""Independent temporary data; no dependency on another TestCase or the demo database."""

import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy.orm import sessionmaker

from database import Base, create_db_engine, engine as project_engine
from models import (
    ExecutionLocation, ExecutionRecord, InspectionRecord, Location,
    PlanInstance, PlanLocation, RequestPlanLink, ServiceRequest,
)


def at(value):
    return datetime.fromisoformat("2026-11-11T" + value + "+08:00")


class AutomaticEvidenceFixture:
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.engine = create_db_engine(Path(folder.name) / "evidence.db")
        self.addCleanup(self.engine.dispose)
        guard = patch.object(project_engine, "connect", side_effect=AssertionError("禁止连接项目业务库"))
        guard.start()
        self.addCleanup(guard.stop)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.sessions.begin() as db:
            for id_, kind, parent, name in [
                ("SITE", "community", None, "清和家园"), ("AREA", "area", "SITE", "A区"),
                ("BUILDING-1", "building", "AREA", "1栋"), ("BUILDING-2", "building", "AREA", "2栋"),
                ("UNIT-1", "unit", "BUILDING-1", "1单元"), ("UNIT-2", "unit", "BUILDING-2", "1单元"),
                ("FLOOR-1", "floor", "UNIT-1", "3层"), ("FLOOR-2", "floor", "UNIT-2", "3层"),
                ("ROOM-302", "room", "FLOOR-1", "302室"), ("ROOM-301", "room", "FLOOR-1", "301室"),
                ("OTHER-302", "room", "FLOOR-2", "302室"),
                ("CORRIDOR", "public_space", "FLOOR-1", "公共走廊"),
                ("LOBBY", "public_space", "BUILDING-1", "大堂"),
                ("COURTYARD", "public_space", "AREA", "中庭"), ("UNKNOWN", "unknown", None, "地点待核实"),
            ]:
                db.add(Location(id=id_, kind=kind, parent_id=parent, name=name))
                db.flush()
        self.report_id = self.report()

    def report(self, point="ROOM-302", observed="10:00", category="water_supply", description=None, received="10:05"):
        with self.sessions.begin() as db:
            row = ServiceRequest(
                request_id=uuid4(), service_category=category, source="phone", risk_status="unknown",
                location_id=point, description=description or "厨房和卫生间都没水，家里的总阀没动过，麻烦来看看。",
                observed_at=None if observed is None else at(observed), received_at=at(received),
            )
            db.add(row)
            db.flush()
            return row.id

    def plan(self, point="ROOM-302", category="water_supply", title="入户供水检查", old=False):
        with self.sessions.begin() as db:
            row = PlanInstance(
                request_id=uuid4(), service_category=category, title=title,
                requirements=title, owner_name="维修班王师傅", plan_kind="special",
                planned_start_at=at("08:00"), planned_end_at=at("09:00"),
                locations=[PlanLocation(location_id=point)],
            )
            if old:
                row.planned_start_at = datetime.fromisoformat("2026-10-01T08:00+08:00")
                row.planned_end_at = datetime.fromisoformat("2026-10-01T09:00+08:00")
            db.add(row)
            db.flush()
            return row.id

    def execution(self, plan_id, point="ROOM-302", finished="09:10", description="已检查室内各用水点。", completed=True):
        with self.sessions.begin() as db:
            row = ExecutionRecord(
                request_id=uuid4(), plan_id=plan_id, executor_name="维修班王师傅", description=description,
                completion_claim="completed" if completed else "unknown", claimed_started_at=None,
                claimed_finished_at=None if finished is None else at(finished), recorded_at=at("11:00"),
                locations=[] if point is None else [ExecutionLocation(location_id=point)],
            )
            db.add(row)
            db.flush()
            return row.id

    def inspection(self, plan_id, point="ROOM-302", observed="10:30", finding="issue_observed", description="厨房和卫生间仍没有出水。"):
        with self.sessions.begin() as db:
            row = InspectionRecord(
                request_id=uuid4(), plan_id=plan_id, inspector_name="值班员陈女士", description=description,
                finding=finding, observed_at=None if observed is None else at(observed),
                location_id=point, recorded_at=at("11:05"),
            )
            db.add(row)
            db.flush()
            return row.id

    def link(self, plan_id):
        with self.sessions.begin() as db:
            row = RequestPlanLink(
                report_id=self.report_id, plan_id=plan_id, request_id=uuid4(),
                linked_by="值班员陈女士", reason="住户补充了此前的检查安排", linked_at=at("11:10"),
            )
            db.add(row)
            db.flush()
            return row.id

    def snapshot(self):
        with self.engine.connect() as connection:
            return {name: connection.exec_driver_sql(f'SELECT * FROM "{name}" ORDER BY 1, 2').all()
                    for name in Base.metadata.tables}
