"""Official historical OHLCV pilot UX is metadata-only, with no chart/Excel mutation."""
from pathlib import Path
import re
import unittest

ROOT=Path(__file__).resolve().parents[1]
PAGE=(ROOT/"ohlcv_history_pilot.html").read_text("utf-8")
HOME=(ROOT/"index.html").read_text("utf-8")
MARKET=(ROOT/"market_universe.html").read_text("utf-8")
STAGE=(ROOT/".github/workflows/ohlcv_pilot_stage.yml").read_text("utf-8")

class PilotPageTests(unittest.TestCase):
 def test_navigation_and_mobile(self):
  self.assertIn('href="ohlcv_history_pilot.html"',HOME)
  self.assertIn('href="ohlcv_history_pilot.html"',MARKET)
  self.assertIn('name="viewport"',PAGE)
  self.assertIn("market-universe/public_market_universe/ohlcv_pilot_summary.json",PAGE)
 def test_raw_prices_not_exposed(self):
  for expected in ["production_coverage_complete!==false",
                   "all_market_history_backfilled!==false",
                   "raw_quotes_publicly_redistributed!==false",
                   "original_excel_modified!==false",
                   "production_analyses_modified!==false",
                   "price_adjustments_applied!==false",
                   "dividend_adjustments_applied!==false",
                   'id="rows"','id="approved"']:
   self.assertIn(expected,PAGE)
  self.assertNotIn("innerHTML=",PAGE)
  self.assertNotIn("localStorage",PAGE)
  self.assertNotIn("YmI1MWFl",PAGE)
  self.assertEqual(len(re.findall(r"<script>(.*?)</script>",PAGE,re.S)),1)
 def test_raw_quotes_remain_in_shortlived_staging_only(self):
  self.assertIn("retention-days: 7",STAGE)
  self.assertIn("historical-ohlcv-research-sqlite-",STAGE)
  self.assertIn("public_market_universe/ohlcv_pilot_summary.json",STAGE)
  self.assertNotIn("git add price_research/",STAGE)
  self.assertIn("source",STAGE)
  self.assertIn("not x",STAGE) if False else self.assertIn("original_excel_modified",STAGE)

if __name__=="__main__":
 unittest.main()
