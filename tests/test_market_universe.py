"""Official market catalog only; no historical NAV / portfolio write operations."""
import json
import unittest
from datetime import datetime, timezone
from tools.build_market_universe import normalize_source,build,FEEDS

class Tests(unittest.TestCase):
    def test_listed_company(self):
        rows=[{"公司代號":"2330","公司簡稱":"台積電"},{"公司代號":"2454","公司簡稱":"聯發科"},{"公司代號":"2317","公司簡稱":"鴻海"},{"公司代號":"2603","公司簡稱":"長榮"},{"公司代號":"2882","公司簡稱":"國泰金"}]
        p=normalize_source("twse_listed_stocks",json.dumps(rows,ensure_ascii=False).encode())
        self.assertEqual(p["accepted_rows"],5)
        self.assertTrue(all(not x["historical_prices_ready"] for x in p["rows"]))
        self.assertTrue(all(x["market"]=="TWSE" for x in p["rows"]))
    def test_otc_english_and_csv_backup(self):
        src="SecuritiesCompanyCode,CompanyAbbreviation\n5483,中美晶\n3529,力旺\n6274,台燿\n6180,橘子\n6488,環球晶\n"
        p=normalize_source("tpex_otc_stocks",src.encode(),format_override="csv")
        self.assertEqual(p["accepted_rows"],5)
        self.assertEqual(p["rows"][0]["market"],"TPEX")
    def test_sitca_distinguishes_classes_not_merged(self):
        src="基金代號,基金名稱,幣別\nAB1,甲基金A,USD\nAB1,甲基金B,TWD\nAB2,乙基金,USD\nAB3,丙基金,TWD\nAB4,丁基金,TWD\n"
        p=normalize_source("sitca_domestic_funds",src.encode(),format_override="csv")
        self.assertEqual(p["accepted_rows"],5)
        self.assertNotEqual(p["rows"][0]["uid"],p["rows"][1]["uid"])
    def test_offshore_isin_currency(self):
        rows="基金代碼,基金名稱,基金ISIN_CODE,計價幣別\nA1,甲基金,LU1234567890,USD\nA2,乙基金,LU9876543210,EUR\nA3,丙基金,LU3333333333,TWD\nA4,丁基金,LU4444444444,ZAR\nA5,戊基金,LU5555555555,USD\n"
        data=normalize_source("tdcc_offshore_funds",rows.encode(),format_override="csv")
        self.assertEqual(data["accepted_rows"],5)
        report=build({"tdcc_offshore_funds":data},{"tpex_otc_stocks":"403"},datetime(2026,10,14,tzinfo=timezone.utc))
        self.assertEqual(report["total_rows"],5)
        self.assertFalse(report["catalog_complete_for_configured_sources"])
        self.assertEqual(report["failed_sources"],["tpex_otc_stocks"])
        self.assertFalse(report["share_classes_approved"])
        self.assertFalse(report["historical_nav_backfilled"])
        self.assertFalse(report["customer_records_included"])
        self.assertFalse(report["original_excel_modified"])
    def test_invalid_schema_rejected(self):
        with self.assertRaisesRegex(ValueError,"schema changed"):
            normalize_source("twse_listed_stocks",b'[{"wrong":"name"}]')
    def test_duplicate_official_records_dedup(self):
        s=json.dumps([{"公司代號":"2330","公司簡稱":"台積電"}]*5,ensure_ascii=False).encode()
        x=normalize_source("twse_listed_stocks",s)
        self.assertEqual(x["source_records"],5)
        self.assertEqual(x["accepted_rows"],1) if False else self.assertEqual(x["accepted_rows"],1)
if __name__=="__main__":unittest.main()
