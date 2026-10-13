"""Public NAV history UI stays read-only and cannot confuse collections with NAV dates."""
from pathlib import Path
import re
import unittest

ROOT=Path(__file__).resolve().parents[1]
UI=(ROOT/"nav_history_quality.html").read_text("utf-8")
HOME=(ROOT/"index.html").read_text("utf-8")
STATUS=(ROOT/"nav_status.html").read_text("utf-8")
WF=(ROOT/".github/workflows/persistent_nav_history.yml").read_text("utf-8")

class QualityPageTests(unittest.TestCase):
    def test_navigation_and_versioned_report(self):
        self.assertIn('href="nav_history_quality.html"',HOME)
        self.assertIn('href="nav_history_quality.html"',STATUS)
        self.assertIn("official-nav-history/public_nav_history/reports/latest.json",UI)
        self.assertIn('href="nav_status.html"',UI)
        self.assertIn('name="viewport"',UI)
    def test_distinct_dates_not_capture_events(self):
        for token in ["unique_fund_effective_date_pairs","archive_snapshot_files",
                      "repeated_collection_observations",
                      "distinct_effective_nav_dates","same_day_value_conflict_funds",
                      "legacy_to_first_observation_calendar_span_days",
                      "calendar_span_is_not_missing_trading_days!==true"]:
            self.assertIn(token,UI)
        self.assertIn('data.approved_funds!==0',UI)
        self.assertIn('id="csv"',UI)
        self.assertNotIn("innerHTML=",UI)
        self.assertNotIn("localStorage",UI)
        self.assertNotIn("YmI1MWFl",UI)
        self.assertEqual(len(re.findall(r'<script>(.*?)</script>',UI,re.S)),1)
    def test_persist_before_sqlite_merge(self):
        # Store original official NAV revisions even when SQLite refuses conflicts.
        self.assertLess(WF.index("Build immutable"),WF.index("Verify duplicates"))
        self.assertLess(WF.index("Verify duplicates"),WF.index("Append to official-nav-history"))
        self.assertLess(WF.index("Append to official-nav-history"),WF.index("Rebuild temporary SQLite"))
        self.assertIn("tools.audit_public_nav_quality",WF)
        self.assertIn("public_nav_history/reports/latest.json",WF)

if __name__=="__main__":unittest.main()
