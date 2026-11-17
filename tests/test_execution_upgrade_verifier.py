import hashlib
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session

from adopt_intake_database import make_backup
from database import create_db_engine, engine as project_engine
from models import ExecutionRecord, PlanInstance, PlanLocation, ServiceRequest
from seed_demo_location_hierarchy import seed_demo_location_hierarchy
from verify_execution_upgrade import verify


ROOT = Path(__file__).resolve().parents[1]


class ExecutionUpgradeVerifierTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / "case.db"
        self.engine = create_db_engine(self.path)
        guard = patch.object(
            project_engine, "connect",
            side_effect=AssertionError("核对测试禁止连接项目数据库"),
        )
        guard.start()
        self.addCleanup(guard.stop)
        self.migrate("0003_location_hierarchy")
        seed_demo_location_hierarchy(self.engine)
        with Session(self.engine) as db, db.begin():
            db.add(ServiceRequest(
                request_id=uuid4(), service_category="cleaning",
                description="  合成旧报事原文  ", source="web", risk_status="unknown",
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
        self.backup = make_backup(self.path.resolve())

    def tearDown(self):
        self.engine.dispose()
        self.folder.cleanup()

    def migrate(self, target="0004_execution_records"):
        config = Config(str(ROOT / "alembic.ini"))
        with self.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, target)

    def fingerprints(self):
        return [hashlib.sha256(path.read_bytes()).hexdigest() for path in (self.backup, self.path)]

    def restore_temporary_current(self):
        self.engine.dispose()
        with closing(sqlite3.connect(self.backup.resolve().as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(self.path)) as target:
                source.backup(target)

    def test_before_after_and_repeat_are_readonly_and_preserve_all_old_rows(self):
        original = self.fingerprints()
        before = verify(self.backup, self.path, before_upgrade=True)
        self.assertEqual(before["stage"], "before_upgrade")
        self.assertEqual(self.fingerprints(), original)
        self.migrate()
        upgraded = self.fingerprints()
        after = verify(self.backup, self.path)
        self.assertEqual(after["current_version"], "0004_execution_records")
        self.assertEqual(after["counts"], {
            "locations": {"backup": 12, "current": 12},
            "service_requests": {"backup": 1, "current": 1},
            "plan_instances": {"backup": 1, "current": 1},
            "plan_locations": {"backup": 2, "current": 2},
        })
        self.assertEqual(after["execution_counts"], {"execution_records": 0, "execution_locations": 0})
        self.assertEqual(verify(self.backup, self.path), after)
        self.assertEqual(self.fingerprints(), upgraded)
        self.assertEqual(upgraded[0], original[0])

    def test_changed_fields_deleted_rows_and_schema_drift_are_rejected(self):
        changes = (
            "UPDATE locations SET version=version+1 WHERE id='LOC-A-LOBBY'",
            "UPDATE locations SET parent_id='LOC-B' WHERE id='LOC-A-LOBBY'",
            "UPDATE service_requests SET description=trim(description)",
            "UPDATE plan_instances SET owner_name='已被修改'",
            "UPDATE plan_locations SET location_id='LOC-A-COURTYARD' WHERE location_id='LOC-B-LOBBY'",
            "DELETE FROM service_requests",
            "CREATE INDEX unexpected_owner_index ON plan_instances(owner_name)",
        )
        for statement in changes:
            with self.subTest(statement=statement):
                self.restore_temporary_current()
                self.migrate()
                with self.engine.begin() as connection:
                    connection.exec_driver_sql(statement)
                changed = self.fingerprints()
                with self.assertRaisesRegex(ValueError, "记录不一致|旧表结构"):
                    verify(self.backup, self.path)
                self.assertEqual(self.fingerprints(), changed)

    def test_wrong_versions_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "当前库版本"):
            verify(self.backup, self.path)
        self.migrate()
        with self.assertRaisesRegex(ValueError, "当前库版本"):
            verify(self.backup, self.path, before_upgrade=True)
        wrong_backup = make_backup(self.path.resolve())
        with self.assertRaisesRegex(ValueError, "备份版本"):
            verify(wrong_backup, self.path)

    def test_existing_unscoped_execution_is_rejected_without_deleting_it(self):
        self.migrate()
        with Session(self.engine) as db, db.begin():
            db.add(ExecutionRecord(
                request_id=uuid4(), plan_id=self.plan_id,
                executor_name="合成执行人", description="合成：点位未知的已有证据",
                completion_claim="unknown",
            ))
        original = self.fingerprints()
        with self.assertRaisesRegex(ValueError, "新执行表应为空"):
            verify(self.backup, self.path)
        self.assertEqual(self.fingerprints(), original)

    def test_invalid_foreign_key_is_detected_readonly(self):
        self.migrate()
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("UPDATE service_requests SET location_id='MISSING'")
            connection.commit()
        original = self.fingerprints()
        with self.assertRaisesRegex(ValueError, "无效外键"):
            verify(self.backup, self.path)
        self.assertEqual(self.fingerprints(), original)

    def test_missing_paths_and_same_file_are_rejected_without_creating_database(self):
        missing = Path(self.folder.name) / "missing.db"
        for backup, current in ((missing, self.path), (self.backup, missing)):
            with self.subTest(backup=backup, current=current):
                with self.assertRaises(FileNotFoundError):
                    verify(backup, current, before_upgrade=True)
                self.assertFalse(missing.exists())
        with self.assertRaisesRegex(ValueError, "不同文件"):
            verify(self.path, self.path, before_upgrade=True)


if __name__ == "__main__":
    unittest.main()