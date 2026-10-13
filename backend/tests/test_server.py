"""Read-only web API tests, without external access."""
import http.client
import json
from threading import Thread
from http.server import ThreadingHTTPServer
import unittest

from backend.server import Handler, report, service_status


class ReadOnlyServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def request(self, path, method="GET"):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        c.request(method, path)
        resp = c.getresponse()
        result = resp.status, resp.read(), resp.headers
        c.close()
        return result

    def test_status_explicitly_not_realtime(self):
        code, body, headers = self.request("/api/status")
        status = json.loads(body)
        self.assertEqual(code, 200)
        self.assertEqual(status["instrument_count"], 68)
        self.assertFalse(status["live_data_connected"])
        self.assertFalse(status["auto_refresh_enabled"])
        self.assertEqual(status["snapshot_latest_nav_date"], "2026-09-01")
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), "https://fullseye01-a11y.github.io")

    def test_coverage_contains_historical_records(self):
        code, body, _ = self.request("/api/coverage")
        self.assertEqual(code, 200)
        self.assertEqual(len(json.loads(body)["records"]), 68)

    def test_no_public_write_endpoint(self):
        code, body, _ = self.request("/api/import", "POST")
        self.assertEqual(code, 405)
        self.assertIn(b"read-only", body)

    def test_healthz_and_homepage(self):
        for path in ["/", "/healthz"]:
            code, body, _ = self.request(path)
            self.assertEqual(code, 200)
            self.assertTrue(body)

    def test_unavailable_route(self):
        code, body, _ = self.request("/api/secrets")
        self.assertEqual(code, 404)


if __name__ == "__main__":
    unittest.main()
