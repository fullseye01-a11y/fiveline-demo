"""Offline TPEx annual cash-stock calendar and staging-only gap tests."""
from datetime import date,datetime,timezone
from io import BytesIO
from pathlib import Path
import sys
import unittest
from unittest.mock import patch,MagicMock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import tpex_official_stock_calendar as m

DOC="""
財團法人中華民國證券櫃檯買賣中心
中華民國115年有價證券櫃檯買賣市場開（休）市日期表
農曆春節前國際債券交易系統最後交易日 2月10日
農曆春節前股票交易系統最後交易日 2月11日
2月12日及2月13日市場無交易，僅辦理給付結算。
農曆春節前債券處所議價最後交易日 2月13日
中秋節 9月25日 五
孔子誕辰紀念日/教師節 9月28日 一
國慶日 10月9日 10月10日
臺灣光復暨金門古寧頭大捷紀念日 10月25日 10月26日
"""

class TPExOfficialStockCalendarTests(unittest.TestCase):
    def setUp(self):
        self.holidays=m.validate_pdf_text(DOC)
        self.asof=datetime(2026,10,18,tzinfo=timezone.utc)
        pilot=m.expected_stock_sessions(m.PILOT_FIRST,m.PILOT_LAST,self.holidays)
        self.raw=[("TPEX",code,date.fromisoformat(day))
                  for code in m.PILOT_CODES for day in pilot]

    def test_stock_not_bond_header(self):
        self.assertEqual(len(self.holidays),4)
        self.assertIn("2026-10-09",self.holidays)

    def test_cash_stock_february_last_session_required(self):
        with self.assertRaisesRegex(ValueError,"stock-versus-bond"):
            m.validate_pdf_text(DOC.replace("農曆春節前股票交易系統最後交易日","普通交易日"))

    def test_holiday_and_name_must_be_near(self):
        bad=DOC.replace("國慶日 10月9日 10月10日","國慶日 "+("無關資料"*100)+" 10月9日 10月10日")
        with self.assertRaisesRegex(ValueError,"label/date"):
            m.validate_pdf_text(bad)

    def test_wrong_roc_year_is_rejected(self):
        with self.assertRaisesRegex(ValueError,"year/type"):
            m.validate_pdf_text(DOC.replace("115年","114年"))

    def test_pilot_five_sessions_and_expanded_eight(self):
        self.assertEqual(len(m.expected_stock_sessions(m.PILOT_FIRST,m.PILOT_LAST,self.holidays)),5)
        self.assertEqual(len(m.expected_stock_sessions(m.EXPANDED_FIRST,m.EXPANDED_LAST,self.holidays)),8)
        self.assertNotIn("2026-10-09",m.expected_stock_sessions(m.EXPANDED_FIRST,m.EXPANDED_LAST,self.holidays))

    def test_neon_data_full_pilot_but_expanded_missing_six(self):
        out=m.compare_to_neon(self.holidays,self.raw,"test-sha",self.asof)
        self.assertEqual(out["pilot_missing_planned_quote_days"],0)
        self.assertEqual(out["expanded_unverified_gap_candidates"],6)
        for i in out["instruments"]:
            self.assertEqual(i["expanded_missing_date_candidates"],["2026-10-01","2026-10-02","2026-10-05"])
            self.assertFalse(i["listing_history_verified"])
        self.assertFalse(out["complete_history_verified"])
        self.assertFalse(out["database_mutated"])

    def test_unexpected_quote_on_national_holiday(self):
        out=m.compare_to_neon(self.holidays,self.raw+[("TPEX","6488",date(2026,10,9))],"test",self.asof)
        self.assertEqual(out["official_announced_closed_session_quote_count"],1)

    def test_pilot_missing_quote_alerts(self):
        out=m.compare_to_neon(self.holidays,self.raw[1:],"test",self.asof)
        self.assertEqual(out["pilot_missing_planned_quote_days"],1)

    def test_duplicates_fail_closed(self):
        with self.assertRaisesRegex(ValueError,"Duplicated"):
            m.compare_to_neon(self.holidays,self.raw+self.raw[:1],"test",self.asof)

    def test_overlong_window_rejected(self):
        with self.assertRaisesRegex(ValueError,"over 46 days"):
            m.expected_stock_sessions("2026-01-01","2026-10-13",self.holidays)

    def test_nonpdf_does_not_parse(self):
        with self.assertRaisesRegex(ValueError,"missing/invalid"):
            m.extract_official_pdf(b"<html>not a PDF</html>")

    def test_pdf_parser_rejects_encrypted_document(self):
        fake=MagicMock()
        fake.pages=[MagicMock()]
        fake.is_encrypted=True
        with patch.dict(sys.modules,{"pypdf":MagicMock(PdfReader=MagicMock(return_value=fake))}):
            with self.assertRaisesRegex(ValueError,"encryption"):
                m.extract_official_pdf(b"%PDF-1.7 synthetic")

    def test_one_shot_neon_readonly_no_import(self):
        with patch.object(m,"extract_official_pdf",return_value=DOC):
            called=[]
            def fetch():
                called.append(1)
                return (b"%PDF-fake", "test-sha")
            def readonly(dsn):
                self.assertEqual(dsn,"testing-neon-only")
                return self.raw
            out=m.run_probe("testing-neon-only",fetcher=fetch,date_reader=readonly)
        self.assertEqual(len(called),1)
        self.assertEqual(out["pilot_missing_planned_quote_days"],0)
        self.assertFalse(out["import_executed"])

    def test_without_dsn_no_remote_request(self):
        fetch=MagicMock()
        with self.assertRaisesRegex(RuntimeError,"no remote"):
            m.run_probe(None,fetcher=fetch)
        fetch.assert_not_called()

if __name__=="__main__":
    unittest.main()
