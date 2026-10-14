import tempfile
import unittest
from pathlib import Path

from history_pilot import iso_date, make_bar, open_db, parse_tpex, parse_twse, stage_batch


class TestHistoryPilot(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.conn = open_db(Path(self.temp.name) / "test.sqlite3")

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def twse_fixture(self):
        return {"stat": "OK", "fields": ["日期", "成交股數", "成交金額", "開盤價", "最高價", "最低價", "收盤價", "漲跌價差", "成交筆數", "註記"],
                "data": [["115/10/13", "10,000", "1,015,000", "100", "103", "99", "101.5", "+1.5", "45", ""]]}

    def test_roc_iso(self):
        self.assertEqual(iso_date("115/10/13"), "2026-10-13")
        self.assertEqual(iso_date("1151013"), "2026-10-13")
        with self.assertRaises(ValueError):
            iso_date("115/13/43")

    def test_twse_parsing_and_units(self):
        rows, errors = parse_twse(self.twse_fixture(), "2330", "2026-10")
        self.assertEqual(errors, [])
        self.assertEqual(rows[0]["volume_shares"], 10000)
        self.assertEqual(rows[0]["close"], "101.5")

    def test_tpex_legacy_unit_must_be_explicit(self):
        data = {"reportDate": "115/10/13", "aaData": [["8069", "mock", "200", "+2", "198", "204", "197", "2,000", "402,000", "10"]]}
        rows, errors = parse_tpex(data, "2026-10-13", {"8069"})
        self.assertEqual(len(rows), 0)
        self.assertIn("volume", errors[0])
        rows, errors = parse_tpex(data, "2026-10-13", {"8069"}, "shares")
        self.assertEqual(rows[0]["volume_shares"], 2000)
        self.assertEqual(errors, [])
        with self.assertRaises(ValueError):
            parse_tpex(data, "2026-10-12", {"8069"}, "shares")

    def test_tpex_tables_thousand_shares(self):
        doc = {"reportDate": "115/10/13", "tables": [{"fields": ["代號", "開盤", "最高", "最低", "收盤", "成交仟股", "成交金額(元)"],
                "data": [["8069", "100", "105", "99", "103", "12", "1234000"]]}]}
        rows, errs = parse_tpex(doc, "2026-10-13", {"8069"})
        self.assertEqual(errs, [])
        self.assertEqual(rows[0]["volume_shares"], 12000)

    def test_ohlc_reject_and_no_public_export(self):
        with self.assertRaises(ValueError):
            make_bar("TWSE", "2330", "115/10/13", "100", "90", "95", "102", "500", "1000")

    def test_idempotency_revisions_and_approval_gate(self):
        row = make_bar("TWSE", "2330", "115/10/13", "100", "103", "99", "101", "1000", "100000")
        a = stage_batch(self.conn, "TWSE", "2330/2026-10", [row], [])
        b = stage_batch(self.conn, "TWSE", "2330/2026-10", [row], [])
        changed = dict(row, close="102")
        c = stage_batch(self.conn, "TWSE", "2330/2026-10", [changed], [])
        self.assertEqual((a["inserted"], b["unchanged"], c["pending"]), (1, 1, 1))
        self.assertEqual(self.conn.execute("SELECT close FROM approved_bars").fetchone()[0], "101")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM pending_review").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM bar_versions").fetchone()[0], 2)

    def test_price_jump_pending(self):
        one = make_bar("TWSE", "2330", "115/10/12", "100", "101", "99", "100", "100", "10000")
        two = make_bar("TWSE", "2330", "115/10/13", "150", "160", "149", "155", "100", "15500")
        stage_batch(self.conn, "TWSE", "p1", [one], [])
        result = stage_batch(self.conn, "TWSE", "p2", [two], [])
        self.assertEqual(result["pending"], 1)



class TestExtendedHistoryPilot(unittest.TestCase):
    def test_twse_future_day_is_excluded(self):
        doc = {"stat": "OK", "fields": ["日期", "成交股數", "成交金額", "開盤價", "最高價", "最低價", "收盤價", "成交筆數"],
               "data": [["115/10/13", "100", "10,000", "100", "102", "99", "100", "8"],
                        ["115/10/14", "100", "10,000", "100", "102", "99", "100", "8"]]}
        bars, errors = parse_twse(doc, "2330", "2026-10", as_of="2026-10-13")
        self.assertEqual(errors, [])
        self.assertEqual([r["day"] for r in bars], ["2026-10-13"])

    def test_precision_changes_do_not_forge_revision(self):
        with tempfile.TemporaryDirectory() as d:
            with open_db(Path(d) / "sample.sqlite3") as conn:
                row1 = make_bar("TWSE", "2330", "115/10/13", "100.00", "102.00", "99", "100", "100", "10,000")
                row2 = make_bar("TWSE", "2330", "115/10/13", "100", "102", "99.0", "100.0", "100", "10000")
                stage_batch(conn, "TWSE", "a", [row1], [])
                self.assertEqual(stage_batch(conn, "TWSE", "a", [row2], [])["unchanged"], 1)

    def test_turnover_unit_outlier_is_held(self):
        with tempfile.TemporaryDirectory() as d:
            with open_db(Path(d) / "sample.sqlite3") as conn:
                row = make_bar("TPEX", "8069", "115/10/13", "100", "103", "99", "101", "1000", "100000000")
                self.assertEqual(stage_batch(conn, "TPEX", "a", [row], [])["pending"], 1)
                self.assertIn("value_volume", conn.execute("SELECT review_reason FROM pending_review").fetchone()[0])

    def test_quality_report_is_read_only(self):
        from quality_report import summarize
        with tempfile.TemporaryDirectory() as d:
            file = Path(d) / "sample.sqlite3"
            with open_db(file) as conn:
                row = make_bar("TWSE", "2330", "115/10/13", "100", "102", "99", "100", "100", "10,000")
                stage_batch(conn, "TWSE", "a", [row], [])
            report = summarize(file)
            self.assertEqual(report["status"], "LOCAL_CHECKS_PASS")
            self.assertEqual(report["counts"]["approved_bars"], 1)
            self.assertNotIn("open", report["symbols"][0])

if __name__ == "__main__":
    unittest.main()


class TestSelectedCodes(unittest.TestCase):
    def test_bad_unselected_tpex_rows_are_ignored(self):
        data = {"reportDate": "115/10/13", "aaData": [
            ["1234", "non-target", "--", "--", "--", "--", "--", "--", "--", "--"],
            ["8069", "target", "100", "+1", "99", "102", "98", "1000", "100000", "10"]]}
        bars, errors = parse_tpex(data, "2026-10-13", {"8069"}, "shares")
        self.assertEqual(len(bars), 1)
        self.assertEqual(errors, [])
