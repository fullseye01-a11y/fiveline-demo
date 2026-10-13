"""Fund ISIN/recent NAV/dividend evidence endpoint tests with synthetic public data."""
import json
import http.client
from http.server import ThreadingHTTPServer
from threading import Thread
import unittest
from unittest.mock import patch

import backend.server as app

def fake_report():
    rows=[]
    for i in range(1,69):
        chosen=i==1
        rows.append({
          "id":str(i),"name":"TDCC test fund" if chosen else f"Fund{i}",
          "currency":"USD","nav_end":"2026-08-31","approved":False,
          "status":"single_unverified_candidate" if chosen else "no_exact_candidate",
          "candidate_count":1 if chosen else 0,
          "candidates":[{"source":"tdcc_offshore_global","code":"TEST001","isin":"US0378331005",
            "name":"TDCC test fund","currency":"USD","nav_date":"2026-10-13","nav":"12.1"}] if chosen else []
        })
    return {"mode":"read_only_matching_review","legacy_count":68,"records":rows,
            "reviewed_and_approved_count":0,"any_legacy_nav_overwritten":False}

def sample(key):
    if key=="tdcc_offshore_basics":
        return [{"基金代碼":"TEST001","基金ISIN_CODE":"US0378331005",
                  "基金名稱":"TDCC test fund","計價幣別":"USD"}]
    if key=="tdcc_offshore_recent_nav":
        return [{"基金代碼":"TEST001","ISINCODE":"US0378331005",
                  "基金名稱":"TDCC test fund","計價幣別":"USD","日期":"20261013","基金淨值(金額)":"12.1"}]
    return [{"基金代碼":"TEST001","基金名稱":"TDCC test fund","基金幣別":"USD",
             "除息日":"20261010","配息頻率":"月配"}]

class EndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=ThreadingHTTPServer(("127.0.0.1",0),app.Handler)
        cls.t=Thread(target=cls.server.serve_forever,daemon=True)
        cls.t.start()
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.t.join(timeout=3)
    def setUp(self):
        app._class_evidence_cache.clear()
    def test_review_endpoint_has_68_without_approved_or_adjusted_nav(self):
        with patch.object(app,"matching_review",return_value=fake_report()),\
             patch.object(app,"fetch_official",return_value=b"synthetic"),\
             patch.object(app,"load_csv",side_effect=lambda k,raw:((),sample(k))):
            port=self.server.server_address[1]
            client=http.client.HTTPConnection("127.0.0.1",port,timeout=3)
            client.request("GET","/api/official-nav/class-evidence")
            response=client.getresponse()
            data=json.loads(response.read())
            client.close()
        self.assertEqual(response.status,200)
        self.assertEqual(data["total_legacy"],68)
        self.assertEqual(data["official_metadata_consistent_not_approved"],1)
        self.assertFalse(data["historical_nav_backfilled"])
        self.assertFalse(data["cash_distributions_adjusted"])
        self.assertFalse(data["splits_adjusted"])
        self.assertFalse(any(x["approved"] for x in data["records"]))
    def test_post_not_allowed(self):
        client=http.client.HTTPConnection("127.0.0.1",self.server.server_address[1],timeout=3)
        client.request("POST","/api/official-nav/class-evidence")
        res=client.getresponse()
        self.assertEqual(res.status,405)
        res.read()
        client.close()

if __name__=="__main__":
    unittest.main()
