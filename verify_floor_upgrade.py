import argparse
import json
from pathlib import Path

from adopt_intake_database import make_backup, schema_signature
from database import DATABASE_PATH, create_db_engine


OLD = "0006_request_plan_links"
NEW = "0008_floor_locations"
TABLES = (
    "locations", "service_requests", "plan_instances", "plan_locations",
    "execution_records", "execution_locations", "inspection_records",
    "request_plan_links",
)
FLOOR_CHECKS = {
    "ck_location_kind":
        "kind IN ('community', 'unknown', 'area', 'building', 'unit', "
        "'room', 'public_space', 'floor')",
    "ck_location_parent_presence":
        "(kind IN ('community', 'unknown') AND parent_id IS NULL) OR "
        "(kind IN ('area', 'building', 'unit', 'room', 'public_space', 'floor') "
        "AND parent_id IS NOT NULL)",
}


def check_database(connection, version, tables):
    if connection.exec_driver_sql(
        "SELECT version_num FROM alembic_version"
    ).all() != [(version,)]:
        raise ValueError(f"数据库版本不符合预期：{version}")

    if connection.exec_driver_sql(
        "PRAGMA integrity_check"
    ).all() != [("ok",)]:
        raise ValueError("数据库完整性检查失败")

    if connection.exec_driver_sql("PRAGMA foreign_key_check").first():
        raise ValueError("数据库存在无效外键")

    signature = schema_signature(connection)
    if set(signature) != set(tables):
        raise ValueError("业务表清单不符合预期")
    return signature


def verify(backup_path, current_path, before_upgrade=False):
    backup_path = Path(backup_path).resolve()
    current_path = Path(current_path).resolve()

    for path in (backup_path, current_path):
        if not path.is_file():
            raise FileNotFoundError(f"数据库文件不存在：{path}")

    if backup_path.samefile(current_path):
        raise ValueError("备份和当前库必须是不同文件")

    backup_engine = create_db_engine(backup_path)
    current_engine = create_db_engine(current_path)

    try:
        with backup_engine.connect() as before, current_engine.connect() as after:
            before.exec_driver_sql("PRAGMA query_only=ON")
            after.exec_driver_sql("PRAGMA query_only=ON")

            old_schema = check_database(before, OLD, TABLES)
            expected_version = OLD if before_upgrade else NEW
            tables = TABLES if before_upgrade else TABLES + ("agent_runs",)
            new_schema = check_database(after, expected_version, tables)

            if not before_upgrade:
                checks = dict(old_schema["locations"]["checks"])
                for name, sql in FLOOR_CHECKS.items():
                    if name not in checks:
                        raise ValueError(f"备份缺少地点约束：{name}")
                    checks[name] = sql
                old_schema["locations"]["checks"] = sorted(checks.items())

            if old_schema != {
                name: new_schema[name] for name in TABLES
            }:
                raise ValueError("旧表结构存在预期以外的变化，请保留现场核对")

            counts = {}
            for name in TABLES:
                sql = f'SELECT * FROM "{name}" ORDER BY 1, 2'
                old_rows = before.exec_driver_sql(sql).all()
                new_rows = after.exec_driver_sql(sql).all()

                if old_rows != new_rows:
                    raise ValueError(
                        f"{name}记录发生新增、修改或删除，请保留现场核对"
                    )

                counts[name] = {
                    "backup": len(old_rows),
                    "current": len(new_rows),
                }

            if not before_upgrade and after.exec_driver_sql(
                "SELECT COUNT(*) FROM agent_runs"
            ).scalar_one() != 0:
                raise ValueError(
                    "纯结构升级后 Agent 运行表应为空，请核对业务写入"
                )

            return {
                "stage": (
                    "before_upgrade" if before_upgrade else "after_upgrade"
                ),
                "backup": str(backup_path),
                "backup_version": OLD,
                "current_version": expected_version,
                "old_rows_unchanged": True,
                "schema_changes_expected": True,
                "integrity_and_foreign_keys_ok": True,
                "counts": counts,
                **({} if before_upgrade else {"agent_run_count": 0}),
            }
    finally:
        backup_engine.dispose()
        current_engine.dispose()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("backup_path", nargs="?", type=Path)
    parser.add_argument("--make-backup", action="store_true")
    parser.add_argument("--before", action="store_true")
    args = parser.parse_args()

    if args.make_backup:
        if args.backup_path or args.before:
            parser.error("--make-backup 单独使用")

        if not DATABASE_PATH.is_file():
            raise FileNotFoundError(f"数据库不存在：{DATABASE_PATH}")

        backup = make_backup(DATABASE_PATH.resolve())
        print(f"备份已生成：{backup}", flush=True)
        result = verify(backup, DATABASE_PATH, before_upgrade=True)
    else:
        if args.backup_path is None:
            parser.error("请提供备份路径，或使用 --make-backup")

        result = verify(
            args.backup_path,
            DATABASE_PATH,
            before_upgrade=args.before,
        )

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()