"""Fail-closed, quote-free historical OHLCV coverage audit for the private pilot.

The input SQLite contains unadjusted official daily prices, but no prices are
written to this public-safe JSON summary. Calendar and listing-window evidence
are explicit, never inferred from ordinary weekdays or first/last observed bars.
This does not approve bulk redistribution, corporate-action adjustments, or use
in production five-line / SD / back-test calculations.
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import sqlite3
from urllib.parse import urlparse

SCHEMA = "fiveline_ohlcv_gap_audit_v1"
OFFICIAL_HOSTS = {"TWSE": {"twse.com.tw", "www.twse.com.tw"},
                  "TPEX": {"tpex.org.tw", "www.tpex.org.tw"}}
MAX_EXPORTED_DATES = 20


def iso(value: object) -> str:
    s = str(value)
    if date.fromisoformat(s).isoformat() != s:
        raise ValueError(f"date must be ISO YYYY-MM-DD: {s!r}")
    return s


def official_evidence(market: str, url: str) -> bool:
    try:
        p = urlparse(url)
        return p.scheme == "https" and p.hostname in OFFICIAL_HOSTS[market] and not (p.username or p.password)
    except (KeyError, ValueError):
        return False


def read_csv(path: Path, fields: set[str]) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not fields.issubset(reader.fieldnames or []):
            raise ValueError(f"CSV {path.name} missing columns: {sorted(fields - set(reader.fieldnames or []))}")
        rows = list(reader)
    return rows


def read_calendar(path: Path | None):
    """Each calendar row must be source-attributed. Completeness requires review."""
    if path is None:
        return {}, defaultdict(set), defaultdict(set), []
    data = read_csv(path, {"market", "date", "is_open", "evidence_url"})
    days: dict[tuple[str, str], bool] = {}
    seen = defaultdict(set)
    market_open = defaultdict(set)
    issues = []
    for r in data:
        m, d = r["market"].upper().strip(), iso(r["date"].strip())
        if m not in OFFICIAL_HOSTS or r["is_open"].lower().strip() not in {"true", "false"}:
            raise ValueError("unknown market or is_open (must be true/false)")
        if not official_evidence(m, r["evidence_url"].strip()):
            raise ValueError(f"unverified exchange calendar attribution: {m} {d}")
        key = (m, d)
        if key in days:
            raise ValueError(f"duplicate calendar market/date: {m} {d}")
        opened = r["is_open"].lower().strip() == "true"
        days[key] = opened
        seen[m].add(d)
        if opened:
            market_open[m].add(d)
    if not days:
        issues.append("empty_calendar")
    return days, seen, market_open, issues


def read_windows(path: Path | None):
    if path is None:
        return {}
    rows = read_csv(path, {"market", "symbol", "start_date", "end_date", "evidence_url"})
    result = {}
    for r in rows:
        m, symbol = r["market"].upper().strip(), r["symbol"].strip()
        first, last = iso(r["start_date"].strip()), iso(r["end_date"].strip())
        if first > last or not symbol or not official_evidence(m, r["evidence_url"].strip()):
            raise ValueError("invalid or unattributed instrument date window")
        key = (m, symbol)
        if key in result:
            raise ValueError(f"duplicate instrument window: {key}")
        result[key] = (first, last)
    return result


def read_exceptions(path: Path | None):
    if path is None:
        return {}
    rows = read_csv(path, {"market", "symbol", "date", "reason", "evidence_url"})
    result = {}
    for r in rows:
        m, symbol, d = r["market"].upper().strip(), r["symbol"].strip(), iso(r["date"].strip())
        reason = r["reason"].strip()
        if reason not in {"suspended", "trading_halt", "no_trade"} or not official_evidence(m, r["evidence_url"].strip()):
            raise ValueError(f"unverified exception: {m} {symbol} {d}")
        key = (m, symbol, d)
        if key in result:
            raise ValueError(f"duplicate exception: {key}")
        result[key] = reason
    return result


def _decimal(value, field):
    try:
        d = Decimal(str(value))
        if not d.is_finite():
            raise ValueError()
        return d
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f"invalid numeric {field}") from None


def quote_rows(db_path: Path):
    """Reject malformed/duplicate bars; the caller must NOT publish quote values."""
    with sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True) as conn:
        result = conn.execute("SELECT market,symbol,trade_date,open,high,low,close,"
                              "volume_shares,turnover_twd,price_adjustment "
                              "FROM quote_raw ORDER BY market,symbol,trade_date").fetchall()
    records = defaultdict(set)
    for m, code, day, o, h, l, c, vol, amt, adjustment in result:
        d = iso(day)
        if m not in OFFICIAL_HOSTS or not code or adjustment != "RAW_NOT_ADJUSTED":
            raise ValueError(f"unexpected market/symbol/adjustment for {m}:{code}")
        nums = [_decimal(v, fld) for v, fld in zip((o, h, l, c, vol), ("open","high","low","close","volume"))]
        opening, high, low, closing, volume = nums
        if min(opening, high, low, closing) <= 0 or volume < 0 or high < max(opening, closing, low) or low > min(opening, closing, high):
            raise ValueError(f"invalid OHLCV for {m}:{code} on {d}")
        if amt is not None and _decimal(amt, "turnover") < 0:
            raise ValueError(f"negative turnover for {m}:{code}")
        key = (m, str(code))
        if d in records[key]:
            raise ValueError(f"duplicate OHLCV date {m}:{code}:{d}")
        records[key].add(d)
    return records


def _audit(market, symbol, observed, calendar, market_seen, market_open, window, exceptions):
    issues = []
    missing = []
    excused = []
    unexpected = []
    if not window:
        issues.append("missing_official_instrument_window")
    if not market_seen[market]:
        issues.append("missing_official_trading_calendar")
    if window and calendar:
        first, last = window
        # Calendar must enumerate all dates, open and closed, in the audited
        # interval; omitted dates are UNKNOWN, not inferred holidays.
        expected_dates = {date.fromordinal(n).isoformat() for n in range(
            date.fromisoformat(first).toordinal(), date.fromisoformat(last).toordinal() + 1)}
        unverified = expected_dates - market_seen[market]
        if unverified:
            issues.append("calendar_interval_not_fully_attested")
        for d in sorted(market_open[market]):
            if first <= d <= last and d not in observed:
                key = (market, symbol, d)
                if key in exceptions:
                    excused.append(d)
                else:
                    missing.append(d)
        for d in sorted(observed):
            if not first <= d <= last:
                unexpected.append(d)
            elif (market, d) in calendar and not calendar[(market, d)]:
                unexpected.append(d)
        if unexpected:
            issues.append("price_bar_outside_active_session")
        if missing:
            issues.append("missing_session_quotes")
        if any((market, symbol, d) in exceptions and d in observed for d in market_open[market]):
            issues.append("exception_has_existing_quote")
    else:
        unverified = set()
    return {
        "market": market, "code": symbol, "observed_quote_days": len(observed),
        "earliest_observed": min(observed) if observed else None,
        "latest_observed": max(observed) if observed else None,
        "official_instrument_window_supplied": bool(window),
        "official_calendar_supplied": bool(calendar and market_seen[market]),
        "missing_session_count": len(missing), "missing_session_dates_sample": missing[:MAX_EXPORTED_DATES],
        "attested_nontrading_exception_count": len(excused),
        "calendar_unverified_date_count": len(unverified),
        "unexpected_quote_count": len(unexpected),
        "issues": issues,
        "calendar_continuity_passed": bool(window and calendar and not issues),
        "adjusted_series_ready": False,
    }


def make_report(db_path: Path, calendar_path: Path | None = None,
                windows_path: Path | None = None, exceptions_path: Path | None = None,
                now: datetime | None = None):
    observed = quote_rows(db_path)
    calendar, seen, opened, issues = read_calendar(calendar_path)
    windows = read_windows(windows_path)
    exceptions = read_exceptions(exceptions_path)
    instruments = []
    keys = sorted(set(observed) | set(windows))
    for m, symbol in keys:
        instrument = _audit(m, symbol, observed.get((m, symbol), set()), calendar,
                            seen, opened, windows.get((m, symbol)), exceptions)
        instruments.append(instrument)
    checked = now or datetime.now(timezone.utc)
    if checked.tzinfo is None:
        raise ValueError("checked time must be timezone-aware")
    complete = bool(instruments and all(x["calendar_continuity_passed"] for x in instruments))
    # Attested calendars still need independent official review and licensing.
    return {
        "schema": SCHEMA, "checked_at_utc": checked.astimezone(timezone.utc).isoformat(),
        "instrument_count": len(instruments),
        "quote_days_count": sum(len(s) for s in observed.values()),
        "calendar_supplied": bool(calendar), "window_count": len(windows),
        "calendar_provisional_continuity_passed": complete,
        "official_calendar_completeness_reviewed": False,
        "production_coverage_complete": False,
        "price_adjustments_applied": False,
        "dividend_adjustments_applied": False,
        "raw_quotes_publicly_redistributed": False,
        "production_analyses_modified": False,
        "original_excel_modified": False,
        "requires_manual_license_and_corporate_action_review": True,
        "audit_issues": issues,
        "instruments": instruments,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--sqlite", required=True, type=Path, help="private, unadjusted quote_raw SQLite file")
    p.add_argument("--calendar", type=Path, help="explicit official session and holiday CSV")
    p.add_argument("--windows", type=Path, help="official listing/validation window CSV")
    p.add_argument("--exceptions", type=Path, help="attributed suspension/no-trade CSV")
    p.add_argument("--report", required=True, type=Path, help="publishable, quote-free JSON path")
    args = p.parse_args()
    report = make_report(args.sqlite, args.calendar, args.windows, args.exceptions)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Audited {report['instrument_count']} instruments, {report['quote_days_count']} private raw bars; price-free summary: {args.report}")
    print("No calendar proof is inferred from weekdays; production coverage and adjustments remain NOT approved.")


if __name__ == "__main__":
    main()
