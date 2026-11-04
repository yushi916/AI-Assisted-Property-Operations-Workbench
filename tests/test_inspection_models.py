import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session

from adopt_intake_database import schema_signature
from database import Base, create_db_engine, engine as project_engine
from models import (
    ExecutionLocation, ExecutionRecord, InspectionRecord,
    PlanInstance, PlanLocation, ServiceRequest,
)
from seed_demo_location_hierarchy import seed_demo_location_hierarchy


ROOT = Path(__file__).resolve().parents[1]
OLD = (
    "locations", "service_requests", "plan_instances", "plan_locations",
    "execution_records", "execution_locations",
)
ALL = OLD + ("inspection_records",)


class InspectionModelTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.engine = create_db_engine(Path(self.folder.name) / "case.db")
        guard = patch.object(
            project_engine, "connect",
            side_effect=AssertionError("巡查模型测试禁止连接项目数据库"),
        )
        guard.start()
        self.addCleanup(guard.stop)
        config = Config(str(ROOT / "alembic.ini"))
        with self.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0005_inspection_records")

    def tearDown(self):
        self.engine.dispose()
        self.folder.cleanup()

    def rows(self, tables=ALL):
        with self.engine.connect() as connection:
            return {
                table: connection.exec_driver_sql(
                    f'SELECT * FROM "{table}" ORDER BY 1, 2'
                ).all()
                for table in tables
            }

    def prepare_plan(self, with_execution=True):
        seed_demo_location_hierarchy(self.engine)
        with Session(self.engine) as db, db.begin():
            db.add(ServiceRequest(
                request_id=uuid4(), service_category="cleaning",
                description="  合成旧报事  ", source="web", risk_status="unknown",
            ))
            plan = PlanInstance(
                request_id=uuid4(), title="合成A、B大堂保洁",
                service_category="cleaning", requirements="分别核对两处大堂",
                owner_name="合成负责人",
                planned_start_at=datetime(2026, 11, 1, 1, tzinfo=UTC),
                planned_end_at=datetime(2026, 11, 1, 2, tzinfo=UTC),
                locations=[PlanLocation(location_id=key)
                           for key in ("LOC-A-LOBBY", "LOC-B-LOBBY")],
            )
            db.add(plan)
            db.flush()
            self.plan_id = plan.id
            if with_execution:
                db.add(ExecutionRecord(
                    request_id=uuid4(), plan_id=plan.id,
                    executor_name="合成执行人", description="仅A大堂声称完成",
                    completion_claim="completed",
                    claimed_finished_at=datetime(2026, 11, 1, 1, 20, tzinfo=UTC),
                    recorded_at=datetime(2026, 11, 1, 1, 25, tzinfo=UTC),
                    locations=[ExecutionLocation(location_id="LOC-A-LOBBY")],
                ))

    def make_inspection(self, **changes):
        values = dict(
            request_id=uuid4(), plan_id=self.plan_id,
            inspector_name=" 合成巡查员 ", description="  合成：A大堂发现垃圾。  ",
            finding="issue_observed", location_id="LOC-A-LOBBY",
            observed_at=datetime.fromisoformat("2026-11-01T09:50:00+08:00"),
            recorded_at=datetime(2026, 11, 1, 2, tzinfo=UTC),
        )
        values.update(changes)
        return InspectionRecord(**values)

    def test_latest_inspection_revision_matches_all_seven_models(self):
        reference = create_db_engine(Path(self.folder.name) / "reference.db")
        try:
            Base.metadata.create_all(reference)
            with self.engine.connect() as actual, reference.connect() as expected:
                self.assertEqual(
                    MigrationContext.configure(actual).get_current_heads(),
                    ("0005_inspection_records",),
                )
                self.assertEqual(set(schema_signature(actual)), set(ALL))
                self.assertEqual(schema_signature(actual), schema_signature(expected))
        finally:
            reference.dispose()

    def test_round_trip_keeps_text_utc_and_relations_without_execution(self):
        self.prepare_plan(with_execution=False)
        before = self.rows(OLD)
        with Session(self.engine) as db, db.begin():
            record = self.make_inspection()
            db.add(record)
            db.flush()
            key, request_id = record.id, record.request_id
        self.engine.dispose()
        with Session(self.engine) as db:
            saved = db.get(InspectionRecord, key)
            self.assertIsInstance(saved.id, UUID)
            self.assertNotEqual(saved.id, request_id)
            self.assertEqual(saved.inspector_name, " 合成巡查员 ")
            self.assertEqual(saved.description, "  合成：A大堂发现垃圾。  ")
            self.assertEqual(saved.observed_at, datetime(2026, 11, 1, 1, 50, tzinfo=UTC))
            self.assertEqual(saved.observed_at.tzinfo, UTC)
            self.assertEqual(saved.recorded_at.tzinfo, UTC)
            self.assertEqual(saved.version, 1)
            self.assertEqual(saved.plan.id, self.plan_id)
            self.assertEqual(saved.location.id, "LOC-A-LOBBY")
            self.assertEqual(
                {point.location_id for point in saved.plan.locations},
                {"LOC-A-LOBBY", "LOC-B-LOBBY"},
            )
        self.assertEqual(self.rows(OLD), before)
        self.assertEqual(self.rows()["execution_records"], [])

    def test_unknown_point_and_time_keep_defaults_after_reopen(self):
        self.prepare_plan()
        before = self.rows(OLD)
        with Session(self.engine) as db, db.begin():
            for finding in ("no_issue_observed", "issue_observed", "unable_to_verify"):
                db.add(InspectionRecord(
                    request_id=uuid4(), plan_id=self.plan_id,
                    inspector_name="合成巡查员", description="合成：地点和时间待核实。",
                    finding=finding,
                ))
        self.engine.dispose()
        with Session(self.engine) as db:
            records = list(db.scalars(select(InspectionRecord)))
            self.assertEqual(len(records), 3)
            for saved in records:
                self.assertIsNone(saved.location_id)
                self.assertIsNone(saved.location)
                self.assertIsNone(saved.observed_at)
                self.assertIsNotNone(saved.recorded_at)
                self.assertEqual(saved.recorded_at.tzinfo, UTC)
                self.assertEqual(saved.version, 1)
        self.assertEqual(self.rows(OLD), before)

    def test_invalid_values_and_duplicates_preserve_original_evidence(self):
        self.prepare_plan()
        with Session(self.engine) as db, db.begin():
            record = self.make_inspection()
            db.add(record)
            db.flush()
            key, request_id = record.id, record.request_id
        before = self.rows()
        for changes in (
            {"id": key},
            {"request_id": request_id, "description": "不能覆盖", "location_id": "LOC-B-LOBBY"},
            {"plan_id": uuid4()}, {"location_id": "MISSING"}, {"version": 0},
            {"inspector_name": ""}, {"inspector_name": "x" * 121},
            {"description": ""}, {"description": "x" * 2001},
            {"finding": "completed"},
            {"observed_at": datetime(2026, 11, 1, 3, tzinfo=UTC)},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(IntegrityError):
                    with Session(self.engine) as db, db.begin():
                        db.add(self.make_inspection(**changes))
                self.assertEqual(self.rows(), before)

    def test_naive_times_are_rejected_without_writes(self):
        self.prepare_plan()
        before = self.rows()
        for name in ("observed_at", "recorded_at"):
            with self.subTest(field=name):
                with self.assertRaises(StatementError) as caught:
                    with Session(self.engine) as db, db.begin():
                        db.add(self.make_inspection(**{name: datetime(2026, 11, 1, 1)}))
                self.assertIsInstance(caught.exception.orig, ValueError)
                self.assertEqual(self.rows(), before)

    def test_late_failure_rolls_back_earlier_flushed_observation(self):
        self.prepare_plan()
        before = self.rows()
        with self.assertRaises(IntegrityError):
            with Session(self.engine) as db, db.begin():
                db.add(self.make_inspection())
                db.flush()
                self.assertEqual(db.scalar(
                    select(func.count()).select_from(InspectionRecord)
                ), 1)
                db.add(self.make_inspection(location_id="MISSING"))
                db.flush()
        self.assertEqual(self.rows(), before)


if __name__ == "__main__":
    unittest.main()