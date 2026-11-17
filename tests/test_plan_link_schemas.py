import unittest
from uuid import UUID, uuid4

from pydantic import ValidationError

from plan_link_schemas import RequestPlanLinkCreate


class RequestPlanLinkSchemaTests(unittest.TestCase):
    def payload(self, **changes):
        data = {
            "request_id": str(uuid4()),
            "plan_id": str(uuid4()),
            "linked_by": " 合成关联人 ",
            "reason": "  合成关联：居民反映A区大堂有垃圾，选入本次保洁计划共同核对。  ",
        }
        data.update(changes)
        return data

    def test_valid_input_preserves_original_text_and_parses_ids(self):
        data = self.payload()
        result = RequestPlanLinkCreate(**data)
        self.assertEqual(result.request_id, UUID(data["request_id"]))
        self.assertEqual(result.plan_id, UUID(data["plan_id"]))
        self.assertEqual(result.linked_by, data["linked_by"])
        self.assertEqual(result.reason, data["reason"])
        self.assertEqual(result.model_dump(mode="json"), data)

    def test_all_four_fields_are_required(self):
        for name in ("request_id", "plan_id", "linked_by", "reason"):
            with self.subTest(field=name):
                data = self.payload()
                del data[name]
                with self.assertRaises(ValidationError):
                    RequestPlanLinkCreate(**data)

    def test_invalid_identifiers_are_rejected(self):
        for name in ("request_id", "plan_id"):
            for value in ("not-a-uuid", "", None):
                with self.subTest(field=name, value=value):
                    with self.assertRaises(ValidationError):
                        RequestPlanLinkCreate(**self.payload(**{name: value}))

    def test_blank_actor_or_reason_is_rejected(self):
        for name in ("linked_by", "reason"):
            for value in ("", "   ", "\t\n", "\u3000", None):
                with self.subTest(field=name, value=value):
                    with self.assertRaises(ValidationError):
                        RequestPlanLinkCreate(**self.payload(**{name: value}))

    def test_text_length_boundaries(self):
        for name, limit in (("linked_by", 120), ("reason", 2000)):
            with self.subTest(field=name):
                result = RequestPlanLinkCreate(**self.payload(**{name: "x" * limit}))
                self.assertEqual(len(getattr(result, name)), limit)
                with self.assertRaises(ValidationError):
                    RequestPlanLinkCreate(**self.payload(**{name: "x" * (limit + 1)}))

    def test_server_fields_and_claimed_matching_facts_are_rejected(self):
        for name, value in (
            ("id", str(uuid4())), ("report_id", str(uuid4())),
            ("linked_at", "2026-11-05T13:00:00Z"), ("version", 1),
            ("status", "active"), ("service_category", "cleaning"),
            ("location_id", "LOC-A-LOBBY"),
        ):
            with self.subTest(field=name):
                with self.assertRaises(ValidationError):
                    RequestPlanLinkCreate(**self.payload(**{name: value}))


if __name__ == "__main__":
    unittest.main()