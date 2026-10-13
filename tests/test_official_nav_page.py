import re
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
PAGE=(ROOT/"official_nav_search.html").read_text("utf-8")
INDEX=(ROOT/"index.html").read_text("utf-8")
STATUS=(ROOT/"nav_status.html").read_text("utf-8")

class OfficialPageTests(unittest.TestCase):
    def test_links_exist(self):
        self.assertIn('href="official_nav_search.html"',INDEX)
        self.assertIn('href="official_nav_search.html"',STATUS)
        self.assertIn('href="nav_status.html"',PAGE)

    def test_exact_sources_and_no_legacy_overwrites(self):
        for name in ["sitca_domestic_daily","tdcc_offshore_global",
                     "/api/official-nav/status","/api/official-nav/search",
                     "matched_to_legacy_fiveline!==false",
                     "不會修改原始 Excel"]:
            self.assertIn(name,PAGE)
        self.assertNotIn("YmI1MWFl",PAGE)
        self.assertNotIn("localStorage",PAGE)

    def test_responsive_and_safe(self):
        self.assertIn('name="viewport"',PAGE)
        self.assertIn('encodeURIComponent(source)',PAGE)
        self.assertIn('encodeURIComponent(q)',PAGE)
        self.assertIn("textContent",PAGE)
        self.assertNotIn("innerHTML=",PAGE)
        self.assertIn('max-width:1010px',PAGE)
        self.assertIn('aria-live="polite"',PAGE)
        self.assertEqual(len(re.findall(r'<script>(.*?)</script>',PAGE,re.S)),1)

    def test_source_limitations(self):
        self.assertIn("尚未與原 Excel",PAGE)
        self.assertIn("幣別",PAGE)
        self.assertIn("除息",PAGE)
        self.assertIn("一小時",PAGE.replace("1 小時","一小時")) if False else None
        self.assertIn("1 小時快取",PAGE)

if __name__=="__main__":
    unittest.main()
