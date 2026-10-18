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
from .offshore_evidence import latest_nav, dividend_events, OffshoreUnavailable
from .income_readiness import readiness
from .history_coverage import coverage
from .issuer_pdf_review import issuer_distribution_review
from .issuer_current import current_october_issuer
from .issuer_pdf_table import IssuerPdfTableUnverified
from .issuer_source_attestation import PdfFingerprintUnavailable

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
        if path == "/api/offshore-evidence":
            try:
                args = parse_qs(parsed.query, keep_blank_values=True)
                if (len(parsed.query)>155 or set(args)!={"uid","part"} or
                        any(len(v)!=1 for v in args.values())):
                    raise ValueError("境外基金查詢參數無效")
                part=args["part"][0]
                if part not in ("nav","dividend","readiness","issuer","issuer-current"):
                    raise ValueError("境外基金資料類型無效")
                uid=args["uid"][0]
                result = (latest_nav(uid) if part=="nav"
                          else dividend_events(uid) if part=="dividend"
                          else readiness(uid) if part=="readiness"
                          else issuer_distribution_review(uid) if part=="issuer"
                          else current_october_issuer(uid))
                return self._json(result)
            except ValueError as exc:
                return self._json({"error":str(exc)[:100]},400)
            except (CatalogUnavailable, OffshoreUnavailable,
                    IssuerPdfTableUnverified, PdfFingerprintUnavailable):
                return self._json({"error":"本次官方配息來源版本未通過驗證，請稍後重試；未核准任何報酬。"},503)
        if path == "/api/history-coverage":
            try:
                args = parse_qs(parsed.query, keep_blank_values=True)
                if (len(parsed.query)>115 or set(args)!={"uid"} or
                        len(args["uid"])!=1):
                    raise ValueError("歷史淨值核對參數無效")
                return self._json(coverage(args["uid"][0]))
            except ValueError as exc:
                return self._json({"error":str(exc)[:100]},400)
            except (CatalogUnavailable, OffshoreUnavailable, NavEvidenceUnavailable):
                return self._json({"error":"歷史 NAV 來源目前無法核對，請稍後再試。"},503)
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




def offshore_smoke_once():
    """One named offshore public fund's metadata; no NAV amount in logs."""
    uid = os.getenv("ADVISER_OFFSHORE_SMOKE_UID", "")
    if not uid:return
    try:
        row = latest_nav(uid)
        print("ADVISER_OFFSHORE_SMOKE " + json.dumps({
            "status":row.get("status"),
            "unit_nav_evidence_present":bool(row.get("nav")),
            "nav_date_present":bool(row.get("nav_date")),
            "isin_identity_present":bool(row.get("isin")),
            "portfolio_eligible":False,
            "historical_return_approved":False,
            "client_records_used":False,
        }, ensure_ascii=False),flush=True)
        div = dividend_events(uid)
        print("ADVISER_OFFSHORE_DIVIDEND_SMOKE " + json.dumps({
            "status": div.get("status"),
            "event_count": div.get("event_count"),
            "cash_per_unit_verified":False,
            "class_identity_approved":False,
            "dividend_adjustment_ready":False,
            "historical_return_approved":False,
        },ensure_ascii=False),flush=True)
        report = readiness(uid)
        print("ADVISER_INCOME_READINESS_SMOKE " + json.dumps({
            "status":report.get("status"),
            "recent_nav_observations":report.get("recent_nav_observations"),
            "dividend_event_count":report.get("known_dividend_event_count"),
            "dividend_proportion_verified_count":report.get("displayed_income_principal_ratio_verified_count"),
            "cash_per_unit_issuer_reconciled":report.get("issuer_per_unit_payouts_reconciled",0),
            "portfolio_eligible":False,
            "annualized_total_return_ready":False,
            "six_strategies_ready":False,
            "client_records_used":False,
        },ensure_ascii=False),flush=True)
    except OffshoreUnavailable as exc:
        print("ADVISER_OFFSHORE_SMOKE_SOURCE_FAILED "+type(exc).__name__,flush=True)
    except Exception as exc:
        print("ADVISER_OFFSHORE_SMOKE_FAILED "+type(exc).__name__,flush=True)




def issuer_excerpt_smoke_once():
    """One-shot publisher-to-TDCC excerpt comparison; no amounts in logs."""
    uid = os.getenv("ADVISER_ISSUER_PDF_SMOKE_UID", "")
    if not uid:
        return
    try:
        report = issuer_distribution_review(uid)
        print("ADVISER_FIDELITY_ISSUER_EXCERPT_SMOKE " + json.dumps({
            "status": report.get("status"),
            "published_excerpt_periods": report.get("published_monthly_event_count"),
            "tdcc_matching_dates": report.get("tdcc_matching_ex_date_count"),
            "tdcc_matching_composition": report.get("tdcc_matching_composition_count"),
            "tdcc_composition_conflicts": report.get("tdcc_composition_conflict_count"),
            "issuer_pdf_bytes_verified": False,
            "issuer_cash_amounts_approved": 0,
            "total_return_ready": False,
            "customer_records_saved": False
        }, ensure_ascii=False), flush=True)
    except Exception as exc:
        print("ADVISER_FIDELITY_ISSUER_EXCERPT_SMOKE_FAILED " +
              type(exc).__name__, flush=True)



def issuer_pdf_bytes_smoke_once():
    """One explicit official publisher PDF request; log digest, never raw PDF."""
    try:
        from .issuer_source_attestation import fetch_once
        result=fetch_once()
        print("ADVISER_FIDELITY_ISSUER_PDF_BYTES " + json.dumps({
            "status":"TRANSPORT_ENVELOPE_FINGERPRINT_ONLY",
            "sha256":result["pdf_sha256"],
            "pdf_bytes":result["pdf_bytes"],
            "exact_table_page_parsed":False,
            "issuer_cash_amounts_approved":0,
            "customer_records_saved":False,
        },ensure_ascii=False),flush=True)
    except Exception as exc:
        print("ADVISER_FIDELITY_ISSUER_PDF_BYTES_FAILED " +
              type(exc).__name__,flush=True)



def issuer_pdf_table_smoke_once():
    """One official PDF download, SHA+GLIAM table; count-only private logs."""
    try:
        from .issuer_pdf_table import verify_single_official_version
        verified=verify_single_official_version()
        print("ADVISER_FIDELITY_GLIAM_TABLE_VERIFIED " + json.dumps({
            "status":verified["status"],
            "page_number":verified["page_number"],
            "pdf_sha256":verified["pdf_sha256"],
            "class_code":verified["publisher_class_code"],
            "ex_date_rows":verified["verified_ex_date_count"],
            "issuer_pdf_unit_amount_rows":verified["issuer_per_unit_amounts_verified_against_pdf"],
            "tdcc_cash_amounts_reconciled":0,
            "total_return_approved":False,
            "portfolio_usage_approved":False,
        },ensure_ascii=False),flush=True)
    except Exception as exc:
        # IssuerPdfTableUnverified uses locally authored fixed reason strings.
        # Never log raw PDF text, fund amounts, end-user inputs or credentials.
        from .issuer_pdf_table import IssuerPdfTableUnverified
        diagnostic=(str(exc)[:110] if type(exc) is IssuerPdfTableUnverified else type(exc).__name__)
        print("ADVISER_FIDELITY_GLIAM_TABLE_FAILED " + diagnostic,flush=True)


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
    if os.getenv("ADVISER_OFFSHORE_SMOKE_UID", ""):
        threading.Thread(target=offshore_smoke_once, daemon=True, name="offshore-source-smoke").start()
    if os.getenv("ADVISER_ISSUER_PDF_SMOKE_UID", ""):
        threading.Thread(target=issuer_excerpt_smoke_once, daemon=True, name="issuer-excerpt-smoke").start()
    if os.getenv("ADVISER_ISSUER_PDF_BYTES_PROBE_ON_BOOT", "0") == "1":
        threading.Thread(target=issuer_pdf_bytes_smoke_once, daemon=True, name="issuer-pdf-bytes").start()
    if os.getenv("ADVISER_FIDELITY_PDF_TABLE_PROBE_ON_BOOT", "0") == "1":
        threading.Thread(target=issuer_pdf_table_smoke_once, daemon=True, name="fidelity-pdf-table").start()
    try:
        srv.serve_forever()
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
