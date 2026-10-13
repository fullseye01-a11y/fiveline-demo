"""NAV matching review MUST NOT auto-approve fund classes or overwrite OLS history."""
from pathlib import Path
from unittest.mock import patch
import unittest

from backend.fund_matching import (
    CURRENCY_ALIASES, get_legacy_rows, make_report,
    normalize_currency, normalize_name
)
import backend.server as app


class MatchingReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows=get_legacy_rows(app.COVERAGE_FILE)

    def provider(self, records):
        return {"records":records}

    def nav(self, name, currency="TWD",code="F123",source="sitca_domestic_daily"):
        return {"name":name,"currency":currency,"code":code,"source":source,
                "isin":"","nav_date":"2026-10-13","nav":"101.51"}

    def find(self, report, id_):
        return next(x for x in report["records"] if x["id"]==id_)

    def test_fixed_68_unique_snapshot_and_explicit_etf_exclusion(self):
        self.assertEqual(len(self.rows),68)
        r=make_report(self.rows,{})
        self.assertEqual(r["legacy_count"],68)
        self.assertEqual(r["reviewed_and_approved_count"],0)
        self.assertFalse(r["any_legacy_nav_overwritten"])
        for id_ in ["3","4","5","6","7","8","12","56","57","58","59","60","63","64","65"]:
            self.assertEqual(self.find(r,id_)["status"],"separate_exchange_feed_or_identity_required")

    def test_single_name_and_currency_is_only_candidate_not_approved(self):
        official={"sitca_domestic_daily":self.provider([
            self.nav("安聯台灣科技基金",currency="新臺幣",code="FX-1123")
        ])}
        r=make_report(self.rows,official)
        x=self.find(r,"1")
        self.assertEqual(x["status"],"single_unverified_candidate")
        self.assertFalse(x["approved"])
        self.assertEqual(x["candidate_count"],1)
        self.assertEqual(x["candidates"][0]["code"],"FX-1123")
        self.assertEqual(x["candidates"][0]["currency_code"],"TWD")
        self.assertEqual(self.find(r,"4")["candidate_count"],0)

    def test_currency_mismatch_never_mapped(self):
        r=make_report(self.rows,{"tdcc_offshore_global":self.provider([
            self.nav("安聯台灣科技基金",currency="美元",code="F200")
        ])})
        x=self.find(r,"1")
        self.assertEqual(x["status"],"currency_mismatch")
        self.assertFalse(x["approved"])

    def test_ambiguous_share_classes_never_resolved_automatically(self):
        r=make_report(self.rows,{"sitca_domestic_daily":self.provider([
            self.nav("安聯台灣科技基金",code="F001"),
            self.nav("安聯台灣科技基金",code="F002")
        ])})
        x=self.find(r,"1")
        self.assertEqual(x["status"],"multiple_unverified_candidates")
        self.assertEqual(x["candidate_count"],2)
        self.assertFalse(x["approved"])

    def test_provider_failure_does_not_pretend_coverage(self):
        r=make_report(self.rows,{},{"sitca_domestic_daily":"URLError"})
        self.assertEqual(self.find(r,"1")["status"],"incomplete_provider_coverage")
        self.assertEqual(r["provider_errors"]["sitca_domestic_daily"],"URLError")

    def test_name_normalization_keeps_fund_class_tokens(self):
        self.assertEqual(normalize_name("安聯 台灣科技-基金"), normalize_name("安聯台灣科技基金"))
        self.assertNotEqual(normalize_name("貝萊德世界科技基金 A10"),
                            normalize_name("貝萊德世界科技基金 A2"))
        self.assertEqual(normalize_currency("新臺幣"),"TWD")
        self.assertEqual(normalize_currency("美元"),"USD")

    def test_public_route_return_is_pending_review(self):
        with patch.object(app,"official_snapshot",side_effect=lambda s: {
                "records":[self.nav("安聯台灣科技基金",currency="新臺幣")]}):
            report=app.matching_review()
        self.assertEqual(report["reviewed_and_approved_count"],0)
        self.assertEqual(report["legacy_count"],68)
        self.assertEqual(report["provider_success"],["sitca_domestic_daily","tdcc_offshore_global"])
        self.assertEqual(self.find(report,"1")["status"],"multiple_unverified_candidates")


if __name__=="__main__":
    unittest.main()
