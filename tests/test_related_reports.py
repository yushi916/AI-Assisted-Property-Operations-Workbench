import unittest
from datetime import timedelta
from uuid import uuid4

from models import ServiceRequest
from related_reports import RelatedReportLimitExceeded, RelatedReportPolicy, discover_related_reports
from tests.automatic_evidence_fixture import AutomaticEvidenceFixture, at


class RelatedReportTests(AutomaticEvidenceFixture, unittest.TestCase):
    def read(self, policy=None):
        with self.sessions.begin() as db:
            return discover_related_reports(db, self.report_id, policy)

    def test_no_plans_finds_same_building_reports_with_different_original_symptoms(self):
        whole = self.report(point="ROOM-301", observed="09:50", description="厨房和卫生间都没水了。")
        tap = self.report(point="CORRIDOR", observed="10:10", description="走廊清洁龙头出水很小，其他地方没试。")
        before = self.snapshot()
        result = self.read()
        self.assertEqual({row.report.id for row in result.candidates}, {whole, tap})
        self.assertEqual(result.location_scope, "same_building")
        self.assertEqual({row.location_relation for row in result.candidates}, {"same_building"})
        self.assertEqual({row.report.description for row in result.candidates}, {"厨房和卫生间都没水了。", "走廊清洁龙头出水很小，其他地方没试。"})
        self.assertEqual(before, self.snapshot())

    def test_filters_other_building_category_and_time_without_name_matching(self):
        keep = self.report(point="ROOM-301")
        self.report(point="OTHER-302")
        self.report(point="ROOM-301", category="cleaning")
        self.report(point="ROOM-301", observed="06:00")
        self.assertEqual([row.report.id for row in self.read().candidates], [keep])

    def test_cleaning_uses_exact_point_not_entire_building(self):
        with self.sessions.begin() as db:
            row = db.get(ServiceRequest, self.report_id)
            row.service_category, row.location_id = "cleaning", "LOBBY"
        keep = self.report(point="LOBBY", category="cleaning")
        self.report(point="CORRIDOR", category="cleaning")
        result = self.read()
        self.assertEqual([row.report.id for row in result.candidates], [keep])
        self.assertEqual(result.candidates[0].location_relation, "same_location")

    def test_non_building_public_point_does_not_expand_across_community(self):
        with self.sessions.begin() as db:
            db.get(ServiceRequest, self.report_id).location_id = "COURTYARD"
        keep = self.report(point="COURTYARD")
        self.report(point="OTHER-302")
        self.assertEqual([row.report.id for row in self.read().candidates], [keep])

    def test_missing_candidate_observation_is_search_only_not_actual_time(self):
        missing = self.report(observed=None, received="10:15")
        actual_old = self.report(observed="06:00", received="10:15")
        rows = {row.report.id: row for row in self.read().candidates}
        self.assertIn(missing, rows)
        self.assertNotIn(actual_old, rows)
        self.assertIsNone(rows[missing].report.observed_at)
        self.assertEqual(rows[missing].time_basis, "received_at_search_only")
        self.assertEqual(rows[missing].observation_relation, "unknown")

    def test_missing_seed_observation_keeps_all_time_comparisons_unknown(self):
        with self.sessions.begin() as db:
            db.get(ServiceRequest, self.report_id).observed_at = None
        self.report(observed="10:00")
        result = self.read()
        self.assertEqual(result.time_basis, "received_at_search_only")
        self.assertEqual(result.candidates[0].observation_relation, "unknown")

    def test_unknown_seed_location_is_not_queried_and_not_empty(self):
        for location in (None, "UNKNOWN"):
            with self.subTest(location=location):
                with self.sessions.begin() as db:
                    db.get(ServiceRequest, self.report_id).location_id = location
                result = self.read()
                self.assertEqual(result.status, "not_queried")
                self.assertEqual(result.candidates, ())
                self.assertIsNone(result.building_id)

    def test_unknown_other_location_is_excluded_with_scope_limitation(self):
        self.report(point=None)
        self.report(point="UNKNOWN")
        result = self.read()
        self.assertEqual(result.status, "empty")
        self.assertTrue(any("未注明地点" in warning for warning in result.warnings))

    def test_window_boundaries_are_inclusive_and_order_is_stable(self):
        before = self.report(observed="08:00")
        after = self.report(observed="12:00")
        self.report(observed="12:01")
        result = self.read()
        self.assertEqual([row.report.id for row in result.candidates], [before, after])
        self.assertEqual([row.observation_relation for row in result.candidates], ["before", "after"])
        self.assertEqual(result.window_start, at("08:00"))
        self.assertEqual(result.window_end, at("12:00"))
        self.assertEqual(result, self.read())

    def test_over_limit_is_explicit_and_does_not_return_first_page(self):
        for _ in range(3):
            self.report()
        before = self.snapshot()
        with self.assertRaises(RelatedReportLimitExceeded):
            self.read(RelatedReportPolicy(max_reports=2))
        self.assertEqual(before, self.snapshot())

    def test_direct_query_reads_more_than_a_ui_page(self):
        for _ in range(52):
            self.report()
        result = self.read(RelatedReportPolicy(max_reports=60))
        self.assertEqual(len(result.candidates), 52)
        self.assertNotIn(self.report_id, {row.report.id for row in result.candidates})

    def test_ledger_paths_and_versions_keep_rooms_distinct(self):
        result = self.read()
        self.assertEqual(result.locations["ROOM-302"].display_path, "清和家园 / A区 / 1栋 / 1单元 / 3层 / 302室")
        self.assertEqual(result.locations["ROOM-302"].version, 1)
        self.assertNotEqual(result.locations["ROOM-302"].display_path, result.locations["OTHER-302"].display_path)

    def test_invalid_policy_transaction_missing_report_and_pending_write_are_rejected(self):
        for kwargs in ({"max_reports": 0}, {"max_reports": True}, {"lookback": timedelta(seconds=-1)}):
            with self.assertRaises(ValueError):
                RelatedReportPolicy(**kwargs)
        with self.sessions() as db:
            with self.assertRaises(ValueError):
                discover_related_reports(db, self.report_id)
        with self.sessions.begin() as db:
            with self.assertRaises(LookupError):
                discover_related_reports(db, uuid4())
        before = self.snapshot()
        with self.sessions.begin() as db:
            row = db.get(ServiceRequest, self.report_id)
            row.description = "尚未提交的改动"
            with self.assertRaises(ValueError):
                discover_related_reports(db, self.report_id)
            db.rollback()
        self.assertEqual(before, self.snapshot())
