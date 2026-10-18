"""Research-only 2026 TPEx *stock* holiday schedule PDF cross-check.

TPEx publishes a single official PDF with distinct stock, bond and international
bond spring-festival closing rules. This probe explicitly checks the STOCK
schedule and fails closed on an altered/unreadable PDF. It NEVER reads OHLCV,
writes to Neon, certifies actual daily trading, or republishes prices.
"""
from __future__ import annotations

from datetime import date, timedelta, datetime, timezone
from hashlib import sha256
from io import BytesIO
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import re
import ssl
import unicodedata

if __package__:
    from .calendar_coverage import neon_read_only_dates
else:
    from calendar_coverage import neon_read_only_dates

SOURCE_URL = "https://www.tpex.org.tw/storage/eb_data/11411/114007545811-1.pdf"
SOURCE_HOSTS = {"www.tpex.org.tw", "tpex.org.tw", "dsp.tpex.org.tw"}
SCHEMA = "fiveline_tpex_115_official_stock_calendar_probe_v1"
MAX_BYTES = 3_000_000
MAX_PAGES = 8
YEAR = 2026
PILOT_FIRST, PILOT_LAST = "2026-10-06", "2026-10-13"
EXPANDED_FIRST, EXPANDED_LAST = "2026-10-01", "2026-10-13"
PILOT_CODES = ("6488", "8069")
# These four stock market holidays are independently listed in the source
# annual notice; they do not attest ad hoc emergency closures.
SPECIAL_CLOSED = {
    "2026-09-25": ("中秋節", "9月25日"),
    "2026-09-28": ("教師節", "9月28日"),
    "2026-10-09": ("國慶日", "10月9日"),
    "2026-10-26": ("光復", "10月26日"),
}


def normalize_pdf_text(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or ""))


def validate_pdf_text(text: str):
    """Return annual stock-only holiday markers, with conservative gates."""
    t = normalize_pdf_text(text)
    if not all(s in t for s in ("中華民國115年", "櫃檯買賣市場", "開(休)市日期表")):
        raise ValueError("TPEx document year/type/header changed")
    if not all(s in t for s in (
        "農曆春節前股票交易系統最後交易日",
        "農曆春節前國際債券交易系統最後交易日",
        "農曆春節前債券處所議價最後交易日",
    )):
        raise ValueError("TPEx 115 stock-versus-bond schedule markers missing")
    # Stock closing: last stock trading Feb 11; Feb 12/13 settlement-only.
    if "2月11日" not in t or "2月12日及2月13日市場無交易" not in t:
        raise ValueError("TPEx 115 stock spring-festival trading rules changed")
    for day, (name, label) in SPECIAL_CLOSED.items():
        # The named holiday must occur close to the corresponding date in
        # PDF-extracted text, not merely somewhere in the whole document.
        compact_name = normalize_pdf_text(name)
        compact_day = normalize_pdf_text(label)
        name_pos = [m.start() for m in re.finditer(re.escape(compact_name), t)]
        day_pos = [m.start() for m in re.finditer(re.escape(compact_day), t)]
        if not any(abs(a-b)<=140 for a in name_pos for b in day_pos):
            raise ValueError("Official TPEx holiday label/date pairing not verified: "+day)
    return set(SPECIAL_CLOSED)


def extract_official_pdf(data: bytes) -> str:
    if not isinstance(data,bytes) or not data.startswith(b"%PDF-") or len(data)>MAX_BYTES:
        raise ValueError("Official TPEx PDF missing/invalid/oversize")
    from pypdf import PdfReader
    pdf = PdfReader(BytesIO(data),strict=True)
    if not (1 <= len(pdf.pages) <= MAX_PAGES) or pdf.is_encrypted:
        raise ValueError("Official TPEx PDF page count/encryption unexpected")
    parts=[page.extract_text(extraction_mode="plain") or "" for page in pdf.pages]
    text="\n".join(parts)
    if len(text.strip())<150:
        raise ValueError("Official TPEx document not text-readable; manual review required")
    return text


def fetch_official_pdf():
    """At most one official HTTPS request (no guessing of alternate endpoints)."""
    req=Request(SOURCE_URL,headers={
        "User-Agent":"Mozilla/5.0 (compatible; FivelineResearchCalendar/1.0)",
        "Accept":"application/pdf,*/*","Accept-Encoding":"identity",
        "Connection":"close",
    })
    with urlopen(req,timeout=28,context=ssl.create_default_context()) as response:
        final=urlsplit(response.geturl())
        if final.scheme!="https" or final.hostname not in SOURCE_HOSTS:
            raise ValueError("Unexpected TPEx PDF redirect")
        if response.status!=200:
            raise ValueError("TPEx official PDF did not return HTTP 200")
        data=response.read(MAX_BYTES+1)
    if len(data)>MAX_BYTES:
        raise ValueError("TPEx official PDF too large")
    return data,sha256(data).hexdigest()


def expected_stock_sessions(first: str, last: str, closures: set[str]):
    begin, end = date.fromisoformat(first),date.fromisoformat(last)
    if begin.year!=YEAR or end.year!=YEAR or begin>end or (end-begin).days>45:
        raise ValueError("TPEx research window invalid or over 46 days")
    return [d.isoformat() for i in range((end-begin).days+1)
            if (d:=begin+timedelta(days=i)).weekday()<5 and d.isoformat() not in closures]


def compare_to_neon(stock_closures, rows, digest, now=None):
    pilot=set(expected_stock_sessions(PILOT_FIRST,PILOT_LAST,stock_closures))
    expanded=set(expected_stock_sessions(EXPANDED_FIRST,EXPANDED_LAST,stock_closures))
    known={code:set() for code in PILOT_CODES}
    for market,code,day in rows:
        day=day.isoformat() if isinstance(day,date) else str(day)
        date.fromisoformat(day)
        if market=="TPEX" and str(code) in known:
            if day in known[str(code)]:
                raise ValueError("Duplicated approved TPEx symbol-date")
            known[str(code)].add(day)
    symbols=[]
    for code in PILOT_CODES:
        observed=known[code]
        missing=sorted(pilot-observed)
        unexpected=sorted(d for d in observed if PILOT_FIRST<=d<=PILOT_LAST and d not in pilot)
        expanded_candidates=sorted(expanded-observed)
        symbols.append({
            "market":"TPEX","code":code,
            "pilot_window":[PILOT_FIRST,PILOT_LAST],
            "pilot_planned_sessions":len(pilot),
            "pilot_approved_quote_days":len([d for d in observed if PILOT_FIRST<=d<=PILOT_LAST]),
            "pilot_missing_planned_session_count":len(missing),
            "pilot_missing_dates_sample":missing[:12],
            "pilot_quote_on_announced_closed_day_count":len(unexpected),
            "expanded_window":[EXPANDED_FIRST,EXPANDED_LAST],
            "expanded_planned_sessions":len(expanded),
            "expanded_missing_date_candidates":expanded_candidates[:12],
            "expanded_missing_date_candidate_count":len(expanded_candidates),
            "listing_history_verified":False,
            "per_stock_halts_verified":False,
        })
    t=now or datetime.now(timezone.utc)
    if t.tzinfo is None:raise ValueError("Timezone-aware check required")
    return {
        "schema":SCHEMA,"checked_at_utc":t.astimezone(timezone.utc).isoformat(),
        "source_url":SOURCE_URL,"source_pdf_sha256":digest,
        "market_scope":"TPEX_OTC_STOCKS_NOT_BONDS",
        "annual_stock_holiday_markers_validated":len(stock_closures),
        "pilot_expected_sessions":len(pilot),"expanded_expected_sessions":len(expanded),
        "instrument_count":len(symbols),
        "pilot_missing_planned_quote_days":sum(x["pilot_missing_planned_session_count"] for x in symbols),
        "expanded_unverified_gap_candidates":sum(x["expanded_missing_date_candidate_count"] for x in symbols),
        "official_announced_closed_session_quote_count":sum(x["pilot_quote_on_announced_closed_day_count"] for x in symbols),
        "official_stock_market_pdf_text_verified":True,
        "actual_daily_trading_calendar_independently_verified":False,
        "instrument_listing_window_verified":False,
        "complete_history_verified":False,
        "corporate_actions_adjusted":False,
        "public_market_quotes_distributed":False,
        "database_mutated":False,
        "import_executed":False,
        "instruments":symbols,
    }


def run_probe(dsn, fetcher=None, date_reader=None):
    if not dsn:
        raise RuntimeError("Neon storage not connected: no remote data requested")
    data,digest=(fetcher or fetch_official_pdf)()
    text=extract_official_pdf(data)
    closures=validate_pdf_text(text)
    records=(date_reader or neon_read_only_dates)(dsn)
    return compare_to_neon(closures,records,digest)
