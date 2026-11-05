import unittest
from datetime import UTC, datetime
from uuid import UUID

from evidence_rules import check_plan_evidence
from execution_schemas import ExecutionRead
from inspection_schemas import InspectionRead
from plan_schemas import PlanRead


def at(value):
    return datetime.fromisoformat("2026-11-05T" + value + "+08:00")


class EvidenceRuleTests(unittest.TestCase):
    def setUp(self):
        self.plan = PlanRead(
            id=UUID(int=1), request_id=UUID(int=101), title="合成A、B点位计划",
            service_category="cleaning", requirements="分别核对两个点位",
            owner_name="合成负责人", location_ids=["LOC-A", "LOC-B"],
            planned_start_at=at("09:00"), planned_end_at=at("09:30"),
            created_at=at("08:00"), version=1,
        )

    def execution(self, key=2, **changes):
        values = dict(
            id=UUID(int=key), request_id=UUID(int=key + 100), plan_id=self.plan.id,
            executor_name="合成执行人", description="合成执行反馈",
            completion_claim="completed", location_ids=["LOC-A"],
            claimed_started_at=None, claimed_finished_at=at("09:20"),
            recorded_at=at("18:00"), version=1,
        )
        values.update(changes)
        return ExecutionRead(**values)

    def inspection(self, key=3, **changes):
        values = dict(
            id=UUID(int=key), request_id=UUID(int=key + 100), plan_id=self.plan.id,
            inspector_name="合成巡查人", description="合成巡查观察",
            finding="no_issue_observed", location_id="LOC-A", observed_at=at("09:40"),
            recorded_at=at("18:00"), version=1,
        )
        values.update(changes)
        return InspectionRead(**values)

    def check(self, executions=(), inspections=(), checked_at=None):
        return check_plan_evidence(self.plan, executions, inspections, checked_at or at("20:00"))

    def test_due_empty_evidence_reports_each_point_separately(self):
        result = self.check()
        self.assertTrue(result.due)
        self.assertEqual(
            {(item.code, item.location_id) for item in result.findings},
            {(code, point) for code in ("missing_execution", "missing_inspection")
             for point in ("LOC-A", "LOC-B")},
        )
        self.assertEqual(len(result.evidence), 1)
        self.assertTrue(all(not item.execution_ids and not item.inspection_ids for item in result.coverage))

    def test_deadline_boundary_and_other_service_categories(self):
        for category in ("cleaning", "water_supply", "other"):
            self.plan = self.plan.model_copy(update={"service_category": category})
            for clock, due in ((at("09:29:59"), False), (at("09:30"), True)):
                with self.subTest(category=category, clock=clock):
                    result = self.check(checked_at=clock)
                    self.assertEqual(result.due, due)
                    self.assertEqual(len(result.findings), 4 if due else 0)

    def test_a_evidence_never_fills_b_and_partial_claim_remains_visible(self):
        for claim in ("completed", "partially_completed", "not_completed", "unknown"):
            with self.subTest(claim=claim):
                execution = self.execution(
                    completion_claim=claim,
                    claimed_finished_at=at("09:20") if claim == "completed" else None,
                )
                inspection = self.inspection()
                result = self.check([execution], [inspection])
                a, b = result.coverage
                self.assertEqual(a.execution_ids, [execution.id])
                self.assertEqual(a.inspection_ids, [inspection.id])
                self.assertEqual(b.execution_ids, [])
                self.assertEqual(b.inspection_ids, [])
                self.assertEqual(
                    {(item.code, item.location_id) for item in result.findings if item.code.startswith("missing_")},
                    {("missing_execution", "LOC-B"), ("missing_inspection", "LOC-B")},
                )
                self.assertEqual("completion_not_claimed" in {x.code for x in result.findings}, claim != "completed")

    def test_unknown_scope_and_times_are_retained_without_covering_any_point(self):
        execution = self.execution(location_ids=[], claimed_finished_at=None)
        inspection = self.inspection(location_id=None, observed_at=None, finding="issue_observed")
        result = self.check([execution], [inspection])
        self.assertEqual({item.id for item in result.evidence}, {self.plan.id, execution.id, inspection.id})
        self.assertTrue(all(not item.execution_ids and not item.inspection_ids for item in result.coverage))
        self.assertTrue({
            "unknown_execution_scope", "unknown_completion_time",
            "unknown_inspection_location", "unknown_inspection_time", "issue_observed",
        }.issubset({item.code for item in result.findings}))
        self.assertEqual(result.comparisons, [])
        self.assertEqual(execution.location_ids, [])
        self.assertIsNone(inspection.observed_at)

    def test_observed_time_controls_before_equal_after_not_recorded_time(self):
        execution = self.execution()
        for observed, expected in (("09:10", "before"), ("09:20", "same_time"), ("09:50", "after")):
            with self.subTest(observed=observed):
                inspection = self.inspection(observed_at=at(observed), finding="issue_observed")
                result = self.check([execution], [inspection])
                self.assertEqual(len(result.comparisons), 1)
                self.assertEqual(result.comparisons[0].relation, expected)
                self.assertEqual(result.comparisons[0].finding, "issue_observed")
                self.assertIn("issue_observed", {item.code for item in result.findings})

    def test_unknown_time_never_uses_recorded_time_to_order_events(self):
        for execution, inspection in (
            (self.execution(), self.inspection(observed_at=None)),
            (self.execution(claimed_finished_at=None), self.inspection()),
        ):
            with self.subTest(execution=execution, inspection=inspection):
                result = self.check([execution], [inspection])
                self.assertEqual(result.comparisons[0].relation, "unknown")

    def test_different_points_are_not_compared(self):
        result = self.check([self.execution()], [self.inspection(location_id="LOC-B", finding="issue_observed")])
        self.assertEqual(result.comparisons, [])
        self.assertEqual(result.coverage[0].inspection_ids, [])
        self.assertEqual(result.coverage[1].execution_ids, [])

    def test_multiple_completion_claims_are_all_compared(self):
        early = self.execution()
        later = self.execution(key=4, claimed_finished_at=at("09:45"))
        result = self.check([early, later], [self.inspection(observed_at=at("09:30"))])
        self.assertEqual(
            {(item.execution_id, item.relation) for item in result.comparisons},
            {(early.id, "after"), (later.id, "before")},
        )

    def test_completion_outside_plan_time_is_a_prompt_not_record_rejection(self):
        for finished, code in (
            ("08:59", "completion_before_plan_start"),
            ("09:30", None), ("09:31", "completion_after_deadline"),
        ):
            with self.subTest(finished=finished):
                result = self.check([self.execution(claimed_finished_at=at(finished))])
                time_codes = {item.code for item in result.findings if item.code.startswith("completion_")}
                self.assertEqual(time_codes, {code} if code else set())

    def test_outside_current_scope_is_flagged_and_evidence_is_preserved(self):
        execution = self.execution(location_ids=["LOC-C"])
        inspection = self.inspection(location_id="LOC-C")
        result = self.check([execution], [inspection])
        self.assertTrue({"execution_outside_scope", "inspection_outside_scope"}.issubset(
            {item.code for item in result.findings}
        ))
        self.assertEqual(len(result.evidence), 3)
        self.assertTrue(all(not item.execution_ids and not item.inspection_ids for item in result.coverage))

    def test_mixed_plans_duplicate_ids_and_naive_check_time_are_rejected(self):
        execution, inspection = self.execution(), self.inspection()
        cases = (
            ([self.execution(plan_id=UUID(int=999))], [], at("20:00"), "当前计划"),
            ([], [self.inspection(plan_id=UUID(int=999))], at("20:00"), "当前计划"),
            ([execution, execution], [], at("20:00"), "重复"),
            ([], [inspection, inspection], at("20:00"), "重复"),
            ([], [], at("20:00").replace(tzinfo=None), "时区"),
        )
        for executions, inspections, clock, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValueError, message):
                    self.check(executions, inspections, clock)

    def test_order_timezone_versions_and_input_values_are_preserved(self):
        executions = [self.execution(key=4), self.execution(version=3)]
        inspections = [self.inspection(key=5), self.inspection(version=2)]
        before = [item.model_dump() for item in [self.plan, *executions, *inspections]]
        first = self.check(executions, inspections, at("20:00"))
        second = self.check(list(reversed(executions)), list(reversed(inspections)), at("20:00").astimezone(UTC))
        self.assertEqual(first.model_dump(), second.model_dump())
        self.assertEqual(before, [item.model_dump() for item in [self.plan, *executions, *inspections]])
        refs = {(item.kind, item.id, item.version) for item in first.evidence}
        self.assertIn(("execution", UUID(int=2), 3), refs)
        self.assertIn(("inspection", UUID(int=3), 2), refs)


if __name__ == "__main__":
    unittest.main()