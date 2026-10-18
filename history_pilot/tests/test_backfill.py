import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backfill


def official_fixture(url, params, *, omit_code=None, invalid=False):
    if url == backfill.TWSE_URL:
        return {
            "stat": "OK",
            "fields": [
                "日期", "成交股數", "成交金額", "開盤價",
                "最高價", "最低價", "收盤價",
            ],
            "data": [
                ["115/09/30", "1000", "101000", "100", "102", "99", "101"]
            ],
        }
    day = params["d"]
    rows = [
        ["8069", "100", "102", "99", "101", "1000", "101000"],
        ["6488", "200", "205", "198", "202", "1000", "202000"],
    ]
    if omit_code:
        rows = [r for r in rows if r[0] != omit_code]
    if invalid:
        rows[0][2] = "90"  # high < close; reject entire remote batch
    return {
        "reportDate": day,
        "tables": [{
            "fields": ["代號", "開盤", "最高", "最低", "收盤", "成交股數", "成交金額(元)"],
            "data": rows,
        }],
    }


class TestBoundedBackfill(unittest.TestCase):
    def test_precise_plan(self):
        plan = backfill.request_plan()
        self.assertEqual(len(plan), 7)
        self.assertEqual([p["market"] for p in plan], ["TWSE"] * 2 + ["TPEX"] * 5)
        self.assertEqual([p["code"] for p in plan[:2]], ["2330", "0050"])
        self.assertEqual([p["day"] for p in plan[2:]], list(backfill.TPEX_DATES))
        self.assertEqual(backfill.plan_summary()["postgres_write"], False)

    def test_local_stage_exercises_all_seven_requests(self):
        with patch.object(backfill, "fetch_json", side_effect=official_fixture) as fetch, \
             patch.object(backfill.time, "sleep"):
            result = backfill.run_backfill(connect_to_neon=False)
        self.assertEqual(fetch.call_count, 7)
        self.assertEqual(result["status"], "LOCAL_STAGE_ONLY")
        self.assertEqual(result["staged_version_count"], 12)
        self.assertFalse(result["uploaded_to_neon"])

    def test_missing_selected_stock_halts_entire_run(self):
        def missing(url, params):
            return official_fixture(url, params, omit_code="6488")
        with patch.object(backfill, "fetch_json", side_effect=missing), \
             patch.object(backfill.time, "sleep"), \
             patch.object(backfill, "persist_sqlite") as persisted:
            with self.assertRaisesRegex(RuntimeError, "no Neon sync"):
                backfill.run_backfill(connect_to_neon=False)
            persisted.assert_not_called()

    def test_bad_high_price_halts(self):
        def broken(url, params):
            return official_fixture(url, params, invalid=True)
        with patch.object(backfill, "fetch_json", side_effect=broken), \
             patch.object(backfill.time, "sleep"), \
             patch.object(backfill, "persist_sqlite") as persisted:
            with self.assertRaisesRegex(RuntimeError, "no Neon sync"):
                backfill.run_backfill(connect_to_neon=False)
            persisted.assert_not_called()

    def test_neon_must_be_configured_before_http(self):
        with patch.object(backfill, "configured_dsn", return_value=None), \
             patch.object(backfill, "fetch_json") as fetch:
            with self.assertRaisesRegex(RuntimeError, "no official requests"):
                backfill.run_backfill()
            fetch.assert_not_called()

    def test_postgres_one_transaction_after_all_batches(self):
        with patch.object(backfill, "configured_dsn", return_value="private-test-dsn"), \
             patch.object(backfill, "fetch_json", side_effect=official_fixture), \
             patch.object(backfill.time, "sleep"), \
             patch.object(backfill, "persist_sqlite", return_value={
                 "incoming": 12, "inserted": 10, "unchanged": 2, "pending": 0
             }) as persisted, \
             patch.object(backfill, "private_summary", return_value={
                 "approved_bars": 26, "pending_review": 0,
             }):
            result = backfill.run_backfill()
        self.assertEqual(persisted.call_count, 1)
        self.assertEqual(result["neon_inserted_versions"], 10)
        self.assertEqual(result["neon_previously_present_versions"], 2)
        self.assertEqual(result["neon_approved_total"], 26)

    def test_request_throttling_minimum(self):
        with self.assertRaisesRegex(ValueError, "at least"):
            backfill.run_backfill(connect_to_neon=False, delay=0.1)


if __name__ == "__main__":
    unittest.main()
