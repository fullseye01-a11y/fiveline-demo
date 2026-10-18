"""Publisher Fidelity excerpt is a review aid, not an approved return series."""
from datetime import date
from http.server import ThreadingHTTPServer
from threading import Thread
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen
from pathlib import Path
import json
import unittest

from adviser_portal import issuer_pdf_review as i
from adviser_portal.server import Handler

ROW = {
    "uid":"123e331851e3ae6ff190",
    "source":"tdcc_offshore_funds","category":"offshore_fund_share_class",
    "name":"富達基金－全球優質債券基金 (A股C月配息美元)",
    "code":"LU2156566916","isin":"LU2156566916","currency":"美元",
}
BASE_EVENTS = [
    {"ex_date":"2026-07-01",
     "composition":{"status":"PROPORTIONS_ONLY","income_pct":"100","capital_pct":"0"}},
    {"ex_date":"2026-05-01",
     "composition":{"status":"PROPORTIONS_ONLY","income_pct":"45.70","capital_pct":"54.30"}},
    {"ex_date":"2026-06-01",
     "composition":{"status":"INCOMPLETE","income_pct":None,"capital_pct":None}},
    {"ex_date":"2026-10-01",
     "composition":{"status":"PROPORTIONS_ONLY","income_pct":"100","capital_pct":"0"}},
]
def fake_div():
    return {
        "status":"OFFSHORE_DIVIDEND_DATE_METADATA_ONLY",
        "event_count":len(BASE_EVENTS),"events":BASE_EVENTS
    }

class TestIssuerPdfExcerpt(unittest.TestCase):
    def setUp(self):
        self.patch=patch.object(i,"choose_instrument",return_value=ROW)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def report(self,div=None):
        with patch.object(i,"dividend_events",return_value=div if div is not None else fake_div()):
            return i.issuer_distribution_review(ROW["uid"])

    def test_twelve_month_source_indexed_original_not_latest(self):
        r=self.report()
        self.assertEqual(r["status"],"ISSUER_OFFICIAL_PDF_EXCERPT_MANUAL_REVIEW_ONLY")
        self.assertEqual(r["published_monthly_event_count"],12)
        self.assertEqual(r["issuer_last_observed_ex_date"],"2026-07-01")
        self.assertEqual(r["issuer_pdf_page"],33)
        self.assertEqual(r["currency"],"USD")
        self.assertEqual(r["isin"],ROW["isin"])
        self.assertTrue(r["source_doc_not_current_through_today"])

    def test_actual_sample_values_reference_exact_issuer_A_class(self):
        r=self.report()
        items={x["ex_date"]:x for x in r["events"]}
        self.assertEqual(items["2026-07-01"]["issuer_per_unit_distribution_usd"],"0.0580")
        self.assertEqual(items["2026-06-01"]["issuer_per_unit_distribution_usd"],"0.0580")
        self.assertEqual(items["2026-05-01"]["issuer_per_unit_distribution_usd"],"0.0580")
        self.assertEqual(items["2026-04-01"]["issuer_per_unit_distribution_usd"],"0.0575")
        self.assertEqual(items["2025-08-01"]["issuer_per_unit_distribution_usd"],"0.0599")
        self.assertEqual(r["issuer_share_class_code"],"GLIAM")

    def test_tdcc_dates_and_ratio_can_agree_without_amount_approval(self):
        r=self.report()
        self.assertEqual(r["tdcc_matching_ex_date_count"],3)
        self.assertEqual(r["tdcc_matching_composition_count"],2)
        self.assertEqual(r["tdcc_composition_conflict_count"],0)
        self.assertEqual(r["tdcc_dates_newer_than_issuer_excerpt"],["2026-10-01"])
        self.assertEqual(r["issuer_per_unit_payouts_reconciled"],0)
        self.assertFalse(r["issuer_document_bytes_verified"])
        self.assertFalse(r["ready_for_total_return"])
        self.assertFalse(r["usable_for_portfolio"])

    def test_ratio_conflict_is_explicit_not_silently_corrected(self):
        data=fake_div()
        data["events"]=[{"ex_date":"2026-05-01","composition":{
            "status":"PROPORTIONS_ONLY","income_pct":"70","capital_pct":"30"}}]
        r=self.report(data)
        self.assertEqual(r["tdcc_composition_conflict_count"],1)
        self.assertEqual(r["tdcc_matching_composition_count"],0)
        self.assertEqual(r["issuer_per_unit_payouts_reconciled"],0)
        self.assertEqual(r["events"][2]["tdcc_comparison_status"],"TDCC_COMPOSITION_CONFLICT")

    def test_unmapped_share_class_does_not_read_or_publish_this_source(self):
        for change in ({"isin":"LU2156566917"},
                       {"currency":"澳幣"},
                       {"name":"富達基金－全球優質債券基金 (B股C月配息美元)"},
                       {"code":"LU2156566917"}):
            with self.subTest(change=change):
                with patch.object(i,"choose_instrument",return_value={**ROW,**change}),\
                     patch.object(i,"dividend_events") as f:
                    r=i.issuer_distribution_review(ROW["uid"])
                f.assert_not_called()
                self.assertEqual(r["status"],"ISSUER_SOURCE_NOT_YET_MAPPED")
                self.assertFalse(r["issuer_document_bytes_verified"])
                self.assertNotIn("events",r)

    def test_observed_pdf_bytes_fingerprint_is_recorded_but_table_unparsed(self):
        x=self.report()
        self.assertEqual(x["issuer_pdf_sha256_registered"],
            "78166e33f9164cc224bde3853ababe788c5d47456a4b7a06f711b0e0f7eaae10")
        self.assertEqual(x["issuer_pdf_bytes_registered"],3114338)
        self.assertTrue(x["issuer_pdf_transport_envelope_checked"])
        self.assertFalse(x["issuer_pdf_table_content_automatically_parsed"])
        self.assertFalse(x["issuer_document_bytes_verified"])
        self.assertEqual(x["issuer_per_unit_payouts_reconciled"],0)

    def test_offshore_class_date_source_error_never_approves_amount(self):
        r=self.report({"status":"REVIEW_REQUIRED","reason":"ISIN differs"})
        self.assertEqual(r["status"],"TDCC_SOURCE_REVIEW_REQUIRED")
        self.assertEqual(r["issuer_per_unit_payouts_reconciled"],0)
        self.assertNotIn("events",r)

    def test_hard_gate_does_not_claim_official_pdf_bytes_downloaded(self):
        r=self.report()
        self.assertTrue(r["issuer_pdf_url"].startswith("https://www.fidelityinternational.com/"))
        self.assertFalse(r["issuer_document_bytes_verified"])
        self.assertFalse(r["cash_or_reinvestment_approved"])
        self.assertFalse(r["historical_nav_3y_verified"])
        self.assertFalse(r["customer_records_saved"])
        self.assertFalse(r["production_backtests_modified"])
        self.assertNotIn("annualized_return",r)

    def test_public_excerpt_format_and_math_checked_before_display(self):
        self.assertTrue(i._valid_excerpt())
        with patch.object(i,"EXCERPT",(("2026-07-01","-0.7","50","50"),)*12):
            with self.assertRaises(ValueError):
                i._valid_excerpt()
        with patch.object(i,"EXCERPT",(("2026-07-01","0.5","40","40"),)*12):
            with self.assertRaises(ValueError):
                i._valid_excerpt()

    def test_iphone_html_has_source_link_and_explicit_unverified_note(self):
        js=(Path(__file__).resolve().parents[1]/"static"/"app.js").read_text(encoding="utf-8")
        self.assertIn('offshoreParts.push("issuer")',js)
        self.assertIn("issuer_document_bytes_verified!==false",js)
        self.assertIn("公告每單位",js)
        self.assertIn("尚未驗證原始 PDF 檔案雜湊",js)


class TestIssuerHttp(unittest.TestCase):
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
        cls.thread.join(timeout=2)

    def test_issuer_readonly_api_no_portfolio_result(self):
        mock={"instrument_uid":ROW["uid"],"status":"ISSUER_SOURCE_NOT_YET_MAPPED",
              "issuer_document_bytes_verified":False,
              "issuer_per_unit_payouts_reconciled":0,
              "ready_for_total_return":False,"usable_for_portfolio":False,
              "production_backtests_modified":False}
        with patch("adviser_portal.server.issuer_distribution_review",return_value=mock) as fn:
            with urlopen(self.url+"/api/offshore-evidence?uid=123e331851e3ae6ff190&part=issuer",timeout=3) as res:
                data=json.load(res)
        fn.assert_called_once_with(ROW["uid"])
        self.assertFalse(data["issuer_document_bytes_verified"])
        self.assertEqual(data["issuer_per_unit_payouts_reconciled"],0)

    def test_issuer_api_parameter_guard(self):
        with self.assertRaises(HTTPError) as exc:
            urlopen(self.url+"/api/offshore-evidence?part=issuer&uid=x&url=https://fake.example",timeout=3)
        self.assertEqual(exc.exception.code,400)
        exc.exception.close()


if __name__=="__main__":
    unittest.main()
