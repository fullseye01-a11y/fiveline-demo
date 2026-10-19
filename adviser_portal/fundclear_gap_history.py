"""Admit already-acquired single-day gap responses without replacing old NAV."""
from __future__ import annotations
import json
import sqlite3
from collections import defaultdict
from contextlib import closing
from datetime import date,timedelta
from pathlib import Path
from . import fundclear_history_import as m
from . import fundclear_partition_history as p


def apply_probe(root, probe_name='one-day-gap-probe.json'):
    root=Path(root)
    with p.exclusive_run(root):return _apply(root,probe_name)


def _apply(root, probe_name):
    probe_raw=(root/probe_name).read_bytes();probe_sha=m.sha(probe_raw);probe=json.loads(probe_raw)
    queue={q['class_key']:q for q in json.loads((root/'queue.json').read_text())};grouped=defaultdict(list)
    for row in probe['results']:grouped[row['class_key']].append(row)
    if len(grouped)>100:raise ValueError('BATCH_LIMIT')
    results=[]
    for key,entries in grouped.items():
        q=queue[key]
        if m.sha((root/'tdcc-basic.csv').read_bytes())!=q['basic_source_sha256']:raise ValueError('BASIC_SOURCE_FINGERPRINT_MISMATCH')
        with closing(sqlite3.connect(root/'research.sqlite')) as db:
            prior=db.execute('SELECT status,detail_json FROM acquisition WHERE class_key=?',(key,)).fetchone()
        if not prior or prior[0]!='IMPORTED_UNAPPROVED_RESEARCH':raise ValueError('GAP_RECOVERY_REQUIRES_EXISTING_RESEARCH')
        detail=json.loads(prior[1])
        if detail.get('one_day_probe_sha256')==probe_sha:
            results.append({'code':q['code'],'status':'ALREADY_APPLIED','rows_added':0});continue
        reference=detail['partition_manifest_file'];raw=(root/reference).read_bytes()
        if m.sha(raw)!=detail['partition_manifest_sha256']:raise ValueError('PARTITION_MANIFEST_FINGERPRINT_MISMATCH')
        plan=json.loads(raw);byday={}
        for entry in entries:
            if entry['code']!=q['code'] or entry['parent_manifest']!=reference:raise ValueError('PROBE_CLASS_OR_MANIFEST_MISMATCH')
            day=date.fromisoformat(entry['date'])
            if day in byday:raise ValueError('DUPLICATE_DAY_PROBE')
            if not any(leaf['status']!='READY' and date.fromisoformat(leaf['start'])<=day<=date.fromisoformat(leaf['end']) for leaf in plan['leaves']):
                raise ValueError('DAY_NOT_IN_SOURCE_REVIEW_RANGE')
            if 'source' in entry:byday[day]=entry['source']
        leaves=[];new_leaves=[]
        for leaf in plan['leaves']:
            if leaf['status']=='READY':leaves.append(leaf);continue
            start,end=date.fromisoformat(leaf['start']),date.fromisoformat(leaf['end'])
            days=[start+timedelta(days=i) for i in range((end-start).days+1)]
            if any(day not in byday for day in days):leaves.append(leaf);continue
            for day in days:
                meta=byday[day]
                if meta['path']!=p.PATH or meta['request']!=p.request_body(q,day,day,plan.get('fund_code','all')):
                    raise ValueError('PROBE_REQUEST_MISMATCH')
                payload=p.read_raw(root,meta)
                new={'start':day.isoformat(),'end':day.isoformat(),'source':meta,'status':'SOURCE_REQUIRES_REVIEW','reason':'HTTP_'+str(meta['http_status'])}
                if meta['http_status']==200:
                    parsed=m.parse_history(payload,q['currency'],day,day)
                    new.update(status='READY',reason=None,rows=len(parsed));new_leaves.append(new)
                leaves.append(new)
        if not new_leaves:
            results.append({'code':q['code'],'status':'NO_COMPLETE_REFINEMENT_WITH_NEW_PRICES','rows_added':0});continue
        revised={**plan,'leaves':leaves,'source_request_coverage_complete':all(x['status']=='READY' for x in leaves),
                 'parent_manifest_file':reference,'parent_manifest_sha256':detail['partition_manifest_sha256'],'recovery_kind':'SINGLE_DAY_SOURCE_GAP'}
        raw=m.canonical(revised);sha=m.sha(raw);new_reference='partitions/'+key+'.'+sha+'.json';target=root/new_reference
        if target.exists() and target.read_bytes()!=raw:raise ValueError('IMMUTABLE_MANIFEST_CHANGED')
        if not target.exists():target.write_bytes(raw)
        snapshots=[];prices=[]
        for leaf in new_leaves:
            meta={**q,**leaf['source'],'rows':leaf['rows'],'partition_manifest_file':new_reference,'partition_manifest_sha256':sha,
                  'calendar_complete':False,'status':'IMPORTED_UNAPPROVED_RESEARCH'}
            (start,end),verified=p.validate_manifest(root,meta,q)
            parsed=m.parse_history(p.read_raw(root,meta),q['currency'],start,end)
            meta.update(first_date=parsed[0][0],last_date=parsed[-1][0])
            snapshots.append((meta['raw_sha256'],key,json.dumps(meta,ensure_ascii=False)))
            prices.extend((key,d,n,q['currency'],meta['raw_sha256'],m.STATUS) for d,n in parsed)
        with closing(sqlite3.connect(root/'research.sqlite',timeout=30)) as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT status,detail_json FROM acquisition WHERE class_key=?',(key,)).fetchone()!=prior:
                raise ValueError('ACQUISITION_CHANGED_DURING_RECOVERY')
            for row in prices:
                if db.execute('SELECT 1 FROM research_observations WHERE class_key=? AND valuation_date=? LIMIT 1',(key,row[1])).fetchone():
                    raise ValueError('DAY_ALREADY_HAS_PRICE')
            old_count=db.execute('SELECT count(*) FROM research_observations WHERE class_key=?',(key,)).fetchone()[0]
            db.executemany('INSERT INTO source_snapshots VALUES(?,?,?)',snapshots)
            db.executemany('INSERT INTO research_observations VALUES(?,?,?,?,?,?)',prices)
            detail.update(rows=old_count+len(prices),partition_manifest_file=new_reference,partition_manifest_sha256=sha,
                          previous_partition_manifest_file=reference,one_day_probe_sha256=probe_sha,
                          source_request_coverage_complete=revised['source_request_coverage_complete'],calendar_complete=False)
            db.execute('UPDATE acquisition SET detail_json=? WHERE class_key=?',(json.dumps(detail,ensure_ascii=False),key));db.commit()
        results.append({'code':q['code'],'status':'ADDED_UNAPPROVED_SOURCE_GAP_PRICES','rows_added':len(prices),'manifest':new_reference})
    report={'checked_at_utc':m.stamp(),'probe_sha256':probe_sha,'results':results,'rows_added':sum(x['rows_added'] for x in results),
            'old_prices_rewritten':0,'formal_three_year_approved_funds':0}
    (root/'one-day-gap-import-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    prior_report=json.loads((root/'partition-recovery-report.json').read_text()) if (root/'partition-recovery-report.json').exists() else {}
    m.report(root,prior_report.get('paused_reason'))
    return report


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',required=True);args=parser.parse_args()
    print(json.dumps(apply_probe(args.root),ensure_ascii=False,indent=2))
