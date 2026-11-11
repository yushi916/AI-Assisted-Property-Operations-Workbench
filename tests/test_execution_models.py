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
from models import ExecutionLocation, ExecutionRecord, PlanInstance, PlanLocation, ServiceRequest
from seed_demo_location_hierarchy import seed_demo_location_hierarchy
from tests.location_reference import create_pre_floor_schema


ROOT = Path(__file__).resolve().parents[1]
OLD = ("locations", "service_requests", "plan_instances", "plan_locations")
ALL = OLD + ("execution_records", "execution_locations")


class ExecutionModelTests(unittest.TestCase):
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
            command.upgrade(config, "0004_execution_records")

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

    def prepare_plan(self):
        seed_demo_location_hierarchy(self.engine)
        with Session(self.engine) as db, db.begin():
            db.add(ServiceRequest(
                request_id=uuid4(), service_category="cleaning",
                description="  合成报事原文  ", source="web", risk_status="unknown",
                location_id="LOC-A-LOBBY",
            ))
            plan = PlanInstance(
                request_id=uuid4(), title="合成A、B大堂保洁",
                service_category="cleaning", requirements="分别核对两处大堂",
                owner_name="合成负责人",
                planned_start_at=datetime(2026, 11, 1, 1, tzinfo=UTC),
                planned_end_at=datetime(2026, 11, 1, 2, tzinfo=UTC),
                locations=[PlanLocation(location_id=key) for key in ("LOC-A-LOBBY", "LOC-B-LOBBY")],
            )
            db.add(plan)
            db.flush()
            self.plan_id = plan.id

    def make_execution(self, **changes):
        values = dict(
            request_id=uuid4(), plan_id=self.plan_id,
            executor_name="合成执行人", description="  合成：仅A大堂声称完成。  ",
            completion_claim="completed",
            claimed_started_at=datetime.fromisoformat("2026-11-01T09:00:00+08:00"),
            claimed_finished_at=datetime.fromisoformat("2026-11-01T10:20:00+09:00"),
            recorded_at=datetime(2026, 11, 1, 1, 30, tzinfo=UTC),
            locations=[ExecutionLocation(location_id="LOC-A-LOBBY")],
        )
        values.update(changes)
        return ExecutionRecord(**values)

    def test_latest_execution_revision_matches_all_six_models(self):
        reference = create_db_engine(Path(self.folder.name) / "reference.db")
        try:
            create_pre_floor_schema(reference, table_names=ALL)
            with self.engine.connect() as actual, reference.connect() as expected:
                self.assertEqual(
                    MigrationContext.configure(actual).get_current_heads(),
                    ("0004_execution_records",),
                )
                actual_schema = schema_signature(actual)
                self.assertEqual(set(actual_schema), set(ALL))
                self.assertEqual(actual_schema, schema_signature(expected))
        finally:
            reference.dispose()

    def test_round_trip_normalizes_utc_and_preserves_plan_scope_and_old_rows(self):
        self.prepare_plan()
        before = self.rows(OLD)
        with Session(self.engine) as db, db.begin():
            execution = self.make_execution()
            db.add(execution)
            db.flush()
            key = execution.id
        self.engine.dispose()
        with Session(self.engine) as db:
            saved = db.get(ExecutionRecord, key)
            self.assertEqual(saved.description, "  合成：仅A大堂声称完成。  ")
            self.assertEqual(saved.version, 1)
            self.assertEqual(saved.claimed_started_at, datetime(2026, 11, 1, 1, tzinfo=UTC))
            self.assertEqual(saved.claimed_finished_at, datetime(2026, 11, 1, 1, 20, tzinfo=UTC))
            self.assertEqual(saved.recorded_at.tzinfo, UTC)
            self.assertEqual(saved.claimed_finished_at.tzinfo, UTC)
            self.assertEqual(saved.plan.id, self.plan_id)
            self.assertEqual({point.location_id for point in saved.locations}, {"LOC-A-LOBBY"})
            self.assertEqual(saved.locations[0].execution.id, key)
            self.assertEqual(saved.locations[0].location.id, "LOC-A-LOBBY")
            self.assertEqual({point.location_id for point in saved.plan.locations}, {"LOC-A-LOBBY", "LOC-B-LOBBY"})
        with self.engine.connect() as connection:
            self.assertEqual(tuple(connection.exec_driver_sql(
                "SELECT claimed_started_at, claimed_finished_at FROM execution_records"
            ).one()), ("2026-11-01 01:00:00.000000", "2026-11-01 01:20:00.000000"))
        self.assertEqual(self.rows(OLD), before)

    def test_unknown_times_and_scope_keep_defaults_after_reopen(self):
        self.prepare_plan()
        before = self.rows(OLD)
        with Session(self.engine) as db, db.begin():
            for claim in ("completed", "partially_completed", "not_completed", "unknown"):
                db.add(ExecutionRecord(
                    request_id=uuid4(), plan_id=self.plan_id,
                    executor_name="合成执行人", description="合成：具体点位与时间待核实。",
                    completion_claim=claim,
                ))
        self.engine.dispose()
        with Session(self.engine) as db:
            records = list(db.scalars(select(ExecutionRecord)))
            self.assertEqual(len(records), 4)
            for saved in records:
                self.assertIsInstance(saved.id, UUID)
                self.assertEqual(saved.version, 1)
                self.assertIsNone(saved.claimed_started_at)
                self.assertIsNone(saved.claimed_finished_at)
                self.assertEqual(saved.locations, [])
                self.assertIsNotNone(saved.recorded_at)
                self.assertEqual(saved.recorded_at.tzinfo, UTC)
        self.assertEqual(self.rows(OLD), before)

    def test_naive_times_are_rejected_without_writes(self):
        self.prepare_plan()
        before = self.rows()
        for field in ("claimed_started_at", "claimed_finished_at", "recorded_at"):
            with self.subTest(field=field):
                with self.assertRaises(StatementError) as caught:
                    with Session(self.engine) as db, db.begin():
                        db.add(self.make_execution(**{field: datetime(2026, 11, 1, 1)}))
                self.assertIsInstance(caught.exception.orig, ValueError)
                self.assertEqual(self.rows(), before)

    def test_duplicate_request_keeps_original_record_and_points(self):
        self.prepare_plan()
        request_id = uuid4()
        with Session(self.engine) as db, db.begin():
            db.add(self.make_execution(request_id=request_id))
        before = self.rows()
        with self.assertRaises(IntegrityError):
            with Session(self.engine) as db, db.begin():
                db.add(self.make_execution(
                    request_id=request_id, description="不应覆盖原记录",
                    locations=[ExecutionLocation(location_id="LOC-B-LOBBY")],
                ))
        self.assertEqual(self.rows(), before)

    def test_late_point_failure_rolls_back_orm_record_and_point(self):
        self.prepare_plan()
        before = self.rows()
        with self.assertRaises(IntegrityError):
            with Session(self.engine) as db, db.begin():
                execution = self.make_execution()
                db.add(execution)
                db.flush()
                self.assertEqual(db.scalar(select(func.count()).select_from(ExecutionRecord)), 1)
                self.assertEqual(db.scalar(select(func.count()).select_from(ExecutionLocation)), 1)
                execution.locations.append(ExecutionLocation(location_id="MISSING"))
                db.flush()
        self.assertEqual(self.rows(), before)


if __name__ == "__main__":
    unittest.main()