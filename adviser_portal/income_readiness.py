"""Per-share-class fund NAV/distribution reconciliation readiness, research only.

This is a data-quality audit, NOT a NAV adjusted series or a return estimator.
It deliberately refuses to produce any performance result until multi-year
NAV, issuer-verified cash per unit, FX and corporate-action proofs are joined.
"""
from __future__ import annotations

from datetime import datetime,timezone
from .offshore_evidence import latest_nav,dividend_events

MINIMUM_HISTORY_YEARS = 3
SCHEMA="fiveline_offshore_income_reconciliation_readiness_v1"


def readiness(uid, fetcher=None, today=None):
    nav=latest_nav(uid,fetcher=fetcher,today=today)
    if nav.get("status")!="OFFSHORE_RECENT_NAV_EVIDENCE_ONLY":
        return {
            "schema":SCHEMA,"status":"IDENTITY_OR_NAV_REVIEW_REQUIRED",
            "instrument_uid":nav["instrument_uid"],
            "display_name":nav["display_name"],
            "reasons":[nav.get("reason","最近七日 NAV 證據未通過。")],
            "ready_for_portfolio":False,
            "ready_for_total_return":False,
            "ready_for_six_strategies":False,
            "data_modified":False,
        }
    div=dividend_events(uid,fetcher=fetcher,today=today)
    if div.get("status")!="OFFSHORE_DIVIDEND_DATE_METADATA_ONLY":
        return {
            "schema":SCHEMA,"status":"DIVIDEND_IDENTITY_REVIEW_REQUIRED",
            "instrument_uid":nav["instrument_uid"],
            "display_name":nav["display_name"],
            "reasons":[div.get("reason","配息來源資料未通過。")],
            "nav_observations":len(nav["recent_nav_days"]),
            "ready_for_portfolio":False,
            "ready_for_total_return":False,
            "ready_for_six_strategies":False,
            "data_modified":False,
        }
    dates=nav["recent_nav_days"]
    events=div["events"]
    proportion_verified=sum(e["composition"]["status"]=="PROPORTIONS_ONLY"
                            for e in events)
    incomplete_or_unknown=len(events)-proportion_verified
    reasons=[
        "目前官方 3-4 僅提供最近七日 NAV，尚無同一級別至少三年的完整連續歷史 NAV。",
        "3-5 資料未提供級別 ISIN；每單位原幣現金配息額需與基金公司正式公告逐筆對帳。",
        "除息日、發放日、現金入帳／再投入時點尚未與全部原始價格對帳。",
        "不同幣別換算成台幣的歷史匯率與同日估值時點尚未核准。",
        "原 Excel 及六策略公式、費用、拆併與級別歷史變動尚未驗收。",
    ]
    return {
        "schema":SCHEMA,
        "status":"RESEARCH_ONLY_ADJUSTMENT_NOT_READY",
        "instrument_uid":nav["instrument_uid"],
        "display_name":nav["display_name"],
        "code":nav["code"],"isin":nav["isin"],
        "currency":nav["currency"],
        "recent_nav_observations":len(dates),
        "recent_nav_first":min(dates),
        "recent_nav_last":max(dates),
        "minimum_historical_years_required":MINIMUM_HISTORY_YEARS,
        "historical_nav_3y_verified":False,
        "known_dividend_event_count":div["event_count"],
        "displayed_dividend_event_count":len(events),
        "displayed_income_principal_ratio_verified_count":proportion_verified,
        "displayed_income_principal_ratio_not_verified_count":incomplete_or_unknown,
        "issuer_per_unit_payouts_reconciled":0,
        "distribution_amounts_adjusted":False,
        "cash_vs_reinvested_scenarios_validated":False,
        "complete_history_verified":False,
        "share_class_approved":False,
        "fx_twd_history_verified":False,
        "ready_for_portfolio":False,
        "ready_for_total_return":False,
        "ready_for_six_strategies":False,
        "client_records_saved":False,
        "data_modified":False,
        "reasons":reasons,
    }
