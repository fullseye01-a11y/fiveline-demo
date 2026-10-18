"""Whole-market fund share-class pipeline audit, not individual fund showcase.

Reads one already validated six-provider *public instrument directory* snapshot.
Produces an exhaustive in-memory audit for EVERY listed domestic and offshore
fund record, not just named pilot ISINs. Preserves each UID, source, code,
name, currency, and ISIN, and NEVER merges fund records just because their
code matches. Does not ingest NAV, client data, or authorize data licensing.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from .offshore_evidence import valid_isin

CATEGORIES=("domestic_fund_share_class","offshore_fund_share_class")
SCHEMA="fiveline_fund_universe_batch_readiness_v1"
MAX_FUND_RECORDS=24000


def audit_whole_universe(snapshot):
    if not isinstance(snapshot,dict) or not isinstance(snapshot.get("items"),list):
        raise ValueError("A validated public market directory is required")
    all_items=snapshot["items"]
    fund_rows=[item for item in all_items if item.get("category") in CATEGORIES]
    if not fund_rows or len(fund_rows)>MAX_FUND_RECORDS:
        raise ValueError("Empty or oversized fund catalog")
    if len({item["uid"] for item in fund_rows})!=len(fund_rows):
        raise ValueError("Fund source UID collision: refuse pipeline plan")
    counts=Counter(row["category"] for row in fund_rows)
    missing_isin=sum(not row.get("isin","").strip() for row in fund_rows)
    invalid_isin=sum(bool(row.get("isin","").strip()) and
                     not valid_isin(row["isin"].strip().upper()) for row in fund_rows)
    missing_currency=sum(not row.get("currency","").strip() for row in fund_rows)
    codes=defaultdict(list)
    for row in fund_rows:
        # Source-dependent code is NOT a unique fund identity.
        codes[(row["source"],row["code"])].append(row["uid"])
    duplicates={code:members for code,members in codes.items() if len(members)>1}
    collision_rows=sum(len(members) for members in duplicates.values())
    listed_history_ready=sum(row.get("historical_prices_ready") is True
                             for row in fund_rows)
    class_identity_ready=sum(row.get("class_identity_approved") is True
                             for row in fund_rows)
    source_counts=Counter(row["source"] for row in fund_rows)
    nav_pending=len(fund_rows)-listed_history_ready
    return {
        "schema":SCHEMA,
        "status":"WHOLE_FUND_MARKET_CATALOG_AUDITED_METADATA_ONLY",
        "checked_at_utc":snapshot.get("checked_at_utc"),
        "market_scope":"Taiwan domestic funds and Taiwan-registered offshore funds",
        "all_market_international_funds_included":False,
        "fund_records_processed":len(fund_rows),
        "domestic_fund_records":counts["domestic_fund_share_class"],
        "offshore_fund_records":counts["offshore_fund_share_class"],
        "other_market_instrument_records":len(all_items)-len(fund_rows),
        "source_counts":dict(sorted(source_counts.items())),
        "missing_isin_records":missing_isin,
        "invalid_isin_checksum_records":invalid_isin,
        "missing_currency_records":missing_currency,
        "source_code_collision_groups":len(duplicates),
        "source_code_collision_rows":collision_rows,
        "source_code_collisions_are_not_duplicate_funds":True,
        "catalog_identity_approved_rows":class_identity_ready,
        "catalog_historical_prices_ready_rows":listed_history_ready,
        "historical_nav_validation_pending_records":nav_pending,
        "historical_nav_3y_ready_records":0,
        "official_price_history_ingested_by_this_audit":0,
        "batch_priority":[
            {"stage":"01_CLASS_IDENTITY","action":"ISIN / currency / source-code collision checks",
             "candidate_missing_isin":missing_isin,
             "candidate_bad_isin":invalid_isin,
             "ambiguous_code_groups":len(duplicates)},
            {"stage":"02_LICENSED_HISTORY","action":"Official/licensed three-year per-unit NAV and actual valuation-day schedule",
             "candidate_records_without_catalog_history_flag":nav_pending},
            {"stage":"03_CASH_DISTRIBUTIONS","action":"Issuer per-unit payments, splits, fee & FX provenance", "approved":False},
            {"stage":"04_FORMULA_RECONCILIATION","action":"Original Excel, six strategies, client suitability and isolation", "approved":False}
        ],
        "completion_percent_for_official_3y_nav":0,
        "source_snapshot_only":True,
        "customer_account_data_read":False,
        "db_rows_written":0,
        "portfolio_backtests_modified":False,
        "production_site_modified":False,
        "disclaimer":"Catalog records may contain code reuse and incomplete identity data; complete licensed NAV series, official NAV dates, issuer corrections and commercial reuse rights are not yet verified."
    }
