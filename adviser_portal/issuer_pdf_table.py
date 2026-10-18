"""Read-only source-locked Fidelity GLIAM PDF table content verifier.

This parser is intentionally NOT a background importer or a public price API.
It reads page 33 ONLY, isolates the GLIAM A USD monthly share-class table from
adjacent BGIGU B USD share-class data, parses twelve dated distribution rows,
and compares exact amounts/proportions to a separately reviewed transcription.

A verified PDF SHA does not by itself establish table content. We require
BOTH source binary proof and an actual PDF text decoder. A missing decoder,
changed file, changed share-class heading, unexpected row count or any
amount/rate mismatch fails closed (no fallback to manually asserted success).
"""
from __future__ import annotations

from datetime import datetime, date, timezone
from decimal import Decimal
from io import BytesIO
import importlib
import re
import shutil
import subprocess
import unicodedata

from .issuer_pdf_review import (
    EXCERPT, PDF_BYTES_REGISTERED, PDF_SHA256_REGISTERED,
    TARGET_FIDELITY_CLASS, TARGET_ISIN, ISSUER_PDF_URL
)
from .issuer_source_attestation import download_pdf_once, fingerprint_pdf

PDF_PAGE_NUMBER=33
B_SHARE_CLASS_CODE="BGIGU"
SOURCE_VERSION="2026-07"
SCHEMA="fiveline_fidelity_gl iam_pdf_page_table_verified_v1".replace(" ","")
# Row: ex-date, return month, distribution USD/unit, ex-date ref NAV,
# distribution rate, calendar-month total return, income%, capital%.
ROW_RE=re.compile(
    r"(?<!\d)(\d{2})/(\d{2})/(\d{2})\s+"
    r"(\d{2})/(\d{2})\s+"
    r"(\d+\.\d{4})\s+(\d+\.\d{4})\s+"
    r"(\d+\.\d+)%\s+(-?\d+\.\d+)\s+"
    r"(\d+\.\d+)%\s+(\d+\.\d+)%",
)


class IssuerPdfTableUnverified(Exception):
    pass


def extract_page_text(pdf_bytes:bytes, *, decoder=None):
    """Use a real PDF text decoder, never OCR or raw gzip/string guessing."""
    if decoder is not None:
        return decoder(pdf_bytes, PDF_PAGE_NUMBER)
    if shutil.which("pdftotext"):
        try:
            completed=subprocess.run(
                ["pdftotext","-f",str(PDF_PAGE_NUMBER),
                 "-l",str(PDF_PAGE_NUMBER),"-enc","UTF-8",
                 "-layout","-","-"],
                input=pdf_bytes,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                timeout=18,check=False,
            )
            if completed.returncode==0:
                decoded=completed.stdout.decode("utf-8",errors="strict")
                if decoded.strip():
                    return decoded
        except (subprocess.SubprocessError,UnicodeError,OSError):
            pass
    try:
        pypdf=importlib.import_module("pypdf")
        reader=pypdf.PdfReader(BytesIO(pdf_bytes),strict=True)
        if reader.is_encrypted or len(reader.pages)<PDF_PAGE_NUMBER:
            raise IssuerPdfTableUnverified("PDF page count / encryption changed")
        return reader.pages[PDF_PAGE_NUMBER-1].extract_text(extraction_mode="plain") or ""
    except (ModuleNotFoundError,ImportError) as exc:
        raise IssuerPdfTableUnverified(
            "no trusted PDF text decoder: pdftotext or pypdf required"
        ) from exc
    except IssuerPdfTableUnverified:
        raise
    except Exception as exc:
        raise IssuerPdfTableUnverified("official PDF page text extraction failed") from exc


def parse_gl iam_page(page_text:str):
    """Extract exactly one target class, ending before next B share class."""
    if not isinstance(page_text,str) or not page_text.strip():
        raise IssuerPdfTableUnverified("Fidelity PDF page text empty")
    txt=unicodedata.normalize("NFKC",page_text).replace("\u00a0"," ")
    if not re.search(r"2026\s*年",txt) or not re.search(r"7\s*月",txt):
        raise IssuerPdfTableUnverified("PDF edition / month changed")
    if txt.count(TARGET_FIDELITY_CLASS)!=1 or txt.count(B_SHARE_CLASS_CODE)!=1:
        raise IssuerPdfTableUnverified("A/B share class markers missing or repeated")
    a=txt.index(TARGET_FIDELITY_CLASS)
    b=txt.index(B_SHARE_CLASS_CODE)
    if b<=a:
        raise IssuerPdfTableUnverified("Fidelity A/B class block order changed")
    heading=txt[a:a+150]
    norm="".join(c for c in heading if c.isalnum())
    if "全球優質債券基金" not in norm or "A股C月配息美元" not in norm:
        raise IssuerPdfTableUnverified("issuer class header not exact A C distribution USD")
    body=txt[a:b]
    normalized=re.sub(r"\s+"," ",body)
    matches=list(ROW_RE.finditer(normalized))
    if len(matches)!=len(EXCERPT):
        raise IssuerPdfTableUnverified("issuer table expected exactly twelve rows")
    values=[]
    for mt in matches:
        month,day,yr,ret_month,ret_year,payout,nav,dist_pct,monthly_ret,inc,principal=mt.groups()
        try:
            ex_date=date(2000+int(yr),int(month),int(day)).isoformat()
        except ValueError as exc:
            raise IssuerPdfTableUnverified("issuer source contains invalid ex-date") from exc
        if int(ret_year)>99 or int(ret_month)<1 or int(ret_month)>12:
            raise IssuerPdfTableUnverified("issuer return reference month malformed")
        if Decimal(nav)<=0 or Decimal(payout)<=0 or Decimal(dist_pct)<0:
            raise IssuerPdfTableUnverified("issuer dollar amount / NAV inconsistent")
        if not (Decimal("0")<=Decimal(inc)<=100 and Decimal("0")<=Decimal(principal)<=100 and
                abs(Decimal(inc)+Decimal(principal)-Decimal(100))<=Decimal(".1")):
            raise IssuerPdfTableUnverified("issuer composition percentage invalid")
        values.append((ex_date,payout,inc,principal))
    # Independent review excerpt is a strict second gate: never correct a
    # scraped value silently, and never use the neighboring B-class amount.
    if tuple(values)!=EXCERPT:
        raise IssuerPdfTableUnverified("GLIAM PDF rows differ from reviewed excerpt")
    return values


def verify_single_official_version(*, fetcher=None, decoder=None):
    """One HTTPS download + SHA check + actual PDF page parse; no DB access."""
    raw=fetcher() if fetcher is not None else download_pdf_once()
    observed=fingerprint_pdf(raw)
    if (observed["pdf_sha256"]!=PDF_SHA256_REGISTERED or
        observed["pdf_bytes"]!=PDF_BYTES_REGISTERED):
        raise IssuerPdfTableUnverified("official PDF version changed; refuse old table")
    extracted=extract_page_text(raw,decoder=decoder)
    rows=parse_gl iam_page(extracted)
    return {
        "schema":SCHEMA,
        "status":"PUBLISHER_DOCUMENT_TABLE_AUTOMATICALLY_VERIFIED",
        "issuer": "Fidelity International",
        "issuer_source":ISSUER_PDF_URL,
        "pdf_sha256":observed["pdf_sha256"],
        "pdf_bytes":observed["pdf_bytes"],
        "page_number":PDF_PAGE_NUMBER,
        "publisher_class_code":TARGET_FIDELITY_CLASS,
        "matching_catalog_isin":TARGET_ISIN,
        "pdf_edition":SOURCE_VERSION,
        "verified_ex_date_count":len(rows),
        "date_first":rows[-1][0],"date_last":rows[0][0],
        "doc_verification_complete":True,
        # TDCC has dates / ratio but no independently approved amounts:
        # this is verification *against the PDF*, not a total-return gate.
        "issuer_per_unit_amounts_verified_against_pdf":len(rows),
        "issuer_per_unit_payouts_reconciled_with_TDCC":0,
        "historical_nav_three_years_verified":False,
        "total_return_approved":False,
        "portfolio_usage_approved":False,
        "production_strategies_modified":False,
        "customer_data_accessed":False,
        "pdf_bytes_publicly_distributed":False,
    }
