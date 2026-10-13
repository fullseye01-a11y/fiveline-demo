"""Assure official NAV audit separates capture events from unique NAV effective dates."""
from copy import deepcopy
from datetime import datetime,timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import csv
import unittest

from tools.public_nav_history import build,write_snapshot
from tools.audit_public_nav_quality import assess,read_verified_archive

def fixture():
    records=[]
    for i in range(1,69):
        one=i<=2
        records.append({
            "id":str(i),"name":"基金"+str(i),"currency":"USD","nav_end":"2026-08-31",
            "approved":False,
            "status":"single_unverified_candidate" if one else "no_exact_candidate",
            "candidate_count":1 if one else 0,
            "candidates":[{"source":"tdcc_offshore_global",
                "code":"ABC"+str(i),"isin":"","currency":"USD",
                "name":"基金"+str(i),"nav_date":"2026-10-09","nav":str(10+i)}] if one else []
        })
    return {
        "mode":"read_only_matching_review","legacy_count":68,
        "records":records,"provider_success":["sitca_domestic_daily","tdcc_offshore_global"],
        "provider_errors":{},"reviewed_and_approved_count":0,
        "any_legacy_nav_overwritten":False,
        "checked_at_utc":"2026-10-14T05:00:00+00:00"
    }

def coverage(path:Path):
    with path.open("w",encoding="utf-8-sig",newline="") as fp:
        writer=csv.DictWriter(fp,fieldnames=["標的ID","標的名稱","NAV截至日期"])
        writer.writeheader()
        for i in range(1,69):
            writer.writerow({"標的ID":str(i),"標的名稱":"基金"+str(i),
                             "NAV截至日期":"2026-08-31"})

class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir=Path(self.temp.name)
        self.data=self.dir/"public_nav_history"
        self.coverage=self.dir/"coverage.csv"
        coverage(self.coverage)
        self.when=datetime(2026,10,14,5,tzinfo=timezone.utc)

    def archive(self,report=None,run="123"):
        obj=build(report or fixture(),created=self.when,run_id=run)
        return write_snapshot(obj,self.data)

    def audit(self):
        return assess(list(self.data.glob("*/*/*.json")),self.coverage,self.when)

    def test_repeated_collection_has_not_backfilled_two_days(self):
        self.archive(run="123")
        self.archive(run="124")
        r=self.audit()
        self.assertEqual(r["archive_snapshot_files"],2)
        self.assertEqual(r["total_collection_observations"],4)
        self.assertEqual(r["unique_fund_effective_date_pairs"],2)
        self.assertEqual(r["repeated_collection_observations"],2)
        self.assertEqual(r["distinct_effective_nav_dates_all_funds"],1)
        self.assertEqual(r["legacy_with_public_candidate_observations"],2)
        self.assertEqual(r["approved_funds"],0)
        self.assertFalse(r["historical_backfill_completed"])
        self.assertTrue(r["calendar_span_is_not_missing_trading_days"])
        self.assertEqual(r["funds"][0]["quality"],"only_one_distinct_nav_date")

    def test_next_nav_effective_date_counts_as_new_one(self):
        self.archive(run="123")
        other=fixture()
        other["records"][0]["candidates"][0]["nav_date"]="2026-10-12"
        self.archive(report=other,run="125")
        r=self.audit()
        self.assertEqual(r["unique_fund_effective_date_pairs"],3)
        self.assertEqual(r["funds"][0]["distinct_effective_nav_dates"],2)
        self.assertEqual(r["funds"][1]["distinct_effective_nav_dates"],1)
        self.assertEqual(r["funds"][0]["quality"],"multiple_nav_dates_unapproved")

    def test_same_date_different_values_are_explicit_blocker(self):
        self.archive(run="123")
        other=fixture()
        other["records"][0]["candidates"][0]["nav"]="999"
        self.archive(report=other,run="125")
        r=self.audit()
        self.assertEqual(r["same_day_value_conflict_funds"],1)
        self.assertEqual(r["funds"][0]["quality"],"conflicting_same_date_nav_blocked")
        self.assertIsNone(r["funds"][0]["latest_raw_nav"])
        self.assertFalse(r["historical_backfill_completed"])

    def test_changed_official_code_does_not_auto_map(self):
        self.archive(run="123")
        other=fixture()
        other["records"][0]["candidates"][0]["code"]="DIFFERENT-CODE"
        self.archive(report=other,run="125")
        r=self.audit()
        self.assertTrue(r["identity_drift_funds"])
        x=[f for f in r["funds"] if f["legacy_id"]=="1"]
        self.assertEqual(len(x),2)
        self.assertTrue(all(i["quality"]=="fund_identity_drift_blocked" for i in x))

    def test_corrupt_hash_blocks_report(self):
        path=self.archive()
        from json import loads,dumps
        obj=loads(path.read_text("utf-8"))
        obj["observations"][0]["nav_raw"]="321"
        path.write_text(dumps(obj,ensure_ascii=False),encoding="utf-8")
        with self.assertRaisesRegex(ValueError,"SHA-256"):
            self.audit()

    def test_approved_snapshot_rejected(self):
        path=self.archive()
        from json import loads,dumps
        obj=loads(path.read_text("utf-8"))
        obj["all_candidates_approved"]=True
        path.write_text(dumps(obj,ensure_ascii=False),encoding="utf-8")
        with self.assertRaisesRegex(ValueError,"approved archive"):
            self.audit()

    def test_missing_or_corrupt_excel_coverage_rejected(self):
        self.archive()
        self.coverage.write_text("標的ID,標的名稱,NAV截至日期\n1,foo,2026-08-31\n",encoding="utf-8")
        with self.assertRaisesRegex(ValueError,"exactly 68"):
            self.audit()

if __name__=="__main__":
    unittest.main()
