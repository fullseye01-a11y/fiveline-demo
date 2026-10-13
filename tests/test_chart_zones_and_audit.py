"""Regression gates for real iPhone chart source, not generated mockups."""
from pathlib import Path
import json
import re
import unittest

ROOT=Path(__file__).resolve().parents[1]
CHART=(ROOT/"five_line_responsive.html").read_text("utf-8")
EVIDENCE=(ROOT/"nav_class_evidence.html").read_text("utf-8")
ORIG=(ROOT/"five_line.html").read_text("utf-8")

class ChartAndAuditTests(unittest.TestCase):
    def test_68_legacy_records_and_z_unchanged(self):
        obj=json.loads(re.search(r'<script id="dataset" type="application/json">(.*?)</script>',ORIG,re.S).group(1))
        self.assertEqual(len(obj),68)
        self.assertEqual(obj["1"]["windows"]["840"]["points"][-1][0],"2026-08-31")
        self.assertAlmostEqual(obj["1"]["windows"]["840"]["points"][-1][3],2.0691,places=4)
        self.assertIn('fetch("five_line.html"',CHART)
    def test_chart_labels_never_occlude_lines(self):
        self.assertIn('id="zone-name"',CHART)
        self.assertIn('id="scrub-zone-name"',CHART)
        self.assertIn('id="timeline"',CHART)
        self.assertIn('byId("scrub-zone-name").textContent=zoneNameForZ(Number(a[3]))',CHART)
        self.assertIn('byId("zone-name").textContent="所在位階："+zoneNameForZ(Number(last[3]))',CHART)
        self.assertNotIn('tipRect=svgEl',CHART)
        self.assertNotIn('ctx.rect.setAttribute',CHART)
        self.assertNotIn('ctx.tip1',CHART)
        self.assertNotIn('ctx.tip2',CHART)
        self.assertIn('cursorGroup.append(cursorLine,cursorDot)',CHART)
        self.assertIn('ctx.dot.setAttribute("cx",x)',CHART)
    def test_sd_legend_names_and_ols_math_preserved(self):
        for label,sd in [
            ("極端超買區","+3SD"),("超買警戒區","+2SD"),
            ("強勢偏多區","+1SD"),("中心趨勢線","0SD"),
            ("弱勢回檔區","-1SD"),("超賣警戒區","-2SD"),
            ("極端超賣區","-3SD")]:
            self.assertIn(label+" "+sd+"</div>",CHART)
        for v in ['a[2]+3*sd','a[2]-3*sd','currentRange==="all"?p:p.slice(-Number(currentRange))',
                  'svg.setAttribute("preserveAspectRatio","xMidYMid meet")']:
            self.assertIn(v,CHART)
    def test_export_only_pending_68_class_reconciliation(self):
        for v in ['id="download-audit"','exportIdentityAudit()',
                  'reviewed_approved!==0','share_class_approved!==false',
                  '是否核准正式回補','每單位配息金額及幣別人工確認',
                  '可用完整歷史NAV來源與授權','"否",x.reason']:
            self.assertIn(v,EVIDENCE)
        self.assertIn('if(/^\\s*[=+\\-@\\t\\r]/.test(v))',EVIDENCE)
        self.assertNotIn("innerHTML=",EVIDENCE)
        self.assertNotIn("localStorage",EVIDENCE)

if __name__=="__main__":
    unittest.main()
