import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
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
from models import PlanInstance, PlanLocation, ServiceRequest
from routers.plan_links import router
from seed_demo_location_hierarchy import seed_demo_location_hierarchy


ROOT = Path(__file__).resolve().parents[1]
OLD = ("locations", "service_requests", "plan_instances", "plan_locations",
       "execution_records", "execution_locations", "inspection_records")


class PlanLinksAPITests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.engine = create_db_engine(Path(folder.name) / "api.db")
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
        now = datetime(2026, 11, 1, 1, tzinfo=UTC)
        with self.sessions.begin() as db:
            reports = [ServiceRequest(request_id=uuid4(), service_category="cleaning",
                description=" 合成居民原文 ", source="web", risk_status="unknown",
                location_id=point, observed_at=now + timedelta(days=3))
                for point in ("LOC-A-LOBBY", "LOC-A-LOBBY", None, "LOC-A-B02-U01-R302")]
            plans = [PlanInstance(request_id=uuid4(), title="合成计划", service_category=category,
                requirements="核对指定地点", owner_name="合成人员", planned_start_at=now,
                planned_end_at=now + timedelta(hours=1),
                locations=[PlanLocation(location_id=point)])
                for category, point in (("cleaning", "LOC-A-LOBBY"),
                    ("water_supply", "LOC-A-LOBBY"), ("cleaning", "LOC-B-LOBBY"))]
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
        self.original = self.rows()

    def url(self, report=None):
        return f"/api/service-requests/{report or self.reports[0]}/plan-links"

    def payload(self, **changes):
        data = dict(request_id=str(uuid4()), plan_id=str(self.plans[0]),
                    linked_by=" 合成关联人 ", reason=" 合成：发生时间不同，仍需共同核对 ")
        return {**data, **changes}

    def rows(self):
        with self.engine.connect() as connection:
            return {name: connection.exec_driver_sql(f'SELECT * FROM "{name}" ORDER BY 1, 2').all()
                    for name in OLD + ("request_plan_links",)}

    def test_create_retry_and_reopen_keep_original_text_and_old_records(self):
        data = self.payload()
        response = self.client.post(self.url(), json=data)
        self.assertEqual(response.status_code, 201, response.text)
        saved = response.json()
        self.assertNotEqual(saved["id"], data["request_id"])
        self.assertEqual(saved["reason"], data["reason"])
        self.assertEqual(saved["linked_by"], data["linked_by"])
        self.assertEqual((saved["status"], saved["version"]), ("active", 1))
        self.assertIsNone(saved["revoked_at"])
        before = self.rows()
        retry = self.client.post(self.url(), json=data)
        self.assertEqual(retry.status_code, 200, retry.text)
        self.assertEqual(retry.json(), saved)
        self.engine.dispose()
        listed = self.client.get(self.url())
        self.assertEqual(listed.json(), [saved])
        self.assertEqual(listed.headers["cache-control"], "no-store")
        self.assertEqual(self.rows(), before)
        self.assertEqual({name: before[name] for name in OLD}, {name: self.original[name] for name in OLD})

    def test_changed_submission_or_duplicate_active_pair_conflicts_without_writes(self):
        data = self.payload()
        self.assertEqual(self.client.post(self.url(), json=data).status_code, 201)
        before = self.rows()
        for report, changes in ((None, {"reason": "另一理由"}), (None, {"linked_by": "另一人"}),
                (None, {"plan_id": str(uuid4())}), (self.reports[1], {}),
                (None, {"request_id": str(uuid4())})):
            with self.subTest(report=report, changes=changes):
                self.assertEqual(self.client.post(self.url(report), json={**data, **changes}).status_code, 409)
                self.assertEqual(self.rows(), before)

    def test_invalid_parents_category_scope_or_unknown_location_roll_back_and_allow_correction(self):
        for report, changes in ((uuid4(), {}), (None, {"plan_id": str(uuid4())}),
                (None, {"plan_id": str(self.plans[1])}), (None, {"plan_id": str(self.plans[2])}),
                (self.reports[2], {}), (self.reports[3], {})):
            with self.subTest(report=report, changes=changes):
                data = self.payload(**changes)
                self.assertEqual(self.client.post(self.url(report), json=data).status_code, 422)
                self.assertEqual(self.rows(), self.original)
        data = self.payload(plan_id=str(self.plans[2]))
        self.assertEqual(self.client.post(self.url(), json=data).status_code, 422)
        data["plan_id"] = str(self.plans[0])
        self.assertEqual(self.client.post(self.url(), json=data).status_code, 201)

    def test_input_cannot_forge_server_fields(self):
        for changes in ({"reason": "   "}, {"id": str(uuid4())}, {"status": "revoked"},
                        {"report_id": str(uuid4())}, {"linked_at": "2030-01-01T00:00:00Z"}):
            with self.subTest(changes=changes):
                self.assertEqual(self.client.post(self.url(), json=self.payload(**changes)).status_code, 422)
                self.assertEqual(self.rows(), self.original)

    def test_response_failure_rolls_back_link(self):
        data = self.payload()
        with patch("routers.plan_links.RequestPlanLinkRead.model_validate", side_effect=RuntimeError("合成回执失败")):
            with self.assertRaisesRegex(RuntimeError, "合成回执失败"):
                self.client.post(self.url(), json=data)
        self.assertEqual(self.rows(), self.original)
        self.assertEqual(self.client.post(self.url(), json=data).status_code, 201)

    def test_simultaneous_retries_or_duplicate_pair_create_only_one_link(self):
        for same_request, report in ((True, self.reports[0]), (False, self.reports[1])):
            barrier = Barrier(2)
            first = self.payload()
            second = first if same_request else self.payload()

            def send(data):
                with TestClient(self.app) as client:
                    barrier.wait(timeout=5)
                    return client.post(self.url(report), json=data).status_code

            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(send, item) for item in (first, second)]
                codes = sorted(future.result(timeout=10) for future in futures)
            self.assertEqual(codes, [200, 201] if same_request else [201, 409])
            self.assertEqual(len(self.client.get(self.url(report)).json()), 1)

    def test_list_filter_paging_errors_and_main_registration(self):
        self.assertEqual(self.client.get(self.url()).json(), [])
        self.client.post(self.url(), json=self.payload())
        self.client.post(self.url(self.reports[1]), json=self.payload())
        before = self.rows()
        self.assertEqual(len(self.client.get(self.url()+"?limit=1").json()), 1)
        self.assertEqual(self.client.get(self.url()+"?offset=1").json(), [])
        self.assertEqual(self.client.get(self.url(uuid4())).status_code, 404)
        for query in ("limit=0", "limit=101", "offset=-1"):
            self.assertEqual(self.client.get(self.url()+"?"+query).status_code, 422)
        self.assertEqual(self.rows(), before)
        with patch("routers.plan_links.Session.scalars", side_effect=OperationalError(
                "SELECT", {}, sqlite3.OperationalError("合成查询失败"))):
            failed = self.client.get(self.url())
        self.assertEqual(failed.status_code, 503)
        self.assertNotEqual(failed.json(), [])
        from main import app
        with TestClient(app) as client:
            paths = client.get("/openapi.json").json()["paths"]
        self.assertEqual(set(paths["/api/service-requests/{report_id}/plan-links"]), {"get", "post"})

    def test_busy_database_returns_503_and_can_retry(self):
        @event.listens_for(self.engine, "connect")
        def shorten_timeout(connection, record):
            connection.execute("PRAGMA busy_timeout=50").close()

        self.engine.dispose()
        data = self.payload()
        with self.engine.connect() as locker:
            transaction = locker.begin()
            try:
                locker.exec_driver_sql("UPDATE service_requests SET version=version")
                response = self.client.post(self.url(), json=data)
                self.assertEqual(response.status_code, 503, response.text)
            finally:
                transaction.rollback()
        self.assertEqual(self.rows(), self.original)
        self.assertEqual(self.client.post(self.url(), json=data).status_code, 201)


if __name__ == "__main__":
    unittest.main()