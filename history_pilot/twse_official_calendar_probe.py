"""TWSE 2026 annual official schedule verification for existing Neon research bars.

One bounded official HTTP GET only when explicitly enabled. A published annual
holiday/special-session table is a *planned schedule*, not independent proof
that each session actually traded, nor an historical OHLCV redistribution license.
The result reports date-only coverage metadata, never prices or credentials.
"""
from __future__ import annotations

from datetime import date, timedelta, datetime, timezone
from hashlib import sha256
import json
import re
import ssl
from urllib.request import Request, urlopen

from .calendar_coverage import neon_read_only_dates

SOURCE_URL = "https://www.twse.com.tw/holidaySchedule/holidaySchedule?response=json"
ALTERNATE_YEAR_URL = "https://www.twse.com.tw/holidaySchedule/holidaySchedule?response=json&queryYear=115"
YEAR = 2026
MAX_BYTES = 150_000
PILOT_FIRST = "2026-09-01"
PILOT_LAST = "2026-10-13"
PILOT_CODES = ("0050", "2330")
SCHEMA = "fiveline_twse_2026_official_schedule_probe_v1"
EXPECTED_ROWS_2026 = 27

OPEN_NAMES = {
    "國曆新年開始交易日", "農曆春節前最後交易日", "農曆春節後開始交易日",
}
CLOSED_NAMES = {
    "中華民國開國紀念日", "市場無交易，僅辦理結算交割作業",
    "農曆除夕及春節", "和平紀念日", "兒童節及民族掃墓節",
    "勞動節", "端午節", "中秋節", "孔子誕辰紀念日/ 教師節",
    "國慶日", "臺灣光復暨金門古寧頭大捷紀念日", "行憲紀念日",
}
# These reference dates are expressly present in the 2026 TWSE bulletin.
REQUIRED_2026_CLOSED = {
    "2026-02-12", "2026-02-13", "2026-09-25",
    "2026-09-28", "2026-10-09", "2026-10-26",
}


def _iso(value):
    text = str(value)
    if date.fromisoformat(text).isoformat() != text:
        raise ValueError("expected strict ISO trading-calendar day")
    return text


def parse_twse_official_2026(payload):
    """Return explicit special days; fail on schema/name/date changes."""
    if not isinstance(payload, dict) or payload.get("stat") != "ok":
        raise ValueError("official TWSE annual calendar status unavailable")
    if payload.get("queryYear") != YEAR or str(payload.get("date", ""))[:4] != str(YEAR):
        raise ValueError("official year does not match 2026 research window")
    fields = payload.get("fields")
    rows = payload.get("data")
    if (not isinstance(fields, list) or not isinstance(rows, list)
            or not all(x in fields for x in ("日期", "名稱", "說明"))
            or payload.get("total") != len(rows) or len(rows) != EXPECTED_ROWS_2026):
        raise ValueError("official TWSE annual event row count/schema changed; manual review")
    events = {}
    for cells in rows:
        if not isinstance(cells, list) or len(cells) != len(fields):
            raise ValueError("invalid official TWSE calendar row")
        item = dict(zip(fields, cells))
        day, name = _iso(item["日期"]), re.sub(r"\s+", " ", str(item["名稱"]).strip())
        if not day.startswith(f"{YEAR}-") or day in events:
            raise ValueError("official TWSE dates duplicated or outside year")
        if name in OPEN_NAMES:
            is_open = True
        elif name in CLOSED_NAMES:
            is_open = False
        else:
            raise ValueError("unknown official holiday/open-session classification; manual review")
        events[day] = {"is_open": is_open, "event": name}
    if any(day not in events or events[day]["is_open"]
           for day in REQUIRED_2026_CLOSED):
        raise ValueError("2026 official known market-closure sentinel absent")
    if not any(event["is_open"] for event in events.values()):
        raise ValueError("official positive trading-day markers absent")
    return events


def scheduled_days(events, first=PILOT_FIRST, last=PILOT_LAST):
    start, stop = date.fromisoformat(_iso(first)), date.fromisoformat(_iso(last))
    if stop < start or start.year != YEAR or stop.year != YEAR:
        raise ValueError("invalid annual-calendar window")
    if (stop-start).days > 92:
        raise ValueError("research window capped at 93 civil days")
    days = []
    for i in range((stop-start).days + 1):
        d = start + timedelta(days=i)
        day = d.isoformat()
        explicit = events.get(day)
        is_open = explicit["is_open"] if explicit is not None else d.weekday() < 5
        if is_open:
            days.append(day)
    return days


def summarize_schedule_against_neon(events, neon_date_rows, fetched_sha256, now=None):
    planned = scheduled_days(events)
    session_set = set(planned)
    observed = {code:set() for code in PILOT_CODES}
    for market, code, day in neon_date_rows:
        day = _iso(day)
        if market == "TWSE" and str(code) in observed and PILOT_FIRST <= day <= PILOT_LAST:
            if day in observed[str(code)]:
                raise ValueError("duplicate Neon approved daily date")
            observed[str(code)].add(day)
    symbols=[]
    for code in PILOT_CODES:
        unexpected = sorted(observed[code]-session_set)
        missing = sorted(session_set-observed[code])
        symbols.append({
            "market":"TWSE","code":code,"research_start":PILOT_FIRST,
            "research_end":PILOT_LAST, "planned_sessions":len(planned),
            "neon_approved_days":len(observed[code]),
            "planned_session_missing_count":len(missing),
            "planned_session_missing_date_sample":missing[:12],
            "quotes_on_officially_closed_day_count":len(unexpected),
            "quotes_on_officially_closed_day_sample":unexpected[:12],
            "actual_exchange_sessions_independently_verified":False,
            "listing_window_independently_verified":False,
        })
    checked=now or datetime.now(timezone.utc)
    if checked.tzinfo is None:
        raise ValueError("timestamp timezone required")
    return {
        "schema":SCHEMA,"checked_at_utc":checked.astimezone(timezone.utc).isoformat(),
        "source":SOURCE_URL, "source_payload_sha256":fetched_sha256,
        "official_special_event_rows":len(events),"year":YEAR,
        "scope":"two already-staged TWSE instruments in a fixed 43-day research window",
        "total_planned_sessions_per_instrument":len(planned),
        "planned_closed_special_dates_in_range":sorted(
            d for d,e in events.items() if PILOT_FIRST <= d <= PILOT_LAST and not e["is_open"]),
        "symbol_count":len(symbols),
        "missing_planned_session_count":sum(x["planned_session_missing_count"] for x in symbols),
        "unexpected_closed_session_quotes":sum(x["quotes_on_officially_closed_day_count"] for x in symbols),
        "actual_daily_sessions_verified":False,
        "tpex_calendar_verified":False,
        "complete_historical_coverage_verified":False,
        "stock_etf_corporate_actions_adjusted":False,
        "licensed_public_market_data_redistribution":False,
        "raw_quotes_publicly_redistributed":False,
        "neon_database_mutated":False,
        "import_executed":False,
        "production_analyses_modified":False,
        "instruments":symbols,
    }


def fetch_official_2026_payload(fetcher=None):
    """One endpoint request, capped response. Never sends Neon credentials."""
    if fetcher is not None:
        return fetcher()
    req=Request(SOURCE_URL,headers={
        "User-Agent":"Mozilla/5.0 (compatible; FivelineCalendarResearch/1.0)",
        "Accept":"application/json","Accept-Encoding":"identity",
        "Connection":"close",
    })
    with urlopen(req,timeout=24,context=ssl.create_default_context()) as response:
        if response.status != 200:
            raise RuntimeError("official calendar not HTTP 200")
        body=response.read(MAX_BYTES+1)
    if not body or len(body)>MAX_BYTES:
        raise ValueError("official calendar body empty or too large")
    return json.loads(body.decode("utf-8-sig")),sha256(body).hexdigest()


def run_probe(dsn, fetcher=None):
    if not dsn:
        raise RuntimeError("Neon private connection required")
    payload,digest=fetch_official_2026_payload(fetcher)
    events=parse_twse_official_2026(payload)
    dates=neon_read_only_dates(dsn)
    return summarize_schedule_against_neon(events,dates,digest)
