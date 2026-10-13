"""Check official open CSV encoding/schema and non-publishing semantics."""
import unittest
from backend.providers.open_fund_nav import inspect_csv, parse_nav_date, SOURCES

class ProbeTests(unittest.TestCase):
    def test_roc_and_gregorian_dates(self):
        self.assertEqual(parse_nav_date("115/10/13").isoformat(),"2026-10-13")
        self.assertEqual(parse_nav_date("2026-10-13").isoformat(),"2026-10-13")
        self.assertEqual(parse_nav_date("20261013").isoformat(),"2026-10-13")
        self.assertEqual(parse_nav_date("1151013").isoformat(),"2026-10-13")
        self.assertIsNone(parse_nav_date("115/02/30"))

    def test_domestic_big5_csv(self):
        rows="日期,基金代號,基金名稱,基金淨值,幣別\n115/10/13,F001,安聯台灣科技基金,773.25,新臺幣\n"
        result=inspect_csv("sitca_domestic_daily",rows.encode("cp950"))
        self.assertTrue(result["schema_verified"])
        self.assertEqual(result["parsed_nav_rows"],1)
        self.assertEqual(result["most_recent_nav_date"],"2026-10-13")

    def test_tdcc_offshore_header(self):
        rows="資料日期,基金代碼,基金名稱,計價幣別,淨值日期,最新淨值小計(總額)\n2026/10/13,F001,測試全球基金,USD,2026/10/12,12.56\n"
        result=inspect_csv("tdcc_offshore_global",rows.encode("utf-8-sig"))
        self.assertTrue(result["schema_verified"])
        self.assertEqual(result["parsed_nav_rows"],1)

    def test_unexpected_schema_is_flagged(self):
        self.assertFalse(inspect_csv("sitca_domestic_daily",b"abc,def\n1,2\n")["schema_verified"])

    def test_html_is_rejected(self):
        with self.assertRaisesRegex(ValueError,"HTML"):
            inspect_csv("sitca_domestic_daily",b"<!doctype html><html>blocked</html>")

    def test_only_official_sources(self):
        self.assertEqual(len(SOURCES),2)
        for item in SOURCES.values():
            self.assertTrue(item["url"].startswith("https://"))
            self.assertTrue(item["origin"].startswith("https://data.gov.tw/dataset/"))

if __name__=="__main__":
    unittest.main()
