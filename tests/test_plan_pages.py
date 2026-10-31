import unittest
from html.parser import HTMLParser

from fastapi.testclient import TestClient

from main import app


class FormParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.fields = set()
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
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


if __name__ == "__main__":
    unittest.main()