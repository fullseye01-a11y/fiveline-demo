"""Issuer-sourced share-class ID overlay and third-party license gates."""
import json
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

from adviser_portal import fund_source_registry as s
from adviser_portal.server import Handler


FUNDS=[
  ("2d4e6ba412b83aa270a3","安聯台灣科技基金","DDO04","TWD","TW000T3604Y3","domestic_fund_share_class","sitca_domestic_funds"),
  ("f0fed6445d13bc10d3fb","瀚亞多重收益優化組合基金B類型-美元","DIOB3","USD","TW000T0759D2","domestic_fund_share_class","sitca_domestic_funds"),
  ("123e331851e3ae6ff190","富達基金－全球優質債券基金 (A股C月配息美元)","LU2156566916","美元","LU2156566916","offshore_fund_share_class","tdcc_offshore_funds"),
]


class TestIssuerRegistry(unittest.TestCase):
    def row(self,item):
        uid,name,code,currency,isin,kind,src=item
        return {
            "uid":uid,"name":name,"code":code,"currency":currency,
            "isin":isin if kind=="offshore_fund_share_class" else "",
            "category":kind,"source":src,
        }

    def test_three_official_isins_match_exact_share_classes(self):
        for item in FUNDS:
            with self.subTest(uid=item[0]):
                r=s.source_qualification(item[0],chooser=lambda _uid:self.row(item))
                self.assertEqual(r["status"],"ISSUER_PUBLISHED_CLASS_IDENTITY_FOUND")
                self.assertEqual(r["verified_issuer_isin"],item[4])
                self.assertTrue(s.valid_isin(item[4]))
                self.assertEqual(r["existing_catalog_code"],item[2])
                self.assertTrue(r["official_issuer_url"].startswith("https://"))
                self.assertFalse(r["commercial_data_rights_approved"])
                self.assertFalse(r["three_year_history_ready"])
                self.assertEqual(r["full_history_raw_nav_rows_imported"],0)
                self.assertFalse(r["ready_for_total_return"])
                self.assertFalse(r["usable_for_portfolio"])
                self.assertFalse(r["ready_for_six_strategies"])

    def test_wrong_currency_blocks_share_class_overlay(self):
        for item in FUNDS:
            row=self.row(item)
            row["currency"]="EUR"
            r=s.source_qualification(item[0],chooser=lambda _uid:row)
            self.assertEqual(r["status"],"CATALOG_OFFICIAL_CLASS_CONFLICT")
            self.assertIsNone(r["verified_issuer_isin"])

    def test_different_names_do_not_steal_official_isin(self):
        for item in FUNDS:
            row=self.row(item);row["name"]+="（不同級別）"
            r=s.source_qualification(item[0],chooser=lambda _uid:row)
            self.assertEqual(r["status"],"CATALOG_OFFICIAL_CLASS_CONFLICT")

    def test_catalog_isin_conflict_fail_closed(self):
        item=FUNDS[2];row=self.row(item);row["isin"]="LU2156566917"
        r=s.source_qualification(item[0],chooser=lambda _uid:row)
        self.assertEqual(r["status"],"CATALOG_ISIN_CONFLICT")
        self.assertIsNone(r["verified_issuer_isin"])

    def test_unregistered_fund_may_not_borrow_isin_by_partial_name(self):
        item=FUNDS[2]
        row=self.row(item);row["uid"]="other-fund";row["name"]="富達全球優質債券基金B美元"
        r=s.source_qualification("other-fund",chooser=lambda _uid:row)
        self.assertEqual(r["status"],"ISSUER_SHARE_CLASS_IDENTITY_NOT_YET_REVIEWED")
        self.assertIsNone(r["verified_issuer_isin"])

    def test_missing_source_regression_no_history_fabricated(self):
        for item in FUNDS:
            r=s.source_qualification(item[0],chooser=lambda _uid:self.row(item))
            self.assertFalse(r["history_csv_provider_confirmed"])
            self.assertFalse(r["issuer_actual_nav_dates_evidence_available"])
            self.assertIsNone(r["historical_per_unit_nav_source_url"])

    def test_mobile_buttons_are_readonly_and_only_on_priority_funds(self):
        script=(Path(__file__).resolve().parents[1]/"static"/"app.js").read_text("utf-8")
        self.assertIn('"核對發行公司 ISIN 與歷史來源"',script)
        self.assertIn('"/api/fund-source-qualification"',script)
        self.assertIn('report.three_year_history_ready!==false',script)
        self.assertIn('report.full_history_raw_nav_rows_imported!==0',script)


class TestSourceQualificationHttp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        cls.thread=Thread(target=cls.srv.serve_forever,daemon=True)
        cls.thread.start()
        cls.base=f"http://127.0.0.1:{cls.srv.server_address[1]}"
    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown();cls.srv.server_close();cls.thread.join(timeout=3)

    def test_exact_uid_works_without_issuer_network_or_client_input(self):
        mock={"instrument_uid":FUNDS[0][0],
              "status":"ISSUER_PUBLISHED_CLASS_IDENTITY_FOUND",
              "full_history_raw_nav_rows_imported":0,
              "commercial_data_rights_approved":False}
        with patch("adviser_portal.server.source_qualification",return_value=mock) as fn:
            with urlopen(self.base+"/api/fund-source-qualification?uid="+FUNDS[0][0],timeout=3) as resp:
                record=json.load(resp)
        self.assertEqual(record["status"],mock["status"])
        fn.assert_called_once_with(FUNDS[0][0])

    def test_bad_parameters_400_and_no_url_fetch(self):
        for q in ("?uid=x&uid=y","?uid=x&url=https://attacker.invalid",
                  "?uid=x&part=nav","?blah=0",""):
            with self.subTest(query=q),self.assertRaises(HTTPError) as ctx:
                urlopen(self.base+"/api/fund-source-qualification"+q,timeout=3)
            self.assertEqual(ctx.exception.code,400)
            ctx.exception.close()


if __name__=="__main__":
    unittest.main()
