import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session

from adopt_intake_database import schema_signature
from database import Base, create_db_engine, engine as project_engine
from models import (
    ExecutionLocation, ExecutionRecord, InspectionRecord,
    PlanInstance, PlanLocation, RequestPlanLink, ServiceRequest,
)
from seed_demo_location_hierarchy import seed_demo_location_hierarchy
from tests.location_reference import create_pre_floor_schema


ROOT = Path(__file__).resolve().parents[1]
OLD = ("locations", "service_requests", "plan_instances", "plan_locations",
       "execution_records", "execution_locations", "inspection_records")
PREVIOUS = "0005_inspection_records"
CURRENT = "0006_request_plan_links"


class PlanLinkDataTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.engine = create_db_engine(Path(self.folder.name) / "case.db")
        guard = patch.object(project_engine, "connect", side_effect=AssertionError("禁止连接项目库"))
        guard.start()
        self.addCleanup(guard.stop)
        self.migrate("upgrade", PREVIOUS)
        seed_demo_location_hierarchy(self.engine)
        self.now = datetime(2026, 11, 6, 1, tzinfo=UTC)
        with Session(self.engine) as db, db.begin():
            reports = [ServiceRequest(
                request_id=uuid4(), service_category="cleaning", description=" 合成原始报事 ",
                source="web", risk_status="unknown", location_id="LOC-A-LOBBY",
            ) for _ in range(2)]
            plans = [PlanInstance(
                request_id=uuid4(), title="合成保洁计划", service_category="cleaning",
                requirements="分别核对两处大堂", owner_name="合成负责人",
                planned_start_at=self.now, planned_end_at=self.now + timedelta(hours=1),
                locations=[PlanLocation(location_id=x) for x in ("LOC-A-LOBBY", "LOC-B-LOBBY")],
            ) for _ in range(2)]
            db.add_all(reports + plans)
            db.flush()
            self.reports, self.plans = [x.id for x in reports], [x.id for x in plans]
            db.add_all([
                ExecutionRecord(request_id=uuid4(), plan_id=self.plans[0],
                    executor_name="合成执行人", description="合成执行反馈", completion_claim="unknown",
                    locations=[ExecutionLocation(location_id="LOC-A-LOBBY")]),
                InspectionRecord(request_id=uuid4(), plan_id=self.plans[0],
                    inspector_name="合成巡查人", description="合成巡查反馈", finding="unable_to_verify"),
            ])
        self.before = self.rows()
        with self.engine.connect() as connection:
            self.old_schema = schema_signature(connection)
        self.migrate("upgrade", CURRENT)

    def tearDown(self):
        self.engine.dispose()
        self.folder.cleanup()

    def migrate(self, action, revision):
        config = Config(str(ROOT / "alembic.ini"))
        with self.engine.begin() as connection:
            config.attributes["connection"] = connection
            getattr(command, action)(config, revision)

    def rows(self):
        with self.engine.connect() as connection:
            return {name: connection.exec_driver_sql(f'SELECT * FROM "{name}" ORDER BY 1, 2').all()
                    for name in OLD}

    def save(self, **changes):
        values = dict(request_id=uuid4(), report_id=self.reports[0], plan_id=self.plans[0],
                      linked_by=" 合成关联人 ", reason=" 合成：共同核对 ", linked_at=self.now)
        values.update(changes)
        with Session(self.engine) as db, db.begin():
            item = RequestPlanLink(**values)
            db.add(item)
            db.flush()
            return item.id, item.request_id

    def test_upgrade_preserves_old_rows_and_matches_models_and_partial_index(self):
        self.assertEqual(self.rows(), self.before)
        reference = create_db_engine(Path(self.folder.name) / "reference.db")
        try:
            create_pre_floor_schema(
                reference,
                table_names=(*OLD, "request_plan_links"),
            )
            with self.engine.connect() as actual, reference.connect() as expected:
                self.assertEqual(MigrationContext.configure(actual).get_current_heads(), (CURRENT,))
                signature = schema_signature(actual)
                self.assertEqual({name: signature[name] for name in OLD}, self.old_schema)
                self.assertEqual(signature, schema_signature(expected))
                for connection in (actual, expected):
                    index = next(x for x in inspect(connection).get_indexes("request_plan_links")
                                 if x["name"] == "uq_link_active_pair")
                    self.assertEqual(index["unique"], 1)
                    self.assertEqual(str(index["dialect_options"]["sqlite_where"]), "status = 'active'")
        finally:
            reference.dispose()
        self.migrate("upgrade", CURRENT)
        self.assertEqual(self.rows(), self.before)

    def test_many_to_many_allows_distinct_pairs_but_rejects_duplicates(self):
        key, request_id = self.save()
        self.save(plan_id=self.plans[1])
        self.save(report_id=self.reports[1])
        for changes in ({}, {"request_id": request_id, "report_id": self.reports[1], "plan_id": self.plans[1]}):
            with self.subTest(changes=changes), self.assertRaises(IntegrityError):
                self.save(**changes)
        with Session(self.engine) as db:
            item = db.get(RequestPlanLink, key)
            self.assertNotEqual(key, request_id)
            self.assertEqual((item.report.id, item.plan.id), (self.reports[0], self.plans[0]))
            self.assertEqual((item.linked_at, item.linked_by, item.reason),
                             (self.now, " 合成关联人 ", " 合成：共同核对 "))
            self.assertEqual(len(db.scalars(select(RequestPlanLink)).all()), 3)
        self.assertEqual(self.rows(), self.before)

    def test_revocation_retains_history_and_allows_new_active_link(self):
        key, request_id = self.save()
        with Session(self.engine) as db, db.begin():
            item = db.get(RequestPlanLink, key)
            item.status, item.version = "revoked", 2
            item.revocation_request_id = uuid4()
            item.revoked_by, item.revoke_reason = "合成撤销人", "合成：选错计划"
            item.revoked_at = self.now + timedelta(minutes=1)
        new_key, _ = self.save()
        self.engine.dispose()
        with Session(self.engine) as db:
            old = db.get(RequestPlanLink, key)
            self.assertEqual((old.request_id, old.reason), (request_id, " 合成：共同核对 "))
            self.assertEqual((old.status, old.version, old.revoke_reason), ("revoked", 2, "合成：选错计划"))
            self.assertEqual(old.revoked_at.tzinfo, UTC)
            self.assertNotEqual(key, new_key)
            self.assertEqual(db.get(RequestPlanLink, new_key).status, "active")
        self.assertEqual(self.rows(), self.before)

    def test_invalid_links_and_late_failure_rollback_without_altering_old_records(self):
        for changes in ({"report_id": uuid4()}, {"plan_id": uuid4()}, {"version": 0},
                        {"status": "other"}, {"linked_by": " "}, {"reason": "x" * 2001}):
            with self.subTest(changes=changes), self.assertRaises(IntegrityError):
                self.save(**changes)
        with self.assertRaises(IntegrityError):
            with Session(self.engine) as db, db.begin():
                db.add(RequestPlanLink(request_id=uuid4(), report_id=self.reports[0],
                    plan_id=self.plans[0], linked_by="合成人员", reason="合成理由"))
                db.flush()
                db.delete(db.get(ServiceRequest, self.reports[0]))
                db.flush()
        with Session(self.engine) as db:
            self.assertEqual(db.scalars(select(RequestPlanLink)).all(), [])
        self.assertEqual(self.rows(), self.before)

    def test_invalid_revocation_and_naive_time_are_rejected(self):
        revoked = dict(status="revoked", version=2, revocation_request_id=uuid4(),
                       revoked_at=self.now, revoked_by="合成撤销人", revoke_reason="合成撤销理由")
        for changes in ({"status": "revoked"}, {"revoked_by": "不能提前填写"},
                        {**revoked, "version": 1}, {**revoked, "revoked_by": " "},
                        {**revoked, "revoked_at": self.now - timedelta(seconds=1)}):
            with self.subTest(changes=changes), self.assertRaises(IntegrityError):
                self.save(**changes)
        with self.assertRaises(StatementError):
            self.save(linked_at=self.now.replace(tzinfo=None))

    def test_empty_downgrade_is_reversible_but_existing_history_is_protected(self):
        self.migrate("downgrade", PREVIOUS)
        with self.engine.connect() as connection:
            self.assertEqual(MigrationContext.configure(connection).get_current_heads(), (PREVIOUS,))
            self.assertEqual(schema_signature(connection), self.old_schema)
        self.migrate("upgrade", CURRENT)
        self.save()
        with self.assertRaisesRegex(ValueError, "历史"):
            self.migrate("downgrade", PREVIOUS)
        with self.engine.connect() as connection:
            self.assertEqual(MigrationContext.configure(connection).get_current_heads(), (CURRENT,))
        self.assertEqual(self.rows(), self.before)


if __name__ == "__main__":
    unittest.main()