import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from adopt_intake_database import schema_signature
from database import Base, create_db_engine, engine as project_engine
from models import Location, PlanInstance, PlanLocation, ServiceRequest


ROOT = Path(__file__).resolve().parents[1]
OLD_TABLES = ("locations", "service_requests")
ALL_TABLES = OLD_TABLES + ("plan_instances", "plan_locations")


class PlanMigrationTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.engine = create_db_engine(Path(self.folder.name) / "case.db")
        self.config = Config(str(ROOT / "alembic.ini"))
        guard = patch.object(
            project_engine, "connect",
            side_effect=AssertionError("迁移测试禁止连接项目数据库"),
        )
        guard.start()
        self.addCleanup(guard.stop)

    def tearDown(self):
        self.engine.dispose()
        self.folder.cleanup()

    def upgrade(self, target="head"):
        with self.engine.begin() as connection:
            self.config.attributes["connection"] = connection
            try:
                command.upgrade(self.config, target)
            finally:
                self.config.attributes.pop("connection", None)

    def heads(self):
        with self.engine.connect() as connection:
            return MigrationContext.configure(connection).get_current_heads()

    def rows(self, tables=OLD_TABLES):
        with self.engine.connect() as connection:
            return {
                table: connection.exec_driver_sql(
                    f'SELECT * FROM "{table}" ORDER BY 1, 2'
                ).all()
                for table in tables
            }

    def seed_legacy(self):
        self.upgrade("0001_intake")
        with Session(self.engine) as db, db.begin():
            db.add_all([
                Location(id="LOC-A-LOBBY", name="A区大堂"),
                Location(id="LOC-B-LOBBY", name="B区大堂"),
            ])
            db.flush()
            for location_id, observed in (
                ("LOC-A-LOBBY", datetime.fromisoformat(
                    "2026-10-29T09:50:00+08:00"
                )),
                (None, None),
            ):
                db.add(ServiceRequest(
                    request_id=uuid4(), service_category="cleaning",
                    description="  合成旧报事：保留原文空格。",
                    source="web", risk_status="unknown",
                    location_id=location_id, observed_at=observed,
                ))

    def make_plan(self, **changes):
        values = {
            "request_id": uuid4(),
            "title": "合成计划：两处大堂保洁",
            "service_category": "cleaning",
            "requirements": "清理地面散落垃圾。",
            "owner_name": "合成负责人",
            "planned_start_at": datetime.fromisoformat(
                "2026-11-01T09:00:00+08:00"
            ),
            "planned_end_at": datetime.fromisoformat(
                "2026-11-01T09:30:00+08:00"
            ),
            "locations": [
                PlanLocation(location_id="LOC-A-LOBBY"),
                PlanLocation(location_id="LOC-B-LOBBY"),
            ],
        }
        values.update(changes)
        return PlanInstance(**values)

    def test_fresh_upgrade_matches_current_models(self):
        self.upgrade()
        self.assertEqual(self.heads(), ("0002_plans",))
        self.assertEqual(
            set(inspect(self.engine).get_table_names()),
            set(ALL_TABLES) | {"alembic_version"},
        )
        reference = create_db_engine(Path(self.folder.name) / "reference.db")
        try:
            Base.metadata.create_all(reference)
            with self.engine.connect() as actual, reference.connect() as expected:
                self.assertEqual(schema_signature(actual), schema_signature(expected))
        finally:
            reference.dispose()

    def test_upgrade_preserves_old_rows_and_supports_plans(self):
        self.seed_legacy()
        before = self.rows()
        self.upgrade()
        self.assertEqual(self.heads(), ("0002_plans",))
        self.assertEqual(self.rows(), before)
        with Session(self.engine) as db, db.begin():
            plan = self.make_plan()
            db.add(plan)
            db.flush()
            plan_id = plan.id
        self.engine.dispose()
        with Session(self.engine) as db:
            saved = db.get(PlanInstance, plan_id)
            self.assertEqual(
                {point.location_id for point in saved.locations},
                {"LOC-A-LOBBY", "LOC-B-LOBBY"},
            )
        self.assertEqual(self.rows(), before)

    def test_repeated_upgrade_keeps_all_business_rows(self):
        self.seed_legacy()
        self.upgrade()
        with Session(self.engine) as db, db.begin():
            db.add(self.make_plan())
        before = self.rows(ALL_TABLES)
        self.upgrade()
        self.assertEqual(self.rows(ALL_TABLES), before)
        self.assertEqual(self.heads(), ("0002_plans",))

    def test_migrated_plan_constraints_reject_invalid_writes(self):
        self.seed_legacy()
        self.upgrade()
        request_id = uuid4()
        with Session(self.engine) as db, db.begin():
            original = self.make_plan(request_id=request_id)
            db.add(original)
            db.flush()
            plan_id = original.id
        before = self.rows(ALL_TABLES)
        for changes in (
            {"request_id": request_id}, {"version": 0},
            {"title": ""}, {"service_category": "invalid"},
            {"plan_kind": "invalid"},
            {"planned_end_at": datetime.fromisoformat(
                "2026-11-01T09:00:00+08:00"
            )},
        ):
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(
                    IntegrityError, "UNIQUE constraint failed|CHECK constraint failed"
                ):
                    with Session(self.engine) as db, db.begin():
                        db.add(self.make_plan(**changes))
                self.assertEqual(self.rows(ALL_TABLES), before)
        for target_plan, location, error in (
            (plan_id, "MISSING", "FOREIGN KEY constraint failed"),
            (uuid4(), "LOC-A-LOBBY", "FOREIGN KEY constraint failed"),
            (plan_id, "LOC-A-LOBBY", "UNIQUE constraint failed"),
        ):
            with self.subTest(plan=target_plan, location=location):
                with self.assertRaisesRegex(IntegrityError, error):
                    with Session(self.engine) as db, db.begin():
                        db.add(PlanLocation(plan_id=target_plan, location_id=location))
                self.assertEqual(self.rows(ALL_TABLES), before)

    def test_failed_upgrade_keeps_old_version_and_rows(self):
        self.seed_legacy()
        before = self.rows()
        with self.engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE plan_locations (id INTEGER PRIMARY KEY)"
            )
            connection.exec_driver_sql("INSERT INTO plan_locations VALUES (7)")
        with self.assertRaisesRegex(
            OperationalError, "table plan_locations already exists"
        ):
            self.upgrade()
        self.assertEqual(self.heads(), ("0001_intake",))
        self.assertEqual(self.rows(), before)
        self.assertNotIn("plan_instances", inspect(self.engine).get_table_names())
        with self.engine.connect() as connection:
            self.assertEqual(
                connection.exec_driver_sql("SELECT id FROM plan_locations").scalar(), 7
            )


if __name__ == "__main__":
    unittest.main()