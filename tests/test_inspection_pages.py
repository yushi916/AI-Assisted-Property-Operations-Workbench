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
        self.ids = []
        self.fields = []
        self.open_panels = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if "id" in values:
            self.ids.append(values["id"])
        if tag in {"input", "select", "textarea"} and "name" in values:
            self.fields.append(values["name"])
        if tag == "details" and "open" in values:
            self.open_panels.append(values.get("id"))
        if tag == "script" and "src" in values:
            self.scripts.append(values["src"])
        if tag == "a" and "href" in values:
            self.links.append(values["href"])


class InspectionPageTests(unittest.TestCase):
    def test_page_navigation_and_script_are_connected_without_database_access(self):
        with patch.object(engine, "connect", side_effect=AssertionError("页面壳不应连接数据库")):
            with TestClient(app) as client:
                response = client.get("/inspections?plan_id=23832b15-693f-4ff8-a563-4a60fa7d42be")
                self.assertEqual(response.status_code, 200)
                self.assertIn("text/html", response.headers["content-type"])
                self.assertIn("现场检查记录", response.text)
                parser = PageParser()
                parser.feed(response.text)
                self.assertIn("http://testserver/plans", parser.links)
                self.assertTrue(parser.scripts)
                for source in parser.scripts:
                    script = client.get(source)
                    self.assertEqual(script.status_code, 200)
                    self.assertIn("javascript", script.headers["content-type"])
                self.assertEqual(client.get("/inspections").status_code, 200)

    def test_history_precedes_closed_intake_and_business_fields_are_preserved(self):
        with TestClient(app) as client:
            text = client.get("/inspections").text
        parser = PageParser()
        parser.feed(text)
        self.assertEqual(len(parser.ids), len(set(parser.ids)))
        self.assertEqual(set(parser.fields), {"inspector_name", "finding", "description", "scope_mode", "location_id", "observed_at"})
        self.assertEqual(len(parser.fields), 6)
        self.assertLess(text.index('id="inspection-rows"'), text.index('id="inspection-form"'))
        self.assertNotIn("inspection-intake-panel", parser.open_panels)
        self.assertIn('id="inspection-return-report" class="button-link" hidden', text)
        self.assertIn('/static/workbench.css', text)


if __name__ == "__main__":
    unittest.main()
