"""Regression label consistency with existing fixed OLS data."""
from html.parser import HTMLParser
from pathlib import Path
import json
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "five_line.html").read_text(encoding="utf-8")
data_match = re.search(r'<script id="dataset" type="application/json">([\s\S]*?)</script>', HTML)
DB = json.loads(data_match.group(1)) if data_match else {}


class FiveLineLabelTests(unittest.TestCase):
    def test_all_68_instruments_and_840_day_example(self):
        self.assertEqual(len(DB), 68)
        r = DB["4"]["windows"]["840"]
        self.assertEqual(r["status"], "ok")
        self.assertGreater(r["sd"], 0)
        self.assertGreater(len(r["points"]), 10)

    def test_all_seven_labels_exact_SD_levels(self):
        for value in [
            "+3SD 極樂觀", "+2SD 樂觀", "+1SD 相對樂觀",
            "TL 趨勢", "-1SD 相對悲觀", "-2SD 悲觀", "-3SD 極悲觀"
        ]:
            self.assertIn(value, HTML)
        for value in ["a[2]+sd*r.sd", "a[2]-sd*r.sd"]:
            self.assertIn(value, HTML)
        self.assertIn("viewBox=\\"0 0 1180 400\\"", HTML)

    def test_original_data_schema_unchanged(self):
        self.assertEqual(DB["4"]["name"], "元大台灣50")
        self.assertEqual(DB["4"]["windows"]["840"]["status"], "ok")
        self.assertEqual(DB["4"]["windows"]["840"]["points"][-1][0], "2026-09-01")

    def test_legend_explains_statistical_not_predictive(self):
        self.assertIn("並非價格一定反轉的預測", HTML)


if __name__ == "__main__":
    unittest.main()
