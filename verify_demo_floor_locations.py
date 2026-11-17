import argparse
import json
from contextlib import closing
from pathlib import Path

from adopt_intake_database import make_backup
from database import DATABASE_PATH
from location_hierarchy import build_location_paths
from location_schemas import LocationCreate
from seed_demo_floor_locations import NEW_LOCATIONS, ROOM_PARENTS, VERSION
from seed_demo_location_hierarchy import DEMO_HIERARCHY
from verify_location_upgrade import open_readonly


BUSINESS_TABLES = (
    "service_requests", "plan_instances", "plan_locations",
    "execution_records", "execution_locations", "inspection_records",
    "request_plan_links", "agent_runs",
)


def location_rows(connection):
    cursor = connection.execute(
        "SELECT * FROM locations ORDER BY id"
    )
    columns = [column[0] for column in cursor.description]
    return {
        row[columns.index("id")]: dict(zip(columns, row))
        for row in cursor
    }


def expected_locations(original):
    expected = {
        key: dict(row) for key, row in original.items()
    }

    for location_id, name, kind, parent_id in DEMO_HIERARCHY:
        row = expected.get(location_id)
        target_parent = ROOM_PARENTS.get(location_id, parent_id)

        if (
            row is None
            or (row["name"], row["kind"]) != (name, kind)
            or row["parent_id"] not in {parent_id, target_parent}
        ):
            raise ValueError(
                f"备份中的原台账缺失或冲突：{location_id}"
            )

        if row["parent_id"] != target_parent:
            row["parent_id"] = target_parent
            row["version"] += 1

    for location_id, name, kind, parent_id in NEW_LOCATIONS:
        row = expected.get(location_id)

        if row is not None:
            if (
                row["name"], row["kind"], row["parent_id"]
            ) != (name, kind, parent_id):
                raise ValueError(
                    f"备份中的新增点位编号冲突：{location_id}"
                )
        else:
            expected[location_id] = dict(
                id=location_id,
                name=name,
                kind=kind,
                parent_id=parent_id,
                version=1,
            )

    return expected


def verify(backup_path, current_path, before_apply=False):
    backup_path = Path(backup_path).resolve()
    current_path = Path(current_path).resolve()

    for path in (backup_path, current_path):
        if not path.is_file():
            raise FileNotFoundError(f"数据库不存在：{path}")

    if backup_path.samefile(current_path):
        raise ValueError("备份和当前库必须是不同文件")

    with (
        closing(open_readonly(backup_path)) as before,
        closing(open_readonly(current_path)) as after,
    ):
        for connection in (before, after):
            versions = connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchall()
            if versions != [(VERSION,)]:
                raise ValueError(f"数据库版本应为：{VERSION}")

            if connection.execute(
                "PRAGMA integrity_check"
            ).fetchall() != [("ok",)]:
                raise ValueError("数据库完整性检查失败")

            if connection.execute(
                "PRAGMA foreign_key_check"
            ).fetchall():
                raise ValueError("数据库存在无效外键")

            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%'"
                )
            }
            expected_tables = set(BUSINESS_TABLES) | {
                "locations", "alembic_version",
            }
            if tables != expected_tables:
                raise ValueError("数据库表清单不符合预期")

        schema_sql = (
            "SELECT type,name,tbl_name,sql "
            "FROM sqlite_master ORDER BY type,name"
        )
        if (
            before.execute(schema_sql).fetchall()
            != after.execute(schema_sql).fetchall()
        ):
            raise ValueError("数据库结构发生了变化")

        original = location_rows(before)
        expected = expected_locations(original)
        current = location_rows(after)

        if current != (original if before_apply else expected):
            raise ValueError(
                "地点新增、归属或版本变化不符合预期"
            )

        counts = {}
        for table in BUSINESS_TABLES:
            sql = f'SELECT * FROM "{table}" ORDER BY 1,2'
            old_rows = before.execute(sql).fetchall()
            new_rows = after.execute(sql).fetchall()

            if old_rows != new_rows:
                raise ValueError(
                    f"{table}记录发生新增、修改或删除"
                )

            counts[table] = {
                "backup": len(old_rows),
                "current": len(new_rows),
            }

        paths = build_location_paths([
            LocationCreate(**{
                key: row[key]
                for key in ("id", "name", "kind", "parent_id")
            })
            for row in current.values()
        ])

        counts["locations"] = {
            "backup": len(original),
            "current": len(current),
        }

        return {
            "stage": (
                "before_apply" if before_apply else "after_apply"
            ),
            "backup": str(backup_path),
            "version": VERSION,
            "schema_unchanged": True,
            "business_rows_unchanged": True,
            "locations_match_expected": True,
            "integrity_and_foreign_keys_ok": True,
            "counts": counts,
            "room_paths": {
                key: paths[key]
                for key, row in current.items()
                if row["kind"] == "room"
            },
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("backup_path", nargs="?", type=Path)
    parser.add_argument("--make-backup", action="store_true")
    parser.add_argument("--before", action="store_true")
    args = parser.parse_args()

    if args.make_backup:
        if args.backup_path or args.before:
            parser.error("--make-backup 单独使用")

        if not DATABASE_PATH.is_file():
            raise FileNotFoundError(
                f"数据库不存在：{DATABASE_PATH}"
            )

        backup = make_backup(DATABASE_PATH.resolve())
        print(f"备份已生成：{backup}", flush=True)
        result = verify(
            backup, DATABASE_PATH, before_apply=True,
        )
    else:
        if args.backup_path is None:
            parser.error(
                "请提供备份路径，或使用 --make-backup"
            )

        result = verify(
            args.backup_path, DATABASE_PATH, args.before,
        )

    print(json.dumps(result, ensure_ascii=False, indent=2))