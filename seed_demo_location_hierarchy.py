import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from database import DATABASE_PATH, engine
from location_hierarchy import build_location_paths
from location_schemas import LocationCreate
from models import Location
from seed_demo_locations import DEMO_LOCATIONS


DEMO_HIERARCHY = (
    ("LOC-DEMO-COMMUNITY", "合成小区", "community", None),
    ("LOC-A", "A区", "area", "LOC-DEMO-COMMUNITY"),
    ("LOC-B", "B区", "area", "LOC-DEMO-COMMUNITY"),
    ("LOC-A-B01", "1栋", "building", "LOC-A"),
    ("LOC-A-B02", "2栋", "building", "LOC-A"),
    ("LOC-A-B01-U01", "1单元", "unit", "LOC-A-B01"),
    ("LOC-A-B02-U01", "1单元", "unit", "LOC-A-B02"),
    ("LOC-A-B01-U01-R302", "302室", "room", "LOC-A-B01-U01"),
    ("LOC-A-B02-U01-R302", "302室", "room", "LOC-A-B02-U01"),
    ("LOC-A-LOBBY", "A区大堂", "public_space", "LOC-A"),
    ("LOC-A-COURTYARD", "A区中庭", "public_space", "LOC-A"),
    ("LOC-B-LOBBY", "B区大堂", "public_space", "LOC-B"),
)
LEGACY_IDS = {location_id for location_id, _ in DEMO_LOCATIONS}


def seed_demo_location_hierarchy(db_engine):
    targets = [
        LocationCreate(id=location_id, name=name, kind=kind, parent_id=parent_id)
        for location_id, name, kind, parent_id in DEMO_HIERARCHY
    ]
    build_location_paths(targets)
    result = {"added": 0, "updated": 0}

    with Session(db_engine) as db, db.begin():
        current = {item.id: item for item in db.scalars(select(Location))}
        candidates = dict(current)
        changes = []

        for target in targets:
            saved = current.get(target.id)
            if saved is not None:
                if saved.name != target.name:
                    raise ValueError(f"地点名称冲突，未覆盖原记录：{target.id}")
                if (saved.kind, saved.parent_id) == (target.kind, target.parent_id):
                    continue
                if not (
                    saved.id in LEGACY_IDS
                    and saved.kind == "unknown"
                    and saved.parent_id is None
                ):
                    raise ValueError(f"地点分类或归属冲突，未覆盖原记录：{target.id}")

            candidates[target.id] = target
            changes.append((target, saved))

        # 在修改ORM对象或执行写入之前，检查合并后的完整台账。
        build_location_paths(candidates.values())

        for target, saved in changes:
            if saved is None:
                db.add(Location(**target.model_dump(), version=1))
                result["added"] += 1
            else:
                saved.kind = target.kind
                saved.parent_id = target.parent_id
                saved.version += 1
                result["updated"] += 1
            # 配置按父节点在前的顺序排列，先让父节点通过外键检查。
            db.flush()

    return result


if __name__ == "__main__":
    if not DATABASE_PATH.is_file():
        raise SystemExit("未找到项目数据库；请先完成既定数据库迁移。")
    try:
        print(json.dumps(seed_demo_location_hierarchy(engine), ensure_ascii=False))
    finally:
        engine.dispose()