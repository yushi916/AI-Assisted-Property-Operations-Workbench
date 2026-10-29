import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier
from unittest.mock import patch
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import sessionmaker

from database import Base, create_db_engine, get_db
from models import Location, ServiceRequest
from routers.reports import router


def payload():
    return {
        "request_id": str(uuid4()),
        "service_category": "cleaning",
        "description": "  合成演示：居民反映公共区域仍有垃圾。  ",
        "source": "web",
        "risk_status": "unknown",
        "location_id": None,
        "observed_at": "2026-10-28T09:50:00+08:00",
    }


class TestReportsAPI(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.engine = create_db_engine(
            Path(self.folder.name) / "api.db"
        )
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(
            bind=self.engine,
            expire_on_commit=False,
        )

        def test_db():
            with self.sessions() as db:
                yield db

        self.app = FastAPI()
        self.app.include_router(router)
        self.app.dependency_overrides[get_db] = test_db
        self.client = TestClient(self.app)

        self.clock_patch = patch("routers.reports.utc_now")
        self.clock = self.clock_patch.start()
        self.clock.return_value = datetime(
            2026, 10, 28, 2, 0, tzinfo=UTC
        )

    def tearDown(self):
        self.client.close()
        self.clock_patch.stop()
        self.engine.dispose()
        self.folder.cleanup()

    def count(self):
        with self.sessions() as db:
            return db.scalar(
                select(func.count()).select_from(ServiceRequest)
            )

    def test_create_read_and_list_preserve_data(self):
        data = payload()
        response = self.client.post(
            "/api/service-requests", json=data
        )
        self.assertEqual(response.status_code, 201, response.text)

        report = response.json()
        UUID(report["id"])
        self.assertEqual(report["description"], data["description"])
        self.assertEqual(
            report["received_at"], "2026-10-28T02:00:00Z"
        )
        self.assertEqual(
            report["observed_at"], "2026-10-28T01:50:00Z"
        )
        self.assertEqual(report["version"], 1)

        self.assertEqual(
            self.client.get(
                "/api/service-requests/" + report["id"]
            ).json(),
            report,
        )
        self.assertEqual(
            self.client.get("/api/service-requests").json(),
            [report],
        )
        self.assertEqual(self.count(), 1)

    def test_same_request_returns_original_after_time_passes(self):
        data = payload()
        original = self.client.post(
            "/api/service-requests", json=data
        ).json()

        self.clock.return_value = datetime(
            2026, 10, 28, 3, 0, tzinfo=UTC
        )
        retry = self.client.post(
            "/api/service-requests", json=data
        )

        self.assertEqual(retry.status_code, 200, retry.text)
        self.assertEqual(retry.json(), original)
        self.assertEqual(self.count(), 1)

    def test_changed_content_conflicts_without_overwriting(self):
        data = payload()
        original = self.client.post(
            "/api/service-requests", json=data
        ).json()

        data["description"] = "另一条内容"
        response = self.client.post(
            "/api/service-requests", json=data
        )

        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.count(), 1)
        self.assertEqual(
            self.client.get(
                "/api/service-requests/" + original["id"]
            ).json(),
            original,
        )

    def test_same_instant_in_different_offsets_is_same_request(self):
        data = payload()
        original = self.client.post(
            "/api/service-requests", json=data
        ).json()

        data["observed_at"] = "2026-10-28T01:50:00Z"
        retry = self.client.post(
            "/api/service-requests", json=data
        )

        self.assertEqual(retry.status_code, 200, retry.text)
        self.assertEqual(retry.json(), original)
        self.assertEqual(self.count(), 1)

    def test_omitted_unknown_fields_match_explicit_null(self):
        data = payload()
        data["observed_at"] = None
        original = self.client.post(
            "/api/service-requests", json=data
        ).json()

        del data["observed_at"]
        del data["location_id"]
        retry = self.client.post(
            "/api/service-requests", json=data
        )

        self.assertEqual(retry.status_code, 200, retry.text)
        self.assertEqual(retry.json(), original)

    def test_nonexistent_location_is_422_without_writes(self):
        data = payload()
        data["location_id"] = "MISSING"
        response = self.client.post(
            "/api/service-requests", json=data
        )

        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.count(), 0)

    def test_valid_location_is_saved(self):
        with self.sessions.begin() as db:
            db.add(Location(id="LOC-A-LOBBY", name="A区大堂"))

        data = payload()
        data["location_id"] = "LOC-A-LOBBY"
        response = self.client.post(
            "/api/service-requests", json=data
        )

        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(
            response.json()["location_id"], "LOC-A-LOBBY"
        )

    def test_future_observation_rolls_back_and_id_can_be_retried(self):
        data = payload()
        data["observed_at"] = "2026-10-29T09:50:00+08:00"
        response = self.client.post(
            "/api/service-requests", json=data
        )

        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.count(), 0)

        data["observed_at"] = None
        self.assertEqual(
            self.client.post(
                "/api/service-requests", json=data
            ).status_code,
            201,
        )
        self.assertEqual(self.count(), 1)

    def test_blank_input_is_422_without_writes(self):
        data = payload()
        data["description"] = " \n\t"

        self.assertEqual(
            self.client.post(
                "/api/service-requests", json=data
            ).status_code,
            422,
        )
        self.assertEqual(self.count(), 0)

    def test_naive_observation_is_422_without_writes(self):
        data = payload()
        data["observed_at"] = "2026-10-28T09:50:00"

        self.assertEqual(
            self.client.post(
                "/api/service-requests", json=data
            ).status_code,
            422,
        )
        self.assertEqual(self.count(), 0)

    def test_client_cannot_supply_server_timestamp(self):
        data = payload()
        data["received_at"] = "2000-01-01T00:00:00Z"

        self.assertEqual(
            self.client.post(
                "/api/service-requests", json=data
            ).status_code,
            422,
        )
        self.assertEqual(self.count(), 0)

    def test_risk_must_be_declared(self):
        data = payload()
        del data["risk_status"]

        self.assertEqual(
            self.client.post(
                "/api/service-requests", json=data
            ).status_code,
            422,
        )
        self.assertEqual(self.count(), 0)

    def test_risk_flags_are_persisted_as_declared(self):
        data = payload()
        data["risk_status"] = "urgent"
        response = self.client.post(
            "/api/service-requests", json=data
        )

        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["risk_status"], "urgent")

    def test_missing_and_invalid_report_ids(self):
        self.assertEqual(
            self.client.get(
                "/api/service-requests/" + str(uuid4())
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                "/api/service-requests/not-a-uuid"
            ).status_code,
            422,
        )

    def test_list_bounds_and_paging(self):
        first = self.client.post(
            "/api/service-requests", json=payload()
        ).json()

        self.clock.return_value = datetime(
            2026, 10, 28, 3, 0, tzinfo=UTC
        )
        second = self.client.post(
            "/api/service-requests", json=payload()
        ).json()

        self.assertEqual(
            self.client.get(
                "/api/service-requests?limit=1"
            ).json(),
            [second],
        )
        self.assertEqual(
            self.client.get(
                "/api/service-requests?limit=1&offset=1"
            ).json(),
            [first],
        )
        for query in ("limit=0", "limit=101", "offset=-1"):
            with self.subTest(query=query):
                self.assertEqual(
                    self.client.get(
                        "/api/service-requests?" + query
                    ).status_code,
                    422,
                )

    def test_two_simultaneous_retries_create_one_record(self):
        data = payload()
        barrier = Barrier(2)

        def send():
            with TestClient(self.app) as client:
                barrier.wait(timeout=5)
                response = client.post(
                    "/api/service-requests", json=data
                )
                return response.status_code, response.json()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(send), pool.submit(send)]
            results = [
                future.result(timeout=10) for future in futures
            ]

        self.assertEqual(
            sorted(item[0] for item in results), [200, 201]
        )
        self.assertEqual(results[0][1], results[1][1])
        self.assertEqual(self.count(), 1)

    def test_response_validation_failure_rolls_back_insert(self):
        data = payload()
        with patch(
            "routers.reports.ServiceRequestRead.model_validate",
            side_effect=RuntimeError("synthetic response failure"),
        ):
            with self.assertRaisesRegex(
                RuntimeError, "synthetic response failure"
            ):
                self.client.post(
                    "/api/service-requests", json=data
                )

        self.assertEqual(self.count(), 0)
        self.assertEqual(
            self.client.post(
                "/api/service-requests", json=data
            ).status_code,
            201,
        )

    def test_busy_database_is_503_and_same_submission_can_retry(self):
        @event.listens_for(self.engine, "connect")
        def shorten_busy_timeout(connection, record):
            connection.execute("PRAGMA busy_timeout=50").close()

        locker = self.engine.connect()
        transaction = locker.begin()
        try:
            locker.execute(
                insert(Location).values(
                    id="LOC-LOCK",
                    name="锁测试",
                    version=1,
                )
            )
            data = payload()
            response = self.client.post(
                "/api/service-requests", json=data
            )
            self.assertEqual(
                response.status_code, 503, response.text
            )
        finally:
            transaction.rollback()
            locker.close()

        self.assertEqual(self.count(), 0)
        self.assertEqual(
            self.client.post(
                "/api/service-requests", json=data
            ).status_code,
            201,
        )

    def test_openapi_documents_replay_conflict_and_busy(self):
        operation = self.client.get(
            "/openapi.json"
        ).json()["paths"]["/api/service-requests"]["post"]

        for code in ("200", "201", "409", "422", "503"):
            self.assertIn(code, operation["responses"])

    def test_assembled_main_keeps_home_health_and_registers_reports(self):
        from main import app

        with TestClient(app) as client:
            self.assertEqual(
                client.get("/health").json(),
                {"status": "ok"},
            )
            home = client.get("/")
            self.assertEqual(home.status_code, 200)
            self.assertIn(
                "text/html", home.headers["content-type"]
            )
            self.assertIn(
                "物业事项与履约证据工作台", home.text
            )

            paths = client.get("/openapi.json").json()["paths"]
            self.assertIn(
                "post", paths["/api/service-requests"]
            )
            self.assertIn(
                "get",
                paths["/api/service-requests/{report_id}"],
            )