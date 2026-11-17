import unittest
from html.parser import HTMLParser

from fastapi.testclient import TestClient

from main import app


class FormParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.fields = set()
        self.scripts = []
        self.elements = {}
        self.ids = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if "id" in values:
            self.ids.append(values["id"])
            self.elements[values["id"]] = {"tag": tag, **values}
        if tag in {"input", "select", "textarea"} and "name" in values:
            self.fields.add(values["name"])
        if tag == "script" and "src" in values:
            self.scripts.append(values["src"])


class PlanPageTests(unittest.TestCase):
    def test_navigation_form_and_script_are_connected(self):
        with TestClient(app) as client:
            self.assertIn('href="http://testserver/plans"', client.get("/").text)
            response = client.get("/plans")
            self.assertEqual(response.status_code, 200)
            self.assertIn("text/html", response.headers["content-type"])
            parser = FormParser()
            parser.feed(response.text)
            self.assertEqual(parser.fields, {
                "title", "service_category", "plan_kind", "requirements",
                "owner_name", "location_ids", "planned_start_at", "planned_end_at",
            })
            self.assertEqual(len(parser.scripts), 2)
            for source in parser.scripts:
                script = client.get(source)
                self.assertEqual(script.status_code, 200)
                self.assertIn("javascript", script.headers["content-type"])

    def test_list_precedes_collapsed_intake_and_preserves_business_fields(self):
        with TestClient(app) as client:
            html = client.get("/plans").text
            css = client.get("/static/plan-pages.css")
        parser = FormParser()
        parser.feed(html)
        self.assertEqual(len(parser.ids), len(set(parser.ids)))
        self.assertEqual(parser.elements["plan-intake-panel"]["tag"], "details")
        self.assertNotIn("open", parser.elements["plan-intake-panel"])
        self.assertLess(html.index('id="plan-records"'), html.index('id="plan-form"'))
        self.assertEqual(parser.elements["plan-open-form"]["type"], "button")
        self.assertEqual(parser.elements["plan-open-form"]["aria-controls"], "plan-intake-panel")
        self.assertIn('name="location_ids" multiple', html)
        self.assertIn('href="http://testserver/static/workbench.css', html)
        self.assertEqual(css.status_code, 200)
        for label in ("计划覆盖地点", "现场检查", "记录核对", "不会自动重复生成任务"):
            self.assertIn(label, html)


if __name__ == "__main__":
    unittest.main()