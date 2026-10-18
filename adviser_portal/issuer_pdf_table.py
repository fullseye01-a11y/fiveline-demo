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
SCHEMA="fiveline_fidelity_gliam_pdf_page_table_verified_v1"
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


def parse_gliam_page(page_text:str):
    """Extract exactly one target class, ending before next B share class."""
    if not isinstance(page_text,str) or not page_text.strip():
        raise IssuerPdfTableUnverified("Fidelity PDF page text empty")
    txt=unicodedata.normalize("NFKC",page_text).replace("\u00a0"," ")
    # Some page text decoders assign the decorated "2026 年" masthead
    # to the previous PDF page. The official edition identifier is printed
    # inside this page's content and is separately pinned by SHA-256.
    edition=re.search(r"SITE-MKT-EXMP_(\d{4})-01",txt)
    if edition is None:
        raise IssuerPdfTableUnverified("PDF edition code absent from text decoder")
    if edition.group(1)!="2607":
        raise IssuerPdfTableUnverified("PDF edition does not match pinned July excerpt; edition="+edition.group(1))
    if not re.search(r"7\s*月",txt):
        raise IssuerPdfTableUnverified("PDF cover month 7 absent from extracted page")
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



# The PDF URL is not immutable: 2026-10 current-version source SHA was
# already captured in staging. Preserve the 2026-07 excerpt only as a
# separate overlap check; never treat July as the current PDF edition.
CURRENT_MARKER="2610"
CURRENT_LABEL="2026-10"
CURRENT_FIRST_MONTH=(2026,10)
CURRENT_LAST_MONTH=(2025,11)
PREVIOUS_TRANSCRIPTION={date_str:(price,inc,principal)
                        for date_str,price,inc,principal in EXCERPT}


def parse_current_oct_gliam_page(page_text:str):
    """Validate 12 consecutive monthly A USD records in the *2610* PDF."""
    if not isinstance(page_text,str) or not page_text.strip():
        raise IssuerPdfTableUnverified("Current issuer PDF table text is empty")
    txt=unicodedata.normalize("NFKC",page_text).replace("\u00a0"," ")
    # SHA-256 of the actual source is checked by the caller as well.
    marker=re.search(r"SITE-MKT-EXMP_(\d{4})-01",txt)
    if marker is None or marker.group(1)!=CURRENT_MARKER:
        raise IssuerPdfTableUnverified("Pinned PDF does not contain expected 2610 edition")
    if not re.search(r"10\s*月",txt):
        raise IssuerPdfTableUnverified("Pinned PDF does not display October masthead")
    if txt.count(TARGET_FIDELITY_CLASS)!=1 or txt.count(B_SHARE_CLASS_CODE)!=1:
        raise IssuerPdfTableUnverified("Current PDF GLIAM / BGIGU markers missing or repeated")
    a=txt.index(TARGET_FIDELITY_CLASS)
    b=txt.index(B_SHARE_CLASS_CODE)
    if a>=b:
        raise IssuerPdfTableUnverified("Current A / B classes are not in expected order")
    heading="".join(c for c in txt[a:a+180] if c.isalnum())
    if "全球優質債券基金" not in heading or "A股C月配息美元" not in heading:
        raise IssuerPdfTableUnverified("Current PDF A USD share-class heading mismatch")
    normalized=re.sub(r"\s+"," ",txt[a:b])
    records=list(ROW_RE.finditer(normalized))
    if len(records)!=12:
        raise IssuerPdfTableUnverified("Current PDF GLIAM table needs 12 actual rows, observed="+str(len(records)))
    values=[]
    seen_months=set()
    for match in records:
        mm,dd,yy,ref_mm,ref_yy,payout,nav,rate,monthly_ret,inc,principal=match.groups()
        try:
            date_obj=date(2000+int(yy),int(mm),int(dd))
        except ValueError as exc:
            raise IssuerPdfTableUnverified("Current PDF invalid ex-date") from exc
        y,m=date_obj.year,date_obj.month
        if (y,m) in seen_months:
            raise IssuerPdfTableUnverified("Current PDF repeated one month")
        seen_months.add((y,m))
        cash=Decimal(payout); ref_nav=Decimal(nav); ratio=Decimal(rate)
        income=Decimal(inc); capital=Decimal(principal)
        if not (cash>0 and ref_nav>0 and Decimal("0")<ratio<Decimal("100") and
                abs(income+capital-Decimal("100"))<=Decimal(".10")):
            raise IssuerPdfTableUnverified("Current PDF invalid per-unit NAV / payout or ratios")
        if abs((cash/ref_nav)*100-ratio)>Decimal(".12"):
            raise IssuerPdfTableUnverified("Current PDF monthly payout ratio inconsistent")
        # The reference performance month must refer to the immediately
        # preceding calendar month (including year crossing Dec -> Jan).
        prev_y = y-1 if m==1 else y
        prev_m = 12 if m==1 else m-1
        if (int(ref_yy),int(ref_mm)) != (prev_y%100,prev_m):
            raise IssuerPdfTableUnverified("Current PDF NAV reference month not previous")
        values.append((date_obj.isoformat(),payout,inc,principal))
    year_months=[(2026,m) for m in range(1,11)]+[(2025,m) for m in (11,12)]
    if seen_months!=set(year_months):
        raise IssuerPdfTableUnverified("Current PDF 12-month coverage not Nov-2025 through Oct-2026")
    if [date.fromisoformat(x[0]) for x in values] != sorted(
            (date.fromisoformat(x[0]) for x in values),reverse=True):
        raise IssuerPdfTableUnverified("Current PDF dates not descending")
    # Overlap with independently reviewed older July source; reject silent
    # retrospective rewrites until manually resolved.
    overlap_count=0
    for day,amount,inc,principal in values:
        old=PREVIOUS_TRANSCRIPTION.get(day)
        if old is not None:
            overlap_count+=1
            if (amount,inc,principal)!=old:
                raise IssuerPdfTableUnverified(
                    "Current PDF changes old July source's overlapping amount/proportions"
                )
    if overlap_count!=9:
        raise IssuerPdfTableUnverified("Current PDF July overlap not exactly nine dates")
    fresh=[x for x in values if x[0] not in PREVIOUS_TRANSCRIPTION]
    if len(fresh)!=3 or {x[0][:7] for x in fresh}!={"2026-08","2026-09","2026-10"}:
        raise IssuerPdfTableUnverified("Current PDF newer three months not independently present")
    return values,overlap_count,fresh


def verify_single_official_version(*, fetcher=None, decoder=None):
    """One pinned official Oct PDF download, actual 12-row parse, no DB writes."""
    raw=fetcher() if fetcher is not None else download_pdf_once()
    observed=fingerprint_pdf(raw)
    if (observed["pdf_sha256"]!=PDF_SHA256_REGISTERED or
        observed["pdf_bytes"]!=PDF_BYTES_REGISTERED):
        raise IssuerPdfTableUnverified("official Oct PDF version changed; refuse old table")
    extracted=extract_page_text(raw,decoder=decoder)
    rows,overlap,new_rows=parse_current_oct_gliam_page(extracted)
    return {
        "schema":SCHEMA,
        "status":"PUBLISHER_OCTOBER_DOCUMENT_TABLE_AUTOMATICALLY_VERIFIED",
        "issuer":"Fidelity International",
        "issuer_source":ISSUER_PDF_URL,
        "pdf_sha256":observed["pdf_sha256"],
        "pdf_bytes":observed["pdf_bytes"],
        "page_number":PDF_PAGE_NUMBER,
        "publisher_class_code":TARGET_FIDELITY_CLASS,
        "matching_catalog_isin":TARGET_ISIN,
        "pdf_edition":CURRENT_LABEL,
        "verified_ex_date_count":len(rows),
        "independent_older_issuer_overlap_count":overlap,
        "newer_months_source_verified_count":len(new_rows),
        "newer_issuer_payouts":[
            {"ex_date":d,"usd_per_unit":amount,"income_percent":inc,
             "capital_percent":capital} for d,amount,inc,capital in new_rows
        ],
        "date_first":rows[-1][0],"date_last":rows[0][0],
        "doc_verification_complete":True,
        "issuer_per_unit_amounts_verified_against_pdf":len(rows),
        "issuer_per_unit_payouts_reconciled_with_TDCC":0,
        "historical_nav_three_years_verified":False,
        "total_return_approved":False,
        "portfolio_usage_approved":False,
        "production_strategies_modified":False,
        "customer_data_accessed":False,
        "pdf_bytes_publicly_distributed":False,
    }
