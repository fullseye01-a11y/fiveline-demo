"""Official domestic per-unit SITCA CSV transport/identity regression tests."""
import csv
from datetime import date
from io import StringIO
from unittest.mock import patch
import unittest

from adviser_portal import sitca_direct_nav as s
from adviser_portal import nav_evidence as n

ROW={"uid":"dom-001","source":"sitca_domestic_funds",
     "category":"domestic_fund_share_class","name":"安聯台灣科技基金",
     "code":"DDO04","currency":"TWD","isin":"",
     "class_identity_approved":False,"historical_prices_ready":False}

def generated_csv(rows=1100,mutate=None):
    stream=StringIO()
    w=csv.writer(stream)
    w.writerow(["基金名稱","基金代號","基金淨值","幣別","日期"])
    for i in range(rows):
        row=[f"測試模擬基金{i}",f"AB{i:04d}","27.586","TWD","115/10/16"]
        if i==0:
            row=[ROW["name"],ROW["code"],"32.74925","TWD","115/10/16"]
        if mutate:
            row=mutate(row,i)
        w.writerow(row)
    return stream.getvalue().encode("utf-8-sig")


class TestDirectSITCA(unittest.TestCase):
    def test_official_dates_roc_and_iso(self):
        self.assertEqual(s.parse_date("115/10/16"),"2026-10-16")
        self.assertEqual(s.parse_date("1151016"),"2026-10-16")
        self.assertEqual(s.parse_date("2026-10-16"),"2026-10-16")
        self.assertIsNone(s.parse_date("115/13/16"))
    def test_valid_unit_nav_records_and_origin(self):
        snap=s.validate_csv(generated_csv())
        self.assertEqual(snap["source_count"],1100)
        self.assertEqual(snap["origin"],s.ORIGIN)
        record=next(x for x in snap["items"] if x["code"]=="DDO04")
        self.assertEqual(record["nav"],"32.74925")
        self.assertEqual(record["nav_date"],"2026-10-16")
    def test_html_refused(self):
        with self.assertRaises(s.SITCAUnavailable):
            s.validate_csv(b"<!doctype html><html>Error 502</html>")
    def test_schema_change_refused(self):
        raw=generated_csv().replace("基金淨值".encode(), "基金總值".encode())
        with self.assertRaisesRegex(s.SITCAUnavailable,"schema"):
            s.validate_csv(raw)
    def test_minimum_completeness_refused(self):
        with self.assertRaisesRegex(s.SITCAUnavailable,"too few"):
            s.validate_csv(generated_csv(rows=5))
    def test_conflicting_same_day_prices_refused(self):
        raw=generated_csv(mutate=lambda row,i: [ROW["name"],ROW["code"],"99","TWD","115/10/16"] if i==1 else row)
        with self.assertRaisesRegex(s.SITCAUnavailable,"conflicting"):
            s.validate_csv(raw)
    def test_no_similar_name_substitution(self):
        snap=s.validate_csv(generated_csv())
        wrong=dict(ROW,name="安聯台灣科技基金美元避險級別")
        result=s.lookup(wrong,fetcher=lambda:snap)
        self.assertEqual(result["records"],[])
    def test_exact_identity_can_be_matched_after_direct_transport(self):
        snap=s.validate_csv(generated_csv())
        with patch.object(n,"get_catalog",return_value={"items":[ROW]}),\
             patch.object(n,"_upstream_search",side_effect=n.NavEvidenceUnavailable("upstream offline")),\
             patch.object(s,"snapshot",return_value=snap):
            out=n.evidence(ROW["uid"],today=date(2026,10,18))
        self.assertEqual(out["status"],"LATEST_NAV_EVIDENCE_ONLY")
        self.assertEqual(out["nav"],"32.74925")
        self.assertFalse(out["usable_for_portfolio"])
        self.assertFalse(out["class_identity_approved"])
    def test_official_fallback_failed_does_not_show_price(self):
        with patch.object(n,"get_catalog",return_value={"items":[ROW]}),\
             patch.object(n,"_upstream_search",side_effect=n.NavEvidenceUnavailable("upstream offline")),\
             patch.object(s,"snapshot",side_effect=s.SITCAUnavailable("can't read source")):
            with self.assertRaises(n.NavEvidenceUnavailable):
                n.evidence(ROW["uid"],today=date(2026,10,18))

if __name__=="__main__":
    unittest.main()
