import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from database import create_db_engine, engine as project_engine
from models import ServiceRequest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.engine = create_db_engine(Path(self.folder.name) / "case.db")
        self.config = Config(str(ROOT / "alembic.ini"))

    def tearDown(self):
        self.engine.dispose()
        self.folder.cleanup()

    def upgrade(self):
        with self.engine.begin() as connection:
            self.config.attributes["connection"] = connection
            try:
                with patch.object(
                    project_engine,
                    "connect",
                    side_effect=AssertionError("迁移测试禁止连接项目数据库"),
                ):
                    command.upgrade(self.config, "0001_intake")
            finally:
                self.config.attributes.pop("connection", None)


    def report(self, **changes):
        values = {
            "request_id": uuid4(),
            "service_category": "cleaning",
            "description": "合成迁移测试：大堂发现垃圾。",
            "source": "web",
            "risk_status": "unknown",
        }
        values.update(changes)
        return ServiceRequest(**values)

    def test_empty_database_can_upgrade_and_store_report(self):
        self.upgrade()
        self.assertEqual(
            set(inspect(self.engine).get_table_names()),
            {"locations", "service_requests", "alembic_version"},
        )
        with self.engine.connect() as connection:
            self.assertEqual(
                connection.scalar(text("SELECT version_num FROM alembic_version")),
                "0001_intake",
            )
        with Session(self.engine) as db, db.begin():
            report = self.report()
            db.add(report)
            db.flush()
            report_id = report.id
        self.engine.dispose()
        with Session(self.engine) as db:
            saved = db.get(ServiceRequest, report_id)
            self.assertEqual(saved.description, "合成迁移测试：大堂发现垃圾。")
            self.assertIsNotNone(saved.received_at.utcoffset())

    def test_migrated_database_enforces_constraints(self):
        self.upgrade()
        request_id = uuid4()
        with Session(self.engine) as db, db.begin():
            db.add(self.report(request_id=request_id))
        for changes in (
            {"request_id": request_id},
            {"location_id": "MISSING"},
            {"version": 0},
            {"description": ""},
            {"service_category": "invalid"},
            {"source": "invalid"},
            {"risk_status": "invalid"},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(IntegrityError):
                    with Session(self.engine) as db, db.begin():
                        db.add(self.report(**changes))
        with Session(self.engine) as db:
            self.assertEqual(
                db.scalar(select(func.count()).select_from(ServiceRequest)),
                1,
            )

    def test_existing_database_is_not_overwritten(self):
        self.upgrade()
        with self.engine.begin() as connection:
            connection.exec_driver_sql("DROP TABLE alembic_version")
        with Session(self.engine) as db, db.begin():
            report = self.report()
            db.add(report)
            db.flush()
            report_id = report.id
        with self.assertRaisesRegex(
            OperationalError, "table locations already exists"
        ):
            self.upgrade()
        self.assertEqual(
            set(inspect(self.engine).get_table_names()),
            {"locations", "service_requests"},
        )
        with Session(self.engine) as db:
            self.assertEqual(
                db.get(ServiceRequest, report_id).description,
                "合成迁移测试：大堂发现垃圾。",
            )

    def test_failed_upgrade_rolls_back_created_tables(self):
        with self.engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE service_requests (id INTEGER PRIMARY KEY)"
            )
            connection.exec_driver_sql("INSERT INTO service_requests VALUES (7)")
        with self.assertRaisesRegex(
            OperationalError, "table service_requests already exists"
        ):
            self.upgrade()
        self.assertEqual(
            set(inspect(self.engine).get_table_names()), {"service_requests"}
        )
        with self.engine.connect() as connection:
            self.assertEqual(
                connection.scalar(text("SELECT id FROM service_requests")), 7
            )


if __name__ == "__main__":
    unittest.main()