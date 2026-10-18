"""One-shot Fidelity publisher PDF integrity fingerprint (not payout approval).

Obtain exact bytes from the *fixed* official HTTPS issuer URL only. This
procedure intentionally does not publish or archive copyrighted PDF bytes and
does not use a price feed. It verifies transport/file envelope and SHA-256,
not the parsed issuer share-class tables. Publisher payout amounts remain a
manual review sample until source content is independently parsed and audited.
"""
from __future__ import annotations

from hashlib import sha256
from urllib.parse import urlsplit
from urllib.request import Request,urlopen
import ssl

from .issuer_pdf_review import ISSUER_PDF_URL

MAX_PDF_BYTES=10_000_000
MIN_PDF_BYTES=25_000


class PdfFingerprintUnavailable(Exception):
    pass


def fingerprint_pdf(raw: bytes):
    if not isinstance(raw,bytes) or not MIN_PDF_BYTES<=len(raw)<=MAX_PDF_BYTES:
        raise PdfFingerprintUnavailable("issuer PDF empty, implausible or over cap")
    if raw[:5]!=b"%PDF-" or b"%%EOF" not in raw[-2048:]:
        raise PdfFingerprintUnavailable("issuer document not a complete PDF envelope")
    return {"pdf_sha256":sha256(raw).hexdigest(),
            "pdf_bytes":len(raw),
            "pdf_header_present":True,
            "pdf_eof_present":True,
            "exact_table_page_parsed":False,
            "issuer_per_unit_amounts_approved":0,
            "customer_data_read":False,
            "document_bytes_archived":False}


def fetch_once(reader=None):
    """One bounded request, no retries, redirects allowed only to exact URL."""
    if reader:
        return fingerprint_pdf(reader())
    req=Request(ISSUER_PDF_URL,headers={
        "User-Agent":"Mozilla/5.0 (compatible; FivelineIssuerResearch/0.1)",
        "Accept":"application/pdf","Accept-Encoding":"identity",
        "Connection":"close"
    })
    try:
        with urlopen(req,timeout=35,context=ssl.create_default_context()) as resp:
            uri=urlsplit(resp.geturl())
            original=urlsplit(ISSUER_PDF_URL)
            if (resp.status!=200 or uri.scheme!="https" or
                uri.hostname!=original.hostname or uri.path!=original.path or
                uri.query!=original.query):
                raise PdfFingerprintUnavailable("publisher PDF source redirected")
            data=resp.read(MAX_PDF_BYTES+1)
    except (OSError,TimeoutError) as exc:
        raise PdfFingerprintUnavailable("issuer PDF fetch failed") from exc
    return fingerprint_pdf(data)
