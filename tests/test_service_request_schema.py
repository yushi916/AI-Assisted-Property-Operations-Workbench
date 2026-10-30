import unittest
from datetime import timedelta

from pydantic import ValidationError

from schemas import ServiceRequestCreate


def valid_payload():
    return {
        "request_id": "7ca16b36-0bb3-4637-93e8-5dc67a8bed9c",
        "service_category": "cleaning",
        "description": "  合成演示：居民反映大堂仍有垃圾。  ",
        "source": "web",
        "risk_status": "unknown",
        "location_id": "LOC-A-LOBBY",
        "observed_at": "2026-10-28T09:50:00+08:00",
    }


class TestServiceRequestCreate(unittest.TestCase):
    def test_valid_input_preserves_description_and_timezone(self):
        payload = valid_payload()
        report = ServiceRequestCreate.model_validate(payload)
        self.assertEqual(report.description, payload["description"])
        self.assertEqual(report.observed_at.utcoffset(), timedelta(hours=8))

    def test_unknown_location_and_observation_time_are_allowed(self):
        payload = valid_payload()
        payload["location_id"] = None
        payload["observed_at"] = None
        report = ServiceRequestCreate.model_validate(payload)
        self.assertIsNone(report.location_id)
        self.assertIsNone(report.observed_at)

    def test_blank_description_is_rejected(self):
        payload = valid_payload()
        payload["description"] = " \t\n"
        with self.assertRaisesRegex(ValidationError, "不能仅包含空白字符"):
            ServiceRequestCreate.model_validate(payload)

    def test_observation_time_without_timezone_is_rejected(self):
        payload = valid_payload()
        payload["observed_at"] = "2026-10-28T09:50:00"
        with self.assertRaisesRegex(ValidationError, "timezone_aware"):
            ServiceRequestCreate.model_validate(payload)

    def test_client_cannot_supply_received_at(self):
        payload = valid_payload()
        payload["received_at"] = "2026-10-28T10:00:00+08:00"
        with self.assertRaisesRegex(ValidationError, "extra_forbidden"):
            ServiceRequestCreate.model_validate(payload)