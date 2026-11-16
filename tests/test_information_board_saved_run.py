"""Offline regression: known source-state conflict is blocked, no real LLM calls."""
import copy
import json
import unittest
from pathlib import Path
from unittest.mock import Mock

from agent_evidence import AgentEvidence, EvidenceReferenceError, SourceStateConflictError, validate_agent_suggestion
from agent_runner import run_agent

BASELINE = Path(__file__).resolve().parents[1] / 'docs/fixtures/information-board-saved-runs-20261116.json'


class InformationBoardSavedRunTests(unittest.TestCase):
    def setUp(self):
        self.original = BASELINE.read_bytes()
        self.run = json.loads(self.original)[0]
        self.evidence = AgentEvidence.model_validate(self.run['evidence'])

    def tearDown(self):
        self.assertEqual(BASELINE.read_bytes(), self.original)

    def test_known_source_state_error_is_rejected_without_changing_saved_result(self):
        before = copy.deepcopy(self.run)
        records = self.evidence.records
        self.assertEqual(records['context:investigation']['candidate_refs'], [])
        self.assertEqual(records['search:work_orders']['status'], 'unsupported')
        self.assertIsNone(records['search:work_orders']['count'])
        self.assertEqual(records['search:plans']['status'], 'empty')
        raw = json.dumps(self.run['suggestion'], ensure_ascii=False)
        with self.assertRaises(SourceStateConflictError):
            validate_agent_suggestion(raw, self.evidence)
        fake = Mock(model_name='offline-saved-output-replay')
        fake.suggest.return_value = raw
        result = run_agent(fake, lambda: self.evidence)
        self.assertEqual(result.status, 'failed')
        self.assertEqual(result.failure_stage, 'validation')
        self.assertEqual(result.error_code, 'SourceStateConflictError')
        self.assertIsNone(result.suggestion)
        self.assertEqual(result.evidence.model_dump(mode='json'), self.run['evidence'])
        self.assertEqual(self.run['status'], 'pending_review')
        fake.suggest.assert_called_once()
        self.assertEqual(before, self.run)

    def test_invalid_reference_control_is_still_rejected(self):
        changed = copy.deepcopy(self.run['suggestion'])
        changed['uncertainties'][0]['evidence_refs'].append('search:missing-source')
        raw = json.dumps(changed, ensure_ascii=False)
        with self.assertRaises(EvidenceReferenceError):
            validate_agent_suggestion(raw, self.evidence)
        fake = Mock(model_name='offline-negative-control')
        fake.suggest.return_value = raw
        result = run_agent(fake, lambda: self.evidence)
        self.assertEqual(result.status, 'failed')
        self.assertEqual(result.failure_stage, 'validation')
        self.assertEqual(result.error_code, 'EvidenceReferenceError')
        fake.suggest.assert_called_once()


if __name__ == '__main__':
    unittest.main()
