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

if __name__ == "__main__":
    unittest.main()