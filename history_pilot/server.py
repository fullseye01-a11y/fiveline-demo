#!/usr/bin/env python3
"""Render free-tier *ephemeral* official-history pilot.

Only GET /health is public. The local SQLite cache is not served. The import
worker runs at most once per process startup if explicitly enabled.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import traceback
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .history_pilot import run
from .quality_report import summarize

STATE = {"phase": "not_started", "started_at": None}


def scrub_status(result: dict) -> dict:
    """Only counts: never expose prices, original documents, or full source URLs."""
    return {
        "requested_date": result["requested_date"],
        "approved_bars": result["approved_bars"],
        "pending_review": result["pending_review"],
        "batches": [
            {key: x[key] for key in ("source", "request", "retrieved", "inserted", "unchanged", "pending", "rejected")}
            for x in result["batches"]
        ],
    }


def pilot_worker() -> None:
    STATE["phase"] = "running"
    STATE["started_at"] = datetime.now(timezone.utc).isoformat()
    db = Path(os.getenv("PILOT_DB_PATH", "/tmp/fiveline-private-history/history_pilot.sqlite3"))
    try:
        # Refuse an ephemeral-only fetch if the durable database is not connected.
        from .storage_config import configured_dsn
        durable_dsn = configured_dsn()
        if durable_dsn is None:
            raise RuntimeError("Durable storage disabled; refusing ephemeral-only import")
        result = run(
            date=os.getenv("PILOT_DATE", "2026-10-13"),
            db_path=db,
            twse_codes=["2330", "0050"],
            tpex_codes=["8069", "6488"],
            legacy_unit=None,  # DO NOT guess old TPEx aaData trading-volume unit.
        )
        report = summarize(db)
        # Archive first, confirm totals from Neon. SQLite remains only a spool.
        from .postgres_store import persist_sqlite, private_summary
        pg_sync = persist_sqlite(db, dsn=durable_dsn)
        pg_counts = private_summary(dsn=durable_dsn)
        print("HISTORY_PILOT_NEON " + json.dumps(
            {"sync": pg_sync, "durable_counts": pg_counts}, ensure_ascii=False), flush=True)
        if os.getenv("PILOT_R2_BACKUP_ON_SYNC", "0") == "1":
            from .r2_backup import upload_and_verify
            result_backup = upload_and_verify(durable_dsn)
            print("HISTORY_PILOT_R2_BACKUP " + json.dumps(result_backup, ensure_ascii=False), flush=True)
        STATE["phase"] = "finished"
        print("HISTORY_PILOT_RESULT " + json.dumps(scrub_status(result), ensure_ascii=False), flush=True)
        print("HISTORY_PILOT_QUALITY " + json.dumps({
            "status": report["status"],
            "counts": report["counts"],
            "review_reasons": report["review_reasons"],
        }, ensure_ascii=False), flush=True)
    except Exception as exc:
        STATE["phase"] = "error"
        # Never log raw response bodies, market data or user credentials.
        print("HISTORY_PILOT_FAILED " + type(exc).__name__, flush=True)


def backfill_worker() -> None:
    """Private one-shot bounded importer, never a public endpoint."""
    STATE["phase"] = "backfill_running"
    STATE["started_at"] = datetime.now(timezone.utc).isoformat()
    try:
        from .backfill import run_backfill
        result = run_backfill()
        STATE["phase"] = "backfill_finished"
        # Counts only. No original OHLCV, credentials or source payloads.
        print("HISTORY_BACKFILL_SUCCESS " +
              json.dumps(result, ensure_ascii=False), flush=True)
    except Exception as exc:
        STATE["phase"] = "backfill_error"
        # Do not log exception text (may contain URI or sensitive response body).
        print("HISTORY_BACKFILL_FAILED " + type(exc).__name__, flush=True)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            payload = b'{"service":"fiveline-history-pilot","ok":true,"data_store":"not_exposed","public_market_data":false}\n'
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        elif self.path == "/robots.txt":
            payload = b"User-agent: *\nDisallow: /\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        else:
            self.send_error(404)

    def log_message(self, fmt, *args):
        pass  # Suppress request-specific logs.


def main():
    backfill = os.getenv("PILOT_BACKFILL_ON_BOOT", "0") == "1"
    initial = os.getenv("PILOT_FETCH_ON_BOOT", "0") == "1"
    if backfill and initial:
        raise RuntimeError("Cannot enable the original importer and backfill together")
    if backfill:
        threading.Thread(target=backfill_worker, daemon=True, name="backfill-worker").start()
    elif initial:
        threading.Thread(target=pilot_worker, daemon=True, name="pilot-worker").start()
    else:
        print("HISTORY_IMPORTS_DISABLED", flush=True)
    port = int(os.environ.get("PORT", "10000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print("HISTORY_PILOT_SERVER_READY", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
