"""Issuer NAV revisions and 3Y CSV quality regression, no real NAV imports."""
import csv
from datetime import date
from decimal import Decimal
from hashlib import sha256
from io import StringIO
import unittest
from unittest.mock import patch

from adviser_portal import nav_correction_evidence as c
from adviser_portal import nav_history_audit as a
from adviser_portal.tests.test_nav_history_audit import sample,bundle,TODAY

ALLIANZ="TW000T3604Y3"
DATES=("2026-02-26","2026-03-02")


def official_csv(versions=("493.76","485.27"),missing=()):
    raw,days=sample(isin=ALLIANZ,currency="TWD")
    content=StringIO(raw.decode("utf-8"))
    rows=list(csv.DictReader(content))
    for row in rows:
        if row["nav_date"] in DATES:
            row["nav_per_unit"]=versions[DATES.index(row["nav_date"])]
    rows=[r for r in rows if r["nav_date"] not in missing]
    out=StringIO()
    writer=csv.DictWriter(out,fieldnames=a.REQUIRED_COLUMNS)
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue().encode("utf-8"),days


class TestOfficialAllianzCorrections(unittest.TestCase):
    def test_true_issuer_notice_prices(self):
        self.assertEqual(c.EVENTS["2026-02-26"],("490.11","493.76"))
        self.assertEqual(c.EVENTS["2026-03-02"],("481.62","485.27"))
        self.assertIn("allianzgi.com",c.ISSUER_NOTICE)

    def test_verified_corrected_values_accept_only_provisional_calendar_pass(self):
        body,days=official_csv()
        meta,sessions=bundle(body,days,isin=ALLIANZ,currency="TWD")
        result=a.audit_history(meta,body,sessions,today=TODAY)
        self.assertTrue(result["official_nav_revision_gate_passed"])
        self.assertEqual(result["official_nav_correction_dates_expected"],2)
        self.assertEqual(result["official_nav_correction_dates_checked"],2)
        self.assertEqual(result["status"],"PROVISIONAL_HISTORY_SOURCE_COVERAGE_ONLY")
        self.assertFalse(result["ready_for_total_return"])
        self.assertFalse(result["ready_for_portfolio"])
        self.assertFalse(result["database_modified"])

    def test_old_unadjusted_issuer_nav_fails_even_when_3yr_calendar_complete(self):
        body,days=official_csv(versions=("490.11","481.62"))
        meta,sessions=bundle(body,days,isin=ALLIANZ,currency="TWD")
        result=a.audit_history(meta,body,sessions,today=TODAY)
        self.assertEqual(result["official_nav_correction_outdated"],2)
        self.assertFalse(result["official_nav_revision_gate_passed"])
        self.assertFalse(result["calendar_coverage_provisional_pass"])
        self.assertIn("OUTDATED_BEFORE_OFFICIAL_NAV_CORRECTION",result["blockers"])
        self.assertEqual(result["status"],"HISTORY_SOURCE_NEEDS_REVIEW")

    def test_unknown_wrong_nav_fails_and_never_auto_fixes(self):
        body,days=official_csv(versions=("500.13","485.27"))
        meta,sessions=bundle(body,days,isin=ALLIANZ,currency="TWD")
        result=a.audit_history(meta,body,sessions,today=TODAY)
        self.assertEqual(result["official_nav_correction_conflicting"],1)
        self.assertFalse(result["calendar_coverage_provisional_pass"])
        self.assertFalse(result["database_modified"])
        self.assertIn("OFFICIAL_REVISED_NAV_VALUE_CONFLICT",result["blockers"])

    def test_missing_corrected_day_blocks_even_if_self_submitted_dates_omit_day(self):
        body,days=official_csv(missing=("2026-02-26",))
        days=[d for d in days if d!="2026-02-26"]
        meta,sessions=bundle(body,days,isin=ALLIANZ,currency="TWD")
        result=a.audit_history(meta,body,sessions,today=TODAY)
        self.assertEqual(result["missing_expected_nav_count"],0)
        self.assertEqual(result["official_nav_correction_missing"],1)
        self.assertIn("OFFICIAL_CORRECTION_NAV_DATE_MISSING",result["blockers"])
        self.assertFalse(result["calendar_coverage_provisional_pass"])

    def test_other_fund_same_dates_not_checked_against_allianz_prices(self):
        r=c.compare_official_corrections("LU2156566916","USD",{
            date(2026,2,26):Decimal("490.11"),date(2026,3,2):Decimal("481.62")})
        self.assertFalse(r["relevant"])
        self.assertEqual(r["expected_correction_count"],0)
        self.assertTrue(r["all_in_range_corrections_valid"])

    def test_allianz_explicit_short_interval_does_not_claim_three_years(self):
        r=c.compare_official_corrections(ALLIANZ,"TWD",{
            date(2023,1,1):Decimal("10"),date(2023,1,2):Decimal("10.2")})
        self.assertTrue(r["relevant"])
        self.assertEqual(r["expected_correction_count"],0)

    def test_public_notice_requires_exact_official_isin(self):
        classrow={
            "uid":"2d4e6ba412b83aa270a3","name":"安聯台灣科技基金",
            "code":"DDO04","isin":"","currency":"TWD",
            "category":"domestic_fund_share_class",
            "source":"sitca_domestic_funds"
        }
        detail=c.correction_notice_for(classrow["uid"],chooser=lambda uid:classrow)
        self.assertEqual(detail["status"],"ISSUER_CORRECTION_NOTICE_RESEARCH_EVIDENCE")
        self.assertEqual(detail["event_count"],2)
        self.assertFalse(detail["three_year_history_ready"])
        self.assertEqual(detail["history_source_rows_checked"],0)

    def test_alternate_share_class_never_gets_allianz_notice(self):
        row={"uid":"other","name":"安聯台灣科技基金－另一級別",
             "code":"DDD05","isin":"","currency":"EUR",
             "category":"domestic_fund_share_class"}
        detail=c.correction_notice_for("other",chooser=lambda uid:row)
        self.assertEqual(detail["status"],"OFFICIAL_NAV_CORRECTION_NOT_MAPPED")
        self.assertFalse(detail["official_adjustment_evidence_available"])



class TestCorrectionHttp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from http.server import ThreadingHTTPServer
        from threading import Thread
        from adviser_portal.server import Handler
        cls.server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        cls.url=f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.thread=Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)

    def test_issuer_correction_api_has_no_history_prices(self):
        from urllib.request import urlopen
        from unittest.mock import patch
        result={
            "status":"ISSUER_CORRECTION_NOTICE_RESEARCH_EVIDENCE",
            "instrument_uid":"2d4e6ba412b83aa270a3",
            "event_count":2,
            "history_source_rows_checked":0,
            "three_year_history_ready":False,
            "ready_for_total_return":False,
            "usable_for_portfolio":False,
        }
        with patch("adviser_portal.server.correction_notice_for",return_value=result) as fn:
            with urlopen(self.url+
                         "/api/fund-nav-corrections?uid=2d4e6ba412b83aa270a3",timeout=3) as response:
                import json
                actual=json.load(response)
        fn.assert_called_once_with("2d4e6ba412b83aa270a3")
        self.assertFalse(actual["three_year_history_ready"])
        self.assertNotIn("annualized_performance",actual)

    def test_issuer_api_invalid_request_rejected(self):
        from urllib.request import urlopen
        from urllib.error import HTTPError
        for bad in ("?uid=x&uid=y","?uid=x&source=http://evil.invalid","?uid=",""):
            with self.subTest(bad=bad),self.assertRaises(HTTPError) as cm:
                urlopen(self.url+"/api/fund-nav-corrections"+bad,timeout=3)
            self.assertEqual(cm.exception.code,400)
            cm.exception.close()

    def test_ui_displays_official_correction_without_edit(self):
        from pathlib import Path
        js=(Path(__file__).resolve().parents[1]/"static"/"app.js").read_text("utf-8")
        self.assertIn('"/api/fund-nav-corrections"',js)
        self.assertIn("查看安聯歷史 NAV 官方更正",js)
        self.assertIn("report.history_source_rows_checked!==0",js)
        self.assertIn("三年資料尚未匯入",js)


if __name__=="__main__":
    unittest.main()
