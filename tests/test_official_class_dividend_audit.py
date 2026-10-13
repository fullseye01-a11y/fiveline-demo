"""Fail-closed checks for cross-source ISIN evidence and dividend metadata."""
import unittest
from tools.official_class_dividend_audit import valid_isin, evidence_review, load_csv, numeric_nav

def base_report():
    rows=[]
    for i in range(1,69):
        single=i==1
        rows.append({
            "id":str(i),"name":"TDCC test fund" if single else f"基金{i}","currency":"USD","nav_end":"2026-08-31",
            "status":"single_unverified_candidate" if single else "no_exact_candidate",
            "candidate_count":1 if single else 0,
            "candidates":[{"source":"tdcc_offshore_global","code":"TEST001",
                           "isin":"US0378331005","currency":"USD","name":"TDCC test fund",
                           "nav_date":"2026-10-13","nav":"12.34"}] if single else [],"approved":False
        })
    return {"mode":"read_only_matching_review","legacy_count":68,"records":rows,
            "reviewed_and_approved_count":0,"any_legacy_nav_overwritten":False}

def basic():
    return [{"基金代碼":"TEST001","基金ISIN_CODE":"US0378331005",
             "基金名稱":"TDCC test fund","計價幣別":"USD"}]

def nav():
    return [{"基金代碼":"TEST001","ISINCODE":"US0378331005",
             "基金名稱":"TDCC test fund","計價幣別":"USD","日期":"20261013",
             "基金淨值(金額)":"12.34"}]

def div():
    return [{"基金代碼":"TEST001","基金名稱":"TDCC test fund","基金幣別":"USD",
            "除息日":"2026-10-01","配息頻率":"月配"}]

class EvidenceTests(unittest.TestCase):
    def test_isin_luhn(self):
        self.assertTrue(valid_isin("US0378331005"))
        self.assertFalse(valid_isin("US0378331006"))
        self.assertFalse(valid_isin(""))
        self.assertFalse(valid_isin("LU123"))

    def test_cross_source_consistency_still_unapproved(self):
        result=evidence_review(base_report(),basic(),nav(),div())
        self.assertEqual(result["total_legacy"],68)
        self.assertEqual(result["official_metadata_consistent_not_approved"],1)
        x=result["records"][0]
        self.assertTrue(x["same_code_isin_evidence"])
        self.assertEqual(x["recent_nav_days"],1)
        self.assertEqual(x["official_dividend_event_count"],1)
        self.assertFalse(x["share_class_approved"])
        self.assertFalse(x["dividend_adjustment_ready"])
        self.assertFalse(x["historical_backfill_ready"])
        self.assertEqual(result["reviewed_approved"],0)

    def test_identity_mismatch_blocks(self):
        wrong=basic();wrong[0]["基金ISIN_CODE"]="US0378331006"
        a=evidence_review(base_report(),wrong,nav(),div())
        self.assertEqual(a["records"][0]["status"],"basic_identity_missing_or_ambiguous")

    def test_nav_missing_blocks(self):
        a=evidence_review(base_report(),basic(),[],div())
        self.assertEqual(a["records"][0]["status"],"recent_nav_identity_missing")
        self.assertFalse(a["records"][0]["same_code_isin_evidence"])

    def test_false_approval_rejected(self):
        r=base_report();r["records"][0]["approved"]=True
        with self.assertRaisesRegex(ValueError,"unapproved"):
            evidence_review(r,basic(),nav(),div())

    def test_dividend_not_a_cash_payment_amount(self):
        a=evidence_review(base_report(),basic(),nav(),div())
        self.assertFalse(a["cash_distributions_adjusted"])
        self.assertEqual(a["records"][0]["official_dividend_event_count"],1)
        self.assertFalse(a["records"][0]["dividend_adjustment_ready"])

    def test_schema_and_nav_failclosed(self):
        with self.assertRaisesRegex(ValueError,"schema changed"):
            load_csv("tdcc_offshore_recent_nav",b"A,B\n1,2\n")
        self.assertIsNone(numeric_nav("-10"))
        self.assertIsNone(numeric_nav("0"))
        self.assertIsNone(numeric_nav("NaN"))
        self.assertEqual(str(numeric_nav("123.04")),"123.04")

if __name__=="__main__": unittest.main()
