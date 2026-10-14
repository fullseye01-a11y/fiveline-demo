"""Backfill planner tests exercise missing-only, caps, and strict calendar proof."""
import csv
from datetime import datetime,timezone
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from tools.official_history_backfill_plan import plan

TW='https://www.twse.com.tw/zh/trading/holiday.html'
TP='https://www.tpex.org.tw/zh-tw/index.html'

def write(path, cols, data):
    with path.open('w',newline='',encoding='utf-8') as f:
        writer=csv.writer(f);writer.writerow(cols);writer.writerows(data)

class BackfillTest(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        p=Path(self.tmp.name);self.db=p/'bars.sqlite';self.calendar=p/'days.csv';self.windows=p/'windows.csv';self.excepts=p/'except.csv'
        with sqlite3.connect(self.db) as con:
            con.execute('CREATE TABLE quote_raw(market TEXT,symbol TEXT,trade_date TEXT,open TEXT,high TEXT,low TEXT,close TEXT,volume_shares TEXT,turnover_twd TEXT,price_adjustment TEXT)')
            con.executemany('INSERT INTO quote_raw VALUES (?,?,?,?,?,?,?,?,?,?)', [
                ('TWSE','2330','2026-09-01','100','105','99','102','1000','100000','RAW_NOT_ADJUSTED'),
                ('TPEX','6488','2026-09-01','100','105','99','102','1000','100000','RAW_NOT_ADJUSTED')])
        write(self.calendar,['market','date','is_open','evidence_url'],[
            ('TWSE','2026-09-01','true',TW),('TWSE','2026-09-02','true',TW),
            ('TWSE','2026-09-03','true',TW),('TPEX','2026-09-01','true',TP),
            ('TPEX','2026-09-02','true',TP),('TPEX','2026-09-03','false',TP)])
        write(self.windows,['market','symbol','start_date','end_date','evidence_url'],[
            ('TWSE','2330','2026-09-01','2026-09-03',TW),
            ('TPEX','6488','2026-09-01','2026-09-03',TP)])
        self.now=datetime(2026,10,14,tzinfo=timezone.utc)
    def run_plan(self,limit=20,except_path=None):
        return plan(self.db,self.calendar,self.windows,except_path,limit,self.now)
    def test_month_and_day_grouping(self):
        r=self.run_plan()
        self.assertEqual(r['missing_raw_quote_instrument_days'],3)
        self.assertEqual(r['total_work_units'],2)
        self.assertEqual({x['unit_id'] for x in r['work_units']},{'TWSE:2330:2026-09','TPEX:2026-09-02'})
        self.assertFalse(r['private_quote_values_exported'])
    def test_request_cap_never_disables_missing_audit(self):
        r=self.run_plan(limit=1)
        self.assertEqual(r['total_work_units'],2)
        self.assertEqual(r['work_units_returned'],1)
        with self.assertRaisesRegex(ValueError,'limit'):
            self.run_plan(limit=101)
    def test_no_calendar_inference(self):
        write(self.calendar,['market','date','is_open','evidence_url'],[
            ('TWSE','2026-09-01','true',TW),('TPEX','2026-09-01','true',TP)])
        r=self.run_plan()
        self.assertEqual(r['total_work_units'],0)
        self.assertEqual(len(r['blocked_instrument_windows']),2)
    def test_excused_halting_date_is_not_planned(self):
        write(self.excepts,['market','symbol','date','reason','evidence_url'],[
            ('TPEX','6488','2026-09-02','trading_halt',TP)])
        r=self.run_plan(except_path=self.excepts)
        self.assertEqual(r['total_work_units'],1)
        self.assertEqual(r['missing_raw_quote_instrument_days'],2)
    def test_replay_only_missing_sessions(self):
        with sqlite3.connect(self.db) as con:
            con.execute('INSERT INTO quote_raw VALUES (?,?,?,?,?,?,?,?,?,?)',
                        ('TPEX','6488','2026-09-02','102','104','101','103','500','50000','RAW_NOT_ADJUSTED'))
        r=self.run_plan()
        self.assertEqual(r['total_work_units'],1)
        self.assertEqual(r['missing_raw_quote_instrument_days'],2)

if __name__=='__main__':unittest.main()
