"""Whole-catalog recent TDCC evidence scan; never imports or outputs prices.

Consumes bounded local official CSV snapshots. This is an offline operator
command, not a public upload route, history importer or authorization review.
"""
from collections import Counter, defaultdict
from datetime import date
from hashlib import sha256
import json
from pathlib import Path

from . import offshore_evidence as offshore
from .catalog import validate_catalog
from .fund_batch_queue import _identity_index


def audit_recent_market(snapshot, basic_raw, recent_raw, *, today=None):
    today = today or date.today()
    rows, digest, _, shared = _identity_index(snapshot)
    raw = {"basic": basic_raw, "recent": recent_raw}
    parsed = {k: offshore.parse_official_csv(k, v) for k, v in raw.items()}
    indexes = {k: defaultdict(list) for k in parsed}
    recent_isins = set()
    for kind, records in parsed.items():
        isin_field = "基金ISIN_CODE" if kind == "basic" else "ISINCODE"
        for record in records:
            key = (record["基金代碼"].strip().upper(), record[isin_field].strip().upper(),
                   offshore._ccy(record["計價幣別"]), offshore._norm_name(record["基金名稱"]))
            indexes[kind][key].append(record)
            if kind == "recent":
                recent_isins.add(key[1])
    results = []
    counts, dates, blockers = Counter(), Counter(), Counter()
    observations = 0
    for start in range(0, len(rows), 100):
        for row in rows[start:start + 100]:
            item = {"uid": row["uid"], "recent_nav_date_count": 0}
            if row["category"] == "domestic_fund_share_class":
                status = "DOMESTIC_SOURCE_NOT_SUPPLIED"
                item["blocker_code"] = status
            elif row["uid"] in shared:
                status = "SHARED_ISIN_IDENTITY_REVIEW"
                item["blocker_code"] = status
            else:
                ident = offshore._identity(row)
                # Each existing matcher receives only the exact key's rows.
                # Sources are parsed once, rather than downloaded per fund.
                matched = offshore.latest_nav_for_row(
                    row, fetcher=lambda kind: indexes[kind].get(ident, []), today=today)
                status = matched["status"]
                if status == "REVIEW_REQUIRED":
                    code = matched["reason_code"]
                    if code == "IDENTITY_INCOMPLETE":
                        isin = str(row.get("isin", "")).strip().upper()
                        code = ("MISSING_ISIN" if not isin else "INVALID_ISIN"
                                if not offshore.valid_isin(isin) else "UNSUPPORTED_CLASS_IDENTITY")
                    elif code == "NO_VALID_RECENT_OBSERVATIONS":
                        code = ("RECENT_ISIN_ABSENT" if ident[1] not in recent_isins else
                                "RECENT_CLASS_KEY_MISMATCH" if not indexes["recent"].get(ident) else
                                "RECENT_CLASS_OBSERVATIONS_INVALID")
                    item["blocker_code"] = code
                if status == "OFFSHORE_RECENT_NAV_EVIDENCE_ONLY":
                    if matched["stale_warning"]:
                        status = "STALE_RECENT_NAV_EVIDENCE_ONLY"
                        item["blocker_code"] = status
                    else:
                        item["recent_nav_date_count"] = len(matched["recent_nav_days"])
                        item["latest_nav_date"] = matched["nav_date"]
                        dates[matched["nav_date"]] += 1
                        observations += item["recent_nav_date_count"]
            item["status"] = status
            counts[status] += 1
            if item.get("blocker_code"):
                blockers[item["blocker_code"]] += 1
            results.append(item)
    return {
        "schema": "fiveline_full_market_recent_source_scan_v1",
        "as_of": today.isoformat(), "catalog_snapshot_sha256": digest,
        "catalog_fund_records": len(rows), "batches_at_100": (len(rows) + 99) // 100,
        "official_source_rows": {k: len(v) for k, v in parsed.items()},
        "source_sha256": {k: sha256(v).hexdigest() for k, v in raw.items()},
        "fund_status_counts": dict(counts), "latest_date_counts": dict(dates),
        "blocker_counts": dict(blockers),
        "verified_recent_fund_date_observations": observations,
        "results": results, "three_year_history_imported_funds": 0,
        "three_year_history_imported_rows": 0, "all_fund_3y_nav_ready": False,
        "source_transport_verified_by_offline_scanner": False,
        "commercial_data_rights_verified": False,
        "portfolio_or_six_strategies_modified": False,
    }


def _read(path, cap):
    file = Path(path)
    if file.stat().st_size > cap:
        raise ValueError("SOURCE_FILE_TOO_LARGE")
    return file.read_bytes()


def _cli():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--tdcc-basic", required=True)
    parser.add_argument("--tdcc-recent", required=True)
    args = parser.parse_args()
    snapshot = validate_catalog(json.loads(_read(args.catalog, 8_000_000)))
    report = audit_recent_market(snapshot,
                                 _read(args.tdcc_basic, offshore.MAX_BYTES),
                                 _read(args.tdcc_recent, offshore.MAX_BYTES))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    _cli()
