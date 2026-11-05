import unittest
from html.parser import HTMLParser
from unittest.mock import patch

from fastapi.testclient import TestClient

from database import engine
from main import app


PLAN_ID = "23832b15-693f-4ff8-a563-4a60fa7d42be"


class EvidencePageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements = {}
        self.links = []
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if "id" in values:
            self.elements[values["id"]] = values
        if tag == "a":
            self.links.append(values.get("href"))
        if tag == "script" and "src" in values:
            self.scripts.append(values["src"])

class PlanEvidencePageTests(unittest.TestCase):
    def test_page_preserves_plan_context_without_reading_database(self):
        with patch.object(engine, "connect", side_effect=AssertionError("页面不能读取数据库")):
            with TestClient(app) as client:
                response = client.get("/plan-evidence", params={"plan_id": PLAN_ID})
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])
        parser = EvidencePageParser()
        parser.feed(response.text)
        self.assertEqual(parser.elements["plan-evidence"]["data-plan-id"], PLAN_ID)
        self.assertIn("http://testserver/plans", parser.links)
        self.assertIn(f"http://testserver/executions?plan_id={PLAN_ID}", parser.links)
        self.assertIn(f"http://testserver/inspections?plan_id={PLAN_ID}", parser.links)
        self.assertIn("hidden", parser.elements["evidence-result"])
        self.assertIn("尚未查询", response.text)

    def test_missing_or_invalid_plan_id_is_rejected_before_database_access(self):
        with patch.object(engine, "connect", side_effect=AssertionError("无效参数不能读库")):
            with TestClient(app) as client:
                for params in ({}, {"plan_id": "not-a-uuid"}):
                    with self.subTest(params=params):
                        self.assertEqual(client.get("/plan-evidence", params=params).status_code, 422)

    def test_page_route_does_not_replace_evidence_api(self):
        with TestClient(app) as client:
            paths = client.get("/openapi.json").json()["paths"]
        self.assertNotIn("/plan-evidence", paths)
        self.assertIn("/api/plans/{plan_id}/evidence-check", paths)
    def test_query_script_is_connected_and_can_be_served(self):
        with TestClient(app) as client:
            parser = EvidencePageParser()
            parser.feed(client.get("/plan-evidence", params={"plan_id": PLAN_ID}).text)
            self.assertEqual(parser.scripts, ["http://testserver/static/plan-evidence.js"])
            response = client.get(parser.scripts[0])
            self.assertEqual(response.status_code, 200)
            self.assertIn("javascript", response.headers["content-type"])

if __name__ == "__main__":
    unittest.main()