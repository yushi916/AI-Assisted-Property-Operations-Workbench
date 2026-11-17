import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from database import create_db_engine, engine as project_engine, get_db
from models import (
    ExecutionLocation, ExecutionRecord, InspectionRecord,
    PlanInstance, PlanLocation, ServiceRequest,
)
from routers.plan_evidence import router
from seed_demo_location_hierarchy import seed_demo_location_hierarchy
from evidence_reader import read_plan_evidence
from evidence_rules import check_plan_evidence

ROOT = Path(__file__).resolve().parents[1]
TABLES = (
    "locations", "service_requests", "plan_instances", "plan_locations",
    "execution_records", "execution_locations", "inspection_records",
)


def at(value):
    return datetime.fromisoformat("2026-11-05T" + value + "+08:00")


class PlanEvidenceAPITests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.db_path = Path(self.folder.name) / "api.db"
        self.engine = create_db_engine(self.db_path)
        guard = patch.object(project_engine, "connect", side_effect=AssertionError("禁止连接项目数据库"))
        guard.start()
        self.addCleanup(guard.stop)
        config = Config(str(ROOT / "alembic.ini"))
        with self.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0005_inspection_records")
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)
        seed_demo_location_hierarchy(self.engine)
        self.plan_id = self.add_plan()
        with self.sessions.begin() as db:
            db.add(ServiceRequest(
                request_id=uuid4(), service_category="cleaning", description="合成旧报事",
                source="web", risk_status="unknown",
            ))

        def test_db():
            with self.sessions() as db:
                yield db

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_db] = test_db
        self.client = TestClient(app)
        clock = patch("routers.plan_evidence.utc_now", return_value=at("20:00"))
        self.clock = clock.start()
        self.addCleanup(clock.stop)
        self.url = "/api/plans/" + str(self.plan_id) + "/evidence-check"

    def tearDown(self):
        self.client.close()
        self.engine.dispose()
        self.folder.cleanup()

    def add_plan(self):
        with self.sessions.begin() as db:
            plan = PlanInstance(
                request_id=uuid4(), title="合成A、B点位计划", service_category="cleaning",
                requirements="分别核对两个点位", owner_name="合成负责人",
                planned_start_at=at("09:00"), planned_end_at=at("09:30"),
                locations=[PlanLocation(location_id=x) for x in ("LOC-A-LOBBY", "LOC-B-LOBBY")],
            )
            db.add(plan)
            db.flush()
            return plan.id

    def add_evidence(self, plan_id=None, unknown=False, count=1):
        execution_ids, inspection_ids = [], []
        with self.sessions.begin() as db:
            for _ in range(count):
                execution = ExecutionRecord(
                    request_id=uuid4(), plan_id=plan_id or self.plan_id,
                    executor_name="合成执行人", description="合成执行反馈",
                    completion_claim="unknown" if unknown else "completed",
                    claimed_finished_at=None if unknown else at("09:20"),
                    recorded_at=at("18:00"),
                    locations=[] if unknown else [ExecutionLocation(location_id="LOC-A-LOBBY")],
                )
                inspection = InspectionRecord(
                    request_id=uuid4(), plan_id=plan_id or self.plan_id,
                    inspector_name="合成巡查人", description="合成巡查观察",
                    finding="unable_to_verify" if unknown else "issue_observed",
                    location_id=None if unknown else "LOC-A-LOBBY",
                    observed_at=None if unknown else at("09:40"), recorded_at=at("18:00"),
                )
                db.add_all([execution, inspection])
                db.flush()
                execution_ids.append(str(execution.id))
                inspection_ids.append(str(inspection.id))
        return execution_ids, inspection_ids

    def rows(self):
        with self.engine.connect() as connection:
            return {
                name: connection.exec_driver_sql(f'SELECT * FROM "{name}" ORDER BY 1, 2').all()
                for name in TABLES
            }

    def test_empty_evidence_returns_gaps_without_writing(self):
        before = self.rows()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["due"])
        self.assertEqual(len(body["evidence"]), 1)
        self.assertEqual(len(body["findings"]), 4)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(self.rows(), before)

    def test_known_and_unknown_evidence_are_loaded_and_scope_is_preserved(self):
        known_e, known_i = self.add_evidence()
        unknown_e, unknown_i = self.add_evidence(unknown=True)
        before = self.rows()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual({x["id"] for x in body["evidence"]}, {
            str(self.plan_id), *known_e, *known_i, *unknown_e, *unknown_i,
        })
        a, b = body["coverage"]
        self.assertEqual(a["execution_ids"], known_e)
        self.assertEqual(a["inspection_ids"], known_i)
        self.assertEqual(b["execution_ids"], [])
        self.assertEqual(b["inspection_ids"], [])
        self.assertEqual(body["comparisons"][0]["relation"], "after")
        self.assertEqual(self.rows(), before)

    def test_more_than_one_api_page_is_not_truncated(self):
        execution_ids, inspection_ids = self.add_evidence(unknown=True, count=121)
        before = self.rows()
        response = self.client.get(self.url, params={"limit": 1, "offset": 999})
        self.assertEqual(response.status_code, 200, response.text)
        refs = response.json()["evidence"]
        self.assertEqual({x["id"] for x in refs if x["kind"] == "execution"}, set(execution_ids))
        self.assertEqual({x["id"] for x in refs if x["kind"] == "inspection"}, set(inspection_ids))
        self.assertEqual(len(refs), 243)
        self.assertEqual(self.rows(), before)

    def test_other_plan_same_location_and_unknown_scope_are_excluded(self):
        current_e, current_i = self.add_evidence()
        other_id = self.add_plan()
        self.add_evidence(plan_id=other_id)
        self.add_evidence(plan_id=other_id, unknown=True)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual({x["id"] for x in response.json()["evidence"]}, {
            str(self.plan_id), *current_e, *current_i,
        })

    def test_server_clock_is_used_and_get_does_not_change_versions(self):
        before = self.rows()
        self.clock.return_value = at("09:29:59")
        response = self.client.get(self.url, params={"checked_at": "2099-01-01T00:00:00Z"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["due"])
        self.assertEqual(response.json()["checked_at"], "2026-11-05T01:29:59Z")
        self.clock.return_value = at("09:30")
        self.assertTrue(self.client.get(self.url).json()["due"])
        self.assertEqual(self.rows(), before)

    def test_query_failure_returns_503_not_partial_or_empty_success(self):
        before = self.rows()

        def fail_inspection_read(connection, cursor, statement, parameters, context, many):
            if statement.lstrip().upper().startswith("SELECT") and "FROM inspection_records" in statement:
                raise OperationalError(statement, parameters, sqlite3.OperationalError("synthetic read failure"))

        event.listen(self.engine, "before_cursor_execute", fail_inspection_read)
        try:
            response = self.client.get(self.url)
        finally:
            event.remove(self.engine, "before_cursor_execute", fail_inspection_read)
        self.assertEqual(response.status_code, 503, response.text)
        self.assertNotIn("coverage", response.json())
        self.assertEqual(self.rows(), before)

    def test_plan_and_children_are_read_from_one_transaction_snapshot(self):
        # 仅在此测试的临时数据库启用 WAL，以允许读取期间另一事务提交。
        with closing(sqlite3.connect(self.db_path)) as raw:
            self.assertEqual(raw.execute("PRAGMA journal_mode=WAL").fetchone()[0], "wal")
        inserted = []
        changed = False

        def change_after_first_read(connection, cursor, statement, parameters, context, many):
            nonlocal changed
            if changed or "FROM plan_instances" not in statement:
                return
            changed = True
            with self.sessions.begin() as db:
                db.get(PlanInstance, self.plan_id).version = 2
                item = InspectionRecord(
                    request_id=uuid4(), plan_id=self.plan_id,
                    inspector_name="合成巡查人", description="合成并发新增",
                    finding="unable_to_verify", location_id=None, observed_at=None,
                )
                db.add(item)
                db.flush()
                inserted.append(str(item.id))

        event.listen(self.engine, "after_cursor_execute", change_after_first_read)
        try:
            response = self.client.get(self.url)
        finally:
            event.remove(self.engine, "after_cursor_execute", change_after_first_read)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(changed)
        self.assertEqual(response.json()["plan_version"], 1)
        self.assertEqual(len(response.json()["evidence"]), 1)
        newer = self.client.get(self.url).json()
        self.assertEqual(newer["plan_version"], 2)
        self.assertEqual({x["id"] for x in newer["evidence"] if x["kind"] == "inspection"}, set(inserted))

    def test_shared_reader_returns_data_usable_after_session_closes(self):
        known_e, known_i = self.add_evidence()
        unknown_e, unknown_i = self.add_evidence(unknown=True)
        self.add_evidence(plan_id=self.add_plan())
        before = self.rows()
        with self.sessions() as db, db.begin():
            evidence = read_plan_evidence(db, self.plan_id)
        result = check_plan_evidence(
            evidence.plan, evidence.executions, evidence.inspections, at("20:00"),
        )
        self.assertEqual({str(item.id) for item in result.evidence}, {
            str(self.plan_id), *known_e, *known_i, *unknown_e, *unknown_i,
        })
        self.assertEqual([str(item) for item in result.coverage[0].execution_ids], known_e)
        self.assertEqual(result.coverage[1].execution_ids, [])
        self.assertEqual(self.rows(), before)

    def test_shared_reader_requires_transaction_and_does_not_commit_callers_work(self):
        with self.sessions() as db:
            with self.assertRaisesRegex(ValueError, "调用方事务"):
                read_plan_evidence(db, self.plan_id)
        before = self.rows()
        with self.assertRaisesRegex(RuntimeError, "caller rollback"):
            with self.sessions() as db, db.begin():
                db.add(ServiceRequest(
                    request_id=uuid4(), service_category="cleaning",
                    description="合成：调用方尚未提交的记录", source="web", risk_status="unknown",
                ))
                db.flush()
                self.assertIsNotNone(read_plan_evidence(db, self.plan_id))
                self.assertTrue(db.in_transaction())
                raise RuntimeError("caller rollback")
        self.assertEqual(self.rows(), before)

    def test_missing_invalid_ids_and_main_route_registration(self):
        before = self.rows()
        self.assertEqual(self.client.get("/api/plans/" + str(uuid4()) + "/evidence-check").status_code, 404)
        self.assertEqual(self.client.get("/api/plans/bad/evidence-check").status_code, 422)
        self.assertEqual(self.rows(), before)
        from main import app

        with TestClient(app) as client:
            operation = client.get("/openapi.json").json()["paths"]["/api/plans/{plan_id}/evidence-check"]["get"]
        self.assertEqual({x["name"] for x in operation["parameters"]}, {"plan_id"})
        self.assertIn("404", operation["responses"])
        self.assertIn("503", operation["responses"])


if __name__ == "__main__":
    unittest.main()