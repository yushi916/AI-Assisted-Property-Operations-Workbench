import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy.orm import sessionmaker

from database import Base, create_db_engine, engine as project_engine
from models import (
    ExecutionLocation, ExecutionRecord, InspectionRecord, Location,
    PlanInstance, PlanLocation, RequestPlanLink, ServiceRequest,
)
from report_candidates import CandidateLimitExceeded, CandidatePolicy, discover_report_candidates


def at(value):
    return datetime.fromisoformat(value + "+08:00")


class ReportCandidateTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.engine = create_db_engine(Path(folder.name) / "candidates.db")
        self.addCleanup(self.engine.dispose)
        guard = patch.object(project_engine, "connect", side_effect=AssertionError("禁止连接项目业务库"))
        guard.start()
        self.addCleanup(guard.stop)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.sessions.begin() as db:
            for id_, kind, parent in [
                ("SITE", "community", None), ("A", "area", "SITE"),
                ("B", "area", "SITE"), ("LOBBY-A", "public_space", "A"),
                ("LOBBY-B", "public_space", "B"), ("BUILDING", "building", "A"),
                ("UNIT", "unit", "BUILDING"), ("FLOOR", "floor", "UNIT"),
                ("ROOM-302", "room", "FLOOR"), ("CORRIDOR", "public_space", "FLOOR"),
            ]:
                db.add(Location(id=id_, name=id_, kind=kind, parent_id=parent))
                db.flush()
            report = ServiceRequest(
                request_id=uuid4(), service_category="cleaning", source="web", risk_status="unknown",
                location_id="LOBBY-A", description="入口到电梯口有水印和泥脚印，踩着有点滑，麻烦清理一下。",
                observed_at=at("2026-11-11T10:20"), received_at=at("2026-11-12T08:00"),
            )
            db.add(report)
            db.flush()
            self.report_id = report.id

    def plan(self, title="大堂地面清洁", point="LOBBY-A", category="cleaning", old=False, start=None, end=None):
        with self.sessions.begin() as db:
            plan = PlanInstance(
                request_id=uuid4(), title=title, service_category=category, requirements=title,
                owner_name="林悦", plan_kind="routine",
                planned_start_at=start or at("2026-10-01T09:00" if old else "2026-11-11T09:00"),
                planned_end_at=end or at("2026-10-01T09:30" if old else "2026-11-11T09:30"),
                locations=[PlanLocation(location_id=point)],
            )
            db.add(plan)
            db.flush()
            return plan.id

    def execution(self, plan_id, point="LOBBY-A", unknown_time=False, old=False):
        with self.sessions.begin() as db:
            record = ExecutionRecord(
                request_id=uuid4(), plan_id=plan_id, executor_name="林悦", description="已拖洗入口地面。",
                completion_claim="unknown" if unknown_time else "completed",
                claimed_started_at=None,
                claimed_finished_at=None if unknown_time else at("2026-10-01T09:20" if old else "2026-11-11T09:20"),
                recorded_at=at("2026-11-11T09:40"),
                locations=[] if point is None else [ExecutionLocation(location_id=point)],
            )
            db.add(record)

    def inspection(self, plan_id, point="LOBBY-A", unknown_time=False):
        with self.sessions.begin() as db:
            db.add(InspectionRecord(
                request_id=uuid4(), plan_id=plan_id, inspector_name="周宁", description="查看了入口地面。",
                finding="unable_to_verify", location_id=point,
                observed_at=None if unknown_time else at("2026-11-11T09:35"),
                recorded_at=at("2026-11-11T09:40"),
            ))

    def link(self, plan_id, revoked=False):
        with self.sessions.begin() as db:
            item = RequestPlanLink(
                report_id=self.report_id, plan_id=plan_id, request_id=uuid4(),
                linked_by="周宁", reason="现场人员补充的工作安排", linked_at=at("2026-11-11T12:00"),
            )
            if revoked:
                item.status, item.version = "revoked", 2
                item.revoked_at, item.revoked_by, item.revoke_reason = at("2026-11-11T13:00"), "周宁", "先移除再核实"
                item.revocation_request_id = uuid4()
            db.add(item)
            db.flush()
            return item.id

    def read(self, policy=None):
        with self.sessions.begin() as db:
            return discover_report_candidates(db, self.report_id, policy)

    def snapshot(self):
        with self.engine.connect() as connection:
            return {name: connection.exec_driver_sql(f'SELECT * FROM "{name}" ORDER BY 1, 2').all()
                    for name in Base.metadata.tables}

    def test_unlinked_report_discovers_plan_without_writing_any_rows(self):
        plan = self.plan()
        before = self.snapshot()
        result = self.read()
        self.assertEqual([x.plan.id for x in result.candidates], [plan])
        self.assertTrue(result.candidates[0].automatic)
        self.assertEqual(result.candidates[0].manual_link_ids, ())
        self.assertEqual(result.report.description, "入口到电梯口有水印和泥脚印，踩着有点滑，麻烦清理一下。")
        self.assertEqual(before, self.snapshot())

    def test_same_place_and_category_distractor_is_retained_for_semantic_review(self):
        floor = self.plan()
        glass = self.plan(title="大堂玻璃及门框擦拭")
        self.assertEqual({x.plan.id for x in self.read().candidates}, {floor, glass})

    def test_other_explicit_location_and_category_are_not_auto_candidates(self):
        self.plan(point="LOBBY-B")
        self.plan(category="water_supply")
        self.assertEqual(self.read().candidates, ())

    def test_room_search_does_not_treat_corridor_as_same_location(self):
        with self.sessions.begin() as db:
            report = db.get(ServiceRequest, self.report_id)
            report.service_category, report.location_id = "water_supply", "ROOM-302"
        corridor = self.plan(point="CORRIDOR", category="water_supply")
        room = self.plan(point="ROOM-302", category="water_supply")
        self.inspection(corridor, point="CORRIDOR")
        self.assertEqual([x.plan.id for x in self.read().candidates], [room])

    def test_recent_execution_finds_old_plan_even_after_plan_scope_changes(self):
        plan = self.plan(old=True, point="LOBBY-B")
        self.execution(plan)
        result = self.read().candidates[0]
        self.assertEqual(result.plan.id, plan)
        self.assertFalse(result.plan_location_matches)
        self.assertIn("execution_record", result.reason_codes)

    def test_recent_inspection_also_finds_old_plan(self):
        plan = self.plan(old=True, point="LOBBY-B")
        self.inspection(plan)
        self.assertEqual(self.read().candidates[0].reason_codes, ("inspection_record",))

    def test_location_and_time_must_come_from_the_same_execution(self):
        plan = self.plan(old=True, point="LOBBY-B")
        self.execution(plan, point="LOBBY-A", old=True)
        self.execution(plan, point="LOBBY-B")
        self.assertEqual(self.read().candidates, ())

    def test_missing_observation_time_uses_received_time_only_as_search_reference(self):
        self.plan()
        with self.sessions.begin() as db:
            db.get(ServiceRequest, self.report_id).observed_at = None
        result = self.read()
        self.assertIsNone(result.report.observed_at)
        self.assertEqual(result.time_basis, "received_at_search_only")
        self.assertTrue(any("不能用于判断" in warning for warning in result.warnings))

    def test_missing_report_location_does_not_invent_an_exact_match(self):
        self.plan()
        other = self.plan(point="LOBBY-B")
        with self.sessions.begin() as db:
            db.get(ServiceRequest, self.report_id).location_id = None
        result = self.read()
        self.assertIn(other, {x.plan.id for x in result.candidates})
        self.assertTrue(all(x.plan_location_matches is None for x in result.candidates))
        self.assertTrue(all("report_location_unknown" in x.reason_codes for x in result.candidates))

    def test_unknown_record_time_is_explicitly_marked_as_recorded_time_search(self):
        plan = self.plan(old=True)
        self.execution(plan, unknown_time=True)
        result = self.read()
        self.assertIn("recorded_time_search_only", result.candidates[0].reason_codes)
        self.assertTrue(any("不能代替" in warning for warning in result.warnings))

    def test_time_window_boundary_and_policy_are_explicit(self):
        boundary = at("2026-11-04T10:20")
        keep = self.plan(start=boundary - timedelta(hours=1), end=boundary)
        self.plan(start=boundary - timedelta(hours=2), end=boundary - timedelta(seconds=1))
        result = self.read()
        self.assertEqual([x.plan.id for x in result.candidates], [keep])
        self.assertEqual(result.window_start, boundary)
        with self.assertRaises(ValueError):
            CandidatePolicy(lookback=timedelta(days=-1))
        with self.assertRaises(ValueError):
            CandidatePolicy(max_candidates=0)

    def test_missing_inspection_time_is_search_metadata_not_observation_time(self):
        plan = self.plan(old=True)
        self.inspection(plan, unknown_time=True)
        result = self.read()
        self.assertIn("inspection_record", result.candidates[0].reason_codes)
        self.assertIn("recorded_time_search_only", result.candidates[0].reason_codes)
        with self.sessions() as db:
            self.assertIsNone(db.query(InspectionRecord).one().observed_at)

    def test_pending_session_changes_are_rejected_instead_of_implicitly_flushed(self):
        before = self.snapshot()
        with self.sessions() as db:
            db.add(Location(id="UNSAVED", name="未保存地点", kind="community"))
            with self.assertRaises(ValueError):
                discover_report_candidates(db, self.report_id)
            self.assertEqual(len(db.new), 1)
        self.assertEqual(before, self.snapshot())

    def test_manual_plan_supplements_results_and_deduplicates_automatic_matches(self):
        automatic = self.plan()
        active = self.link(automatic)
        supplemental = self.plan(old=True, point="LOBBY-B", category="other")
        self.link(supplemental)
        result = {x.plan.id: x for x in self.read().candidates}
        self.assertEqual(set(result), {automatic, supplemental})
        self.assertEqual(result[automatic].manual_link_ids, (active,))
        self.assertFalse(result[supplemental].automatic)
        self.assertFalse(result[supplemental].category_matches)

    def test_revoked_link_is_not_an_active_supplement_or_a_silent_permanent_exclusion(self):
        automatic = self.plan()
        self.link(automatic, revoked=True)
        outside = self.plan(old=True, point="LOBBY-B")
        self.link(outside, revoked=True)
        result = self.read().candidates
        self.assertEqual([x.plan.id for x in result], [automatic])
        self.assertTrue(result[0].has_revoked_manual_link)
        self.assertEqual(result[0].manual_link_ids, ())

    def test_excess_candidates_fail_explicitly_without_truncating_or_writing(self):
        for _ in range(3):
            self.plan()
        before = self.snapshot()
        with self.assertRaises(CandidateLimitExceeded):
            self.read(CandidatePolicy(max_candidates=2))
        self.assertEqual(before, self.snapshot())

    def test_missing_report_and_missing_read_transaction_are_explicit_errors(self):
        with self.sessions() as db:
            with self.assertRaises(ValueError):
                discover_report_candidates(db, self.report_id)
        with self.sessions.begin() as db:
            with self.assertRaises(LookupError):
                discover_report_candidates(db, uuid4())

    def test_plans_after_observation_are_candidates_not_completed_work(self):
        plan = self.plan(start=at("2026-11-11T12:00"), end=at("2026-11-11T13:00"))
        result = self.read()
        self.assertEqual(result.candidates[0].plan.id, plan)
        self.assertGreater(result.candidates[0].plan.planned_start_at, result.report.observed_at)


if __name__ == "__main__":
    unittest.main()
