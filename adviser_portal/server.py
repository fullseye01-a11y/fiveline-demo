"""Read-only adviser/client preview with synthetic data and NO user accounts.

Do not enable personal-data collection on this service. Production sign-in
requires separately deployed, audited authentication and RLS-backed storage.
"""
from __future__ import annotations
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from .catalog import search_catalog, CatalogUnavailable
from .nav_evidence import evidence, NavEvidenceUnavailable

from .engine import SAMPLE_POSITIONS, allocation_plan, risk_assessment, retirement_projection

STATIC = Path(__file__).resolve().parent / "static"
ROUTES = {"/": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "application/javascript; charset=utf-8"),
          "/style.css": ("style.css", "text/css; charset=utf-8")}
MAX_BODY = 16_384


class Handler(BaseHTTPRequestHandler):
    server_version = "FivelinePreview"
    sys_version = ""
    
    def _headers(self, status=200, content_type="application/json; charset=utf-8", length=0):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Content-Security-Policy",
                         "default-src 'none'; script-src 'self'; style-src 'self'; "
                         "img-src 'self' data:; connect-src 'self'; base-uri 'none'; "
                         "frame-ancestors 'none'; form-action 'self'")
        self.end_headers()

    def _json(self, obj, status=200):
        data = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._headers(status, length=len(data))
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlsplit(self.path)
        path = parsed.path
        if path == "/api/catalog":
            try:
                args = parse_qs(parsed.query, keep_blank_values=True)
                if len(parsed.query) > 320 or not all(len(x) == 1 for x in args.values()):
                    raise ValueError("搜尋參數無效")
                accepted = {"query", "kind", "offset", "limit", "refresh"}
                if set(args) - accepted:
                    raise ValueError("不支援的搜尋參數")
                raw = lambda name, default: args.get(name, [default])[0]
                refresh = raw("refresh", "0")
                if refresh not in ("0", "1"):
                    raise ValueError("更新參數無效")
                result = search_catalog(
                    query=raw("query", ""), kind=raw("kind", "all"),
                    offset=int(raw("offset", "0")),
                    limit=int(raw("limit", "30")),
                    refresh=(refresh == "1"))
                return self._json(result)
            except ValueError as exc:
                return self._json({"error": str(exc)[:100]}, 400)
            except CatalogUnavailable:
                return self._json({"error": "官方來源名錄目前無法驗證，請稍後重試。"}, 503)
        if path == "/api/nav-evidence":
            try:
                args = parse_qs(parsed.query, keep_blank_values=True)
                if len(parsed.query) > 130 or set(args) != {"uid"} or len(args["uid"]) != 1:
                    raise ValueError("基金級別查詢參數不正確")
                return self._json(evidence(args["uid"][0]))
            except ValueError as exc:
                return self._json({"error": str(exc)[:100]}, 400)
            except (CatalogUnavailable, NavEvidenceUnavailable):
                return self._json({"error":"官方基金淨值來源暫時無法核對，請稍後再查。"},503)
        if path == "/health":
            return self._json({"ok": True, "service": "fiveline-adviser-preview",
                               "data_mode": "synthetic_only", "client_records": False})
        if path == "/api/demo":
            return self._json({"positions": SAMPLE_POSITIONS, "synthetic": True,
                               "login_available": False, "market_data_live": False,
                               "modules": {"rr": "provisional", "allocation": "provisional",
                                           "retirement": "provisional", "five_line": "existing_demo_only",
                                           "clients": "not_enabled"}})
        if path in ROUTES:
            filename, mime = ROUTES[path]
            content = (STATIC / filename).read_bytes()
            self._headers(200, mime, len(content))
            self.wfile.write(content)
            return
        if path.startswith("/api/clients") or path.startswith("/api/portfolio"):
            return self._json({"error": "未開放：需要真實登入及租戶資料庫驗收。"}, 403)
        return self._json({"error": "頁面不存在"}, 404)

    def do_POST(self):
        path = urlsplit(self.path).path
        if path.startswith("/api/clients") or path.startswith("/api/portfolio") or path.startswith("/api/auth"):
            return self._json({"error": "此展示站不收集真實客戶資料，也沒有登入功能。"}, 403)
        handlers = {"/api/risk": risk_assessment,
                    "/api/allocation": allocation_plan,
                    "/api/retirement": retirement_projection}
        if path not in handlers:
            return self._json({"error": "未開放的 API"}, 404)
        if not self.headers.get("Content-Type", "").split(";")[0].strip().lower() == "application/json":
            return self._json({"error": "請使用 JSON"}, 415)
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size < 1 or size > MAX_BODY:
                return self._json({"error": "輸入資料大小不符"}, 413)
            payload = json.loads(self.rfile.read(size))
            if not isinstance(payload, dict):
                return self._json({"error": "JSON 必須是物件"}, 400)
            return self._json(handlers[path](payload))
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            return self._json({"error": str(exc)[:140]}, 400)
        except Exception:
            return self._json({"error": "試算失敗，請確認示範輸入格式。"}, 500)

    def log_message(self, *_args):
        # Do not log entered amounts or encoded URL parameters.
        pass



def catalog_smoke_once():
    """Opt-in deploy-time identity-only source validation; no prices or clients."""
    try:
        from .catalog import get_catalog
        result = get_catalog()
        print("ADVISER_CATALOG_SMOKE " + json.dumps({
            "catalog_rows": result["total_rows"],
            "category_counts": result["category_counts"],
            "checked_at_utc": result["checked_at_utc"],
            "private_customer_data_used": False,
            "live_nav_enabled": False,
        }, ensure_ascii=False), flush=True)
    except Exception as exc:
        # Do not echo URL bodies or caller/search data into server logs.
        print("ADVISER_CATALOG_SMOKE_FAILED " + type(exc).__name__, flush=True)




def nav_evidence_smoke_once():
    """One-shot named-public-fund verification; log status only, never NAV."""
    uid = os.getenv("ADVISER_NAV_EVIDENCE_SMOKE_UID", "")
    if not uid:
        return
    try:
        result = evidence(uid)
        print("ADVISER_NAV_EVIDENCE_SMOKE " + json.dumps({
            "status": result.get("status"),
            "official_unit_nav_present": bool(result.get("nav")),
            "nav_date_present": bool(result.get("nav_date")),
            "class_identity_approved": False,
            "usable_for_portfolio": False,
            "client_data_used": False,
        }, ensure_ascii=False), flush=True)
    except NavEvidenceUnavailable as exc:
        # These reason phrases are authored inside this private adapter and
        # contain no URLs, raw NAV values, secrets, client inputs or payloads.
        print("ADVISER_NAV_EVIDENCE_SMOKE_REVIEW " + str(exc)[:100], flush=True)
    except Exception as exc:
        print("ADVISER_NAV_EVIDENCE_SMOKE_FAILED " + type(exc).__name__, flush=True)



def main():
    # Dedicated independent free Render preview. No Neon URL, account secret,
    # customer identifiers or private backend endpoints at this stage.
    port = int(os.getenv("PORT", "10000"))
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print("ADVISER_PREVIEW_READY synthetic_only=true login=false", flush=True)
    if os.getenv("ADVISER_CATALOG_SMOKE_ON_BOOT", "0") == "1":
        threading.Thread(target=catalog_smoke_once, daemon=True, name="catalog-smoke").start()
    if os.getenv("ADVISER_NAV_EVIDENCE_SMOKE_UID", ""):
        threading.Thread(target=nav_evidence_smoke_once, daemon=True, name="nav-evidence-smoke").start()
    try:
        srv.serve_forever()
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
