"""Offline data-quality gates for dividend composition and 3-year readiness."""
from pathlib import Path
from unittest.mock import patch
import json
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen
from http.server import ThreadingHTTPServer
from threading import Thread

from adviser_portal import offshore_evidence as o
from adviser_portal import income_readiness as r
from adviser_portal.server import Handler

IDENTITY={
 "uid":"off-001","name":"紐約梅隆美國股票收益基金-美元A配息",
 "code":"00BD5M6702","isin":"IE00BD5M6702",
 "source":"tdcc_offshore_funds","category":"offshore_fund_share_class",
 "currency":"美元","class_identity_approved":False,
 "historical_prices_ready":False,
}
BASIC={"基金代碼":IDENTITY["code"],"基金ISIN_CODE":IDENTITY["isin"],
       "基金名稱":IDENTITY["name"],"計價幣別":"美元"}
DIV={"基金代碼":IDENTITY["code"],"基金名稱":IDENTITY["name"],
     "基金幣別":"美元","除息日":"2026/09/30","配息頻率":"月配",
     "單位數":"0.0212","可分配淨利益/配息":"73.21","本金/配息":"26.79"}

class TestDividendComponents(unittest.TestCase):
    def test_sum_100_is_percent_not_dividend_cash(self):
        v=o._component_ratios(DIV)
        self.assertEqual(v["status"],"PROPORTIONS_ONLY")
        self.assertEqual(v["income_pct"],"73.21")
        self.assertEqual(v["capital_pct"],"26.79")
        self.assertNotIn("cash",str(v))
    def test_not_a_ratio_is_not_amount(self):
        for row in [
            {**DIV,"本金/配息":"99"},
            {**DIV,"本金/配息":"-5"},
            {**DIV,"本金/配息":"not a number"},
            {**DIV,"可分配淨利益/配息":"101"},
        ]:
            with self.subTest(row=row):
                self.assertIn(o._component_ratios(row)["status"],("INVALID","CONFLICT"))
    def test_missing_components_must_not_appear_as_0_percent(self):
        x=o._component_ratios({**DIV,"本金/配息":"NA"})
        self.assertEqual(x["status"],"INCOMPLETE")
        self.assertIsNone(x["income_pct"])
    def test_event_field_unit_number_not_promoted_to_cash_amount(self):
        with patch.object(o,"choose_instrument",return_value=IDENTITY):
            def fetch(k):
                return {"basic":[BASIC],"dividend":[DIV]}[k]
            x=o.dividend_events(IDENTITY["uid"],fetcher=fetch)
        self.assertEqual(x["event_count"],1)
        event=x["events"][0]
        self.assertEqual(event["composition"]["income_pct"],"73.21")
        self.assertEqual(event["composition"]["capital_pct"],"26.79")
        self.assertTrue(event["reported_unit_field_present"])
        self.assertIsNone(event["per_unit_cash_amount"])
        self.assertFalse(event["per_unit_cash_amount_verified"])
        self.assertFalse(x["cash_dividend_per_share_verified"])
        self.assertFalse(x["dividend_adjustment_ready"])
    def test_conflicting_composition_same_day_rejected(self):
        with patch.object(o,"choose_instrument",return_value=IDENTITY):
            records=[DIV,{**DIV,"可分配淨利益/配息":"80","本金/配息":"20"}]
            x=o.dividend_events(IDENTITY["uid"],
                fetcher=lambda k:{"basic":[BASIC],"dividend":records}[k])
        self.assertEqual(x["status"],"REVIEW_REQUIRED")
    def test_missing_components_still_date_metadata_only(self):
        with patch.object(o,"choose_instrument",return_value=IDENTITY):
            x=o.dividend_events(IDENTITY["uid"],
                fetcher=lambda k:{"basic":[BASIC],"dividend":[{"基金代碼":IDENTITY["code"],
                    "基金名稱":IDENTITY["name"],"基金幣別":"美元",
                    "除息日":"2026/09/30","配息頻率":"月配"}]}[k])
        self.assertEqual(x["events"][0]["composition"]["status"],"INCOMPLETE")
        self.assertEqual(x["event_count"],1)


class TestIncomeReadiness(unittest.TestCase):
    def setUp(self):
        self.nav={
            "instrument_uid":IDENTITY["uid"],"display_name":IDENTITY["name"],
            "code":IDENTITY["code"],"isin":IDENTITY["isin"],
            "currency":"USD","status":"OFFSHORE_RECENT_NAV_EVIDENCE_ONLY",
            "recent_nav_days":["2026-10-16","2026-10-15","2026-10-14"]
        }
        self.div={
            "instrument_uid":IDENTITY["uid"],"display_name":IDENTITY["name"],
            "status":"OFFSHORE_DIVIDEND_DATE_METADATA_ONLY",
            "event_count":2,
            "events":[
                {"composition":{"status":"PROPORTIONS_ONLY"}},
                {"composition":{"status":"INCOMPLETE"}},
            ]
        }
    def call(self):
        with patch.object(r,"latest_nav",return_value=self.nav),\
             patch.object(r,"dividend_events",return_value=self.div):
            return r.readiness(IDENTITY["uid"])
    def test_three_year_history_gate_never_fabricated(self):
        x=self.call()
        self.assertEqual(x["status"],"RESEARCH_ONLY_ADJUSTMENT_NOT_READY")
        self.assertEqual(x["recent_nav_observations"],3)
        self.assertEqual(x["minimum_historical_years_required"],3)
        self.assertFalse(x["historical_nav_3y_verified"])
        self.assertFalse(x["ready_for_portfolio"])
        self.assertFalse(x["ready_for_total_return"])
        self.assertFalse(x["ready_for_six_strategies"])
        self.assertFalse(x["client_records_saved"])
    def test_cash_dividend_issuer_evidence_remains_zero(self):
        x=self.call()
        self.assertEqual(x["displayed_income_principal_ratio_verified_count"],1)
        self.assertEqual(x["displayed_income_principal_ratio_not_verified_count"],1)
        self.assertEqual(x["issuer_per_unit_payouts_reconciled"],0)
        self.assertFalse(x["distribution_amounts_adjusted"])
        self.assertFalse(x["cash_vs_reinvested_scenarios_validated"])
    def test_bad_nav_stops_before_fetching_dividend(self):
        self.nav={"status":"REVIEW_REQUIRED","instrument_uid":IDENTITY["uid"],
                  "display_name":IDENTITY["name"],"reason":"NAV identity wrong"}
        with patch.object(r,"latest_nav",return_value=self.nav),\
             patch.object(r,"dividend_events") as getdiv:
            x=r.readiness(IDENTITY["uid"])
        self.assertEqual(x["status"],"IDENTITY_OR_NAV_REVIEW_REQUIRED")
        getdiv.assert_not_called()
    def test_ambiguous_dividend_blocks_further_use(self):
        self.div={"status":"REVIEW_REQUIRED","instrument_uid":IDENTITY["uid"],
                  "display_name":IDENTITY["name"],"reason":"ISIN mismatch"}
        x=self.call()
        self.assertEqual(x["status"],"DIVIDEND_IDENTITY_REVIEW_REQUIRED")
        self.assertFalse(x["ready_for_total_return"])
    def test_frontend_contains_readiness_and_proportion_wording(self):
        js=(Path(__file__).resolve().parents[1]/"static"/"app.js").read_text(encoding="utf-8")
        self.assertIn('["nav","dividend","readiness"]',js)
        self.assertIn("part===\"readiness\"",js)
        self.assertIn("含息績效：資料尚未齊全",js)

class TestIncomeReadinessHttp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.http=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        cls.url=f"http://127.0.0.1:{cls.http.server_address[1]}"
        cls.thread=Thread(target=cls.http.serve_forever,daemon=True)
        cls.thread.start()
    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown();cls.http.server_close();cls.thread.join(timeout=3)
    def test_research_readiness_route_never_returns_performance(self):
        mock={"status":"RESEARCH_ONLY_ADJUSTMENT_NOT_READY",
              "instrument_uid":"off-001","ready_for_portfolio":False,
              "ready_for_total_return":False,"ready_for_six_strategies":False,
              "data_modified":False}
        with patch("adviser_portal.server.readiness",return_value=mock) as fn:
            with urlopen(self.url+"/api/offshore-evidence?uid=off-001&part=readiness",timeout=3) as res:
                data=json.load(res)
        fn.assert_called_once_with("off-001")
        self.assertFalse(data["ready_for_total_return"])
        self.assertNotIn("annualized_total_return",data)

if __name__=="__main__":
    unittest.main()
