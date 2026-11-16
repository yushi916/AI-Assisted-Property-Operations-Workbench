"""Audit transport fidelity, not the semantic quality of a mocked model response."""

import copy
import io
import json
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from google import genai

from agent_evidence import AgentEvidence
from automatic_agent_evidence import add_beijing_time_labels, read_automatic_agent_evidence
from database import get_db
from evaluate_water_faithfulness import digest, prepare_replay, replay, request_error_details
from models import ServiceRequest
from routers.agent_runs import router
from tests.automatic_evidence_fixture import AutomaticEvidenceFixture
from tests.automatic_suggestion_fixture import automatic_draft


WATER_CASES = [
    ("09cece20-85a5-460b-a916-eba61d2809a7", "ROOM-302", "09:20",
     "早上洗漱时，厨房和卫生间的冷水龙头都不出水了。家里的总阀没有动过，邻居家什么情况我还不知道，麻烦安排人来看一下。"),
    ("758b541a-f8a0-4f8a-ad06-91a5d12e832d", "ROOM-301", "09:10",
     "我家厨房和卫生间都没有冷水，昨天晚上用着还正常。家里没有人在维修，麻烦帮忙查一下。"),
    ("6d1b876f-e1ad-47cf-9b15-927a6213aab8", "CORRIDOR", "09:15",
     "三层公共走廊的清洁水龙头打开后只有很细的一股水，关上再开还是这样。我只试了这个龙头，住户家里的情况不清楚。"),
    ("6e152d52-c6d1-41ea-84f9-c1df57e286f2", "OTHER-302", "09:25",
     "厨房的冷水龙头没有水，卫生间的龙头出水正常。刚才又试了一次还是这样，麻烦来看看厨房这边。"),
]


class AgentInputFidelityTests(AutomaticEvidenceFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        with self.sessions.begin() as db:
            db.delete(db.get(ServiceRequest, self.report_id))
            for id_, point, clock, description in WATER_CASES:
                db.add(ServiceRequest(
                    id=UUID(id_), request_id=uuid4(), service_category="water_supply",
                    source="web", risk_status="unknown", location_id=point,
                    description=description,
                    observed_at=datetime.fromisoformat(f"2026-10-13T{clock}:00+08:00"),
                    received_at=datetime.fromisoformat("2026-11-13T21:45:54+08:00"),
                ))
        self.report_id = UUID(WATER_CASES[0][0])

    def test_w1_w4_database_to_actual_sdk_body_preserves_scope_and_unknowns(self):
        before = self.snapshot()
        self.captured_requests = []
        def respond(request):
            body = json.loads(request.content)
            self.captured_requests.append(body)
            evidence = AgentEvidence.model_validate_json(body["contents"][0]["parts"][0]["text"])
            return httpx.Response(200, json={"candidates": [{
                "finishReason": "STOP", "content": {"role": "model", "parts": [
                    {"text": json.dumps(automatic_draft(evidence), ensure_ascii=False)},
                ]},
            }]})
        real_client = genai.Client
        def client_factory(**kwargs):
            kwargs["http_options"].httpx_client = httpx.Client(
                transport=httpx.MockTransport(respond), trust_env=False,
            )
            kwargs["http_options"].httpx_async_client = httpx.AsyncClient(trust_env=False)
            return real_client(**kwargs)
        def db_dependency():
            with self.sessions() as db:
                yield db
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_db] = db_dependency
        with (
            patch.dict("os.environ", {"GEMINI_API_KEY": "synthetic-test-key", "GEMINI_MODEL": "test-model"}),
            patch("gemini_agent.genai.Client", side_effect=client_factory),
            TestClient(app) as client,
        ):
            result = client.post(f"/api/service-requests/{self.report_id}/agent-runs",
                                 json={"request_id": str(uuid4())})
        self.assertEqual(result.status_code, 201, result.text)
        self.assertEqual(result.json()["status"], "pending_review")
        self.assertEqual(len(self.captured_requests), 1)
        payload = json.loads(self.captured_requests[0]["contents"][0]["parts"][0]["text"])
        # Exact equality verifies the stored snapshot is the model's actual JSON input.
        self.assertEqual(payload, result.json()["evidence"])
        records = payload["records"]
        for id_, point, clock, description in WATER_CASES[:3]:
            row = records[f"report:{id_}"]
            self.assertEqual(row["description"], description)
            self.assertEqual(row["location_id"], point)
            self.assertEqual(row["risk_status"], "unknown")
            expected = datetime.fromisoformat(f"2026-10-13T{clock}:00+08:00")
            self.assertEqual(datetime.fromisoformat(row["observed_at"]), expected)
            self.assertEqual(row["display_times_beijing"]["observed_at"],
                             f"2026-10-13 {clock}:00（北京时间）")
            self.assertNotIn("request_id", row)
        self.assertNotIn(f"report:{WATER_CASES[3][0]}", records)
        self.assertEqual(set(records["context:investigation"]["candidate_refs"]),
                         {f"report:{row[0]}" for row in WATER_CASES[1:3]})
        self.assertFalse(any(ref.startswith(("plan:", "link:")) for ref in records))
        after = self.snapshot()
        self.assertEqual({k: v for k, v in before.items() if k != "agent_runs"},
                         {k: v for k, v in after.items() if k != "agent_runs"})

    def test_unknown_observation_is_not_replaced_by_later_receipt(self):
        with self.sessions.begin() as db:
            db.get(ServiceRequest, self.report_id).observed_at = None
        with self.sessions() as db:
            evidence = read_automatic_agent_evidence(db, self.report_id)
        row = evidence.records[evidence.report_ref]
        self.assertIsNone(row["observed_at"])
        self.assertIsNone(row["display_times_beijing"]["observed_at"])
        self.assertEqual(row["display_times_beijing"]["received_at"],
                         "2026-11-13 21:45:54（北京时间）")
        self.assertEqual(row["description"], WATER_CASES[0][3])


class BeijingDisplayTests(unittest.TestCase):
    def test_conversion_preserves_originals_and_handles_midnight_and_claimed_times(self):
        record = {"observed_at": "2026-10-12T16:05:00Z", "claimed_finished_at": "2026-10-13T00:50:00+08:00",
                  "claimed_started_at": None, "description": "只试了厨房冷水，热水和其他房间未检查。"}
        before = copy.deepcopy(record)
        enriched = add_beijing_time_labels(record)
        self.assertEqual(record, before)
        self.assertEqual({k: v for k, v in enriched.items() if k != "display_times_beijing"}, before)
        self.assertEqual(enriched["display_times_beijing"], {
            "observed_at": "2026-10-13 00:05:00（北京时间）",
            "claimed_finished_at": "2026-10-13 00:50:00（北京时间）", "claimed_started_at": None,
        })

    def test_naive_or_invalid_times_are_not_guessed_and_unrelated_text_is_untouched(self):
        row = add_beijing_time_labels({"observed_at": "2026-10-13T09:20:00", "received_at": "bad-time",
                                       "description": "2026-10-13T01:20:00Z是居民正文的一部分"})
        self.assertEqual(row["display_times_beijing"], {"observed_at": None, "received_at": None})
        self.assertEqual(row["description"], "2026-10-13T01:20:00Z是居民正文的一部分")


class FrozenReplayTests(unittest.TestCase):
    def baseline(self):
        seed = f"report:{WATER_CASES[0][0]}"
        evidence = AgentEvidence(
            report_ref=seed, source_rule_version="automatic-evidence-v1",
            checked_at=datetime.fromisoformat("2026-11-14T10:15:36+00:00"),
            records={seed: {"description": WATER_CASES[0][3], "observed_at": "2026-10-13T01:20:00Z"},
                     "context:investigation": {"candidate_refs": []}, "search:assets": {"status": "unsupported"}},
        ).model_dump(mode="json")
        return {"selected_run": {"id": "saved-before", "model_name": "test-model", "evidence": evidence},
                "evidence_sha256": digest(evidence)}

    def test_frozen_business_input_is_unchanged_and_tampering_is_rejected(self):
        baseline = self.baseline()
        before = copy.deepcopy(baseline)
        result = prepare_replay(baseline)
        self.assertEqual(before, baseline)
        self.assertEqual(result.records[result.report_ref]["description"], WATER_CASES[0][3])
        baseline["selected_run"]["evidence"]["records"][result.report_ref]["description"] = "篡改原文"
        with self.assertRaisesRegex(ValueError, "摘要不一致"):
            prepare_replay(baseline)

    def test_repeated_calls_share_frozen_input_and_do_not_claim_semantic_pass(self):
        self.run_replay(fail=False)

    def test_request_failure_is_saved_and_stops_remaining_calls(self):
        self.run_replay(fail=True)

    def test_api_error_details_are_recorded_without_the_local_key(self):
        class Rejected(Exception):
            code = 400
            status = "INVALID_ARGUMENT"
            message = "Invalid argument with synthetic-key; no field details."
        with patch.dict("os.environ", {"GEMINI_API_KEY": "synthetic-key"}):
            details = request_error_details(Rejected())
        self.assertEqual(details["api_status"], "INVALID_ARGUMENT")
        self.assertEqual(details["http_code"], 400)
        self.assertIn("no field details", details["api_message"])
        self.assertNotIn("synthetic-key", details["api_message"])
        self.assertIn("[REDACTED]", details["api_message"])

    def run_replay(self, fail):
        baseline = self.baseline()
        inputs = []
        def suggest(evidence):
            inputs.append(evidence.model_dump(mode="json"))
            if fail:
                raise TimeoutError()
            return json.dumps(automatic_draft(evidence), ensure_ascii=False)
        model = Mock(model_name="test-model")
        model.suggest.side_effect = suggest
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            source = folder / "baseline.json"
            source.write_text(json.dumps(baseline), encoding="utf-8")
            original_bytes = source.read_bytes()
            args = Namespace(input=source, runs=2, output_dir=folder / "results")
            with (patch.dict("os.environ", {"GEMINI_API_KEY": "synthetic-test-key"}),
                  patch("evaluate_water_faithfulness.GeminiEvidenceModel", return_value=model),
                  redirect_stdout(io.StringIO())):
                replay(args)
            self.assertEqual(source.read_bytes(), original_bytes)
            results = [json.loads(path.read_text(encoding="utf-8")) for path in args.output_dir.glob("*.json")]
            final = max(results, key=lambda item: len(item["runs"]))
            self.assertEqual(len(inputs), 1 if fail else 2)
            self.assertTrue(all(value == inputs[0] for value in inputs))
            for row in final["runs"]:
                self.assertEqual(row["semantic_review"]["status"], "not_reviewed")
                if fail:
                    self.assertEqual(row["request_error"], "TimeoutError")
                else:
                    self.assertEqual(row["structural_checks"], "passed")
