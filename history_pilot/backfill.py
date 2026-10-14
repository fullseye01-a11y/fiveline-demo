#!/usr/bin/env python3
"""One-time, bounded, private official OHLCV backfill (NOT a market crawler).

Safeguards:
* Exactly 2 TWSE month calls and 5 TPEx trading-day calls, at most 7 requests.
* Parse and validate all responses before uploading any SQLite rows to Neon.
* Keep all exchange quotes in ephemeral SQLite and private Neon; never serve data.
* Fail closed if any selected instrument or expected response date is absent.
* Do not reinterpret missing holidays or trading suspensions as zero bars.
* No background/recurring execution; caller must explicitly opt in.
"""
from __future__ import annotations

import datetime as dt
import json
import tempfile
import time
from pathlib import Path

if __package__:
    from .history_pilot import (
        TWSE_URL, TPEX_URL, fetch_json, iso_date, open_db,
        parse_twse, parse_tpex, stage_batch,
    )
    from .postgres_store import persist_sqlite, private_summary
    from .storage_config import configured_dsn
else:
    from history_pilot import (
        TWSE_URL, TPEX_URL, fetch_json, iso_date, open_db,
        parse_twse, parse_tpex, stage_batch,
    )
    from postgres_store import persist_sqlite, private_summary
    from storage_config import configured_dsn

TWSE_CODES = ("2330", "0050")
TPEX_CODES = ("8069", "6488")
TWSE_MONTH = "2026-09"
TPEX_DATES = ("2026-10-06", "2026-10-07", "2026-10-08", "2026-10-12", "2026-10-13")
LAST_APPROVED_CUTOFF = "2026-10-13"
MAX_HTTP_REQUESTS = 7


def request_plan() -> tuple[dict, ...]:
    assert len(TWSE_CODES) == 2 and len(TPEX_DATES) == 5
    plan = []
    for code in TWSE_CODES:
        plan.append({
            "market": "TWSE", "code": code, "month": TWSE_MONTH,
            "request_key": f"{code}/{TWSE_MONTH}",
            "url": TWSE_URL,
            "params": {"date": TWSE_MONTH.replace("-", "") + "01", "stockNo": code, "response": "json"},
        })
    for day in TPEX_DATES:
        # For TPEx, the official endpoint takes ROC date and returns many symbols.
        date = dt.date.fromisoformat(iso_date(day))
        roc = date.year - 1911
        plan.append({
            "market": "TPEX", "day": day, "request_key": day,
            "url": TPEX_URL,
            "params": {"l": "zh-tw", "d": f"{roc}/{date:%m/%d}", "se": "EW"},
        })
    if (len(plan) != MAX_HTTP_REQUESTS or
            max(TPEX_DATES) > LAST_APPROVED_CUTOFF or
            TWSE_MONTH >= LAST_APPROVED_CUTOFF[:7]):
        raise RuntimeError("Pilot request bounds exceeded")
    return tuple(plan)


def plan_summary() -> dict:
    plan = request_plan()
    return {
        "mode": "dry_run", "api_calls": len(plan), "rate_limit_seconds": 1.2,
        "twse_codes": list(TWSE_CODES), "twse_month": TWSE_MONTH,
        "tpex_codes": list(TPEX_CODES), "tpex_dates": list(TPEX_DATES),
        "max_cutoff": LAST_APPROVED_CUTOFF,
        "public_raw_market_data": False, "postgres_write": False,
    }


def run_backfill(*, connect_to_neon: bool = True, delay: float = 1.2) -> dict:
    """Fetch, stage and then transactionally archive, or fail without Neon writes."""
    if delay < 1.0:
        raise ValueError("Backfill request throttle must be at least 1.0 sec")
    plan = request_plan()
    dsn = configured_dsn() if connect_to_neon else None
    if connect_to_neon and not dsn:
        raise RuntimeError("Neon is unavailable; no official requests were sent")

    batches = []
    with tempfile.TemporaryDirectory(prefix="fiveline-private-small-backfill-") as folder:
        db_path = Path(folder) / "backfill.sqlite3"
        with open_db(db_path) as con:
            for index, request in enumerate(plan):
                if index:
                    time.sleep(delay)
                source = request["market"]
                doc = fetch_json(request["url"], request["params"])
                if source == "TWSE":
                    bars, errors = parse_twse(
                        doc, request["code"], request["month"],
                        as_of=LAST_APPROVED_CUTOFF)
                    found = {b["code"] for b in bars}
                    expected = {request["code"]}
                else:
                    bars, errors = parse_tpex(
                        doc, request["day"], set(TPEX_CODES), legacy_unit=None)
                    found = {b["code"] for b in bars}
                    expected = set(TPEX_CODES)
                # Atomic reject-on-error: never load a partly parsed response.
                if errors or not bars or found != expected:
                    raise RuntimeError(
                        f"Official {source} batch {request['request_key']} "
                        "has invalid/unavailable selected rows; no Neon sync performed")
                summary = stage_batch(con, source, request["request_key"], bars, [])
                if summary["rejected"]:
                    raise RuntimeError("Staging rejection; no Neon sync performed")
                batches.append({
                    "market": source, "request_key": request["request_key"],
                    "staged": summary["inserted"],
                    "unchanged_locally": summary["unchanged"],
                    "review_required": summary["pending"],
                })
            expected_staged = con.execute("SELECT COUNT(*) FROM bar_versions").fetchone()[0]
            if expected_staged < len(TWSE_CODES) + len(TPEX_CODES):
                raise RuntimeError("Unreasonably small staged backfill")
        if not connect_to_neon:
            return {
                "status": "LOCAL_STAGE_ONLY", "api_calls": len(batches),
                "staged_version_count": expected_staged,
                "batches": batches, "uploaded_to_neon": False,
            }
        # Single Neon transaction; a failure rolls the archive back.
        synced = persist_sqlite(db_path, dsn=dsn)
        verified = private_summary(dsn=dsn)
        if synced["incoming"] != expected_staged or (
                synced["inserted"] + synced["unchanged"] != synced["incoming"]):
            raise RuntimeError("Archive accounting mismatch (check Neon before retrying)")
        return {
            "status": "NEON_SYNCHRONIZED",
            "api_calls": len(batches), "staged_version_count": expected_staged,
            "neon_inserted_versions": synced["inserted"],
            "neon_previously_present_versions": synced["unchanged"],
            "neon_pending_this_run": synced["pending"],
            "neon_approved_total": verified["approved_bars"],
            "neon_pending_total": verified["pending_review"],
            "batches": batches, "public_raw_market_data": False,
        }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Controlled seven-call historical OHLCV backfill")
    parser.add_argument("--execute", action="store_true",
                        help="Explicitly fetch and sync 7 official batches into Neon")
    args = parser.parse_args()
    result = run_backfill(connect_to_neon=True) if args.execute else plan_summary()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
