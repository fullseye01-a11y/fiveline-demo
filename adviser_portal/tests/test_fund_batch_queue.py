"""Every public fund record gets a deterministic source-readiness queue state."""
import json
import unittest
from http.server import ThreadingHTTPServer
from threading import Thread
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

from adviser_portal.fund_batch_queue import queue_page
from adviser_portal.server import Handler

BASE=[
 {"uid":"d1","source":"sitca_domestic_funds","category":"domestic_fund_share_class",
  "code":"DDA01","name":"台幣基金A","currency":"TWD","isin":""},
 {"uid":"d2","source":"sitca_domestic_funds","category":"domestic_fund_share_class",
  "code":"DDA01","name":"美元基金B","currency":"USD","isin":""},
 {"uid":"d3","source":"sitca_domestic_funds","category":"domestic_fund_share_class",
  "code":"DDA03","name":"基金C","currency":"TWD","isin":"TW000T3604Y3"},
 {"uid":"o1","source":"tdcc_offshore_funds","category":"offshore_fund_share_class",
  "code":"LU2156566916","name":"美元級別A","currency":"USD","isin":"LU2156566916"},
 {"uid":"o2","source":"tdcc_offshore_funds","category":"offshore_fund_share_class",
  "code":"LU2156566917","name":"假ISIN級別","currency":"USD","isin":"LU2156566917"},
 {"uid":"o3","source":"tdcc_offshore_funds","category":"offshore_fund_share_class",
  "code":"DOP01","name":"共享ISIN級別","currency":"EUR","isin":"LU2156566916"},
 {"uid":"s1","source":"twse_listed_stocks","category":"listed_stock",
  "code":"2330","name":"台積電","currency":"TWD","isin":""},
]
def snap(rows=BASE):
    return {"items":rows,"checked_at_utc":"2026-10-18T12:00:00Z"}


class BatchQueueTests(unittest.TestCase):
    def test_all_six_funds_have_a_state_and_stock_excluded(self):
        report=queue_page(snap())
        self.assertEqual(report["catalog_fund_records_checked"],6)
        self.assertEqual(report["domestic_fund_records"],3)
        self.assertEqual(report["offshore_fund_records"],3)
        self.assertEqual(len(report["rows"]),6)
        self.assertEqual(len({r["uid"] for r in report["rows"]}),6)
        self.assertEqual(report["all_fund_status_counts"]["MISSING_ISIN"],2)
        self.assertEqual(report["all_fund_status_counts"]["INVALID_ISIN"],1)
        self.assertEqual(report["all_fund_status_counts"]["SHARED_ISIN_IDENTITY_REVIEW"],2)
        self.assertEqual(report["all_fund_status_counts"]["AWAITING_LICENSED_NAV_SOURCE"],1)
        self.assertEqual(report["all_fund_blocker_counts"]["REUSED_SOURCE_CODE_REVIEW"],2)
        self.assertEqual(report["history_nav_3y_approved_records"],0)
        self.assertEqual(report["history_nav_3y_uploaded_records"],0)
        self.assertEqual(report["database_rows_written"],0)
        self.assertFalse(report["portfolio_or_strategies_modified"])

    def test_missing_isin_and_reused_code_kept_as_separate_share_classes(self):
        r=queue_page(snap())
        d=[x for x in r["rows"] if x["uid"] in ("d1","d2")]
        self.assertEqual(len(d),2)
        for item in d:
            self.assertEqual(item["queue_state"],"MISSING_ISIN")
            self.assertIn("REUSED_SOURCE_CODE_REVIEW",item["blockers"])

    def test_complete_identity_is_still_awaiting_authorized_history(self):
        r=queue_page(snap())
        item=next(x for x in r["rows"] if x["uid"]=="d3")
        self.assertEqual(item["queue_state"],"AWAITING_LICENSED_NAV_SOURCE")
        self.assertFalse(item["official_share_class_identity_approved"])
        self.assertFalse(item["authorized_3y_nav_file_received"])
        self.assertEqual(item["history_rows_accepted"],0)

    def test_snapshot_identity_stable_across_catalog_reordering(self):
        a=queue_page(snap(BASE))
        b=queue_page(snap(list(reversed(BASE))))
        self.assertEqual(a["snapshot_sha256"],b["snapshot_sha256"])
        self.assertEqual(a["rows"],b["rows"])

    def test_next_page_requires_snapshot_and_preserves_exact_fund_ids(self):
        first=queue_page(snap(),limit=2)
        self.assertEqual(first["next_offset"],2)
        with self.assertRaisesRegex(ValueError,"snapshot"):
            queue_page(snap(),offset=2,limit=2)
        second=queue_page(snap(),offset=2,limit=2,
                          expected_snapshot=first["snapshot_sha256"])
        third=queue_page(snap(),offset=4,limit=2,
                         expected_snapshot=first["snapshot_sha256"])
        uids=[x["uid"] for part in (first,second,third) for x in part["rows"]]
        self.assertEqual(len(uids),6)
        self.assertEqual(len(set(uids)),6)
        self.assertIsNone(third["next_offset"])

    def test_changed_public_catalog_forces_restart_and_no_partial_merging(self):
        one=queue_page(snap(),limit=2)
        changed=[dict(x) for x in BASE]
        changed[0]["name"]="修正版不同名稱"
        with self.assertRaisesRegex(ValueError,"changed"):
            queue_page(snap(changed),offset=2,limit=2,
                       expected_snapshot=one["snapshot_sha256"])

    def test_wrong_category_source_pair_and_duplicate_uid_fail_closed(self):
        with self.assertRaisesRegex(ValueError,"source mismatch"):
            queue_page(snap([{**BASE[0],"source":"tdcc_offshore_funds"}]))
        with self.assertRaisesRegex(ValueError,"UID"):
            queue_page(snap([BASE[0],dict(BASE[0])]))

    def test_selected_scope_is_paged_without_cross_market_funds(self):
        r=queue_page(snap(),kind="offshore",limit=2)
        self.assertEqual(r["selected_scope_records"],3)
        self.assertTrue(all(x["category"]=="offshore_fund_share_class" for x in r["rows"]))
        self.assertEqual(r["domestic_fund_records"],3)
        r2=queue_page(snap(),kind="domestic",limit=50)
        self.assertEqual(len(r2["rows"]),3)

    def test_bad_pagination_and_random_tokens_refused(self):
        for args in ({"kind":"stocks"},{"offset":-1},{"offset":1.5},
                     {"offset":1},{"limit":0},{"limit":201},
                     {"expected_snapshot":"wrong"}):
            with self.subTest(args=args),self.assertRaises(ValueError):
                queue_page(snap(),**args)

    def test_full_9767_records_partition_without_omitting_any(self):
        # Synthetic 4,472+5,295 public identities; NOT fake historical prices.
        rows=[]
        for kind,count,prefix,src,ccy in (
            ("domestic_fund_share_class",4472,"d","sitca_domestic_funds","TWD"),
            ("offshore_fund_share_class",5295,"o","tdcc_offshore_funds","USD")
        ):
            for i in range(count):
                rows.append({"uid":f"{prefix}{i:05d}",
                             "source":src,"category":kind,"code":f"{prefix.upper()}{i:05d}",
                             "name":f"合成來源級別{i}","currency":ccy,"isin":""})
        first=queue_page(snap(rows),limit=100)
        last=queue_page(snap(rows),offset=9700,limit=100,
                        expected_snapshot=first["snapshot_sha256"])
        self.assertEqual(first["catalog_fund_records_checked"],9767)
        self.assertEqual(first["domestic_fund_records"],4472)
        self.assertEqual(first["offshore_fund_records"],5295)
        self.assertEqual(first["total_pages_at_page_size"],98)
        self.assertEqual(first["page_records_checked"],100)
        self.assertEqual(last["page_records_checked"],67)
        self.assertEqual(last["selected_scope_records"],9767)
        self.assertIsNone(last["next_offset"])
        self.assertEqual(sum(first["all_fund_status_counts"].values()),9767)
        self.assertEqual(first["history_nav_3y_approved_records"],0)
        self.assertFalse(first["commercial_data_rights_verified"])

    def test_mobile_queue_wired_to_safe_paged_api(self):
        folder=Path(__file__).resolve().parents[1]/"static"
        html=(folder/"index.html").read_text(encoding="utf-8")
        js=(folder/"app.js").read_text(encoding="utf-8")
        css=(folder/"style.css").read_text(encoding="utf-8")
        for text in ("allfund-queue-button","allfund-queue-next",
                     "allfund-queue-kind","allfund-queue-results"):
            self.assertIn(text,html)
        self.assertIn('"/api/fund-batch-queue"',js)
        self.assertIn("history_nav_3y_approved_records!==0",js)
        self.assertIn("allFundBatch.snapshot",js)
        self.assertIn(".allfund-queue-scroller",css)


class BatchQueueHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        cls.url=f"http://127.0.0.1:{cls.srv.server_address[1]}"
        cls.thread=Thread(target=cls.srv.serve_forever,daemon=True)
        cls.thread.start()
    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown();cls.srv.server_close();cls.thread.join(timeout=2)

    def test_public_batch_100_and_snapshot_next_page(self):
        with patch("adviser_portal.catalog.get_catalog",return_value=snap()):
            with urlopen(self.url+"/api/fund-batch-queue?limit=2",timeout=3) as res:
                first=json.load(res)
            second_url=self.url+"/api/fund-batch-queue?limit=2&offset=2&snapshot="+first["snapshot_sha256"]
            with urlopen(second_url,timeout=3) as res:
                second=json.load(res)
        self.assertEqual(first["page_records_checked"],2)
        self.assertEqual(second["offset"],2)
        self.assertEqual(second["snapshot_sha256"],first["snapshot_sha256"])
        self.assertFalse(second["customer_data_read"])

    def test_no_remote_source_url_or_magic_history_upload_http(self):
        for suffix in ("?offset=2","?source=https://fake.invalid","?uid=other",
                       "?limit=201","?limit=0","?snapshot=bad",
                       "?kind=all&kind=offshore"):
            with self.subTest(suffix=suffix),self.assertRaises(HTTPError) as e:
                urlopen(self.url+"/api/fund-batch-queue"+suffix,timeout=3)
            self.assertEqual(e.exception.code,400)
            e.exception.close()


if __name__=="__main__":
    unittest.main()
