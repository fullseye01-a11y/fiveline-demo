"""Latest *domestic* fund NAV evidence by exact public directory UID.

Never reprice client portfolios or treat a similar-name match as an approved
share class. Offshore 'NAV total' is not a per-unit price; it is not supported.
A trusted upstream read-only NAV service is queried on user request only.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
import json
import re
import unicodedata

from .catalog import get_catalog, CatalogUnavailable

UPSTREAM = "https://fiveline-nav-readonly.onrender.com"
UPSTREAM_PATH = "/api/official-nav/search"
ORIGIN = "https://data.gov.tw/dataset/11109"
MAX_RESPONSE_BYTES = 250_000
MAX_UID_LENGTH = 80
MAX_AGE_DAYS = 7


class NavEvidenceUnavailable(Exception):
    pass


def identity(x):
    return " ".join(unicodedata.normalize("NFKC", str(x or "")).casefold().split())


def choose_instrument(uid):
    if not isinstance(uid, str) or not re.fullmatch(r"[a-zA-Z0-9-]{1,80}", uid):
        raise ValueError("基金識別碼無效")
    snap = get_catalog()
    matches = [item for item in snap["items"] if item["uid"] == uid]
    if len(matches) != 1:
        raise ValueError("市場名錄沒有唯一標的")
    return matches[0]


def _upstream_search(row, fetcher=None):
    # Query the longest distinctive name first; never use an arbitrary user
    # URL, user-supplied fund code, credentials, or unrelated external host.
    term = row["name"] if 2 <= len(row["name"]) <= 60 else row["code"]
    if len(term) < 2 or len(term) > 60:
        raise NavEvidenceUnavailable("此基金名稱無法安全比對")
    query = urlencode({"source": "sitca_domestic_daily", "q": term})
    url = UPSTREAM + UPSTREAM_PATH + "?" + query
    if fetcher is not None:
        return fetcher(url)
    req = Request(url, headers={
        "User-Agent": "FivelineAdviserLatestNavEvidence/1.0",
        "Accept": "application/json", "Accept-Encoding": "identity",
        "Connection": "close",
    })
    try:
        with urlopen(req, timeout=24) as resp:
            if resp.status != 200:
                raise NavEvidenceUnavailable("官方基金來源服務沒有可用回應")
            final = urlsplit(resp.geturl())
            if final.scheme != "https" or final.netloc != "fiveline-nav-readonly.onrender.com" or final.path != UPSTREAM_PATH:
                raise NavEvidenceUnavailable("基金資料來源網址異常")
            raw = resp.read(MAX_RESPONSE_BYTES + 1)
    except (OSError, TimeoutError) as exc:
        raise NavEvidenceUnavailable("暫時無法取得官方 NAV 證據") from exc
    if not raw or len(raw) > MAX_RESPONSE_BYTES:
        raise NavEvidenceUnavailable("NAV 資料來源大小異常")
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError):
        raise NavEvidenceUnavailable("官方 NAV 回應格式異常") from None


def _positive_nav(value):
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        raise NavEvidenceUnavailable("淨值格式異常")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise NavEvidenceUnavailable("淨值數值無法解析") from None
    if not amount.is_finite() or amount <= 0 or amount > Decimal("1000000000"):
        raise NavEvidenceUnavailable("不合理 NAV 數值")
    return str(amount)


def match_domestic_evidence(row, payload, today=None):
    if not isinstance(payload, dict) or payload.get("mode") != "government_open_data_latest_only":
        raise NavEvidenceUnavailable("來源不屬於已驗證的最新 NAV 資料")
    if payload.get("source") != "sitca_domestic_daily" or payload.get("source_dataset") != ORIGIN:
        raise NavEvidenceUnavailable("來源資料集身分不符")
    if payload.get("matched_to_legacy_fiveline") is not False:
        raise NavEvidenceUnavailable("官方 NAV 配對狀態不符合唯讀研究版")
    found = payload.get("records")
    if not isinstance(found, list) or len(found) > 30:
        raise NavEvidenceUnavailable("基金查詢結果超出範圍")
    candidates = [
        x for x in found if isinstance(x, dict) and
        x.get("source") == "sitca_domestic_daily" and
        identity(x.get("name")) == identity(row["name"]) and
        identity(x.get("code")) == identity(row["code"]) and
        identity(x.get("currency")) == identity(row["currency"])
    ]
    if len(candidates) != 1:
        # Never attach a price to an ambiguous or mismatched class.
        return {
            "status": "REVIEW_REQUIRED", "instrument_uid": row["uid"],
            "display_name": row["name"], "code": row["code"],
            "currency": row["currency"], "nav": None,
            "nav_date": None, "reason": (
                "官方 NAV 來源無法同時唯一核對名稱、代碼與幣別；"
                "已停止自動配對，需進一步確認基金級別。"),
            "class_identity_approved": False,
            "usable_for_portfolio": False,
            "usable_for_backtest": False,
        }

    x = candidates[0]
    if row["isin"] and identity(row["isin"]) != identity(x.get("isin")):
        return {
            "status":"REVIEW_REQUIRED","instrument_uid":row["uid"],
            "display_name":row["name"],"code":row["code"],
            "currency":row["currency"],"nav":None,"nav_date":None,
            "reason":"ISIN 不一致或來源未提供，不能認定基金級別相同。",
            "class_identity_approved":False,
            "usable_for_portfolio":False,
            "usable_for_backtest":False,
        }
    try:
        d = date.fromisoformat(x["nav_date"])
    except (ValueError, KeyError, TypeError):
        raise NavEvidenceUnavailable("淨值生效日期無法驗證") from None
    asof = today or datetime.now(timezone.utc).date()
    if d > asof or (asof - d).days < 0:
        raise NavEvidenceUnavailable("淨值日期異常，需人工核對")
    nav = _positive_nav(x.get("nav"))
    age = (asof-d).days
    return {
        "status": "LATEST_NAV_EVIDENCE_ONLY",
        "instrument_uid": row["uid"], "display_name": row["name"],
        "code": row["code"], "currency": row["currency"],
        "nav": nav, "nav_date": d.isoformat(), "age_calendar_days": age,
        "stale_warning": age > MAX_AGE_DAYS,
        "source": "SITCA", "source_dataset": ORIGIN,
        "source_last_verified_at_utc": str(payload.get("last_verified_at_utc", ""))[:35],
        "class_identity_approved": False,
        "usable_for_portfolio": False,
        "usable_for_backtest": False,
        "reason": "已核對公開來源名稱、代碼、幣別與單次淨值；未核准所有基金級別、配息及含息歷史序列。",
    }


def evidence(uid, fetcher=None, today=None):
    row = choose_instrument(uid)
    # Foreign source 3-20 is 'NAV subtotal (aggregate)', not a verified
    # per-unit share-class price. Stocks/ETFs are also out of this provider scope.
    if row["source"] != "sitca_domestic_funds" or row["category"] != "domestic_fund_share_class":
        return {
            "status": "UNSUPPORTED_PRICE_SOURCE",
            "instrument_uid": row["uid"], "display_name": row["name"],
            "code": row["code"], "currency": row["currency"],
            "nav": None, "nav_date": None,
            "reason": (
                "目前僅支援境內基金每單位 NAV 證據查詢。"
                "境外基金或 ETF 的級別／單位價格來源仍待驗證。"),
            "class_identity_approved": False,
            "usable_for_portfolio": False,
            "usable_for_backtest": False,
        }
    if row["class_identity_approved"] or row["historical_prices_ready"]:
        # Even if an upstream directory claims approval, this isolated
        # research function may not derive investment eligibility from it.
        pass
    if fetcher is not None:
        # Injected upstream data for deterministic tests; never use network.
        payload = _upstream_search(row, fetcher=fetcher)
    else:
        try:
            payload = _upstream_search(row)
        except NavEvidenceUnavailable:
            # If the separate free Render NAV service is unavailable, verify
            # the original official SITCA per-unit CSV. No stale fallback.
            from .sitca_direct_nav import lookup, SITCAUnavailable
            try:
                payload = lookup(row)
            except SITCAUnavailable as exc:
                raise NavEvidenceUnavailable(
                    "原獨立 NAV 服務及官方 SITCA 檔案均暫時無法驗證"
                ) from exc
    return match_domestic_evidence(row,payload,today=today)
