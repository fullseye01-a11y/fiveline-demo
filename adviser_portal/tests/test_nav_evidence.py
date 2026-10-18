"""NAV evidence must never be mistaken for approved portfolio prices."""
from datetime import date
from http.server import ThreadingHTTPServer
from threading import Thread
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen
from unittest.mock import patch,MagicMock
import json
import unittest

from adviser_portal import nav_evidence as m
from adviser_portal.server import Handler

ROW={"uid":"dom-001","source":"sitca_domestic_funds",
     "category":"domestic_fund_share_class",
     "name":"安聯台灣科技基金", "code":"ACCT09",
     "currency":"TWD","isin":"",
     "class_identity_approved":False,"historical_prices_ready":False}
OTHER={**ROW,"uid":"foreign-001","source":"tdcc_offshore_funds",
       "category":"offshore_fund_share_class","isin":"LU0000000001"}
def upstream(records=None):
    return {
        "mode":"government_open_data_latest_only",
        "source":"sitca_domestic_daily",
        "source_dataset":m.ORIGIN,
        "matched_to_legacy_fiveline":False,
        "last_verified_at_utc":"2026-10-18T02:00:00+00:00",
        "records":records if records is not None else [{
            "name":ROW["name"],"code":ROW["code"],"currency":"TWD",
            "isin":"","nav":"32.713952","nav_date":"2026-10-16",
            "source":"sitca_domestic_daily",
        }]
    }

class TestNavEvidence(unittest.TestCase):
    def setUp(self):
        self.patch=patch.object(m,"get_catalog",return_value={"items":[ROW,OTHER]})
        self.patch.start()
        self.addCleanup(self.patch.stop)
    def call(self,payload=None,uid=ROW["uid"]):
        calls=[]
        def fetch(url):
            calls.append(url)
            return payload if payload is not None else upstream()
        result=m.evidence(uid,fetcher=fetch,today=date(2026,10,18))
        return result,calls

    def test_exact_domestic_match_is_display_only(self):
        result,urls=self.call()
        self.assertEqual(result["status"],"LATEST_NAV_EVIDENCE_ONLY")
        self.assertEqual(result["nav"],"32.713952")
        self.assertEqual(result["nav_date"],"2026-10-16")
        self.assertEqual(result["age_calendar_days"],2)
        self.assertFalse(result["stale_warning"])
        self.assertFalse(result["usable_for_portfolio"])
        self.assertFalse(result["usable_for_backtest"])
        self.assertFalse(result["class_identity_approved"])
        self.assertEqual(len(urls),1)
        self.assertIn("source=sitca_domestic_daily",urls[0])

    def test_offshore_not_queried_or_presented_as_unit_nav(self):
        mock=MagicMock()
        result=m.evidence(OTHER["uid"],fetcher=mock)
        self.assertEqual(result["status"],"UNSUPPORTED_PRICE_SOURCE")
        self.assertIsNone(result["nav"])
        mock.assert_not_called()

    def test_name_mismatch_does_not_attach_price(self):
        r=upstream()
        r["records"][0]["name"]="安聯台灣科技基金（美元避險級別）"
        result,_=self.call(r)
        self.assertEqual(result["status"],"REVIEW_REQUIRED")
        self.assertIsNone(result["nav"])

    def test_currency_mismatch_does_not_attach_price(self):
        r=upstream()
        r["records"][0]["currency"]="USD"
        result,_=self.call(r)
        self.assertEqual(result["status"],"REVIEW_REQUIRED")

    def test_reused_code_must_match_full_name(self):
        r=upstream([
            {"name":"同基金其他級別","code":ROW["code"],"currency":"TWD",
             "source":"sitca_domestic_daily","nav":"105","nav_date":"2026-10-16"}
        ])
        result,_=self.call(r)
        self.assertEqual(result["status"],"REVIEW_REQUIRED")
        self.assertIsNone(result["nav"])

    def test_multiple_identical_matches_fail_closed(self):
        r=upstream()
        r["records"].append(dict(r["records"][0]))
        result,_=self.call(r)
        self.assertEqual(result["status"],"REVIEW_REQUIRED")

    def test_stale_nav_warning_not_fabricated_latest(self):
        r=upstream()
        r["records"][0]["nav_date"]="2026-09-20"
        result,_=self.call(r)
        self.assertTrue(result["stale_warning"])
        self.assertEqual(result["nav_date"],"2026-09-20")

    def test_invalid_nav_values_fail_closed(self):
        for value in ("NaN","0","Infinity","-12","100000000000"):
            with self.subTest(value=value):
                r=upstream()
                r["records"][0]["nav"]=value
                with self.assertRaises(m.NavEvidenceUnavailable):
                    self.call(r)

    def test_future_nav_date_refused(self):
        r=upstream()
        r["records"][0]["nav_date"]="2026-10-20"
        with self.assertRaisesRegex(m.NavEvidenceUnavailable,"日期異常"):
            self.call(r)

    def test_incorrect_provenance_refused(self):
        for key,val in [("source_dataset","https://untrusted.example/"),
                        ("mode","latest_synthetic"),("matched_to_legacy_fiveline",True)]:
            r=upstream()
            r[key]=val
            with self.subTest(key=key),self.assertRaises(m.NavEvidenceUnavailable):
                self.call(r)

    def test_invalid_uid_refused_before_http(self):
        with self.assertRaises(ValueError):
            m.evidence("../secret",fetcher=MagicMock())

    def test_isin_require_exact_provider_match(self):
        confirmed={**ROW,"uid":"dom-isin","isin":"TW0001112223"}
        with patch.object(m,"get_catalog",return_value={"items":[confirmed]}):
            r=m.evidence(confirmed["uid"],fetcher=lambda url:upstream(),today=date(2026,10,18))
        self.assertEqual(r["status"],"REVIEW_REQUIRED")
        self.assertIsNone(r["nav"])

    def test_iphone_ui_is_on_demand_and_no_direct_cross_site(self):
        static=Path(__file__).resolve().parents[1]/"static"
        js=(static/"app.js").read_text(encoding="utf-8")
        self.assertIn('"/api/nav-evidence"',js)
        self.assertIn("value.usable_for_portfolio!==false",js)
        self.assertIn('row.source==="sitca_domestic_funds"',js)
        self.assertIn("淨值有效日",js)
        self.assertNotIn("fiveline-nav-readonly.onrender.com",js)

class TestNavEvidenceHttp(unittest.TestCase):
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
    def test_uid_api_returns_non_portfolio_evidence(self):
        data={"status":"LATEST_NAV_EVIDENCE_ONLY","instrument_uid":ROW["uid"],
              "nav":"32.7","class_identity_approved":False,"usable_for_portfolio":False}
        with patch("adviser_portal.server.evidence",return_value=data) as fn:
            with urlopen(self.url+"/api/nav-evidence?uid=dom-001",timeout=3) as resp:
                obj=json.load(resp)
        fn.assert_called_once_with("dom-001")
        self.assertFalse(obj["usable_for_portfolio"])
    def test_bad_uid_args_not_accepted(self):
        for query in ("", "?uid=abc&uid=xyz","?source=foreign","?uid=abc&url=x"):
            with self.subTest(query=query),self.assertRaises(HTTPError) as cm:
                urlopen(self.url+"/api/nav-evidence"+query,timeout=3)
            self.assertEqual(cm.exception.code,400)
            cm.exception.close()
    def test_source_failure_is_503_without_confidential_detail(self):
        with patch("adviser_portal.server.evidence",side_effect=m.NavEvidenceUnavailable("bad debug")):
            with self.assertRaises(HTTPError) as cm:
                urlopen(self.url+"/api/nav-evidence?uid=dom-001",timeout=3)
            self.assertEqual(cm.exception.code,503)
            self.assertNotIn("bad debug",cm.exception.read().decode())
            cm.exception.close()


if __name__=="__main__":
    unittest.main()
