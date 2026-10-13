"""Market-wide directory is a source-separated registry, not historical investment NAV."""
from pathlib import Path
import re
import unittest

ROOT=Path(__file__).resolve().parents[1]
WEB=(ROOT/"market_universe.html").read_text("utf-8")
HOME=(ROOT/"index.html").read_text("utf-8")
HEALTH=(ROOT/"nav_status.html").read_text("utf-8")
SOURCE=(ROOT/"tools/build_market_universe.py").read_text("utf-8")
DAILY=(ROOT/".github/workflows/market_universe_daily.yml").read_text("utf-8")

class MarketPageTests(unittest.TestCase):
    def test_mobile_search_and_linked(self):
        for page in (HOME,HEALTH):
            self.assertIn('href="market_universe.html"',page)
        for s in ["id=\"query\"","id=\"kind\"","id=\"more\"","id=\"listed\"","id=\"otc\"",
                  "id=\"domestic\"","id=\"offshore\"","id=\"etf\"",
                  "name=\"viewport\"","market-universe/public_market_universe/latest.json"]:
            self.assertIn(s,WEB)
        self.assertIn("PAGE_SIZE=40",WEB)
    def test_catalog_does_not_claim_prices_or_universal_global_coverage(self):
        for s in ["result.market_data_live_prices!==false",
                  "result.historical_nav_backfilled!==false","result.share_classes_approved!==false",
                  "result.original_excel_modified!==false","result.customer_records_included!==false",
                  "尚未涵蓋全部上櫃 ETF","在台核備"]:
            self.assertIn(s,WEB)
        self.assertNotIn("innerHTML=",WEB)
        self.assertNotIn("localStorage",WEB)
        self.assertNotIn("YmI1MWFl",WEB)
        self.assertEqual(len(re.findall(r"<script>(.*?)</script>",WEB,re.S)),1)
    def test_independent_daily_validation(self):
        self.assertIn("python -m tools.build_market_universe",DAILY)
        self.assertIn("market-universe",DAILY)
        self.assertIn("catalog_complete_for_configured_sources",DAILY)
        self.assertIn("not x['customer_records_included']",DAILY)
        self.assertNotIn("five_line.html",DAILY)
        self.assertNotIn("openpyxl",SOURCE)
        self.assertNotIn("pandas",SOURCE)

if __name__=="__main__":
    unittest.main()
