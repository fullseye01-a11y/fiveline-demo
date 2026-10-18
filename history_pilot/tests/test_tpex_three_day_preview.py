"""Three-day TPEx quote preview remains isolated, bounded and ephemeral."""
from datetime import date
from pathlib import Path
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import tpex_three_day_preview as m

CLOSURES={"2026-09-25","2026-09-28","2026-10-09","2026-10-26"}
DATES=("2026-10-06","2026-10-07","2026-10-08","2026-10-12","2026-10-13")
SOURCE="source-2026"
def records():
    return [("TPEX",code,date.fromisoformat(day)) for code in m.PILOT_CODES for day in DATES]

def quote(date_roc, missing=None, bad=False):
    rows=[
        ["8069","100","102","99","101","1000","101000"],
        ["6488","200","205","198","202","1000","202000"],
    ]
    if missing:
        rows=[x for x in rows if x[0]!=missing]
    if bad:
        rows[0][2]="98"
    return {
        "reportDate":date_roc,
        "tables":[{
            "fields":["代號","開盤","最高","最低","收盤","成交股數","成交金額(元)"],
            "data":rows,
        }]
    }

class ThreeDayPrivateStage(unittest.TestCase):
    def setUp(self):
        self.requests=[]
        self.delays=[]
    def quotes(self,url,params):
        self.assertEqual(url,m.TPEX_URL)
        self.assertEqual(params["se"],"EW")
        self.requests.append(params["d"])
        return quote(params["d"])
    def execute(self,quotes=None,read=None,pdf_digest=None):
        digest=pdf_digest or m.SOURCE_SHA256_20261018
        with patch.object(m,"extract_official_pdf",return_value="official synthetic pdf"),\
             patch.object(m,"validate_pdf_text",return_value=CLOSURES):
            return m.preview("fake-private-neon",
                official_pdf_fetch=lambda:(b"%PDF-1.7 TEST",digest),
                quotes_fetch=quotes or self.quotes,
                read_dates=read or (lambda dsn:records()),
                wait=self.delays.append)
    def test_three_bounded_official_requests_six_private_rows(self):
        o=self.execute()
        self.assertEqual(self.requests,["115/10/01","115/10/02","115/10/05"])
        self.assertEqual(self.delays,[1.2,1.2])
        self.assertEqual(o["private_staged_source_rows"],6)
        self.assertTrue(o["ephemeral_raw_sqlite_deleted"])
        self.assertFalse(o["neon_write_performed"])
        self.assertFalse(o["raw_quotes_publicly_redistributed"])
    def test_no_neon_dsn_refuses_all_requests(self):
        fetch=MagicMock()
        with self.assertRaisesRegex(RuntimeError,"Neon"):
            m.preview(None,official_pdf_fetch=fetch)
        fetch.assert_not_called()
    def test_changed_pdf_digest_blocks_fetch(self):
        fetch=MagicMock()
        with self.assertRaisesRegex(ValueError,"digest changed"):
            self.execute(quotes=fetch,pdf_digest="new-unknown-pdf-digest")
        fetch.assert_not_called()
    def test_missing_symbol_blocks_archive(self):
        def bad(url,params):
            return quote(params["d"],missing="6488")
        with self.assertRaisesRegex(ValueError,"unavailable/ambiguous"):
            self.execute(quotes=bad)
    def test_bad_ohlc_cannot_be_staged(self):
        def bad(url,params):
            return quote(params["d"],bad=True)
        with self.assertRaisesRegex(ValueError,"unavailable/ambiguous"):
            self.execute(quotes=bad)
    def test_existing_neon_day_cancels_preview(self):
        seen=records()+[("TPEX","6488",date(2026,10,1))]
        fetch=MagicMock()
        with self.assertRaisesRegex(ValueError,"no longer exactly missing"):
            self.execute(read=lambda dsn:seen,quotes=fetch)
        fetch.assert_not_called()
    def test_missing_old_pilot_date_blocks_preview(self):
        seen=records()[1:]
        fetch=MagicMock()
        with self.assertRaisesRegex(ValueError,"no longer exactly missing"):
            self.execute(read=lambda dsn:seen,quotes=fetch)
        fetch.assert_not_called()
    def test_source_date_mismatch_blocks_stage(self):
        def bad(url,params):
            return quote("115/10/13")
        with self.assertRaisesRegex(ValueError,"differs"):
            self.execute(quotes=bad)
    def test_request_parameter_guard(self):
        with self.assertRaises(AssertionError):
            m.request_params("2026-10-09")
    def test_no_business_secrets_in_report(self):
        o=self.execute()
        rendered=str(o)
        self.assertNotIn("open",rendered)
        self.assertNotIn("close",rendered)
        self.assertNotIn("volume_shares",rendered)
        self.assertNotIn("turnover_twd",rendered)
        self.assertNotIn("fake-private-neon",rendered)

if __name__=="__main__":
    unittest.main()
