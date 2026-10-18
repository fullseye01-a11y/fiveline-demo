"""All-fund share-class intake QUEUE / stage-0 quality map, no NAV ingest.

Builds a deterministic plan for EVERY 4,472 SITCA + 5,295 TDCC share-class
record in one validated public snapshot. Runs in O(n) over all fund records;
paged reads expose only public identities and data blockers. Per-user requests
do NOT crawl sources, save records, sync funds, set up jobs, or touch portfolio.

This is an *operator-facing work queue* for acquiring licensed NAV history,
NOT evidence that the same number of official historical price files exist.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
import json
import re

from .offshore_evidence import valid_isin
from .fund_universe_audit import CATEGORIES

SCHEMA="fiveline_allfund_batch_queue_v1"
MAX_QUEUE=24000
MAX_PAGE=200
DEFAULT_PAGE=100
SOURCES={
    "domestic_fund_share_class":"sitca_domestic_funds",
    "offshore_fund_share_class":"tdcc_offshore_funds",
}
STATES=(
    "MISSING_ISIN",
    "INVALID_ISIN",
    "MISSING_CURRENCY",
    "REUSED_SOURCE_CODE_REVIEW",
    "SHARED_ISIN_IDENTITY_REVIEW",
    "AWAITING_LICENSED_NAV_SOURCE",
)


def _identity_index(snapshot):
    if not isinstance(snapshot,dict) or not isinstance(snapshot.get("items"),list):
        raise ValueError("Verified market catalog required")
    rows=[r for r in snapshot["items"] if r.get("category") in CATEGORIES]
    if not rows or len(rows)>MAX_QUEUE:
        raise ValueError("Fund queue empty or beyond safe capacity")
    uids=[r.get("uid") for r in rows]
    if any(not isinstance(u,str) or not u for u in uids) or len(set(uids))!=len(uids):
        raise ValueError("Duplicate/blank fund source UID")
    if any(r.get("source")!=SOURCES[r["category"]] for r in rows):
        raise ValueError("Fund category and official directory source mismatch")
    # Stable across source ordering and restarts, but changes if any public
    # identity in the snapshot changes. Page 2+ must present this snapshot ID.
    rows.sort(key=lambda r:(r["category"],r["uid"]))
    fingerprints=[[r["category"],r["source"],r["uid"],r["code"],
                   r["name"],r.get("currency",""),r.get("isin","")] for r in rows]
    digest=sha256(json.dumps(fingerprints,ensure_ascii=False,
                             separators=(",",":")).encode("utf-8")).hexdigest()
    source_codes=Counter((r["source"],r["code"]) for r in rows)
    isins=defaultdict(list)
    for r in rows:
        value=str(r.get("isin","")).strip().upper()
        if value and valid_isin(value):
            isins[value].append(r)
    # Same ISIN and same exact class descriptor could be duplicated by a
    # source. But multiple names/currencies/codes require manual review.
    isin_ambiguous=set()
    for members in isins.values():
        if len(members)>1:
            for member in members:
                isin_ambiguous.add(member["uid"])
    return rows,digest,source_codes,isin_ambiguous


def _case(r,source_codes,isin_ambiguous):
    blockers=[]
    isin=str(r.get("isin","")).strip().upper()
    ccy=str(r.get("currency","")).strip().upper()
    if not isin:
        blockers.append("MISSING_ISIN")
    elif not valid_isin(isin):
        blockers.append("INVALID_ISIN")
    if not re.fullmatch(r"[A-Z]{3}",ccy):
        blockers.append("MISSING_CURRENCY")
    if source_codes[(r["source"],r["code"])]>1:
        blockers.append("REUSED_SOURCE_CODE_REVIEW")
    if r["uid"] in isin_ambiguous:
        blockers.append("SHARED_ISIN_IDENTITY_REVIEW")
    state=blockers[0] if blockers else "AWAITING_LICENSED_NAV_SOURCE"
    return {
        "uid":r["uid"],"source":r["source"],"category":r["category"],
        "code":r["code"],"name":r["name"],
        "currency":r.get("currency",""),"isin":r.get("isin",""),
        "queue_state":state,"blockers":blockers,
        "official_share_class_identity_approved":False,
        "authorized_3y_nav_file_received":False,
        "history_rows_accepted":0,
    }


def queue_page(snapshot, *, kind="all", offset=0, limit=DEFAULT_PAGE,
               expected_snapshot=None):
    if kind not in ("all","domestic","offshore"):
        raise ValueError("Fund queue category invalid")
    if type(offset) is not int or offset<0 or offset>MAX_QUEUE:
        raise ValueError("Fund queue offset invalid")
    if type(limit) is not int or not 1<=limit<=MAX_PAGE:
        raise ValueError("Fund queue page size invalid")
    if expected_snapshot is not None and (not isinstance(expected_snapshot,str)
            or not re.fullmatch(r"[a-f0-9]{64}",expected_snapshot)):
        raise ValueError("Source snapshot token invalid")
    rows,digest,codes,shared=_identity_index(snapshot)
    if expected_snapshot is not None and expected_snapshot!=digest:
        raise ValueError("Catalog changed; restart batch plan from first page")
    if offset and expected_snapshot is None:
        raise ValueError("Page continuation requires an exact snapshot token")

    cases=[_case(r,codes,shared) for r in rows]
    all_counts=Counter(c["queue_state"] for c in cases)
    all_blockers=Counter(b for c in cases for b in c["blockers"])
    source_counts=Counter(c["category"] for c in cases)
    if kind=="domestic":
        cases=[x for x in cases if x["category"]=="domestic_fund_share_class"]
    elif kind=="offshore":
        cases=[x for x in cases if x["category"]=="offshore_fund_share_class"]
    chunk=cases[offset:offset+limit]
    batch_counts=Counter(x["queue_state"] for x in chunk)
    total_pages=(len(cases)+limit-1)//limit
    return {
        "schema":SCHEMA,
        "status":"ALL_FUND_SOURCE_IDENTITIES_BATCH_PLANNED_ONLY",
        "checked_at_utc":snapshot.get("checked_at_utc"),
        "snapshot_sha256":digest,"kind":kind,
        "catalog_fund_records_checked":len(rows),
        "domestic_fund_records":source_counts["domestic_fund_share_class"],
        "offshore_fund_records":source_counts["offshore_fund_share_class"],
        "all_fund_status_counts":{key:all_counts.get(key,0) for key in STATES},
        "all_fund_blocker_counts":{key:all_blockers.get(key,0) for key in STATES[:-1]},
        "selected_scope_records":len(cases),
        "offset":offset,"limit":limit,"total_pages_at_page_size":total_pages,
        "page_records_checked":len(chunk),
        "page_status_counts":{key:batch_counts.get(key,0) for key in STATES},
        "next_offset":offset+len(chunk) if offset+len(chunk)<len(cases) else None,
        "rows":chunk,
        "history_nav_3y_uploaded_records":0,
        "history_nav_3y_approved_records":0,
        "cash_distribution_records_reconciled":0,
        "historical_fx_records_approved":0,
        "commercial_data_rights_verified":False,
        "public_directory_snapshot_only":True,
        "database_rows_written":0,
        "customer_data_read":False,
        "production_site_modified":False,
        "portfolio_or_strategies_modified":False,
        "note":"Every in-scope fund has a source-discovery queue state, not an approved fund share class or historical NAV price series.",
    }
