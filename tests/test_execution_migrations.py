import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from database import create_db_engine, engine as project_engine
from models import PlanInstance, PlanLocation, ServiceRequest
from seed_demo_location_hierarchy import seed_demo_location_hierarchy


ROOT = Path(__file__).resolve().parents[1]
OLD = ("locations", "service_requests", "plan_instances", "plan_locations")
NEW = ("execution_records", "execution_locations")


class ExecutionMigrationTests(unittest.TestCase):
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

    def migrate(self, target="0004_execution_records", action="upgrade"):
        with self.engine.begin() as connection:
            self.config.attributes["connection"] = connection
            try:
                getattr(command, action)(self.config, target)
            finally:
                self.config.attributes.pop("connection", None)

    def heads(self):
        with self.engine.connect() as connection:
            return MigrationContext.configure(connection).get_current_heads()

    def rows(self, tables=OLD):
        with self.engine.connect() as connection:
            return {
                table: connection.exec_driver_sql(
                    f'SELECT * FROM "{table}" ORDER BY 1, 2'
                ).all()
                for table in tables
            }

    def prepare_old_database(self):
        self.migrate("0003_location_hierarchy")
        seed_demo_location_hierarchy(self.engine)
        with Session(self.engine) as db, db.begin():
            db.add(ServiceRequest(
                request_id=uuid4(), service_category="cleaning",
                description="  合成旧报事原文  ", source="web", risk_status="unknown",
                location_id="LOC-A-LOBBY",
            ))
            plan = PlanInstance(
                request_id=uuid4(), title="合成两处大堂保洁",
                service_category="cleaning", requirements="分别核对A、B大堂",
                owner_name="合成负责人",
                planned_start_at=datetime(2026, 11, 1, 1, tzinfo=UTC),
                planned_end_at=datetime(2026, 11, 1, 2, tzinfo=UTC),
                locations=[PlanLocation(location_id=key) for key in ("LOC-A-LOBBY", "LOC-B-LOBBY")],
            )
            db.add(plan)
            db.flush()
            self.plan_id = plan.id.hex

    def insert_execution(self, connection, **changes):
        values = dict(
            id=uuid4().hex, request_id=uuid4().hex, plan_id=self.plan_id,
            executor_name="合成执行人", description="  合成执行声称  ",
            completion_claim="completed", version=1,
            claimed_started_at="2026-11-01 01:00:00.000000",
            claimed_finished_at="2026-11-01 01:20:00.000000",
            recorded_at="2026-11-01 02:00:00.000000",
        )
        values.update(changes)
        connection.exec_driver_sql("""
            INSERT INTO execution_records (
                id, request_id, plan_id, executor_name, description, completion_claim,
                claimed_started_at, claimed_finished_at, recorded_at, version
            ) VALUES (:id, :request_id, :plan_id, :executor_name, :description, :completion_claim,
                      :claimed_started_at, :claimed_finished_at, :recorded_at, :version)
        """, values)
        return values["id"]

    def test_fresh_database_has_new_tables_foreign_keys_and_plan_index(self):
        self.migrate()
        self.assertEqual(self.heads(), ("0004_execution_records",))
        info = inspect(self.engine)
        self.assertEqual(set(info.get_table_names()), set(OLD + NEW) | {"alembic_version"})
        self.assertEqual(len(info.get_foreign_keys("execution_records")), 1)
        self.assertEqual(len(info.get_foreign_keys("execution_locations")), 2)
        self.assertEqual(
            info.get_pk_constraint("execution_locations")["constrained_columns"],
            ["execution_id", "location_id"],
        )
        self.assertIn("ix_execution_records_plan_id", {index["name"] for index in info.get_indexes("execution_records")})

    def test_upgrade_repeat_and_reopen_preserve_every_old_field(self):
        self.prepare_old_database()
        before = self.rows()
        self.migrate()
        self.assertEqual(self.rows(), before)
        self.assertEqual(self.rows(NEW), {table: [] for table in NEW})
        complete = self.rows(OLD + NEW)
        self.migrate()
        self.engine.dispose()
        self.assertEqual(self.heads(), ("0004_execution_records",))
        self.assertEqual(self.rows(OLD + NEW), complete)

    def test_record_and_point_constraints_reject_invalid_writes(self):
        self.prepare_old_database()
        self.migrate()
        request_id = uuid4().hex
        with self.engine.begin() as connection:
            execution_id = self.insert_execution(connection, request_id=request_id)
            connection.exec_driver_sql("INSERT INTO execution_locations VALUES (?, ?)", (execution_id, "LOC-A-LOBBY"))
        before = self.rows(OLD + NEW)
        for changes in (
            {"request_id": request_id}, {"plan_id": uuid4().hex}, {"version": 0},
            {"executor_name": ""}, {"executor_name": "x" * 121},
            {"description": ""}, {"description": "x" * 2001},
            {"completion_claim": "invalid"}, {"completion_claim": "partially_completed"},
            {"claimed_finished_at": "2026-11-01 00:50:00.000000"},
            {"claimed_started_at": "2026-11-01 03:00:00.000000", "claimed_finished_at": None},
            {"claimed_finished_at": "2026-11-01 03:00:00.000000"},
            {"recorded_at": None},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(IntegrityError):
                    with self.engine.begin() as connection:
                        self.insert_execution(connection, **changes)
                self.assertEqual(self.rows(OLD + NEW), before)
        for key, location in (
            (execution_id, "LOC-A-LOBBY"), (execution_id, "MISSING"), (uuid4().hex, "LOC-A-LOBBY"),
        ):
            with self.subTest(execution=key, location=location):
                with self.assertRaises(IntegrityError):
                    with self.engine.begin() as connection:
                        connection.exec_driver_sql("INSERT INTO execution_locations VALUES (?, ?)", (key, location))
                self.assertEqual(self.rows(OLD + NEW), before)

    def test_unknown_times_and_scope_remain_unknown(self):
        self.prepare_old_database()
        self.migrate()
        before = self.rows()
        claims = {"completed", "partially_completed", "not_completed", "unknown"}
        with self.engine.begin() as connection:
            for claim in claims:
                self.insert_execution(connection, completion_claim=claim, claimed_started_at=None, claimed_finished_at=None)
        with self.engine.connect() as connection:
            records = connection.exec_driver_sql("SELECT completion_claim, claimed_started_at, claimed_finished_at FROM execution_records").all()
            self.assertEqual({row[0] for row in records}, claims)
            self.assertTrue(all(tuple(row[1:]) == (None, None) for row in records))
        self.assertEqual(self.rows(NEW)["execution_locations"], [])
        self.assertEqual(self.rows(), before)

    def test_late_point_failure_rolls_back_record_and_earlier_point(self):
        self.prepare_old_database()
        self.migrate()
        before = self.rows(OLD + NEW)
        with self.assertRaises(IntegrityError):
            with self.engine.begin() as connection:
                key = self.insert_execution(connection)
                connection.exec_driver_sql("INSERT INTO execution_locations VALUES (?, ?)", (key, "LOC-A-LOBBY"))
                self.assertEqual(connection.exec_driver_sql("SELECT COUNT(*) FROM execution_records").scalar(), 1)
                connection.exec_driver_sql("INSERT INTO execution_locations VALUES (?, ?)", (key, "MISSING"))
        self.assertEqual(self.rows(OLD + NEW), before)

    def test_failed_upgrade_rolls_back_created_table_index_and_version(self):
        self.prepare_old_database()
        before = self.rows()
        with self.engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE execution_locations (marker INTEGER PRIMARY KEY)")
            connection.exec_driver_sql("INSERT INTO execution_locations VALUES (7)")
        with self.assertRaisesRegex(OperationalError, "already exists"):
            self.migrate()
        self.assertEqual(self.heads(), ("0003_location_hierarchy",))
        self.assertNotIn("execution_records", inspect(self.engine).get_table_names())
        self.assertEqual(self.rows(), before)
        with self.engine.connect() as connection:
            self.assertEqual(connection.exec_driver_sql("SELECT marker FROM execution_locations").scalar(), 7)
            self.assertEqual(connection.exec_driver_sql("SELECT COUNT(*) FROM sqlite_master WHERE name='ix_execution_records_plan_id'").scalar(), 0)

    def test_empty_execution_tables_can_downgrade_without_changing_old_rows(self):
        self.prepare_old_database()
        before = self.rows()
        self.migrate()
        self.migrate("0003_location_hierarchy", action="downgrade")
        self.assertEqual(self.heads(), ("0003_location_hierarchy",))
        self.assertEqual(set(inspect(self.engine).get_table_names()), set(OLD) | {"alembic_version"})
        self.assertEqual(self.rows(), before)

    def test_downgrade_refuses_to_discard_even_unscoped_execution_evidence(self):
        self.prepare_old_database()
        self.migrate()
        with self.engine.begin() as connection:
            key = self.insert_execution(connection, claimed_started_at=None, claimed_finished_at=None)
        for with_point in (False, True):
            with self.subTest(with_point=with_point):
                if with_point:
                    with self.engine.begin() as connection:
                        connection.exec_driver_sql("INSERT INTO execution_locations VALUES (?, ?)", (key, "LOC-A-LOBBY"))
                before = self.rows(OLD + NEW)
                with self.assertRaisesRegex(ValueError, "已有执行证据"):
                    self.migrate("0003_location_hierarchy", action="downgrade")
                self.assertEqual(self.heads(), ("0004_execution_records",))
                self.assertEqual(self.rows(OLD + NEW), before)


if __name__ == "__main__":
    unittest.main()