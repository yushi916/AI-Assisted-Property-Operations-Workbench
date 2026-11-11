import unittest

from fastapi.testclient import TestClient

from main import app


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
                client.get("/static/location-picker.js").status_code,
                200,
            )