import unittest
from uuid import UUID, uuid4

from pydantic import ValidationError

from execution_schemas import ExecutionCreate


class ExecutionCreateTests(unittest.TestCase):
    def data(self, **changes):
        values = {
            "request_id": str(uuid4()),
            "plan_id": str(uuid4()),
            "executor_name": "合成执行人",
            "description": "  合成：仅A区大堂完成清理，B区尚未处理。  ",
            "completion_claim": "completed",
            "location_ids": ["LOC-A-LOBBY"],
            "claimed_started_at": "2026-11-02T09:00:00+08:00",
            "claimed_finished_at": "2026-11-02T09:20:00+08:00",
        }
        values.update(changes)
        return values

    def test_valid_claim_preserves_selected_points_and_original_description(self):
        values = self.data()
        execution = ExecutionCreate.model_validate(values)
        self.assertEqual(execution.plan_id, UUID(values["plan_id"]))
        self.assertEqual(execution.location_ids, ["LOC-A-LOBBY"])
        self.assertEqual(execution.description, values["description"])
        self.assertEqual(execution.completion_claim, "completed")

    def test_unknown_points_stay_empty_and_scope_field_is_required(self):
        execution = ExecutionCreate.model_validate(self.data(location_ids=[]))
        self.assertEqual(execution.location_ids, [])
        values = self.data()
        del values["location_ids"]
        with self.assertRaises(ValidationError):
            ExecutionCreate.model_validate(values)

    def test_required_text_rejects_blank_and_overlong_values(self):
        for field, limit in (("executor_name", 120), ("description", 2000)):
            for value in ("   ", "x" * (limit + 1)):
                with self.subTest(field=field, length=len(value)):
                    with self.assertRaises(ValidationError):
                        ExecutionCreate.model_validate(self.data(**{field: value}))

    def test_invalid_location_ids_are_rejected(self):
        for values in (
            ["LOC-A-LOBBY", "LOC-A-LOBBY"], [""], ["   "], ["x" * 65], None,
        ):
            with self.subTest(values=values):
                with self.assertRaises(ValidationError):
                    ExecutionCreate.model_validate(self.data(location_ids=values))

    def test_provided_times_must_include_timezone(self):
        for field in ("claimed_started_at", "claimed_finished_at"):
            with self.subTest(field=field):
                with self.assertRaises(ValidationError):
                    ExecutionCreate.model_validate(
                        self.data(**{field: "2026-11-02T09:00:00"})
                    )

    def test_supported_claims_and_client_field_boundary(self):
        for claim in ("completed", "partially_completed", "not_completed", "unknown"):
            with self.subTest(claim=claim):
                execution = ExecutionCreate.model_validate(
                    self.data(completion_claim=claim, claimed_finished_at=None)
                )
                self.assertEqual(execution.completion_claim, claim)
        for changes in (
            {"completion_claim": "invalid"},
            {"request_id": "invalid"},
            {"plan_id": "invalid"},
            {"id": str(uuid4())},
            {"recorded_at": "2026-11-02T01:30:00Z"},
            {"version": 1},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(ValidationError):
                    ExecutionCreate.model_validate(self.data(**changes))

    def test_unknown_times_are_preserved_without_fabricating_completion_time(self):
        execution = ExecutionCreate.model_validate(
            self.data(claimed_started_at=None, claimed_finished_at=None)
        )
        self.assertEqual(execution.completion_claim, "completed")
        self.assertIsNone(execution.claimed_started_at)
        self.assertIsNone(execution.claimed_finished_at)

    def test_only_completed_claim_can_include_completion_time(self):
        for claim in ("partially_completed", "not_completed", "unknown"):
            with self.subTest(claim=claim):
                with self.assertRaisesRegex(ValidationError, "未声称已完成"):
                    ExecutionCreate.model_validate(self.data(completion_claim=claim))

    def test_time_order_uses_actual_instant_and_allows_equal_recorded_times(self):
        for finish in ("2026-11-02T01:20:00Z", "2026-11-02T09:00:00+08:00"):
            with self.subTest(finish=finish):
                ExecutionCreate.model_validate(self.data(claimed_finished_at=finish))
        with self.assertRaisesRegex(ValidationError, "不能早于"):
            ExecutionCreate.model_validate(
                self.data(claimed_finished_at="2026-11-02T09:30:00+09:00")
            )


if __name__ == "__main__":
    unittest.main()