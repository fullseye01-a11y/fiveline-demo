"""Whole-market coverage of ALL fund catalog records, never single-fund only."""
from http.server import ThreadingHTTPServer
from threading import Thread
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen
from pathlib import Path
import json
import unittest

from adviser_portal.fund_universe_audit import audit_whole_universe
from adviser_portal.server import Handler


ROWS=[
 {"uid":"d1","source":"sitca_domestic_funds","category":"domestic_fund_share_class",
  "code":"DDA01","name":"甲基金 TWD","currency":"TWD","isin":"",
  "class_identity_approved":False,"historical_prices_ready":False},
 {"uid":"d2","source":"sitca_domestic_funds","category":"domestic_fund_share_class",
  "code":"DDA01","name":"乙基金 USD","currency":"USD","isin":"",
  "class_identity_approved":False,"historical_prices_ready":False},
 {"uid":"d3","source":"sitca_domestic_funds","category":"domestic_fund_share_class",
  "code":"DDA02","name":"丙基金 TWD","currency":"TWD","isin":"TW000T3604Y3",
  "class_identity_approved":False,"historical_prices_ready":False},
 {"uid":"f1","source":"tdcc_offshore_funds","category":"offshore_fund_share_class",
  "code":"LU2156566916","name":"富達美元級別","currency":"USD","isin":"LU2156566916",
  "class_identity_approved":False,"historical_prices_ready":False},
 {"uid":"f2","source":"tdcc_offshore_funds","category":"offshore_fund_share_class",
  "code":"XXXX","name":"境外資料錯誤測試","currency":"USD","isin":"LU2156566917",
  "class_identity_approved":False,"historical_prices_ready":False},
 {"uid":"e1","source":"twse_etfs","category":"etf",
  "code":"0050","name":"元大台灣50","currency":"TWD","isin":"",
  "class_identity_approved":False,"historical_prices_ready":False},
]

def snap(rows=ROWS):
    return {"items":rows,"checked_at_utc":"2026-10-18T00:00:00Z"}


class WholeMarketFundAuditTests(unittest.TestCase):
    def test_all_fund_classes_without_losing_same_code_funds(self):
        out=audit_whole_universe(snap())
        self.assertEqual(out["fund_records_processed"],5)
        self.assertEqual(out["domestic_fund_records"],3)
        self.assertEqual(out["offshore_fund_records"],2)
        self.assertEqual(out["other_market_instrument_records"],1)
        self.assertEqual(out["source_code_collision_groups"],1)
        self.assertEqual(out["source_code_collision_rows"],2)
        self.assertTrue(out["source_code_collisions_are_not_duplicate_funds"])
        self.assertEqual(out["missing_isin_records"],2)
        self.assertEqual(out["invalid_isin_checksum_records"],1)
        self.assertEqual(out["historical_nav_3y_ready_records"],0)
        self.assertEqual(out["official_price_history_ingested_by_this_audit"],0)
        self.assertEqual(out["db_rows_written"],0)
        self.assertFalse(out["portfolio_backtests_modified"])
        self.assertFalse(out["customer_account_data_read"])

    def test_spurious_duplicates_not_counted_across_different_sources(self):
        rows=[{**ROWS[0],"uid":"d1"},{
            **ROWS[3],"code":"DDA01","uid":"f1"}]
        out=audit_whole_universe(snap(rows))
        self.assertEqual(out["source_code_collision_groups"],0)
        self.assertEqual(out["fund_records_processed"],2)

    def test_no_fund_deleted_just_because_same_source_code(self):
        out=audit_whole_universe(snap())
        self.assertEqual(sum(out["source_counts"].values()),5)
        self.assertEqual(out["historical_nav_validation_pending_records"],5)
        self.assertEqual(out["completion_percent_for_official_3y_nav"],0)

    def test_different_category_flag_does_not_inflate_fund_records(self):
        out=audit_whole_universe(snap([ROWS[0],ROWS[-1]]))
        self.assertEqual(out["fund_records_processed"],1)
        self.assertEqual(out["other_market_instrument_records"],1)

    def test_duplicate_uid_fails_closed(self):
        with self.assertRaisesRegex(ValueError,"UID collision"):
            audit_whole_universe(snap([ROWS[0],{**ROWS[0]}]))

    def test_missing_catalog_does_not_report_zero_as_complete(self):
        for value in ({},{"items":[]},None):
            with self.subTest(value=value),self.assertRaises(ValueError):
                audit_whole_universe(value)

    def test_plan_for_whole_market_stages_not_three_named_funds(self):
        out=audit_whole_universe(snap())
        self.assertEqual(
            [r["stage"] for r in out["batch_priority"]],
            ["01_CLASS_IDENTITY","02_LICENSED_HISTORY",
             "03_CASH_DISTRIBUTIONS","04_FORMULA_RECONCILIATION"])
        self.assertFalse(out["all_market_international_funds_included"])

    def test_all_market_mobile_dashboard_exposes_no_price_or_login(self):
        folder=Path(__file__).resolve().parents[1]
        html=(folder/"static"/"index.html").read_text(encoding="utf-8")
        js=(folder/"static"/"app.js").read_text(encoding="utf-8")
        for ident in ("allfund-total","allfund-missing-isin",
                      "allfund-code-collisions","allfund-history-ready",
                      "allfund-audit-button"):
            self.assertIn(ident,html)
        self.assertIn('"/api/fund-universe-audit"',js)
        self.assertIn("official_price_history_ingested_by_this_audit!==0",js)
        self.assertIn("result.db_rows_written!==0",js)


class WholeMarketFundAuditHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.http=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        cls.url=f"http://127.0.0.1:{cls.http.server_address[1]}"
        cls.thread=Thread(target=cls.http.serve_forever,daemon=True)
        cls.thread.start()
    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.thread.join(timeout=3)

    def test_status_api_returns_all_funds_not_one_uid(self):
        with patch("adviser_portal.catalog.get_catalog",return_value=snap()):
            with urlopen(self.url+"/api/fund-universe-audit",timeout=3) as response:
                result=json.load(response)
        self.assertEqual(result["fund_records_processed"],5)
        self.assertEqual(result["status"],
                         "WHOLE_FUND_MARKET_CATALOG_AUDITED_METADATA_ONLY")
        self.assertFalse(result["portfolio_backtests_modified"])

    def test_api_rejects_user_controlled_urls_or_uids(self):
        for query in ("?uid=123", "?source=https://bad.example",
                      "?refresh=1"):
            with self.subTest(query=query),self.assertRaises(HTTPError) as error:
                urlopen(self.url+"/api/fund-universe-audit"+query,timeout=3)
            self.assertEqual(error.exception.code,400)
            error.exception.close()

if __name__=="__main__":
    unittest.main()
