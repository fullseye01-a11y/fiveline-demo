"""Public market instrument directory for the adviser ALPHA (identity only).

This proxy loads one capped, public, versioned, official-source catalog snapshot
from GitHub, and serves paginated identity-only results. It does NOT approve
share classes, NAV history, dividends, FX, suitability or portfolio exposure.
No live price data, login data or customer positions are stored or fetched.
"""
from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock
from urllib.request import Request, urlopen
import json
import re
import time
import unicodedata

RAW_URL = ("https://raw.githubusercontent.com/fullseye01-a11y/"
           "fiveline-demo/market-universe/public_market_universe/latest.json")
ALLOWED_SOURCES = frozenset((
    "twse_listed_stocks", "tpex_otc_stocks", "twse_etfs", "tpex_etfs",
    "sitca_domestic_funds", "tdcc_offshore_funds",
))
KINDS = frozenset((
    "all", "domestic_fund_share_class", "offshore_fund_share_class",
    "listed_stock", "otc_stock", "etf",
))
MAX_BYTES = 8_000_000
MAX_ITEMS = 24_000
PAGE_SIZE = 30
MAX_PAGE_SIZE = 50
# Explicit user refresh is permitted, but rate-limited across all visitors.
MIN_REFRESH_SECONDS = 120
_lock = Lock()
_cache = None
_last_fetch = 0.0


class CatalogUnavailable(Exception):
    pass


def _string(val, cap=220):
    if not isinstance(val, str):
        return ""
    return val.strip()[:cap]


def _norm(text):
    return unicodedata.normalize("NFKC", text).casefold().strip()


def validate_catalog(doc):
    if not isinstance(doc, dict) or doc.get("schema") != "fiveline_market_universe_v1":
        raise CatalogUnavailable("市場名錄版本不符")
    if doc.get("catalog_complete_for_configured_sources") is not True:
        raise CatalogUnavailable("市場來源尚未全部成功更新")
    if set(doc.get("covered_sources", [])) != ALLOWED_SOURCES:
        raise CatalogUnavailable("市場來源清單未完成")
    rows = doc.get("instruments")
    if not isinstance(rows, list) or not (1 <= len(rows) <= MAX_ITEMS):
        raise CatalogUnavailable("市場標的名錄數量不正確")
    if doc.get("total_rows") != len(rows):
        raise CatalogUnavailable("市場名錄統計與內容筆數不一致")
    if not isinstance(doc.get("category_counts"), dict):
        raise CatalogUnavailable("市場類別統計缺漏")
    if sum(doc["category_counts"].values()) != len(rows):
        raise CatalogUnavailable("市場類別統計異常")
    checked = _string(doc.get("checked_at_utc"))
    try:
        d = datetime.fromisoformat(checked.replace("Z", "+00:00"))
        if not d.tzinfo:
            raise ValueError("missing timezone")
    except (ValueError, TypeError):
        raise CatalogUnavailable("市場名錄更新時間無效") from None
    compact = []
    seen = set()
    sums = {}
    for row in rows:
        if not isinstance(row, dict):
            raise CatalogUnavailable("市場名錄格式錯誤")
        uid = _string(row.get("uid"), 80)
        source = _string(row.get("source"), 90)
        category = _string(row.get("category"), 90)
        name = _string(row.get("name"), 240)
        code = _string(row.get("code"), 80)
        if not uid or uid in seen or source not in ALLOWED_SOURCES or category not in KINDS - {"all", "etf"}:
            raise CatalogUnavailable("市場級別身分或資料來源異常")
        if not name or not code:
            raise CatalogUnavailable("市場名稱／代碼缺漏")
        seen.add(uid)
        sums[category] = sums.get(category, 0) + 1
        compact.append({
            "uid": uid, "source": source, "category": category,
            "name": name, "code": code,
            "currency": _string(row.get("currency"), 24),
            "isin": _string(row.get("isin"), 40),
            "class_identity_approved": row.get("class_identity_approved") is True,
            "historical_prices_ready": row.get("historical_prices_ready") is True,
        })
    if sums != doc["category_counts"]:
        raise CatalogUnavailable("市場類別合計驗證失敗")
    return {
        "checked_at_utc": checked,
        "source_url": RAW_URL,
        "total_rows": len(compact),
        "category_counts": sums,
        "known_coverage_gaps": [
            _string(x, 280) for x in doc.get("known_coverage_gaps", [])[:10]
        ],
        "items": compact,
    }


def _download():
    req = Request(RAW_URL, headers={
        "User-Agent": "FivelineAdviserResearchPreview/1.0",
        "Accept": "application/json", "Accept-Encoding": "identity",
    })
    try:
        with urlopen(req, timeout=24) as response:
            if response.status != 200:
                raise CatalogUnavailable("公開基金名錄暫時無法讀取")
            # Refuse redirects to untrusted domains.
            if response.geturl().split("?")[0] != RAW_URL:
                raise CatalogUnavailable("公開基金名錄來源不符")
            raw = response.read(MAX_BYTES + 1)
    except (TimeoutError, OSError) as exc:
        raise CatalogUnavailable("目前無法連上公開基金名錄") from exc
    if not raw or len(raw) > MAX_BYTES:
        raise CatalogUnavailable("公開名錄大小異常")
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError):
        raise CatalogUnavailable("公開名錄資料損毀") from None
    return validate_catalog(doc)


def get_catalog(refresh=False):
    """Lazy-load only on user request. No polling, cron or background updates."""
    global _cache, _last_fetch
    with _lock:
        now = time.monotonic()
        if _cache is None or (refresh and now - _last_fetch >= MIN_REFRESH_SECONDS):
            snapshot = _download()  # prior verified cache survives network errors
            _cache = snapshot
            _last_fetch = time.monotonic()
        return _cache


def search_catalog(query="", kind="all", offset=0, limit=PAGE_SIZE, refresh=False):
    if not isinstance(query, str) or len(query) > 80:
        raise ValueError("搜尋字數不可超過 80 字")
    if kind not in KINDS:
        raise ValueError("標的類別不正確")
    if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= MAX_ITEMS:
        raise ValueError("分頁起點錯誤")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_PAGE_SIZE:
        raise ValueError("單頁最多 50 筆")
    snap = get_catalog(refresh=refresh)
    term = _norm(query)
    matches = []
    for item in snap["items"]:
        if kind != "all" and (item["category"] != kind if kind != "etf" else item["source"] not in ("twse_etfs", "tpex_etfs")):
            continue
        if term and term not in _norm(item["name"]+" "+item["code"]+" "+item["isin"]):
            continue
        matches.append(item)
    return {
        "schema": "fiveline_adviser_catalog_search_v1",
        "status": "PUBLIC_DIRECTORY_ONLY",
        "checked_at_utc": snap["checked_at_utc"],
        "source": snap["source_url"],
        "total_catalog_rows": snap["total_rows"],
        "category_counts": snap["category_counts"],
        "known_coverage_gaps": snap["known_coverage_gaps"],
        "query": query.strip(),
        "kind": kind,
        "offset": offset,
        "limit": limit,
        "matched_rows": len(matches),
        "items": matches[offset:offset+limit],
        "can_add_real_portfolio": False,
        "class_identity_reviewed": False,
        "historical_nav_backfilled": False,
        "real_prices_available": False,
        "customer_data_saved": False,
    }
