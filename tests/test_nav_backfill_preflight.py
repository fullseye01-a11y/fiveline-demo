"""Production NAV must be blocked even after offline, synthetic evidence passes staging."""
import csv
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tools.nav_backfill_preflight import make_report,legacy_rows,check_bundle,SCHEMA

def test_coverage():
    return {str(i):{"標的ID":str(i),"標的名稱":"基金"+str(i),
                     "幣別":"USD","NAV截至日期":"2026-08-31"} for i in range(1,69)}

def quality():
    return {
      "schema":"public_nav_archive_quality_v1","legacy_total":68,
      "checked_at_utc":"2026-10-13T20:48:41+00:00",
      "approved_funds":0,
      "historical_backfill_completed":False,
      "dividend_split_adjustment_completed":False,
      "calendar_span_is_not_missing_trading_days":True,
      "funds":[{
          "legacy_id":"1","legacy_name":"基金1","official_source":"tdcc_offshore_global",
          "official_code":"TEST123","official_isin":"US0378331005",
          "official_currency":"USD","distinct_effective_nav_dates":2,
          "last_effective_nav_date":"2026-10-13","approved":False,
          "dividend_split_adjusted":False,"conflicting_nav_dates":[],
          "identity_changed_between_archives":False,
        }]
    }

def evidence():
    return {
     "schema":"fiveline_private_nav_evidence_v1",
     "instruments":[{
        "legacy_id":"1","official_source":"tdcc_offshore_global","official_code":"TEST123",
        "official_isin":"US0378331005","official_currency":"USD",
        "original_excel_identifier":"Independent Excel ID 1",
        "share_class":"Class A distribution",
        "distribution_policy":"distributing",
        "independent_class_evidence_url":"https://example.org/official-prospectus",
        "reviewer":"Verified reviewer","reviewed_at":"2026-10-14",
        "historical_nav_source_url":"https://example.org/official-daily-history",
        "verified_valuation_dates":["2026-09-01","2026-10-13"],
        "historical_nav":[{"nav_date":"2026-09-01","nav":"100.25"},
                          {"nav_date":"2026-10-13","nav":"104.8"}],
        "dividend_events":[{"ex_date":"2026-09-15","cash_per_unit":"0.34",
                           "currency":"USD","official_evidence_url":"https://example.org/distributions"}],
        "split_events":[],
        "reconciliation_document_url":"https://example.org/independent-review",
        "identity_approved":True,"share_class_approved":True,
        "nav_source_license_approved":True,
        "distribution_coverage_confirmed":True,"split_coverage_confirmed":True,
        "history_reconciled":True,"performance_reconciled":True
    }]
    }

class GateTests(unittest.TestCase):
    def test_live_without_private_evidence_blocks_all(self):
        report=make_report(quality(),test_coverage())
        self.assertEqual(report["schema"],SCHEMA)
        self.assertEqual(report["legacy_total"],68)
        self.assertEqual(report["staging_eligible_count"],0)
        self.assertEqual(report["status_counts"]["blocked"],68)
        self.assertFalse(report["production_backfill_authorized"])
        self.assertTrue(report["read_only_report"])
        self.assertFalse(report["production_excel_changed"])
        self.assertEqual(report["funds"][1]["blockers"][0],"NO_OFFICIAL_CANDIDATE")

    def test_synthetic_complete_private_bundle_is_staging_only(self):
        report=make_report(quality(),test_coverage(),evidence())
        self.assertEqual(report["staging_eligible_count"],1)
        self.assertEqual(report["funds"][0]["status"],"staging_eligible_manual_signoff_required")
        self.assertFalse(report["funds"][0]["eligible_for_production_backfill"])
        self.assertFalse(report["production_backfill_authorized"])
        dumped=json.dumps(report)
        self.assertNotIn("Verified reviewer",dumped)
        self.assertNotIn("example.org",dumped)
        self.assertNotIn("0.34",dumped)

    def test_missing_unit_dividend_proof_and_calendar_block(self):
        manifest=evidence()
        a=manifest["instruments"][0]
        a["dividend_events"]=[{"ex_date":"2026-09-15","cash_per_unit":"0.34",
            "currency":"TWD","official_evidence_url":"https://example.org"}]
        a["historical_nav"]=a["historical_nav"][:1]
        row=make_report(quality(),test_coverage(),manifest)["funds"][0]
        self.assertIn("INVALID_DIVIDEND_EVENT_OR_CURRENCY",row["blockers"])
        self.assertIn("UNVERIFIED_OR_MISSING_EXPECTED_NAV_DATES",row["blockers"])
        self.assertIn("NAV_SERIES_NOT_CONTINUOUS_THROUGH_LATEST_SNAPSHOT",row["blockers"])

    def test_unreviewed_split_and_negative_nav_rejected(self):
        m=evidence()["instruments"][0]
        m["split_events"]=None
        m["historical_nav"][1]["nav"]="-100"
        row=check_bundle(m,quality()["funds"][0],test_coverage()["1"])
        self.assertIn("SPLIT_EVENTS_NOT_REVIEWED",row)
        self.assertIn("INVALID_HISTORICAL_NAV",row)

    def test_fund_identity_or_same_day_official_conflict_never_ready(self):
        q=quality()
        q["funds"][0]["conflicting_nav_dates"]=["2026-10-13"]
        q["funds"][0]["identity_changed_between_archives"]=True
        row=make_report(q,test_coverage(),evidence())["funds"][0]
        self.assertIn("SAME_DATE_NAV_CONFLICT",row["blockers"])
        self.assertIn("OFFICIAL_IDENTITY_DRIFT",row["blockers"])

    def test_duplicate_quality_fund_ids_rejected(self):
        q=quality()
        q["funds"].append(dict(q["funds"][0]))
        with self.assertRaisesRegex(ValueError,"repeated legacy"):
            make_report(q,test_coverage())

    def test_evidence_unknown_id_rejected(self):
        m=evidence()
        m["instruments"][0]["legacy_id"]="999"
        with self.assertRaisesRegex(ValueError,"unknown legacy"):
            make_report(quality(),test_coverage(),m)

    def test_invalid_original_coverage_rejected(self):
        with TemporaryDirectory() as d:
            p=Path(d)/"coverage.csv"
            with p.open("w",encoding="utf-8-sig",newline="") as f:
                w=csv.DictWriter(f,fieldnames=["標的ID","標的名稱","幣別","NAV截至日期"])
                w.writeheader()
                for i in range(1,68):w.writerow(test_coverage()[str(i)])
            with self.assertRaisesRegex(ValueError,"68 unique"):
                legacy_rows(p)

if __name__=="__main__":
    unittest.main()
