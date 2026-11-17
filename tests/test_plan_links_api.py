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

    def revoke_url(self, link, report=None):
        return self.url(report) + "/" + link["id"] + "/revoke"

    def revoke_payload(self, **changes):
        data = dict(request_id=str(uuid4()), expected_version=1,
                    revoked_by=" 合成撤销人 ", reason=" 合成：选错计划，撤销后重新核对 ")
        return {**data, **changes}

    def new_link(self, report=None):
        response = self.client.post(self.url(report), json=self.payload())
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_revoke_input_validation_and_route_registration(self):
        link = self.new_link()
        before = self.rows()
        for changes in ({"reason": "　"}, {"revoked_by": " "}, {"expected_version": 0},
                {"expected_version": True}, {"expected_version": "1"}, {"request_id": "bad"},
                {"status": "revoked"}, {"revoked_at": "2030-01-01T00:00:00Z"},
                {"reason": "x" * 2001}, {"revoked_by": "x" * 121}):
            with self.subTest(changes=changes):
                result = self.client.post(self.revoke_url(link), json=self.revoke_payload(**changes))
                self.assertEqual(result.status_code, 422, result.text)
                self.assertEqual(self.rows(), before)
        from main import app
        with TestClient(app) as client:
            paths = client.get("/openapi.json").json()["paths"]
        self.assertIn("post", paths["/api/service-requests/{report_id}/plan-links/{link_id}/revoke"])

    def test_revoke_preserves_creation_and_original_retry_receipt(self):
        link = self.new_link()
        data = self.revoke_payload()
        result = self.client.post(self.revoke_url(link), json=data)
        self.assertEqual(result.status_code, 200, result.text)
        saved = result.json()
        for field in ("id", "request_id", "report_id", "plan_id", "linked_by", "reason", "linked_at"):
            self.assertEqual(saved[field], link[field])
        self.assertEqual((saved["status"], saved["version"]), ("revoked", 2))
        self.assertEqual(saved["revocation_request_id"], data["request_id"])
        self.assertEqual(saved["revoked_by"], data["revoked_by"])
        self.assertEqual(saved["revoke_reason"], data["reason"])
        before = self.rows()
        with patch("routers.plan_links.utc_now", return_value=datetime(2000, 1, 1, tzinfo=UTC)):
            retry = self.client.post(self.revoke_url(link), json=data)
        self.assertEqual(retry.status_code, 200, retry.text)
        self.assertEqual(retry.json(), saved)
        self.engine.dispose()
        self.assertEqual(self.client.get(self.url()).json(), [saved])
        self.assertEqual(self.rows(), before)
        self.assertEqual({name: before[name] for name in OLD}, {name: self.original[name] for name in OLD})

    def test_revoke_stale_versions_conflicts_and_missing_links_do_not_write(self):
        link = self.new_link()
        data = self.revoke_payload()
        before = self.rows()
        stale = self.client.post(self.revoke_url(link), json={**data, "expected_version": 2})
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(self.rows(), before)
        self.assertEqual(self.client.post(self.revoke_url(link), json=data).status_code, 200)
        before = self.rows()
        for changes in ({"reason": "另一个理由"}, {"revoked_by": "另一人"},
                        {"expected_version": 2}, {"request_id": str(uuid4())}):
            with self.subTest(changes=changes):
                result = self.client.post(self.revoke_url(link), json={**data, **changes})
                self.assertEqual(result.status_code, 409, result.text)
                self.assertEqual(self.rows(), before)
        for url in (self.revoke_url(link, self.reports[1]),
                    self.url() + "/" + str(uuid4()) + "/revoke"):
            self.assertEqual(self.client.post(url, json=data).status_code, 404)
            self.assertEqual(self.rows(), before)

    def test_revoke_request_cannot_be_reused_on_another_link(self):
        first = self.new_link()
        second = self.new_link(self.reports[1])
        data = self.revoke_payload()
        self.assertEqual(self.client.post(self.revoke_url(first), json=data).status_code, 200)
        before = self.rows()
        result = self.client.post(self.revoke_url(second, self.reports[1]), json=data)
        self.assertEqual(result.status_code, 409, result.text)
        self.assertEqual(self.rows(), before)
        self.assertEqual(self.client.get(self.url(self.reports[1])).json()[0]["status"], "active")

    def test_revoke_then_relink_retains_history_and_old_retries_do_not_change_new_link(self):
        create_data = self.payload()
        old = self.client.post(self.url(), json=create_data).json()
        data = self.revoke_payload()
        revoked = self.client.post(self.revoke_url(old), json=data).json()
        new = self.new_link()
        before = self.rows()
        retry = self.client.post(self.url(), json=create_data)
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.json(), revoked)
        self.assertEqual(self.client.post(self.revoke_url(old), json=data).json(), revoked)
        records = {item["id"]: item for item in self.client.get(self.url()).json()}
        self.assertEqual(records, {old["id"]: revoked, new["id"]: new})
        self.assertEqual(self.rows(), before)

    def test_revoke_response_failure_rolls_back_transition(self):
        link = self.new_link()
        data = self.revoke_payload()
        before = self.rows()
        with patch("routers.plan_links.RequestPlanLinkRead.model_validate", side_effect=RuntimeError("合成撤销回执失败")):
            with self.assertRaisesRegex(RuntimeError, "合成撤销回执失败"):
                self.client.post(self.revoke_url(link), json=data)
        self.assertEqual(self.rows(), before)
        self.assertEqual(self.client.post(self.revoke_url(link), json=data).status_code, 200)

    def test_simultaneous_revokes_change_version_only_once(self):
        for same, report in ((True, self.reports[0]), (False, self.reports[1])):
            link = self.new_link(report)
            barrier = Barrier(2)
            first = self.revoke_payload()
            second = first if same else self.revoke_payload(reason="另一合成撤销理由")

            def send(data):
                with TestClient(self.app) as client:
                    barrier.wait(timeout=5)
                    result = client.post(self.revoke_url(link, report), json=data)
                    return result.status_code, result.json()

            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(send, item) for item in (first, second)]
                results = [future.result(timeout=10) for future in futures]
            self.assertEqual(sorted(code for code, _ in results), [200, 200] if same else [200, 409])
            if same:
                self.assertEqual(results[0][1], results[1][1])
            stored = self.client.get(self.url(report)).json()[0]
            self.assertEqual((stored["status"], stored["version"]), ("revoked", 2))
            self.assertIn(stored["revoke_reason"], (first["reason"], second["reason"]))

    def test_revoke_busy_and_clock_errors_leave_active_record_for_retry(self):
        link = self.new_link()
        data = self.revoke_payload()
        before = self.rows()

        @event.listens_for(self.engine, "connect")
        def shorten_timeout(connection, record):
            connection.execute("PRAGMA busy_timeout=50").close()

        self.engine.dispose()
        with self.engine.connect() as locker:
            transaction = locker.begin()
            try:
                locker.exec_driver_sql("UPDATE service_requests SET version=version")
                result = self.client.post(self.revoke_url(link), json=data)
                self.assertEqual(result.status_code, 503, result.text)
            finally:
                transaction.rollback()
        self.assertEqual(self.rows(), before)
        earlier = datetime.fromisoformat(link["linked_at"].replace("Z", "+00:00")) - timedelta(seconds=1)
        with patch("routers.plan_links.utc_now", return_value=earlier):
            result = self.client.post(self.revoke_url(link), json=data)
        self.assertEqual(result.status_code, 503, result.text)
        self.assertEqual(self.rows(), before)
        self.assertEqual(self.client.post(self.revoke_url(link), json=data).status_code, 200)


if __name__ == "__main__":
    unittest.main()