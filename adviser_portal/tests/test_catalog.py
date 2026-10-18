"""Market identity directory tests; never fetch public GitHub during CI."""
from __future__ import annotations

from http.server import ThreadingHTTPServer
from threading import Thread
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen
import json
import unittest
from pathlib import Path

from adviser_portal import catalog
from adviser_portal.server import Handler


def fixture():
    items = [
        ("d1", "sitca_domestic_funds", "domestic_fund_share_class", "DDA01", "安聯台灣科技基金", "TWD", ""),
        ("d2", "sitca_domestic_funds", "domestic_fund_share_class", "DDA01", "安聯台灣科技基金另一級別", "USD", ""),
        ("o1", "tdcc_offshore_funds", "offshore_fund_share_class", "ACCP135", "全球多重收益基金", "USD", "LU0123456789"),
        ("e1", "twse_etfs", "etf", "0050", "元大台灣50 ETF", "TWD", ""),
        ("e2", "tpex_etfs", "etf", "006201", "上櫃 ETF", "TWD", ""),
        ("s1", "twse_listed_stocks", "listed_stock", "2330", "台積電", "TWD", ""),
        ("s2", "tpex_otc_stocks", "otc_stock", "6488", "環球晶", "TWD", ""),
    ]
    records = [{
        "uid":uid,"source":src,"category":kind,"code":code,"name":name,
        "currency":ccy,"isin":isin,
        "class_identity_approved":False,"historical_prices_ready":False
    } for uid,src,kind,code,name,ccy,isin in items]
    counts={}
    for item in records:
        counts[item["category"]]=counts.get(item["category"],0)+1
    return {"schema":"fiveline_market_universe_v1",
            "checked_at_utc":"2026-10-16T14:48:04+00:00",
            "covered_sources":list(catalog.ALLOWED_SOURCES),
            "catalog_complete_for_configured_sources":True,
            "total_rows":len(records),"category_counts":counts,
            "known_coverage_gaps":["不是全球所有基金"],
            "instruments":records}


class TestCatalogIdentity(unittest.TestCase):
    def setUp(self):
        self.catalog=catalog.validate_catalog(fixture())
        self.patcher=patch.object(catalog,"get_catalog",return_value=self.catalog)
        self.mock_get=self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_directory_stats_count_not_shareclass_approval(self):
        self.assertEqual(self.catalog["total_rows"],7)
        self.assertEqual(self.catalog["category_counts"]["domestic_fund_share_class"],2)
        self.assertFalse(any(x["class_identity_approved"] for x in self.catalog["items"]))

    def test_same_code_different_classes_not_merged(self):
        r=catalog.search_catalog(query="DDA01")
        self.assertEqual(r["matched_rows"],2)
        self.assertNotEqual(r["items"][0]["uid"],r["items"][1]["uid"])
        self.assertFalse(r["can_add_real_portfolio"])

    def test_unicode_name_normalization(self):
        r=catalog.search_catalog(query="台灣科技",kind="domestic_fund_share_class")
        self.assertEqual(r["matched_rows"],2)
        self.assertEqual(r["items"][1]["currency"],"USD")

    def test_etf_two_market_pagination(self):
        a=catalog.search_catalog(kind="etf",offset=0,limit=1)
        b=catalog.search_catalog(kind="etf",offset=1,limit=1)
        self.assertEqual(a["matched_rows"],2)
        self.assertNotEqual(a["items"][0]["uid"],b["items"][0]["uid"])
        self.assertEqual(b["items"][0]["source"],"tpex_etfs")

    def test_name_code_isin_search(self):
        self.assertEqual(catalog.search_catalog(query="LU0123456789")["items"][0]["code"],"ACCP135")
        self.assertEqual(catalog.search_catalog(query="2330",kind="listed_stock")["matched_rows"],1)

    def test_invalid_search_params(self):
        invalid=[
            {"query":"x"*81},{"kind":"random"},{"offset":-1},{"limit":0},
            {"offset":True},{"limit":100},{"query":2}
        ]
        for x in invalid:
            with self.subTest(x=x),self.assertRaises(ValueError):
                catalog.search_catalog(**x)

    def test_incomplete_sources_fail_closed(self):
        doc=fixture()
        doc["covered_sources"].remove("tpex_etfs")
        with self.assertRaises(catalog.CatalogUnavailable):
            catalog.validate_catalog(doc)

    def test_duplicate_uid_rejected(self):
        doc=fixture()
        doc["instruments"][1]["uid"]=doc["instruments"][0]["uid"]
        with self.assertRaisesRegex(catalog.CatalogUnavailable,"級別"):
            catalog.validate_catalog(doc)

    def test_count_mismatch_rejected(self):
        doc=fixture()
        doc["total_rows"]=100
        with self.assertRaisesRegex(catalog.CatalogUnavailable,"統計"):
            catalog.validate_catalog(doc)

    def test_explicit_refresh_rate_limited(self):
        self.patcher.stop()  # Exercise real cache; mock only the network.
        with patch.object(catalog,"_download",return_value=self.catalog) as downloader:
            old_cache,old_time=catalog._cache,catalog._last_fetch
            catalog._cache=None
            catalog._last_fetch=0
            try:
                catalog.get_catalog(refresh=True)
                catalog.get_catalog(refresh=True)
                self.assertEqual(downloader.call_count,1)
            finally:
                catalog._cache,catalog._last_fetch=old_cache,old_time

    def test_static_mobile_page_has_catalog(self):
        base=Path(__file__).resolve().parents[1]/"static"
        html=(base/"index.html").read_text(encoding="utf-8")
        js=(base/"app.js").read_text(encoding="utf-8")
        css=(base/"style.css").read_text(encoding="utf-8")
        for key in ('data-panel="catalog"', 'id="catalog-query"',
                    'id="catalog-kind"', 'id="catalog-more"', 'id="catalog-selected"'):
            self.assertIn(key,html)
        self.assertIn("/api/catalog",js)
        self.assertIn("catalog.selected",js)
        self.assertIn("@media(max-width:600px)",css)


class TestCatalogHttp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        cls.url=f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.thread=Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def test_end_to_end_paged_market_catalog(self):
        snap=catalog.validate_catalog(fixture())
        with patch("adviser_portal.server.search_catalog",side_effect=lambda **k: catalog.search_catalog(**k)),\
             patch.object(catalog,"get_catalog",return_value=snap):
            with urlopen(self.url+"/api/catalog?query=0050&kind=etf&limit=1",timeout=3) as r:
                data=json.load(r)
        self.assertEqual(data["matched_rows"],1)
        self.assertEqual(data["items"][0]["code"],"0050")
        self.assertFalse(data["customer_data_saved"])
        self.assertFalse(data["real_prices_available"])

    def test_invalid_or_duplicate_query_rejected(self):
        for path in ("/api/catalog?kind=nope", "/api/catalog?query=x&query=y",
                     "/api/catalog?offset=-10","/api/catalog?refresh=x"):
            with self.subTest(path=path):
                with self.assertRaises(HTTPError) as cm:
                    urlopen(self.url+path,timeout=3)
                self.assertEqual(cm.exception.code,400)
                cm.exception.close()

    def test_source_error_returns_503_not_fabricated_data(self):
        with patch("adviser_portal.server.search_catalog",
                   side_effect=catalog.CatalogUnavailable("remote offline")):
            with self.assertRaises(HTTPError) as cm:
                urlopen(self.url+"/api/catalog?query=abc",timeout=3)
            self.assertEqual(cm.exception.code,503)
            self.assertNotIn("remote offline",cm.exception.read().decode("utf-8"))
            cm.exception.close()


if __name__=="__main__":
    unittest.main()
