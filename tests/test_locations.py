import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from database import Base, create_db_engine, get_db
from models import Location, ServiceRequest
from routers.locations import router as locations_router
from routers.reports import router as reports_router
from schemas import ServiceRequestRead
from seed_demo_locations import DEMO_LOCATIONS, seed_demo_locations


class TestLocations(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.engine = create_db_engine(
            Path(self.folder.name) / "locations.db"
        )
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(
            bind=self.engine,
            expire_on_commit=False,
        )

        def test_db():
            with self.sessions() as db:
                yield db

        app = FastAPI()
        app.include_router(locations_router)
        app.include_router(reports_router)
        app.dependency_overrides[get_db] = test_db
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.engine.dispose()
        self.folder.cleanup()

    def counts(self):
        with self.sessions() as db:
            return (
                db.scalar(select(func.count()).select_from(Location)),
                db.scalar(select(func.count()).select_from(ServiceRequest)),
            )

    def create_report(self, location_id=None):
        return self.client.post("/api/service-requests", json={
            "request_id": str(uuid4()),
            "service_category": "cleaning",
            "description": "  合成演示：具体地点仍待核实。  ",
            "source": "web",
            "risk_status": "unknown",
            "location_id": location_id,
            "observed_at": None,
        })

    def test_empty_catalog_is_read_only(self):
        response = self.client.get("/api/locations")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])
        self.assertEqual(self.counts(), (0, 0))

    def test_seeded_catalog_returns_stable_ids_names_and_versions(self):
        self.assertEqual(seed_demo_locations(self.engine), 3)
        response = self.client.get("/api/locations")
        self.assertEqual(response.status_code, 200)
        expected = [
            {"id": location_id, "name": name, "version": 1}
            for location_id, name in sorted(DEMO_LOCATIONS)
        ]
        self.assertEqual(response.json(), expected)

    def test_repeated_seed_does_not_duplicate(self):
        self.assertEqual(seed_demo_locations(self.engine), 3)
        self.assertEqual(seed_demo_locations(self.engine), 0)
        self.assertEqual(self.counts(), (3, 0))

    def test_matching_existing_location_keeps_its_version(self):
        with self.sessions.begin() as db:
            db.add(Location(
                id="LOC-A-LOBBY",
                name="A区大堂",
                version=7,
            ))

        self.assertEqual(seed_demo_locations(self.engine), 2)
        with self.sessions() as db:
            self.assertEqual(
                db.get(Location, "LOC-A-LOBBY").version,
                7,
            )
        self.assertEqual(self.counts(), (3, 0))

    def test_late_name_conflict_rolls_back_new_locations(self):
        with self.sessions.begin() as db:
            db.add(Location(
                id="LOC-B-LOBBY",
                name="既有不同点位",
                version=5,
            ))

        with self.assertRaisesRegex(ValueError, "名称不同"):
            seed_demo_locations(self.engine)

        self.assertEqual(self.counts(), (1, 0))
        with self.sessions() as db:
            original = db.get(Location, "LOC-B-LOBBY")
            self.assertEqual(original.name, "既有不同点位")
            self.assertEqual(original.version, 5)
            self.assertIsNone(db.get(Location, "LOC-A-LOBBY"))

    def test_seed_preserves_other_locations_and_unknown_report(self):
        with self.sessions.begin() as db:
            db.add(Location(
                id="LOC-C-OTHER",
                name="C区其他点位",
                version=2,
            ))

        response = self.create_report()
        self.assertEqual(response.status_code, 201, response.text)
        before = response.json()

        seed_demo_locations(self.engine)
        seed_demo_locations(self.engine)

        after = self.client.get(
            "/api/service-requests/" + before["id"]
        )
        self.assertEqual(after.status_code, 200)
        self.assertEqual(after.json(), before)
        self.assertIsNone(after.json()["location_id"])
        self.assertEqual(self.counts(), (4, 1))

        with self.sessions() as db:
            self.assertEqual(
                db.get(Location, "LOC-C-OTHER").name,
                "C区其他点位",
            )

    def test_report_can_use_catalog_id_but_cannot_invent_one(self):
        seed_demo_locations(self.engine)

        response = self.create_report("LOC-A-LOBBY")
        self.assertEqual(response.status_code, 201, response.text)
        report = ServiceRequestRead.model_validate(response.json())
        self.assertEqual(report.location_id, "LOC-A-LOBBY")

        read = self.client.get(
            "/api/service-requests/" + str(report.id)
        )
        self.assertEqual(read.json(), response.json())

        invalid = self.create_report("LOC-NOT-IN-CATALOG")
        self.assertEqual(invalid.status_code, 422, invalid.text)
        self.assertEqual(self.counts(), (3, 1))