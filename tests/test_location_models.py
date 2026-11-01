import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy.orm import Session

from adopt_intake_database import schema_signature
from database import Base, create_db_engine, engine as project_engine
from models import Location, PlanInstance, PlanLocation, ServiceRequest


ROOT = Path(__file__).resolve().parents[1]


class LocationModelTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.engine = create_db_engine(Path(self.folder.name) / "case.db")
        guard = patch.object(
            project_engine, "connect",
            side_effect=AssertionError("模型测试禁止连接项目数据库"),
        )
        guard.start()
        self.addCleanup(guard.stop)
        config = Config(str(ROOT / "alembic.ini"))

        with self.engine.begin() as connection:
            config.attributes["connection"] = connection
            try:
                command.upgrade(config, "head")
            finally:
                config.attributes.pop("connection", None)

    def tearDown(self):
        self.engine.dispose()
        self.folder.cleanup()

    def test_latest_migration_matches_all_current_models(self):
        reference = create_db_engine(Path(self.folder.name) / "reference.db")
        try:
            Base.metadata.create_all(reference)
            with self.engine.connect() as actual, reference.connect() as expected:
                self.assertEqual(
                    MigrationContext.configure(actual).get_current_heads(),
                    ("0003_location_hierarchy",),
                )
                self.assertEqual(schema_signature(actual), schema_signature(expected))
        finally:
            reference.dispose()

    def test_same_room_names_keep_distinct_ids_and_parents_after_reopen(self):
        with Session(self.engine) as db, db.begin():
            db.add(Location(id="COMMUNITY", name="合成小区", kind="community"))
            db.flush()
            db.add_all([
                Location(id="BUILDING-1", name="1栋", kind="building", parent_id="COMMUNITY"),
                Location(id="BUILDING-2", name="2栋", kind="building", parent_id="COMMUNITY"),
            ])
            db.flush()
            db.add_all([
                Location(id="ROOM-1-302", name="302室", kind="room", parent_id="BUILDING-1"),
                Location(id="ROOM-2-302", name="302室", kind="room", parent_id="BUILDING-2"),
            ])

        self.engine.dispose()
        with Session(self.engine) as db:
            first = db.get(Location, "ROOM-1-302")
            second = db.get(Location, "ROOM-2-302")
            self.assertEqual((first.name, second.name), ("302室", "302室"))
            self.assertEqual((first.kind, second.kind), ("room", "room"))
            self.assertNotEqual(first.id, second.id)
            self.assertEqual(first.parent_id, "BUILDING-1")
            self.assertEqual(second.parent_id, "BUILDING-2")

    def test_default_location_and_report_plan_links_remain_readable(self):
        with Session(self.engine) as db, db.begin():
            location = Location(id="LOC-OLD", name="原有大堂", version=7)
            report = ServiceRequest(
                request_id=uuid4(), service_category="cleaning",
                description="  合成原文  ", source="web", risk_status="unknown",
                location=location,
            )
            db.add(report)
            db.flush()
            plan = PlanInstance(
                request_id=uuid4(), title="合成计划", service_category="cleaning",
                requirements="核对大堂", owner_name="合成负责人",
                planned_start_at=datetime(2026, 11, 1, 1, tzinfo=UTC),
                planned_end_at=datetime(2026, 11, 1, 2, tzinfo=UTC),
                locations=[PlanLocation(location_id=location.id)],
            )
            db.add(plan)
            db.flush()
            report_id, plan_id = report.id, plan.id

        self.engine.dispose()
        with Session(self.engine) as db:
            saved = db.get(Location, "LOC-OLD")
            self.assertEqual(
                (saved.kind, saved.parent_id, saved.version),
                ("unknown", None, 7),
            )
            saved_report = db.get(ServiceRequest, report_id)
            saved_plan = db.get(PlanInstance, plan_id)
            self.assertEqual(saved_report.description, "  合成原文  ")
            self.assertEqual(saved_report.location.id, saved.id)
            self.assertEqual(saved_plan.locations[0].location.id, saved.id)


if __name__ == "__main__":
    unittest.main()