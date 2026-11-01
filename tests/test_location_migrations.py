import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import event, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database import create_db_engine, engine as project_engine
from models import Location


ROOT = Path(__file__).resolve().parents[1]
TABLES = ("locations", "service_requests", "plan_instances", "plan_locations")


class LocationMigrationTests(unittest.TestCase):
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

    def migrate(self, target="head", action="upgrade"):
        with self.engine.begin() as connection:
            self.config.attributes["connection"] = connection
            try:
                getattr(command, action)(self.config, target)
            finally:
                self.config.attributes.pop("connection", None)

    def heads(self):
        with self.engine.connect() as connection:
            return MigrationContext.configure(connection).get_current_heads()

    def rows(self, full=False):
        with self.engine.connect() as connection:
            result = {}
            for table in TABLES:
                columns = "id, name, version" if table == "locations" and not full else "*"
                result[table] = connection.exec_driver_sql(
                    f'SELECT {columns} FROM "{table}" ORDER BY 1, 2'
                ).all()
            return result

    def seed_old_database(self):
        self.migrate("0002_plans")
        with self.engine.begin() as connection:
            connection.exec_driver_sql(
                "INSERT INTO locations (id, name, version) VALUES (?, ?, ?)",
                [("LOC-A", "  合成A区大堂  ", 7), ("LOC-B", "合成B区大堂", 2)],
            )
            connection.exec_driver_sql("""
                INSERT INTO service_requests (
                    id, request_id, service_category, description, source,
                    risk_status, location_id, observed_at, received_at, version
                ) VALUES (?, ?, 'cleaning', ?, 'web', 'unknown', ?, NULL,
                          '2026-10-31 01:00:00.000000', 3)
            """, [
                (uuid4().hex, uuid4().hex, "  合成原始报事  ", "LOC-A"),
                (uuid4().hex, uuid4().hex, "合成地点未知报事", None),
            ])
            plan_id = uuid4().hex
            connection.exec_driver_sql("""
                INSERT INTO plan_instances (
                    id, request_id, title, service_category, plan_kind,
                    requirements, owner_name, planned_start_at, planned_end_at,
                    created_at, version
                ) VALUES (?, ?, '合成旧计划', 'cleaning', 'routine',
                          '保留原计划要求', '合成负责人',
                          '2026-11-01 01:00:00.000000',
                          '2026-11-01 01:30:00.000000',
                          '2026-10-31 01:00:00.000000', 4)
            """, (plan_id, uuid4().hex))
            connection.exec_driver_sql(
                "INSERT INTO plan_locations (plan_id, location_id) VALUES (?, ?)",
                [(plan_id, "LOC-A"), (plan_id, "LOC-B")],
            )

    def test_fresh_database_adds_columns_and_keeps_old_style_inserts_working(self):
        self.migrate()
        self.assertEqual(self.heads(), ("0003_location_hierarchy",))
        self.assertEqual(
            [c["name"] for c in inspect(self.engine).get_columns("locations")],
            ["id", "name", "version", "parent_id", "kind"],
        )
        with Session(self.engine) as db, db.begin():
            db.add(Location(id="LOC-OLD-STYLE", name="合成既有写法"))
        with self.engine.connect() as connection:
            self.assertEqual(
                tuple(connection.exec_driver_sql(
                    "SELECT kind, parent_id, version FROM locations"
                ).one()),
                ("unknown", None, 1),
            )

    def test_populated_upgrade_and_repeat_preserve_every_old_field(self):
        self.seed_old_database()
        before = self.rows()
        self.migrate()
        self.assertEqual(self.rows(), before)
        with self.engine.connect() as connection:
            self.assertEqual(
                connection.exec_driver_sql(
                    "SELECT DISTINCT kind, parent_id FROM locations"
                ).all(),
                [("unknown", None)],
            )
            self.assertEqual(connection.exec_driver_sql("PRAGMA foreign_keys").scalar(), 1)
            self.assertEqual(connection.exec_driver_sql("PRAGMA foreign_key_check").all(), [])
        complete = self.rows(full=True)
        self.migrate()
        self.engine.dispose()
        self.assertEqual(self.heads(), ("0003_location_hierarchy",))
        self.assertEqual(self.rows(full=True), complete)

    def test_migrated_constraints_reject_invalid_writes_without_changes(self):
        self.seed_old_database()
        self.migrate()
        with self.engine.begin() as connection:
            connection.exec_driver_sql(
                "INSERT INTO locations (id, name, kind, version) VALUES (?, ?, ?, 1)",
                ("COMMUNITY", "合成小区", "community"),
            )
            connection.exec_driver_sql("""
                INSERT INTO locations (id, name, kind, parent_id, version)
                VALUES ('PUBLIC', '合成公共点位', 'public_space', 'COMMUNITY', 1)
            """)
        before = self.rows(full=True)
        for kind, parent_id, version in (
            ("invalid", None, 1), ("room", None, 1),
            ("community", "COMMUNITY", 1), ("unknown", "COMMUNITY", 1),
            ("room", "BAD", 1), ("room", "MISSING", 1),
            ("unknown", None, 0),
        ):
            with self.subTest(kind=kind, parent_id=parent_id, version=version):
                with self.assertRaises(IntegrityError):
                    with self.engine.begin() as connection:
                        connection.exec_driver_sql("""
                            INSERT INTO locations (id, name, kind, parent_id, version)
                            VALUES ('BAD', '不应保存', ?, ?, ?)
                        """, (kind, parent_id, version))
                self.assertEqual(self.rows(full=True), before)
        with self.assertRaisesRegex(IntegrityError, "FOREIGN KEY constraint failed"):
            with self.engine.begin() as connection:
                connection.exec_driver_sql("DELETE FROM locations WHERE id='COMMUNITY'")
        self.assertEqual(self.rows(full=True), before)

    def test_failure_after_first_column_rolls_back_schema_rows_and_version(self):
        self.seed_old_database()
        before = self.rows()

        def fail_before_kind(connection, cursor, statement, parameters, context, many):
            if "ADD COLUMN kind" in statement:
                raise RuntimeError("合成迁移中途失败")

        event.listen(self.engine, "before_cursor_execute", fail_before_kind)
        try:
            with self.assertRaisesRegex(RuntimeError, "迁移中途失败"):
                self.migrate()
        finally:
            event.remove(self.engine, "before_cursor_execute", fail_before_kind)
        self.assertEqual(self.heads(), ("0002_plans",))
        self.assertEqual(self.rows(), before)
        self.assertEqual(
            [c["name"] for c in inspect(self.engine).get_columns("locations")],
            ["id", "name", "version"],
        )

    def test_unused_hierarchy_can_downgrade_without_changing_old_rows(self):
        self.seed_old_database()
        before = self.rows()
        self.migrate()
        self.migrate("0002_plans", action="downgrade")
        self.assertEqual(self.heads(), ("0002_plans",))
        self.assertEqual(self.rows(), before)
        self.assertEqual(
            [c["name"] for c in inspect(self.engine).get_columns("locations")],
            ["id", "name", "version"],
        )

    def test_downgrade_refuses_to_discard_assigned_hierarchy(self):
        self.seed_old_database()
        self.migrate()
        with self.engine.begin() as connection:
            connection.exec_driver_sql(
                "UPDATE locations SET kind='community', version=version+1 WHERE id='LOC-A'"
            )
        before = self.rows(full=True)
        with self.assertRaisesRegex(ValueError, "拒绝自动降级"):
            self.migrate("0002_plans", action="downgrade")
        self.assertEqual(self.heads(), ("0003_location_hierarchy",))
        self.assertEqual(self.rows(full=True), before)


if __name__ == "__main__":
    unittest.main()