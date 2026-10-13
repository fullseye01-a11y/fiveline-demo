"""Read-only NAV backend readiness service for Render.

No quotes, provider credentials, client records, customer positions, or NAV writes.
Staging CLI exists separately and is *not* exposed as a public HTTP endpoint.
"""
from __future__ import annotations

from csv import DictReader
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
import threading
import time
from backend.providers.open_fund_nav import SOURCES, download_source, normalized_records
from backend.fund_matching import get_legacy_rows, make_report
from backend.class_dividend_audit import REGISTRY as CLASS_SOURCES, fetch_official, load_csv, evidence_review

ROOT = Path(__file__).resolve().parents[1]
SUMMARY_FILE = ROOT / "data" / "phase16_summary.json"
COVERAGE_FILE = ROOT / "data" / "phase16_instrument_coverage.csv"
CORS_ORIGIN = "https://fullseye01-a11y.github.io"
OFFICIAL_CACHE_TTL_SEC = 3600
_official_cache = {}
_cache_lock = threading.RLock()


def official_snapshot(source: str) -> dict:
    """On-demand government CSV check; in-memory TTL, no production NAV writes."""
    if source not in SOURCES:
        raise ValueError("unsupported official provider")
    # Serialise initial fetch to avoid hammering public government endpoints.
    with _cache_lock:
        cached = _official_cache.get(source)
        now = time.monotonic()
        if cached and now-cached["since"] < OFFICIAL_CACHE_TTL_SEC:
            return cached["data"]
        data = normalized_records(source, download_source(source))
        data["checked_at_utc"] = datetime.now(timezone.utc).isoformat()
        _official_cache[source] = {"since":now,"data":data}
        return data


def official_summary() -> dict:
    providers=[]
    for name in SOURCES:
        try:
            snapshot=official_snapshot(name)
            providers.append({"source":name, "count":snapshot["count"],
                "latest_nav_date":snapshot["latest_nav_date"],
                "checked_at_utc":snapshot["checked_at_utc"],
                "source_dataset":snapshot["origin"], "available":True})
        except Exception as exc:
            print("official_NAV_provider_error", name, type(exc).__name__, str(exc)[:240], flush=True)
            providers.append({"source":name,"available":False,
                "error":type(exc).__name__, "count":0})
    return {"mode":"government_open_data_latest_only", "providers":providers,
       "legacy_fiveline_history_updated":False,
       "share_class_matched_to_legacy":False,
       "dividend_split_adjusted":False,
       "cache_ttl_seconds":OFFICIAL_CACHE_TTL_SEC}



def matching_review() -> dict:
    """Never writes NAV or trusts similar names as matching share classes."""
    successes, failures={},{}
    for source in SOURCES:
        try:
            successes[source]=official_snapshot(source)
        except Exception as exc:
            failures[source]=type(exc).__name__
    rows=get_legacy_rows(COVERAGE_FILE)
    return make_report(rows,successes,failures)



_class_evidence_cache = {}

def class_dividend_evidence() -> dict:
    """Isolated metadata review: no historical NAV writes or automatic class approval."""
    with _cache_lock:
        cached=_class_evidence_cache.get("report")
        now=time.monotonic()
        if cached and now-cached["since"]<OFFICIAL_CACHE_TTL_SEC:
            return cached["value"]
        original=matching_review()
        source_rows={}
        for source in CLASS_SOURCES:
            _headers,rows=load_csv(source,fetch_official(source))
            source_rows[source]=rows
        result=evidence_review(
            original,
            source_rows["tdcc_offshore_basics"],
            source_rows["tdcc_offshore_recent_nav"],
            source_rows["tdcc_offshore_dividend"]
        )
        _class_evidence_cache["report"]={"since":time.monotonic(),"value":result}
        return result

def official_lookup(source: str, query: str, limit: int = 30) -> dict:
    if source not in SOURCES or len(query)<2 or len(query)>60:
        raise ValueError("invalid provider or search text (2-60 characters)")
    data=official_snapshot(source)
    q=query.casefold()
    matching=[x for x in data["records"] if q in x["name"].casefold()
              or q in x["code"].casefold() or q in x["isin"].casefold()]
    return {"mode":"government_open_data_latest_only","source":source,
        "source_dataset":data["origin"],"last_verified_at_utc":data["checked_at_utc"],
        "source_latest_nav_date":data["latest_nav_date"],
        "total_matches":len(matching),"records":matching[:limit],
        "matched_to_legacy_fiveline":False,
        "notice":"Official latest NAV only. Not linked to historical OLS charts; fund class/currency and total-return adjustments unverified."}


def report():
    summary = json.loads(SUMMARY_FILE.read_text(encoding="utf-8"))
    with COVERAGE_FILE.open(encoding="utf-8-sig", newline="") as file:
        instruments = list(DictReader(file))
    return summary, instruments


def service_status():
    summary, instruments = report()
    return {
        "service": "fiveline-nav-backend",
        "health": "ok",
        "mode": "read_only_historical_snapshot",
        "live_data_connected": False,
        "auto_refresh_enabled": False,
        "provider_credentials_configured": False,
        "client_accounts_connected": False,
        "data_source": "historical Excel-derived demonstration",
        "snapshot_latest_nav_date": summary["snapshot_latest_nav_date"],
        "instrument_count": len(instruments),
        "dividend_record_count": summary["total_dividend_rows"],
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "official_latest_nav_query_available": True,
        "warning": "Historical OLS charts are not auto-refreshed. A separate official latest-NAV lookup is available, but no class matching or adjustments are verified.",
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "FivelineStatus/0.1"

    def _send(self, code, body, content_type, head_only=False):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'; base-uri 'none'")
        self.send_header("Access-Control-Allow-Origin", CORS_ORIGIN)
        self.send_header("Vary", "Origin")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def do_GET(self):
        self._handle(False)

    def do_HEAD(self):
        self._handle(True)

    def do_POST(self):
        self._send(405, b'{"error":"read-only API; import is not public"}', "application/json; charset=utf-8")

    def _handle(self, head_only):
        path = urlsplit(self.path).path
        try:
            if path in ("/healthz", "/api/status"):
                data = service_status()
                if path == "/healthz":
                    data = {"health": data["health"], "live_data_connected": False}
            elif path == "/api/official-nav/status":
                data = official_summary()
            elif path == "/api/official-nav/match-report":
                data = matching_review()
            elif path == "/api/official-nav/class-evidence":
                data = class_dividend_evidence()
            elif path == "/api/official-nav/search":
                args=parse_qs(urlsplit(self.path).query,keep_blank_values=False)
                source=(args.get("source") or [""])[0]
                query=(args.get("q") or [""])[0].strip()
                if source not in SOURCES or not (2<=len(query)<=60):
                    return self._send(400,b'{"error":"valid source and q (2-60 chars) required"}',
                                      "application/json; charset=utf-8",head_only)
                data=official_lookup(source,query)
            elif path == "/api/coverage":
                s, instruments = report()
                data = {"source": "Excel historical snapshot", "live_data_connected": False,
                        "snapshot_latest_nav_date": s["snapshot_latest_nav_date"],
                        "records": instruments}
            elif path == "/":
                html = (
                    '<!doctype html><html lang="zh-Hant"><meta charset="utf-8">'
                    '<meta name="viewport" content="width=device-width,initial-scale=1">'
                    '<title>五線譜 NAV 更新後端｜驗證服務</title>'
                    '<style>body{font-family:system-ui;background:#f3f6fa;color:#213853;max-width:750px;'
                    'margin:10vh auto;padding:24px}main{background:white;border-radius:16px;padding:25px;'
                    'box-shadow:0 2px 18px #15233216}a{color:#145aa0}p{line-height:1.8}</style>'
                    '<main><h1>五線譜 NAV 後端｜第一階段</h1>'
                    '<p><b>歷史五線譜尚未自動更新，最新官方 NAV 可獨立查詢。</b></p>'
                    '<p>服務提供歷史 Excel 快照，以及境內／境外全球型基金官方最新 NAV 查詢，'
                    '不儲存客戶持倉、不接受網路匯入，也不提供即時投資訊號。</p>'
                    '<p><a href="/api/status">查看 API 資料狀態</a> · '
                    '<a href="/api/coverage">查看 68 檔歷史覆蓋摘要</a> · '
                    '<a href="/api/official-nav/status">官方 NAV 資料來源狀態</a></p>'
                    '<p><a href="https://fullseye01-a11y.github.io/fiveline-demo/">返回五線譜展示平台</a></p></main></html>'
                )
                return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8", head_only)
            else:
                return self._send(404, b'{"error":"not found"}', "application/json; charset=utf-8", head_only)
            body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8", head_only)
        except (OSError, ValueError, KeyError, json.JSONDecodeError, TimeoutError):
            self._send(503, b'{"error":"data unavailable or provider access failed"}', "application/json; charset=utf-8", head_only)


def main():
    port = int(os.environ.get("PORT", "10000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"Fiveline read-only NAV status listening on {port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
