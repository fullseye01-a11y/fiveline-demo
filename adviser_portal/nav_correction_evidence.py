"""Issuer-published per-unit NAV revision events; no synthetic history or writes.

Source: Allianz Taiwan Technology Fund official 2026-03-04 NAV
correction notice. These historical adjustments have absolute Decimal values,
and MUST be checked when validating any 3-year same-ISIN raw NAV export.
The event ledger has an explicit official URL, effective announcement date and
original/corrected amounts. These are issuer corrections, NOT dividends or
price adjustments to compute market total returns.
"""
from __future__ import annotations

from decimal import Decimal
from .offshore_evidence import valid_isin

ISSUER_NOTICE="https://tw.allianzgi.com/zh-tw/announcement/product-announcement/2026-03-04-1"
ISSUER_ISIN="TW000T3604Y3"
ISSUER_CURRENCY="TWD"
ISSUER_NAME="安聯台灣科技基金"
NOTICE_PUBLISHED="2026-03-04"

# Published original NAV -> published final corrected per-unit NAV (TWD).
# A future correction notice must create a new reviewed version, never
# silently override these evidence values.
EVENTS={
    "2026-02-26":("490.11","493.76"),
    "2026-03-02":("481.62","485.27"),
}


def compare_official_corrections(isin:str,currency:str,observations):
    """Check dates inside actual requested observation interval only.

    observations: dict[datetime.date, Decimal] as parsed by the strict NAV
    auditor. Never blindly patch a source's original bytes or rewrite prices.
    """
    if not isinstance(observations,dict) or not observations:
        raise ValueError("NAV observations missing")
    if not valid_isin(isin):
        raise ValueError("ISIN invalid")
    if isin!=ISSUER_ISIN or currency!=ISSUER_CURRENCY:
        return {
            "relevant":False, "expected_correction_count":0,
            "checked_count":0,"missing_count":0,
            "outdated_count":0,"conflicting_count":0,
            "source_url":None,"cases":[],
            "all_in_range_corrections_valid":True,
        }
    first=min(observations)
    last=max(observations)
    findings=[]
    for day_str,(old,corrected) in EVENTS.items():
        from datetime import date
        day=date.fromisoformat(day_str)
        if not first<=day<=last:
            continue
        nav=observations.get(day)
        if nav is None:
            status="OFFICIAL_CORRECTION_DATE_MISSING"
        elif nav==Decimal(corrected):
            status="CORRECTED_OFFICIAL_NAV_MATCHED"
        elif nav==Decimal(old):
            status="OUTDATED_PRE_CORRECTION_NAV"
        else:
            status="NAV_CONFLICTS_WITH_OFFICIAL_CORRECTION"
        findings.append({"date":day_str,"status":status})
    return {
        "relevant":True,
        "expected_correction_count":len(findings),
        "checked_count":sum(x["status"]=="CORRECTED_OFFICIAL_NAV_MATCHED" for x in findings),
        "missing_count":sum(x["status"]=="OFFICIAL_CORRECTION_DATE_MISSING" for x in findings),
        "outdated_count":sum(x["status"]=="OUTDATED_PRE_CORRECTION_NAV" for x in findings),
        "conflicting_count":sum(x["status"]=="NAV_CONFLICTS_WITH_OFFICIAL_CORRECTION" for x in findings),
        "source_url":ISSUER_NOTICE,"cases":findings,
        "all_in_range_corrections_valid":all(
            x["status"]=="CORRECTED_OFFICIAL_NAV_MATCHED" for x in findings),
    }


def correction_notice_for(uid:str,chooser=None):
    """Public issuer notice, only on exact validated Allianz share class."""
    from .fund_source_registry import source_qualification
    r=source_qualification(uid,chooser=chooser)
    if (r.get("status")!="ISSUER_PUBLISHED_CLASS_IDENTITY_FOUND" or
        r.get("verified_issuer_isin")!=ISSUER_ISIN or
        r.get("currency")!=ISSUER_CURRENCY):
        return {
            "status":"OFFICIAL_NAV_CORRECTION_NOT_MAPPED",
            "instrument_uid":uid,"official_adjustment_evidence_available":False,
            "three_year_history_ready":False,"usable_for_portfolio":False,
            "ready_for_total_return":False,
        }
    return {
        "status":"ISSUER_CORRECTION_NOTICE_RESEARCH_EVIDENCE",
        "instrument_uid":uid,
        "share_class_isin":ISSUER_ISIN,"currency":ISSUER_CURRENCY,
        "announcement_date":NOTICE_PUBLISHED,
        "official_notice_url":ISSUER_NOTICE,
        "event_count":len(EVENTS),
        "events":[{"date":d,"previous_nav":before,"revised_nav":after}
                  for d,(before,after) in sorted(EVENTS.items())],
        "official_adjustment_evidence_available":True,
        "history_source_rows_checked":0,
        "three_year_history_ready":False,
        "usable_for_portfolio":False,
        "ready_for_total_return":False,
        "ready_for_six_strategies":False,
        "client_records_used":False,
        "reason":"官方更正公告屬於個別日期 NAV 品質證據；尚未取得三年歷史逐日 NAV，不能回推三年績效。",
    }
