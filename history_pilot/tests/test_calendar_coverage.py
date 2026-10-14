import csv
from datetime import date,datetime,timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import sys
import unittest
from unittest.mock import MagicMock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from calendar_coverage import (
    audit_dates,load_evidence,neon_read_only_dates
)

TW="https://www.twse.com.tw/zh/trading/holiday.html"

class CalendarGateTests(unittest.TestCase):
    def setUp(self):
        self.records=[
            ("TWSE","2330",date(2026,9,1)),
            ("TWSE","2330",date(2026,9,3)),
            ("TWSE","0050",date(2026,9,1)),
            ("TWSE","0050",date(2026,9,2)),
            ("TWSE","0050",date(2026,9,3)),
        ]
        self.now=datetime(2026,10,14,tzinfo=timezone.utc)
    def write(self,path,fields,rows):
        with path.open("w",encoding="utf-8",newline="") as f:
            w=csv.writer(f);w.writerow(fields);w.writerows(rows)
    def test_peer_candidates_not_official_gaps(self):
        r=audit_dates(self.records,now=self.now)
        self.assertEqual(r["approved_raw_days"],5)
        self.assertEqual(r["peer_observed_gap_candidates"],1)
        self.assertEqual(r["official_session_gaps"],0)
        self.assertFalse(r["provisional_coverage_passed"])
        self.assertFalse(r["official_calendar_completeness_reviewed"])
        self.assertFalse(r["database_mutated"])
        self.assertEqual(r["instruments"][1]["issues"],["MISSING_LISTING_WINDOW_EVIDENCE"])
    def test_missing_session_explicit(self):
        cal={("TWSE",f"2026-09-0{i}"):True for i in range(1,4)}
        win={("TWSE","2330"):("2026-09-01","2026-09-03"),
             ("TWSE","0050"):("2026-09-01","2026-09-03")}
        r=audit_dates(self.records,cal,win,now=self.now)
        self.assertEqual(r["official_session_gaps"],1)
        self.assertEqual(r["unattested_calendar_days"],0)
        self.assertFalse(r["provisional_coverage_passed"])
    def test_proven_suspension_only_provisionally_passes(self):
        cal={("TWSE",f"2026-09-0{i}"):True for i in range(1,4)}
        win={("TWSE","2330"):("2026-09-01","2026-09-03"),
             ("TWSE","0050"):("2026-09-01","2026-09-03")}
        exceptions={("TWSE","2330","2026-09-02"):"trading_halt"}
        r=audit_dates(self.records,cal,win,exceptions,now=self.now)
        self.assertEqual(r["official_session_gaps"],0)
        self.assertTrue(r["provisional_coverage_passed"])
        self.assertFalse(r["full_market_history_verified"])
    def test_missing_calendar_day_is_unknown_not_holiday(self):
        cal={("TWSE","2026-09-01"):True,("TWSE","2026-09-03"):True}
        win={("TWSE","2330"):("2026-09-01","2026-09-03")}
        r=audit_dates(self.records,cal,win,now=self.now)
        self.assertEqual(r["unattested_calendar_days"],1)
        self.assertFalse(r["provisional_coverage_passed"])
    def test_explicit_holiday_not_gap(self):
        cal={("TWSE","2026-09-01"):True,("TWSE","2026-09-02"):False,
             ("TWSE","2026-09-03"):True}
        win={("TWSE","2330"):("2026-09-01","2026-09-03")}
        one=[x for x in self.records if x[1]=="2330"]
        r=audit_dates(one,cal,win,now=self.now)
        self.assertTrue(r["provisional_coverage_passed"])
    def test_extra_price_on_closed_market(self):
        cal={("TWSE","2026-09-01"):True,("TWSE","2026-09-02"):True,
             ("TWSE","2026-09-03"):False}
        win={("TWSE","2330"):("2026-09-01","2026-09-03")}
        r=audit_dates([x for x in self.records if x[1]=="2330"],cal,win,now=self.now)
        self.assertEqual(r["instruments"][0]["unexpected_quote_sessions"],1)
    def test_duplicate_quote_days_fail_closed(self):
        with self.assertRaisesRegex(ValueError,"duplicate"):
            audit_dates(self.records+self.records[:1],now=self.now)
    def test_unofficial_calendar_rejected(self):
        with TemporaryDirectory() as tmp:
            p=Path(tmp)/"calendar.csv"
            self.write(p,["market","date","is_open","evidence_url"],
                       [("TWSE","2026-09-01","true","https://not-the-exchange.test")])
            with self.assertRaisesRegex(ValueError,"official"):
                load_evidence(calendar_csv=p)
    def test_duplicate_calendar_rejected(self):
        with TemporaryDirectory() as tmp:
            p=Path(tmp)/"calendar.csv"
            row=("TWSE","2026-09-01","true",TW)
            self.write(p,["market","date","is_open","evidence_url"],[row,row])
            with self.assertRaisesRegex(ValueError,"duplicate"):
                load_evidence(calendar_csv=p)
    def test_unverified_exception_rejected(self):
        with TemporaryDirectory() as tmp:
            p=Path(tmp)/"exception.csv"
            self.write(p,["market","symbol","date","reason","evidence_url"],
                       [("TWSE","2330","2026-09-02","bad_reason",TW)])
            with self.assertRaisesRegex(ValueError,"reason"):
                load_evidence(exceptions_csv=p)
    def test_read_only_sql(self):
        connection=MagicMock()
        cursor=connection.__enter__.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value=[("TWSE","2330",date(2026,9,1))]
        with patch.dict(sys.modules,{"psycopg":MagicMock(connect=MagicMock(return_value=connection))}):
            self.assertEqual(len(neon_read_only_dates("testing-dsn")),1)
        statements=[call.args[0] for call in cursor.execute.call_args_list]
        self.assertEqual(statements[0],"SET TRANSACTION READ ONLY")
        self.assertIn("approved_bars",statements[1])
        self.assertEqual(len(statements),2)

if __name__=="__main__":unittest.main()
