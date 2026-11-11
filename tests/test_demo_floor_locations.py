import unittest

from sqlalchemy import event

from location_hierarchy import build_location_paths
from location_schemas import LocationCreate
from seed_demo_floor_locations import seed_demo_floor_locations
from tests import test_floor_location_migration as migration_tests


class DemoFloorLocationTests(unittest.TestCase):
    def setUp(self):
        self.case = migration_tests.FloorLocationMigrationTests()
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.case.seed_records()
        self.case.migrate()
        self.engine = self.case.engine

    def test_additions_keep_business_rows_ids_and_only_increment_moved_rooms(self):
        before = self.case.rows()
        self.assertEqual(
            seed_demo_floor_locations(self.engine),
            {"added": 6, "updated": 2},
        )
        after = self.case.rows()

        for name in before:
            if name != "locations":
                self.assertEqual(after[name], before[name])

        with self.engine.connect() as connection:
            rows = connection.exec_driver_sql(
                "SELECT id,name,kind,parent_id,version FROM locations"
            ).all()

        by_id = {row.id: row for row in rows}
        self.assertEqual(len(by_id), 18)

        for old in before["locations"]:
            saved = by_id[old.id]
            self.assertEqual(
                (saved.id, saved.name, saved.kind),
                (old.id, old.name, old.kind),
            )
            if old.kind == "room":
                self.assertEqual(
                    saved.parent_id, old.parent_id + "-F03",
                )
                self.assertEqual(saved.version, old.version + 1)
            else:
                self.assertEqual(
                    (saved.parent_id, saved.version),
                    (old.parent_id, old.version),
                )

        paths = build_location_paths([
            LocationCreate(
                id=r.id,
                name=r.name,
                kind=r.kind,
                parent_id=r.parent_id,
            )
            for r in rows
        ])

        for building in ("01", "02"):
            prefix = (
                f"合成小区 / A区 / {int(building)}栋 / "
                "1单元 / 3层 / "
            )
            for room in ("301", "302"):
                self.assertEqual(
                    paths[f"LOC-A-B{building}-U01-R{room}"],
                    prefix + room + "室",
                )
            self.assertEqual(
                paths[f"LOC-A-B{building}-U01-F03-CORRIDOR"],
                prefix + "公共走廊",
            )

    def test_repeat_changes_no_fields(self):
        seed_demo_floor_locations(self.engine)
        before = self.case.rows()

        self.assertEqual(
            seed_demo_floor_locations(self.engine),
            {"added": 0, "updated": 0},
        )
        self.assertEqual(self.case.rows(), before)

    def test_existing_room_conflict_prevents_all_writes(self):
        with self.engine.begin() as connection:
            connection.exec_driver_sql(
                "UPDATE locations SET name='另一个房间' "
                "WHERE id='LOC-A-B02-U01-R302'"
            )

        before = self.case.rows()
        with self.assertRaisesRegex(
            ValueError, "原台账缺失或冲突"
        ):
            seed_demo_floor_locations(self.engine)

        self.assertEqual(self.case.rows(), before)

    def test_new_id_conflict_prevents_all_writes(self):
        with self.engine.begin() as connection:
            connection.exec_driver_sql(
                "INSERT INTO locations"
                "(id,name,kind,parent_id,version) VALUES(?,?,?,?,?)",
                (
                    "LOC-A-B02-U01-F03-CORRIDOR",
                    "其他点位",
                    "public_space",
                    "LOC-A",
                    4,
                ),
            )

        before = self.case.rows()
        with self.assertRaisesRegex(
            ValueError, "新增点位编号已被其他地点使用"
        ):
            seed_demo_floor_locations(self.engine)

        self.assertEqual(self.case.rows(), before)

    def test_failure_after_inserts_and_first_room_move_rolls_back_every_table(self):
        before = self.case.rows()
        updates = []

        def fail_second_update(
            connection, cursor, statement, parameters, context, many,
        ):
            if statement.lstrip().upper().startswith(
                "UPDATE LOCATIONS"
            ):
                updates.append(statement)
                if len(updates) == 2:
                    raise RuntimeError("模拟地点写入中断")

        event.listen(
            self.engine, "before_cursor_execute", fail_second_update,
        )
        try:
            with self.assertRaisesRegex(
                RuntimeError, "模拟地点写入中断"
            ):
                seed_demo_floor_locations(self.engine)
        finally:
            event.remove(
                self.engine,
                "before_cursor_execute",
                fail_second_update,
            )

        self.assertEqual(len(updates), 2)
        self.assertEqual(self.case.rows(), before)

    def test_old_revision_refuses_all_writes(self):
        self.case.migrate(
            "0007_agent_runs", action="downgrade",
        )
        before = self.case.rows()

        with self.assertRaisesRegex(
            ValueError, "请先完成数据库升级"
        ):
            seed_demo_floor_locations(self.engine)

        self.assertEqual(self.case.rows(), before)


if __name__ == "__main__":
    unittest.main()