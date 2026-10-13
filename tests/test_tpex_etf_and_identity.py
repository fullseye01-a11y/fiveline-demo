"""TPEx official ETF suggestions and conservative fund identity evidence tests."""
import json
from datetime import datetime, timezone
import unittest
from tools.build_market_universe import normalize_source, build, FEEDS
from tools.audit_market_identity import audit,valid_isin

def etf_source():
    suggestions={"query":"","suggestions":[
      "006201 元大富櫃50","00883B 中信ESG投資級債",
      "00983B 大華優利美公債20","00984B 大華優利美A債15",
      "00411A 主動統一前沿科技"]}
    return normalize_source("tpex_etfs",json.dumps(suggestions,ensure_ascii=False).encode())

def catalog():
    a=etf_source()
    b=normalize_source("sitca_domestic_funds",
        b'code,name,currency\nA,test,USD\n' if False else
        "基金代號,基金名稱,幣別\nAB1,富達全球收益A,USD\nAB2,同一名稱,TWD\nAB3,同一名稱,USD\nAB4,相異基金,USD\nAB5,最後基金,EUR\n".encode())
    off=normalize_source("tdcc_offshore_funds",
        "基金代碼,基金名稱,基金ISIN_CODE,計價幣別\nOF1,富達全球收益A,US0378331005,USD\nOF2,另一基金,US0378331005,USD\nOF3,不同級別,US0378331005,EUR\nOF4,同一名稱,US0378331005,TWD\nOF5,安全基金,US0378331005,USD\n".encode())
    return build({"tpex_etfs":a,"sitca_domestic_funds":b,"tdcc_offshore_funds":off},{})

class Tests(unittest.TestCase):
    def test_tpex_official_118_shape_and_category(self):
        p=etf_source()
        self.assertEqual(p["source_records"],5)
        self.assertEqual(p["accepted_rows"],5)
        self.assertTrue(all(x["market"]=="TPEX" and x["category"]=="etf" for x in p["rows"]))
        self.assertIn("00983B",{x["code"] for x in p["rows"]})
        self.assertTrue(all(not x["class_identity_approved"] for x in p["rows"]))
        self.assertEqual(FEEDS["tpex_etfs"]["format"],"tpex_suggestions")

    def test_bad_suggestions_and_conflicting_code_fail_closed(self):
        for payload in [{"foo":[]},{"suggestions":["WRONG"]},
                        {"suggestions":["006201 元大富櫃50","006201 不同基金"]}]:
            with self.assertRaises(ValueError):
                normalize_source("tpex_etfs",json.dumps(payload,ensure_ascii=False).encode())

    def test_names_do_not_trigger_any_automatic_merge(self):
        p=catalog()
        report=audit(p,datetime(2026,10,14,tzinfo=timezone.utc))
        self.assertFalse(report["automatic_deduplication_applied"])
        self.assertFalse(report["all_fund_classes_approved"])
        self.assertIsNone(report["unique_investment_products_verified"])
        self.assertEqual(report["source_record_total"],15)
        self.assertTrue(report["potential_link_groups_total"]>0)
        self.assertTrue(all(not r["merge_approved"] for r in report["groups"]))
        self.assertTrue(all(r["needs_manual_review"] for r in report["groups"]))
        self.assertTrue(all(not r.get("historical_prices_ready",False) for r in p["instruments"]))

    def test_isin_check_digit_not_class_approval(self):
        self.assertTrue(valid_isin("US0378331005"))
        self.assertFalse(valid_isin("US0378331006"))
        self.assertFalse(valid_isin("LU0000000000"))
        self.assertFalse(valid_isin(""))

    def test_malformed_catalog_blocked(self):
        p=catalog()
        p["instruments"][0]["class_identity_approved"]=True
        with self.assertRaisesRegex(ValueError,"unexpected approved"):
            audit(p)

    def test_trading_ticker_is_not_nav_identity(self):
        p=catalog()
        groups=audit(p)["groups"]
        self.assertTrue(any(g["relation"]=="same_normalized_name_currency" for g in groups))
        self.assertFalse(any(g.get("merge_approved") for g in groups))
if __name__=="__main__":
    unittest.main()
