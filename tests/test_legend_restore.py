"""Ensure only legend suffixes are restored; original OLS data and slider remain."""
from pathlib import Path
from html.parser import HTMLParser
import re
import unittest

ROOT=Path(__file__).resolve().parents[1]
PAGE=(ROOT/"five_line_responsive.html").read_text(encoding="utf-8")

class LegendRestoreTests(unittest.TestCase):
    def test_seven_sd_suffixes_restored(self):
        legend=PAGE.split('<div class="legend"',1)[1].split('</div>\n<p class="info">',1)[0]
        for label,sd in [
          ("極端超買區","+3SD"),("超買警戒區","+2SD"),("強勢偏多區","+1SD"),
          ("中心趨勢線","0SD"),("弱勢回檔區","-1SD"),("超賣警戒區","-2SD"),("極端超賣區","-3SD")]:
            self.assertIn(f"{label} {sd}</div>",legend)
    def test_original_svg_math_slider_and_links_preserved(self):
        for token in ('id="timeline"','function updateTimeline()','fetch("five_line.html"',
                      'var width=compact?720:1200,height=compact?470:455',
                      'a[2]+pair[0]*ctx.sd','id="visible-range"',
                      '迴歸通道位階：+3SD ~ -3SD'):
            self.assertIn(token,PAGE)
    def test_no_asset_credentials(self):
        self.assertNotIn("YmI1MWFl",PAGE)

if __name__=="__main__":unittest.main()
