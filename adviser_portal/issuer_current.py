"""Read-only, user-triggered exact official October Fidelity payout evidence.

A pinned 2026-10 publisher PDF is scanned once per hour per Alpha instance,
verifying immutable byte SHA, unique GLIAM A-USD share-class table and all 12
rows, including nine independent July-document overlaps and three new dates.
The issuer PDF does NOT print this ISIN in the table: TDCC catalog identity
is a separate gate, but is NOT final historical total-return approval.
"""
from __future__ import annotations

from threading import Lock
from time import monotonic

from .offshore_evidence import choose_instrument,_norm_name,_ccy
from .issuer_pdf_review import (
    TARGET_ISIN, TARGET_LABEL, ISSUER_PDF_URL, TARGET_FIDELITY_CLASS
)
from .issuer_pdf_table import verify_single_official_version

TTL_SECONDS=3600
_lock=Lock()
_last=0.0
_snapshot=None


def current_october_issuer(uid, fetcher=None, decoder=None):
    """Single exact class. No unverified foreign ISIN shares can get the data."""
    row=choose_instrument(uid)
    if (row.get("category")!="offshore_fund_share_class" or
        row.get("source")!="tdcc_offshore_funds" or
        row.get("isin","").upper()!=TARGET_ISIN or
        row.get("code","").upper()!=TARGET_ISIN or
        _ccy(row.get("currency"))!="USD" or
        _norm_name(row.get("name"))!=_norm_name(TARGET_LABEL)):
        return {
            "status":"ISSUER_SOURCE_NOT_YET_MAPPED",
            "instrument_uid":row["uid"],
            "publisher_pdf_table_verified":False,
            "per_unit_cash_amounts_verified_against_pdf":0,
            "tdcc_amounts_reconciled":0,
            "ready_for_total_return":False,"usable_for_portfolio":False,
            "reason":"目前只為富達全球優質債券 A 股 C 月配息美元建立 2026/10 原始公告核對。",
        }
    global _snapshot, _last
    if fetcher is not None or decoder is not None:
        result=verify_single_official_version(fetcher=fetcher,decoder=decoder)
    else:
        with _lock:
            now=monotonic()
            if _snapshot is None or now-_last>=TTL_SECONDS:
                fresh=verify_single_official_version()
                _snapshot=fresh
                _last=monotonic()
            result=_snapshot
    if (result.get("status")!="PUBLISHER_OCTOBER_DOCUMENT_TABLE_AUTOMATICALLY_VERIFIED" or
        result.get("matching_catalog_isin")!=TARGET_ISIN or
        result.get("publisher_class_code")!=TARGET_FIDELITY_CLASS or
        result.get("verified_ex_date_count")!=12 or
        result.get("independent_older_issuer_overlap_count")!=9 or
        result.get("newer_months_source_verified_count")!=3 or
        result.get("total_return_approved") is not False):
        raise ValueError("Fidelity verified table result inconsistent")
    return {
        "status":"ISSUER_OCTOBER_12_RECORDS_PDF_VERIFIED_RESEARCH_ONLY",
        "instrument_uid":row["uid"],
        "display_name":row["name"],
        "code":row["code"],"isin":TARGET_ISIN,
        "currency":"USD",
        "publisher_class_code":TARGET_FIDELITY_CLASS,
        "official_pdf_url":ISSUER_PDF_URL,
        "pdf_document_edition":"2026-10",
        "pdf_page_number":result["page_number"],
        "pdf_sha256":result["pdf_sha256"],
        "publisher_pdf_table_verified":True,
        "issuer_published_monthly_records":12,
        "older_source_overlap_confirmed":9,
        "newer_months_verified":3,
        "newer_per_unit_cash_distribution":result["newer_issuer_payouts"],
        "per_unit_cash_amounts_verified_against_pdf":12,
        "tdcc_amounts_reconciled":0,
        "complete_historical_nav_3y_verified":False,
        "issuer_units_paid_or_reinvested_reviewed":False,
        "ready_for_total_return":False,
        "usable_for_portfolio":False,
        "production_backtests_modified":False,
        "client_records_saved":False,
        "reason":"2026/10 發行人原始 PDF 12 期表格已依 SHA、級別、金額及來源比例驗證；仍未與集保獨立核對每單位現金金額或補齊三年 NAV，不能計算含息績效。",
    }
