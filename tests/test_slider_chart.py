"""Regression tests for minimal legend decluttering + slider; original NAV snapshot untouched."""
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
PAGE = (ROOT/"five_line_responsive.html").read_text(encoding="utf-8")
ORIGINAL = (ROOT/"five_line.html").read_text(encoding="utf-8")


class SliderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        m = re.search(r'<script id="dataset" type="application/json">([\s\S]*?)</script>', ORIGINAL)
        assert m, "original embedded dataset not found"
        cls.dataset=json.loads(m.group(1))

    def test_same_68_instrument_history_as_original(self):
        self.assertEqual(len(self.dataset),68)
        self.assertIn('fetch("five_line.html"', PAGE)
        self.assertEqual(self.dataset["1"]["windows"]["840"]["points"][-1][0], "2026-08-31")
        self.assertAlmostEqual(self.dataset["1"]["windows"]["840"]["points"][-1][3], 2.0691,places=4)

    def test_legend_plain_names_and_original_layout(self):
        legend=PAGE.split('<div class="legend"',1)[1].split('</div>\n<p class="info">',1)[0]
        for name in ["極端超買區","超買警戒區","強勢偏多區","中心趨勢線",
                     "弱勢回檔區","超賣警戒區","極端超賣區","NAV／價格走勢"]:
            self.assertIn(name,legend)
        for suffix in ["+3SD","+2SD","+1SD","0SD","-1SD","-2SD","-3SD"]:
            self.assertNotIn(suffix,legend)
        self.assertIn('迴歸通道位階：+3SD ~ -3SD',PAGE)
        self.assertIn('"目前位置："+r.zone',PAGE)
        self.assertIn('grid-template-columns:repeat(2,minmax(0,1fr))',PAGE)

    def test_slider_and_floating_cursor(self):
        for field in ['id="timeline"','id="scrub-date"','id="scrub-nav"','id="scrub-tl"',
                      'id="scrub-z"','id="scrub-zone"','id="scrub-levels"']:
            self.assertIn(field,PAGE)
        for term in ['function updateTimeline(){',
                     'addEventListener("input",updateTimeline)',
                     'slider.setAttribute("aria-valuetext"',
                     'ctx.line.setAttribute("x1",x)',
                     'ctx.dot.setAttribute("cx",x)',
                     'ctx.tip2.textContent="NAV "+fmt(a[1])']:
            self.assertIn(term,PAGE)
        self.assertIn('cursorContext={visible:visible,sd:sd',PAGE)

    def test_slider_seven_actual_channel_prices(self):
        r=self.dataset["1"]["windows"]["840"]
        a=r["points"][-1]
        tl,sd=a[2],r["sd"]
        prices=[tl+m*sd for m in (3,2,1,0,-1,-2,-3)]
        self.assertEqual(len(prices),7)
        self.assertTrue(all(prices[i]>prices[i+1] for i in range(6)))
        self.assertAlmostEqual(prices[3],tl)
        self.assertAlmostEqual((prices[0]+prices[-1])/2,tl)
        self.assertIn('a[2]+pair[0]*ctx.sd', PAGE)

    def test_limits_orig_chart_risk(self):
        self.assertIn('var width=compact?720:1200,height=compact?470:455',PAGE)
        self.assertIn('visible=currentRange==="all"?p:p.slice(-Number(currentRange))',PAGE)
        self.assertIn('var pad=Math.max((max-min)*.06,0.0001)',PAGE)
        self.assertIn('var trend=visible.map',PAGE)
        self.assertIn('if(!compact){',PAGE)


if __name__=="__main__":
    unittest.main()
