import hashlib
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session

from adopt_intake_database import make_backup
from database import create_db_engine, engine as project_engine
from models import (
    ExecutionLocation, ExecutionRecord, InspectionRecord, PlanInstance,
    PlanLocation, RequestPlanLink, ServiceRequest,
)
from seed_demo_location_hierarchy import seed_demo_location_hierarchy
from verify_plan_link_upgrade import verify


ROOT = Path(__file__).resolve().parents[1]


class PlanLinkUpgradeVerifierTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "case.db"
        self.engine = create_db_engine(self.path)
        self.addCleanup(self.engine.dispose)
        guard = patch.object(project_engine, "connect", side_effect=AssertionError("禁止连接项目库"))
        guard.start()
        self.addCleanup(guard.stop)
        self.migrate("0005_inspection_records")
        seed_demo_location_hierarchy(self.engine)
        now = datetime(2026, 11, 6, 1, tzinfo=UTC)
        with Session(self.engine) as db, db.begin():
            report = ServiceRequest(request_id=uuid4(), service_category="cleaning",
                description=" 合成旧报事 ", source="web", risk_status="unknown")
            plan = PlanInstance(request_id=uuid4(), title="合成保洁", service_category="cleaning",
                requirements="清理大堂", owner_name="合成负责人", planned_start_at=now,
                planned_end_at=now + timedelta(hours=1),
                locations=[PlanLocation(location_id="LOC-A-LOBBY")])
            db.add_all([report, plan])
            db.flush()
            self.report_id, self.plan_id = report.id, plan.id
            db.add_all([
                ExecutionRecord(request_id=uuid4(), plan_id=plan.id, executor_name="合成执行人",
                    description=" 合成旧执行 ", completion_claim="unknown",
                    locations=[ExecutionLocation(location_id="LOC-A-LOBBY")]),
                InspectionRecord(request_id=uuid4(), plan_id=plan.id, inspector_name="合成巡查人",
                    description=" 合成旧巡查 ", finding="unable_to_verify"),
            ])
        self.backup = make_backup(self.path.resolve())

    def migrate(self, target="0006_request_plan_links"):
        config = Config(str(ROOT / "alembic.ini"))
        with self.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, target)

    def fingerprints(self):
        return [hashlib.sha256(path.read_bytes()).hexdigest() for path in (self.backup, self.path)]

    def test_before_after_and_repeat_are_readonly(self):
        original = self.fingerprints()
        verify(self.backup, self.path, before_upgrade=True)
        self.assertEqual(self.fingerprints(), original)
        self.migrate()
        upgraded = self.fingerprints()
        result = verify(self.backup, self.path)
        self.assertEqual(result["current_version"], "0006_request_plan_links")
        self.assertEqual(result["link_counts"], {"request_plan_links": 0})
        self.assertEqual(len(result["counts"]), 7)
        self.assertTrue(all(row["backup"] == row["current"] > 0 for row in result["counts"].values()))
        self.assertEqual(verify(self.backup, self.path), result)
        self.assertEqual(self.fingerprints(), upgraded)
        self.assertEqual(upgraded[0], original[0])

    def test_changed_inspection_and_old_schema_are_rejected(self):
        self.migrate()
        with self.engine.begin() as connection:
            connection.exec_driver_sql("UPDATE inspection_records SET description='被修改'")
        with self.assertRaisesRegex(ValueError, "inspection_records记录不一致"):
            verify(self.backup, self.path)
        with self.engine.begin() as connection:
            connection.exec_driver_sql("CREATE INDEX unexpected_index ON inspection_records(description)")
        with self.assertRaisesRegex(ValueError, "旧表结构"):
            verify(self.backup, self.path)

    def test_wrong_versions_missing_paths_and_same_file_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "当前库版本"):
            verify(self.backup, self.path)
        missing = Path(self.folder.name) / "missing.db"
        with self.assertRaises(FileNotFoundError):
            verify(self.backup, missing)
        self.assertFalse(missing.exists())
        with self.assertRaisesRegex(ValueError, "不同文件"):
            verify(self.path, self.path, before_upgrade=True)
        self.migrate()
        with self.assertRaisesRegex(ValueError, "备份版本"):
            verify(make_backup(self.path.resolve()), self.path)

    def test_existing_links_and_invalid_foreign_keys_are_rejected_readonly(self):
        self.migrate()
        with Session(self.engine) as db, db.begin():
            db.add(RequestPlanLink(request_id=uuid4(), report_id=self.report_id,
                plan_id=self.plan_id, linked_by="合成人员", reason="合成核对"))
        original = self.fingerprints()
        with self.assertRaisesRegex(ValueError, "新关联表应为空"):
            verify(self.backup, self.path)
        self.assertEqual(self.fingerprints(), original)
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("UPDATE request_plan_links SET plan_id=?", (uuid4().hex,))
            connection.commit()
        with self.assertRaisesRegex(ValueError, "无效外键"):
            verify(self.backup, self.path)


if __name__ == "__main__":
    unittest.main()