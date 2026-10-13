"""Official TPEx ETF and class review mobile UI remains read-only."""
from pathlib import Path
import re
import unittest

ROOT=Path(__file__).resolve().parents[1]
CAT=(ROOT/"market_universe.html").read_text("utf-8")
REVIEW=(ROOT/"market_identity_review.html").read_text("utf-8")
INDEX=(ROOT/"index.html").read_text("utf-8")
WF=(ROOT/".github/workflows/market_universe_daily.yml").read_text("utf-8")
SRC=(ROOT/"tools/build_market_universe.py").read_text("utf-8")

class UiTests(unittest.TestCase):
    def test_links(self):
        self.assertIn('href="market_identity_review.html"',CAT)
        self.assertIn('href="market_identity_review.html"',INDEX)
        self.assertIn('href="market_universe.html"',REVIEW)
        self.assertIn('id="tpex-etf"',CAT)
        self.assertIn('value="etf_tpex"',CAT)
        self.assertIn('value="etf_twse"',CAT)
        self.assertIn("tpex_etfs",SRC)
        self.assertIn("tpex_suggestions",SRC)
    def test_review_requires_unapproved_dataset(self):
        for token in ["fiveline_market_identity_review_v1","automatic_deduplication_applied!==false",
                      "all_fund_classes_approved!==false","historical_nav_backfilled!==false",
                      "original_excel_modified!==false","customer_data_included!==false",
                      "unique_investment_products_verified!==null", "id=\"csv\"",
                      "基金級別","同名稱","上櫃股／ETF"]:
            self.assertIn(token,REVIEW)
        self.assertNotIn("innerHTML=",REVIEW)
        self.assertNotIn("localStorage",REVIEW)
        self.assertNotIn("YmI1MWFl",REVIEW)
        self.assertEqual(len(re.findall(r'<script>(.*?)</script>',REVIEW,re.S)),1)
    def test_nightly_stores_both_catalog_and_review(self):
        self.assertIn("python -m tools.audit_market_identity",WF)
        self.assertIn("public_market_universe/identity_review.json",WF)
        self.assertIn("len(x['covered_sources'])==6",WF)
        self.assertIn("not d['automatic_deduplication_applied']",WF)
        self.assertNotIn("five_line.html",WF)
if __name__=="__main__":
    unittest.main()
