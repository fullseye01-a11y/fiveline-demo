"""Issuer PDF fingerprinting is bounded and cannot approve dividend payouts."""
from unittest.mock import patch,MagicMock
import unittest

from adviser_portal import issuer_source_attestation as a

def fake_pdf():
    return b"%PDF-1.7\n"+b"Test issuer PDF integration bytes "+b"x"*30000+b"\n%%EOF\n"

class TestIssuerFingerprint(unittest.TestCase):
    def test_valid_official_envelope_sha(self):
        f=a.fetch_once(reader=fake_pdf)
        self.assertEqual(len(f["pdf_sha256"]),64)
        self.assertEqual(f["pdf_bytes"],len(fake_pdf()))
        self.assertFalse(f["exact_table_page_parsed"])
        self.assertEqual(f["issuer_per_unit_amounts_approved"],0)
        self.assertFalse(f["document_bytes_archived"])
        self.assertFalse(f["customer_data_read"])
    def test_rejects_non_pdf(self):
        with self.assertRaisesRegex(a.PdfFingerprintUnavailable,"complete PDF"):
            a.fetch_once(reader=lambda:b"<html>" + b"x"*30000)
    def test_rejects_missing_eof(self):
        with self.assertRaisesRegex(a.PdfFingerprintUnavailable,"complete PDF"):
            a.fetch_once(reader=lambda:fake_pdf()[:-7])
    def test_rejects_oversize_bounded(self):
        with self.assertRaisesRegex(a.PdfFingerprintUnavailable,"cap"):
            a.fetch_once(reader=lambda:fake_pdf()+b"x"*a.MAX_PDF_BYTES)
    def test_rejects_tiny_fake_pdf(self):
        with self.assertRaisesRegex(a.PdfFingerprintUnavailable,"implausible"):
            a.fetch_once(reader=lambda:b"%PDF\n%%EOF")
    def test_no_retry_if_reader_fails(self):
        calls=[]
        def failure():
            calls.append(1)
            raise a.PdfFingerprintUnavailable("offline")
        with self.assertRaises(a.PdfFingerprintUnavailable):
            a.fetch_once(reader=failure)
        self.assertEqual(len(calls),1)
    def test_same_envelope_changes_with_bytes(self):
        x=a.fingerprint_pdf(fake_pdf())
        y=a.fingerprint_pdf(fake_pdf().replace(b"integration",b"INTEGRATION"))
        self.assertNotEqual(x["pdf_sha256"],y["pdf_sha256"])

if __name__=="__main__":
    unittest.main()
