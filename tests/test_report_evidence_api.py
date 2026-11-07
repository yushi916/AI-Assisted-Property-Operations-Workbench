import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from database import create_db_engine, engine as project_engine, get_db
from models import (
    ExecutionLocation, ExecutionRecord, InspectionRecord, PlanInstance,
    PlanLocation, RequestPlanLink, ServiceRequest,
)
from routers.report_evidence import router
from seed_demo_location_hierarchy import seed_demo_location_hierarchy


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
        self.assertTrue(any("重新核实关联" in x for x in plan["warnings"]))
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


if __name__ == "__main__":
    unittest.main()