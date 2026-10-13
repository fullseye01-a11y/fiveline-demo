"""Official public fund NAV lookup is separate from Excel history and read-only."""
import http.client
import json
from http.server import ThreadingHTTPServer
from threading import Thread
import unittest
from unittest.mock import patch

import backend.server as app
from backend.providers.open_fund_nav import normalized_records, parse_nav_date

def sample(source="sitca_domestic_daily"):
    if source=="sitca_domestic_daily":
        header="日期,基金代號,基金名稱,基金淨值,幣別\n"
        lines=[f"20261013,F{i:04},基金{i},12.{i%100:02},TWD" for i in range(1005)]
    else:
        header="資料日期,基金代碼,基金名稱,基金ISIN_CODE,計價幣別,淨值日期,最新淨值小計(總額)\n"
        lines=[f"20261013,A{i:04},全球基金{i},LU{i:010},USD,20261009,23.{i%100:02}" for i in range(1005)]
    return (header+"\n".join(lines)+"\n").encode("utf-8-sig")


class ProviderTests(unittest.TestCase):
    def setUp(self):
        app._official_cache.clear()

    def test_normalize_domestic_records(self):
        obj=normalized_records("sitca_domestic_daily",sample())
        self.assertEqual(obj["count"],1005)
        self.assertEqual(obj["latest_nav_date"],"2026-10-13")
        self.assertFalse(obj["share_class_matching_verified"])

    def test_normalize_tdcc_records(self):
        obj=normalized_records("tdcc_offshore_global",sample("tdcc_offshore_global"))
        self.assertEqual(obj["count"],1005)
        self.assertEqual(obj["latest_nav_date"],"2026-10-09")
        self.assertEqual(obj["records"][0]["currency"],"USD")

    def test_source_cache_prevents_repeat_requests(self):
        with patch.object(app,"download_source",return_value=sample()) as mocked:
            x=app.official_snapshot("sitca_domestic_daily")
            y=app.official_snapshot("sitca_domestic_daily")
        self.assertEqual(mocked.call_count,1)
        self.assertEqual(x["count"],y["count"])

    def test_search_never_links_fund_to_legacy_ols(self):
        with patch.object(app,"download_source",return_value=sample()):
            out=app.official_lookup("sitca_domestic_daily","基金10")
        self.assertGreater(out["total_matches"],0)
        self.assertFalse(out["matched_to_legacy_fiveline"])
        self.assertLessEqual(len(out["records"]),30)

    def test_invalid_search_is_rejected(self):
        with self.assertRaises(ValueError):
            app.official_lookup("sitca_domestic_daily","")
        with self.assertRaises(ValueError):
            app.official_snapshot("example.invalid")

    def test_legacy_status_remains_historical(self):
        status=app.service_status()
        self.assertFalse(status["live_data_connected"])
        self.assertFalse(status["auto_refresh_enabled"])
        self.assertEqual(status["snapshot_latest_nav_date"],"2026-09-01")

class EndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=ThreadingHTTPServer(("127.0.0.1",0),app.Handler)
        cls.t=Thread(target=cls.server.serve_forever,daemon=True)
        cls.t.start()
        cls.port=cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.t.join(timeout=3)

    def do(self,url,method="GET"):
        client=http.client.HTTPConnection("127.0.0.1",self.port,timeout=4)
        client.request(method,url)
        response=client.getresponse()
        result=(response.status,response.read())
        client.close()
        return result

    def test_official_search_and_status_with_mocked_provider(self):
        app._official_cache.clear()
        def fake_fetch(source):
            return sample(source)
        with patch.object(app,"download_source",side_effect=fake_fetch):
            code,data=self.do("/api/official-nav/status")
            self.assertEqual(code,200)
            self.assertTrue(all(p["available"] for p in json.loads(data)["providers"]))
            code,data=self.do("/api/official-nav/search?source=sitca_domestic_daily&q=%E5%9F%BA%E9%87%911")
            self.assertEqual(code,200)
            self.assertFalse(json.loads(data)["matched_to_legacy_fiveline"])

    def test_reject_bad_query_and_post(self):
        self.assertEqual(self.do("/api/official-nav/search?source=example&q=XY")[0],400)
        self.assertEqual(self.do("/api/official-nav/search?source=sitca_domestic_daily&q=A")[0],400)
        self.assertEqual(self.do("/api/official-nav/search",method="POST")[0],405)

if __name__=="__main__":
    unittest.main()
