"""Bounded date partition recovery for failed public FundClear histories.

Prices retain their original response, request interval and immutable manifest.
Only existing source-review classes with valid frozen identities are eligible.
"""
from __future__ import annotations
import csv
import gzip
import json
import re
import sqlite3
import time
from http.client import HTTPException
from contextlib import closing, contextmanager
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from . import fundclear_history_import as m

PATH = '/api/offshore/nav-profit/query-history'
SCHEMA = 'fundclear_partition_manifest_v1'


class SourceStop(RuntimeError):
    pass


def intervals(start=m.START, end=m.END, days=365):
    if not 1 <= days <= 365 or not m.START <= start <= end <= m.END:
        raise ValueError('INVALID_PARTITION_SCOPE')
    while start <= end:
        stop = min(end, start + timedelta(days=days-1))
        yield start, stop
        start = stop + timedelta(days=1)


def request_body(q, start, end, fund_code='all'):
    return {'organizeCode': q['organize_code'], 'fundCode': fund_code,
            'fundClassCode': q['code'], 'startDate': start.strftime('%Y/%m/%d'),
            'endDate': end.strftime('%Y/%m/%d')}


def read_raw(root, meta):
    if not re.fullmatch(r'raw/[a-f0-9]{64}\.[a-f0-9]{64}\.json\.gz', meta['raw_file']):
        raise ValueError('INVALID_RAW_REFERENCE')
    raw = gzip.decompress((root / meta['raw_file']).read_bytes())
    request_sha = m.sha(m.canonical({'path': meta['path'], 'body': meta['request']}))
    if m.sha(raw) != meta['raw_sha256'] or Path(meta['raw_file']).name != request_sha+'.'+m.sha(raw)+'.json.gz':
        raise ValueError('RAW_FINGERPRINT_MISMATCH')
    return raw


def verify_parent(root, q, fund_code, selector):
    if fund_code=='all' and selector is None: return
    if not selector or selector['path']!='/api/offshore/common-select/fund-class-selection' or selector['http_status']!=200:
        raise ValueError('PARENT_SELECTOR_REQUIRES_REVIEW')
    if selector['request']!={'all':False,'organizeCode':q['organize_code'],'fundCode':fund_code}:
        raise ValueError('PARENT_SELECTOR_REQUEST_MISMATCH')
    rows=json.loads(read_raw(root,selector))
    exact=[row for row in rows if row.get('value')==q['code'] and m.norm(row.get('name',''))==m.norm(q['name'])]
    if len(exact)!=1: raise ValueError('PARENT_CLASS_BINDING_REVIEW')


def parent_binding(root, q):
    history=root/'parent-history-review.json'
    if history.exists():
        matches=[row for row in json.loads(history.read_text()) if row.get('class_key')==q['class_key'] and row.get('code')==q['code']]
        if len(matches)==1 and 'parent_class_selector_source' in matches[0]:
            selector=matches[0]['parent_class_selector_source'];fund_code=selector['request']['fundCode']
            verify_parent(root,q,fund_code,selector)
            return fund_code,selector
    return 'all',None


def acquire(root, q, initial_days=31):
    """Recover up to one class; cache immutable source responses between runs."""
    root = Path(root)
    if not m.valid_isin(q['isin']):
        raise ValueError('IDENTITY_REVIEW')
    folder = root / 'partitions'; folder.mkdir(exist_ok=True)
    fund_code,parent_source=parent_binding(root,q)
    if not 1<=initial_days<=365: raise ValueError('INVALID_PARTITION_SCOPE')
    strategy=m.sha(m.canonical({'fund_code':fund_code,'initial_interval_days':initial_days}))
    work = folder / (q['class_key'] + '.' + strategy + '.work.json')
    identity = {'schema': SCHEMA, 'class_key': q['class_key'], 'code': q['code'],
                'currency': q['currency'], 'isin': q['isin'],
                'basic_source_sha256': q['basic_source_sha256'],'fund_code':fund_code,'parent_selector_source':parent_source,'initial_interval_days':initial_days,
                'start': m.START.isoformat(), 'end': m.END.isoformat()}
    state = json.loads(work.read_text()) if work.exists() else {**identity, 'responses': {}}
    if any(state.get(k) != v for k,v in identity.items()):
        raise ValueError('PARTITION_IDENTITY_OR_SCOPE_CHANGED')
    calls = 0; consecutive_failures = 0

    def save():
        temp = work.with_suffix('.tmp'); temp.write_text(json.dumps(state,ensure_ascii=False,indent=2)); temp.replace(work)

    def query(start, end):
        nonlocal calls, consecutive_failures
        if (root/'pause-requested').exists(): raise SourceStop('OPERATOR_PAUSE')
        body = request_body(q,start,end,fund_code); key = m.sha(m.canonical(body))
        meta = state['responses'].get(key)
        if meta is None:
            for attempt in range(3):
                calls += 1
                if calls > 192: raise SourceStop('PARTITION_REQUEST_BUDGET')
                try:
                    status,raw = m.fetch(PATH,body,spacing=1)
                except (OSError,TimeoutError,HTTPException) as exc:
                    consecutive_failures += 1
                    if attempt == 2 or consecutive_failures >= 3: raise SourceStop('SOURCE_TRANSPORT_FAILURE') from exc
                    time.sleep(attempt+1); continue
                except ValueError as exc:
                    raise SourceStop(str(exc)) from exc
                meta = m.save_raw(root,PATH,body,status,raw)
                state['responses'][key] = meta; save()
                if status not in (502,503,504) or attempt == 2: break
                time.sleep(attempt+1)
        raw = read_raw(root,meta); status = meta['http_status']
        if status in (401,403,429) or 300 <= status < 400:
            raise SourceStop('HTTP_'+str(status))
        if raw.lstrip().lower().startswith((b'<html',b'<!doctype')):
            raise SourceStop('NON_JSON_SOURCE_RESPONSE')
        if status in (500,502,503,504) and (end-start).days+1 > 7:
            middle = start + timedelta(days=(end-start).days//2)
            return query(start,middle) + query(middle+timedelta(days=1),end)
        if status in (500,502,503,504):
            consecutive_failures += 1
            if consecutive_failures >= 3: raise SourceStop('CONSECUTIVE_SOURCE_TRANSPORT_FAILURES')
        else: consecutive_failures = 0
        leaf = {'start': start.isoformat(), 'end': end.isoformat(), 'source': meta,
                'status': 'SOURCE_REQUIRES_REVIEW', 'reason': 'HTTP_'+str(status)}
        if status == 200:
            try: rows = m.parse_history(raw,q['currency'],start,end)
            except (ValueError,TypeError) as exc: leaf['reason'] = str(exc)
            else: leaf.update(status='READY',reason=None,rows=len(rows))
        return [leaf]

    leaves = []
    for start,end in intervals(days=initial_days): leaves.extend(query(start,end))
    manifest = {**identity, 'leaves': leaves,
                'source_request_coverage_complete': all(x['status']=='READY' for x in leaves),
                'calendar_completeness_approved': False, 'commercial_rights_verified': False}
    raw = m.canonical(manifest); fingerprint = m.sha(raw)
    relative = 'partitions/'+q['class_key']+'.'+fingerprint+'.json'
    target = root/relative
    if target.exists() and target.read_bytes()!=raw: raise ValueError('IMMUTABLE_MANIFEST_CHANGED')
    if not target.exists(): target.write_bytes(raw)
    return manifest,relative,fingerprint


def validate_manifest(root, meta, q):
    relative = meta['partition_manifest_file']
    if not re.fullmatch(r'partitions/[a-f0-9]{64}\.[a-f0-9]{64}\.json', relative):
        raise ValueError('INVALID_PARTITION_REFERENCE')
    raw = (root/relative).read_bytes(); fingerprint = m.sha(raw)
    if fingerprint != meta['partition_manifest_sha256'] or Path(relative).name != q['class_key']+'.'+fingerprint+'.json':
        raise ValueError('PARTITION_MANIFEST_FINGERPRINT_MISMATCH')
    plan = json.loads(raw)
    fund_code=plan.get('fund_code','all');verify_parent(root,q,fund_code,plan.get('parent_selector_source'))
    expected = {'schema':SCHEMA,'class_key':q['class_key'],'code':q['code'],
                'currency':q['currency'],'isin':q['isin'],'basic_source_sha256':q['basic_source_sha256'],
                'start':m.START.isoformat(),'end':m.END.isoformat()}
    if any(plan.get(k)!=v for k,v in expected.items()): raise ValueError('PARTITION_IDENTITY_OR_SCOPE_CHANGED')
    if (plan.get('source_request_coverage_complete')!=all(leaf['status']=='READY' for leaf in plan['leaves']) or
        plan.get('calendar_completeness_approved') is not False or plan.get('commercial_rights_verified') is not False):
        raise ValueError('PARTITION_APPROVAL_FLAGS_INVALID')
    cursor = m.START; matched = []
    for leaf in plan['leaves']:
        start,end = date.fromisoformat(leaf['start']),date.fromisoformat(leaf['end'])
        source = leaf['source']
        if start != cursor or not start <= end <= m.END: raise ValueError('PARTITION_GAP_OR_OVERLAP')
        if source['path']!=PATH or source['request']!=request_body(q,start,end,fund_code): raise ValueError('PARTITION_REQUEST_MISMATCH')
        payload = read_raw(root,source)
        if leaf['status']=='READY':
            if source['http_status']!=200 or len(m.parse_history(payload,q['currency'],start,end))!=leaf['rows']:
                raise ValueError('PARTITION_SOURCE_MISMATCH')
        elif leaf['status']!='SOURCE_REQUIRES_REVIEW': raise ValueError('UNKNOWN_PARTITION_STATUS')
        if source['raw_sha256']==meta['raw_sha256'] and source['request']==meta['request']:
            if leaf['status']!='READY': raise ValueError('UNAPPROVED_PARTITION_SOURCE')
            matched.append((start,end))
        cursor = end+timedelta(days=1)
    if cursor!=m.END+timedelta(days=1) or len(matched)!=1: raise ValueError('PARTITION_GAP_OR_MISSING_SOURCE')
    return matched[0],plan


def import_one(root, q):
    root=Path(root)
    if m.sha((root/'tdcc-basic.csv').read_bytes())!=q['basic_source_sha256']: raise ValueError('BASIC_SOURCE_FINGERPRINT_MISMATCH')
    with closing(sqlite3.connect(root/'research.sqlite',timeout=30)) as db:
        prior=db.execute('SELECT status,detail_json FROM acquisition WHERE class_key=?',(q['class_key'],)).fetchone()
        if prior and prior[0]=='IMPORTED_UNAPPROVED_RESEARCH': return {'code':q['code'],'status':'ALREADY_IMPORTED','rows_added':0}
        if not prior or prior[0]!='SOURCE_REQUIRES_REVIEW' or json.loads(prior[1]).get('http_status')!=500:
            raise ValueError('PARTITION_RECOVERY_REQUIRES_PRIOR_HTTP_500')
        if db.execute('SELECT 1 FROM research_observations WHERE class_key=? LIMIT 1',(q['class_key'],)).fetchone():
            raise ValueError('EXISTING_CLASS_PRICES_REQUIRE_SEPARATE_REVIEW')
    manifest,relative,fingerprint=acquire(root,q)
    invalid=[leaf for leaf in manifest['leaves'] if leaf['source']['http_status']==200 and leaf['status']!='READY']
    if invalid:
        return {'code':q['code'],'status':'SOURCE_REQUIRES_REVIEW','rows_added':0,'manifest':relative,
                'reasons':sorted({leaf['reason'] for leaf in invalid})}
    snapshots=[]; observations=[]
    for leaf in manifest['leaves']:
        if leaf['status']!='READY': continue
        meta={**q,**leaf['source'],'rows':leaf['rows'],'first_date':None,'last_date':None,
              'partition_manifest_file':relative,'partition_manifest_sha256':fingerprint,
              'status':'IMPORTED_UNAPPROVED_RESEARCH','calendar_complete':False}
        (start,end),plan=validate_manifest(root,meta,q)
        rows=m.parse_history(read_raw(root,meta),q['currency'],start,end)
        meta.update(first_date=rows[0][0],last_date=rows[-1][0])
        snapshots.append((meta['raw_sha256'],q['class_key'],json.dumps(meta,ensure_ascii=False)))
        observations.extend((q['class_key'],d,n,q['currency'],meta['raw_sha256'],m.STATUS) for d,n in rows)
    if not observations: return {'code':q['code'],'status':'SOURCE_REQUIRES_REVIEW','rows_added':0,'manifest':relative}
    if len({r[1] for r in observations})!=len(observations): raise ValueError('DUPLICATE_PARTITION_DATE')
    detail={**q,'status':'IMPORTED_UNAPPROVED_RESEARCH','rows':len(observations),'partition_manifest_file':relative,
            'partition_manifest_sha256':fingerprint,'source_request_coverage_complete':manifest['source_request_coverage_complete'],
            'calendar_complete':False,'previous_source_review':json.loads(prior[1])}
    with closing(sqlite3.connect(root/'research.sqlite',timeout=30)) as db:
        db.execute('BEGIN IMMEDIATE')
        current=db.execute('SELECT status,detail_json FROM acquisition WHERE class_key=?',(q['class_key'],)).fetchone()
        if current!=prior: raise ValueError('ACQUISITION_CHANGED_DURING_RECOVERY')
        db.executemany('INSERT INTO source_snapshots VALUES(?,?,?)',snapshots)
        db.executemany('INSERT INTO research_observations VALUES(?,?,?,?,?,?)',observations)
        db.execute('UPDATE acquisition SET status=?,detail_json=? WHERE class_key=?',(detail['status'],json.dumps(detail,ensure_ascii=False),q['class_key']))
        db.commit()
    m.report(root)
    return {'code':q['code'],'status':detail['status'],'rows_added':len(observations),
            'source_request_coverage_complete':manifest['source_request_coverage_complete'],'manifest':relative}


@contextmanager
def exclusive_run(root):
    """A crashed process releases the OS lock; no stale PID override is needed."""
    import fcntl
    with (Path(root)/'.partition-recovery.lock').open('a') as handle:
        try: fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as exc: raise SourceStop('PARTITION_RECOVERY_ALREADY_RUNNING') from exc
        try: yield
        finally: fcntl.flock(handle,fcntl.LOCK_UN)


def recover(root, limit=100):
    with exclusive_run(root): return _recover(root,limit)


def _recover(root, limit=100):
    if not 1<=limit<=100: raise ValueError('BATCH_LIMIT')
    root=Path(root);queue=json.loads((root/'queue.json').read_text())
    with closing(sqlite3.connect(root/'research.sqlite')) as db:
        eligible={key for key,d in db.execute("SELECT class_key,detail_json FROM acquisition WHERE status='SOURCE_REQUIRES_REVIEW'") if json.loads(d).get('http_status')==500}
    positive=set()
    if (root/'tdcc-recent.csv').exists():
        for row in csv.DictReader((root/'tdcc-recent.csv').read_text(encoding='utf-8-sig').splitlines()):
            try:
                value=Decimal(row['基金淨值(金額)'].strip())
                if value.is_finite() and value>0: positive.add(row['基金代碼'].strip())
            except InvalidOperation: pass
    selected=sorted((q for q in queue if q['class_key'] in eligible),key=lambda q:q['code'] not in positive)[:limit]
    results=[];paused=None
    for q in selected:
        try: result=import_one(root,q)
        except SourceStop as exc: paused=str(exc);break
        results.append(result);print(json.dumps(result,ensure_ascii=False),flush=True)
    result={'schema':'fundclear_partition_recovery_v1','checked_at_utc':m.stamp(),'selected_classes':len(selected),
            'results':results,'rows_added':sum(x['rows_added'] for x in results),'paused_reason':paused,
            'formal_three_year_approved_funds':0,'commercial_rights_verified':False}
    (root/'partition-recovery-report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    m.report(root,paused)
    return result


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',required=True);p.add_argument('--limit',type=int,default=100)
    args=p.parse_args();print(json.dumps(recover(args.root,args.limit),ensure_ascii=False,indent=2))
