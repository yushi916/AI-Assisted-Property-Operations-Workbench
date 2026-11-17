import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from alembic.runtime.migration import MigrationContext
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, MetaData, UniqueConstraint
from sqlalchemy.orm import Session

import adopt_intake_database as adoption
from database import create_db_engine, engine as project_engine
from models import ServiceRequest


class DatabaseAdoptionTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / "case.db"
        self.guard = patch.object(
            project_engine, "connect",
            side_effect=AssertionError("接管测试禁止连接项目数据库"),
        )
        self.guard.start()
        self.addCleanup(self.guard.stop)
        self.engine = create_db_engine(self.path)

        with self.engine.begin() as connection:
            adoption.run_alembic(connection, "upgrade")
            connection.exec_driver_sql("DROP TABLE alembic_version")
            connection.exec_driver_sql(
                "INSERT INTO locations (id, name, version) VALUES (?, ?, ?)",
                ("LOC-A", "合成A区大堂", 1),
            )

        with Session(self.engine) as db, db.begin():
            for description, location_id in (
                ("  合成报事一：保留空格。", "LOC-A"),
                ("合成报事二：位置未知。", None),
            ):
                db.add(ServiceRequest(
                    request_id=uuid4(), service_category="cleaning",
                    description=description, source="web", risk_status="unknown",
                    location_id=location_id,
                ))

    def tearDown(self):
        self.engine.dispose()
        self.folder.cleanup()

    def snapshot(self):
        with self.engine.connect() as connection:
            return adoption.read_rows(connection)

    def heads(self):
        with self.engine.connect() as connection:
            return MigrationContext.configure(connection).get_current_heads()

    def test_check_is_read_only(self):
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        result = adoption.adopt_database(self.path)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["counts"], {"locations": 1, "service_requests": 2})
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)
        self.assertFalse((self.path.parent / "backups").exists())

    def test_adoption_preserves_all_values_and_backup(self):
        before = self.snapshot()
        result = adoption.adopt_database(self.path, apply=True)
        self.assertEqual(result["status"], "adopted")
        self.assertEqual(self.heads(), ("0001_intake",))
        self.assertEqual(self.snapshot(), before)

        backup_engine = create_db_engine(Path(result["backup"]))
        try:
            with backup_engine.connect() as connection:
                self.assertEqual(adoption.read_rows(connection), before)
                self.assertEqual(
                    MigrationContext.configure(connection).get_current_heads(),
                    (),
                )
        finally:
            backup_engine.dispose()

        repeated = adoption.adopt_database(self.path, apply=True)
        self.assertEqual(repeated["status"], "already_managed")
        self.assertEqual(
            len(list((self.path.parent / "backups").glob("*.db"))),
            1,
        )

    def test_mismatched_schema_is_rejected(self):
        for kind in (CheckConstraint, ForeignKeyConstraint, UniqueConstraint):
            with self.subTest(missing=kind.__name__):
                metadata = MetaData()
                metadata.reflect(bind=self.engine)
                table = metadata.tables["service_requests"]
                for constraint in list(table.constraints):
                    if isinstance(constraint, kind):
                        table.constraints.remove(constraint)

                wrong = self.path.parent / f"{kind.__name__}.db"
                wrong_engine = create_db_engine(wrong)
                try:
                    metadata.create_all(wrong_engine)
                    with self.assertRaisesRegex(ValueError, "结构"):
                        adoption.adopt_database(wrong, apply=True)
                    with wrong_engine.connect() as connection:
                        self.assertEqual(
                            MigrationContext.configure(connection).get_current_heads(),
                            (),
                        )
                finally:
                    wrong_engine.dispose()

        with self.engine.begin() as connection:
            connection.exec_driver_sql("ALTER TABLE locations ADD COLUMN extra TEXT")
        with self.assertRaisesRegex(ValueError, "结构"):
            adoption.adopt_database(self.path, apply=True)
        self.assertEqual(self.heads(), ())

    def test_stamp_failure_rolls_back(self):
        before = self.snapshot()
        original = adoption.run_alembic

        def fail_after_stamp(connection, action):
            original(connection, action)
            if action == "stamp":
                raise RuntimeError("合成登记失败")

        with patch.object(adoption, "run_alembic", side_effect=fail_after_stamp):
            with self.assertRaisesRegex(RuntimeError, "合成登记失败"):
                adoption.adopt_database(self.path, apply=True)

        self.assertEqual(self.heads(), ())
        self.assertEqual(self.snapshot(), before)

    def test_changes_after_backup_prevent_stamp(self):
        original = adoption.make_backup

        def backup_then_change(path):
            backup = original(path)
            with self.engine.begin() as connection:
                connection.exec_driver_sql(
                    "UPDATE locations SET name='合成新名称' WHERE id='LOC-A'"
                )
            return backup

        with patch.object(adoption, "make_backup", side_effect=backup_then_change):
            with self.assertRaisesRegex(ValueError, "备份后数据发生变化"):
                adoption.adopt_database(self.path, apply=True)

        self.assertEqual(self.heads(), ())
        with self.engine.connect() as connection:
            self.assertEqual(
                connection.exec_driver_sql("SELECT name FROM locations").scalar_one(),
                "合成新名称",
            )


if __name__ == "__main__":
    unittest.main()