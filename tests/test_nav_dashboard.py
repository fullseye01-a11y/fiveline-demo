"""Static publication guards. No external data requests."""
import json
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]

class HTMLCheck(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.ids = set()
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.add(attrs["id"])
        if tag == "a" and attrs.get("href"):
            self.links.append(attrs["href"])

class DashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads((ROOT / "data/nav_freshness_68.json").read_text(encoding="utf-8"))
        cls.html = (ROOT / "nav_status.html").read_text(encoding="utf-8")
        cls.index = (ROOT / "index.html").read_text(encoding="utf-8")
        cls.parser = HTMLCheck()
        cls.parser.feed(cls.html)

    def test_snapshot_total_and_uniqueness(self):
        rows = self.manifest["instruments"]
        self.assertEqual(len(rows), 68)
        self.assertEqual(len({x["instrument_id"] for x in rows}), 68)
        self.assertFalse(self.manifest["is_realtime"])

    def test_nav_date_coherence(self):
        dates = [date.fromisoformat(x["nav_end"]) for x in self.manifest["instruments"]]
        self.assertEqual(max(dates).isoformat(), self.manifest["data_as_of_max"])
        for x in self.manifest["instruments"]:
            self.assertLessEqual(x["nav_start"], x["nav_end"])
            self.assertGreater(x["nav_rows"], 0)

    def test_distribution_summary_consistency(self):
        rows = self.manifest["instruments"]
        s = self.manifest["summary"]
        self.assertEqual(s["dividend_rows"], 2456)
        self.assertEqual(s["dividend_rows"], sum(x["dividend_rows"] for x in rows))
        self.assertEqual(s["dividend_early"], 1114)
        self.assertEqual(s["dividend_early"], sum(x["dividends_before_nav_start"] for x in rows))
        self.assertEqual(s["dividend_late"], 2)
        self.assertEqual(s["dividend_late"], sum(x["dividends_after_nav_end"] for x in rows))
        self.assertEqual(s["missing_nav_on_distribution_dates"], 15)

    def test_html_page_links_and_dashboard_ids(self):
        self.assertIn('href="nav_status.html"', self.index)
        self.assertIn("data/nav_freshness_68.json", self.html)
        self.assertIn("data/phase16_instrument_coverage.csv", self.parser.links)
        for required in ["cards", "search", "sort", "count", "k-total", "k-stale", "k-gap", "k-missing"]:
            self.assertIn(required, self.parser.ids)

    def test_explicit_non_realtime_limitations(self):
        for label in ["不是即時行情", "歷史快照", "尚不能", "待更新"]:
            self.assertIn(label, self.html)
        self.assertNotIn("YmI1MWFl", self.html)
        self.assertNotIn("YmI1MWFl", json.dumps(self.manifest))

if __name__ == "__main__":
    unittest.main()
