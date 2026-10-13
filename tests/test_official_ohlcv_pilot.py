"""Historical TWSE and TPEx raw OHLCV schema and fail-closed safeguards."""
import sqlite3
from tempfile import TemporaryDirectory
from pathlib import Path
import unittest
from datetime import datetime,timezone

from tools.official_ohlcv_pilot import (
  parse_twse_month,parse_tpex_day,price_row,roc_date,
  make_summary,build_sqlite,PILOTS
)

def twse_sample():
 return {"stat":"OK",
  "fields":["日期","成交股數","成交金額","開盤價","最高價","最低價","收盤價","漲跌價差","成交筆數","註記"],
  "data":[["115/09/01","25,000,000","300,000,000","101.0","102","99.0","100.0","+1","5000",""],
          ["115/09/02","25,000,000","300,000,000","101.0","103","100.0","102.0","+2","5000",""]]
 }
def tpex_sample():
 fields=["代號","名稱","收盤","漲跌","開盤","最高","最低","均價","成交股數","成交金額(元)","成交筆數"]
 data=[
  ["6488","環球晶","1000.0","+20","985","1000","980","990","15,000","150,000,000","2000"],
  ["006201","元大富櫃50","46.00","+1","45.5","46.2","45.2","45.8","55,000","2,500,000","200"]
 ]
 return {"stat":"ok","date":"20260930","tables":[{"title":"上櫃股票行情","fields":fields,"data":data}]}

class PilotTests(unittest.TestCase):
 def test_roc_calendar(self):
  self.assertEqual(roc_date("115/09/01"),"2026-09-01")
  with self.assertRaisesRegex(ValueError,"invalid ROC"):roc_date("2026/09/01")
 def test_twse_two_unadjusted_price_rows(self):
  rows=parse_twse_month(twse_sample(),"2330","listed_stock","20260901")
  self.assertEqual(len(rows),2)
  self.assertEqual(rows[0]["trade_date"],"2026-09-01")
  self.assertEqual(rows[0]["volume_shares"],"25000000")
  self.assertEqual(rows[0]["price_adjustment"],"RAW_NOT_ADJUSTED")
 def test_tpex_both_stock_and_etf(self):
  rows=parse_tpex_day(tpex_sample(),"2026/09/30",{"6488":"otc_stock","006201":"etf"})
  self.assertEqual(len(rows),2)
  self.assertEqual({x["symbol"] for x in rows},{"6488","006201"})
  self.assertTrue(all(x["market"]=="TPEX" for x in rows))
 def test_tpex_request_date_mismatch_or_missing_code_fails(self):
  with self.assertRaisesRegex(ValueError,"date mismatch"):
   parse_tpex_day(tpex_sample(),"2025/10/08",{"6488":"otc_stock","006201":"etf"})
  with self.assertRaisesRegex(ValueError,"codes missing"):
   parse_tpex_day(tpex_sample(),"2026/09/30",{"6488":"otc_stock","006201":"etf","1234":"etf"})
 def test_reject_bad_ohlc_and_duplicated_twse_date(self):
  with self.assertRaisesRegex(ValueError,"enclosure"):
   price_row("TWSE","2330","listed_stock","2026-09-01","100","105","99","110","1000","100000","TWSE_STOCK_DAY")
  p=twse_sample();p["data"].append(p["data"][0])
  with self.assertRaisesRegex(ValueError,"duplicate TWSE date"):
   parse_twse_month(p,"2330","listed_stock","20260901")
 def test_sqlite_unique_primary_key_and_research_only(self):
  tw=parse_twse_month(twse_sample(),"2330","listed_stock","20260901")
  tp=parse_tpex_day(tpex_sample(),"2026/09/30",{"6488":"otc_stock","006201":"etf"})
  quote=tw+tp
  with TemporaryDirectory() as temp:
   p=Path(temp)/"pilot.sqlite"
   build_sqlite(quote,p)
   with sqlite3.connect(p) as conn:
    self.assertEqual(conn.execute("SELECT count(*) FROM quote_raw").fetchone()[0],4)
    self.assertEqual(conn.execute("SELECT count(*) FROM quote_raw WHERE price_adjustment='RAW_NOT_ADJUSTED'").fetchone()[0],4)
   with self.assertRaisesRegex(ValueError,"refuse overwrite"):
    build_sqlite(quote,p)
   with self.assertRaisesRegex(ValueError,"same instrument/date collided"):
    build_sqlite(quote+tw[:1],Path(temp)/"dup.sqlite")
 def test_aggregate_metadata_no_raw_prices(self):
  all_rows=[]
  for market,symbol,category in PILOTS:
   if market=="TWSE":all_rows.extend(parse_twse_month(twse_sample(),symbol,category,"20260901"))
   else:all_rows.extend([x for x in parse_tpex_day(tpex_sample(),"2026/09/30",{"6488":"otc_stock","006201":"etf"}) if x["symbol"]==symbol])
  summary=make_summary(all_rows,[{"market":"TWSE","period":"20260901","sha256":"dummy"}],datetime(2026,10,14,tzinfo=timezone.utc))
  self.assertEqual(summary["pilot_symbols"],4)
  self.assertFalse(summary["raw_quotes_publicly_redistributed"])
  self.assertFalse(summary["original_excel_modified"])
  self.assertFalse(summary["all_market_history_backfilled"])
  self.assertFalse(summary["price_adjustments_applied"])
  self.assertFalse(summary["production_analyses_modified"])
  self.assertNotIn('"open":',str(summary))
  self.assertNotIn('"close":',str(summary))
if __name__=="__main__":unittest.main()
