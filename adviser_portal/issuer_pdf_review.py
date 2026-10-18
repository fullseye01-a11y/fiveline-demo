"""Fidelity publisher distribution sample vs TDCC 3-5 date/ratio evidence.

One narrowly-scoped, publicly indexed Fidelity PDF excerpt, page 33 (zero-based
page 32), released 2026-07-02 and current only through 2026-07-01. Amounts are
issuer-published USD per unit, NOT inferred from TDCC 3-5 '單位數'.
This version does NOT download, verify or archive the PDF bytes and must never
assert independent document verification, final issuer amount reconciliation,
full payout completeness, or total-return readiness. It is a review worksheet,
not a trading price feed.

Later production ingestion should download the publisher PDF, hash and archive
its immutable bytes with license review, verify the exact share-class label and
ISIN-to-class mapping, and cross-check all payout / payment dates before signoff.
"""
from __future__ import annotations

from decimal import Decimal
from .offshore_evidence import (
    choose_instrument, dividend_events, _norm_name, _ccy
)

SCHEMA = "fiveline_fidelity_issuer_public_pdf_review_v1"
ISSUER_PDF_URL = "https://www.fidelityinternational.com/legal/documents/FF/TW-zh/fdpc.ff.TW-zh.TW.pdf"
DOC_EDITION = "2026-07"
DOC_PUBLISHED_DATE = "2026-07-02"
PDF_PAGE_NUMBER = 33
# Match the catalog's ISIN to TDCC independently, and use the issuer
# PDF's GLIAM class label and full share-class name for the other side.
# The issuer PDF section does not itself print this share class's ISIN.
TARGET_ISIN = "LU2156566916"
TARGET_FIDELITY_CLASS = "GLIAM"
TARGET_LABEL = "富達基金－全球優質債券基金 (A股C月配息美元)"

# Limited public issuer text transcription; descending ex-dates.
# Fields: ex_date, cash_distribution_per_unit_USD, income%, capital%.
# These are *historical issuer statements*, not current estimates or
# confirmation of the payout amount by the application's document byte parser.
EXCERPT = (
    ("2026-07-01", "0.0580", "100.00", "0.00"),
    ("2026-06-01", "0.0580", "100.00", "0.00"),
    ("2026-05-01", "0.0580", "45.70", "54.30"),
    ("2026-04-01", "0.0575", "54.46", "45.54"),
    ("2026-03-02", "0.0596", "98.15", "1.85"),
    ("2026-02-02", "0.0596", "100.00", "0.00"),
    ("2026-01-02", "0.0596", "100.00", "0.00"),
    ("2025-12-01", "0.0599", "100.00", "0.00"),
    ("2025-11-03", "0.0601", "57.10", "42.90"),
    ("2025-10-01", "0.0602", "100.00", "0.00"),
    ("2025-09-01", "0.0600", "48.08", "51.92"),
    ("2025-08-01", "0.0599", "84.92", "15.08"),
)


def _valid_excerpt():
    from datetime import date
    if len(EXCERPT) != 12:
        raise ValueError("Issuer July-2026 publisher sample changed")
    seen = set()
    for ex, payout, inc, capital in EXCERPT:
        date.fromisoformat(ex)
        if ex in seen:
            raise ValueError("Duplicate publisher monthly event")
        seen.add(ex)
        p = Decimal(payout)
        i = Decimal(inc)
        c = Decimal(capital)
        if not (Decimal("0") < p < Decimal("10") and
                Decimal("0") <= i <= Decimal("100") and
                Decimal("0") <= c <= Decimal("100") and
                abs(i + c - Decimal("100")) <= Decimal("0.1")):
            raise ValueError("Issuer sample inconsistent")
    return True


def issuer_distribution_review(uid, fetcher=None):
    """Provide source-labelled worksheet; no new NAV or total-return result."""
    row = choose_instrument(uid)
    if row.get("category") != "offshore_fund_share_class":
        raise ValueError("Not an offshore fund")
    if not (
        str(row.get("isin", "")).strip().upper() == TARGET_ISIN and
        _ccy(row.get("currency")) == "USD" and
        _norm_name(row.get("name")) == _norm_name(TARGET_LABEL) and
        str(row.get("code", "")).upper() == TARGET_ISIN
    ):
        return {
            "schema": SCHEMA, "status": "ISSUER_SOURCE_NOT_YET_MAPPED",
            "instrument_uid": row["uid"],
            "issuer_pdf_sample_supported": False,
            "issuer_document_bytes_verified": False,
            "issuer_per_unit_payouts_reconciled": 0,
            "ready_for_total_return": False, "usable_for_portfolio": False,
            "production_backtests_modified": False,
            "reason": "目前僅整理富達全球優質債券 A 股 C 月配息美元的官方 PDF 摘錄，其他級別尚未建立發行公司專屬對照。",
        }
    _valid_excerpt()
    tdcc = dividend_events(uid, fetcher=fetcher)
    if tdcc.get("status") != "OFFSHORE_DIVIDEND_DATE_METADATA_ONLY":
        return {
            "schema": SCHEMA, "status": "TDCC_SOURCE_REVIEW_REQUIRED",
            "instrument_uid": row["uid"],
            "issuer_pdf_sample_supported": True,
            "issuer_document_bytes_verified": False,
            "issuer_per_unit_payouts_reconciled": 0,
            "ready_for_total_return": False, "usable_for_portfolio": False,
            "production_backtests_modified": False,
            "reason": "TDCC 級別／日期來源尚未核准，發行公司 PDF 摘錄不能自動接上價格或配息。"
        }

    tdcc_by_day = {event["ex_date"]: event for event in tdcc.get("events", [])}
    evidence = []
    date_overlap = 0
    matching_composition = 0
    composition_mismatches = 0
    for ex, amount, income, capital in EXCERPT:
        official = tdcc_by_day.get(ex)
        state = "ISSUER_PDF_ONLY"
        if official is not None:
            date_overlap += 1
            proportions = official.get("composition", {})
            if proportions.get("status") == "PROPORTIONS_ONLY":
                td_income = Decimal(proportions["income_pct"])
                td_capital = Decimal(proportions["capital_pct"])
                if (abs(td_income - Decimal(income)) <= Decimal("0.10") and
                    abs(td_capital - Decimal(capital)) <= Decimal("0.10")):
                    matching_composition += 1
                    state = "TDCC_DATE_AND_COMPOSITION_AGREE"
                else:
                    composition_mismatches += 1
                    state = "TDCC_COMPOSITION_CONFLICT"
            else:
                state = "TDCC_DATE_ONLY_COMPONENTS_UNVERIFIED"
        evidence.append({
            "ex_date": ex, "issuer_per_unit_distribution_usd": amount,
            "income_share_pct": income, "capital_share_pct": capital,
            "tdcc_comparison_status": state,
            "pdf_document_bytes_verified": False,
            "issuer_amount_reconciled": False,
        })
    newer_tdcc = sorted(day for day in tdcc_by_day if day > EXCERPT[0][0])
    return {
        "schema": SCHEMA,
        "status": "ISSUER_OFFICIAL_PDF_EXCERPT_MANUAL_REVIEW_ONLY",
        "instrument_uid": row["uid"], "display_name": row["name"],
        "isin": TARGET_ISIN, "issuer_share_class_code": TARGET_FIDELITY_CLASS,
        "currency": "USD",
        "issuer_pdf_url": ISSUER_PDF_URL,
        "issuer_pdf_page": PDF_PAGE_NUMBER, "issuer_pdf_edition": DOC_EDITION,
        "issuer_report_published": DOC_PUBLISHED_DATE,
        "published_monthly_event_count": len(EXCERPT),
        "issuer_last_observed_ex_date": EXCERPT[0][0],
        "source_doc_not_current_through_today": True,
        "tdcc_recent_event_count": tdcc["event_count"],
        "tdcc_matching_ex_date_count": date_overlap,
        "tdcc_matching_composition_count": matching_composition,
        "tdcc_composition_conflict_count": composition_mismatches,
        "tdcc_dates_newer_than_issuer_excerpt": newer_tdcc[:10],
        "issuer_document_bytes_verified": False,
        "issuer_per_unit_payouts_reconciled": 0,
        "cash_or_reinvestment_approved": False,
        "historical_nav_3y_verified": False,
        "ready_for_total_return": False, "usable_for_portfolio": False,
        "production_backtests_modified": False, "customer_records_saved": False,
        "events": evidence,
        "reason": (
            "富達官方 2026/07 文件曾刊出這些原幣每單位配息；目前為具來源頁碼的人工摘錄，"
            "後端尚未下載並比對原始 PDF 雜湊或逐筆建立不可變版本，因此不計入"
            "『每單位金額已正式核准』，僅供與 TDCC 日期及來源比例對照。"
        ),
    }
