"""Live research coverage indicator using only currently verified TDCC/SITCA NAV.

The 3-4 public TDCC set covers recent NAV only. This endpoint cannot
manufacture multi-year bars from a current window, a 3-year summary statistic,
a chart screenshot or an offshore 'NAV total' aggregate.
"""
from __future__ import annotations
from datetime import date
from .nav_evidence import choose_instrument, evidence
from .offshore_evidence import latest_nav

SCHEMA="fiveline_live_fund_history_gap_report_v1"


def coverage(uid, fetcher=None, today=None):
    today=today or date.today()
    row=choose_instrument(uid)
    category=row["category"]
    if category=="offshore_fund_share_class":
        v=latest_nav(uid,fetcher=fetcher,today=today)
        if v["status"]=="OFFSHORE_RECENT_NAV_EVIDENCE_ONLY":
            observed=sorted(set(v["recent_nav_days"]))
            source="TDCC 3-4 / 近七日淨值"
        else:
            observed=[]
            source="TDCC 3-4 / 基金身分待核對"
    elif category=="domestic_fund_share_class":
        v=evidence(uid,fetcher=fetcher,today=today)
        if v["status"]=="LATEST_NAV_EVIDENCE_ONLY":
            observed=[v["nav_date"]]
            source="SITCA / 最新淨值"
        else:
            observed=[]
            source="SITCA / 基金級別待核對"
    else:
        raise ValueError("此功能僅接受境內或境外基金級別")
    try:
        cutoff=today.replace(year=today.year-3)
    except ValueError:
        cutoff=today.replace(year=today.year-3,day=28)
    return {
        "schema":SCHEMA,"status":"THREE_YEAR_NAV_SOURCE_NOT_READY",
        "instrument_uid":row["uid"],"display_name":row["name"],
        "isin":row.get("isin",""),"currency":row.get("currency",""),
        "minimum_start_date":cutoff.isoformat(),
        "today_date":today.isoformat(),
        "current_source":source,"source_identity_status":v["status"],
        "recent_evidence_nav_dates_count":len(observed),
        "recent_evidence_first_nav_date":observed[0] if observed else None,
        "recent_evidence_last_nav_date":observed[-1] if observed else None,
        "full_historical_rows_imported":0,
        "history_source_bundle_attested":False,
        "independent_official_session_dates_verified":False,
        "historical_three_years_ready":False,
        "validated_annualized_total_return":None,
        "ready_for_portfolio":False,
        "ready_for_six_strategies":False,
        "customer_records_saved":False,
        "production_backtests_modified":False,
        "requirements":[
            "取得同一基金級別 ISIN、幣別的官方或授權每單位歷史 NAV，至少追溯至 "+
            cutoff.isoformat()+"。",
            "取得基金實際估值日期或經發行人核准的日期清單，才能區分假期和缺漏，不以週一至週五推算。",
            "校驗歷史檔案 SHA-256、資料授權、重複日、同日多價和級別變動。",
            "再對帳配息、股數拆併、再投入日、匯率和費用，才可計算含息績效。"
        ],
        "note":"上方僅是本次公開來源能核對的近期 NAV 日期筆數，不是已保存的歷史資料，也不能據此換算成 3 年覆蓋率。",
    }
