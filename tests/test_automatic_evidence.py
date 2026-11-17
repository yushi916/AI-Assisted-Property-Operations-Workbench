import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import select

from automatic_evidence import AutomaticEvidenceRead, AutomaticEvidenceReadError, read_automatic_evidence
from models import InspectionRecord, ServiceRequest
from related_reports import RelatedReportPolicy
from report_candidates import CandidatePolicy, discover_report_candidates
from tests.automatic_evidence_fixture import AutomaticEvidenceFixture, at


class AutomaticEvidenceTests(AutomaticEvidenceFixture, unittest.TestCase):
    def read(self, **kwargs):
        with self.sessions.begin() as db:
            return read_automatic_evidence(db, self.report_id, **kwargs)

    def test_no_plan_still_finds_water_reports_without_manual_links_or_writes(self):
        near = self.report(point="ROOM-301", observed="09:55", description="家里几个龙头都不出水了。")
        self.report(point="OTHER-302", description="厨房没水。")
        before = self.snapshot()
        result = self.read()
        self.assertEqual([row.report.id for row in result.related_reports], [near])
        self.assertEqual(result.plans, [])
        self.assertEqual(result.manual_links, [])
        self.assertEqual(before, self.snapshot())
        statuses = {row.source: row.status for row in result.queries}
        self.assertEqual(statuses["related_reports"], "found")
        self.assertEqual(statuses["plans"], "empty")
        self.assertEqual(statuses["plan_records"], "not_queried")
        self.assertEqual(statuses["assets"], "unsupported")
        self.assertTrue(any("没有开展工作" in warning for warning in result.warnings))

    def test_empty_supported_sources_are_distinct_from_unsupported(self):
        result = self.read()
        by_source = {row.source: row for row in result.queries}
        self.assertEqual(by_source["related_reports"].status, "empty")
        self.assertEqual(by_source["related_reports"].count, 0)
        self.assertEqual(by_source["work_orders"].status, "unsupported")
        self.assertIsNone(by_source["work_orders"].count)
        self.assertTrue(result.report.description)

    def test_cleaning_keeps_floor_and_glass_plan_originals_for_semantic_review(self):
        with self.sessions.begin() as db:
            report = db.get(ServiceRequest, self.report_id)
            report.service_category, report.location_id = "cleaning", "LOBBY"
            report.description = "信箱旁边又有几个外卖袋，地上还有黏脚的饮料印子，麻烦清理。"
        floor = self.plan(point="LOBBY", category="cleaning", title="大堂地面清理，清除垃圾和饮料污渍")
        glass = self.plan(point="LOBBY", category="cleaning", title="擦拭大堂玻璃及门框")
        execution = self.execution(floor, point="LOBBY", finished="09:00", description="信箱旁的垃圾和污渍已清理，地面已拖干。")
        inspection = self.inspection(floor, point="LOBBY", observed="09:10", finding="no_issue_observed", description="查看了信箱旁地面，没有看见垃圾和水渍。")
        self.plan(point="COURTYARD", category="cleaning", title="中庭落叶清理")
        self.plan(point="LOBBY", category="water_supply")
        result = self.read()
        self.assertEqual({row.plan.id for row in result.plans}, {floor, glass})
        item = next(row for row in result.plans if row.plan.id == floor)
        self.assertEqual(item.executions[0].record.id, execution)
        self.assertEqual(item.inspections[0].record.id, inspection)
        self.assertEqual(item.executions[0].report_relation_to_completion, "after")
        self.assertEqual(item.inspections[0].observation_relation_to_report, "before")
        self.assertEqual(item.plan_checks.comparisons[0].relation, "after")
        self.assertTrue(all(row.manual_link_ids == [] for row in result.plans))

    def test_room_corridor_unknown_and_other_building_children_remain_separate(self):
        plan = self.plan()
        room = self.inspection(plan, observed="10:30")
        corridor = self.inspection(plan, point="CORRIDOR", finding="no_issue_observed", description="公共龙头出水正常。")
        unknown = self.inspection(plan, point=None)
        unknown_placeholder = self.inspection(plan, point="UNKNOWN")
        other = self.inspection(plan, point="OTHER-302")
        result = self.read().plans[0]
        rows = {row.record.id: row for row in result.inspections}
        self.assertEqual(rows[room].location_relation, "same_location")
        self.assertEqual(rows[room].observation_relation_to_report, "after")
        self.assertEqual(rows[corridor].location_relation, "other_location")
        self.assertEqual(rows[other].location_relation, "other_location")
        self.assertEqual(rows[unknown].location_relation, "location_unknown")
        self.assertEqual(rows[unknown_placeholder].location_relation, "location_unknown")
        self.assertTrue(all(rows[id_].observation_relation_to_report == "unknown" for id_ in (corridor, unknown, other)))
        self.assertEqual(rows[corridor].record.finding, "no_issue_observed")

    def test_historical_same_room_record_survives_current_plan_scope_mismatch(self):
        plan = self.plan(point="OTHER-302", old=True)
        execution = self.execution(plan)
        inspection = self.inspection(plan)
        result = self.read().plans[0]
        self.assertFalse(result.plan_location_matches)
        self.assertEqual(result.executions[0].record.id, execution)
        self.assertEqual(result.executions[0].report_relation_to_completion, "after")
        self.assertEqual(result.inspections[0].record.id, inspection)
        self.assertIn("execution_outside_scope", {row.code for row in result.plan_checks.findings})

    def test_unknown_times_never_use_recording_or_receipt_for_comparison(self):
        plan = self.plan()
        self.execution(plan, finished=None)
        self.inspection(plan, observed=None)
        result = self.read().plans[0]
        self.assertEqual(result.executions[0].report_relation_to_completion, "unknown")
        self.assertEqual(result.inspections[0].observation_relation_to_report, "unknown")
        with self.sessions.begin() as db:
            db.get(ServiceRequest, self.report_id).observed_at = None
        self.execution(plan)
        result = self.read().plans[0]
        self.assertTrue(all(row.report_relation_to_completion == "unknown" for row in result.executions))

    def test_structured_completion_with_conflicting_description_is_preserved(self):
        plan = self.plan()
        id_ = self.execution(plan, description="本次只检查了走廊，住户不在家，未进入302室。")
        result = self.read().plans[0].executions[0]
        self.assertEqual(result.record.id, id_)
        self.assertEqual(result.record.completion_claim, "completed")
        self.assertEqual(result.record.location_ids, ["ROOM-302"])
        self.assertIn("未进入302室", result.record.description)
        # Structured comparison is only a time relation; semantic contradiction is a later model acceptance.
        self.assertEqual(result.report_relation_to_completion, "after")

    def test_claimed_completion_and_later_issue_check_both_present(self):
        plan = self.plan()
        execution = self.execution(plan)
        inspection = self.inspection(plan)
        item = self.read().plans[0]
        self.assertEqual(item.plan_checks.comparisons[0].execution_id, execution)
        self.assertEqual(item.plan_checks.comparisons[0].inspection_id, inspection)
        self.assertEqual(item.plan_checks.comparisons[0].finding, "issue_observed")
        self.assertEqual(item.plan_checks.comparisons[0].relation, "after")
        self.assertFalse(hasattr(item, "resolved"))

    def test_manual_supplement_deduplicates_but_does_not_prove_other_category_matches(self):
        plan = self.plan()
        id_ = self.link(plan)
        other = self.plan(category="cleaning", point="CORRIDOR", old=True)
        self.link(other)
        self.execution(other)
        result = self.read()
        self.assertEqual(len(result.plans), 2)
        item = next(row for row in result.plans if row.plan.id == plan)
        self.assertTrue(item.automatic)
        self.assertEqual(item.manual_link_ids, [id_])
        self.assertEqual({row.plan_id for row in result.manual_links}, {plan, other})
        foreign = next(row for row in result.plans if row.plan.id == other)
        self.assertFalse(foreign.category_matches)
        self.assertEqual(foreign.executions[0].report_relation_to_completion, "unknown")

    def test_saved_snapshot_is_detached_and_new_check_can_read_new_room_evidence(self):
        plan = self.plan()
        self.inspection(plan, point="CORRIDOR", finding="no_issue_observed")
        first = self.read()
        new_id = self.inspection(plan, point="ROOM-302")
        second = self.read()
        self.assertNotIn(new_id, {row.record.id for row in first.plans[0].inspections})
        self.assertIn(new_id, {row.record.id for row in second.plans[0].inspections})
        self.assertTrue(any("本报事地点的明确现场检查" in warning for warning in first.plans[0].warnings))

    def test_complete_originals_ids_versions_people_and_paths_survive_json_roundtrip(self):
        near = self.report(point="ROOM-301", description="只有洗手盆没水，厨房还正常。")
        plan = self.plan()
        self.execution(plan)
        self.inspection(plan)
        result = self.read()
        reread = AutomaticEvidenceRead.model_validate_json(result.model_dump_json())
        self.assertEqual(result, reread)
        self.assertEqual(reread.related_reports[0].report.id, near)
        self.assertEqual(reread.related_reports[0].report.version, 1)
        self.assertEqual(reread.plans[0].plan.owner_name, "维修班王师傅")
        self.assertEqual(reread.plans[0].executions[0].record.executor_name, "维修班王师傅")
        self.assertEqual(reread.plans[0].inspections[0].record.inspector_name, "值班员陈女士")
        locations = {row.id: row for row in reread.locations}
        self.assertIn("1栋", locations["ROOM-302"].display_path)
        self.assertIn("BUILDING-1", locations)
        self.assertNotIn("OTHER-302", locations)
        query = next(row for row in reread.queries if row.source == "related_reports")
        self.assertEqual(query.scope["window_start"], "2026-11-11T00:00:00+00:00")
        self.assertIn(f"report:{near}", query.record_refs)

    def test_failure_is_not_empty_or_partial_success_and_keeps_trace(self):
        self.report(point="ROOM-301")
        self.plan()
        before = self.snapshot()
        with patch("automatic_evidence.read_plan_evidence", side_effect=RuntimeError("模拟读取失败")):
            with self.assertRaises(AutomaticEvidenceReadError) as caught:
                self.read()
        error = caught.exception
        self.assertEqual(error.source, "plan_records")
        self.assertEqual(error.code, "RuntimeError")
        self.assertEqual(error.queries[-2].status, "failed")
        self.assertTrue(any(row.source == "related_reports" and row.status == "found" for row in error.queries))
        self.assertEqual(before, self.snapshot())

    def test_related_report_query_failure_stops_before_plan_read(self):
        with patch("automatic_evidence.discover_related_reports", side_effect=RuntimeError("失败")), patch("automatic_evidence.discover_report_candidates") as plans:
            with self.assertRaises(AutomaticEvidenceReadError) as caught:
                self.read()
        self.assertEqual(caught.exception.source, "related_reports")
        self.assertTrue(any(row.source == "plans" and row.status == "not_queried" for row in caught.exception.queries))
        plans.assert_not_called()

    def test_missing_candidate_during_child_read_is_failure(self):
        self.plan()
        with patch("automatic_evidence.read_plan_evidence", return_value=None):
            with self.assertRaises(AutomaticEvidenceReadError) as caught:
                self.read()
        self.assertEqual(caught.exception.code, "LookupError")

    def test_report_and_plan_limits_stop_instead_of_partial_evidence(self):
        self.report()
        self.report()
        with self.assertRaises(AutomaticEvidenceReadError) as caught:
            self.read(report_policy=RelatedReportPolicy(max_reports=1))
        self.assertEqual(caught.exception.code, "RelatedReportLimitExceeded")
        for _ in range(2):
            self.plan()
        with self.assertRaises(AutomaticEvidenceReadError) as caught:
            self.read(plan_policy=CandidatePolicy(max_candidates=1))
        self.assertEqual(caught.exception.code, "CandidateLimitExceeded")

    def test_child_budget_counts_all_plans_and_does_not_silently_truncate(self):
        first, second = self.plan(), self.plan()
        self.execution(first)
        self.inspection(second)
        before = self.snapshot()
        with self.assertRaises(AutomaticEvidenceReadError) as caught:
            self.read(max_child_records=1)
        self.assertEqual(caught.exception.code, "EvidenceRecordLimitExceeded")
        self.assertEqual(len(self.read(max_child_records=2).plans), 2)
        self.assertEqual(before, self.snapshot())

    def test_all_children_are_read_without_the_ui_first_page_limit(self):
        plan = self.plan()
        for _ in range(52):
            self.inspection(plan)
        result = self.read()
        self.assertEqual(len(result.plans[0].inspections), 52)
        query = next(row for row in result.queries if row.source == "plan_records")
        self.assertEqual(query.count, 52)

    def test_single_sqlite_snapshot_under_concurrent_update_and_new_inspection(self):
        plan = self.plan()
        connection = self.engine.raw_connection()
        try:
            connection.execute("PRAGMA journal_mode=WAL").close()
        finally:
            connection.close()
        def change_after_related_read(*args, **kwargs):
            with self.sessions.begin() as writer:
                writer.get(ServiceRequest, self.report_id).description = "后来补充：厨房已恢复。"
                writer.add(InspectionRecord(
                    request_id=uuid4(), plan_id=plan, inspector_name="维修班王师傅",
                    description="后来检查室内已有水。", finding="no_issue_observed",
                    location_id="ROOM-302", observed_at=at("11:00"), recorded_at=at("11:05"),
                ))
            return discover_report_candidates(*args, **kwargs)
        with patch("automatic_evidence.discover_report_candidates", side_effect=change_after_related_read):
            first = self.read()
        self.assertIn("都没水", first.report.description)
        self.assertEqual(first.plans[0].inspections, [])
        second = self.read()
        self.assertEqual(len(second.plans[0].inspections), 1)
        self.assertIn("已恢复", second.report.description)

    def test_database_query_only_mode_and_caller_transaction_are_preserved(self):
        self.plan()
        before = self.snapshot()
        with self.sessions.begin() as db:
            db.connection().exec_driver_sql("PRAGMA query_only=ON")
            try:
                result = read_automatic_evidence(db, self.report_id)
                self.assertTrue(db.in_transaction())
                self.assertEqual(db.scalar(select(ServiceRequest.id).where(ServiceRequest.id == self.report_id)), self.report_id)
                self.assertEqual(len(result.plans), 1)
            finally:
                db.connection().exec_driver_sql("PRAGMA query_only=OFF")
        self.assertEqual(before, self.snapshot())

    def test_unknown_location_has_no_exact_scope_or_time_claim(self):
        plan = self.plan(point="UNKNOWN")
        self.execution(plan, point="UNKNOWN")
        with self.sessions.begin() as db:
            db.get(ServiceRequest, self.report_id).location_id = "UNKNOWN"
        result = self.read()
        query = next(row for row in result.queries if row.source == "related_reports")
        self.assertEqual(query.status, "not_queried")
        self.assertIsNone(query.count)
        self.assertIsNone(result.plans[0].plan_location_matches)
        self.assertEqual(result.plans[0].executions[0].location_relation, "report_location_unknown")
        self.assertEqual(result.plans[0].executions[0].report_relation_to_completion, "unknown")

    def test_invalid_call_and_missing_report_do_not_start_a_transaction_or_write(self):
        before = self.snapshot()
        with self.sessions() as db:
            with self.assertRaises(ValueError):
                read_automatic_evidence(db, self.report_id)
            self.assertFalse(db.in_transaction())
        with self.sessions.begin() as db:
            with self.assertRaises(AutomaticEvidenceReadError) as caught:
                read_automatic_evidence(db, uuid4())
            self.assertEqual(caught.exception.code, "LookupError")
        with self.assertRaises(ValueError):
            self.read(max_child_records=0)
        self.assertEqual(before, self.snapshot())
