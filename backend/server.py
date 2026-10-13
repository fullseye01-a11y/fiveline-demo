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
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
SUMMARY_FILE = ROOT / "data" / "phase16_summary.json"
COVERAGE_FILE = ROOT / "data" / "phase16_instrument_coverage.csv"
CORS_ORIGIN = "https://fullseye01-a11y.github.io"


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
        "warning": "No live or automatically updated NAV prices are available; not for trading decisions.",
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
                    '<p><b>伺服器已啟動，但尚未連接最新 NAV 行情。</b></p>'
                    '<p>目前僅提供歷史 Excel 快照的健康檢查及讀取介面，'
                    '不儲存客戶持倉、不接受網路匯入，也不提供即時投資訊號。</p>'
                    '<p><a href="/api/status">查看 API 資料狀態</a> · '
                    '<a href="/api/coverage">查看 68 檔歷史覆蓋摘要</a></p>'
                    '<p><a href="https://fullseye01-a11y.github.io/fiveline-demo/">返回五線譜展示平台</a></p></main></html>'
                )
                return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8", head_only)
            else:
                return self._send(404, b'{"error":"not found"}', "application/json; charset=utf-8", head_only)
            body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8", head_only)
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            self._send(503, b'{"error":"historical snapshot unavailable"}', "application/json; charset=utf-8", head_only)


def main():
    port = int(os.environ.get("PORT", "10000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"Fiveline read-only NAV status listening on {port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
