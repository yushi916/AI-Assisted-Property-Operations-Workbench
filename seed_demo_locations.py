from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from database import engine
from models import Location


DEMO_LOCATIONS = (
    ("LOC-A-LOBBY", "A区大堂"),
    ("LOC-A-COURTYARD", "A区中庭"),
    ("LOC-B-LOBBY", "B区大堂"),
)


def seed_demo_locations(db_engine):
    added = 0
    with Session(db_engine) as db:
        with db.begin():
            for location_id, name in DEMO_LOCATIONS:
                statement = (
                    insert(Location)
                    .values(id=location_id, name=name, version=1)
                    .on_conflict_do_nothing(index_elements=["id"])
                    .returning(Location.id)
                )
                inserted_id = db.scalar(statement)
                saved = db.get(Location, location_id)

                if saved.name != name:
                    raise ValueError(
                        f"地点编号 {location_id} 已存在且名称不同；"
                        "本次初始化已回滚，请人工核对，未覆盖原地点。"
                    )

                if inserted_id is not None:
                    added += 1

    return added


if __name__ == "__main__":
    added = seed_demo_locations(engine)
    print(f"合成地点台账检查完成：新增 {added} 条。")