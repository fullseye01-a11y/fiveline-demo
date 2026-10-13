import json
from html.parser import HTMLParser
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
RESPONSIVE = (ROOT/"five_line_responsive.html").read_text(encoding="utf-8")
SNAPSHOT_PAGE = (ROOT/"five_line.html").read_text(encoding="utf-8")
NAV_STATUS = (ROOT/"nav_status.html").read_text(encoding="utf-8")
HOME = (ROOT/"index.html").read_text(encoding="utf-8")

class Collector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.links = []
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if a.get("id"):
            self.ids.add(a["id"])
        if tag=="a" and a.get("href"):
            self.links.append(a["href"])

class MobileChartTests(unittest.TestCase):
    def test_original_embedded_history_is_present_and_unchanged_for_runtime(self):
        pattern = r'<script id="dataset" type="application/json">([\s\S]*?)</script>'
        match = re.search(pattern, SNAPSHOT_PAGE)
        self.assertIsNotNone(match)
        raw = json.loads(match.group(1))
        self.assertEqual(len(raw), 68)
        for symbol in ("1","4","5","6"):
            self.assertIn(symbol, raw)
        r = raw["1"]["windows"]["840"]
        self.assertEqual(r["status"], "ok")
        self.assertAlmostEqual(r["points"][-1][3], 2.0691, places=4)
        self.assertGreater(len(r["points"]), 800)

    def test_homepage_points_to_responsive_version_and_keeps_legacy(self):
        self.assertIn('href="five_line_responsive.html"', HOME)
        self.assertIn('href="five_line.html"', RESPONSIVE)
        self.assertIn('fetch("five_line.html"', RESPONSIVE)

    def test_no_mobile_min_width_and_no_mobile_squeezed_labels(self):
        self.assertIn("min-width:0", RESPONSIVE)
        self.assertIn("overflow:hidden", RESPONSIVE)
        self.assertIn('preserveAspectRatio="xMidYMid meet"', RESPONSIVE)
        self.assertIn("if(!compact){", RESPONSIVE)
        self.assertIn('parentWidth<650', RESPONSIVE)
        self.assertIn('compact?720:1200', RESPONSIVE)
        self.assertIn('svg.setAttribute("viewBox"', RESPONSIVE)

    def test_seven_channel_mapping_and_dynamic_position(self):
        for name in ["極端超買區", "超買警戒區", "強勢偏多區", "中心趨勢線",
                     "弱勢回檔區", "超賣警戒區", "極端超賣區"]:
            self.assertIn(name, RESPONSIVE)
        self.assertIn('迴歸通道位階：+3SD ~ -3SD', RESPONSIVE)
        self.assertIn('"目前位置："+r.zone', RESPONSIVE)
        self.assertIn('p.slice(-Number(currentRange))', RESPONSIVE)
        self.assertIn('a[2]+3*sd', RESPONSIVE)
        self.assertIn('a[2]-3*sd', RESPONSIVE)

    def test_backend_connection_is_explicitly_historical_and_nonblocking(self):
        self.assertIn('https://fiveline-nav-readonly.onrender.com/api/status',NAV_STATUS)
        self.assertIn('只讀歷史模式',NAV_STATUS)
        self.assertIn('未啟用最新 NAV 自動更新',NAV_STATUS)
        self.assertIn('checkBackend()',NAV_STATUS)
        self.assertIn('AbortController',NAV_STATUS)

    def test_html_required_element_ids(self):
        p=Collector()
        p.feed(RESPONSIVE)
        for required in ["query","fund","window","visible-range","plot","last","mid","z","date","status","zone","rows"]:
            self.assertIn(required,p.ids)
        self.assertEqual(RESPONSIVE.count("<script>"),1)

if __name__ == "__main__":
    unittest.main()
