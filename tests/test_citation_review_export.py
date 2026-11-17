"""Local review alignment and preservation, not automated semantic grading."""

import copy
import io
import json
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from evaluate_water_faithfulness import citation_review_html, digest, export_review, review_template


class CitationReviewExportTests(unittest.TestCase):
    def bundle(self):
        records = {"report:root": {"description": "厨房冷水没有水。", "observed_at": None},
                   "execution:other": {"description": "本次未入户。", "location_ids": ["CORRIDOR"]},
                   "search:assets": {"status": "unsupported", "count": None}}
        input_ = {"records": records}
        row = {"text": "走廊已检查，但室内仍待核实。", "evidence_refs": ["report:root"]}
        suggestion = {"observations": [copy.deepcopy(row)], "candidate_assessments": [copy.deepcopy(row)],
                      "uncertainties": [copy.deepcopy(row)], "verification_items": ["联系住户核查厨房冷水。"]}
        return {"input": input_, "input_sha256": digest(input_), "baseline_run_id": "frozen-r1",
                "model_name": "test-model", "runs": [{"run": number, "structural_checks": "passed",
                "semantic_review": {"status": "not_reviewed"}, "suggestion": copy.deepcopy(suggestion)} for number in [1, 2]]}

    def test_every_row_citation_and_other_record_are_available_without_auto_adding_refs(self):
        bundle = self.bundle()
        before = copy.deepcopy(bundle)
        review = review_template(bundle)
        html = citation_review_html(bundle, review)
        self.assertEqual(len(review["entries"]), 8)
        self.assertEqual(html.count('data-review-row="'), 8)
        self.assertEqual(html.count('<details id="record-'), 3)
        self.assertIn("本次未入户。", html)
        self.assertIn("observed_at", html)
        self.assertIn("null", html)
        self.assertIn("unsupported", html)
        self.assertTrue(all(row["evidence_refs"] == ([] if row["section"] == "verification_items" else ["report:root"])
                            for row in review["entries"]))
        self.assertTrue(all(row["fact_coverage"] == "not_reviewed" for row in review["entries"]))
        self.assertEqual(bundle, before)

    def test_support_and_completeness_remain_separate_manual_decisions(self):
        bundle = self.bundle()
        review = review_template(bundle)
        row = review["entries"][0]
        row.update(cited_source_support="supported", fact_coverage="incomplete",
                   other_records_to_check=["execution:other"], note="报事支持部分内容，执行事实漏引。")
        row["cited_sources"][0]["support"] = "supported"
        html = citation_review_html(bundle, review)
        self.assertIn("支持对应内容", html)
        self.assertIn("正文仍有事实缺少充分引用", html)
        self.assertIn("未加入原引用", html)
        self.assertEqual(row["evidence_refs"], ["report:root"])
        self.assertEqual(bundle["runs"][0]["semantic_review"]["status"], "not_reviewed")

    def test_missing_ref_is_explicit_and_never_silently_skipped_or_repaired(self):
        bundle = self.bundle()
        bundle["runs"][0]["suggestion"]["observations"][0]["evidence_refs"] = ["execution:missing"]
        html = citation_review_html(bundle)
        self.assertIn("execution:missing：本快照不存在，未自动修补", html)
        self.assertNotIn("execution:missing", bundle["input"]["records"])

    def test_annotations_cannot_be_applied_to_changed_text_refs_or_different_outputs(self):
        for mutation in ["text", "refs", "omitted_entry", "different_output", "structural_status"]:
            bundle = self.bundle()
            review = review_template(bundle)
            if mutation == "text": review["entries"][0]["text"] = "改过正文"
            if mutation == "refs": review["entries"][0]["evidence_refs"].append("execution:other")
            if mutation == "omitted_entry": review["entries"].pop()
            if mutation == "different_output": review["replay_sha256"] = "another-output"
            if mutation == "structural_status": review["run_reviews"][0]["structural_checks"] = "failed"
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                citation_review_html(bundle, review)

    def test_input_hash_mismatch_rejects_export(self):
        bundle = self.bundle()
        bundle["input"]["records"]["report:root"]["description"] = "changed"
        with self.assertRaisesRegex(ValueError, "摘要不一致"):
            citation_review_html(bundle)

    def test_business_text_is_escaped_including_unquoted_model_strings(self):
        bundle = self.bundle()
        hostile = '</script><script>alert("record")</script><img src=x onerror=alert(1)>'
        bundle["input"]["records"]["report:root"]["description"] = hostile
        bundle["input_sha256"] = digest(bundle["input"])
        bundle["runs"][0]["suggestion"]["observations"][0]["text"] = hostile
        html = citation_review_html(bundle)
        self.assertNotIn(hostile, html)
        self.assertEqual(html.count("<script>"), 1)
        self.assertIn("&lt;img", html)

    def test_failed_run_retains_raw_output_and_failure_details_without_invented_claims(self):
        bundle = self.bundle()
        bundle["runs"][0] = {"run": 1, "structural_checks": "failed", "validation_error": "EvidenceReferenceError",
                             "raw_output": "the original invalid output", "semantic_review": {"status": "not_reviewed"}}
        html = citation_review_html(bundle)
        self.assertIn("EvidenceReferenceError", html)
        self.assertIn("the original invalid output", html)
        self.assertEqual(html.count('data-review-row="'), 4)

    def test_command_exports_new_sidecars_without_key_network_model_or_overwriting_sources(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "saved.json"
            original = json.dumps(self.bundle(), ensure_ascii=False).encode("utf-8")
            path.write_bytes(original)
            args = Namespace(input=path, annotations=None, output_dir=Path(folder))
            with (patch.dict("os.environ", {"GEMINI_API_KEY": ""}),
                  patch("evaluate_water_faithfulness.GeminiEvidenceModel") as model,
                  patch("evaluate_water_faithfulness.build_opener") as http,
                  redirect_stdout(io.StringIO())):
                export_review(args)
                export_review(args)
            model.assert_not_called()
            http.assert_not_called()
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(len(list(Path(folder).glob("citation-review-*.html"))), 2)
            annotations = list(Path(folder).glob("citation-review-*.json"))
            self.assertEqual(len(annotations), 2)
            self.assertTrue(all(row["cited_source_support"] == "not_reviewed" for row in
                                json.loads(annotations[0].read_text(encoding="utf-8"))["entries"]))
