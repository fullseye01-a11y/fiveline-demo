"""Synthetic official annual TWSE schedule tests; no exchange or Neon network."""
from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import twse_official_calendar_probe as probe

# 2026 TWSE annual market calendar's 27 dated special events, no quotes.
SPECIALS = [
 ("2026-01-01","中華民國開國紀念日"),
 ("2026-01-02","國曆新年開始交易日"),
 ("2026-02-11","農曆春節前最後交易日"),
 ("2026-02-12","市場無交易，僅辦理結算交割作業"),
 ("2026-02-13","市場無交易，僅辦理結算交割作業"),
 ("2026-02-15","農曆除夕及春節"),
 ("2026-02-16","農曆除夕及春節"),
 ("2026-02-17","農曆除夕及春節"),
 ("2026-02-18","農曆除夕及春節"),
 ("2026-02-19","農曆除夕及春節"),
 ("2026-02-20","農曆除夕及春節"),
 ("2026-02-23","農曆春節後開始交易日"),
 ("2026-02-27","和平紀念日"),
 ("2026-02-28","和平紀念日"),
 ("2026-04-03","兒童節及民族掃墓節"),
 ("2026-04-04","兒童節及民族掃墓節"),
 ("2026-04-05","兒童節及民族掃墓節"),
 ("2026-04-06","兒童節及民族掃墓節"),
 ("2026-05-01","勞動節"),
 ("2026-06-19","端午節"),
 ("2026-09-25","中秋節"),
 ("2026-09-28","孔子誕辰紀念日/ 教師節"),
 ("2026-10-09","國慶日"),
 ("2026-10-10","國慶日"),
 ("2026-10-25","臺灣光復暨金門古寧頭大捷紀念日"),
 ("2026-10-26","臺灣光復暨金門古寧頭大捷紀念日"),
 ("2026-12-25","行憲紀念日"),
]
assert len(SPECIALS)==27

def fixture():
    return {
      "stat":"ok","date":"20260101","title":"115 年市場開休市日期",
      "fields":["日期","名稱","說明"],
      "data":[[d,n,"official research test"] for d,n in SPECIALS],
      "queryYear":2026,"total":27,
    }

class TwseAnnualCalendarTests(unittest.TestCase):
    def setUp(self):
        self.events=probe.parse_twse_official_2026(fixture())
        self.now=datetime(2026,10,14,tzinfo=timezone.utc)
    def test_known_official_scheduled_closures(self):
        self.assertEqual(len(self.events),27)
        for x in probe.REQUIRED_2026_CLOSED:
            self.assertFalse(self.events[x]["is_open"])
        self.assertTrue(self.events["2026-02-23"]["is_open"])
    def test_provisional_pilot_window_expected_28_days(self):
        days=probe.scheduled_days(self.events)
        self.assertEqual(len(days),28)
        self.assertNotIn("2026-09-25",days)
        self.assertNotIn("2026-09-28",days)
        self.assertNotIn("2026-10-09",days)
    def test_missing_day_warning_not_hidden(self):
        days=probe.scheduled_days(self.events)
        rows=[("TWSE",code,date.fromisoformat(d)) for code in probe.PILOT_CODES for d in days]
        rows.remove(("TWSE","2330",date(2026,9,2)))
        result=probe.summarize_schedule_against_neon(self.events,rows,"fixture-hash",now=self.now)
        self.assertEqual(result["missing_planned_session_count"],1)
        self.assertEqual(result["instruments"][1]["planned_session_missing_date_sample"],["2026-09-02"])
        self.assertFalse(result["complete_historical_coverage_verified"])
        self.assertFalse(result["neon_database_mutated"])
    def test_fully_observed_still_not_marked_historically_verified(self):
        days=probe.scheduled_days(self.events)
        rows=[("TWSE",code,date.fromisoformat(d)) for code in probe.PILOT_CODES for d in days]
        result=probe.summarize_schedule_against_neon(self.events,rows,"fixture-hash",now=self.now)
        self.assertEqual(result["missing_planned_session_count"],0)
        self.assertEqual(result["unexpected_closed_session_quotes"],0)
        self.assertEqual(result["total_planned_sessions_per_instrument"],28)
        self.assertFalse(result["actual_daily_sessions_verified"])
        self.assertFalse(result["tpex_calendar_verified"])
        self.assertFalse(result["licensed_public_market_data_redistribution"])
    def test_unexpected_closed_session_quote(self):
        rows=[("TWSE","2330",date(2026,9,25))]
        result=probe.summarize_schedule_against_neon(self.events,rows,"fixture-hash",now=self.now)
        self.assertEqual(result["unexpected_closed_session_quotes"],1)
        self.assertEqual(result["instruments"][1]["quotes_on_officially_closed_day_sample"],["2026-09-25"])
    def test_tpex_rows_are_ignored_not_cross_verified(self):
        rows=[("TPEX","6488",date(2026,10,6))]
        result=probe.summarize_schedule_against_neon(self.events,rows,"fixture-hash",now=self.now)
        self.assertEqual(result["symbol_count"],2)
        self.assertFalse(result["tpex_calendar_verified"])
    def test_unknown_event_fails_closed(self):
        p=fixture()
        p["data"][0][1]="為了投資進行補交易（未審核）"
        with self.assertRaisesRegex(ValueError,"unknown"):
            probe.parse_twse_official_2026(p)
    def test_year_mismatch_fails_closed(self):
        p=fixture()
        p["queryYear"]=2025
        with self.assertRaisesRegex(ValueError,"year"):
            probe.parse_twse_official_2026(p)
    def test_missing_event_fails_closed(self):
        p=fixture()
        p["data"].pop(20)
        p["total"]=26
        with self.assertRaisesRegex(ValueError,"count"):
            probe.parse_twse_official_2026(p)
    def test_duplicate_event_fails_closed(self):
        p=fixture()
        p["data"][1][0]=p["data"][0][0]
        with self.assertRaisesRegex(ValueError,"duplicated"):
            probe.parse_twse_official_2026(p)
    def test_duplicate_approved_date_fails_closed(self):
        with self.assertRaisesRegex(ValueError,"duplicate Neon"):
            probe.summarize_schedule_against_neon(self.events,[
                ("TWSE","2330",date(2026,9,1)),("TWSE","2330",date(2026,9,1))],
                "fixture",now=self.now)
    def test_out_of_scope_rejected(self):
        with self.assertRaisesRegex(ValueError,"capped"):
            probe.scheduled_days(self.events,"2026-01-01","2026-10-13")
    def test_fetcher_one_call_and_neon_read_only_injection(self):
        with patch.object(probe,"neon_read_only_dates",return_value=[]) as reader:
            calls=[]
            def fetch():
                calls.append(1)
                return fixture(),"synthetic-hash"
            result=probe.run_probe("private-testing-dsn",fetcher=fetch)
        self.assertEqual(len(calls),1)
        reader.assert_called_once_with("private-testing-dsn")
        self.assertEqual(result["source_payload_sha256"],"synthetic-hash")
        self.assertFalse(result["import_executed"])
    def test_invalid_year_calendar_date_rejected(self):
        p=fixture()
        p["data"][0][0]="2025-01-01"
        with self.assertRaisesRegex(ValueError,"outside year"):
            probe.parse_twse_official_2026(p)

if __name__ == "__main__":
    unittest.main()
