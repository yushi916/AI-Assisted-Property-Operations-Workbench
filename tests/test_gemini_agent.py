import json
import unittest
from datetime import UTC, datetime
from unittest.mock import patch

import httpx
from google import genai

from agent_evidence import AgentEvidence
from agent_runner import run_agent
from gemini_agent import GeminiEvidenceModel, GeminiNotConfigured


class TestGeminiAdapter(unittest.TestCase):
    def setUp(self):
        self.report_ref = "report:00000000-0000-0000-0000-000000000001"
        self.evidence = AgentEvidence(
            report_ref=self.report_ref,
            source_rule_version="report-evidence-v1",
            checked_at=datetime.now(UTC),
            records={self.report_ref: {"description": "合成 A 大堂案例"}},
        )

    def test_sdk_sends_one_request_after_reading_evidence(self):
        calls = []
        requests = []
        suggestion = {
            "observations": [
                {"text": "收到居民观察。", "evidence_refs": [self.report_ref]},
            ],
            "verification_items": ["现场核实。"],
        }

        def read_evidence():
            calls.append("read")
            return self.evidence

        def respond(request):
            calls.append("http")
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={"candidates": [{
                "finishReason": "STOP",
                "content": {
                    "role": "model",
                    "parts": [{"text": json.dumps(suggestion, ensure_ascii=False)}],
                },
            }]})

        real_client = genai.Client

        def client_factory(**kwargs):
            kwargs["http_options"].httpx_client = httpx.Client(
                transport=httpx.MockTransport(respond), trust_env=False,
            )
            kwargs["http_options"].httpx_async_client = httpx.AsyncClient(
                trust_env=False,
            )
            return real_client(**kwargs)

        with (
            patch.dict("os.environ", {
                "GEMINI_API_KEY": "synthetic-test-key",
                "GEMINI_MODEL": "test-model",
            }),
            patch("gemini_agent.genai.Client", side_effect=client_factory),
        ):
            result = run_agent(GeminiEvidenceModel(), read_evidence)

        self.assertEqual(result.status, "pending_review")
        self.assertEqual(result.suggestion.model_dump(), suggestion)
        self.assertEqual(calls, ["read", "http"])

        request = requests[0]
        self.assertNotIn("tools", request)
        self.assertNotIn("toolConfig", request)
        self.assertEqual(
            request["generationConfig"]["responseMimeType"], "application/json",
        )
        self.assertEqual(
            json.loads(request["contents"][0]["parts"][0]["text"]),
            self.evidence.model_dump(mode="json"),
        )

    def test_missing_key_fails_without_constructing_sdk_client(self):
        with (
            patch.dict("os.environ", {"GEMINI_API_KEY": ""}),
            patch("gemini_agent.genai.Client") as client,
        ):
            with self.assertRaises(GeminiNotConfigured):
                GeminiEvidenceModel().suggest(self.evidence)
            client.assert_not_called()