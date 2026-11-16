import unittest
from html.parser import HTMLParser
from unittest.mock import patch

from fastapi.testclient import TestClient

from database import engine
from main import app


REPORT_ID = "565eddc7-b40e-47b1-805f-cd89f8128380"


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements, self.scripts = {}, []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.elements[attrs["id"]] = attrs
        if tag == "script" and "src" in attrs:
            self.scripts.append(attrs)


class ReportEvidencePageTests(unittest.TestCase):
    def test_page_preserves_report_context_without_reading_database(self):
        with patch.object(engine, "connect", side_effect=AssertionError("页面外壳不能读取数据库")):
            with TestClient(app) as client:
                response = client.get("/report-evidence", params={"report_id": REPORT_ID})
        self.assertEqual(response.status_code, 200)
        parser = PageParser()
        parser.feed(response.text)
        self.assertEqual(parser.elements["report-evidence"]["data-report-id"], REPORT_ID)
        self.assertIn("hidden", parser.elements["report-evidence-result"])
        self.assertIn("尚未查询", response.text)
        self.assertIn("http://testserver/reports", response.text)

    def test_missing_or_invalid_report_id_is_rejected_before_database_access(self):
        with patch.object(engine, "connect", side_effect=AssertionError("非法编号不能读取数据库")):
            with TestClient(app) as client:
                for params in ({}, {"report_id": "bad"}):
                    with self.subTest(params=params):
                        self.assertEqual(client.get("/report-evidence", params=params).status_code, 422)

    def test_page_and_api_routes_are_distinct(self):
        with TestClient(app) as client:
            paths = client.get("/openapi.json").json()["paths"]
        self.assertNotIn("/report-evidence", paths)
        self.assertIn("/api/service-requests/{report_id}/evidence-check", paths)

    def test_script_is_connected_and_served(self):
        with TestClient(app) as client:
            response = client.get("/report-evidence", params={"report_id": REPORT_ID})
            parser = PageParser()
            parser.feed(response.text)
            self.assertEqual(len(parser.scripts), 2)
            self.assertIn("report-agent-runs.js", parser.scripts[1]["src"])
            self.assertIn("disabled", parser.elements["generate-agent-run"])
            self.assertIn("hidden", parser.elements["agent-run-result"])
            for entry in parser.scripts:
                self.assertEqual(entry["type"], "module")
                script = client.get(entry["src"])
                self.assertEqual(script.status_code, 200)
                self.assertIn("javascript", script.headers["content-type"])

    def test_action_form_and_receipt_controls_are_connected(self):
        with TestClient(app) as client:
            response = client.get("/report-evidence", params={"report_id": REPORT_ID})
        parser = PageParser()
        parser.feed(response.text)
        for name in ("link-action-form", "link-action-fields", "send-link-action",
                     "link-action-request-id", "link-action-receipt", "new-link-action"):
            self.assertIn(name, parser.elements)
        self.assertIn("disabled", parser.elements["link-action-fields"])
        self.assertIn("report-link-actions.js", parser.scripts[0]["src"])

    def test_shared_layout_serves_styles_and_keeps_the_reading_order(self):
        with TestClient(app) as client:
            page = client.get("/report-evidence", params={"report_id": REPORT_ID})
            self.assertEqual(page.status_code, 200)
            self.assertIn("/static/workbench.css?v=execution-layout-1", page.text)
            self.assertEqual(client.get("/static/workbench.css").status_code, 200)
        anchors = [
            'id="report-summary-title"', 'id="agent-runs-title"',
            'id="joint-plans-title"', 'id="link-action-title"',
        ]
        positions = [page.text.index(anchor) for anchor in anchors]
        self.assertEqual(positions, sorted(positions))
        self.assertIn('aria-label="工作导航"', page.text)


    def test_ai_entry_precedes_optional_manual_tools_and_all_five_results_are_present(self):
        with TestClient(app) as client:
            page = client.get("/report-evidence", params={"report_id": REPORT_ID})
        parser = PageParser()
        parser.feed(page.text)
        self.assertIn("无需先选择工作计划", page.text)
        self.assertIn("AI辅助核查", page.text)
        self.assertIn("可选", page.text)
        self.assertLess(page.text.index('id="agent-runs-title"'), page.text.index('id="manual-plan-tools"'))
        for id_ in ("agent-run-records", "agent-run-observations", "agent-run-assessments",
                    "agent-run-uncertainties", "agent-run-verification", "agent-run-scope",
                    "agent-run-history", "agent-query-details"):
            self.assertIn(id_, parser.elements)
        self.assertNotIn("open", parser.elements["manual-plan-tools"])
        self.assertIn("未找到记录不等于没有开展工作", page.text)

    def test_ai_renderer_and_scoped_styles_are_served_with_current_script_version(self):
        with TestClient(app) as client:
            page = client.get("/report-evidence", params={"report_id": REPORT_ID})
            self.assertIn("report-agent-runs.js?v=agent-readability-1", page.text)
            self.assertIn("agent-check.css?v=agent-readability-1", page.text)
            for path in ("/static/agent-check.css", "/static/agent-run-view.js"):
                self.assertEqual(client.get(path).status_code, 200)
            script = client.get("/static/report-agent-runs.js").text
        self.assertNotIn("watchEvidence", script)
        self.assertNotIn("evidenceReady", script)
        self.assertIn("agent-run-view.js?v=agent-readability-1", script)

    def test_analysis_precedes_originals_and_supplements_are_collapsed_without_hiding_failure_controls(self):
        with TestClient(app) as client:
            page = client.get("/report-evidence", params={"report_id": REPORT_ID})
        parser = PageParser()
        parser.feed(page.text)
        ids = ["agent-run-summary", "agent-assessments-title", "agent-uncertainties-title",
               "agent-verification-title", "agent-original-details", "agent-fact-details",
               "agent-query-details", "agent-runtime-details"]
        positions = [page.text.index(f'id="{name}"') for name in ids]
        self.assertEqual(positions, sorted(positions))
        for name in ("agent-original-details", "agent-fact-details", "agent-query-details", "agent-runtime-details"):
            self.assertNotIn("open", parser.elements[name])
        self.assertIn("hidden", parser.elements["agent-fact-details"])
        self.assertNotIn("hidden", parser.elements["agent-run-message"])
        self.assertIn('id="agent-run-meta"', page.text.split('id="agent-runtime-details"', 1)[1].split('</details>', 1)[0])
        self.assertIn('id="agent-run-records"', page.text.split('id="agent-original-details"', 1)[1].split('</details>', 1)[0])
        for text in ("本次核查依据", "事实摘要", "初步判断", "待核实信息", "建议核查事项", "运行详情"):
            self.assertIn(text, page.text)
        self.assertEqual(page.text.count("不会自动建立关联、结案或发布通知"), 1)
        self.assertEqual(page.text.count("尚不支持正式审核"), 1)

    def test_optional_manual_form_keeps_one_copy_of_each_control(self):
        with TestClient(app) as client:
            page = client.get("/report-evidence", params={"report_id": REPORT_ID})
        for id_ in ("report-evidence-result", "link-action-form", "generate-agent-run", "joint-plans", "agent-run-result"):
            self.assertEqual(page.text.count(f'id="{id_}"'), 1)
        self.assertIn("AI核查无需先操作这里", page.text)


if __name__ == "__main__":
    unittest.main()
