import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier
from unittest.mock import patch
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import sessionmaker

from database import create_db_engine, engine as project_engine, get_db
from models import Location, PlanInstance, PlanLocation, ServiceRequest
from routers.executions import router
from seed_demo_location_hierarchy import seed_demo_location_hierarchy


ROOT = Path(__file__).resolve().parents[1]
OLD = ("locations", "service_requests", "plan_instances", "plan_locations")
ALL = OLD + ("execution_records", "execution_locations")


class ExecutionAPITests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.engine = create_db_engine(Path(self.folder.name) / "api.db")
        guard = patch.object(project_engine, "connect", side_effect=AssertionError("禁止连接项目数据库"))
        guard.start()
        self.addCleanup(guard.stop)
        config = Config(str(ROOT / "alembic.ini"))
        with self.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0004_execution_records")
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)
        seed_demo_location_hierarchy(self.engine)
        with self.sessions.begin() as db:
            db.add(ServiceRequest(
                request_id=uuid4(), service_category="cleaning",
                description="  合成旧报事  ", source="web", risk_status="unknown",
            ))
            plan = PlanInstance(
                request_id=uuid4(), title="合成A、B大堂保洁", service_category="cleaning",
                requirements="分别核对两处大堂", owner_name="合成负责人",
                planned_start_at=datetime(2026, 11, 1, 1, tzinfo=UTC),
                planned_end_at=datetime(2026, 11, 1, 2, tzinfo=UTC),
                locations=[PlanLocation(location_id=key) for key in ("LOC-A-LOBBY", "LOC-B-LOBBY")],
            )
            db.add(plan)
            db.flush()
            self.plan_id = plan.id

        def test_db():
            with self.sessions() as db:
                yield db

        self.app = FastAPI()
        self.app.include_router(router)
        self.app.dependency_overrides[get_db] = test_db
        self.client = TestClient(self.app)
        clock = patch("routers.executions.utc_now", return_value=datetime(2026, 11, 2, 2, tzinfo=UTC))
        self.clock = clock.start()
        self.addCleanup(clock.stop)
        self.original = self.rows(OLD)

    def tearDown(self):
        self.client.close()
        self.engine.dispose()
        self.folder.cleanup()

    def payload(self, **changes):
        values = {
            "request_id": str(uuid4()), "plan_id": str(self.plan_id),
            "executor_name": "合成执行人", "description": "  合成：A大堂声称完成。  ",
            "completion_claim": "completed", "location_ids": ["LOC-A-LOBBY"],
            "claimed_started_at": "2026-11-01T09:00:00+08:00",
            "claimed_finished_at": "2026-11-01T09:20:00+08:00",
        }
        values.update(changes)
        return values

    def rows(self, tables=ALL):
        with self.engine.connect() as connection:
            return {
                table: connection.exec_driver_sql(f'SELECT * FROM "{table}" ORDER BY 1, 2').all()
                for table in tables
            }

    def test_create_detail_and_reopen_preserve_raw_values_scope_and_old_rows(self):
        data = self.payload()
        response = self.client.post("/api/executions", json=data)
        self.assertEqual(response.status_code, 201, response.text)
        saved = response.json()
        UUID(saved["id"])
        self.assertNotEqual(saved["id"], data["request_id"])
        self.assertEqual(saved["description"], data["description"])
        self.assertEqual(saved["location_ids"], ["LOC-A-LOBBY"])
        self.assertEqual(saved["claimed_finished_at"], "2026-11-01T01:20:00Z")
        self.assertEqual(saved["recorded_at"], "2026-11-02T02:00:00Z")
        self.assertEqual(saved["version"], 1)
        self.engine.dispose()
        self.assertEqual(self.client.get("/api/executions/" + saved["id"]).json(), saved)
        self.assertEqual(self.rows(OLD), self.original)
        self.assertEqual(len(self.rows()["execution_locations"]), 1)

    def test_all_claims_can_keep_unknown_times_and_scope(self):
        for claim in ("completed", "partially_completed", "not_completed", "unknown"):
            with self.subTest(claim=claim):
                response = self.client.post("/api/executions", json=self.payload(
                    completion_claim=claim, location_ids=[],
                    claimed_started_at=None, claimed_finished_at=None,
                ))
                self.assertEqual(response.status_code, 201, response.text)
                saved = response.json()
                self.assertEqual(saved["location_ids"], [])
                self.assertIsNone(saved["claimed_started_at"])
                self.assertIsNone(saved["claimed_finished_at"])
        self.assertEqual(len(self.rows()["execution_records"]), 4)
        self.assertEqual(self.rows()["execution_locations"], [])
        self.assertEqual(self.rows(OLD), self.original)

    def test_retry_keeps_original_receipt_with_reordered_scope_and_changed_clock(self):
        data = self.payload(location_ids=["LOC-B-LOBBY", "LOC-A-LOBBY"])
        saved = self.client.post("/api/executions", json=data).json()
        before = self.rows()
        data.update(location_ids=list(reversed(data["location_ids"])),
                    claimed_started_at="2026-11-01T01:00:00Z",
                    claimed_finished_at="2026-11-01T01:20:00Z")
        self.clock.return_value = datetime(2026, 10, 31, tzinfo=UTC)
        response = self.client.post("/api/executions", json=data)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), saved)
        self.assertEqual(self.rows(), before)

    def test_changed_content_conflicts_without_overwriting_original(self):
        data = self.payload()
        saved = self.client.post("/api/executions", json=data).json()
        before = self.rows()
        for changes in (
            {"description": data["description"].strip()}, {"executor_name": "另一执行人"},
            {"plan_id": str(uuid4())}, {"location_ids": []}, {"location_ids": ["MISSING"]},
            {"completion_claim": "partially_completed", "claimed_finished_at": None},
            {"claimed_finished_at": "2030-01-01T00:00:00Z"},
        ):
            with self.subTest(changes=changes):
                response = self.client.post("/api/executions", json={**data, **changes})
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(self.rows(), before)
                self.assertEqual(self.client.get("/api/executions/" + saved["id"]).json(), saved)

    def test_invalid_input_and_future_times_are_422_without_writes(self):
        before = self.rows()
        for changes in (
            {"description": "   "}, {"location_ids": ["LOC-A-LOBBY", "LOC-A-LOBBY"]},
            {"claimed_started_at": "2026-11-01T09:00:00"},
            {"claimed_finished_at": "2026-11-01T00:00:00Z"},
            {"claimed_started_at": "2030-01-01T00:00:00Z", "claimed_finished_at": None},
            {"claimed_finished_at": "2030-01-01T00:00:00Z"},
            {"id": str(uuid4())}, {"recorded_at": "2026-11-01T00:00:00Z"}, {"version": 1},
        ):
            with self.subTest(changes=changes):
                response = self.client.post("/api/executions", json=self.payload(**changes))
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(self.rows(), before)

    def test_invalid_plan_or_scope_rolls_back_and_same_request_can_be_fixed(self):
        for changes in (
            {"plan_id": str(uuid4())},
            {"location_ids": ["LOC-A-LOBBY", "LOC-A-COURTYARD"]},
            {"location_ids": ["LOC-A-B02-U01-R302"]},
            {"location_ids": ["MISSING"]},
        ):
            with self.subTest(changes=changes):
                data = self.payload(**changes)
                before = self.rows()
                response = self.client.post("/api/executions", json=data)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(self.rows(), before)
                data.update(plan_id=str(self.plan_id), location_ids=["LOC-A-LOBBY"])
                self.assertEqual(self.client.post("/api/executions", json=data).status_code, 201)
        self.assertEqual(self.rows(OLD), self.original)

    def test_response_failure_rolls_back_record_and_all_points(self):
        data = self.payload(location_ids=["LOC-A-LOBBY", "LOC-B-LOBBY"])
        before = self.rows()
        with patch("routers.executions.ExecutionRead.model_validate", side_effect=RuntimeError("synthetic response failure")):
            with self.assertRaisesRegex(RuntimeError, "synthetic response failure"):
                self.client.post("/api/executions", json=data)
        self.assertEqual(self.rows(), before)
        self.assertEqual(self.client.post("/api/executions", json=data).status_code, 201)

    def test_simultaneous_identical_and_conflicting_submissions_are_atomic(self):
        for different in (False, True):
            with self.subTest(different=different):
                data = self.payload(location_ids=["LOC-A-LOBBY", "LOC-B-LOBBY"])
                other = {**data, "description": "另一份合成执行说明"} if different else data
                before = self.rows()
                barrier = Barrier(2)

                def send(payload):
                    with TestClient(self.app) as client:
                        barrier.wait(timeout=5)
                        result = client.post("/api/executions", json=payload)
                        return result.status_code, result.json()

                with ThreadPoolExecutor(max_workers=2) as pool:
                    futures = [pool.submit(send, payload) for payload in (data, other)]
                    results = [future.result(timeout=10) for future in futures]
                self.assertEqual(sorted(code for code, body in results), [201, 409] if different else [200, 201])
                saved = next(body for code, body in results if code == 201)
                self.assertEqual(self.client.get("/api/executions/" + saved["id"]).json(), saved)
                if not different:
                    self.assertEqual(results[0][1], results[1][1])
                self.assertEqual(len(self.rows()["execution_records"]), len(before["execution_records"]) + 1)
                self.assertEqual(len(self.rows()["execution_locations"]), len(before["execution_locations"]) + 2)
                self.assertEqual(self.rows(OLD), self.original)

    def test_busy_database_is_503_and_original_submission_can_retry(self):
        @event.listens_for(self.engine, "connect")
        def shorten_timeout(connection, record):
            connection.execute("PRAGMA busy_timeout=50").close()

        data = self.payload()
        before = self.rows()
        with self.engine.connect() as locker:
            transaction = locker.begin()
            try:
                locker.execute(Location.__table__.insert().values(id="LOC-LOCK", name="合成锁测试", version=1))
                response = self.client.post("/api/executions", json=data)
                self.assertEqual(response.status_code, 503, response.text)
            finally:
                transaction.rollback()
        self.assertEqual(self.rows(), before)
        self.assertEqual(self.client.post("/api/executions", json=data).status_code, 201)

    def test_missing_ids_and_main_route_registration(self):
        before = self.rows()
        self.assertEqual(self.client.get("/api/executions/" + str(uuid4())).status_code, 404)
        self.assertEqual(self.client.get("/api/executions/not-a-uuid").status_code, 422)
        self.assertEqual(self.rows(), before)
        from main import app

        with TestClient(app) as client:
            paths = client.get("/openapi.json").json()["paths"]
        for code in ("200", "201", "409", "422", "503"):
            self.assertIn(code, paths["/api/executions"]["post"]["responses"])
        self.assertIn("get", paths["/api/executions/{execution_id}"])


    def test_empty_execution_list_is_read_only(self):
        before = self.rows()
        for query in ("", "?plan_id=" + str(self.plan_id)):
            with self.subTest(query=query):
                response = self.client.get("/api/executions" + query)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json(), [])
        self.assertEqual(self.rows(), before)

    def test_plan_filter_keeps_unknown_scope_and_excludes_other_plans(self):
        known = self.client.post("/api/executions", json=self.payload(
            location_ids=["LOC-A-LOBBY", "LOC-B-LOBBY"],
        )).json()
        unknown = self.client.post("/api/executions", json=self.payload(
            completion_claim="unknown", location_ids=[],
            claimed_started_at=None, claimed_finished_at=None,
        )).json()
        with self.sessions.begin() as db:
            other_plan = PlanInstance(
                request_id=uuid4(), title="合成另一项计划", service_category="cleaning",
                requirements="仅A大堂", owner_name="合成负责人",
                planned_start_at=datetime(2026, 11, 1, 1, tzinfo=UTC),
                planned_end_at=datetime(2026, 11, 1, 2, tzinfo=UTC),
                locations=[PlanLocation(location_id="LOC-A-LOBBY")],
            )
            db.add(other_plan)
            db.flush()
            other_id = other_plan.id
        self.clock.return_value = datetime(2026, 11, 2, 3, tzinfo=UTC)
        other = self.client.post("/api/executions", json=self.payload(plan_id=str(other_id))).json()
        before = self.rows()
        response = self.client.get("/api/executions", params={"plan_id": str(self.plan_id)})
        self.assertEqual(response.status_code, 200, response.text)
        records = {item["id"]: item for item in response.json()}
        self.assertEqual(set(records), {known["id"], unknown["id"]})
        self.assertEqual(records[known["id"]]["location_ids"], ["LOC-A-LOBBY", "LOC-B-LOBBY"])
        self.assertEqual(records[unknown["id"]]["location_ids"], [])
        self.assertIsNone(records[unknown["id"]]["claimed_finished_at"])
        pages = [
            self.client.get("/api/executions", params={
                "plan_id": str(self.plan_id), "limit": 1, "offset": offset,
            }).json()
            for offset in (0, 1)
        ]
        self.assertEqual([len(page) for page in pages], [1, 1])
        self.assertEqual({page[0]["id"] for page in pages}, set(records))
        self.assertEqual(
            {item["id"] for item in self.client.get("/api/executions").json()},
            {known["id"], unknown["id"], other["id"]},
        )
        self.assertEqual(self.rows(), before)

    def test_execution_list_paging_preserves_order_and_complete_points(self):
        saved = []
        for index in range(4):
            self.clock.return_value = datetime(2026, 11, 2, 1 if index == 3 else 2, tzinfo=UTC)
            response = self.client.post("/api/executions", json=self.payload(
                location_ids=["LOC-B-LOBBY", "LOC-A-LOBBY"],
            ))
            self.assertEqual(response.status_code, 201, response.text)
            saved.append(response.json())
        before = self.rows()
        expected = sorted(saved, key=lambda item: (item["recorded_at"], item["id"]), reverse=True)
        first = self.client.get("/api/executions?limit=2&offset=0").json()
        second = self.client.get("/api/executions?limit=2&offset=2").json()
        self.assertEqual([item["id"] for item in first + second], [item["id"] for item in expected])
        for item in first + second:
            self.assertEqual(item["location_ids"], ["LOC-A-LOBBY", "LOC-B-LOBBY"])
        self.assertEqual(self.client.get("/api/executions?offset=99").json(), [])
        self.assertEqual(self.rows(), before)

    def test_execution_list_invalid_parameters_and_missing_plan_do_not_write(self):
        before = self.rows()
        for query in ("limit=0", "limit=101", "limit=bad", "offset=-1", "offset=bad", "plan_id=bad"):
            with self.subTest(query=query):
                response = self.client.get("/api/executions?" + query)
                self.assertEqual(response.status_code, 422, response.text)
        response = self.client.get("/api/executions", params={"plan_id": str(uuid4())})
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(self.rows(), before)

    def test_main_documents_execution_list_parameters(self):
        from main import app

        with TestClient(app) as client:
            operation = client.get("/openapi.json").json()["paths"]["/api/executions"]["get"]
        self.assertEqual({item["name"] for item in operation["parameters"]}, {"plan_id", "limit", "offset"})
        self.assertIn("404", operation["responses"])


if __name__ == "__main__":
    unittest.main()