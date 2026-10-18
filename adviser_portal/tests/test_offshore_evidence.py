"""TDCC offshore real-schema NAV and dividend metadata; synthetic tests only."""
from datetime import date
from http.server import ThreadingHTTPServer
from threading import Thread
from io import StringIO
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen
import csv
import json
import unittest

from adviser_portal import offshore_evidence as m
from adviser_portal.server import Handler

FUND={
  "uid":"off-001","source":"tdcc_offshore_funds",
  "category":"offshore_fund_share_class","name":"紐約梅隆美國股票收益基金-美元A配息",
  "code":"00BD5M6702","isin":"IE00BD5M6702","currency":"美元",
  "class_identity_approved":False,"historical_prices_ready":False,
}
def fixtures():
    basics=[{"基金代碼":FUND["code"],"基金ISIN_CODE":FUND["isin"],
             "基金名稱":FUND["name"],"計價幣別":"美元"}]
    navs=[{"基金代碼":FUND["code"],"ISINCODE":FUND["isin"],
           "基金名稱":FUND["name"],"計價幣別":"美元",
           "日期":d,"基金淨值(金額)":price}
          for d,price in [("20261014","10.5455"),("20261015","10.6904"),
                          ("20261016","10.8027")]]
    divs=[{"基金代碼":FUND["code"],"基金名稱":FUND["name"],
           "基金幣別":"美元","除息日":"20260930","配息頻率":"月配"},
          {"基金代碼":FUND["code"],"基金名稱":FUND["name"],
           "基金幣別":"美元","除息日":"20260831","配息頻率":"月配"}]
    return {"basic":basics,"recent":navs,"dividend":divs}


def as_csv(kind, num=300):
    config={
        "basic":["基金代碼","基金ISIN_CODE","基金名稱","計價幣別"],
        "recent":["基金代碼","ISINCODE","基金名稱","計價幣別","日期","基金淨值(金額)"],
        "dividend":["基金代碼","基金名稱","基金幣別","除息日","配息頻率"],
    }
    out=StringIO()
    writer=csv.DictWriter(out,fieldnames=config[kind])
    writer.writeheader()
    for i in range(num):
        row=dict(fixtures()[kind][0]) if i==0 else {
           key:{"基金代碼":f"DUMMY{i:04d}","基金ISIN_CODE":FUND["isin"],
                "ISINCODE":FUND["isin"],"基金名稱":f"其他基金{i}",
                "計價幣別":"美元","基金幣別":"美元",
                "日期":"20261016","基金淨值(金額)":"12.98",
                "除息日":"20260930","配息頻率":"月配"}[key]
           for key in config[kind]
        }
        writer.writerow(row)
    return out.getvalue().encode("utf-8-sig")


class TestOfficialOffshore(unittest.TestCase):
    def setUp(self):
        self.records=fixtures()
        self.patch=patch("adviser_portal.offshore_evidence.choose_instrument",return_value=FUND)
        self.patch.start()
        self.addCleanup(self.patch.stop)
    def fetcher(self,kind):
        return self.records[kind]

    def test_official_three_endpoints_provenance(self):
        self.assertTrue(m.REGISTRY["recent"]["url"].endswith("id=3-4"))
        self.assertTrue(m.REGISTRY["dividend"]["url"].endswith("id=3-5"))
        self.assertTrue(m.REGISTRY["basic"]["url"].endswith("id=3-2"))
    def test_csv_expected_field_schema_and_coverage(self):
        for name in m.REGISTRY:
            with self.subTest(source=name):
                records=m.parse_official_csv(name,as_csv(name))
                self.assertEqual(len(records),300)
    def test_source_isin_6166_and_currency_normalization(self):
        self.assertTrue(m.valid_isin(FUND["isin"]))
        self.assertFalse(m.valid_isin("IE00BD5M6701"))
        self.assertEqual(m._ccy("美元"),"USD")
        self.assertEqual(m._ccy("新臺幣"),"TWD")
        self.assertEqual(m._ccy("謎之幣別"),"")
    def test_recent_seven_day_nav_exact_identity(self):
        out=m.latest_nav(FUND["uid"],fetcher=self.fetcher,today=date(2026,10,18))
        self.assertEqual(out["status"],"OFFSHORE_RECENT_NAV_EVIDENCE_ONLY")
        self.assertEqual(out["nav"],"10.8027")
        self.assertEqual(out["nav_date"],"2026-10-16")
        self.assertEqual(out["currency"],"USD")
        self.assertEqual(len(out["recent_nav_days"]),3)
        self.assertFalse(out["usable_for_portfolio"])
        self.assertFalse(out["cash_dividend_per_share_verified"])
    def test_invalid_isin_blocks_all_network_access(self):
        bad={**FUND,"isin":"IE00BD5M6701"}
        with patch("adviser_portal.offshore_evidence.choose_instrument",return_value=bad):
            calls=[]
            out=m.latest_nav(bad["uid"],fetcher=lambda k:calls.append(k))
        self.assertEqual(out["status"],"REVIEW_REQUIRED")
        self.assertEqual(calls,[])
    def test_no_basic_identity_no_nav_even_if_recent_exists(self):
        self.records["basic"][0]["基金ISIN_CODE"]="IE00BD5M6701"
        out=m.latest_nav(FUND["uid"],fetcher=self.fetcher)
        self.assertEqual(out["status"],"REVIEW_REQUIRED")
        self.assertIsNone(out["nav"])
    def test_similar_name_cannot_steal_fund_nav(self):
        self.records["basic"][0]["基金名稱"]="紐約梅隆美國股票收益基金-美元B累積"
        out=m.latest_nav(FUND["uid"],fetcher=self.fetcher)
        self.assertEqual(out["status"],"REVIEW_REQUIRED")
    def test_different_currency_cannot_steal_nav(self):
        self.records["basic"][0]["計價幣別"]="歐元"
        out=m.latest_nav(FUND["uid"],fetcher=self.fetcher)
        self.assertEqual(out["status"],"REVIEW_REQUIRED")
    def test_duplicate_recent_nav_conflict_not_published(self):
        self.records["recent"].append(dict(self.records["recent"][-1],**{"基金淨值(金額)":"20.1"}))
        out=m.latest_nav(FUND["uid"],fetcher=self.fetcher)
        self.assertEqual(out["status"],"REVIEW_REQUIRED")
        self.assertIsNone(out["nav"])
    def test_stale_fund_warning(self):
        for x in self.records["recent"]:
            x["日期"]="20260901"
        out=m.latest_nav(FUND["uid"],fetcher=self.fetcher,today=date(2026,10,18))
        self.assertTrue(out["stale_warning"])
        self.assertEqual(out["age_calendar_days"],47)
    def test_future_nav_date_refused(self):
        for x in self.records["recent"]:
            x["日期"]="20261020"
        out=m.latest_nav(FUND["uid"],fetcher=self.fetcher,today=date(2026,10,18))
        self.assertEqual(out["status"],"REVIEW_REQUIRED")
    def test_empty_recent_file_refuses_price(self):
        self.records["recent"]=[]
        out=m.latest_nav(FUND["uid"],fetcher=self.fetcher)
        self.assertEqual(out["status"],"REVIEW_REQUIRED")
    def test_dividend_dates_but_no_assumed_per_unit_amount(self):
        r=m.dividend_events(FUND["uid"],fetcher=self.fetcher)
        self.assertEqual(r["status"],"OFFSHORE_DIVIDEND_DATE_METADATA_ONLY")
        self.assertEqual(r["event_count"],2)
        self.assertEqual(r["events"][0]["ex_date"],"2026-09-30")
        self.assertFalse(r["cash_dividend_per_share_verified"])
        self.assertFalse(r["dividend_adjustment_ready"])
        self.assertNotIn("amount_per_share",str(r))
    def test_dividend_wrong_name_and_currency_not_mixed(self):
        self.records["dividend"][0]["基金名稱"]="其他級別"
        self.records["dividend"][1]["基金幣別"]="歐元"
        out=m.dividend_events(FUND["uid"],fetcher=self.fetcher)
        self.assertEqual(out["event_count"],0)
    def test_no_isin_in_dividend_source_still_unapproved(self):
        out=m.dividend_events(FUND["uid"],fetcher=self.fetcher)
        self.assertFalse(out["class_identity_approved"])
        self.assertFalse(out["usable_for_backtest"])
    def test_modified_csv_column_fails_closed(self):
        payload=as_csv("recent").replace("基金淨值(金額)".encode("utf-8"),
                                              "淨值小計(總額)".encode("utf-8"))
        with self.assertRaises(m.OffshoreUnavailable):
            m.parse_official_csv("recent",payload)
    def test_html_response_and_too_few_rows_fail_closed(self):
        with self.assertRaises(m.OffshoreUnavailable):
            m.parse_official_csv("basic",b"<!DOCTYPE html>blocked")
        with self.assertRaises(m.OffshoreUnavailable):
            m.parse_official_csv("basic",as_csv("basic",4))
    def test_tdcc_official_dates(self):
        self.assertEqual(m._date("115/10/16"),date(2026,10,16))
        self.assertEqual(m._date("20261016"),date(2026,10,16))
        self.assertIsNone(m._date("115/13/16"))
    def test_unknown_currency_cannot_be_assumed_usd(self):
        self.records["basic"][0]["計價幣別"]="XYZ"
        out=m.latest_nav(FUND["uid"],fetcher=self.fetcher)
        self.assertEqual(out["status"],"REVIEW_REQUIRED")


class TestOffshoreHttp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.http=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        cls.url=f"http://127.0.0.1:{cls.http.server_address[1]}"
        cls.thread=Thread(target=cls.http.serve_forever,daemon=True)
        cls.thread.start()
    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.thread.join(timeout=3)
    def test_exact_offshore_nav_endpoint(self):
        payload={"status":"REVIEW_REQUIRED","instrument_uid":FUND["uid"],
                 "nav":None,"usable_for_portfolio":False}
        with patch("adviser_portal.server.latest_nav",return_value=payload) as proc:
            with urlopen(self.url+"/api/offshore-evidence?uid=off-001&part=nav",timeout=3) as r:
                data=json.load(r)
        proc.assert_called_once_with("off-001")
        self.assertEqual(data["status"],"REVIEW_REQUIRED")
    def test_exact_offshore_dividend_endpoint(self):
        with patch("adviser_portal.server.dividend_events",
                   return_value={"status":"OFFSHORE_DIVIDEND_DATE_METADATA_ONLY","event_count":0}):
            with urlopen(self.url+"/api/offshore-evidence?uid=off-001&part=dividend",timeout=3) as r:
                out=json.load(r)
        self.assertEqual(out["event_count"],0)
    def test_duplicate_and_bad_parameters_rejected(self):
        cases=["?uid=off-001&part=x","?uid=off-001&uid=other&part=nav",
               "?uid=off-001","?uid=off-001&part=nav&hack=x"]
        for suffix in cases:
            with self.subTest(suffix=suffix),self.assertRaises(HTTPError) as cm:
                urlopen(self.url+"/api/offshore-evidence"+suffix,timeout=3)
            self.assertEqual(cm.exception.code,400)
            cm.exception.close()
    def test_source_failure_returns_503(self):
        with patch("adviser_portal.server.latest_nav",side_effect=m.OffshoreUnavailable("tdcc source path")):
            with self.assertRaises(HTTPError) as cm:
                urlopen(self.url+"/api/offshore-evidence?uid=off-001&part=nav",timeout=3)
            self.assertEqual(cm.exception.code,503)
            self.assertNotIn("tdcc source path",cm.exception.read().decode())
            cm.exception.close()


if __name__=="__main__":
    unittest.main()
