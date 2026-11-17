import unittest
from html.parser import HTMLParser
from unittest.mock import patch

from fastapi.testclient import TestClient

from database import engine
from main import app


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = []
        self.links = []
        self.parents = []
        self.hidden_ancestors = {}
        self.fields = set()
        self.elements = {}

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if "id" in values:
            self.hidden_ancestors[values["id"]] = tuple(self.parents)
            self.elements[values["id"]] = values
        if tag in {"input", "select", "textarea"} and "name" in values:
            self.fields.add(values["name"])
        if tag in {"section", "div", "details"}:
            self.parents.append((tag, values.get("id"), "hidden" in values))
        if tag == "script" and "src" in values:
            self.scripts.append(values["src"])
        if tag == "a" and "href" in values:
            self.links.append(values["href"])

    def handle_endtag(self, tag):
        if tag in {"section", "div", "details"} and self.parents:
            self.parents.pop()


class ExecutionPageTests(unittest.TestCase):
    def test_page_navigation_and_script_are_connected_without_database_access(self):
        with patch.object(engine, "connect", side_effect=AssertionError("页面壳不应连接数据库")):
            with TestClient(app) as client:
                response = client.get("/executions?plan_id=23832b15-693f-4ff8-a563-4a60fa7d42be")
                self.assertEqual(response.status_code, 200)
                self.assertIn("text/html", response.headers["content-type"])
                self.assertIn("工作执行记录", response.text)
                parser = PageParser()
                parser.feed(response.text)
                self.assertIn("http://testserver/plans", parser.links)
                self.assertTrue(parser.scripts)
                for source in parser.scripts:
                    script = client.get(source)
                    self.assertEqual(script.status_code, 200)
                    self.assertIn("javascript", script.headers["content-type"])
                self.assertEqual(client.get("/executions").status_code, 200)

    def test_collapsible_form_keeps_save_feedback_outside_and_preserves_fields(self):
        with TestClient(app) as client:
            page = client.get("/executions", params={"plan_id": "23832b15-693f-4ff8-a563-4a60fa7d42be"})
        parser = PageParser()
        parser.feed(page.text)
        self.assertEqual(parser.fields, {
            "executor_name", "description", "completion_claim", "scope_mode", "location_ids",
            "claimed_started_at", "claimed_finished_at",
        })
        self.assertEqual(parser.elements["execution-send"].get("form"), "execution-form")
        form_parents = parser.hidden_ancestors["execution-form"]
        self.assertIn(("section", "execution-intake-panel", True), form_parents)
        for element in ("execution-submit-message", "execution-retry-note", "execution-receipt", "execution-view-saved"):
            with self.subTest(element=element):
                self.assertFalse(any(parent[1] == "execution-intake-panel" for parent in parser.hidden_ancestors[element]))
        self.assertIn("execution-open-form", parser.hidden_ancestors)
        self.assertIn("execution-return-report", parser.hidden_ancestors)


if __name__ == "__main__":
    unittest.main()
