#!/usr/bin/env python3
"""Read-only, aggregate-only SQLite quality report (does not export raw quotes)."""
from __future__ import annotations
import argparse
import json
import sqlite3
from pathlib import Path

def summarize(db_path: Path) -> dict:
    if not db_path.is_file():
        raise FileNotFoundError(f"Database does not exist: {db_path}")
    with sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True) as conn:
        versions = conn.execute("SELECT COUNT(*) FROM bar_versions").fetchone()[0]
        approved = conn.execute("SELECT COUNT(*) FROM approved_bars").fetchone()[0]
        pending = conn.execute("SELECT COUNT(*) FROM pending_review").fetchone()[0]
        amendments = conn.execute("SELECT COUNT(*) FROM bar_versions WHERE version>1").fetchone()[0]
        duplicated = conn.execute("SELECT COUNT(*) FROM (SELECT market,code,day,version FROM bar_versions GROUP BY market,code,day,version HAVING COUNT(*)>1)").fetchone()[0]
        reasons = dict(conn.execute("SELECT review_reason,COUNT(*) FROM pending_review GROUP BY review_reason"))
        runs = conn.execute("SELECT source,COUNT(*),COALESCE(SUM(inserted),0),COALESCE(SUM(unchanged),0),COALESCE(SUM(rejected),0) FROM ingest_runs GROUP BY source ORDER BY source").fetchall()
        symbols = conn.execute("SELECT market,code,MIN(day),MAX(day),COUNT(*) FROM approved_bars GROUP BY market,code ORDER BY market,code").fetchall()
        latest = conn.execute("SELECT source,request_key,inserted,unchanged,rejected,pending,fetched_at FROM ingest_runs ORDER BY id DESC LIMIT 20").fetchall()
    status = "NEEDS_REVIEW" if pending or any(row[4] for row in runs) else ("NO_REAL_DATA" if not versions else "LOCAL_CHECKS_PASS")
    return {
      "status": status, "data_authenticity": "CHECK_INGEST_RUNS: no claim of official fetching is implied",
      "scope": "private_local_staging_only",
      "counts": {"bar_versions": versions, "approved_bars": approved, "pending_review": pending,
                 "revisions": amendments, "duplicate_version_keys": duplicated},
      "review_reasons": reasons,
      "runs_by_source": [{"source": s, "runs": r, "inserted": i, "unchanged": u, "rejected": bad} for s,r,i,u,bad in runs],
      "symbols": [{"market": m, "code": c, "first": f, "last": l, "approved_days": n} for m,c,f,l,n in symbols],
      "latest_runs": [{"source": s, "request": key, "inserted": i, "unchanged": u,
                      "rejected": bad, "pending": p, "fetched_at": at} for s,key,i,u,bad,p,at in latest],
      "limitations": ["Trading-calendar, suspension and delisting completeness not checked",
                      "Unadjusted OHLCV; corporate actions and total returns are not calculated",
                      "No commercial redistribution or publish/export is performed"]
    }

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Read-only private quality report")
    parser.add_argument("--db", type=Path, default=Path(__file__).resolve().parent / "private_data" / "history_pilot.sqlite3")
    args = parser.parse_args()
    print(json.dumps(summarize(args.db), ensure_ascii=False, indent=2))
