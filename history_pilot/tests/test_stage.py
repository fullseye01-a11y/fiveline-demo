import sqlite3
import tempfile
import unittest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from postgres_store import plan_revision, persist_sqlite
from gap_audit import observed_gap_candidates

class TestPostgresStaging(unittest.TestCase):
    def test_new_approved(self):
        self.assertEqual(plan_revision(None, True, ''),(True,''))

    def test_existing_always_held(self):
        self.assertEqual(plan_revision((1,'oldfingerprint'),True,''),(False,'data_revised'))

    def test_existing_pending_carries_reason(self):
        self.assertEqual(plan_revision((4,'fingerprint'),False,'unit_mismatch'),(False,'data_revised;unit_mismatch'))

    def test_missing_dsn_fail_closed(self):
        with tempfile.TemporaryDirectory() as path:
            p=Path(path)/'source.db'
            sqlite3.connect(p).close()
            with self.assertRaisesRegex(RuntimeError,'DISABLED'):
                persist_sqlite(p,dsn='')

    def test_interior_peer_gap_only(self):
        with tempfile.TemporaryDirectory() as path:
            f=Path(path)/'data.db'
            with sqlite3.connect(f) as c:
                c.execute('CREATE TABLE approved_bars (market TEXT, code TEXT, day TEXT)')
                c.executemany('INSERT INTO approved_bars VALUES (?,?,?)',[
                   ('TWSE','2330','2026-10-12'),('TWSE','2330','2026-10-14'),
                   ('TWSE','0050','2026-10-12'),('TWSE','0050','2026-10-13'),('TWSE','0050','2026-10-14'),
                   ('TPEX','8069','2026-10-13'),('TPEX','6488','2026-10-13')])
            a=observed_gap_candidates(f)
            self.assertEqual(a['candidate_missing_total'],1)
            self.assertEqual(a['symbols'][0]['code'],'2330')
            self.assertFalse(a['symbols'][0]['confirmed_missing'])

if __name__=='__main__':
    unittest.main()
