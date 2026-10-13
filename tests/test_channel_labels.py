"""Validate original five-line data and seven names are retained."""
from html.parser import HTMLParser
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "five_line.html"

class ChannelLabelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = PATH.read_text(encoding="utf-8")

    def test_has_all_seven_channel_titles(self):
        names = ["極樂觀線", "樂觀線", "相對樂觀線", "趨勢線", "相對悲觀線", "悲觀線", "極悲觀線"]
        self.assertTrue(all(name in self.html for name in names))

    def test_extremes_mapped_to_three_sd_and_midline(self):
        self.assertIn("[3,'極樂觀線',colors.up]", self.html)
        self.assertIn("[0,'趨勢線',colors.tl]", self.html)
        self.assertIn("[-3,'極悲觀線',colors.down]", self.html)
        self.assertIn("a[2]+sd*r.sd", self.html)
        self.assertIn("a[2]-sd*r.sd", self.html)

    def test_preserves_fund_selector_and_ols_windows(self):
        self.assertIn("const db=JSON.parse(document.getElementById('dataset').textContent)", self.html)
        self.assertIn("filterFunds();", self.html)
        self.assertIn("svg.append(elem('circle'", self.html)
        self.assertIn('id="plot" viewBox="0 0 1140 400"', self.html)
        self.assertGreater(len(self.html), 2500000)

if __name__ == "__main__":
    unittest.main()
