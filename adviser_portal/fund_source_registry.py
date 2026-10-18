"""Reviewed source discovery and share-class identity overlay for NAV intake.

A small source registry with issuer-published ISIN + exact SITCA/TDCC code.
Official web-page identity proof is NOT a historical NAV price export, a
commercial license, or a verified complete valuation calendar.
No scraping, network reads or storage. Registry entries must never be
inferred from matching only a partial Chinese fund name.
"""
from __future__ import annotations

from .nav_evidence import choose_instrument
from .offshore_evidence import valid_isin, _ccy, _norm_name

SCHEMA="fiveline_fund_nav_source_qualification_v1"
SOURCES={
  "123e331851e3ae6ff190":{
    "code":"LU2156566916",
    "isin":"LU2156566916",
    "currency":"USD",
    "name":"富達基金－全球優質債券基金 (A股C月配息美元)",
    "proof_source_url":"https://www.fidelity.com.tw/fund-and-service/fundui/funds/Fidelity-Funds-Global-Income-Fund-A-MCDIST-G-USD/overview",
    "provider_class_code_current":"GLIAM",
    "provider_class_code_before_2026_02_23":"GGIAM",
    "provider_class_code_change_date":"2026-02-23",
    "class_code_change_proof_url":"https://www.fidelity.com.tw/s3files/documents/announcement/web_announcement_20251212_3.pdf",
    "issuer":"富達投信 / Fidelity International",
    "instrument_kind":"offshore_fund_share_class",
    "source_purpose":"issuer_page_identifies_ISIN_class_and_original_currency",
    "history_export_available_and_license_verified":False,
  },
  "2d4e6ba412b83aa270a3":{
    "code":"DDO04",
    "isin":"TW000T3604Y3",
    "currency":"TWD",
    "name":"安聯台灣科技基金",
    "proof_source_url":"https://tw.allianzgi.com/zh-tw/products-solutions/taiwan-onshore/allianz-global-investors-taiwan-technology-fund",
    "issuer":"安聯投信",
    "instrument_kind":"domestic_fund_share_class",
    "source_purpose":"issuer_page_identifies_ISIN_for_catalog_code_DDO04",
    "history_export_available_and_license_verified":False,
  },
  "f0fed6445d13bc10d3fb":{
    "code":"DIOB3",
    "isin":"TW000T0759D2",
    "currency":"USD",
    "name":"瀚亞多重收益優化組合基金B類型-美元",
    "proof_source_url":"https://www.eastspring.com.tw/our-funds/all-funds/funddetails?isincode=TW000T0759D2",
    "official_historical_nav_query_url":"https://www.eastspring.com.tw/our-funds/exportnav",
    "history_query_mode":"date_range_web_or_excel_export",
    "web_display_max_years":1,
    "issuer":"瀚亞投信",
    "instrument_kind":"domestic_fund_share_class",
    "source_purpose":"issuer_page_identifies_USD_B_distribution_share_class_ISIN",
    "history_export_available_and_license_verified":False,
  },
}

def source_qualification(uid:str, *, chooser=None):
    chooser=chooser or choose_instrument
    row=chooser(uid)
    entry=SOURCES.get(uid)
    if entry is None:
        return {
            "schema":SCHEMA,"instrument_uid":row["uid"],
            "status":"ISSUER_SHARE_CLASS_IDENTITY_NOT_YET_REVIEWED",
            "verified_issuer_isin":None,
            "full_history_raw_nav_rows_imported":0,
            "three_year_history_ready":False,
            "source_rights_approved":False,
            "usable_for_portfolio":False,
            "ready_for_total_return":False,
            "ready_for_six_strategies":False,
            "reason":"尚無發行公司身分對照。請勿依相近中文名稱自動合併不同幣別／級別。",
        }
    if (entry["code"]!=row["code"] or
        _ccy(entry["currency"])!=_ccy(row["currency"]) or
        entry["instrument_kind"]!=row["category"] or
        not valid_isin(entry["isin"]) or
        _norm_name(entry["name"])!=_norm_name(row["name"])):
        return {
            "schema":SCHEMA,"instrument_uid":row["uid"],
            "status":"CATALOG_OFFICIAL_CLASS_CONFLICT",
            "verified_issuer_isin":None,
            "full_history_raw_nav_rows_imported":0,
            "three_year_history_ready":False,
            "source_rights_approved":False,
            "usable_for_portfolio":False,
            "ready_for_total_return":False,
            "ready_for_six_strategies":False,
            "reason":"現有基金名錄與官方級別身分不一致，停止配對。",
        }
    if row.get("isin") and str(row["isin"]).strip().upper()!=entry["isin"]:
        return {
            "schema":SCHEMA,"instrument_uid":row["uid"],
            "status":"CATALOG_ISIN_CONFLICT",
            "verified_issuer_isin":None,
            "full_history_raw_nav_rows_imported":0,
            "three_year_history_ready":False,
            "source_rights_approved":False,
            "usable_for_portfolio":False,
            "ready_for_total_return":False,
            "ready_for_six_strategies":False,
            "reason":"名錄 ISIN 與官方資料衝突，需要人工複核，不能沿用舊碼。",
        }
    return {
        "schema":SCHEMA,
        "instrument_uid":row["uid"],
        "status":"ISSUER_PUBLISHED_CLASS_IDENTITY_FOUND",
        "display_name":row["name"],
        "existing_catalog_code":row["code"],
        "issuer_name":entry["issuer"],
        "verified_issuer_isin":entry["isin"],
        "currency":entry["currency"],
        "share_class_kind":entry["instrument_kind"],
        "official_issuer_url":entry["proof_source_url"],
        "official_historical_nav_query_url":entry.get("official_historical_nav_query_url"),
        "history_query_entry_page_confirmed":bool(entry.get("official_historical_nav_query_url")),
        "provider_code_current":entry.get("provider_class_code_current"),
        "provider_code_legacy":entry.get("provider_class_code_before_2026_02_23"),
        "provider_code_change_date":entry.get("provider_class_code_change_date"),
        "provider_code_change_proof_url":entry.get("class_code_change_proof_url"),
        "provider_code_alias_history_reconciled":False,
        "exported_three_years_verified":False,
        "issuer_identity_source_review_state":"PUBLIC_ISSUER_PAGE_REVIEWED_NOT_LIVE_RECHECKED",
        "history_csv_provider_confirmed":False,
        "historical_per_unit_nav_source_url":None,
        "issuer_actual_nav_dates_evidence_available":False,
        "commercial_data_rights_approved":False,
        "full_history_raw_nav_rows_imported":0,
        "three_year_history_ready":False,
        "ready_for_total_return":False,
        "usable_for_portfolio":False,
        "ready_for_six_strategies":False,
        "reason":"已找到官方級別 ISIN 身分佐證；尚需正式三年歷史 NAV 原始檔、估值日清單及商用授權。",
    }
