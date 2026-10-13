"""Tests for historical NAV candidate archive. No provider calls or history writes."""
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tools.nav_daily_review_snapshot import checked_report, write_archive, extract_candidates, csv_safe

def fake():
    rows=[]
    for i in range(1,69):
        c={"name":"基金A","code":"A001","isin":"LU0000000000",
           "currency":"USD","nav":"10.1","nav_date":"2026-10-13","source":"tdcc_offshore_global"}
        is_single=i<=39
        status=("single_unverified_candidate" if is_single else
                "no_exact_candidate" if i<=53 else
                "separate_exchange_feed_or_identity_required")
        rows.append({"id":str(i),"name":"基金"+str(i),"currency":"USD",
                     "nav_end":"2026-08-31","status":status,
                     "candidate_count":1 if is_single else 0,
                     "candidates":[c] if is_single else [],
                     "approved":False})
    return {"mode":"read_only_matching_review","legacy_count":68,
            "records":rows,"provider_success":["sitca_domestic_daily","tdcc_offshore_global"],
            "provider_errors":{},"reviewed_and_approved_count":0,
            "any_legacy_nav_overwritten":False,"candidate_status_counts":{
               "single_unverified_candidate":39,"no_exact_candidate":14,
               "separate_exchange_feed_or_identity_required":15
            },"checked_at_utc":"2026-10-13T13:00:00+00:00"}

class ArchiveTests(unittest.TestCase):
    def test_all_68_unapproved(self):
        report=checked_report(fake())
        records=extract_candidates(report)
        self.assertEqual(len(records),68)
        self.assertEqual(sum(bool(r["official_code"]) for r in records),39)
        self.assertTrue(all(r["approved"]=="NO" for r in records))
        self.assertFalse(checked_report(report)["any_legacy_nav_overwritten"])

    def test_reject_unverified_source_or_approval(self):
        report=fake();report["records"][0]["approved"]=True
        with self.assertRaisesRegex(ValueError,"unapproved"):
            checked_report(report)
        report=fake();report["provider_success"]=["tdcc_offshore_global"]
        with self.assertRaisesRegex(ValueError,"Both official"):
            checked_report(report)

    def test_write_csv_and_json_with_snapshot_metadata(self):
        with TemporaryDirectory() as tmp:
            jp,cp=write_archive(fake(),Path(tmp),datetime(2026,10,13,13,0,tzinfo=timezone.utc))
            obj=json.loads(jp.read_text("utf-8"))
            self.assertEqual(len(obj["rows"]),68)
            self.assertFalse(obj["historical_nav_backfilled"])
            self.assertTrue(obj["all_candidates_unapproved"])
            with cp.open(encoding="utf-8-sig",newline="") as fp:
                csv_rows=list(csv.DictReader(fp))
            self.assertEqual(len(csv_rows),68)
            self.assertEqual(csv_rows[0]["official_nav"],"10.1")
            self.assertEqual(csv_rows[-1]["official_nav"],"")

    def test_csv_formula_injection_guard(self):
        self.assertEqual(csv_safe("=SUM(1,1)"),"'=SUM(1,1)")
        self.assertEqual(csv_safe("-1"),"'-1")
        self.assertEqual(csv_safe("NAV"),"NAV")

if __name__=="__main__":
    unittest.main()
