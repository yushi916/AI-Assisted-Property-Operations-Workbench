import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from database import Base, create_db_engine, engine as project_engine
from location_hierarchy import build_location_paths
from models import Location, PlanInstance, PlanLocation, ServiceRequest
from seed_demo_location_hierarchy import seed_demo_location_hierarchy
from seed_demo_locations import DEMO_LOCATIONS, seed_demo_locations


class LocationHierarchySeedTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.engine = create_db_engine(Path(self.folder.name) / "case.db")
        guard = patch.object(
            project_engine, "connect",
            side_effect=AssertionError("测试禁止连接项目数据库"),
        )
        guard.start()
        self.addCleanup(guard.stop)
        Base.metadata.create_all(self.engine)

    def tearDown(self):
        self.engine.dispose()
        self.folder.cleanup()

    def snapshot(self):
        with self.engine.connect() as connection:
            return {
                table.name: connection.execute(
                    select(table).order_by(*table.primary_key.columns)
                ).all()
                for table in Base.metadata.sorted_tables
            }

    def paths(self):
        with Session(self.engine) as db:
            return build_location_paths(db.scalars(select(Location)))

    def test_fresh_catalog_has_distinct_paths_for_same_room_names(self):
        self.assertEqual(
            seed_demo_location_hierarchy(self.engine),
            {"added": 12, "updated": 0},
        )
        paths = self.paths()
        self.assertEqual(len(paths), 12)
        self.assertEqual(
            paths["LOC-A-B01-U01-R302"],
            "合成小区 / A区 / 1栋 / 1单元 / 302室",
        )
        self.assertEqual(
            paths["LOC-A-B02-U01-R302"],
            "合成小区 / A区 / 2栋 / 1单元 / 302室",
        )

    def test_legacy_upgrade_preserves_names_history_and_plan_scope(self):
        seed_demo_locations(self.engine)
        with Session(self.engine) as db, db.begin():
            db.add(Location(id="OTHER", name="保留的其他合成点位", version=5))
            db.get(Location, "LOC-A-LOBBY").version = 7
            db.add(ServiceRequest(
                request_id=uuid4(), service_category="cleaning",
                description="  合成原始报事  ", source="web",
                risk_status="unknown", location_id="LOC-A-LOBBY",
            ))
            db.add(PlanInstance(
                request_id=uuid4(), title="合成大堂保洁",
                service_category="cleaning", requirements="只清理大堂",
                owner_name="合成负责人",
                planned_start_at=datetime(2026, 11, 1, 1, tzinfo=UTC),
                planned_end_at=datetime(2026, 11, 1, 2, tzinfo=UTC),
                locations=[PlanLocation(location_id="LOC-A-LOBBY")],
            ))
        before = self.snapshot()

        self.assertEqual(
            seed_demo_location_hierarchy(self.engine),
            {"added": 9, "updated": 3},
        )

        after = self.snapshot()
        for table in ("service_requests", "plan_instances", "plan_locations"):
            self.assertEqual(after[table], before[table])
        other_before = next(row for row in before["locations"] if row.id == "OTHER")
        self.assertIn(other_before, after["locations"])
        with Session(self.engine) as db:
            for location_id, name in DEMO_LOCATIONS:
                saved = db.get(Location, location_id)
                self.assertEqual(saved.name, name)
                self.assertEqual(saved.kind, "public_space")
                self.assertEqual(
                    saved.version, 8 if location_id == "LOC-A-LOBBY" else 2,
                )
        self.assertEqual(len(after["locations"]), 13)
        self.assertEqual(len(after["plan_locations"]), 1)

    def test_repeat_and_old_seed_leave_every_field_unchanged(self):
        seed_demo_locations(self.engine)
        seed_demo_location_hierarchy(self.engine)
        before = self.snapshot()
        self.assertEqual(
            seed_demo_location_hierarchy(self.engine),
            {"added": 0, "updated": 0},
        )
        self.assertEqual(seed_demo_locations(self.engine), 0)
        self.assertEqual(self.snapshot(), before)

    def test_late_name_conflict_leaves_every_table_unchanged(self):
        with Session(self.engine) as db, db.begin():
            db.add(Location(id="LOC-B-LOBBY", name="已有其他名称", version=5))
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "地点名称冲突"):
            seed_demo_location_hierarchy(self.engine)
        self.assertEqual(self.snapshot(), before)

    def test_existing_classification_is_not_overwritten(self):
        seed_demo_location_hierarchy(self.engine)
        with Session(self.engine) as db, db.begin():
            db.get(Location, "LOC-A-LOBBY").parent_id = "LOC-DEMO-COMMUNITY"
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "地点分类或归属冲突"):
            seed_demo_location_hierarchy(self.engine)
        self.assertEqual(self.snapshot(), before)

    def test_invalid_existing_graph_prevents_all_new_writes(self):
        with Session(self.engine) as db, db.begin():
            db.add(Location(id="OTHER-C", name="其他合成小区", kind="community"))
            db.flush()
            db.add_all([
                Location(id="OTHER-1", name="1栋", kind="building", parent_id="OTHER-C"),
                Location(id="OTHER-2", name="2栋", kind="building", parent_id="OTHER-C"),
            ])
            db.flush()
            db.get(Location, "OTHER-1").parent_id = "OTHER-2"
            db.get(Location, "OTHER-2").parent_id = "OTHER-1"
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "存在循环"):
            seed_demo_location_hierarchy(self.engine)
        self.assertEqual(self.snapshot(), before)

    def test_database_failure_rolls_back_new_rows_and_legacy_updates(self):
        seed_demo_locations(self.engine)
        before = self.snapshot()
        attempts = {"insert": 0, "update": 0}

        def fail_second_update(connection, cursor, statement, parameters, context, many):
            sql = statement.lstrip().upper()
            if sql.startswith("INSERT INTO LOCATIONS"):
                attempts["insert"] += 1
            elif sql.startswith("UPDATE LOCATIONS"):
                attempts["update"] += 1
                if attempts["update"] == 2:
                    raise RuntimeError("合成写入中断")

        event.listen(self.engine, "before_cursor_execute", fail_second_update)
        try:
            with self.assertRaisesRegex(RuntimeError, "合成写入中断"):
                seed_demo_location_hierarchy(self.engine)
        finally:
            event.remove(self.engine, "before_cursor_execute", fail_second_update)

        self.assertEqual(attempts, {"insert": 9, "update": 2})
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()