"""Read-only Neon coverage gate: official-calendar evidence required.

Only day identifiers and counts are queried; no open/high/low/close or volume is
read or emitted. Missing days cannot be certified from weekdays or from the
first/last observed quote. Source URLs attest origin, not commercial license.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta, datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
import argparse
import csv
import json

HOSTS = {
    "TWSE": {"www.twse.com.tw", "twse.com.tw"},
    "TPEX": {"www.tpex.org.tw", "tpex.org.tw"},
}
SCHEMA = "fiveline_neon_calendar_coverage_gate_v1"


def _date(x):
    s = x.isoformat() if isinstance(x, date) else str(x)
    if date.fromisoformat(s).isoformat() != s:
        raise ValueError("day must be YYYY-MM-DD")
    return s


def _official(market, url):
    if market not in HOSTS:
        raise ValueError("unsupported exchange")
    p = urlsplit(url)
    if p.scheme != "https" or p.hostname not in HOSTS[market] or p.username or p.password:
        raise ValueError("source must be attributed to correct official HTTPS exchange")
    return True


def load_csv(path, columns):
    if not path:
        return []
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not set(columns).issubset(set(reader.fieldnames or ())):
            raise ValueError("official evidence CSV required headers missing")
        return [dict(row) for row in reader]


def load_evidence(calendar_csv=None, windows_csv=None, exceptions_csv=None):
    calendar, windows, exceptions = {}, {}, {}
    for row in load_csv(calendar_csv, ("market","date","is_open","evidence_url")):
        m = row["market"].strip().upper()
        d = _date(row["date"].strip())
        _official(m, row["evidence_url"].strip())
        status = row["is_open"].strip().lower()
        if status not in ("true","false"):
            raise ValueError("invalid exchange open flag")
        key = (m,d)
        if key in calendar:
            raise ValueError("duplicate exchange calendar session")
        calendar[key] = status == "true"
    for row in load_csv(windows_csv, ("market","symbol","start_date","end_date","evidence_url")):
        m = row["market"].strip().upper()
        sym = row["symbol"].strip()
        first,last = _date(row["start_date"].strip()),_date(row["end_date"].strip())
        _official(m,row["evidence_url"].strip())
        if not sym or first > last:
            raise ValueError("invalid listing window")
        key = (m,sym)
        if key in windows:
            raise ValueError("duplicate listing window")
        windows[key] = (first,last)
    for row in load_csv(exceptions_csv, ("market","symbol","date","reason","evidence_url")):
        m = row["market"].strip().upper()
        sym = row["symbol"].strip()
        d = _date(row["date"].strip())
        _official(m,row["evidence_url"].strip())
        reason = row["reason"].strip()
        if reason not in ("suspended","trading_halt","no_trade") or not sym:
            raise ValueError("invalid no-trade reason")
        key = (m,sym,d)
        if key in exceptions:
            raise ValueError("duplicate no-trade exception")
        exceptions[key] = reason
    return calendar,windows,exceptions


def audit_dates(records, calendar=None, windows=None, exceptions=None, now=None):
    """Input is (market,code,day) from private approved_bars, never quotes."""
    calendar,windows,exceptions = calendar or {}, windows or {}, exceptions or {}
    observed = defaultdict(set)
    market_days = defaultdict(set)
    for market,code,rawday in records:
        day = _date(rawday)
        if market not in HOSTS or not code:
            raise ValueError("invalid market/code in Neon date-only result")
        key = (market,str(code))
        if day in observed[key]:
            raise ValueError("duplicate approved instrument/date in Neon")
        observed[key].add(day)
        market_days[market].add(day)

    result=[]
    for market,code in sorted(set(observed)|set(windows)):
        days = observed[(market,code)]
        candidate = sorted(d for d in market_days[market]
                           if days and min(days) < d < max(days) and d not in days)
        window = windows.get((market,code))
        gaps=0
        excused=0
        unknown=0
        outside=0
        reasons=[]
        if window is None:
            reasons.append("MISSING_LISTING_WINDOW_EVIDENCE")
        else:
            start,end = map(date.fromisoformat,window)
            length=(end-start).days+1
            if length>365*40:
                raise ValueError("listing window greater than bounded 40 years")
            for i in range(length):
                d = (start+timedelta(days=i)).isoformat()
                session = calendar.get((market,d))
                if session is None:
                    unknown+=1
                elif session and d not in days:
                    if (market,code,d) in exceptions:
                        excused+=1
                    else:
                        gaps+=1
            outside = sum(d < window[0] or d > window[1] or
                          calendar.get((market,d)) is False for d in days)
            if unknown: reasons.append("UNATTESTED_CALENDAR_DATES")
            if gaps: reasons.append("MISSING_OFFICIAL_SESSIONS")
            if outside: reasons.append("PRICE_DATE_OUTSIDE_OFFICIAL_WINDOW")
            if any((market,code,d) in exceptions and d in days for d in days):
                reasons.append("EXCEPTION_CONFLICTS_WITH_PRICE")
        result.append({
            "market":market, "symbol":code,"approved_raw_days":len(days),
            "first_day":min(days) if days else None, "last_day":max(days) if days else None,
            "peer_observed_gap_candidates":len(candidate),
            "candidate_is_officially_confirmed":False,
            "missing_official_sessions":gaps,"excused_no_trade_sessions":excused,
            "calendar_days_not_attested":unknown,"unexpected_quote_sessions":outside,
            "listing_window_supplied":window is not None,
            "provisional_coverage_passed":window is not None and not reasons,
            "issues":reasons,
        })

    checked = now or datetime.now(timezone.utc)
    if checked.tzinfo is None:
        raise ValueError("timezone-aware audit timestamp required")
    return {
        "schema":SCHEMA,"checked_at_utc":checked.astimezone(timezone.utc).isoformat(),
        "status":"PROVISIONAL_RESEARCH_ONLY","symbols":len(result),
        "approved_raw_days":sum(x["approved_raw_days"] for x in result),
        "peer_observed_gap_candidates":sum(x["peer_observed_gap_candidates"] for x in result),
        "official_session_gaps":sum(x["missing_official_sessions"] for x in result),
        "unattested_calendar_days":sum(x["calendar_days_not_attested"] for x in result),
        "provisional_coverage_passed": bool(result and all(x["provisional_coverage_passed"] for x in result)),
        "official_calendar_completeness_reviewed":False,
        "full_market_history_verified":False,
        "licensed_raw_price_redistribution":False,
        "corporate_action_adjustments_applied":False,
        "production_backtests_modified":False,
        "database_mutated":False,
        "instruments":result,
    }


def neon_read_only_dates(dsn):
    if not dsn:
        raise RuntimeError("Neon credentials not configured")
    import psycopg
    with psycopg.connect(dsn,connect_timeout=15,
                         application_name="fiveline_history_coverage_readonly") as conn:
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute("""SELECT market,code,day FROM history_stage.approved_bars
                           ORDER BY market,code,day""")
            return cur.fetchall()


def audit_neon(dsn,calendar_csv=None,windows_csv=None,exceptions_csv=None):
    calendar,windows,exceptions=load_evidence(calendar_csv,windows_csv,exceptions_csv)
    return audit_dates(neon_read_only_dates(dsn),calendar,windows,exceptions)


def main():
    p=argparse.ArgumentParser(description="Read-only Neon trade-session coverage study")
    p.add_argument("--calendar",type=Path)
    p.add_argument("--windows",type=Path)
    p.add_argument("--exceptions",type=Path)
    args=p.parse_args()
    from storage_config import configured_dsn
    r=audit_neon(configured_dsn(),args.calendar,args.windows,args.exceptions)
    # Private stdout: never OHLC, volume, credentials or API source bodies.
    print(json.dumps(r,ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
