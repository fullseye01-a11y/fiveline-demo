"""Offline guarded six-row TPEx Neon archive transaction validation."""
from datetime import date
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest.mock import MagicMock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import tpex_three_day_guarded_neon as m

PRE={"approved_bars":66,"pending_review":0,"revisions":0}
POST={"approved_bars":72,"pending_review":0,"revisions":0}
DAYS=("2026-10-06","2026-10-07","2026-10-08","2026-10-12","2026-10-13")
MARKERS={"2026-09-25","2026-09-28","2026-10-09","2026-10-26"}

def dates():
    return [("TPEX",symbol,date.fromisoformat(day)) for symbol in m.PILOT_CODES for day in DAYS]

def official_fixture(roc_date,*,drop=None,bad=False):
    rows=[
        ["6488","200","205","198","202","1000","202000"],
        ["8069","100","102","99","101","1000","101000"],
    ]
    if drop:
        rows=[row for row in rows if row[0]!=drop]
    if bad:
        rows[0][2]="80"
    return {
        "reportDate":roc_date,
        "tables":[{"fields":["代號","開盤","最高","最低","收盤","成交股數","成交金額(元)"],
                   "data":rows}],
    }

class GuardedBackfillTests(unittest.TestCase):
    def setUp(self):
        self.calls=[]
        self.staged=0
        self.committed=0
        self.archive_path=None
        self.after=False
    def read_dates(self,dsn):
        self.assertEqual(dsn,"mock-private-neon")
        return dates()
    def summary(self,*,dsn):
        return POST if self.after else PRE
    def quotes(self,url,params):
        self.calls.append(params["d"])
        return official_fixture(params["d"])
    def archive(self,path,*,dsn):
        self.assertEqual(dsn,"mock-private-neon")
        self.archive_path=path
        with sqlite3.connect(f"file:{path.resolve()}?mode=ro",uri=True) as con:
            self.staged=con.execute("SELECT COUNT(*) FROM approved_bars").fetchone()[0]
            self.assertEqual(self.staged,6)
        self.after=True
        self.committed+=1
        return {"incoming":6,"inserted":6,"unchanged":0,"pending":0}
    def execute(self,*,summary=None,read_dates=None,quotes=None,backup=None,pdf_digest=None,archive=None):
        with patch.object(m,"extract_official_pdf",return_value="mocked checked document"),\
             patch.object(m,"validate_pdf_text",return_value=MARKERS):
            return m.run_guarded_import(
                "mock-private-neon",backup_proof=backup or m.BACKUP_BRANCH,
                read_dates=read_dates or self.read_dates,
                fetch_pdf=lambda:(b"%PDF-mock",pdf_digest or m.SOURCE_SHA256_20261018),
                fetch_quotes=quotes or self.quotes,
                summarize=summary or self.summary,
                archive=archive or self.archive,
                pause=lambda _:None,
            )
    def test_sequential_three_day_six_row_commit(self):
        r=self.execute()
        self.assertEqual(self.calls,["115/10/01","115/10/02","115/10/05"])
        self.assertEqual(self.committed,1)
        self.assertEqual(self.staged,6)
        self.assertTrue(r["temporary_quote_spool_deleted"])
        self.assertFalse(self.archive_path.exists())
        self.assertFalse(r["public_market_quotes_distributed"])
        self.assertEqual(r["neon_approved_after"],72)
    def test_missing_backup_proof_refuses_any_access(self):
        quotes=MagicMock()
        with self.assertRaisesRegex(ValueError,"backup"):
            self.execute(backup="not-verified",quotes=quotes)
        quotes.assert_not_called()
        self.assertEqual(self.committed,0)
    def test_baseline_approved_count_fails(self):
        quotes=MagicMock()
        with self.assertRaisesRegex(ValueError,"pristine"):
            self.execute(summary=lambda **k:dict(PRE,approved_bars=65),quotes=quotes)
        quotes.assert_not_called()
    def test_baseline_pending_revision_fails(self):
        with self.assertRaisesRegex(ValueError,"pristine"):
            self.execute(summary=lambda **k:dict(PRE,pending_review=1))
        self.assertEqual(self.committed,0)
    def test_source_pdf_modified_refuses_quote_fetch(self):
        quotes=MagicMock()
        with self.assertRaisesRegex(ValueError,"PDF changed"):
            self.execute(pdf_digest="different",quotes=quotes)
        quotes.assert_not_called()
    def test_gap_candidates_changed_refuses_write(self):
        r=dates()+[("TPEX","6488",date(2026,10,1))]
        with self.assertRaisesRegex(ValueError,"date identity changed"):
            self.execute(read_dates=lambda dsn:r)
        self.assertEqual(self.committed,0)
    def test_missing_source_security_halts_atomic(self):
        def bad(url,params):
            return official_fixture(params["d"],drop="8069")
        with self.assertRaisesRegex(ValueError,"incomplete or invalid"):
            self.execute(quotes=bad)
        self.assertEqual(self.committed,0)
    def test_invalid_price_halts_atomic(self):
        def bad(url,params):
            return official_fixture(params["d"],bad=True)
        with self.assertRaisesRegex(ValueError,"incomplete or invalid"):
            self.execute(quotes=bad)
        self.assertEqual(self.committed,0)
    def test_second_preflight_detects_parallel_mutation(self):
        count=[0]
        def summary(**kwargs):
            count[0]+=1
            return PRE if count[0]==1 else POST
        with self.assertRaisesRegex(ValueError,"pristine"):
            self.execute(summary=summary)
        self.assertEqual(self.committed,0)
    def test_post_commit_mismatch_disables_retry(self):
        def broken_archive(path,*,dsn):
            self.after=True
            return {"incoming":6,"inserted":5,"unchanged":1,"pending":0}
        with self.assertRaisesRegex(RuntimeError,"do NOT auto retry"):
            self.execute(archive=broken_archive)
    def test_no_neon_dsn_no_calls(self):
        fetch=MagicMock()
        with self.assertRaisesRegex(RuntimeError,"Neon"):
            m.run_guarded_import(None,backup_proof=m.BACKUP_BRANCH,fetch_pdf=fetch)
        fetch.assert_not_called()
    def test_no_raw_ohlcv_in_public_report(self):
        report=self.execute()
        txt=str(report)
        self.assertNotIn("volume_shares",txt)
        self.assertNotIn("turnover_twd",txt)
        self.assertNotIn("mock-private-neon",txt)

if __name__=="__main__":
    unittest.main()
