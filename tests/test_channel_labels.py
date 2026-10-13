"""Regression-channel terminology and data calculation invariants."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "five_line.html"

class ChannelLabelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = PATH.read_text(encoding="utf-8")

    def test_heading_distinguishes_channel_from_actual_position(self):
        self.assertIn("迴歸通道位階：+3SD ~ -3SD", self.html)
        self.assertIn("目前位置：—", self.html)
        self.assertIn("document.getElementById('zone').textContent='目前位置：'+r.zone", self.html)
        self.assertIn("document.getElementById('zone').textContent='目前位置：歷史不足'", self.html)

    def test_seven_objective_channel_labels(self):
        names = ["極端超買區", "超買警戒區", "強勢偏多區", "中心趨勢線", "弱勢回檔區", "超賣警戒區", "極端超賣區"]
        self.assertTrue(all(name in self.html for name in names))
        for sd,name,color in [
            (3,"極端超買區","colors.up"),
            (2,"超買警戒區","colors.up"),
            (1,"強勢偏多區","colors.up"),
            (0,"中心趨勢線","colors.tl"),
            (-1,"弱勢回檔區","colors.down"),
            (-2,"超賣警戒區","colors.down"),
            (-3,"極端超賣區","colors.down"),
        ]:
            self.assertIn(f"[{sd},'{name}',{color}]", self.html)
        self.assertNotIn("極樂觀線", self.html)
        self.assertNotIn("極悲觀線", self.html)

    def test_unchanged_regression_formula_and_chart(self):
        self.assertIn("a[2]+sd*r.sd", self.html)
        self.assertIn("a[2]-sd*r.sd", self.html)
        self.assertIn("r.points", self.html)
        self.assertIn("r.zone", self.html)
        self.assertIn("const db=JSON.parse(document.getElementById('dataset').textContent)", self.html)
        self.assertIn("filterFunds();", self.html)
        self.assertIn('id="plot" viewBox="0 0 1140 400"', self.html)
        self.assertGreater(len(self.html), 2500000)

if __name__ == "__main__":
    unittest.main()
