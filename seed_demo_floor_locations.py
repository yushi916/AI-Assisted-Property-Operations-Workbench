import json

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from database import DATABASE_PATH, engine
from location_hierarchy import build_location_paths
from location_schemas import LocationCreate
from models import Location
from seed_demo_location_hierarchy import DEMO_HIERARCHY


VERSION = "0008_floor_locations"

NEW_LOCATIONS = (
    ("LOC-A-B01-U01-F03", "3层", "floor", "LOC-A-B01-U01"),
    ("LOC-A-B02-U01-F03", "3层", "floor", "LOC-A-B02-U01"),
    ("LOC-A-B01-U01-R301", "301室", "room", "LOC-A-B01-U01-F03"),
    ("LOC-A-B02-U01-R301", "301室", "room", "LOC-A-B02-U01-F03"),
    (
        "LOC-A-B01-U01-F03-CORRIDOR",
        "公共走廊",
        "public_space",
        "LOC-A-B01-U01-F03",
    ),
    (
        "LOC-A-B02-U01-F03-CORRIDOR",
        "公共走廊",
        "public_space",
        "LOC-A-B02-U01-F03",
    ),
)

ROOM_PARENTS = {
    "LOC-A-B01-U01-R302": "LOC-A-B01-U01-F03",
    "LOC-A-B02-U01-R302": "LOC-A-B02-U01-F03",
}


def seed_demo_floor_locations(db_engine):
    result = {"added": 0, "updated": 0}

    with Session(db_engine) as db, db.begin():
        versions = db.execute(
            text("SELECT version_num FROM alembic_version")
        ).all()
        if versions != [(VERSION,)]:
            raise ValueError(f"请先完成数据库升级：{VERSION}")

        current = {
            item.id: item for item in db.scalars(select(Location))
        }
        candidates = dict(current)
        additions = []
        moves = []

        # 仅接受此前确认的合成台账，或本工具已完成的状态。
        for location_id, name, kind, parent_id in DEMO_HIERARCHY:
            saved = current.get(location_id)
            allowed_parents = {
                parent_id,
                ROOM_PARENTS.get(location_id, parent_id),
            }
            if (
                saved is None
                or (saved.name, saved.kind) != (name, kind)
                or saved.parent_id not in allowed_parents
            ):
                raise ValueError(
                    f"原台账缺失或冲突，未覆盖：{location_id}"
                )

            target_parent = ROOM_PARENTS.get(location_id, parent_id)
            if saved.parent_id != target_parent:
                target = LocationCreate(
                    id=location_id,
                    name=name,
                    kind=kind,
                    parent_id=target_parent,
                )
                candidates[location_id] = target
                moves.append((saved, target_parent))

        for location_id, name, kind, parent_id in NEW_LOCATIONS:
            target = LocationCreate(
                id=location_id,
                name=name,
                kind=kind,
                parent_id=parent_id,
            )
            saved = current.get(location_id)

            if saved is not None:
                if (
                    saved.name, saved.kind, saved.parent_id
                ) != (name, kind, parent_id):
                    raise ValueError(
                        f"新增点位编号已被其他地点使用：{location_id}"
                    )
            else:
                candidates[location_id] = target
                additions.append(target)

        # 先校验合并后的完整台账，再执行任何写入。
        build_location_paths(candidates.values())

        for target in additions:
            db.add(Location(**target.model_dump(), version=1))
            db.flush()
            result["added"] += 1

        for saved, parent_id in moves:
            saved.parent_id = parent_id
            saved.version += 1
            db.flush()
            result["updated"] += 1

    return result


if __name__ == "__main__":
    if not DATABASE_PATH.is_file():
        raise SystemExit(
            "未找到项目数据库；请先完成既定数据库迁移。"
        )
    try:
        print(json.dumps(
            seed_demo_floor_locations(engine),
            ensure_ascii=False,
        ))
    finally:
        engine.dispose()