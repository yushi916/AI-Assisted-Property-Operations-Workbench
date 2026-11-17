import argparse
import json
from contextlib import closing
from pathlib import Path

from database import DATABASE_PATH
from verify_location_upgrade import open_readonly


OLD_VERSION = "0004_execution_records"
NEW_VERSION = "0005_inspection_records"
OLD_TABLES = (
    "locations", "service_requests", "plan_instances", "plan_locations",
    "execution_records", "execution_locations",
)
NEW_TABLES = ("inspection_records",)


def verify(backup_path, current_path, before_upgrade=False):
    backup_path = Path(backup_path).resolve()
    current_path = Path(current_path).resolve()
    for label, path in (("备份", backup_path), ("当前库", current_path)):
        if not path.is_file():
            raise FileNotFoundError(f"{label}文件不存在：{path}")
    if backup_path.samefile(current_path):
        raise ValueError("备份与当前库必须是不同文件")

    current_version = OLD_VERSION if before_upgrade else NEW_VERSION
    with closing(open_readonly(backup_path)) as before, closing(open_readonly(current_path)) as after:
        for label, connection, version, tables in (
            ("备份", before, OLD_VERSION, OLD_TABLES),
            ("当前库", after, current_version, OLD_TABLES if before_upgrade else OLD_TABLES + NEW_TABLES),
        ):
            if connection.execute("SELECT version_num FROM alembic_version").fetchall() != [(version,)]:
                raise ValueError(f"{label}版本不符合预期：{version}")
            actual_tables = {
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            }
            if actual_tables != set(tables) | {"alembic_version"}:
                raise ValueError(f"{label}表清单不符合预期")
            if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise ValueError(f"{label}完整性检查失败")
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError(f"{label}存在无效外键")

        schema_sql = """
            SELECT type, name, tbl_name, sql FROM sqlite_master
            WHERE tbl_name IN (?, ?, ?, ?, ?, ?) ORDER BY type, name
        """
        if before.execute(schema_sql, OLD_TABLES).fetchall() != after.execute(schema_sql, OLD_TABLES).fetchall():
            raise ValueError("旧表结构发生变化，请先核对")

        counts = {}
        for table in OLD_TABLES:
            statement = f'SELECT * FROM "{table}" ORDER BY 1, 2'
            old_rows = before.execute(statement).fetchall()
            current_rows = after.execute(statement).fetchall()
            if old_rows != current_rows:
                raise ValueError(f"{table}记录不一致，请核对新增、修改或删除")
            counts[table] = {"backup": len(old_rows), "current": len(current_rows)}

        result = {
            "stage": "before_upgrade" if before_upgrade else "after_upgrade",
            "backup_version": OLD_VERSION,
            "current_version": current_version,
            "old_schema_unchanged": True,
            "old_rows_unchanged": True,
            "integrity_and_foreign_keys_ok": True,
            "counts": counts,
        }
        if not before_upgrade:
            inspection_counts = {
                table: after.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                for table in NEW_TABLES
            }
            if any(inspection_counts.values()):
                raise ValueError("新巡查表应为空，请先核对是否已有业务写入")
            result["inspection_counts"] = inspection_counts
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("backup", type=Path)
    parser.add_argument("--before", action="store_true")
    args = parser.parse_args()
    result = verify(args.backup, DATABASE_PATH, before_upgrade=args.before)
    print(json.dumps(result, ensure_ascii=False, indent=2))