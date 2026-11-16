"""Frozen R1 transport/replay regression. Mock outputs do not prove model faithfulness."""

import copy
import io
import json
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
from google import genai

from agent_evidence import validate_agent_suggestion
from evaluate_water_faithfulness import digest, prepare_replay, replay
from gemini_agent import AUTOMATIC_INSTRUCTIONS, AUTOMATIC_PROMPT_VERSION, GeminiEvidenceModel


BASELINE = Path(__file__).resolve().parents[1] / "docs/fixtures/r1-record-scope-baseline-20261115.json"


class RecordScopeReplayTests(unittest.TestCase):
    def setUp(self):
        self.original_bytes = BASELINE.read_bytes()
        self.bundle = json.loads(self.original_bytes)

    def tearDown(self):
        self.assertEqual(BASELINE.read_bytes(), self.original_bytes)

    def test_exact_replay_preserves_all_records_times_and_query_warnings(self):
        before = copy.deepcopy(self.bundle)
        evidence = prepare_replay(self.bundle)
        self.assertEqual(evidence.model_dump(mode="json"), self.bundle["selected_run"]["evidence"])
        self.assertEqual(digest(evidence.model_dump(mode="json")), self.bundle["evidence_sha256"])
        self.assertEqual(before, self.bundle)
        self.bundle["selected_run"]["evidence"]["records"][evidence.report_ref]["description"] = "changed"
        with self.assertRaisesRegex(ValueError, "摘要不一致"):
            prepare_replay(self.bundle)

    def test_actual_sdk_body_uses_exact_r1_input_and_new_prompt_once(self):
        evidence = prepare_replay(self.bundle)
        requests = []

        def respond(request):
            requests.append(json.loads(request.content))
            # Reuse the known-bad historical output to expose the validator's limit.
            return httpx.Response(200, json={"candidates": [{
                "finishReason": "STOP", "content": {"role": "model", "parts": [
                    {"text": json.dumps(self.bundle["selected_run"]["suggestion"], ensure_ascii=False)},
                ]},
            }]})

        real_client = genai.Client

        def factory(**kwargs):
            kwargs["http_options"].httpx_client = httpx.Client(transport=httpx.MockTransport(respond), trust_env=False)
            kwargs["http_options"].httpx_async_client = httpx.AsyncClient(trust_env=False)
            return real_client(**kwargs)

        with (
            patch.dict("os.environ", {"GEMINI_API_KEY": "synthetic-test-key", "GEMINI_MODEL": "test-model"}),
            patch("gemini_agent.genai.Client", side_effect=factory),
        ):
            raw = GeminiEvidenceModel().suggest(evidence)
        self.assertEqual(len(requests), 1)
        body = requests[0]
        self.assertEqual(json.loads(body["contents"][0]["parts"][0]["text"]), self.bundle["selected_run"]["evidence"])
        self.assertEqual(body["systemInstruction"]["parts"][0]["text"], AUTOMATIC_INSTRUCTIONS)
        self.assertNotEqual(AUTOMATIC_INSTRUCTIONS, self.bundle["reference_before_prompt"])
        self.assertEqual(json.loads(raw), self.bundle["selected_run"]["suggestion"])
        self.assertNotIn("tools", body)

    def test_known_semantic_failure_passes_structure_and_is_not_silently_repaired(self):
        saved = self.bundle["selected_run"]["suggestion"]
        parsed = validate_agent_suggestion(json.dumps(saved, ensure_ascii=False), prepare_replay(self.bundle))
        self.assertEqual(parsed.model_dump(mode="json"), saved)
        self.assertEqual(self.bundle["semantic_review"]["status"], "needs_revision")
        assessment = saved["candidate_assessments"][1]
        self.assertNotIn("execution:5e49d838-1068-4190-9824-810f4e4a1cd2", assessment["evidence_refs"])
        self.assertIn("尚未完成", saved["uncertainties"][1]["text"])

    def run_frozen_replay(self, mode):
        inputs = []
        raw = json.dumps(self.bundle["selected_run"]["suggestion"], ensure_ascii=False)
        if mode == "invalid_ref":
            bad = copy.deepcopy(self.bundle["selected_run"]["suggestion"])
            bad["candidate_assessments"][1]["evidence_refs"].append("execution:not-in-this-snapshot")
            raw = json.dumps(bad, ensure_ascii=False)

        def suggest(evidence):
            inputs.append(evidence.model_dump(mode="json"))
            if mode == "request_error":
                raise TimeoutError()
            return raw

        model = Mock(model_name=self.bundle["selected_run"]["model_name"])
        model.suggest.side_effect = suggest
        with tempfile.TemporaryDirectory() as folder:
            args = Namespace(input=BASELINE, runs=2, output_dir=Path(folder))
            with (
                patch.dict("os.environ", {"GEMINI_API_KEY": "synthetic-test-key"}),
                patch("evaluate_water_faithfulness.GeminiEvidenceModel", return_value=model),
                redirect_stdout(io.StringIO()),
            ):
                replay(args)
            files = list(Path(folder).glob("r1-after-*.json"))
            self.assertEqual(len(files), 1 if mode == "request_error" else 2)
            results = [json.loads(path.read_text(encoding="utf-8")) for path in files]
        final = max(results, key=lambda value: len(value["runs"]))
        self.assertEqual(len(inputs), 1 if mode == "request_error" else 2)
        self.assertTrue(all(value == self.bundle["selected_run"]["evidence"] for value in inputs))
        self.assertTrue(final["input_matches_saved_evidence"])
        self.assertEqual(final["input_sha256"], self.bundle["evidence_sha256"])
        self.assertEqual(final["prompt_version"], AUTOMATIC_PROMPT_VERSION)
        self.assertEqual(final["baseline_semantic_review"], self.bundle["semantic_review"])
        for row in final["runs"]:
            self.assertEqual(row["semantic_review"], {"status": "not_reviewed", "checks": self.bundle["semantic_review"]["checks"]})
        return final, raw

    def test_two_predeclared_replays_do_not_turn_known_bad_text_into_semantic_pass(self):
        final, raw = self.run_frozen_replay("saved_bad_output")
        for row in final["runs"]:
            self.assertEqual(row["structural_checks"], "passed")
            self.assertEqual(row["raw_output"], raw)
            self.assertEqual(row["suggestion"], self.bundle["selected_run"]["suggestion"])

    def test_request_failure_is_preserved_and_stops_second_call(self):
        final, _ = self.run_frozen_replay("request_error")
        self.assertEqual(final["runs"][0]["request_error"], "TimeoutError")
        self.assertEqual(final["runs"][0]["structural_checks"], "not_run")

    def test_invalid_reference_is_preserved_without_repair_or_extra_attempts(self):
        final, raw = self.run_frozen_replay("invalid_ref")
        for row in final["runs"]:
            self.assertEqual(row["structural_checks"], "failed")
            self.assertEqual(row["validation_error"], "EvidenceReferenceError")
            self.assertEqual(row["raw_output"], raw)
            self.assertNotIn("suggestion", row)
