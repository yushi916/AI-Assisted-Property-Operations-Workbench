import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database import Base, create_db_engine
from models import Location, PlanInstance, PlanLocation


class PlanModelTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.engine = create_db_engine(Path(self.folder.name) / "case.db")
        Base.metadata.create_all(self.engine)
        with Session(self.engine) as db, db.begin():
            db.add_all([
                Location(id="LOC-A-LOBBY", name="A区大堂"),
                Location(id="LOC-B-LOBBY", name="B区大堂"),
            ])

    def tearDown(self):
        self.engine.dispose()
        self.folder.cleanup()

    def make_plan(self, **changes):
        values = {
            "request_id": uuid4(),
            "title": "合成计划：两处大堂保洁",
            "service_category": "cleaning",
            "requirements": "清理地面及垃圾桶周围的散落垃圾。",
            "owner_name": "合成负责人",
            "planned_start_at": datetime.fromisoformat(
                "2026-11-01T09:00:00+08:00"
            ),
            "planned_end_at": datetime.fromisoformat(
                "2026-11-01T09:30:00+08:00"
            ),
            "locations": [
                PlanLocation(location_id="LOC-A-LOBBY"),
                PlanLocation(location_id="LOC-B-LOBBY"),
            ],
        }
        values.update(changes)
        return PlanInstance(**values)

    def counts(self):
        with Session(self.engine) as db:
            return (
                db.scalar(select(func.count()).select_from(PlanInstance)),
                db.scalar(select(func.count()).select_from(PlanLocation)),
            )

    def test_plan_scope_and_utc_times_survive_reopen(self):
        with Session(self.engine) as db, db.begin():
            plan = self.make_plan()
            db.add(plan)
            db.flush()
            plan_id, request_id = plan.id, plan.request_id
        self.engine.dispose()
        with Session(self.engine) as db:
            saved = db.get(PlanInstance, plan_id)
            self.assertNotEqual(saved.id, request_id)
            self.assertEqual(saved.request_id, request_id)
            self.assertEqual(saved.plan_kind, "routine")
            self.assertEqual(saved.version, 1)
            self.assertEqual(saved.planned_start_at.hour, 1)
            self.assertEqual(saved.planned_start_at.tzinfo, UTC)
            self.assertEqual(saved.created_at.tzinfo, UTC)
            self.assertEqual(
                (saved.planned_end_at - saved.planned_start_at).total_seconds(),
                1800,
            )
            self.assertEqual(
                {point.location.name for point in saved.locations},
                {"A区大堂", "B区大堂"},
            )
        self.assertEqual(self.counts(), (1, 2))

    def test_same_location_can_belong_to_different_plans(self):
        with Session(self.engine) as db, db.begin():
            db.add_all([self.make_plan(), self.make_plan()])
        self.assertEqual(self.counts(), (2, 4))

    def test_duplicate_submission_preserves_original_plan(self):
        request_id = uuid4()
        with Session(self.engine) as db, db.begin():
            db.add(self.make_plan(request_id=request_id))
        with self.assertRaisesRegex(IntegrityError, "UNIQUE constraint failed"):
            with Session(self.engine) as db, db.begin():
                db.add(self.make_plan(request_id=request_id, title="重复内容"))
        self.assertEqual(self.counts(), (1, 2))
        with Session(self.engine) as db:
            self.assertEqual(
                db.scalar(select(PlanInstance)).title,
                "合成计划：两处大堂保洁",
            )

    def test_duplicate_coverage_rolls_back_entire_plan(self):
        with self.assertRaisesRegex(IntegrityError, "UNIQUE constraint failed"):
            with Session(self.engine) as db, db.begin():
                plan = self.make_plan()
                plan.locations.append(PlanLocation(location_id="LOC-A-LOBBY"))
                db.add(plan)
        self.assertEqual(self.counts(), (0, 0))

    def test_invalid_links_reject_and_roll_back_flushed_rows(self):
        with self.assertRaisesRegex(IntegrityError, "FOREIGN KEY constraint failed"):
            with Session(self.engine) as db, db.begin():
                plan = self.make_plan()
                db.add(plan)
                db.flush()
                self.assertEqual(
                    db.scalar(select(func.count()).select_from(PlanInstance)), 1
                )
                plan.locations.append(PlanLocation(location_id="MISSING"))
        self.assertEqual(self.counts(), (0, 0))
        with self.assertRaisesRegex(IntegrityError, "FOREIGN KEY constraint failed"):
            with Session(self.engine) as db, db.begin():
                db.add(PlanLocation(plan_id=uuid4(), location_id="LOC-A-LOBBY"))
        self.assertEqual(self.counts(), (0, 0))

    def test_database_rejects_invalid_plan_fields(self):
        with Session(self.engine) as db, db.begin():
            db.add(self.make_plan())
        invalid_changes = [
            {"version": 0},
            {"service_category": "invalid"},
            {"plan_kind": "invalid"},
            {"planned_end_at": datetime.fromisoformat(
                "2026-11-01T09:00:00+08:00"
            )},
            {"planned_end_at": datetime.fromisoformat(
                "2026-11-01T09:30:00+09:00"
            )},
        ]
        for field, limit in (
            ("title", 120), ("requirements", 2000), ("owner_name", 120),
        ):
            invalid_changes.extend([{field: ""}, {field: "x" * (limit + 1)}])
        for changes in invalid_changes:
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(IntegrityError, "CHECK constraint failed"):
                    with Session(self.engine) as db, db.begin():
                        db.add(self.make_plan(**changes))
                self.assertEqual(self.counts(), (1, 2))


if __name__ == "__main__":
    unittest.main()