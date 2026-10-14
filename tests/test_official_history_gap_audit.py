"""Synthetic coverage/quality/holiday tests: no live exchange data or rights assumptions."""
from __future__ import annotations

from datetime import datetime, timezone
import csv
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from tools.official_history_gap_audit import make_report, quote_rows, official_evidence

TWSE = "https://www.twse.com.tw/zh/trading/historical"


def csv_file(path, header, rows):
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def new_db(path):
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE quote_raw(market TEXT,symbol TEXT,trade_date TEXT,"
                     "open TEXT,high TEXT,low TEXT,close TEXT,volume_shares TEXT,"
                     "turnover_twd TEXT,price_adjustment TEXT)")
        conn.executemany("INSERT INTO quote_raw VALUES (?,?,?,?,?,?,?,?,?,?)", [
            ("TWSE","2330","2026-09-01","100","104","99","102","30000","3000000","RAW_NOT_ADJUSTED"),
            ("TWSE","2330","2026-09-03","102","105","101","104","40000","4000000","RAW_NOT_ADJUSTED")])


class AuditTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.db = self.path / "pilot.sqlite"
        new_db(self.db)
        self.day_csv = self.path / "calendar.csv"
        self.win_csv = self.path / "windows.csv"
        self.exc_csv = self.path / "exc.csv"
        csv_file(self.day_csv, ["market","date","is_open","evidence_url"], [
            ("TWSE","2026-09-01","true",TWSE),
            ("TWSE","2026-09-02","true",TWSE),
            ("TWSE","2026-09-03","true",TWSE),
            ("TWSE","2026-09-04","false",TWSE)])
        csv_file(self.win_csv, ["market","symbol","start_date","end_date","evidence_url"], [
            ("TWSE","2330","2026-09-01","2026-09-04",TWSE)])
        self.now = datetime(2026, 10, 14, tzinfo=timezone.utc)

    def report(self, **k):
        return make_report(self.db,self.day_csv,self.win_csv,now=self.now,**k)

    def test_no_calendar_refuses_certification(self):
        r = make_report(self.db,now=self.now)
        self.assertEqual(r['quote_days_count'],2)
        self.assertEqual(r['instruments'][0]['issues'], ["missing_official_instrument_window","missing_official_trading_calendar"])
        self.assertFalse(r['production_coverage_complete'])
        self.assertNotIn('102',str(r))

    def test_missing_official_session_not_weekday_guess(self):
        r = self.report()
        item = r['instruments'][0]
        self.assertEqual(item['missing_session_dates_sample'],["2026-09-02"])
        self.assertEqual(item['missing_session_count'],1)
        self.assertFalse(item['calendar_continuity_passed'])
        self.assertFalse(r['price_adjustments_applied'])

    def test_official_suspension_evidence_excuses_gap(self):
        csv_file(self.exc_csv, ["market","symbol","date","reason","evidence_url"], [
            ("TWSE","2330","2026-09-02","trading_halt",TWSE)])
        r = self.report(exceptions_path=self.exc_csv)
        item=r['instruments'][0]
        self.assertTrue(item['calendar_continuity_passed'])
        self.assertEqual(item['attested_nontrading_exception_count'],1)
        self.assertTrue(r['calendar_provisional_continuity_passed'])
        self.assertFalse(r['official_calendar_completeness_reviewed'])
        self.assertFalse(r['production_coverage_complete'])

    def test_calendar_incomplete_is_not_healthy(self):
        csv_file(self.day_csv, ["market","date","is_open","evidence_url"], [
            ("TWSE","2026-09-01","true",TWSE),
            ("TWSE","2026-09-03","true",TWSE)])
        r = self.report()
        self.assertEqual(r['instruments'][0]['calendar_unverified_date_count'],2)
        self.assertIn('calendar_interval_not_fully_attested',r['instruments'][0]['issues'])

    def test_invalid_source_and_duplicates_are_rejected(self):
        self.assertFalse(official_evidence('TWSE','https://evil.twse.com.tw/whatever'))
        self.assertFalse(official_evidence('TWSE','http://www.twse.com.tw/a'))
        csv_file(self.day_csv, ["market","date","is_open","evidence_url"], [
            ("TWSE","2026-09-01","true",TWSE),("TWSE","2026-09-01","true",TWSE)])
        with self.assertRaisesRegex(ValueError,'duplicate calendar'):
            self.report()

    def test_bad_ohlcv_rejected(self):
        with sqlite3.connect(self.db) as c:
            c.execute("UPDATE quote_raw SET high='99' WHERE trade_date='2026-09-01'")
        with self.assertRaisesRegex(ValueError,'invalid OHLCV'):
            quote_rows(self.db)

    def test_duplicate_ohlcv_rejected(self):
        with sqlite3.connect(self.db) as c:
            c.execute("INSERT INTO quote_raw SELECT * FROM quote_raw LIMIT 1")
        with self.assertRaisesRegex(ValueError,'duplicate OHLCV date'):
            quote_rows(self.db)

    def test_outside_window(self):
        csv_file(self.win_csv,["market","symbol","start_date","end_date","evidence_url"],[("TWSE","2330","2026-09-02","2026-09-04",TWSE)])
        self.assertEqual(self.report()['instruments'][0]['unexpected_quote_count'],1)

    def test_no_trade_calendar_date_not_expected(self):
        csv_file(self.day_csv, ["market","date","is_open","evidence_url"], [
            ("TWSE","2026-09-01","true",TWSE),("TWSE","2026-09-02","false",TWSE),
            ("TWSE","2026-09-03","true",TWSE),("TWSE","2026-09-04","false",TWSE)])
        item=self.report()['instruments'][0]
        self.assertEqual(item['missing_session_count'],0)
        self.assertTrue(item['calendar_continuity_passed'])

    def test_unattributed_instrument_window_rejected(self):
        csv_file(self.win_csv,["market","symbol","start_date","end_date","evidence_url"],[("TWSE","2330","2026-09-01","2026-09-04","https://example.com")])
        with self.assertRaisesRegex(ValueError,'unattributed'):
            self.report()


if __name__=='__main__':
    unittest.main()
