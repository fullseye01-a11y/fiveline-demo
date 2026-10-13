"""Conservative matching review of 68 Excel history instruments to public official latest NAV.

All candidates require human verification of ISIN, class, currency, distribution
policy before use. No matching output ever updates history or trading results.
"""
from __future__ import annotations

import csv
from collections import Counter, defaultdict
from datetime import datetime, timezone
import re
import unicodedata
from pathlib import Path

# Historical instrument IDs requiring exchange or other *non fund NAV* feeds.
# Includes equities, leveraged/index ETFs, foreign-listed ETFs and a record
# whose display name is too generic to distinguish an actual mutual fund.
NON_MUTUAL_OR_UNCERTAIN = frozenset(
    ["3", "4", "5", "6", "7", "8", "12", "56", "57", "58", "59", "60", "63", "64", "65"]
)

CURRENCY_ALIASES = {
    "新臺幣": "TWD", "新台幣": "TWD", "臺幣": "TWD", "台幣": "TWD",
    "NTD": "TWD", "TWD": "TWD",
    "美金": "USD", "美元": "USD", "USD": "USD",
    "南非幣": "ZAR", "ZAR": "ZAR",
    "人民幣": "CNY", "CNY": "CNY", "歐元": "EUR", "EUR": "EUR",
}

def normalize_name(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().replace("臺", "台")
    # Eliminate only punctuation and whitespace; keep all meaningful name tokens.
    return "".join(ch for ch in text if ch.isalnum())

def normalize_currency(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    return CURRENCY_ALIASES.get(text, text.upper())

def get_legacy_rows(csv_path: Path) -> list[dict]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as fh:
        rows=list(csv.DictReader(fh))
    if len(rows)!=68 or len({x["標的ID"] for x in rows})!=68:
        raise ValueError("Expected exact 68 unique instruments from historical snapshot")
    return rows

def make_report(legacy: list[dict], official_by_source: dict, errors: dict | None = None) -> dict:
    """Produce *review candidates*, never a verified/approved mapping.

    official_by_source keys must be source names, values are normalized_records
    snapshot dictionaries. Unavailable sources are explicitly represented by errors.
    """
    errors = errors or {}
    by_name=defaultdict(list)
    for src, batch in official_by_source.items():
        for x in batch["records"]:
            record={k:x.get(k,"") for k in ("name","code","isin","currency","nav_date","nav","source")}
            record["currency_code"]=normalize_currency(record["currency"])
            by_name[normalize_name(record["name"])].append(record)

    report=[]
    count=Counter()
    for legacy_row in legacy:
        id_=str(legacy_row["標的ID"])
        name=legacy_row["標的名稱"]
        ccy=normalize_currency(legacy_row["幣別"])
        item={"id":id_, "name":name, "currency":ccy,
              "nav_end":legacy_row["NAV截至日期"],
              "candidate_count":0,"candidates":[],
              "approved":False}
        if id_ in NON_MUTUAL_OR_UNCERTAIN:
            item["status"]="separate_exchange_feed_or_identity_required"
            item["reason"]="Listed equity/ETF or security identity uncertain; never match to a mutual-fund NAV"
        else:
            possible=by_name.get(normalize_name(name),[])
            compatible=[x for x in possible if x["currency_code"]==ccy]
            # Candidates include exact normalized full-name and currency only.
            # Distinct provider records, not near-name fuzzy scores or class guesses.
            if len(compatible)==1:
                item["status"]="single_unverified_candidate"
                item["reason"]="Exact normalized name/currency; ISIN and class still need manual confirmation"
                selected=compatible
            elif len(compatible)>1:
                item["status"]="multiple_unverified_candidates"
                item["reason"]="Same normalized name/currency found more than once; do not select automatically"
                selected=compatible
            elif len(possible)>0:
                item["status"]="currency_mismatch"
                item["reason"]="Name found but provider currency does not agree with legacy instrument"
                selected=possible
            elif errors:
                item["status"]="incomplete_provider_coverage"
                item["reason"]="At least one official data source unavailable; cannot mark as unmatched"
                selected=[]
            else:
                item["status"]="no_exact_candidate"
                item["reason"]="No exact name+currency match. Do not infer from similar-name funds"
                selected=[]
            item["candidate_count"]=len(selected)
            item["candidates"]=selected[:5]
        count[item["status"]]+=1
        report.append(item)
    if len(report)!=68:raise ValueError("Wrong row count")
    return {
        "mode":"read_only_matching_review",
        "legacy_count":len(report),
        "candidate_status_counts":dict(count),
        "provider_errors":errors,
        "provider_success":list(official_by_source),
        "reviewed_and_approved_count":0,
        "any_legacy_nav_overwritten":False,
        "checked_at_utc":datetime.now(timezone.utc).isoformat(),
        "notice":"This is a preliminary full-name/currency-only screening. No ISIN/class/dividend confirmation, no historical NAV write.",
        "records":report,
    }
