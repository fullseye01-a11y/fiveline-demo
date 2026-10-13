"""Official fund identity evidence UI never claims reconciled historical performance."""
from pathlib import Path
import re
import unittest

ROOT=Path(__file__).resolve().parents[1]
PAGE=(ROOT/"nav_class_evidence.html").read_text("utf-8")
HOME=(ROOT/"index.html").read_text("utf-8")
NAV=(ROOT/"nav_status.html").read_text("utf-8")
REVIEW=(ROOT/"nav_match_review.html").read_text("utf-8")

class PageTests(unittest.TestCase):
    def test_entry_points(self):
        self.assertIn('href="nav_class_evidence.html"',HOME)
        self.assertIn('href="nav_class_evidence.html"',NAV)
        self.assertIn('href="nav_class_evidence.html"',REVIEW)
        self.assertIn('href="nav_match_review.html"',PAGE)
    def test_fail_closed(self):
        for term in ['reviewed_approved!==0','historical_nav_backfilled!==false',
                     'cash_distributions_adjusted!==false','splits_adjusted!==false',
                     'share_class_approved!==false','api/official-nav/class-evidence',
                     'name="viewport"','aria-live="polite"','最近七日']:
            self.assertIn(term,PAGE)
        self.assertNotIn("innerHTML=",PAGE)
        self.assertNotIn("localStorage",PAGE)
        self.assertNotIn("YmI1MWFl",PAGE)
        self.assertEqual(len(re.findall(r'<script>(.*?)</script>',PAGE,re.S)),1)

if __name__=="__main__":
    unittest.main()
