import json
import unittest
from datetime import UTC, datetime
from unittest.mock import Mock

from agent_evidence import AgentEvidence
from agent_runner import MAX_EVIDENCE_BYTES, MAX_OUTPUT_BYTES, run_agent


REPORT_REF = "report:demo"
EXECUTION_REF = "execution:demo"


class TestAgentRunner(unittest.TestCase):
    def setUp(self):
        self.evidence = AgentEvidence(
            report_ref=REPORT_REF,
            source_rule_version="report-evidence-v1",
            checked_at=datetime(2026, 11, 7, 2, tzinfo=UTC),
            records={
                REPORT_REF: {"observed_at": "2026-11-07T09:40:00+08:00"},
                EXECUTION_REF: {
                    "claimed_finished_at": "2026-11-07T09:20:00+08:00",
                    "report_comparison": {"relation": "after"},
                },
            },
        )
        self.model = Mock()
        self.draft = {
            "observations": [{
                "text": "居民观察晚于声称完成。",
                "evidence_refs": [REPORT_REF, EXECUTION_REF],
            }],
            "verification_items": ["现场原因仍待核实。"],
        }
        self.model.suggest.return_value = json.dumps(self.draft)
        self.read = Mock(return_value=self.evidence)

    def assert_failure(self, outcome, stage, code, evidence=None):
        self.assertEqual(outcome.status, "failed")
        self.assertEqual((outcome.failure_stage, outcome.error_code), (stage, code))
        self.assertIs(outcome.evidence, evidence)
        self.assertIsNone(outcome.suggestion)

    def test_reads_evidence_before_single_suggestion(self):
        sequence = Mock()
        sequence.attach_mock(self.read, "read")
        sequence.attach_mock(self.model.suggest, "suggest")

        outcome = run_agent(self.model, self.read)

        self.assertEqual(outcome.status, "pending_review")
        self.assertIs(outcome.evidence, self.evidence)
        self.assertEqual(outcome.suggestion.model_dump(), self.draft)
        self.assertEqual([entry[0] for entry in sequence.mock_calls], ["read", "suggest"])
        self.read.assert_called_once_with()
        self.model.suggest.assert_called_once_with(self.evidence)
        
    def test_evidence_read_failure_stops_before_suggestion(self):
        self.read.side_effect = OSError("内部详情")

        outcome = run_agent(self.model, self.read)

        self.assert_failure(outcome, "evidence_read", "OSError")
        self.model.suggest.assert_not_called()

    def test_second_model_call_timeout_retains_evidence(self):
        self.model.suggest.side_effect = TimeoutError("内部详情")

        outcome = run_agent(self.model, self.read)

        self.assert_failure(outcome, "suggestion_request", "TimeoutError", self.evidence)

    def test_invalid_json_is_a_validation_failure(self):
        self.model.suggest.return_value = "not JSON"

        outcome = run_agent(self.model, self.read)

        self.assert_failure(outcome, "validation", "ValidationError", self.evidence)

    def test_invented_reference_is_distinct_from_invalid_json(self):
        self.draft["observations"][0]["evidence_refs"].append("execution:invented")
        self.model.suggest.return_value = json.dumps(self.draft)

        outcome = run_agent(self.model, self.read)

        self.assert_failure(outcome, "validation", "EvidenceReferenceError", self.evidence)

    def test_size_limits_are_explicit_failures(self):
        self.evidence.records[REPORT_REF]["description"] = "a" * MAX_EVIDENCE_BYTES
        outcome = run_agent(self.model, self.read)
        self.assert_failure(outcome, "evidence_read", "EvidenceTooLarge")
        self.model.suggest.assert_not_called()

        self.evidence.records[REPORT_REF].pop("description")
        self.model.suggest.return_value = "a" * (MAX_OUTPUT_BYTES + 1)
        outcome = run_agent(self.model, self.read)
        self.assert_failure(outcome, "validation", "OutputTooLarge", self.evidence)
