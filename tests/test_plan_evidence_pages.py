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
            self.assertEqual(parser.scripts, ["http://testserver/static/plan-evidence.js?v=material-types-1"])
            response = client.get(parser.scripts[0])
            self.assertEqual(response.status_code, 200)
            self.assertIn("javascript", response.headers["content-type"])

    def test_rule_check_is_distinct_from_observation_and_keeps_result_sections(self):
        with TestClient(app) as client:
            html = client.get("/plan-evidence", params={"plan_id": PLAN_ID}).text
        parser = EvidencePageParser()
        parser.feed(html)
        self.assertIn("计划执行与检查记录核对", html)
        self.assertIn("这是程序规则核对，不调用 AI", html)
        self.assertIn("未查到明确记录，不代表实际没有开展工作", html)
        self.assertIn('href="http://testserver/static/workbench.css', html)
        for element_id in ("evidence-coverage", "evidence-findings", "evidence-comparisons",
                           "evidence-summary", "evidence-references"):
            self.assertIn(element_id, parser.elements)
        self.assertNotIn("open", parser.elements["evidence-run-details"])
        self.assertNotIn("open", parser.elements["evidence-source-details"])
        self.assertIn("返回计划列表", html)
        self.assertIn("计划要求说明安排做什么", html)
        self.assertIn("本次填报工作地点、现场观察地点在各自记录中单独列出", html)
        self.assertIn("版本不一致时提示重新核对", html)
        self.assertNotIn("是否再次产生", html)
        self.assertLess(html.index('id="evidence-findings"'), html.index('id="evidence-coverage"'))

if __name__ == "__main__":
    unittest.main()