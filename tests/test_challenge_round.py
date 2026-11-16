"""Offline plumbing tests; synthetic SDK replies do not evaluate model quality."""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import httpx
from google import genai

import evaluate_challenge_round as round_eval
from agent_evidence import AgentEvidence
from tests.automatic_suggestion_fixture import automatic_draft
from gemini_agent import GeminiUnexpectedResponse


class ChallengeRoundTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.bundle = Path(cls.folder.name) / 'frozen'
        cls.manifest = round_eval.prepare(cls.bundle)

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def setUp(self):
        self.folder_each = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder_each.cleanup)
        self.output = Path(self.folder_each.name) / 'results'
        self.env = patch.dict(os.environ, {'GEMINI_API_KEY': 'synthetic-test-key', 'GEMINI_MODEL': round_eval.MODEL})
        self.env.start(); self.addCleanup(self.env.stop)

    def run_fake(self, invoke):
        with redirect_stdout(io.StringIO()):
            return round_eval.run_round(self.bundle, self.output, invoke_fn=invoke)

    def test_temporary_database_preflight_preserves_both_cases_and_boundaries(self):
        _, _, bundles = round_eval.verify(self.bundle)
        self.assertEqual([b['case_id'] for b in bundles], ['H1', 'H2'])
        for b in bundles:
            p = b['preflight']
            self.assertEqual(p['business_records'], 4)
            self.assertEqual(p['coverage'], {'execution': 1, 'inspection': 1})
            self.assertTrue(p['business_data_unchanged'])
            self.assertFalse(p['business_database_connected'])
            self.assertTrue(p['same_location'])
            self.assertEqual(p['observation_relation_to_report'], 'after')
            case = b['case_id']; spec = round_eval.cases()[case]
            records = b['input']['records']
            for kind in ('report', 'execution', 'inspection'):
                self.assertEqual(records[f'{kind}:{round_eval.uid(case, kind)}']['description'], spec[kind])
            self.assertEqual(p['source_statuses']['assets'], 'unsupported')
        self.assertEqual(bundles[0]['preflight']['report_relation_to_completion'], 'before')
        self.assertEqual(bundles[1]['preflight']['report_relation_to_completion'], 'after')

    def test_two_by_two_immutable_inputs_and_unreviewed_exports(self):
        calls = []
        def fake(evidence, _path):
            calls.append(evidence.model_dump(mode='json'))
            return json.dumps(automatic_draft(evidence), ensure_ascii=False)
        result = self.run_fake(fake)
        self.assertEqual(len(calls), 4)
        self.assertEqual(calls[0], calls[1]); self.assertEqual(calls[2], calls[3])
        self.assertNotEqual(calls[0], calls[2])
        self.assertEqual(result['status'], 'finished')
        self.assertEqual(result['attempted'], ['H1-1', 'H1-2', 'H2-1', 'H2-2'])
        for case in ('H1', 'H2'):
            bundle = json.loads((self.output / f'{case}-results.json').read_text(encoding='utf-8'))
            self.assertTrue(all(x['structural_checks'] == 'passed' for x in bundle['runs']))
            self.assertTrue(all(x['semantic_review']['status'] == 'not_reviewed' for x in bundle['runs']))
            review = json.loads((self.output / f'{case}-review.json').read_text(encoding='utf-8'))
            self.assertEqual(review['author_confirmation'], 'not_reviewed')
            self.assertEqual(review['assistant_review'], 'not_reviewed')
            self.assertEqual(len(review['dimension_reviews']), 2)
            self.assertEqual(len(review['dimension_reviews'][0]['dimensions']), 8)
            self.assertTrue(all(x['fact_coverage'] == x['cited_source_support'] == 'not_reviewed' for x in review['entries']))
            html = (self.output / f'{case}-review.html').read_text(encoding='utf-8')
            self.assertIn('同一快照全部记录', html)
            self.assertIn('冻结评审标准', html)
            for ref, original in bundle['input']['records'].items():
                if ref.startswith(('report:', 'execution:', 'inspection:')):
                    self.assertIn(original['description'], html)
        with self.assertRaises(FileExistsError): self.run_fake(fake)
        self.assertEqual(len(calls), 4)

    def test_bad_structure_is_retained_without_sampling_again(self):
        count = []
        def fake(evidence, _path):
            count.append(1)
            return '{bad JSON' if len(count) == 1 else json.dumps(automatic_draft(evidence))
        self.run_fake(fake)
        self.assertEqual(len(count), 4)
        row = json.loads((self.output / 'H1-trial-1.json').read_text(encoding='utf-8'))
        self.assertEqual(row['raw_output'], '{bad JSON')
        self.assertEqual(row['structural_checks'], 'failed')

    def test_request_error_stops_and_keeps_partial_results_and_redacts_key(self):
        calls = []
        class FakeError(Exception):
            message = 'failed synthetic-test-key'; code = 503; status = 'UNAVAILABLE'
        def fake(evidence, _path):
            calls.append(1)
            if len(calls) == 2: raise FakeError()
            return json.dumps(automatic_draft(evidence))
        result = self.run_fake(fake)
        self.assertEqual(len(calls), 2)
        self.assertEqual(result['status'], 'stopped_on_request_error')
        row = json.loads((self.output / 'H1-trial-2.json').read_text(encoding='utf-8'))
        self.assertEqual(row['http_code'], 503)
        self.assertIn('[REDACTED]', row['api_message'])
        self.assertNotIn('synthetic-test-key', row['api_message'])
        self.assertTrue((self.output / 'H1-review.html').exists())
        self.assertFalse((self.output / 'H2-trial-1.json').exists())
        with self.assertRaises(FileExistsError): self.run_fake(fake)
        self.assertEqual(len(calls), 2)

    def test_pending_slot_blocks_reentry_without_any_request(self):
        self.output.mkdir()
        (self.output / 'round-state.json').write_text('{"status":"started"}', encoding='utf-8')
        with patch('evaluate_challenge_round.invoke') as fake:
            with self.assertRaises(FileExistsError): self.run_fake(fake)
            fake.assert_not_called()

    def test_missing_key_and_wrong_model_stop_before_state_or_request(self):
        with patch('evaluate_challenge_round.invoke') as fake:
            with patch.dict(os.environ, {'GEMINI_API_KEY': ''}):
                with self.assertRaises(ValueError): self.run_fake(fake)
            with patch.dict(os.environ, {'GEMINI_MODEL': 'other-model'}):
                with self.assertRaises(ValueError): self.run_fake(fake)
            fake.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_modified_input_and_rubric_fail_before_request(self):
        import shutil
        for name in ('H1.json', 'rubric.json'):
            clone = Path(self.folder_each.name) / name.replace('.', '-')
            shutil.copytree(self.bundle, clone)
            value = json.loads((clone / name).read_text(encoding='utf-8'))
            value['changed'] = True
            (clone / name).write_text(json.dumps(value), encoding='utf-8')
            with self.assertRaises(ValueError): round_eval.verify(clone)

    def test_truncated_sdk_response_kept_before_adapter_rejects_it(self):
        _, _, bundles = round_eval.verify(self.bundle)
        evidence = AgentEvidence.model_validate(bundles[0]['input'])
        calls = []
        def respond(request):
            calls.append(json.loads(request.content))
            return httpx.Response(200, json={'candidates': [{'finishReason': 'MAX_TOKENS', 'content': {'role': 'model', 'parts': [{'text': '{partial'}]}}],
                'usageMetadata': {'promptTokenCount': 10, 'candidatesTokenCount': 2, 'totalTokenCount': 12}})
        real_client = genai.Client
        def factory(**kwargs):
            kwargs['http_options'].httpx_client = httpx.Client(transport=httpx.MockTransport(respond), trust_env=False)
            kwargs['http_options'].httpx_async_client = httpx.AsyncClient(trust_env=False)
            return real_client(**kwargs)
        path = Path(self.folder_each.name) / 'provider.json'
        with patch('gemini_agent.genai.Client', side_effect=factory):
            with self.assertRaises(GeminiUnexpectedResponse):
                round_eval.invoke(evidence, path)
        self.assertEqual(len(calls), 1)
        saved = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(saved['text'], '{partial')
        self.assertIn('MAX_TOKENS', saved['finish_reasons'][0])
        self.assertEqual(saved['usage_metadata']['total_token_count'], 12)
        self.assertEqual(calls[0]['generationConfig']['maxOutputTokens'], 4096)


if __name__ == '__main__':
    unittest.main()
