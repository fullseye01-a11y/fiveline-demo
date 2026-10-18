"""3-year historical NAV truth-in-display and read-only HTTP regression."""
from datetime import date
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from unittest.mock import patch
from urllib.request import urlopen
from urllib.error import HTTPError
import json
import unittest

from adviser_portal import history_coverage as h
from adviser_portal.server import Handler

TODAY=date(2026,10,18)
OFF={
 "uid":"off-1","category":"offshore_fund_share_class",
 "name":"富達基金－全球優質債券基金 (A股C月配息美元)",
 "source":"tdcc_offshore_funds","isin":"LU2156566916","currency":"美元",
}
DOM={"uid":"dom-1","category":"domestic_fund_share_class",
     "name":"安聯台灣科技基金","source":"sitca_domestic_funds",
     "isin":"","currency":"TWD"}

class TestLiveCoverage(unittest.TestCase):
    def test_offshore_short_window_is_not_three_years(self):
        with patch.object(h,"choose_instrument",return_value=OFF),\
             patch.object(h,"latest_nav",return_value={
               "status":"OFFSHORE_RECENT_NAV_EVIDENCE_ONLY",
               "recent_nav_days":["2026-10-16","2026-10-15","2026-10-14"],
             }):
            r=h.coverage(OFF["uid"],today=TODAY)
        self.assertEqual(r["status"],"THREE_YEAR_NAV_SOURCE_NOT_READY")
        self.assertEqual(r["recent_evidence_nav_dates_count"],3)
        self.assertEqual(r["minimum_start_date"],"2023-10-18")
        self.assertEqual(r["recent_evidence_first_nav_date"],"2026-10-14")
        self.assertEqual(r["full_historical_rows_imported"],0)
        self.assertFalse(r["historical_three_years_ready"])
        self.assertIsNone(r["validated_annualized_total_return"])
        self.assertFalse(r["ready_for_portfolio"])
        self.assertFalse(r["ready_for_six_strategies"])
        self.assertFalse(r["production_backtests_modified"])

    def test_domestic_latest_is_only_one_source_date(self):
        with patch.object(h,"choose_instrument",return_value=DOM),\
             patch.object(h,"evidence",return_value={
                  "status":"LATEST_NAV_EVIDENCE_ONLY",
                  "nav_date":"2026-10-16"
             }):
            r=h.coverage(DOM["uid"],today=TODAY)
        self.assertEqual(r["recent_evidence_nav_dates_count"],1)
        self.assertIn("SITCA",r["current_source"])
        self.assertFalse(r["history_source_bundle_attested"])

    def test_share_class_mismatch_exposes_no_fake_history(self):
        with patch.object(h,"choose_instrument",return_value=OFF),\
             patch.object(h,"latest_nav",return_value={
                  "status":"REVIEW_REQUIRED","recent_nav_days":[],
             }):
            r=h.coverage(OFF["uid"],today=TODAY)
        self.assertEqual(r["recent_evidence_nav_dates_count"],0)
        self.assertIsNone(r["recent_evidence_first_nav_date"])
        self.assertFalse(r["historical_three_years_ready"])

    def test_stocks_and_etfs_are_not_fund_nav_history(self):
        with patch.object(h,"choose_instrument",return_value={
           **OFF,"category":"etf"}):
            with self.assertRaises(ValueError):
                h.coverage(OFF["uid"],today=TODAY)

    def test_iphone_manual_check_is_wired_and_is_nonprice(self):
        js=(Path(__file__).resolve().parents[1]/"static"/"app.js").read_text("utf-8")
        self.assertIn('"/api/history-coverage"',js)
        self.assertIn("檢查三年以上歷史 NAV",js)
        self.assertIn("info.validated_annualized_total_return!==null",js)

class TestCoverageHttp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.http=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        cls.url=f"http://127.0.0.1:{cls.http.server_address[1]}"
        cls.thread=Thread(target=cls.http.serve_forever,daemon=True)
        cls.thread.start()
    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown();cls.http.server_close();cls.thread.join(timeout=2)

    def test_readonly_uid_coverage(self):
        with patch("adviser_portal.server.coverage",return_value={
             "status":"THREE_YEAR_NAV_SOURCE_NOT_READY",
             "instrument_uid":"off-1","validated_annualized_total_return":None,
             "historical_three_years_ready":False}) as checker:
            with urlopen(self.url+"/api/history-coverage?uid=off-1",timeout=3) as resp:
                data=json.load(resp)
        checker.assert_called_once_with("off-1")
        self.assertFalse(data["historical_three_years_ready"])

    def test_bad_query_parameters_rejected(self):
        for query in ("?uid=x&uid=y","?uid=x&url=http://fake.example",
                      "?x=y",""):
            with self.subTest(query=query),self.assertRaises(HTTPError) as exc:
                urlopen(self.url+"/api/history-coverage"+query,timeout=3)
            self.assertEqual(exc.exception.code,400)
            exc.exception.close()

if __name__=="__main__":
    unittest.main()
