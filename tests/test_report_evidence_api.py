import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from unittest.mock import Mock, patch
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from adopt_intake_database import schema_signature
from agent_evidence import build_agent_evidence, validate_agent_suggestion
from agent_runner import run_agent
from automatic_agent_evidence import read_automatic_agent_evidence
from tests.automatic_suggestion_fixture import automatic_draft
from database import Base, create_db_engine, engine as project_engine, get_db
from models import (
    AgentRun, ExecutionLocation, ExecutionRecord, InspectionRecord, PlanInstance,
    PlanLocation, RequestPlanLink, ServiceRequest,
)
from report_evidence_schemas import ReportEvidenceRead
from routers.agent_runs import get_agent_model, router as agent_runs_router
from routers.report_evidence import read_report_evidence, router
from seed_demo_location_hierarchy import seed_demo_location_hierarchy
from tests.location_reference import create_pre_floor_schema


ROOT = Path(__file__).resolve().parents[1]
TABLES = ("locations", "service_requests", "plan_instances", "plan_locations",
          "execution_records", "execution_locations", "inspection_records", "request_plan_links")


def at(time):
    return datetime.fromisoformat("2026-11-01T" + time + "+08:00")


class ReportEvidenceAPITests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "case.db"
        self.engine = create_db_engine(self.path)
        self.addCleanup(self.engine.dispose)
        guard = patch.object(project_engine, "connect", side_effect=AssertionError("禁止连接项目库"))
        guard.start()
        self.addCleanup(guard.stop)
        config = Config(str(ROOT / "alembic.ini"))
        with self.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0006_request_plan_links")
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)
        seed_demo_location_hierarchy(self.engine)
        with self.sessions.begin() as db:
            reports = [ServiceRequest(request_id=uuid4(), service_category="cleaning",
                description=" 合成居民报事 ", source="web", risk_status="unknown",
                location_id="LOC-A-LOBBY", observed_at=at("09:40"), received_at=at("18:00"))
                for _ in range(2)]
            plans = [PlanInstance(request_id=uuid4(), title="合成保洁计划", service_category="cleaning",
                requirements="分别核对大堂", owner_name="合成负责人",
                planned_start_at=at("09:00"), planned_end_at=at("09:30"),
                locations=[PlanLocation(location_id=x) for x in ("LOC-A-LOBBY", "LOC-B-LOBBY")])
                for _ in range(2)]
            db.add_all(reports + plans)
            db.flush()
            self.reports, self.plans = [x.id for x in reports], [x.id for x in plans]

        def test_db():
            with self.sessions() as db:
                yield db

        self.app = FastAPI()
        self.app.include_router(router)
        self.app.dependency_overrides[get_db] = test_db
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)

    def url(self, report=None):
        return f"/api/service-requests/{report or self.reports[0]}/evidence-check"

    def rows(self):
        with self.engine.connect() as connection:
            return {name: connection.exec_driver_sql(f'SELECT * FROM "{name}" ORDER BY 1, 2').all()
                    for name in TABLES}

    def link(self, plan=None, report=None, revoked=False):
        with self.sessions.begin() as db:
            item = RequestPlanLink(request_id=uuid4(), report_id=report or self.reports[0],
                plan_id=plan or self.plans[0], linked_by="合成人员", reason=" 合成核对理由 ",
                linked_at=at("12:00"))
            if revoked:
                item.status, item.version = "revoked", 2
                item.revocation_request_id = uuid4()
                item.revoked_at, item.revoked_by, item.revoke_reason = at("13:00"), "合成撤销人", "选错计划"
            db.add(item)
            db.flush()
            return str(item.id)

    def evidence(self, point="LOC-A-LOBBY", plan=None, count=1, claim="completed"):
        executions, inspections = [], []
        with self.sessions.begin() as db:
            for _ in range(count):
                execution = ExecutionRecord(request_id=uuid4(), plan_id=plan or self.plans[0],
                    executor_name="合成执行人", description=" 合成执行原文 ", completion_claim=claim,
                    claimed_finished_at=at("09:20") if claim == "completed" else None, recorded_at=at("18:00"),
                    locations=[] if point is None else [ExecutionLocation(location_id=point)])
                inspection = InspectionRecord(request_id=uuid4(), plan_id=plan or self.plans[0],
                    inspector_name="合成巡查人", description=" 合成巡查原文 ", finding="issue_observed",
                    location_id=point, observed_at=None if point is None else at("09:10"), recorded_at=at("18:00"))
                db.add_all([execution, inspection])
                db.flush()
                executions.append(str(execution.id))
                inspections.append(str(inspection.id))
        return executions, inspections

    def test_unlinked_report_and_unknown_fields_are_retained_readonly(self):
        with self.sessions.begin() as db:
            report = db.get(ServiceRequest, self.reports[0])
            report.location_id, report.observed_at = None, None
        before = self.rows()
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertIsNone(body["report"]["location_id"])
        self.assertIsNone(body["report"]["observed_at"])
        self.assertEqual((body["links"], body["linked_plans"]), ([], []))
        self.assertEqual(body["report"]["description"], " 合成居民报事 ")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(self.rows(), before)

    def test_only_active_links_supply_evidence_but_history_remains(self):
        old = self.link(plan=self.plans[1], revoked=True)
        active = self.link()
        self.link(plan=self.plans[1], report=self.reports[1])
        current_e, current_i = self.evidence()
        self.evidence(plan=self.plans[1])
        before = self.rows()
        body = self.client.get(self.url()).json()
        self.assertEqual({x["id"] for x in body["links"]}, {old, active})
        self.assertEqual([x["link_id"] for x in body["linked_plans"]], [active])
        plan = body["linked_plans"][0]
        self.assertEqual([x["id"] for x in plan["executions"]], current_e)
        self.assertEqual([x["id"] for x in plan["inspections"]], current_i)
        self.assertEqual(plan["plan"]["location_ids"], ["LOC-A-LOBBY", "LOC-B-LOBBY"])
        self.assertEqual(self.rows(), before)

    def test_location_filter_retains_unknown_records_of_all_claims(self):
        self.link()
        known_e, known_i = self.evidence()
        self.evidence(point="LOC-B-LOBBY")
        unknown_e, unknown_i = [], []
        for claim in ("completed", "partially_completed", "not_completed", "unknown"):
            e, i = self.evidence(point=None, claim=claim)
            unknown_e.extend(e)
            unknown_i.extend(i)
        plan = self.client.get(self.url()).json()["linked_plans"][0]
        self.assertTrue(plan["scope_matches"])
        self.assertEqual({x["id"] for x in plan["executions"]}, {*known_e, *unknown_e})
        self.assertEqual({x["id"] for x in plan["inspections"]}, {*known_i, *unknown_i})
        self.assertEqual((plan["excluded_execution_count"], plan["excluded_inspection_count"]), (1, 1))
        comparisons = {x["execution_id"]: x["relation"] for x in plan["report_comparisons"]}
        self.assertEqual(comparisons, {known_e[0]: "after", unknown_e[0]: "unknown"})
        self.assertTrue(any("地点未知" in x for x in plan["warnings"]))

    def test_observation_time_is_used_and_unknown_time_is_not_replaced(self):
        self.link()
        self.evidence()
        for observed, relation in ((at("09:10"), "before"), (at("09:20"), "same_time"),
                                   (at("09:40").astimezone(UTC), "after"), (None, "unknown")):
            with self.subTest(relation=relation):
                with self.sessions.begin() as db:
                    db.get(ServiceRequest, self.reports[0]).observed_at = observed
                before = self.rows()
                comparison = self.client.get(self.url()).json()["linked_plans"][0]["report_comparisons"][0]
                self.assertEqual(comparison["relation"], relation)
                self.assertEqual(self.rows(), before)

    def test_unknown_completion_and_changed_scope_are_not_used_for_time_judgments(self):
        self.link()
        e, _ = self.evidence()
        with self.sessions.begin() as db:
            db.get(ExecutionRecord, UUID(e[0])).claimed_finished_at = None
        plan = self.client.get(self.url()).json()["linked_plans"][0]
        self.assertEqual(plan["report_comparisons"][0]["relation"], "unknown")
        with self.sessions.begin() as db:
            db.get(PlanInstance, self.plans[0]).service_category = "water_supply"
        plan = self.client.get(self.url()).json()["linked_plans"][0]
        self.assertFalse(plan["scope_matches"])
        self.assertTrue(any("未能对应" in x for x in plan["warnings"]))
        self.assertEqual(plan["report_comparisons"][0]["relation"], "unknown")

    def test_all_history_and_evidence_are_read_without_list_pagination(self):
        active = self.link()
        for _ in range(121):
            self.link(revoked=True)
        executions, inspections = self.evidence(point=None, count=121)
        before = self.rows()
        body = self.client.get(self.url()+"?limit=1&offset=999").json()
        self.assertEqual(len(body["links"]), 122)
        self.assertEqual([x["link_id"] for x in body["linked_plans"]], [active])
        plan = body["linked_plans"][0]
        self.assertEqual({x["id"] for x in plan["executions"]}, set(executions))
        self.assertEqual({x["id"] for x in plan["inspections"]}, set(inspections))
        self.assertTrue(any("明确执行记录" in x for x in plan["warnings"]))
        self.assertEqual(self.rows(), before)

    def test_failed_child_read_returns_503_not_partial_success(self):
        self.link()
        self.evidence()
        before = self.rows()

        def fail(connection, cursor, statement, parameters, context, many):
            if statement.lstrip().upper().startswith("SELECT") and "FROM inspection_records" in statement:
                raise OperationalError(statement, parameters, sqlite3.OperationalError("合成取证失败"))

        event.listen(self.engine, "before_cursor_execute", fail)
        try:
            response = self.client.get(self.url())
        finally:
            event.remove(self.engine, "before_cursor_execute", fail)
        self.assertEqual(response.status_code, 503, response.text)
        self.assertNotIn("linked_plans", response.json())
        self.assertEqual(self.rows(), before)

    def test_report_links_and_children_share_one_snapshot(self):
        key = self.link()
        with closing(sqlite3.connect(self.path)) as raw:
            self.assertEqual(raw.execute("PRAGMA journal_mode=WAL").fetchone()[0], "wal")
        changed = False

        def revoke_during_read(connection, cursor, statement, parameters, context, many):
            nonlocal changed
            if changed or "FROM service_requests" not in statement:
                return
            changed = True
            with self.sessions.begin() as db:
                link = db.get(RequestPlanLink, UUID(key))
                link.status, link.version = "revoked", 2
                link.revocation_request_id, link.revoked_at = uuid4(), at("13:00")
                link.revoked_by, link.revoke_reason = "合成人员", "并发撤销"
                db.add(InspectionRecord(request_id=uuid4(), plan_id=self.plans[0],
                    inspector_name="合成巡查人", description="并发新增巡查",
                    finding="unable_to_verify", recorded_at=at("18:00")))

        event.listen(self.engine, "after_cursor_execute", revoke_during_read)
        try:
            body = self.client.get(self.url()).json()
        finally:
            event.remove(self.engine, "after_cursor_execute", revoke_during_read)
        self.assertTrue(changed)
        self.assertEqual(body["links"][0]["status"], "active")
        self.assertEqual(len(body["linked_plans"]), 1)
        self.assertEqual(body["linked_plans"][0]["inspections"], [])
        newer = self.client.get(self.url()).json()
        self.assertEqual(newer["links"][0]["status"], "revoked")
        self.assertEqual(newer["linked_plans"], [])

    def test_ids_clock_and_main_registration(self):
        before = self.rows()
        self.assertEqual(self.client.get(self.url(uuid4())).status_code, 404)
        self.assertEqual(self.client.get(self.url("bad")).status_code, 422)
        with patch("routers.report_evidence.utc_now", return_value=at("20:00")):
            body = self.client.get(self.url()+"?checked_at=2099-01-01T00:00:00Z").json()
        self.assertEqual(body["checked_at"], "2026-11-01T12:00:00Z")
        self.assertEqual(self.rows(), before)
        from main import app
        with TestClient(app) as client:
            paths = client.get("/openapi.json").json()["paths"]
        self.assertIn("get", paths["/api/service-requests/{report_id}/evidence-check"])
        self.assertIn("post", paths["/api/service-requests/{report_id}/agent-runs"])

    def agent_evidence(self):
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200, response.text)
        return build_agent_evidence(ReportEvidenceRead.model_validate(response.json()))

    def test_agent_evidence_uses_projected_fields_and_report_time_comparison(self):
        link = self.link()
        revoked = self.link(plan=self.plans[1], revoked=True)
        executions, inspections = self.evidence()
        unknown, unknown_inspections = self.evidence(point=None)
        self.evidence(point="LOC-B-LOBBY")
        self.evidence(plan=self.plans[1])
        before = self.rows()
        evidence = self.agent_evidence()
        records = evidence.records
        plan_ref = f"plan:{self.plans[0]}"
        self.assertEqual(evidence.source_rule_version, "report-evidence-v1")
        self.assertEqual(set(records[evidence.report_ref]),
                         {"service_category", "description", "risk_status", "location_id", "observed_at"})
        self.assertEqual(records[evidence.report_ref]["description"], " 合成居民报事 ")
        self.assertEqual(set(records[f"link:{link}"]), {"plan_ref", "reason"})
        self.assertNotIn(f"link:{revoked}", records)
        self.assertNotIn(f"plan:{self.plans[1]}", records)
        self.assertEqual({ref for ref in records if ref.startswith("execution:")},
                         {f"execution:{executions[0]}", f"execution:{unknown[0]}"})
        self.assertEqual({ref for ref in records if ref.startswith("inspection:")},
                         {f"inspection:{inspections[0]}", f"inspection:{unknown_inspections[0]}"})
        self.assertEqual(records[f"execution:{executions[0]}"]["report_comparison"]["relation"], "after")
        self.assertEqual(records[f"execution:{executions[0]}"]["plan_ref"], plan_ref)
        self.assertNotIn("executor_name", records[f"execution:{executions[0]}"])
        self.assertNotIn("inspector_name", records[f"inspection:{inspections[0]}"])
        self.assertEqual(records[f"execution:{unknown[0]}"]["report_comparison"]["relation"], "unknown")
        self.assertEqual(records[f"execution:{unknown[0]}"]["location_ids"], [])
        self.assertEqual(self.rows(), before)

    def test_agent_evidence_limits_mismatched_plan_to_context(self):
        link = self.link()
        self.evidence()
        with self.sessions.begin() as db:
            db.get(PlanInstance, self.plans[0]).service_category = "water_supply"
        evidence = self.agent_evidence()
        plan_ref = f"plan:{self.plans[0]}"
        self.assertEqual(set(evidence.records), {evidence.report_ref, f"link:{link}", plan_ref})
        self.assertFalse(evidence.records[plan_ref]["scope_matches"])
        self.assertTrue(evidence.records[plan_ref]["warnings"])

    def test_agent_suggestion_accepts_current_refs_and_rejects_wrong_refs(self):
        self.link()
        self.evidence()
        evidence = self.agent_evidence()
        exec_ref = next(ref for ref in evidence.records if ref.startswith("execution:"))
        payload = {
            "observations": [{"text": "观察晚于声称完成。", "evidence_refs": [evidence.report_ref, exec_ref]}],
            "verification_items": ["现场核对是否再次产生。"],
        }
        suggestion = validate_agent_suggestion(json.dumps(payload), evidence)
        self.assertEqual(suggestion.observations[0].evidence_refs, [evidence.report_ref, exec_ref])
        for ref in (f"report:{self.reports[1]}", f"execution:{uuid4()}", exec_ref.replace("execution:", "inspection:")):
            with self.subTest(ref=ref), self.assertRaisesRegex(ValueError, "证据范围外"):
                payload["observations"][0]["evidence_refs"] = [ref]
                validate_agent_suggestion(json.dumps(payload), evidence)

    def test_agent_suggestion_for_unlinked_report_requires_cited_observation_and_manual_check(self):
        evidence = self.agent_evidence()
        self.assertEqual(list(evidence.records), [evidence.report_ref])
        observation = {"text": "现有材料只有居民报事。", "evidence_refs": [evidence.report_ref]}
        payload = {"observations": [observation], "verification_items": ["查找是否存在相应计划。"]}
        self.assertEqual(len(validate_agent_suggestion(json.dumps(payload), evidence).verification_items), 1)
        for bad in (
            {**payload, "observations": [{**observation, "evidence_refs": []}]},
            {**payload, "observations": [{**observation, "text": "  "}]},
            {**payload, "verification_items": []},
            {**payload, "auto_close": True},
        ):
            with self.subTest(payload=bad), self.assertRaises(ValidationError):
                validate_agent_suggestion(json.dumps(bad), evidence)


    def test_agent_runner_uses_existing_database_evidence_for_selected_report(self):
        link = self.link()
        executions, inspections = self.evidence()
        self.link(plan=self.plans[1], report=self.reports[1])
        self.evidence(plan=self.plans[1])
        self.evidence(point="LOC-B-LOBBY")
        before = self.rows()
        model = Mock()
        report_ref = f"report:{self.reports[0]}"
        execution_ref = f"execution:{executions[0]}"
        draft = {
            "observations": [{
                "text": "居民观察晚于声称完成。",
                "evidence_refs": [report_ref, execution_ref],
            }],
            "verification_items": ["现场原因仍待核实。"],
        }

        with self.sessions() as db:
            def suggest(evidence):
                self.assertFalse(db.in_transaction())
                return json.dumps(draft)

            model.suggest.side_effect = suggest
            outcome = run_agent(model, lambda: build_agent_evidence(read_report_evidence(self.reports[0], db)))

        self.assertEqual(outcome.status, "pending_review")
        self.assertEqual(outcome.suggestion.model_dump(), draft)
        self.assertEqual(set(outcome.evidence.records), {
            report_ref, execution_ref, f"inspection:{inspections[0]}",
            f"plan:{self.plans[0]}",
            f"link:{link}",
        })
        self.assertEqual(outcome.evidence.records[execution_ref]["report_comparison"]["relation"], "after")
        self.assertEqual(self.rows(), before)

    def test_agent_runner_rejects_reference_from_another_report_in_database(self):
        self.link()
        self.evidence()
        self.link(plan=self.plans[1], report=self.reports[1])
        other_executions, _ = self.evidence(plan=self.plans[1])
        before = self.rows()
        other_ref = f"execution:{other_executions[0]}"
        model = Mock()
        model.suggest.return_value = json.dumps({
            "observations": [{"text": "尝试引用另一条报事的执行记录。", "evidence_refs": [other_ref]}],
            "verification_items": ["复核来源。"],
        })
        with self.sessions() as db:
            outcome = run_agent(model, lambda: build_agent_evidence(read_report_evidence(self.reports[0], db)))

        self.assertEqual(outcome.status, "failed")
        self.assertEqual((outcome.failure_stage, outcome.error_code), ("validation", "EvidenceReferenceError"))
        self.assertNotIn(other_ref, outcome.evidence.records)
        self.assertEqual(self.rows(), before)

    def test_agent_runner_stops_when_joint_database_read_fails(self):
        self.link()
        before = self.rows()
        model = Mock()
        failure = OperationalError("SELECT", (), sqlite3.OperationalError("合成取证失败"))
        with self.sessions() as db, patch("routers.report_evidence.read_plan_evidence", side_effect=failure):
            outcome = run_agent(model, lambda: build_agent_evidence(read_report_evidence(self.reports[0], db)))
            self.assertFalse(db.in_transaction())

        self.assertEqual(outcome.status, "failed")
        self.assertEqual((outcome.failure_stage, outcome.error_code), ("evidence_read", "OperationalError"))
        self.assertIsNone(outcome.evidence)
        self.assertIsNone(outcome.suggestion)
        model.suggest.assert_not_called()
        self.assertEqual(self.rows(), before)


    def enable_agent_runs(self):
        before = self.rows()
        config = Config(str(ROOT / "alembic.ini"))
        with self.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0007_agent_runs")
        self.assertEqual(self.rows(), before)
        model = Mock()
        model.model_name = "deterministic-test"
        self.app.include_router(agent_runs_router)
        self.app.dependency_overrides[get_agent_model] = lambda: model
        return model

    def agent_runs_url(self, report=None):
        return f"/api/service-requests/{report or self.reports[0]}/agent-runs"

    def agent_run_demo(self):
        self.link()
        executions, _ = self.evidence()
        model = self.enable_agent_runs()
        with self.sessions() as db:
            evidence = read_automatic_agent_evidence(db, self.reports[0])
        model.suggest.return_value = json.dumps(automatic_draft(evidence, observations=[{
            "text": "居民观察晚于声称完成。",
            "evidence_refs": [f"report:{self.reports[0]}", f"execution:{executions[0]}"],
        }], verification_items=["现场原因仍待核实。"]))
        return model

    def test_0007_migration_matches_orm_and_keeps_old_rows(self):
        self.link()
        self.evidence()
        self.enable_agent_runs()
        expected = create_db_engine(self.path.with_name("expected.db"))
        self.addCleanup(expected.dispose)
        create_pre_floor_schema(expected)
        with self.engine.connect() as actual, expected.connect() as orm:
            self.assertEqual(schema_signature(actual), schema_signature(orm))
            self.assertEqual(actual.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one(),
                             "0007_agent_runs")

    def test_saves_and_reads_back_pending_a_lobby_suggestion(self):
        model = self.agent_run_demo()
        before = self.rows()
        body = self.client.post(self.agent_runs_url(), json={"request_id": str(uuid4())})
        self.assertEqual(body.status_code, 201, body.text)
        saved = body.json()
        self.assertEqual(saved["status"], "pending_review")
        observation = saved["suggestion"]["observations"][0]
        self.assertEqual(observation["text"], "居民观察晚于声称完成。")
        execution_ref = observation["evidence_refs"][1]
        self.assertEqual(saved["evidence"]["records"][execution_ref]["report_comparison"]["relation"], "after")
        self.assertEqual(saved["suggestion"]["verification_items"], ["现场原因仍待核实。"])
        self.engine.dispose()
        self.assertEqual(self.client.get(self.agent_runs_url()).json(), [saved])
        self.assertEqual(self.rows(), before)
        model.suggest.assert_called_once()

    def test_saves_and_reads_back_failure_without_suggestion(self):
        model = self.agent_run_demo()
        model.suggest.side_effect = TimeoutError("internal details")
        before = self.rows()
        body = self.client.post(self.agent_runs_url(), json={"request_id": str(uuid4())})
        self.assertEqual(body.status_code, 201, body.text)
        saved = body.json()
        self.assertEqual((saved["status"], saved["failure_stage"], saved["error_code"]),
                         ("failed", "suggestion_request", "TimeoutError"))
        self.assertIsNone(saved["suggestion"])
        self.assertIsNotNone(saved["evidence"])
        self.assertNotIn("internal details", body.text)
        self.assertEqual(self.client.get(self.agent_runs_url()).json(), [saved])
        self.assertEqual(self.rows(), before)

    def test_invalid_reference_saves_failure_without_suggestion(self):
        model = self.agent_run_demo()
        draft = json.loads(model.suggest.return_value)
        with self.sessions.begin() as db:
            outside = ServiceRequest(request_id=uuid4(), service_category="cleaning",
                description="另一地点的报事", source="web", risk_status="unknown",
                location_id="LOC-B-LOBBY", observed_at=at("09:40"), received_at=at("18:00"))
            db.add(outside)
            db.flush()
            outside_ref = f"report:{outside.id}"
        draft["observations"][0]["evidence_refs"] = [outside_ref]
        model.suggest.return_value = json.dumps(draft)
        response = self.client.post(self.agent_runs_url(), json={"request_id": str(uuid4())})
        self.assertEqual(response.status_code, 201, response.text)
        saved = response.json()
        self.assertEqual(
            (saved["status"], saved["failure_stage"], saved["error_code"]),
            ("failed", "validation", "EvidenceReferenceError"),
        )
        self.assertIsNone(saved["suggestion"])
        self.assertEqual(self.client.get(self.agent_runs_url()).json(), [saved])

    def test_reads_failures_written_by_two_round_version(self):
        self.enable_agent_runs()
        with self.sessions.begin() as db:
            for stage in ("tool_request", "tool_dispatch"):
                db.add(AgentRun(
                    id=uuid4(), report_id=self.reports[0], status="failed",
                    model_name="old-test-model", created_at=at("10:00"), finished_at=at("10:01"),
                    failure_stage=stage, error_code="TestError",
                ))
        response = self.client.get(self.agent_runs_url())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual({row["failure_stage"] for row in response.json()}, {"tool_request", "tool_dispatch"})

    def test_replay_reuses_record_and_respects_report_scope(self):
        model = self.agent_run_demo()
        payload = {"request_id": str(uuid4())}
        created = self.client.post(self.agent_runs_url(), json=payload)
        replay = self.client.post(self.agent_runs_url(), json=payload)
        self.assertEqual((created.status_code, replay.status_code), (201, 200))
        self.assertEqual(created.json(), replay.json())
        other_url = self.agent_runs_url(self.reports[1])
        self.assertEqual(self.client.post(other_url, json=payload).status_code, 409)
        self.assertEqual(self.client.get(other_url).json(), [])
        self.assertEqual(self.client.get(self.agent_runs_url()).json(), [created.json()])
        model.suggest.assert_called_once()

    def test_concurrent_replay_does_not_start_another_model_call(self):
        model = self.agent_run_demo()
        started, release = Event(), Event()
        payload = {"request_id": str(uuid4())}

        draft = model.suggest.return_value

        def suggest(evidence):
            started.set()
            if not release.wait(8):
                raise TimeoutError()
            return draft

        model.suggest.side_effect = suggest
        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(self.client.post, self.agent_runs_url(), json=payload)
            try:
                self.assertTrue(started.wait(5))
                replay = self.client.post(self.agent_runs_url(), json=payload)
                self.assertEqual(replay.status_code, 200, replay.text)
                self.assertEqual(replay.json()["status"], "running")
            finally:
                release.set()
            self.assertEqual(first.result().json()["status"], "pending_review")
        model.suggest.assert_called_once()
        self.assertEqual(len(self.client.get(self.agent_runs_url()).json()), 1)

if __name__ == "__main__":
    unittest.main()
