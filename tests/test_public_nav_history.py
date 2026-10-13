"""Public official NAV archive tests. No customer positions or original Excel writes."""
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import sqlite3
import unittest

from tools.public_nav_history import build,write_snapshot
from tools.rebuild_public_nav_sqlite import rebuild

def sample():
    records=[]
    for idx in range(1,69):
        one=idx<=39
        candidate={"source":"sitca_domestic_daily","name":"公資料基金"+str(idx),
                   "code":"GOV"+str(idx),"isin":"","currency":"TWD",
                   "nav_date":"2026-10-13","nav":str(idx+10)}
        records.append({"id":str(idx),"approved":False,
                        "status":"single_unverified_candidate" if one else "no_exact_candidate",
                        "candidate_count":1 if one else 0,
                        "candidates":[candidate] if one else []})
    return {
      "mode":"read_only_matching_review","legacy_count":68,
      "records":records,"reviewed_and_approved_count":0,"any_legacy_nav_overwritten":False,
      "provider_success":["sitca_domestic_daily","tdcc_offshore_global"],
      "provider_errors":{},"checked_at_utc":"2026-10-13T13:30:00+00:00"
    }

class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.when=datetime(2026,10,13,13,32,tzinfo=timezone.utc)

    def build(self,rr=None,run="123"):
        return build(rr or sample(),created=self.when,run_id=run)

    def test_screens_39_candidates_no_approval_and_retains_source(self):
        out=self.build()
        self.assertEqual(out["candidate_count"],39)
        self.assertEqual(out["collection_local_date"],"2026-10-13")
        self.assertFalse(out["all_candidates_approved"])
        self.assertFalse(out["legacy_history_modified"])
        self.assertEqual(len(out["observations_sha256"]),64)
        self.assertTrue(all(x["identity_status"]=="name_currency_candidate_unapproved" for x in out["observations"]))
        self.assertIn("data.gov.tw",out["provenance"]["license"])

    def test_rejects_approved_ambiguous_and_invalid_nav(self):
        q=sample();q["records"][0]["approved"]=True
        with self.assertRaises(ValueError):build(q,self.when,"123")
        q=sample();q["records"][0]["candidate_count"]=2
        with self.assertRaisesRegex(ValueError,"ambiguous"):build(q,self.when,"123")
        q=sample();q["records"][0]["candidates"][0]["nav"]="-12"
        with self.assertRaisesRegex(ValueError,"positive"):build(q,self.when,"123")

    def test_snapshot_immutable_and_versioned_sqlite_rebuild(self):
        with TemporaryDirectory() as temp:
            basedir=Path(temp)
            a=self.build()
            p1=write_snapshot(a,basedir/"public_history")
            self.assertTrue(p1.is_file())
            self.assertEqual(p1,write_snapshot(a,basedir/"public_history"))
            out=basedir/"fund_history.sqlite"
            counts=rebuild([p1],out)
            self.assertEqual(counts["archives"],1)
            self.assertEqual(counts["official_funds"],39)
            self.assertEqual(counts["nav_raw_history"],39)
            self.assertEqual(counts["identity_approved"],0)
            with sqlite3.connect(out) as con:
                self.assertEqual(con.execute("select count(*) from nav_raw_history where identity_review!='PENDING'").fetchone()[0],0)
                self.assertEqual(con.execute("select count(*) from official_funds where approval_status!='UNAPPROVED'").fetchone()[0],0)
            with self.assertRaises(FileExistsError):rebuild([p1],out)

    def test_second_collection_same_nav_day_is_not_new_history(self):
        with TemporaryDirectory() as temp:
            basedir=Path(temp)
            p1=write_snapshot(self.build(run="123"),basedir/"history")
            p2=write_snapshot(self.build(run="124"),basedir/"history")
            counts=rebuild([p1,p2],basedir/"two.sqlite")
            self.assertEqual(counts["archives"],2)
            self.assertEqual(counts["official_funds"],39)
            self.assertEqual(counts["nav_raw_history"],39)

    def test_conflicting_nav_revision_does_not_publish_database(self):
        with TemporaryDirectory() as temp:
            basedir=Path(temp)
            p1=write_snapshot(self.build(run="123"),basedir/"history")
            b=sample();b["records"][0]["candidates"][0]["nav"]="987654"
            p2=write_snapshot(self.build(rr=b,run="125"),basedir/"history")
            target=basedir/"conflicted.sqlite"
            with self.assertRaisesRegex(ValueError,"revision/conflict"):
                rebuild([p1,p2],target)
            self.assertFalse(target.exists())

    def test_fingerprint_tampering_is_rejected(self):
        with TemporaryDirectory() as temp:
            basedir=Path(temp)
            item=self.build()
            p=write_snapshot(item,basedir/"history")
            bad=json.loads(p.read_text("utf-8"))
            bad["observations"][0]["nav_raw"]="999"
            p.write_text(json.dumps(bad,ensure_ascii=False),encoding="utf-8")
            with self.assertRaisesRegex(ValueError,"SHA-256"):
                rebuild([p],basedir/"tampered.sqlite")

if __name__=="__main__":unittest.main()
