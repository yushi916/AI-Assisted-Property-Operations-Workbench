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
from models import (
    ExecutionLocation, ExecutionRecord, PlanInstance, PlanLocation, ServiceRequest,
)
from seed_demo_location_hierarchy import seed_demo_location_hierarchy


ROOT = Path(__file__).resolve().parents[1]
OLD = (
    "locations", "service_requests", "plan_instances", "plan_locations",
    "execution_records", "execution_locations",
)
ALL = OLD + ("inspection_records",)


class InspectionMigrationTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.engine = create_db_engine(Path(self.folder.name) / "case.db")
        self.config = Config(str(ROOT / "alembic.ini"))
        guard = patch.object(
            project_engine, "connect",
            side_effect=AssertionError("巡查迁移测试禁止连接项目数据库"),
        )
        guard.start()
        self.addCleanup(guard.stop)

    def tearDown(self):
        self.engine.dispose()
        self.folder.cleanup()

    def migrate(self, target="0005_inspection_records", action="upgrade"):
        with self.engine.begin() as connection:
            self.config.attributes["connection"] = connection
            try:
                getattr(command, action)(self.config, target)
            finally:
                self.config.attributes.pop("connection", None)

    def heads(self):
        with self.engine.connect() as connection:
            return MigrationContext.configure(connection).get_current_heads()

    def rows(self, tables=ALL):
        with self.engine.connect() as connection:
            return {
                table: connection.exec_driver_sql(
                    f'SELECT * FROM "{table}" ORDER BY 1, 2'
                ).all()
                for table in tables
            }

    def old_schema(self):
        marks = ", ".join("?" for _ in OLD)
        with self.engine.connect() as connection:
            return connection.exec_driver_sql(
                "SELECT type, name, tbl_name, sql FROM sqlite_master "
                f"WHERE tbl_name IN ({marks}) ORDER BY type, name",
                OLD,
            ).all()

    def prepare_old_database(self):
        self.migrate("0004_execution_records")
        seed_demo_location_hierarchy(self.engine)
        with Session(self.engine) as db, db.begin():
            db.add(ServiceRequest(
                request_id=uuid4(), service_category="cleaning",
                description="  合成旧报事原文  ", source="web", risk_status="unknown",
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
            self.plan_id = plan.id.hex
            db.add(ExecutionRecord(
                request_id=uuid4(), plan_id=plan.id,
                executor_name="合成执行人", description="  仅A大堂声称完成  ",
                completion_claim="completed",
                claimed_finished_at=datetime(2026, 11, 1, 1, 20, tzinfo=UTC),
                recorded_at=datetime(2026, 11, 1, 1, 25, tzinfo=UTC),
                locations=[ExecutionLocation(location_id="LOC-A-LOBBY")],
            ))

    def insert_inspection(self, connection, **changes):
        values = dict(
            id=uuid4().hex, request_id=uuid4().hex, plan_id=self.plan_id,
            inspector_name="合成巡查员", description="  合成巡查原文  ",
            finding="issue_observed", location_id="LOC-A-LOBBY",
            observed_at="2026-11-01 01:50:00.000000",
            recorded_at="2026-11-01 02:00:00.000000", version=1,
        )
        values.update(changes)
        connection.exec_driver_sql("""
            INSERT INTO inspection_records (
                id, request_id, plan_id, inspector_name, description, finding,
                location_id, observed_at, recorded_at, version
            ) VALUES (
                :id, :request_id, :plan_id, :inspector_name, :description, :finding,
                :location_id, :observed_at, :recorded_at, :version
            )
        """, values)
        return values

    def test_fresh_database_has_independent_table_and_constraints(self):
        self.migrate()
        self.assertEqual(self.heads(), ("0005_inspection_records",))
        info = inspect(self.engine)
        self.assertEqual(set(info.get_table_names()), set(ALL) | {"alembic_version"})
        self.assertEqual(
            {item["referred_table"] for item in info.get_foreign_keys("inspection_records")},
            {"plan_instances", "locations"},
        )
        columns = {item["name"]: item for item in info.get_columns("inspection_records")}
        self.assertTrue(columns["location_id"]["nullable"])
        self.assertTrue(columns["observed_at"]["nullable"])
        self.assertIn("ix_inspection_records_plan_id",
                      {item["name"] for item in info.get_indexes("inspection_records")})
        self.assertEqual(len(info.get_check_constraints("inspection_records")), 5)

    def test_upgrade_repeat_and_reopen_preserve_all_old_rows_and_schema(self):
        self.prepare_old_database()
        before, schema = self.rows(OLD), self.old_schema()
        self.migrate()
        self.migrate()
        self.engine.dispose()
        self.assertEqual(self.heads(), ("0005_inspection_records",))
        self.assertEqual(self.rows(OLD), before)
        self.assertEqual(self.old_schema(), schema)
        self.assertEqual(self.rows()["inspection_records"], [])

    def test_known_and_unknown_observations_preserve_values(self):
        self.prepare_old_database()
        before = self.rows(OLD)
        self.migrate()
        with self.engine.begin() as connection:
            for point, time in (
                ("LOC-A-LOBBY", "2026-11-01 01:10:00.000000"),
                ("LOC-B-LOBBY", "2026-11-01 01:50:00.000000"),
            ):
                self.insert_inspection(connection, location_id=point, observed_at=time)
            for finding in ("no_issue_observed", "issue_observed", "unable_to_verify"):
                self.insert_inspection(
                    connection, finding=finding, location_id=None, observed_at=None,
                )
        with self.engine.connect() as connection:
            rows = connection.exec_driver_sql(
                "SELECT finding, location_id, observed_at, description "
                "FROM inspection_records"
            ).all()
        self.assertEqual(len(rows), 5)
        self.assertEqual(sum(row[1] is None and row[2] is None for row in rows), 3)
        self.assertTrue(all(row[3] == "  合成巡查原文  " for row in rows))
        self.assertEqual(
            {(row[1], row[2]) for row in rows if row[1] is not None},
            {("LOC-A-LOBBY", "2026-11-01 01:10:00.000000"),
             ("LOC-B-LOBBY", "2026-11-01 01:50:00.000000")},
        )
        self.assertEqual(self.rows(OLD), before)

    def test_invalid_values_and_duplicate_ids_roll_back(self):
        self.prepare_old_database()
        self.migrate()
        with self.engine.begin() as connection:
            saved = self.insert_inspection(connection)
        before = self.rows()
        for changes in (
            {"id": saved["id"]}, {"request_id": saved["request_id"]},
            {"plan_id": uuid4().hex}, {"location_id": "MISSING"},
            {"inspector_name": ""}, {"inspector_name": "x" * 121},
            {"description": ""}, {"description": "x" * 2001},
            {"finding": "completed"}, {"finding": None}, {"version": 0},
            {"observed_at": "2026-11-01 03:00:00.000000"},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(IntegrityError):
                    with self.engine.begin() as connection:
                        self.insert_inspection(connection, **changes)
                self.assertEqual(self.rows(), before)

    def test_failed_upgrade_rolls_back_new_table_and_keeps_old_version(self):
        self.prepare_old_database()
        with self.engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE INDEX ix_inspection_records_plan_id ON plan_instances(id)"
            )
        before, schema = self.rows(OLD), self.old_schema()
        with self.assertRaises(OperationalError):
            self.migrate()
        self.assertEqual(self.heads(), ("0004_execution_records",))
        self.assertNotIn("inspection_records", inspect(self.engine).get_table_names())
        self.assertEqual(self.rows(OLD), before)
        self.assertEqual(self.old_schema(), schema)

    def test_empty_downgrade_preserves_existing_execution_evidence(self):
        self.prepare_old_database()
        before, schema = self.rows(OLD), self.old_schema()
        self.migrate()
        self.migrate("0004_execution_records", action="downgrade")
        self.assertEqual(self.heads(), ("0004_execution_records",))
        self.assertEqual(self.rows(OLD), before)
        self.assertEqual(self.old_schema(), schema)
        self.assertNotIn("inspection_records", inspect(self.engine).get_table_names())

    def test_downgrade_refuses_to_drop_saved_inspection_evidence(self):
        self.prepare_old_database()
        self.migrate()
        with self.engine.begin() as connection:
            self.insert_inspection(connection)
        before = self.rows()
        with self.assertRaisesRegex(ValueError, "已有巡查证据"):
            self.migrate("0004_execution_records", action="downgrade")
        self.assertEqual(self.heads(), ("0005_inspection_records",))
        self.assertEqual(self.rows(), before)

    def test_late_insert_failure_rolls_back_earlier_inspection(self):
        self.prepare_old_database()
        self.migrate()
        before = self.rows()
        with self.assertRaises(IntegrityError):
            with self.engine.begin() as connection:
                self.insert_inspection(connection)
                self.assertEqual(connection.exec_driver_sql(
                    "SELECT COUNT(*) FROM inspection_records"
                ).scalar(), 1)
                self.insert_inspection(connection, location_id="MISSING")
        self.assertEqual(self.rows(), before)


if __name__ == "__main__":
    unittest.main()