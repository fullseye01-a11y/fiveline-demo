"""Check the website does not expose private approvals or write production NAV."""
from pathlib import Path
import re
import unittest

ROOT=Path(__file__).resolve().parents[1]
PAGE=(ROOT/"nav_backfill_preflight.html").read_text("utf-8")
HOME=(ROOT/"index.html").read_text("utf-8")
NAV=(ROOT/"nav_status.html").read_text("utf-8")
QUALITY=(ROOT/"nav_history_quality.html").read_text("utf-8")
WORKFLOW=(ROOT/".github/workflows/persistent_nav_history.yml").read_text("utf-8")

class FrontendTests(unittest.TestCase):
    def test_links_and_mobile(self):
        for page in (HOME,NAV,QUALITY):
            self.assertIn('href="nav_backfill_preflight.html"',page)
        self.assertIn('href="nav_history_quality.html"',PAGE)
        self.assertIn('name="viewport"',PAGE)
        self.assertIn("public_nav_history/reports/backfill_preflight.json",PAGE)
    def test_public_only_fail_closed(self):
        for token in [
             "production_backfill_authorized!==false","production_excel_changed!==false",
             "x.read_only_report!==true","x.evidence_submitted!==false",
             "eligible_for_production_backfill!==false",
             "正式回補","歷史 NAV","id=\"download\"","csvCell(value)"]:
            self.assertIn(token,PAGE)
        self.assertNotIn("innerHTML=",PAGE)
        self.assertNotIn("localStorage",PAGE)
        self.assertNotIn("YmI1MWFl",PAGE)
        self.assertEqual(len(re.findall(r'<script>(.*?)</script>',PAGE,re.S)),1)
    def test_nightly_archive_verifies_68_and_does_not_submit_private_evidence(self):
        self.assertIn("python -m tools.nav_backfill_preflight",WORKFLOW)
        self.assertIn("backfill_preflight.json",WORKFLOW)
        self.assertIn("staging_eligible_count']==0",WORKFLOW)
        self.assertIn("not r['evidence_submitted']",WORKFLOW)
        self.assertNotIn("--evidence",WORKFLOW)
        self.assertLess(WORKFLOW.index("Evaluate 68-fund preflight"),WORKFLOW.index("Append to official-nav-history"))

if __name__=="__main__":
    unittest.main()
