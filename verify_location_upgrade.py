import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from database import DATABASE_PATH


TABLES = ("locations", "service_requests", "plan_instances", "plan_locations")


def open_readonly(path):
    connection = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("BEGIN")
    return connection


def original_rows(connection):
    result = {}
    for table in TABLES:
        columns = "id, name, version" if table == "locations" else "*"
        result[table] = connection.execute(
            f'SELECT {columns} FROM "{table}" ORDER BY 1, 2'
        ).fetchall()
    return result


def verify(backup_path, current_path, expected_version):
    with closing(open_readonly(backup_path)) as before, closing(open_readonly(current_path)) as after:
        for label, connection, version in (
            ("备份", before, "0002_plans"),
            ("当前库", after, expected_version),
        ):
            if connection.execute("SELECT version_num FROM alembic_version").fetchall() != [(version,)]:
                raise ValueError(f"{label}版本不符合预期：{version}")
            if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise ValueError(f"{label}完整性检查失败")
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError(f"{label}存在无效外键")

        old = original_rows(before)
        current = original_rows(after)
        for table in TABLES:
            if old[table] != current[table]:
                raise ValueError(f"{table}记录不一致，请核对是否有新增、修改或删除")

        result = {
            "backup_version": "0002_plans",
            "current_version": expected_version,
            "old_rows_unchanged": True,
            "counts": {table: len(rows) for table, rows in old.items()},
        }
        if expected_version == "0003_location_hierarchy":
            changed = after.execute("""
                SELECT COUNT(*) FROM locations
                WHERE kind IS NULL OR kind <> 'unknown' OR parent_id IS NOT NULL
            """).fetchone()[0]
            if changed:
                raise ValueError("本次升级验收时，旧地点应保持unknown类型和空父地点")
            result["new_location_defaults_ok"] = True
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("backup", type=Path)
    parser.add_argument(
        "--expected-version",
        choices=("0002_plans", "0003_location_hierarchy"),
        default="0003_location_hierarchy",
    )
    args = parser.parse_args()
    result = verify(args.backup, DATABASE_PATH, args.expected_version)
    print(json.dumps(result, ensure_ascii=False, indent=2))