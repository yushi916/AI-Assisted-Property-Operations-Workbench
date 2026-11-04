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
from routers.inspections import router
from seed_demo_location_hierarchy import seed_demo_location_hierarchy


ROOT = Path(__file__).resolve().parents[1]
OLD = ("locations", "service_requests", "plan_instances", "plan_locations",
       "execution_records", "execution_locations")
ALL = OLD + ("inspection_records",)


class InspectionAPITests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.engine = create_db_engine(Path(self.folder.name) / "api.db")
        guard = patch.object(project_engine, "connect", side_effect=AssertionError("禁止连接项目数据库"))
        guard.start()
        self.addCleanup(guard.stop)
        config = Config(str(ROOT / "alembic.ini"))
        with self.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0005_inspection_records")
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
        clock = patch("routers.inspections.utc_now", return_value=datetime(2026, 11, 2, 2, tzinfo=UTC))
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
            "inspector_name": "合成巡查人", "description": "  合成：A大堂仍有垃圾。  ",
            "finding": "issue_observed", "location_id": "LOC-A-LOBBY",
            "observed_at": "2026-11-01T09:20:00+08:00",
        }
        values.update(changes)
        return values

    def rows(self, tables=ALL):
        with self.engine.connect() as connection:
            return {
                table: connection.exec_driver_sql(f'SELECT * FROM "{table}" ORDER BY 1, 2').all()
                for table in tables
            }

    def test_create_detail_and_reopen_preserve_raw_values_and_old_rows(self):
        data = self.payload()
        response = self.client.post("/api/inspections", json=data)
        self.assertEqual(response.status_code, 201, response.text)
        saved = response.json()
        UUID(saved["id"])
        self.assertNotEqual(saved["id"], data["request_id"])
        self.assertEqual(saved["description"], data["description"])
        self.assertEqual(saved["location_id"], "LOC-A-LOBBY")
        self.assertEqual(saved["observed_at"], "2026-11-01T01:20:00Z")
        self.assertEqual(saved["recorded_at"], "2026-11-02T02:00:00Z")
        self.assertEqual(saved["version"], 1)
        self.engine.dispose()
        self.assertEqual(self.client.get("/api/inspections/" + saved["id"]).json(), saved)
        self.assertEqual(self.rows(OLD), self.original)
        self.assertEqual(len(self.rows()["inspection_records"]), 1)
        self.assertEqual(self.rows()["execution_records"], [])

    def test_all_findings_can_keep_unknown_point_and_time(self):
        for finding in ("no_issue_observed", "issue_observed", "unable_to_verify"):
            with self.subTest(finding=finding):
                response = self.client.post("/api/inspections", json=self.payload(
                    finding=finding, location_id=None, observed_at=None,
                ))
                self.assertEqual(response.status_code, 201, response.text)
                self.assertIsNone(response.json()["location_id"])
                self.assertIsNone(response.json()["observed_at"])
        self.assertEqual(len(self.rows()["inspection_records"]), 3)
        self.assertEqual(self.rows(OLD), self.original)

    def test_retry_keeps_original_receipt_after_clock_and_scope_change(self):
        data = self.payload()
        saved = self.client.post("/api/inspections", json=data).json()
        with self.sessions.begin() as db:
            point = db.get(PlanLocation, (self.plan_id, "LOC-A-LOBBY"))
            db.delete(point)
        before = self.rows()
        data["observed_at"] = "2026-11-01T01:20:00Z"
        self.clock.return_value = datetime(2026, 10, 31, tzinfo=UTC)
        response = self.client.post("/api/inspections", json=data)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), saved)
        self.assertEqual(self.rows(), before)

    def test_changed_content_conflicts_without_overwriting_original(self):
        data = self.payload()
        saved = self.client.post("/api/inspections", json=data).json()
        before = self.rows()
        for changes in (
            {"description": data["description"].strip()}, {"inspector_name": "另一巡查人"},
            {"plan_id": str(uuid4())}, {"location_id": None}, {"location_id": "MISSING"},
            {"finding": "no_issue_observed"}, {"observed_at": None},
            {"observed_at": "2030-01-01T00:00:00Z"},
        ):
            with self.subTest(changes=changes):
                response = self.client.post("/api/inspections", json={**data, **changes})
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(self.rows(), before)
                self.assertEqual(self.client.get("/api/inspections/" + saved["id"]).json(), saved)

    def test_invalid_input_and_future_observation_are_422_without_writes(self):
        before = self.rows()
        for changes in (
            {"description": "   "}, {"inspector_name": "  "}, {"location_id": " "},
            {"finding": "completed"}, {"location_ids": ["LOC-A-LOBBY"]},
            {"observed_at": "2026-11-01T09:00:00"},
            {"observed_at": "2030-01-01T00:00:00Z"},
            {"id": str(uuid4())}, {"recorded_at": "2026-11-01T00:00:00Z"}, {"version": 1},
        ):
            with self.subTest(changes=changes):
                response = self.client.post("/api/inspections", json=self.payload(**changes))
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(self.rows(), before)

    def test_invalid_plan_or_scope_rolls_back_and_same_request_can_be_fixed(self):
        for changes in (
            {"plan_id": str(uuid4())}, {"location_id": "LOC-A-COURTYARD"},
            {"location_id": "LOC-A-B02-U01-R302"}, {"location_id": "MISSING"},
        ):
            with self.subTest(changes=changes):
                data = self.payload(**changes)
                before = self.rows()
                response = self.client.post("/api/inspections", json=data)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(self.rows(), before)
                data.update(plan_id=str(self.plan_id), location_id="LOC-A-LOBBY")
                self.assertEqual(self.client.post("/api/inspections", json=data).status_code, 201)
        self.assertEqual(self.rows(OLD), self.original)

    def test_response_failure_rolls_back_and_same_request_can_retry(self):
        data = self.payload()
        before = self.rows()
        with patch("routers.inspections.InspectionRead.model_validate", side_effect=RuntimeError("synthetic response failure")):
            with self.assertRaisesRegex(RuntimeError, "synthetic response failure"):
                self.client.post("/api/inspections", json=data)
        self.assertEqual(self.rows(), before)
        self.assertEqual(self.client.post("/api/inspections", json=data).status_code, 201)

    def test_simultaneous_identical_and_conflicting_submissions_are_atomic(self):
        for different in (False, True):
            with self.subTest(different=different):
                data = self.payload()
                other = {**data, "description": "另一份合成巡查说明"} if different else data
                before = self.rows()
                barrier = Barrier(2)

                def send(payload):
                    with TestClient(self.app) as client:
                        barrier.wait(timeout=5)
                        result = client.post("/api/inspections", json=payload)
                        return result.status_code, result.json()

                with ThreadPoolExecutor(max_workers=2) as pool:
                    futures = [pool.submit(send, payload) for payload in (data, other)]
                    results = [future.result(timeout=10) for future in futures]
                self.assertEqual(sorted(code for code, body in results), [201, 409] if different else [200, 201])
                saved = next(body for code, body in results if code == 201)
                self.assertEqual(self.client.get("/api/inspections/" + saved["id"]).json(), saved)
                if not different:
                    self.assertEqual(results[0][1], results[1][1])
                self.assertEqual(len(self.rows()["inspection_records"]), len(before["inspection_records"]) + 1)
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
                response = self.client.post("/api/inspections", json=data)
                self.assertEqual(response.status_code, 503, response.text)
            finally:
                transaction.rollback()
        self.assertEqual(self.rows(), before)
        self.assertEqual(self.client.post("/api/inspections", json=data).status_code, 201)

    def test_missing_ids_and_main_route_registration(self):
        before = self.rows()
        self.assertEqual(self.client.get("/api/inspections/" + str(uuid4())).status_code, 404)
        self.assertEqual(self.client.get("/api/inspections/not-a-uuid").status_code, 422)
        self.assertEqual(self.rows(), before)
        from main import app

        with TestClient(app) as client:
            paths = client.get("/openapi.json").json()["paths"]
        for code in ("200", "201", "409", "422", "503"):
            self.assertIn(code, paths["/api/inspections"]["post"]["responses"])
        self.assertIn("get", paths["/api/inspections/{inspection_id}"])

    def test_empty_inspection_list_is_read_only(self):
        before = self.rows()
        for query in ("", "?plan_id=" + str(self.plan_id)):
            with self.subTest(query=query):
                response = self.client.get("/api/inspections" + query)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json(), [])
        self.assertEqual(self.rows(), before)

    def test_plan_filter_keeps_unknown_point_and_excludes_other_plans(self):
        known_response = self.client.post("/api/inspections", json=self.payload())
        self.assertEqual(known_response.status_code, 201, known_response.text)
        known = known_response.json()
        unknown_response = self.client.post("/api/inspections", json=self.payload(
            finding="unable_to_verify", location_id=None, observed_at=None,
        ))
        self.assertEqual(unknown_response.status_code, 201, unknown_response.text)
        unknown = unknown_response.json()
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
        other_response = self.client.post("/api/inspections", json=self.payload(plan_id=str(other_id)))
        self.assertEqual(other_response.status_code, 201, other_response.text)
        other = other_response.json()
        before = self.rows()
        response = self.client.get("/api/inspections", params={"plan_id": str(self.plan_id)})
        self.assertEqual(response.status_code, 200, response.text)
        records = {item["id"]: item for item in response.json()}
        self.assertEqual(set(records), {known["id"], unknown["id"]})
        self.assertEqual(records[known["id"]]["location_id"], "LOC-A-LOBBY")
        self.assertIsNone(records[unknown["id"]]["location_id"])
        self.assertIsNone(records[unknown["id"]]["observed_at"])
        pages = [
            self.client.get("/api/inspections", params={
                "plan_id": str(self.plan_id), "limit": 1, "offset": offset,
            }).json()
            for offset in (0, 1)
        ]
        self.assertEqual([len(page) for page in pages], [1, 1])
        self.assertEqual({page[0]["id"] for page in pages}, set(records))
        self.assertEqual(
            {item["id"] for item in self.client.get("/api/inspections").json()},
            {known["id"], unknown["id"], other["id"]},
        )
        self.assertEqual(self.rows(), before)

    def test_inspection_list_paging_preserves_order_and_all_fields(self):
        saved = []
        for index in range(4):
            self.clock.return_value = datetime(2026, 11, 2, 1 if index == 3 else 2, tzinfo=UTC)
            response = self.client.post("/api/inspections", json=self.payload())
            self.assertEqual(response.status_code, 201, response.text)
            saved.append(response.json())
        before = self.rows()
        expected = sorted(saved, key=lambda item: (item["recorded_at"], item["id"]), reverse=True)
        first = self.client.get("/api/inspections?limit=2&offset=0").json()
        second = self.client.get("/api/inspections?limit=2&offset=2").json()
        self.assertEqual(first + second, expected)
        self.assertEqual(self.client.get("/api/inspections?offset=99").json(), [])
        self.assertEqual(self.rows(), before)

    def test_inspection_list_invalid_parameters_and_missing_plan_do_not_write(self):
        before = self.rows()
        for query in ("limit=0", "limit=101", "limit=bad", "offset=-1", "offset=bad", "plan_id=bad"):
            with self.subTest(query=query):
                response = self.client.get("/api/inspections?" + query)
                self.assertEqual(response.status_code, 422, response.text)
        response = self.client.get("/api/inspections", params={"plan_id": str(uuid4())})
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(self.rows(), before)

    def test_main_documents_inspection_list_parameters(self):
        from main import app

        with TestClient(app) as client:
            operation = client.get("/openapi.json").json()["paths"]["/api/inspections"]["get"]
        self.assertEqual({item["name"] for item in operation["parameters"]}, {"plan_id", "limit", "offset"})
        self.assertIn("404", operation["responses"])
if __name__ == "__main__":
    unittest.main()