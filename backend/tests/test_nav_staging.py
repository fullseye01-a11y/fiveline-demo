"""Unit tests for safe staging behavior. Run: python -m unittest discover -s backend/tests -v"""
import io
import sqlite3
import unittest

from backend.nav_staging import ingest_csv

HEADER = "instrument_id,nav_date,nav_value,currency,share_class,provider\n"


class NavStagingTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")

    def tearDown(self):
        self.db.close()

    def run_batch(self, text, batch="unit-test"):
        return ingest_csv(io.StringIO(HEADER + text), self.db, batch)

    def count(self):
        return self.db.execute("select count(*) from nav_staging").fetchone()[0]

    def test_stages_valid_rows_as_pending_only(self):
        result = self.run_batch("0050,2026-09-01,108.60,TWD,ETF,official-source\n")
        self.assertEqual(result["staged_rows_committed"], 1)
        self.assertEqual(self.count(), 1)
        self.assertEqual(self.db.execute("select state from nav_staging").fetchone()[0], "pending_review")

    def test_idempotent_import(self):
        row = "0050,2026-09-01,108.60,TWD,ETF,official-source\n"
        self.run_batch(row, "a")
        result = self.run_batch(row, "b")
        self.assertEqual(result["already_staged"], 1)
        self.assertEqual(result["staged_rows_committed"], 0)
        self.assertEqual(self.count(), 1)

    def test_conflict_rolls_back_without_overwrite(self):
        row = "0050,2026-09-01,108.60,TWD,ETF,official-source\n"
        self.run_batch(row, "a")
        result = self.run_batch("0050,2026-09-01,120.00,TWD,ETF,official-source\n", "b")
        self.assertEqual(result["state"], "rolled_back")
        self.assertEqual(result["conflicts"], 1)
        self.assertEqual(self.db.execute("select nav_value from nav_staging").fetchone()[0], "108.60")

    def test_invalid_entry_rolls_back_all_new_rows(self):
        data = "0050,2026-09-01,108.60,TWD,ETF,official-source\n" + "0050,2026-09-02,-1,TWD,ETF,official-source\n"
        result = self.run_batch(data)
        self.assertEqual(result["rejected"], 1)
        self.assertEqual(result["state"], "rolled_back")
        self.assertEqual(self.count(), 0)

    def test_validates_dates_and_missing_share_class(self):
        result = self.run_batch("0050,2026-02-30,100,TWD,ETF,official-source\n" + "F001,2026-09-01,23.1,USD,,official-source\n")
        self.assertEqual(result["rejected"], 2)
        self.assertEqual(self.count(), 0)

    def test_requires_column_header(self):
        with self.assertRaisesRegex(ValueError, "CSV header"):
            ingest_csv(io.StringIO("symbol,price\n0050,123\n"), self.db, "x")


if __name__ == "__main__":
    unittest.main()
