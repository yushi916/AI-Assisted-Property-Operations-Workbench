import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database import create_db_engine, engine as project_engine
from models import (
    AgentRun, ExecutionLocation, ExecutionRecord, InspectionRecord,
    PlanInstance, PlanLocation, RequestPlanLink, ServiceRequest,
)
from seed_demo_location_hierarchy import seed_demo_location_hierarchy
from adopt_intake_database import schema_signature
from database import Base


ROOT = Path(__file__).resolve().parents[1]
OLD = "0007_agent_runs"
NEW = "0008_floor_locations"
TABLES = (
    "locations", "service_requests", "plan_instances", "plan_locations",
    "execution_records", "execution_locations", "inspection_records",
    "request_plan_links", "agent_runs",
)


class FloorLocationMigrationTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.engine = create_db_engine(Path(folder.name) / "case.db")
        self.addCleanup(self.engine.dispose)

        guard = patch.object(
            project_engine, "connect",
            side_effect=AssertionError("禁止连接业务库"),
        )
        guard.start()
        self.addCleanup(guard.stop)

        self.config = Config(str(ROOT / "alembic.ini"))
        self.migrate(OLD)

    def migrate(self, revision=NEW, action="upgrade"):
        with self.engine.connect() as connection:
            self.config.attributes["connection"] = connection
            try:
                getattr(command, action)(self.config, revision)
            finally:
                self.config.attributes.pop("connection", None)

            self.assertEqual(
                connection.exec_driver_sql(
                    "PRAGMA foreign_keys"
                ).scalar_one(),
                1,
            )

    def rows(self):
        with self.engine.connect() as connection:
            return {
                name: connection.exec_driver_sql(
                    f'SELECT * FROM "{name}" ORDER BY 1, 2'
                ).all()
                for name in TABLES
            }

    def revision(self):
        with self.engine.connect() as connection:
            return connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one()

    def seed_records(self):
        seed_demo_location_hierarchy(self.engine)
        point = "LOC-A-B01-U01-R302"
        now = datetime(2026, 11, 11, 2, tzinfo=UTC)

        with Session(self.engine) as db, db.begin():
            report = ServiceRequest(
                request_id=uuid4(),
                service_category="water_supply",
                description="  厨房和卫生间都没有水。  ",
                source="web", risk_status="unknown",
                location_id=point, observed_at=now, received_at=now,
            )
            plan = PlanInstance(
                request_id=uuid4(), title="1栋供水检查",
                service_category="water_supply",
                requirements="入户检查302室供水。",
                owner_name="王师傅",
                planned_start_at=datetime(2026, 11, 11, 1, tzinfo=UTC),
                planned_end_at=now,
                locations=[PlanLocation(location_id=point)],
            )
            db.add_all([report, plan])
            db.flush()

            db.add_all([
                ExecutionRecord(
                    request_id=uuid4(), plan_id=plan.id,
                    executor_name="王师傅",
                    description="已检查入户阀门。",
                    completion_claim="completed",
                    claimed_finished_at=now, recorded_at=now,
                    locations=[ExecutionLocation(location_id=point)],
                ),
                InspectionRecord(
                    request_id=uuid4(), plan_id=plan.id,
                    inspector_name="李师傅",
                    description="厨房龙头未出水。",
                    finding="issue_observed",
                    location_id=point, observed_at=now, recorded_at=now,
                ),
                RequestPlanLink(
                    request_id=uuid4(), report_id=report.id,
                    plan_id=plan.id, linked_by="值班员",
                    reason="核查同一房间的供水反馈。",
                ),
                AgentRun(
                    id=uuid4(), report_id=report.id,
                    status="pending_review", model_name="test-model",
                    created_at=now, finished_at=now,
                    evidence={"note": "保留原始证据快照"},
                    suggestion={"note": "保留原始待审核草稿"},
                ),
                AgentRun(
                    id=uuid4(), report_id=report.id,
                    status="failed", model_name="test-model",
                    created_at=now, finished_at=now,
                    failure_stage="suggestion_request",
                    error_code="TimeoutError",
                ),
            ])

    def add_floor(self):
        with self.engine.begin() as connection:
            connection.exec_driver_sql(
                "INSERT INTO locations "
                "(id,name,version,kind,parent_id) VALUES (?,?,?,?,?)",
                ("F3", "3层", 1, "floor", "LOC-A-B01-U01"),
            )

    def test_upgrade_and_repeat_preserve_every_field_in_all_nine_tables(self):
        self.seed_records()
        before = self.rows()
        self.assertTrue(all(before.values()))

        self.migrate()
        self.assertEqual(self.revision(), NEW)
        self.assertEqual(self.rows(), before)

        self.migrate()
        self.engine.dispose()
        self.assertEqual(self.rows(), before)

        with self.engine.connect() as connection:
            self.assertEqual(
                connection.exec_driver_sql(
                    "PRAGMA foreign_key_check"
                ).all(),
                [],
            )
            self.assertEqual(
                connection.exec_driver_sql(
                    "PRAGMA integrity_check"
                ).all(),
                [("ok",)],
            )

    def test_floor_insert_and_existing_foreign_keys_work_after_upgrade(self):
        self.seed_records()
        self.migrate()
        self.add_floor()

        with self.engine.begin() as connection:
            connection.exec_driver_sql(
                "INSERT INTO locations "
                "(id,name,version,kind,parent_id) VALUES (?,?,?,?,?)",
                ("R301", "301室", 1, "room", "F3"),
            )

        with self.assertRaises(IntegrityError):
            with self.engine.begin() as connection:
                connection.exec_driver_sql(
                    "DELETE FROM locations "
                    "WHERE id='LOC-A-B01-U01-R302'"
                )

        for kind, parent in (
            ("floor", None),
            ("floor", "MISSING"),
            ("community", "LOC-A"),
            ("invalid", None),
        ):
            with self.subTest(kind=kind, parent=parent):
                with self.assertRaises(IntegrityError):
                    with self.engine.begin() as connection:
                        connection.exec_driver_sql(
                            "INSERT INTO locations "
                            "(id,name,version,kind,parent_id) "
                            "VALUES (?,?,?,?,?)",
                            ("BAD", "不应保存", 1, kind, parent),
                        )

    def test_failure_during_rebuild_rolls_back_rows_schema_and_revision(self):
        self.seed_records()
        before = self.rows()

        with self.engine.connect() as connection:
            schema = connection.exec_driver_sql(
                "SELECT sql FROM sqlite_master ORDER BY type,name"
            ).all()

        def fail_rename(
            connection, cursor, statement, parameters, context, many,
        ):
            if statement.lstrip().upper().startswith(
                "ALTER TABLE _ALEMBIC_TMP_LOCATIONS"
            ):
                raise RuntimeError("模拟迁移中断")

        event.listen(self.engine, "before_cursor_execute", fail_rename)
        try:
            with self.assertRaisesRegex(RuntimeError, "模拟迁移中断"):
                self.migrate()
        finally:
            event.remove(self.engine, "before_cursor_execute", fail_rename)

        self.assertEqual(self.revision(), OLD)
        self.assertEqual(self.rows(), before)

        with self.engine.connect() as connection:
            self.assertEqual(
                connection.exec_driver_sql(
                    "PRAGMA foreign_keys"
                ).scalar_one(),
                1,
            )
            self.assertEqual(
                connection.exec_driver_sql(
                    "SELECT sql FROM sqlite_master ORDER BY type,name"
                ).all(),
                schema,
            )

    def test_downgrade_without_floors_preserves_all_records(self):
        self.seed_records()
        before = self.rows()
        self.migrate()
        self.migrate(OLD, action="downgrade")

        self.assertEqual(self.revision(), OLD)
        self.assertEqual(self.rows(), before)

    def test_downgrade_refuses_to_discard_floor_locations(self):
        self.seed_records()
        self.migrate()
        self.add_floor()
        before = self.rows()

        with self.assertRaisesRegex(ValueError, "拒绝自动降级"):
            self.migrate(OLD, action="downgrade")

        self.assertEqual(self.revision(), NEW)
        self.assertEqual(self.rows(), before)

    def test_fresh_database_upgrades_through_the_whole_chain(self):
        fresh = create_db_engine(
            Path(self.engine.url.database + ".fresh")
        )
        try:
            with fresh.connect() as connection:
                self.config.attributes["connection"] = connection
                try:
                    command.upgrade(self.config, NEW)
                finally:
                    self.config.attributes.pop("connection", None)

                self.assertEqual(
                    connection.exec_driver_sql(
                        "PRAGMA foreign_keys"
                    ).scalar_one(),
                    1,
                )
                self.assertEqual(
                    connection.exec_driver_sql(
                        "SELECT version_num FROM alembic_version"
                    ).scalar_one(),
                    NEW,
                )
                self.assertEqual(
                    connection.exec_driver_sql(
                        "PRAGMA foreign_key_check"
                    ).all(),
                    [],
                )
        finally:
            fresh.dispose()

    def test_supplied_active_transaction_with_foreign_keys_is_refused(self):
        self.seed_records()
        before = self.rows()

        with self.assertRaisesRegex(RuntimeError, "事务开始前"):
            with self.engine.begin() as connection:
                self.config.attributes["connection"] = connection
                try:
                    command.upgrade(self.config, NEW)
                finally:
                    self.config.attributes.pop("connection", None)

        self.assertEqual(self.revision(), OLD)
        self.assertEqual(self.rows(), before)

    def test_existing_invalid_foreign_key_prevents_commit_and_restores_enforcement(self):
        self.seed_records()

        with self.engine.connect() as connection:
            raw = connection.connection.driver_connection
            raw.execute("PRAGMA foreign_keys=OFF")
            raw.execute(
                "UPDATE service_requests SET location_id='MISSING'"
            )
            raw.execute("PRAGMA foreign_keys=ON")

        before = self.rows()

        with self.assertRaisesRegex(ValueError, "外键核对失败"):
            self.migrate()

        self.assertEqual(self.revision(), OLD)
        self.assertEqual(self.rows(), before)

        with self.engine.connect() as connection:
            self.assertEqual(
                connection.exec_driver_sql(
                    "PRAGMA foreign_keys"
                ).scalar_one(),
                1,
            )
    def test_latest_migration_matches_current_orm(self):
        self.migrate()
        reference = create_db_engine(
            Path(self.engine.url.database + ".orm")
        )
        try:
            Base.metadata.create_all(reference)
            with (
                self.engine.connect() as actual,
                reference.connect() as expected,
            ):
                self.assertEqual(
                    schema_signature(actual),
                    schema_signature(expected),
                )
        finally:
            reference.dispose()

    def test_upgrade_from_0006_keeps_old_rows_and_adds_empty_agent_table(self):
        self.seed_records()

        # 仅操作测试创建的临时库，准备一个有业务记录的0006版本。
        with self.engine.begin() as connection:
            connection.exec_driver_sql("DELETE FROM agent_runs")
        self.migrate("0006_request_plan_links", action="downgrade")

        with self.engine.connect() as connection:
            before = {
                name: connection.exec_driver_sql(
                    f'SELECT * FROM "{name}" ORDER BY 1, 2'
                ).all()
                for name in TABLES
                if name != "agent_runs"
            }

        self.migrate()
        after = self.rows()

        self.assertEqual(after.pop("agent_runs"), [])
        self.assertEqual(after, before)
        self.assertEqual(self.revision(), NEW)

if __name__ == "__main__":
    unittest.main()