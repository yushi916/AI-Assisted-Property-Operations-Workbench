import argparse
import json
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect

from database import DATABASE_PATH, create_db_engine


ROOT = Path(__file__).resolve().parent
BASELINE = "0001_intake"
TABLES = ("locations", "service_requests")


def run_alembic(connection, action):
    config = Config(str(ROOT / "alembic.ini"))
    config.attributes["connection"] = connection
    getattr(command, action)(config, BASELINE)


def schema_signature(connection):
    inspector = inspect(connection)
    if inspector.get_view_names() or connection.exec_driver_sql(
        "SELECT name FROM sqlite_master WHERE type='trigger'"
    ).first():
        raise ValueError("存在额外视图或触发器，需要人工核对")

    result = {}
    for name in sorted(set(inspector.get_table_names()) - {"alembic_version"}):
        result[name] = {
            "columns": [
                (c["name"], str(c["type"]), c["nullable"], c["default"])
                for c in inspector.get_columns(name)
            ],
            "primary_key": inspector.get_pk_constraint(name)["constrained_columns"],
            "unique": sorted(
                tuple(c["column_names"])
                for c in inspector.get_unique_constraints(name)
            ),
            "checks": sorted(
                (c["name"], " ".join(c["sqltext"].split()))
                for c in inspector.get_check_constraints(name)
            ),
            "foreign_keys": sorted(
                (
                    tuple(c["constrained_columns"]),
                    c["referred_table"],
                    tuple(c["referred_columns"]),
                    tuple(sorted(c["options"].items())),
                )
                for c in inspector.get_foreign_keys(name)
            ),
            "indexes": sorted(
                (c["name"], tuple(c["column_names"]), c["unique"])
                for c in inspector.get_indexes(name)
            ),
        }
    return result


def baseline_signature():
    with tempfile.TemporaryDirectory() as folder:
        engine = create_db_engine(Path(folder) / "baseline.db")
        try:
            with engine.begin() as connection:
                run_alembic(connection, "upgrade")
                return schema_signature(connection)
        finally:
            engine.dispose()


def validate(connection, expected):
    if schema_signature(connection) != expected:
        raise ValueError("数据库结构与0001_intake不匹配，未登记版本")

    if connection.exec_driver_sql("PRAGMA integrity_check").scalars().all() != ["ok"]:
        raise ValueError("数据库完整性检查失败")

    if connection.exec_driver_sql("PRAGMA foreign_key_check").first():
        raise ValueError("存在无效外键，需要先核实数据")

    heads = MigrationContext.configure(connection).get_current_heads()
    if heads not in ((), (BASELINE,)):
        raise ValueError("数据库已有其他迁移版本，不能登记初始版本")
    return heads


def read_rows(connection):
    return {
        name: [
            tuple(row)
            for row in connection.exec_driver_sql(
                f'SELECT * FROM "{name}" ORDER BY id'
            )
        ]
        for name in TABLES
    }


def make_backup(path):
    folder = path.parent / "backups"
    folder.mkdir(exist_ok=True)
    backup = folder / f"{path.stem}-before-alembic-{uuid4().hex}.db"

    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as source:
        with closing(sqlite3.connect(backup)) as target:
            source.backup(target)
    return backup


def adopt_database(db_path, apply=False):
    path = Path(db_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"数据库不存在：{path}")

    expected = baseline_signature()
    engine = create_db_engine(path)
    try:
        with engine.connect() as connection:
            heads = validate(connection, expected)
            rows = read_rows(connection)

        counts = {name: len(items) for name, items in rows.items()}
        if heads == (BASELINE,):
            return {"status": "already_managed", "counts": counts}
        if not apply:
            return {"status": "ready", "counts": counts}

        backup = make_backup(path)
        backup_engine = create_db_engine(backup)
        try:
            with backup_engine.connect() as connection:
                validate(connection, expected)
                backup_rows = read_rows(connection)
        finally:
            backup_engine.dispose()

        with engine.begin() as connection:
            validate(connection, expected)
            if read_rows(connection) != backup_rows:
                raise ValueError("备份后数据发生变化，请停止写入后重试")

            run_alembic(connection, "stamp")

            if read_rows(connection) != backup_rows:
                raise ValueError("登记后业务数据发生变化，已回滚")
            if MigrationContext.configure(connection).get_current_heads() != (BASELINE,):
                raise ValueError("版本登记未成功，已回滚")

        return {
            "status": "adopted",
            "counts": {name: len(items) for name, items in backup_rows.items()},
            "backup": str(backup),
        }
    finally:
        engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(
        adopt_database(DATABASE_PATH, apply=args.apply),
        ensure_ascii=False,
        indent=2,
    ))