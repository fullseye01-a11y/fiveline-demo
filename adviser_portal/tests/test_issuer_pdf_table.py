"""Offline strict GLIAM A-dollar cash monthly PDF table verification tests."""
from unittest.mock import patch
import unittest

from adviser_portal import issuer_pdf_table as v

TEXT="""2026 年 7月
SITE-MKT-EXMP_2607-01
富達基金–全球優質債券基金
GLIAM 富達基金–全球優質債券基金 (A 股 C 月配息美元)
除息日 報酬率日期 每單位 配息基準日 淨值 單月配息率 單月報酬率 配息組成項目
07/01/26 06/26 0.0580 8.1940 0.71% 0.63 100.00% 0.00%
06/01/26 05/26 0.0580 8.2010 0.71% 0.86 100.00% 0.00%
05/01/26 04/26 0.0580 8.1890 0.71% 1.48 45.70% 54.30%
04/01/26 03/26 0.0575 8.1270 0.71% -2.85 54.46% 45.54%
03/02/26 02/26 0.0596 8.4250 0.71% 0.82 98.15% 1.85%
02/02/26 01/26 0.0596 8.4160 0.71% 0.59 100.00% 0.00%
01/02/26 12/25 0.0596 8.4260 0.71% 0.35 100.00% 0.00%
12/01/25 11/25 0.0599 8.4570 0.71% 0.29 100.00% 0.00%
11/03/25 10/25 0.0601 8.4930 0.71% 0.64 57.10% 42.90%
10/01/25 09/25 0.0602 8.4990 0.71% 0.94 100.00% 0.00%
09/01/25 08/25 0.0600 8.4800 0.71% 0.84 48.08% 51.92%
08/01/25 07/25 0.0599 8.4690 0.71% 0.84 84.92% 15.08%
BGIGU 富達基金–全球優質債券基金 (B 股 C 月配息美元)
07/01/26 06/26 0.0672 9.4980 0.71% 0.53 100.00% 0.00%
06/01/26 05/26 0.0673 9.5150 0.71% 0.79 100.00% 0.00%
"""

class TestFidelityGLLIAMPage(unittest.TestCase):
    def test_real_source_layout_fixture_parses_only_A_class(self):
        result=v.parse_gliam_page(TEXT)
        self.assertEqual(len(result),12)
        self.assertEqual(result[0],("2026-07-01","0.0580","100.00","0.00"))
        self.assertEqual(result[-1],("2025-08-01","0.0599","84.92","15.08"))
        self.assertFalse(any(row[1]=="0.0672" for row in result))

    def test_WRONG_B_price_in_A_row_fails(self):
        with self.assertRaisesRegex(v.IssuerPdfTableUnverified,"differ"):
            v.parse_gliam_page(TEXT.replace(
                "07/01/26 06/26 0.0580 8.1940",
                "07/01/26 06/26 0.0672 8.1940"))

    def test_missing_target_or_B_boundary_fails(self):
        with self.assertRaises(v.IssuerPdfTableUnverified):
            v.parse_gliam_page(TEXT.replace("GLIAM","GLIAU"))
        with self.assertRaises(v.IssuerPdfTableUnverified):
            v.parse_gliam_page(TEXT.replace("BGIGU","GIBXX"))

    def test_wrong_class_description_fails(self):
        with self.assertRaisesRegex(v.IssuerPdfTableUnverified,"class header"):
            v.parse_gliam_page(TEXT.replace("(A 股 C 月配息美元)","(A 股 C 月配息歐元)",1))

    def test_missing_row_fails(self):
        with self.assertRaisesRegex(v.IssuerPdfTableUnverified,"twelve"):
            v.parse_gliam_page(TEXT.replace(
                "05/01/26 04/26 0.0580 8.1890 0.71% 1.48 45.70% 54.30%",""))

    def test_wrong_ratio_fails(self):
        with self.assertRaises(v.IssuerPdfTableUnverified):
            v.parse_gliam_page(TEXT.replace(
                "05/01/26 04/26 0.0580 8.1890 0.71% 1.48 45.70% 54.30%",
                "05/01/26 04/26 0.0580 8.1890 0.71% 1.48 80.00% 20.00%"))

    def test_wrong_pdf_edition_fails(self):
        with self.assertRaises(v.IssuerPdfTableUnverified):
            v.parse_gliam_page(TEXT.replace("2026 年 7月","2026 年 10月"))

    def test_probe_must_verify_exact_downloaded_binary_identity(self):
        with patch.object(v,"fingerprint_pdf",return_value={
            "pdf_sha256":v.PDF_SHA256_REGISTERED,
            "pdf_bytes":v.PDF_BYTES_REGISTERED,
        }):
            result=v.verify_single_official_version(
                fetcher=lambda:b"%PDF-1.7 unit-test",
                decoder=lambda data,page:TEXT)
        self.assertEqual(result["verified_ex_date_count"],12)
        self.assertEqual(result["issuer_per_unit_amounts_verified_against_pdf"],12)
        self.assertEqual(result["issuer_per_unit_payouts_reconciled_with_TDCC"],0)
        self.assertFalse(result["portfolio_usage_approved"])
        self.assertFalse(result["total_return_approved"])
        self.assertTrue(result["doc_verification_complete"])
        self.assertEqual(result["page_number"],33)

    def test_mismatched_hash_rejects_table_without_decoder_call(self):
        called=[]
        with patch.object(v,"fingerprint_pdf",return_value={
            "pdf_sha256":"00"*32,"pdf_bytes":v.PDF_BYTES_REGISTERED}):
            with self.assertRaisesRegex(v.IssuerPdfTableUnverified,"version changed"):
                v.verify_single_official_version(
                    fetcher=lambda:b"pdf fake",
                    decoder=lambda *args:called.append(1))
        self.assertEqual(called,[])

    def test_pdf_text_decoder_fail_closed_when_no_binary_parser_installed(self):
        with patch.object(v.shutil,"which",return_value=None),\
             patch.object(v.importlib,"import_module",side_effect=ModuleNotFoundError("pypdf")):
            with self.assertRaisesRegex(v.IssuerPdfTableUnverified,"no trusted"):
                v.extract_page_text(b"%PDF-1.7")

if __name__=="__main__":
    unittest.main()
