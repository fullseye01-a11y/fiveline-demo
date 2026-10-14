"""Plan bounded TWSE monthly and TPEx daily backfill tasks, without fetching.

Only explicit, attributed exchange calendars and listing windows are eligible;
unknown calendar days block planning rather than being guessed as holidays.
No OHLCV appears in the returned plan. It never updates production prices.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date, timedelta, datetime, timezone
import json
from pathlib import Path

from tools.official_history_gap_audit import (
    read_calendar, read_windows, read_exceptions, quote_rows,
)

SCHEMA = 'fiveline_official_backfill_plan_v1'


def plan(sqlite_path: Path, calendar_path: Path, windows_path: Path,
         exceptions_path: Path | None = None, limit: int = 20,
         now: datetime | None = None) -> dict:
    if not 1 <= limit <= 100:
        raise ValueError('limit must be between 1 and 100 to cap requests')
    quotes = quote_rows(sqlite_path)
    calendar, seen, opened, _ = read_calendar(calendar_path)
    windows = read_windows(windows_path)
    exceptions = read_exceptions(exceptions_path)
    if not calendar or not windows:
        raise ValueError('must supply official calendar AND listing windows')
    groups = defaultdict(set)
    blocked = []
    missing_quotes = 0
    for (market, symbol), (first, last) in sorted(windows.items()):
        # Every civil date in scope requires explicit market-open/closed
        # evidence, preventing a partial official holiday CSV from certifying
        # a historical interval it does not cover.
        start, end = date.fromisoformat(first), date.fromisoformat(last)
        n_days = (end - start).days + 1
        if n_days > 365 * 40:
            raise ValueError('40-year per-instrument limit; split the window')
        dates = {(start + timedelta(days=i)).isoformat() for i in range(n_days)}
        if not dates.issubset(seen[market]):
            blocked.append({'market':market,'code':symbol,'reason':'calendar_dates_not_attested',
                            'unverified_days':len(dates-seen[market])})
            continue
        observed = quotes.get((market, symbol), set())
        if any(d not in opened[market] for d in observed if first <= d <= last):
            blocked.append({'market':market,'code':symbol,'reason':'quote_on_nontrading_date'})
            continue
        needed = sorted(d for d in opened[market]
                        if first <= d <= last and d not in observed
                        and (market,symbol,d) not in exceptions)
        missing_quotes += len(needed)
        for d in needed:
            if market == 'TWSE':
                key = ('TWSE', symbol, d[:7])  # one official STOCK_DAY month per ticker
                groups[key].add(d)
            else:
                key = ('TPEX', d, '')  # one official dailyQuotes response, multiple symbols
                groups[key].add(symbol)
    # Most recent batches first for near-term research, deterministic replay.
    raw_units = []
    for (market, a, period), data in groups.items():
        if market == 'TWSE':
            unit_id = f'TWSE:{a}:{period}'
            raw_units.append({'unit_id':unit_id, 'market':'TWSE','source':'TWSE_STOCK_DAY',
                              'symbol':a,'month':period,'missing_session_count':len(data)})
        else:
            unit_id = f'TPEX:{a}'
            raw_units.append({'unit_id':unit_id,'market':'TPEX','source':'TPEX_DAILY_QUOTES',
                              'day':a,'symbols':sorted(data),'missing_instrument_days':len(data)})
    raw_units.sort(key=lambda u: (u.get('day',u.get('month','')),u['unit_id']), reverse=True)
    checked = now or datetime.now(timezone.utc)
    if checked.tzinfo is None:
        raise ValueError('timezone required')
    return {
        'schema':SCHEMA,'generated_at_utc':checked.astimezone(timezone.utc).isoformat(),
        'stage':'PLAN_ONLY_NO_NETWORK_NO_WRITES','request_cap':limit,
        'attested_instrument_windows':len(windows), 'blocked_instrument_windows':blocked,
        'missing_raw_quote_instrument_days':missing_quotes,
        'total_work_units':len(raw_units), 'work_units_returned':min(limit,len(raw_units)),
        'work_units':raw_units[:limit],
        'private_quote_values_exported':False,'bulk_source_license_confirmed':False,
        'production_corporate_action_adjustments_confirmed':False,
        'production_backtests_changed':False,
    }


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sqlite',type=Path,required=True)
    parser.add_argument('--calendar',type=Path,required=True)
    parser.add_argument('--windows',type=Path,required=True)
    parser.add_argument('--exceptions',type=Path)
    parser.add_argument('--limit',type=int,default=20)
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    result=plan(args.sqlite,args.calendar,args.windows,args.exceptions,args.limit)
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f'Planned {result["work_units_returned"]}/{result["total_work_units"]} units for {result["missing_raw_quote_instrument_days"]} missing raw sessions; no network or writes')

if __name__=='__main__':
    main()
