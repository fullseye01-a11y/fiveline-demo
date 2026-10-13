"""Verify seven standard-deviation names without changing original 68-instrument snapshot."""
import json
import re
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
HTML=(ROOT/"five_line.html").read_text(encoding="utf-8")

class SevenLevelLabelsTest(unittest.TestCase):
    def test_original_instrument_data_is_present(self):
        m=re.search(r'<script id="dataset" type="application/json">([\s\S]+?)</script>', HTML)
        self.assertIsNotNone(m)
        data=json.loads(m.group(1))
        self.assertEqual(len(data),68)
        self.assertIn("1",data)
        self.assertIn("4",data)
        self.assertIn("windows",data["4"])

    def test_seven_named_lines_present(self):
        for name in ["+3SD 極樂觀","+2SD 樂觀","+1SD 相對樂觀","TL 趨勢線","-1SD 相對悲觀","-2SD 悲觀","-3SD 極悲觀"]:
            self.assertIn(name,HTML)

    def test_regression_and_sd_computations_are_retained(self):
        self.assertIn("a[2]+sd*r.sd",HTML)
        self.assertIn("a[2]-sd*r.sd",HTML)
        self.assertIn("const p=r.points",HTML)
        self.assertIn("固定 OLS",HTML)
        self.assertIn("樣本殘差 SD",HTML)

    def test_svg_has_room_for_labels(self):
        self.assertIn('viewBox="0 0 1150 400"',HTML)
        self.assertIn('x:1003',HTML)
        self.assertIn("歷史 Excel 快照",HTML)

if __name__=="__main__":
    unittest.main()
