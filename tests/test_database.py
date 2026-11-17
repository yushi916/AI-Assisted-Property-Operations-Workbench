import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func, inspect, select
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session, sessionmaker

from database import Base, create_db_engine
from models import Location, ServiceRequest


def make_report(**changes):
    values = {
        "request_id": uuid4(),
        "service_category": "cleaning",
        "description": "合成演示：大堂垃圾待核实。",
        "source": "web",
        "risk_status": "unknown",
        "location_id": None,
        "observed_at": None,
    }
    values.update(changes)
    return ServiceRequest(**values)


class TestDatabase(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.db_path = Path(self.folder.name) / "case.db"
        self.engine = create_db_engine(self.db_path)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(
            bind=self.engine,
            expire_on_commit=False,
        )

    def tearDown(self):
        self.engine.dispose()
        self.folder.cleanup()

    def counts(self):
        with self.sessions() as session:
            return (
                session.scalar(select(func.count()).select_from(Location)),
                session.scalar(select(func.count()).select_from(ServiceRequest)),
            )

    def test_tables_and_foreign_keys_on_multiple_connections(self):
        self.assertEqual(
            inspect(self.engine).get_table_names(),
            [
                "agent_runs", "execution_locations", "execution_records", "inspection_records",
                "locations", "plan_instances", "plan_locations",
                "request_plan_links", "service_requests",
            ],
        )
        with self.engine.connect() as first, self.engine.connect() as second:
            self.assertEqual(
                first.exec_driver_sql("PRAGMA foreign_keys").scalar(), 1
            )
            self.assertEqual(
                second.exec_driver_sql("PRAGMA foreign_keys").scalar(), 1
            )

    def test_unknown_values_and_file_persistence_after_reopen(self):
        with self.sessions.begin() as session:
            report = make_report()
            session.add(report)
            session.flush()
            report_id = report.id

        self.engine.dispose()
        reopened = create_db_engine(self.db_path)
        try:
            with Session(reopened) as session:
                saved = session.get(ServiceRequest, report_id)
                self.assertIsNone(saved.location_id)
                self.assertIsNone(saved.observed_at)
                self.assertEqual(
                    saved.description, "合成演示：大堂垃圾待核实。"
                )
                self.assertEqual(saved.version, 1)
        finally:
            reopened.dispose()

    def test_unknown_location_id_rejected_without_report(self):
        with self.assertRaises(IntegrityError) as caught:
            with self.sessions.begin() as session:
                session.add(make_report(location_id="MISSING"))
        self.assertEqual(
            caught.exception.orig.sqlite_errorname,
            "SQLITE_CONSTRAINT_FOREIGNKEY",
        )
        self.assertEqual(self.counts(), (0, 0))

    def test_duplicate_request_id_rejected_preserving_original(self):
        request_id = uuid4()
        with self.sessions.begin() as session:
            session.add(make_report(request_id=request_id))

        with self.assertRaises(IntegrityError) as caught:
            with self.sessions.begin() as session:
                session.add(
                    make_report(
                        request_id=request_id,
                        description="不应覆盖原文",
                    )
                )
        self.assertEqual(
            caught.exception.orig.sqlite_errorname,
            "SQLITE_CONSTRAINT_UNIQUE",
        )
        self.assertEqual(self.counts(), (0, 1))
        with self.sessions() as session:
            self.assertEqual(
                session.scalar(select(ServiceRequest)).description,
                "合成演示：大堂垃圾待核实。",
            )

    def test_late_failure_rolls_back_earlier_flushed_location(self):
        with self.assertRaises(IntegrityError) as caught:
            with self.sessions.begin() as session:
                session.add(Location(id="LOC-TEMP", name="临时区域"))
                session.flush()
                location_count = session.scalar(
                    select(func.count()).select_from(Location)
                )
                self.assertEqual(location_count, 1)

                session.add(make_report(location_id="MISSING"))
                session.flush()

        self.assertEqual(
            caught.exception.orig.sqlite_errorname,
            "SQLITE_CONSTRAINT_FOREIGNKEY",
        )
        self.assertEqual(self.counts(), (0, 0))

    def test_known_location_and_utc_time_round_trip(self):
        observation = datetime.fromisoformat("2026-10-28T09:50:00+08:00")
        with self.sessions.begin() as session:
            place = Location(id="LOC-A-LOBBY", name="A区大堂")
            report = make_report(observed_at=observation)
            report.location = place
            session.add(report)
            session.flush()
            report_id = report.id

        with self.sessions() as session:
            saved = session.get(ServiceRequest, report_id)
            self.assertEqual(
                saved.observed_at, observation.astimezone(UTC)
            )
            self.assertEqual(saved.observed_at.hour, 1)
            self.assertEqual(saved.observed_at.tzinfo, UTC)
            self.assertEqual(saved.received_at.tzinfo, UTC)
            self.assertEqual(saved.location.name, "A区大堂")

    def test_naive_datetime_rejected_without_writes(self):
        with self.assertRaises(StatementError) as caught:
            with self.sessions.begin() as session:
                session.add(
                    make_report(observed_at=datetime(2026, 10, 28, 9, 50))
                )
        self.assertIsInstance(caught.exception.orig, ValueError)
        self.assertEqual(str(caught.exception.orig), "时间必须带时区")
        self.assertEqual(self.counts(), (0, 0))

    def test_version_constraint_rejects_zero(self):
        with self.assertRaises(IntegrityError) as caught:
            with self.sessions.begin() as session:
                session.add(make_report(version=0))
        self.assertEqual(
            caught.exception.orig.sqlite_errorname,
            "SQLITE_CONSTRAINT_CHECK",
        )
        self.assertEqual(self.counts(), (0, 0))