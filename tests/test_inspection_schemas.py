import unittest
from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import ValidationError

from inspection_schemas import InspectionCreate


class InspectionSchemaTests(unittest.TestCase):
    def payload(self, **changes):
        values = {
            "request_id": str(uuid4()),
            "plan_id": str(uuid4()),
            "inspector_name": " 合成巡查员 ",
            "description": "  合成巡查：A区大堂发现散落垃圾。  ",
            "finding": "issue_observed",
            "location_id": "LOC-A-LOBBY",
            "observed_at": "2026-11-01T09:50:00+08:00",
        }
        values.update(changes)
        return values

    def test_valid_observation_preserves_original_text(self):
        data = self.payload()
        result = InspectionCreate(**data)
        self.assertEqual(result.request_id, UUID(data["request_id"]))
        self.assertEqual(result.plan_id, UUID(data["plan_id"]))
        self.assertEqual(result.inspector_name, data["inspector_name"])
        self.assertEqual(result.description, data["description"])
        self.assertEqual(result.location_id, "LOC-A-LOBBY")
        self.assertEqual(
            result.observed_at.astimezone(UTC),
            datetime(2026, 11, 1, 1, 50, tzinfo=UTC),
        )

    def test_each_finding_can_keep_unknown_point_and_time(self):
        for finding in ("no_issue_observed", "issue_observed", "unable_to_verify"):
            with self.subTest(finding=finding):
                result = InspectionCreate(**self.payload(
                    finding=finding, location_id=None, observed_at=None,
                ))
                self.assertEqual(result.finding, finding)
                self.assertIsNone(result.location_id)
                self.assertIsNone(result.observed_at)

    def test_omitted_point_and_time_remain_unknown(self):
        data = self.payload()
        del data["location_id"]
        del data["observed_at"]
        result = InspectionCreate(**data)
        self.assertIsNone(result.model_dump()["location_id"])
        self.assertIsNone(result.model_dump()["observed_at"])

    def test_blank_or_too_long_text_is_rejected(self):
        for name, limit in (("inspector_name", 120), ("description", 2000)):
            for value in ("", "   ", "\t\n", "x" * (limit + 1)):
                with self.subTest(field=name, length=len(value)):
                    with self.assertRaises(ValidationError):
                        InspectionCreate(**self.payload(**{name: value}))
            InspectionCreate(**self.payload(**{name: "x" * limit}))

    def test_invalid_location_or_multiple_points_is_rejected(self):
        for value in ("", "   ", "x" * 65, ["LOC-A-LOBBY", "LOC-B-LOBBY"]):
            with self.subTest(location_id=value):
                with self.assertRaises(ValidationError):
                    InspectionCreate(**self.payload(location_id=value))

    def test_invalid_or_missing_finding_is_rejected(self):
        for value in ("completed", "passed", "unknown", ""):
            with self.subTest(finding=value):
                with self.assertRaises(ValidationError):
                    InspectionCreate(**self.payload(finding=value))
        data = self.payload()
        del data["finding"]
        with self.assertRaises(ValidationError):
            InspectionCreate(**data)

    def test_observed_time_requires_timezone(self):
        for value in ("2026-11-01T09:50:00", "not-a-time", ""):
            with self.subTest(observed_at=value):
                with self.assertRaises(ValidationError):
                    InspectionCreate(**self.payload(observed_at=value))

    def test_invalid_identifiers_are_rejected(self):
        for name in ("request_id", "plan_id"):
            with self.subTest(field=name):
                with self.assertRaises(ValidationError):
                    InspectionCreate(**self.payload(**{name: "not-a-uuid"}))

    def test_server_fields_and_execution_claims_are_rejected(self):
        for name, value in (
            ("id", str(uuid4())),
            ("recorded_at", "2026-11-01T02:00:00Z"),
            ("version", 1),
            ("completion_claim", "completed"),
            ("location_ids", ["LOC-A-LOBBY"]),
        ):
            with self.subTest(field=name):
                with self.assertRaises(ValidationError):
                    InspectionCreate(**self.payload(**{name: value}))


if __name__ == "__main__":
    unittest.main()