"""Current October Fidelity exact ISIN source can never turn into returns."""
from http.server import ThreadingHTTPServer
from threading import Thread
from unittest.mock import patch,MagicMock
from urllib.request import urlopen
from urllib.error import HTTPError
from pathlib import Path
import json
import unittest

from adviser_portal import issuer_current as i
from adviser_portal import issuer_pdf_table as t
from adviser_portal.server import Handler

ROW={"uid":"off-target","source":"tdcc_offshore_funds","category":"offshore_fund_share_class",
     "name":"富達基金－全球優質債券基金 (A股C月配息美元)",
     "code":"LU2156566916","isin":"LU2156566916","currency":"美元"}
VERIFIED={
  "status":"PUBLISHER_OCTOBER_DOCUMENT_TABLE_AUTOMATICALLY_VERIFIED",
  "matching_catalog_isin":"LU2156566916",
  "publisher_class_code":"GLIAM","verified_ex_date_count":12,
  "independent_older_issuer_overlap_count":9,
  "newer_months_source_verified_count":3,
  "newer_issuer_payouts":[
     {"ex_date":"2026-10-01","usd_per_unit":"0.0900","income_percent":"100","capital_percent":"0"},
     {"ex_date":"2026-09-01","usd_per_unit":"0.0800","income_percent":"100","capital_percent":"0"},
     {"ex_date":"2026-08-03","usd_per_unit":"0.0700","income_percent":"100","capital_percent":"0"}],
  "total_return_approved":False,
  "pdf_sha256":t.PDF_SHA256_REGISTERED,
  "page_number":31
}
class OctoberPublisherReadOnlyTests(unittest.TestCase):
    def setUp(self):
        p=patch.object(i,"choose_instrument",return_value=ROW)
        p.start();self.addCleanup(p.stop)

    def test_verified_new_months_stay_research_only(self):
        with patch.object(i,"verify_single_official_version",return_value=VERIFIED):
            output=i.current_october_issuer("off-target",fetcher=lambda:b"stub")
        self.assertEqual(output["newer_months_verified"],3)
        self.assertEqual(output["issuer_published_monthly_records"],12)
        self.assertEqual(output["older_source_overlap_confirmed"],9)
        self.assertEqual(output["pdf_page_number"],31)
        self.assertTrue(output["publisher_pdf_table_verified"])
        self.assertEqual(output["tdcc_amounts_reconciled"],0)
        self.assertFalse(output["ready_for_total_return"])
        self.assertFalse(output["usable_for_portfolio"])
        self.assertFalse(output["production_backtests_modified"])
        self.assertNotIn("annualized_total_return",output)

    def test_other_same_provider_b_share_class_does_not_receive_prices(self):
        with patch.object(i,"choose_instrument",return_value={**ROW,
                    "name":"富達基金－全球優質債券基金 (B股C月配息美元)"}),\
             patch.object(i,"verify_single_official_version") as call:
            r=i.current_october_issuer("not-target",fetcher=lambda:None)
        self.assertEqual(r["status"],"ISSUER_SOURCE_NOT_YET_MAPPED")
        self.assertNotIn("newer_per_unit_cash_distribution",r)
        call.assert_not_called()

    def test_unknown_isin_cannot_request_source(self):
        with patch.object(i,"choose_instrument",return_value={**ROW,"isin":"LU2156566917"}),\
             patch.object(i,"verify_single_official_version") as call:
            r=i.current_october_issuer("not-target",fetcher=lambda:None)
        self.assertFalse(r["publisher_pdf_table_verified"])
        call.assert_not_called()

    def test_mismatched_source_schema_prevents_any_display(self):
        with patch.object(i,"verify_single_official_version",return_value={
            **VERIFIED,"verified_ex_date_count":11}):
            with self.assertRaisesRegex(ValueError,"inconsistent"):
                i.current_october_issuer("off-target",fetcher=lambda:b"stub")

    def test_no_tdcc_amount_approval_if_pdf_verified(self):
        with patch.object(i,"verify_single_official_version",return_value=VERIFIED):
            out=i.current_october_issuer("off-target",fetcher=lambda:b"stub")
        self.assertEqual(out["tdcc_amounts_reconciled"],0)
        self.assertFalse(out["complete_historical_nav_3y_verified"])

    def test_mobile_buttons_are_separate_and_no_inferred_annual_returns(self):
        text=(Path(__file__).resolve().parents[1]/"static"/"app.js").read_text(encoding="utf-8")
        self.assertIn('offshoreParts.push("issuer-current","issuer")',text)
        self.assertIn('v.publisher_pdf_table_verified!==true',text)
        self.assertIn("集保每單位金額對帳為 0 筆",text)
        self.assertIn("富達 10 月公告最新配息核對",text)

class OctoberPublisherHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.http=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        cls.url=f"http://127.0.0.1:{cls.http.server_address[1]}"
        cls.thread=Thread(target=cls.http.serve_forever,daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown();cls.http.server_close();cls.thread.join(timeout=3)

    def test_official_current_doc_research_api(self):
        with patch("adviser_portal.server.current_october_issuer",return_value={
            "status":"ISSUER_OCTOBER_12_RECORDS_PDF_VERIFIED_RESEARCH_ONLY",
            "instrument_uid":"off-target","tdcc_amounts_reconciled":0,
            "ready_for_total_return":False,"usable_for_portfolio":False}):
            with urlopen(self.url+"/api/offshore-evidence?uid=off-target&part=issuer-current",timeout=3) as resp:
                data=json.load(resp)
        self.assertFalse(data["ready_for_total_return"])
        self.assertEqual(data["tdcc_amounts_reconciled"],0)

    def test_changed_pdf_version_returns_503_not_price(self):
        with patch("adviser_portal.server.current_october_issuer",
                   side_effect=t.IssuerPdfTableUnverified("issuer source changed")):
            with self.assertRaises(HTTPError) as exc:
                urlopen(self.url+"/api/offshore-evidence?uid=off-target&part=issuer-current",timeout=3)
            self.assertEqual(exc.exception.code,503)
            self.assertNotIn("issuer source changed",exc.exception.read().decode())
            exc.exception.close()

if __name__=="__main__":
    unittest.main()
