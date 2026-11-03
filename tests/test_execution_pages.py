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

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "script" and "src" in values:
            self.scripts.append(values["src"])
        if tag == "a" and "href" in values:
            self.links.append(values["href"])


class ExecutionPageTests(unittest.TestCase):
    def test_page_navigation_and_script_are_connected_without_database_access(self):
        with patch.object(engine, "connect", side_effect=AssertionError("页面壳不应连接数据库")):
            with TestClient(app) as client:
                response = client.get("/executions?plan_id=23832b15-693f-4ff8-a563-4a60fa7d42be")
                self.assertEqual(response.status_code, 200)
                self.assertIn("text/html", response.headers["content-type"])
                self.assertIn("计划执行记录", response.text)
                parser = PageParser()
                parser.feed(response.text)
                self.assertIn("http://testserver/plans", parser.links)
                self.assertTrue(parser.scripts)
                for source in parser.scripts:
                    script = client.get(source)
                    self.assertEqual(script.status_code, 200)
                    self.assertIn("javascript", script.headers["content-type"])
                self.assertEqual(client.get("/executions").status_code, 200)


if __name__ == "__main__":
    unittest.main()