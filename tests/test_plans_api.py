import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest.mock import patch
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.orm import sessionmaker

from database import Base, create_db_engine, get_db
from models import Location, PlanInstance, PlanLocation, ServiceRequest
from routers.plans import router
from datetime import datetime


class PlanAPITests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.engine = create_db_engine(Path(self.folder.name) / "api.db")
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.sessions.begin() as db:
            db.add_all([
                Location(id="LOC-A-LOBBY", name="A区大堂"),
                Location(id="LOC-B-LOBBY", name="B区大堂"),
            ])

        def test_db():
            with self.sessions() as db:
                yield db

        self.app = FastAPI()
        self.app.include_router(router)
        self.app.dependency_overrides[get_db] = test_db
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        self.engine.dispose()
        self.folder.cleanup()

    def payload(self, **changes):
        values = {
            "request_id": str(uuid4()),
            "title": "  合成计划：两处大堂保洁  ",
            "service_category": "cleaning",
            "requirements": "清理地面及垃圾桶周围的散落垃圾。",
            "owner_name": "合成负责人",
            "location_ids": ["LOC-B-LOBBY", "LOC-A-LOBBY"],
            "planned_start_at": "2026-11-01T09:00:00+08:00",
            "planned_end_at": "2026-11-01T09:30:00+08:00",
        }
        values.update(changes)
        return values

    def counts(self):
        with self.sessions() as db:
            return tuple(
                db.scalar(select(func.count()).select_from(model))
                for model in (PlanInstance, PlanLocation, ServiceRequest)
            )

    def test_create_and_read_preserve_plan_and_scope(self):
        data = self.payload()
        response = self.client.post("/api/plans", json=data)
        self.assertEqual(response.status_code, 201, response.text)
        saved = response.json()
        UUID(saved["id"])
        self.assertNotEqual(saved["id"], data["request_id"])
        self.assertEqual(saved["title"], data["title"])
        self.assertEqual(saved["location_ids"], ["LOC-A-LOBBY", "LOC-B-LOBBY"])
        self.assertEqual(saved["planned_start_at"], "2026-11-01T01:00:00Z")
        self.assertEqual(saved["version"], 1)
        self.assertEqual(
            self.client.get("/api/plans/" + saved["id"]).json(), saved
        )
        self.assertEqual(self.counts(), (1, 2, 0))

    def test_retry_accepts_reordered_scope_and_equivalent_times(self):
        data = self.payload()
        original = self.client.post("/api/plans", json=data).json()
        data.update(
            location_ids=list(reversed(data["location_ids"])),
            planned_start_at="2026-11-01T01:00:00Z",
            planned_end_at="2026-11-01T01:30:00Z",
            plan_kind="routine",
        )
        response = self.client.post("/api/plans", json=data)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), original)
        self.assertEqual(self.counts(), (1, 2, 0))

    def test_changed_content_conflicts_and_preserves_original(self):
        data = self.payload()
        original = self.client.post("/api/plans", json=data).json()
        for changes in (
            {"title": "另一计划"}, {"requirements": "另一要求"},
            {"owner_name": "另一负责人"}, {"service_category": "other"},
            {"plan_kind": "special"}, {"location_ids": ["LOC-A-LOBBY"]},
            {"planned_end_at": "2026-11-01T10:00:00+08:00"},
        ):
            with self.subTest(changes=changes):
                response = self.client.post("/api/plans", json={**data, **changes})
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(self.counts(), (1, 2, 0))
                self.assertEqual(
                    self.client.get("/api/plans/" + original["id"]).json(), original
                )

    def test_invalid_input_is_422_without_writes(self):
        for changes in (
            {"title": "   "}, {"location_ids": []},
            {"location_ids": ["LOC-A-LOBBY", "LOC-A-LOBBY"]},
            {"planned_start_at": "2026-11-01T09:00:00"},
            {"planned_end_at": "2026-11-01T08:00:00+08:00"},
            {"id": str(uuid4())}, {"version": 1},
            {"created_at": "2026-11-01T01:00:00Z"},
        ):
            with self.subTest(changes=changes):
                response = self.client.post("/api/plans", json=self.payload(**changes))
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(self.counts(), (0, 0, 0))

    def test_unknown_location_rolls_back_and_submission_can_be_fixed(self):
        data = self.payload(location_ids=["LOC-A-LOBBY", "MISSING"])
        response = self.client.post("/api/plans", json=data)
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.counts(), (0, 0, 0))
        data["location_ids"] = ["LOC-A-LOBBY", "LOC-B-LOBBY"]
        self.assertEqual(self.client.post("/api/plans", json=data).status_code, 201)
        self.assertEqual(self.counts(), (1, 2, 0))

    def test_response_failure_rolls_back_plan_and_all_locations(self):
        data = self.payload()
        with patch(
            "routers.plans.PlanRead.model_validate",
            side_effect=RuntimeError("synthetic response failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "synthetic response failure"):
                self.client.post("/api/plans", json=data)
        self.assertEqual(self.counts(), (0, 0, 0))
        self.assertEqual(self.client.post("/api/plans", json=data).status_code, 201)

    def test_simultaneous_retries_create_one_complete_plan(self):
        data = self.payload()
        barrier = Barrier(2)

        def send():
            with TestClient(self.app) as client:
                barrier.wait(timeout=5)
                response = client.post("/api/plans", json=data)
                return response.status_code, response.json()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(send), pool.submit(send)]
            results = [future.result(timeout=10) for future in futures]
        self.assertEqual(sorted(code for code, body in results), [200, 201])
        self.assertEqual(results[0][1], results[1][1])
        self.assertEqual(self.counts(), (1, 2, 0))

    def test_busy_database_is_503_and_same_submission_can_retry(self):
        @event.listens_for(self.engine, "connect")
        def shorten_timeout(connection, record):
            connection.execute("PRAGMA busy_timeout=50").close()

        data = self.payload()
        with self.engine.connect() as locker:
            transaction = locker.begin()
            try:
                locker.execute(Location.__table__.insert().values(
                    id="LOC-LOCK", name="合成锁测试", version=1
                ))
                response = self.client.post("/api/plans", json=data)
                self.assertEqual(response.status_code, 503, response.text)
            finally:
                transaction.rollback()
        self.assertEqual(self.counts(), (0, 0, 0))
        self.assertEqual(self.client.post("/api/plans", json=data).status_code, 201)

    def test_missing_and_invalid_ids(self):
        self.assertEqual(self.client.get("/api/plans/" + str(uuid4())).status_code, 404)
        self.assertEqual(self.client.get("/api/plans/not-a-uuid").status_code, 422)
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_main_registers_plan_routes_and_documents_statuses(self):
        from main import app

        with TestClient(app) as client:
            paths = client.get("/openapi.json").json()["paths"]
        operation = paths["/api/plans"]["post"]
        for code in ("200", "201", "409", "422", "503"):
            self.assertIn(code, operation["responses"])
        self.assertIn("get", paths["/api/plans/{plan_id}"])

    def test_empty_list_is_read_only(self):
        response = self.client.get("/api/plans")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), [])
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_list_pagination_is_stable_and_keeps_complete_scope(self):
        created = []
        for title in ("合成一", "合成二", "合成三"):
            response = self.client.post("/api/plans", json=self.payload(title=title))
            self.assertEqual(response.status_code, 201, response.text)
            created.append(response.json())
        with self.sessions.begin() as db:
            for plan in db.scalars(select(PlanInstance)):
                plan.created_at = datetime.fromisoformat("2026-10-31T08:00:00Z")
        expected = sorted((plan["id"] for plan in created), reverse=True)
        first = self.client.get("/api/plans?limit=2&offset=0").json()
        second = self.client.get("/api/plans?limit=2&offset=2").json()
        self.assertEqual([plan["id"] for plan in first + second], expected)
        for plan in first + second:
            self.assertEqual(plan["location_ids"], ["LOC-A-LOBBY", "LOC-B-LOBBY"])
        self.assertEqual(self.counts(), (3, 6, 0))

    def test_invalid_list_bounds_do_not_write(self):
        for query in ("limit=0", "limit=101", "offset=-1", "limit=bad"):
            with self.subTest(query=query):
                self.assertEqual(self.client.get("/api/plans?" + query).status_code, 422)
                self.assertEqual(self.counts(), (0, 0, 0))



if __name__ == "__main__":
    unittest.main()