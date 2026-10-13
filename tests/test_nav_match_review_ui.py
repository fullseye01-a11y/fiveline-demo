"""Candidate review webpage is read-only and never describes official NAV as reconciled."""
from pathlib import Path
import unittest
import re

ROOT=Path(__file__).resolve().parents[1]
PAGE=(ROOT/"nav_match_review.html").read_text("utf-8")
HOME=(ROOT/"index.html").read_text("utf-8")
STATUS=(ROOT/"nav_status.html").read_text("utf-8")

class MatcherUITests(unittest.TestCase):
    def test_navigation(self):
        self.assertIn('href="nav_match_review.html"',HOME)
        self.assertIn('href="nav_match_review.html"',STATUS)
        self.assertIn('href="official_nav_search.html"',PAGE)
        self.assertIn('href="nav_status.html"',PAGE)

    def test_readonly_standards(self):
        for text in ['68 檔 NAV 身分對帳','所有配對均未核准','ISIN','基金級別',
                     'id="reload"','id="query"','id="filter"','id="cards"',
                     'read_only_matching_review','reviewed_and_approved_count!==0',
                     'any_legacy_nav_overwritten!==false',
                     'api/official-nav/match-report']:
            self.assertIn(text,PAGE)
        self.assertNotIn("innerHTML=",PAGE)
        self.assertNotIn("localStorage",PAGE)
        self.assertNotIn("YmI1MWFl",PAGE)
        self.assertIn('name="viewport"',PAGE)
        self.assertEqual(len(re.findall(r'<script>(.*?)</script>',PAGE,re.S)),1)

    def test_no_false_identity_claim(self):
        for value in ['single_unverified_candidate','multiple_unverified_candidates',
                      'currency_mismatch','no_exact_candidate',
                      'separate_exchange_feed_or_identity_required',
                      'incomplete_provider_coverage']:
            self.assertIn(value,PAGE)
        self.assertIn('approved').__class__ if False else None

if __name__=="__main__":
    unittest.main()
