"""Explicit calendar-range acquisition and actual isolated research import.

Every date gets an acquisition state. Nonaccepted tables are unresolved,
never assumed holidays, zero prices or proof that fund history is complete.
No unattended scheduler, customer DB access or commercial approval.
"""
from datetime import date, datetime, timedelta, timezone
from contextlib import closing
from pathlib import Path
from urllib.error import HTTPError, URLError
from hashlib import sha256
import gzip
import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from threading import local, Lock

from .sitca_history_capture import capture_days, make_public_fetcher, CaptureError, MAX_BYTES


def import_range(start, end, root, *, fetcher=None, max_dates=None,
                 minimum_rows=1000, retry_failures=False, progress=None,
                 delay_after_fetch=True):
    if (type(start) is not date or type(end) is not date or start>end or
            (end-start).days>2000 or end>date.today()):
        raise ValueError('HISTORY_CALENDAR_RANGE_INVALID')
    if max_dates is not None and (type(max_dates) is not int or max_dates<1):
        raise ValueError('HISTORY_DATE_BUDGET_INVALID')
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    fetcher=fetcher or make_public_fetcher()
    db=sqlite3.connect(root/'research.sqlite',timeout=30)
    try:
        db.execute('''CREATE TABLE IF NOT EXISTS acquisition_dates (
            requested_date TEXT PRIMARY KEY, status TEXT NOT NULL,
            error_code TEXT, raw_sha256 TEXT, attempts INTEGER NOT NULL,
            checked_at_utc TEXT NOT NULL)''')
        db.commit()
        current=start;executed=0;transport_failures=0;paused=None
        while current<=end:
            day=current.isoformat()
            db.close()
            db=sqlite3.connect(root/'research.sqlite',timeout=30)
            prior=db.execute('SELECT status,attempts FROM acquisition_dates WHERE requested_date=?',(day,)).fetchone()
            if prior and prior[0]=='IMPORTED_UNAPPROVED_RESEARCH':
                if not ((root/(day+'.html')).exists() or (root/(day+'.html.gz')).exists()):
                    raise CaptureError('IMPORTED_SOURCE_ARCHIVE_MISSING')
                current+=timedelta(days=1);continue
            if prior and not retry_failures:
                current+=timedelta(days=1);continue
            if (root/'pause-requested').exists():paused='OPERATOR_PAUSE';break
            if max_dates is not None and executed>=max_dates:paused='DATE_BUDGET_REACHED';break
            received=[]
            def tracked_fetcher(requested):
                if received:return received[-1]
                raw=fetcher(requested)
                if not isinstance(raw,bytes) or len(raw)>MAX_BYTES:
                    raise CaptureError('CAPTURE_RESPONSE_TOO_LARGE')
                received.append(raw)
                return raw
            try:
                for storage_attempt in range(3):
                    try:
                        result=capture_days([current],root,fetcher=tracked_fetcher,minimum_rows=minimum_rows)
                        break
                    except sqlite3.OperationalError as exc:
                        # Reopen only the local transaction, keeping the exact
                        # received source bytes. Never consume a prefetch twice.
                        retryable=any(x in str(exc).lower() for x in ('readonly','read-only','locked','busy'))
                        if not retryable or storage_attempt==2:raise
                        time.sleep(0.2)
                digest=result['snapshots'][0]['raw_sha256']
                state='IMPORTED_UNAPPROVED_RESEARCH';code=None
                transport_failures=0
                plain=root/(day+'.html')
                if plain.exists():
                    compressed=plain.with_suffix('.html.gz')
                    temporary=compressed.with_suffix('.part')
                    with gzip.open(temporary,'wb',compresslevel=6) as output:output.write(plain.read_bytes())
                    temporary.replace(compressed);plain.unlink()
            except HTTPError as exc:
                digest=None;state='SOURCE_TRANSPORT_FAILED';code='HTTP_'+str(exc.code)
                transport_failures+=1
                if exc.code in (401,403,429):paused=code
            except (URLError,TimeoutError,OSError):
                digest=None;state='SOURCE_TRANSPORT_FAILED';code='SOURCE_NETWORK_UNAVAILABLE'
                transport_failures+=1
            except CaptureError as exc:
                digest=sha256(received[-1]).hexdigest() if received else None
                state='SOURCE_TABLE_REQUIRES_REVIEW';code=str(exc)
                transport_failures=0
                if received:
                    rejected=root/'rejected';rejected.mkdir(exist_ok=True)
                    with gzip.open(rejected/(day+'.html.gz'),'wb') as output:output.write(received[-1])
            checked=datetime.now(timezone.utc).isoformat()
            db.close()
            db=sqlite3.connect(root/'research.sqlite',timeout=30)
            with db:
                db.execute('INSERT OR REPLACE INTO acquisition_dates VALUES (?,?,?,?,?,?)',
                           (day,state,code,digest,(prior[1] if prior else 0)+1,checked))
            executed+=1
            report=range_report(db,start,end,executed,paused)
            _write_report(root,report)
            if progress:progress(report)
            if transport_failures>=3:paused='CONSECUTIVE_SOURCE_TRANSPORT_FAILURES'
            if paused:break
            current+=timedelta(days=1)
            if received and delay_after_fetch:time.sleep(0.5)
        report=range_report(db,start,end,executed,paused)
        _write_report(root,report)
        return report
    finally:
        db.close()


def import_range_parallel(start, end, root, *, workers=4, max_dates=None,
                          retry_failures=False, minimum_rows=1000, progress=None,
                          fetcher_factory=None):
    """Bounded public-source prefetch; imports/ledger writes remain serial.

    At most 8 responses are in flight, and global new requests are spaced by
    at least one second. Each thread uses its own anonymous public session.
    No retries after access denial/rate limiting; pending fetches are bounded.
    """
    if type(workers) is not int or not 1<=workers<=8:
        raise ValueError('HISTORY_FETCH_CONCURRENCY_INVALID')
    if type(start) is not date or type(end) is not date or start>end or (end-start).days>2000:
        raise ValueError('HISTORY_CALENDAR_RANGE_INVALID')
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    done=set()
    with closing(sqlite3.connect(root/'research.sqlite',timeout=30)) as db:
        exists=db.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='acquisition_dates'").fetchone()[0]
        if exists:
            done={d for d,status in db.execute('SELECT requested_date,status FROM acquisition_dates')
                  if status=='IMPORTED_UNAPPROVED_RESEARCH' or not retry_failures}
    days=[];current=start
    while current<=end:
        if (current.isoformat() not in done and
                not (root/(current.isoformat()+'.html')).exists() and
                not (root/(current.isoformat()+'.html.gz')).exists()):days.append(current)
        current+=timedelta(days=1)
    if max_dates is not None:days=days[:max_dates]
    tls=local();pace=Lock();last=[0.0]
    def fetch(day):
        if not hasattr(tls,'fetcher'):tls.fetcher=(fetcher_factory or make_public_fetcher)()
        with pace:
            remaining=1.0-(time.monotonic()-last[0])
            if remaining>0:time.sleep(remaining)
            last[0]=time.monotonic()
        return tls.fetcher(day)
    pending={}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for index in range(min(workers,len(days))):pending[days[index]]=pool.submit(fetch,days[index])
        next_index=min(workers,len(days))
        def ordered_fetcher(day):
            nonlocal next_index
            if day not in pending:raise CaptureError('HISTORY_PREFETCH_DATE_NOT_PLANNED')
            future=pending.pop(day)
            result=future.result()
            if next_index<len(days) and not (root/'pause-requested').exists():
                scheduled=days[next_index];next_index+=1
                pending[scheduled]=pool.submit(fetch,scheduled)
            return result
        try:
            return import_range(start,end,root,fetcher=ordered_fetcher,max_dates=max_dates,
                                retry_failures=retry_failures,minimum_rows=minimum_rows,progress=progress,
                                delay_after_fetch=False)
        finally:
            for future in pending.values():future.cancel()


def range_report(db,start,end,executed,paused):
    bounds=(start.isoformat(),end.isoformat())
    counts=dict(db.execute('SELECT status,count(*) FROM acquisition_dates WHERE requested_date BETWEEN ? AND ? GROUP BY status',bounds))
    errors=dict(db.execute('SELECT error_code,count(*) FROM acquisition_dates WHERE requested_date BETWEEN ? AND ? AND error_code IS NOT NULL GROUP BY error_code',bounds))
    has_sources=db.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='source_snapshots'").fetchone()[0]
    imported=0
    dates=[]
    if has_sources:
        # Each snapshot and all its rows commit in one transaction. Sum their
        # committed row counts instead of rescanning millions of price rows
        # after every date. Final export separately verifies the actual count.
        imported=db.execute("SELECT coalesce(sum(json_extract(metadata_json,'$.numeric_nav_observations')),0) FROM source_snapshots WHERE requested_date BETWEEN ? AND ?",bounds).fetchone()[0]
        dates=[r[0] for r in db.execute('SELECT DISTINCT requested_date FROM source_snapshots WHERE requested_date BETWEEN ? AND ? ORDER BY requested_date',bounds)]
    total=(end-start).days+1
    return {'schema':'fiveline_continuous_research_nav_import_v1','requested_start':bounds[0],
            'requested_end':bounds[1],'calendar_dates_in_scope':total,
            'attempted_calendar_dates':sum(counts.values()),'unattempted_calendar_dates':total-sum(counts.values()),
            'acquisition_status_counts':counts,'source_error_counts':errors,
            'executed_dates_this_invocation':executed,'paused_reason':paused,
            'research_dates_in_database':len(dates),'first_research_date':dates[0] if dates else None,
            'last_research_date':dates[-1] if dates else None,'research_nav_rows_persisted':imported,
            'every_calendar_date_attempted':sum(counts.values())==total,
            'official_three_year_complete_funds':0,'independent_valuation_calendar_verified':False,
            'class_isin_mapping_approved':False,'commercial_data_rights_verified':False,
            'portfolio_or_six_strategies_modified':False,'production_database_modified':False}


def _write_report(root,report):
    temporary=root/'continuous-report.part'
    temporary.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    temporary.replace(root/'continuous-report.json')


def _cli():
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--start',required=True);p.add_argument('--end',required=True)
    p.add_argument('--root',required=True);p.add_argument('--max-dates',type=int)
    p.add_argument('--retry-failures',action='store_true')
    p.add_argument('--fetch-workers',type=int,default=1)
    args=p.parse_args()
    def progress(r):
        print(json.dumps({k:r[k] for k in ('attempted_calendar_dates','research_dates_in_database','research_nav_rows_persisted','paused_reason')}) ,flush=True)
    runner=import_range if args.fetch_workers==1 else import_range_parallel
    extra={} if args.fetch_workers==1 else {'workers':args.fetch_workers}
    result=runner(date.fromisoformat(args.start),date.fromisoformat(args.end),args.root,
                  max_dates=args.max_dates,retry_failures=args.retry_failures,progress=progress,**extra)
    print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)
    return 0 if (result['every_calendar_date_attempted'] and not result['paused_reason'] and
                 set(result['acquisition_status_counts'])=={'IMPORTED_UNAPPROVED_RESEARCH'}) else 2


if __name__=='__main__':raise SystemExit(_cli())
