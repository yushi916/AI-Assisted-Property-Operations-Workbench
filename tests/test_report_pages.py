import unittest

from fastapi.testclient import TestClient

from main import app
from html.parser import HTMLParser


class PageContract(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.fields = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if "id" in values:
            self.ids.append(values["id"])
        if tag in {"input", "select", "textarea"} and "name" in values:
            self.fields.append(values["name"])


class TestReportPages(unittest.TestCase):
    def test_intake_and_report_page_links_and_scripts_are_connected(self):
        with TestClient(app) as client:
            home = client.get("/")
            self.assertEqual(home.status_code, 200)
            self.assertIn('href="http://testserver/reports"', home.text)

            page = client.get("/reports")
            self.assertEqual(page.status_code, 200)
            self.assertIn("text/html", page.headers["content-type"])
            self.assertIn('href="http://testserver/"', page.text)

            for script in ("reports.js", "report-list.js"):
                with self.subTest(script=script):
                    owner = home if script == "reports.js" else page
                    self.assertIn("/static/" + script, owner.text)
                    response = client.get("/static/" + script)
                    self.assertEqual(response.status_code, 200)
                    self.assertIn("javascript", response.headers["content-type"])

    def test_location_picker_loads_before_intake_and_has_a_mount_point(self):
        with TestClient(app) as client:
            page = client.get("/")
            self.assertIn('id="location-picker"', page.text)
            self.assertLess(
                page.text.index("/static/location-picker.js"),
                page.text.index("/static/reports.js"),
            )
            self.assertEqual(
                client.get("/static/location-picker.js").status_code, 200,
            )

    def test_entry_contract_keeps_business_fields_and_receipt_link_hidden_until_saved(self):
        with TestClient(app) as client:
            intake = client.get("/").text
            listing = client.get("/reports").text
            parser = PageContract()
            parser.feed(intake)
            self.assertEqual(set(parser.fields), {"service_category", "location_id", "description", "observed_at", "risk_status"})
            self.assertEqual(len(parser.fields), 5)
            self.assertEqual(len(parser.ids), len(set(parser.ids)))
            self.assertIn('id="view-saved-report" class="button-link" hidden', intake)
            for page in (intake, listing):
                self.assertIn('/static/workbench.css', page)
                self.assertIn('/static/report-pages.css', page)
                self.assertIn('id="main-content"', page)
            parser = PageContract()
            parser.feed(listing)
            self.assertEqual(len(parser.ids), len(set(parser.ids)))
