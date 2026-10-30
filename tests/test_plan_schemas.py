import unittest
from uuid import uuid4

from pydantic import ValidationError

from plan_schemas import PlanCreate


class PlanCreateTests(unittest.TestCase):
    def data(self, **changes):
        values = {
            "request_id": str(uuid4()),
            "title": "合成计划：A、B区大堂保洁",
            "service_category": "cleaning",
            "requirements": "清理地面及垃圾桶周围的散落垃圾。",
            "owner_name": "合成负责人",
            "location_ids": ["LOC-A-LOBBY", "LOC-B-LOBBY"],
            "planned_start_at": "2026-11-01T09:00:00+08:00",
            "planned_end_at": "2026-11-01T09:30:00+08:00",
        }
        values.update(changes)
        return values

    def test_valid_plan_has_explicit_scope_and_requirements(self):
        plan = PlanCreate.model_validate(self.data())
        self.assertEqual(plan.plan_kind, "routine")
        self.assertEqual(plan.location_ids, ["LOC-A-LOBBY", "LOC-B-LOBBY"])
        self.assertEqual(plan.requirements, "清理地面及垃圾桶周围的散落垃圾。")
        self.assertIsNotNone(plan.planned_start_at.utcoffset())

    def test_required_text_rejects_blank_and_overlong_values(self):
        for field, limit in (
            ("title", 120), ("requirements", 2000), ("owner_name", 120),
        ):
            for value in ("   ", "x" * (limit + 1)):
                with self.subTest(field=field, value_length=len(value)):
                    with self.assertRaises(ValidationError):
                        PlanCreate.model_validate(self.data(**{field: value}))

    def test_invalid_location_sets_are_rejected(self):
        for values in (
            [], ["LOC-A-LOBBY", "LOC-A-LOBBY"], [""], ["   "], ["x" * 65],
        ):
            with self.subTest(values=values):
                with self.assertRaises(ValidationError):
                    PlanCreate.model_validate(self.data(location_ids=values))

    def test_both_times_require_timezone(self):
        for field in ("planned_start_at", "planned_end_at"):
            with self.subTest(field=field):
                with self.assertRaises(ValidationError):
                    PlanCreate.model_validate(
                        self.data(**{field: "2026-11-01T09:00:00"})
                    )

    def test_end_must_be_strictly_later_than_start(self):
        for end in (
            "2026-11-01T09:00:00+08:00",
            "2026-11-01T08:59:00+08:00",
        ):
            with self.subTest(end=end):
                with self.assertRaises(ValidationError):
                    PlanCreate.model_validate(self.data(planned_end_at=end))

    def test_time_order_uses_actual_instant(self):
        plan = PlanCreate.model_validate(
            self.data(planned_end_at="2026-11-01T01:30:00Z")
        )
        self.assertEqual(
            (plan.planned_end_at - plan.planned_start_at).total_seconds(),
            1800,
        )
        with self.assertRaises(ValidationError):
            PlanCreate.model_validate(
                self.data(planned_end_at="2026-11-01T09:30:00+09:00")
            )

    def test_supported_kinds_and_client_field_boundary(self):
        plan = PlanCreate.model_validate(self.data(plan_kind="special"))
        self.assertEqual(plan.plan_kind, "special")
        for changes in (
            {"plan_kind": "invalid"},
            {"service_category": "invalid"},
            {"id": str(uuid4())},
            {"version": 1},
            {"created_at": "2026-11-01T01:00:00Z"},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(ValidationError):
                    PlanCreate.model_validate(self.data(**changes))


if __name__ == "__main__":
    unittest.main()