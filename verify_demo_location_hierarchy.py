import argparse
import json
from contextlib import closing
from pathlib import Path

from database import DATABASE_PATH
from location_hierarchy import build_location_paths
from location_schemas import LocationCreate
from seed_demo_location_hierarchy import DEMO_HIERARCHY, LEGACY_IDS
from verify_location_upgrade import open_readonly


VERSION = "0003_location_hierarchy"
BUSINESS_TABLES = ("service_requests", "plan_instances", "plan_locations")


def location_rows(connection):
    rows = connection.execute(
        "SELECT id, name, kind, parent_id, version FROM locations ORDER BY id"
    ).fetchall()
    return {row[0]: row for row in rows}


def expected_locations(original):
    expected = dict(original)
    for location_id, name, kind, parent_id in DEMO_HIERARCHY:
        old = original.get(location_id)
        version = 1
        if old is not None:
            if old[1] != name:
                raise ValueError(f"备份中的地点名称冲突：{location_id}")
            if old[2:4] == (kind, parent_id):
                version = old[4]
            elif location_id in LEGACY_IDS and old[2:4] == ("unknown", None):
                version = old[4] + 1
            else:
                raise ValueError(f"备份中的地点分类或归属冲突：{location_id}")
        expected[location_id] = (location_id, name, kind, parent_id, version)
    return expected


def verify(backup_path, current_path, before_apply=False):
    with closing(open_readonly(backup_path)) as before, closing(open_readonly(current_path)) as after:
        for label, connection in (("备份", before), ("当前库", after)):
            if connection.execute("SELECT version_num FROM alembic_version").fetchall() != [(VERSION,)]:
                raise ValueError(f"{label}应为{VERSION}")
            if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise ValueError(f"{label}完整性检查失败")
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError(f"{label}存在无效外键")

        schema_sql = "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
        if before.execute(schema_sql).fetchall() != after.execute(schema_sql).fetchall():
            raise ValueError("数据库结构发生了变化，请核对")

        counts = {}
        for table in BUSINESS_TABLES:
            statement = f'SELECT * FROM "{table}" ORDER BY 1, 2'
            old_rows = before.execute(statement).fetchall()
            new_rows = after.execute(statement).fetchall()
            if old_rows != new_rows:
                raise ValueError(f"{table}记录不一致，请核对新增、修改或删除")
            counts[table] = {"backup": len(old_rows), "current": len(new_rows)}

        original = location_rows(before)
        current = location_rows(after)
        expected = original if before_apply else expected_locations(original)
        different = sorted(
            location_id for location_id in expected.keys() | current.keys()
            if expected.get(location_id) != current.get(location_id)
        )
        if different:
            raise ValueError("地点变化不符合预期：" + ", ".join(different))

        paths = build_location_paths([
            LocationCreate(id=row[0], name=row[1], kind=row[2], parent_id=row[3])
            for row in current.values()
        ])
        counts["locations"] = {"backup": len(original), "current": len(current)}
        result = {
            "stage": "before_apply" if before_apply else "after_apply",
            "version": VERSION,
            "business_rows_unchanged": True,
            "locations_match_expected": True,
            "counts": counts,
        }
        if not before_apply:
            result["room_paths"] = {
                location_id: paths[location_id]
                for location_id, _, kind, _ in DEMO_HIERARCHY if kind == "room"
            }
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("backup", type=Path)
    parser.add_argument("--before", action="store_true")
    args = parser.parse_args()
    result = verify(args.backup, DATABASE_PATH, before_apply=args.before)
    print(json.dumps(result, ensure_ascii=False, indent=2))