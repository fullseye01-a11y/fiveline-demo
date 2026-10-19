"""Public FundClear three-year histories, isolated research observations only.

Selector metadata binds the class request; the response does not independently
identify the class. No commercial rights or calendar completeness are inferred.
"""
from __future__ import annotations
import csv, gzip, hashlib, json, re, sqlite3, time, unicodedata
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from threading import Lock, Thread, Event
from urllib.error import HTTPError
from urllib.request import Request, urlopen

BASE = 'https://www.fundclear.com.tw'
START, END = date(2023,10,18), date(2026,10,15)
STATUS = 'UNAPPROVED_SOURCE_OBSERVATION'
lock = Lock()
next_start = 0.0
_pooled_transport = None

class PooledTransport:
    """Optional aiohttp connection reuse; public requests and normal TLS only."""
    def __init__(self,connections=8):
        if not 1<=connections<=8:raise ValueError('connection pool must be 1..8')
        import asyncio,aiohttp
        self.asyncio=asyncio;self.aiohttp=aiohttp;self.connections=connections
        ready=Event()
        def run():
            self.loop=asyncio.new_event_loop();asyncio.set_event_loop(self.loop)
            self.loop.run_until_complete(self.open());ready.set();self.loop.run_forever()
        self.thread=Thread(target=run,daemon=True);self.thread.start()
        if not ready.wait(10):raise TimeoutError('transport startup')
    async def open(self):
        self.session=self.aiohttp.ClientSession(trust_env=True,connector=self.aiohttp.TCPConnector(limit=self.connections),timeout=self.aiohttp.ClientTimeout(total=45),headers={'Content-Type':'application/json','Accept':'application/json','User-Agent':'FivelineAdviserResearch/0.3'})
    async def request_async(self,path,body):
        async with self.session.post(BASE+path,data=canonical(body),allow_redirects=False) as response:
            parts=[];size=0
            async for part in response.content.iter_chunked(65536):
                size+=len(part)
                if size>8_000_000:raise ValueError('RESPONSE_TOO_LARGE')
                parts.append(part)
            return response.status,b''.join(parts)
    def request(self,path,body):
        future=self.asyncio.run_coroutine_threadsafe(self.request_async(path,body),self.loop)
        try:return future.result(timeout=50)
        except Exception:
            future.cancel();raise
    def close(self):
        self.asyncio.run_coroutine_threadsafe(self.session.close(),self.loop).result(timeout=10)
        self.loop.call_soon_threadsafe(self.loop.stop);self.thread.join(timeout=10)

def enable_pooled_transport():
    global _pooled_transport
    if _pooled_transport is None:_pooled_transport=PooledTransport()
    return _pooled_transport

def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()

def sha(raw): return hashlib.sha256(raw).hexdigest()

def norm(value):
    return ''.join(c for c in unicodedata.normalize('NFKC',str(value)).casefold().replace('臺','台') if c.isalnum())

def valid_isin(value):
    value=str(value).strip().upper()
    if not re.fullmatch(r'[A-Z]{2}[A-Z0-9]{9}[0-9]',value):return False
    expanded=''.join(str(ord(c)-55) if c.isalpha() else c for c in value)
    total=0
    for i,c in enumerate(reversed(expanded)):
        d=int(c)*(2 if i%2 else 1);total+=d//10+d%10
    return total%10==0

def stamp(): return datetime.now(timezone.utc).isoformat()

def fetch(path, body, spacing=.5):
    global next_start
    with lock:
        wait=max(0,next_start-time.monotonic())
        if wait: time.sleep(wait)
        next_start=time.monotonic()+spacing
    if _pooled_transport is not None:return _pooled_transport.request(path,body)
    payload=canonical(body)
    req=Request(BASE+path,data=payload,headers={'Content-Type':'application/json','Accept':'application/json','User-Agent':'FivelineAdviserResearch/0.3'})
    try:
        with urlopen(req,timeout=45) as response:
            if response.geturl()!=BASE+path: raise ValueError('UNEXPECTED_REDIRECT')
            raw=response.read(8_000_001)
            if len(raw)>8_000_000: raise ValueError('RESPONSE_TOO_LARGE')
            return response.status,raw
    except HTTPError as exc:
        return exc.code,exc.read(1_000_001)

def save_raw(root,path,body,status,raw):
    key=sha(canonical({'path':path,'body':body}))
    target=root/'raw'/f'{key}.{sha(raw)}.json.gz'
    target.parent.mkdir(parents=True,exist_ok=True)
    if not target.exists(): target.write_bytes(gzip.compress(raw,mtime=0))
    meta={'path':path,'request':body,'http_status':status,'raw_sha256':sha(raw),'raw_file':str(target.relative_to(root)),'fetched_at_utc':stamp()}
    with lock:
        with (root/'responses.jsonl').open('a') as f:f.write(json.dumps(meta,ensure_ascii=False)+'\n')
    return meta

def parse_history(raw,expected_currency,start=START,end=END):
    d=json.loads(raw)
    if not isinstance(d,dict) or d.get('fundCurr')!=expected_currency: raise ValueError('CURRENCY_MISMATCH')
    rows=d.get('tableList')
    if not isinstance(rows,list) or not rows or len(rows)>2000: raise ValueError('EMPTY_OR_INVALID_HISTORY')
    result={}
    for row in rows:
        if not isinstance(row,dict): raise ValueError('INVALID_ROW')
        day=date.fromisoformat(str(row.get('navTxnDate','')).replace('/','-'))
        if not start<=day<=end: raise ValueError('DATE_OUTSIDE_REQUEST')
        original=str(row.get('navValue','')).strip()
        if not re.fullmatch(r'\d+(?:\.\d+)?',original): raise ValueError('INVALID_NAV')
        try: value=Decimal(original)
        except InvalidOperation: raise ValueError('INVALID_NAV')
        if not value.is_finite() or not Decimal('0')<value<Decimal('1000000000'): raise ValueError('INVALID_NAV')
        key=day.isoformat()
        if key in result: raise ValueError('DUPLICATE_VALUATION_DATE')
        result[key]=original
    return sorted(result.items())

def init_db(path):
    with closing(sqlite3.connect(path,timeout=30)) as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS acquisition(class_key TEXT PRIMARY KEY, status TEXT NOT NULL, detail_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS source_snapshots(raw_sha256 TEXT NOT NULL, class_key TEXT NOT NULL, metadata_json TEXT NOT NULL, PRIMARY KEY(raw_sha256,class_key));
        CREATE TABLE IF NOT EXISTS research_observations(class_key TEXT NOT NULL, valuation_date TEXT NOT NULL, nav_per_unit TEXT NOT NULL, currency TEXT NOT NULL, raw_sha256 TEXT NOT NULL, status TEXT NOT NULL, PRIMARY KEY(class_key,valuation_date,raw_sha256));
        ''')
        db.commit()

def report(root,paused_reason=None):
    with closing(sqlite3.connect(root/'research.sqlite',timeout=30)) as db:
        counts=dict(db.execute('SELECT status,count(*) FROM acquisition GROUP BY status'))
        row=db.execute("SELECT coalesce(sum(json_extract(metadata_json,'$.rows')),0),min(json_extract(metadata_json,'$.first_date')),max(json_extract(metadata_json,'$.last_date')) FROM source_snapshots").fetchone()
    queue=json.loads((root/'queue.json').read_text()) if (root/'queue.json').exists() else []
    result={'schema':'fundclear_isolated_history_import_v1','checked_at_utc':stamp(),'requested_start':START.isoformat(),'requested_end':END.isoformat(),
       'catalog_classes':len(queue),'acquisition_status_counts':counts,'research_nav_rows_persisted':row[0],
       'first_observation_date':row[1],'last_observation_date':row[2],'formal_three_year_approved_funds':0,
       'source_class_binding':'REQUEST_AND_SELECTOR_METADATA_ONLY','valuation_calendar_verified':False,'commercial_rights_verified':False,
       'production_or_portfolio_modified':False,'paused_reason':paused_reason}
    temp=root/'report.json.tmp'; temp.write_text(json.dumps(result,ensure_ascii=False,indent=2)); temp.replace(root/'report.json')
    return result

def build_queue(root,basic_file=None,workers=4):
    if not 1<=workers<=8:raise ValueError('workers must be 1..8')
    root.mkdir(parents=True,exist_ok=True)
    if basic_file is None:
        basic_url='https://opendata.tdcc.com.tw/getOD.ashx?id=3-2'
        with urlopen(Request(basic_url,headers={'User-Agent':'FivelineAdviserResearch/0.3'}),timeout=45) as response:
            if response.status!=200 or response.geturl()!=basic_url:raise ValueError('BASIC_SOURCE_TRANSPORT_MISMATCH')
            source=response.read(36_000_001)
    else:source=Path(basic_file).read_bytes()
    if not 0<len(source)<=36_000_000:raise ValueError('BASIC_SOURCE_SIZE_INVALID')
    if (root/'queue.json').exists():
        if not (root/'tdcc-basic.csv').exists() or sha((root/'tdcc-basic.csv').read_bytes())!=sha(source):
            raise ValueError('CATALOG_SNAPSHOT_CHANGED_USE_NEW_RESEARCH_DIRECTORY')
        return json.loads((root/'queue.json').read_text())
    (root/'tdcc-basic.csv').write_bytes(source)
    reader=csv.DictReader(source.decode('utf-8-sig').splitlines())
    if len(reader.fieldnames or [])!=len(set(reader.fieldnames or [])):raise ValueError('DUPLICATE_BASIC_SOURCE_COLUMNS')
    basics=list(reader)
    required={'基金代碼','基金ISIN_CODE','基金名稱','境外基金機構','計價幣別'}
    if not 1000<=len(basics)<=55000 or not all(required<=set(row) and None not in row and all(isinstance(v,str) for v in row.values()) for row in basics):raise ValueError('BASIC_SOURCE_COVERAGE_OR_SCHEMA_INVALID')
    bycode=defaultdict(list)
    for row in basics: bycode[row['基金代碼'].strip()].append(row)
    path='/api/offshore/common-select/org-selection'
    status,raw=fetch(path,{'all':False},1)
    meta=save_raw(root,path,{'all':False},status,raw)
    if status!=200: raise ValueError(f'ORG_HTTP_{status}')
    orgs=json.loads(raw)
    if not isinstance(orgs,list) or not 30<=len(orgs)<=300: raise ValueError('ORG_COVERAGE_INVALID')
    selector_meta=[meta]; selections=[]
    def get(org):
        path='/api/offshore/common-select/fund-class-selection'
        body={'all':False,'organizeCode':org['value'],'fundCode':'all'}
        status,raw=fetch(path,body,1)
        return org,status,raw,save_raw(root,path,body,status,raw)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        iterator=iter(orgs);futures=set()
        def submit():
            try:org=next(iterator)
            except StopIteration:return
            futures.add(pool.submit(get,org))
        for _ in range(workers):submit()
        while futures:
            for f in as_completed(list(futures)):
                futures.remove(f)
                org,status,raw,meta=f.result();selector_meta.append(meta)
                if status in (401,403,429):
                    for pending in futures:pending.cancel()
                    raise ValueError(f'SELECTOR_HTTP_{status}_STOP')
                if status!=200: raise ValueError(f'SELECTOR_HTTP_{status}')
                rows=json.loads(raw)
                if not isinstance(rows,list): raise ValueError('INVALID_SELECTOR')
                for row in rows:
                    if row.get('value')=='all':continue
                    selections.append((org,row,meta['raw_sha256']))
                print(json.dumps({'selector_orgs':len(selector_meta)-1,'classes_seen':len(selections)}),flush=True)
                submit()
    grouped=defaultdict(list)
    for org,selector,selector_sha in selections: grouped[str(selector['value']).strip()].append((org,selector,selector_sha))
    ccys={'美元':'USD','歐元':'EUR','日幣':'JPY','日圓':'JPY','英鎊':'GBP','港幣':'HKD','澳幣':'AUD','澳元':'AUD','南非幣':'ZAR','加幣':'CAD','加元':'CAD','人民幣':'CNY','瑞士法郎':'CHF','新加坡幣':'SGD','新加坡元':'SGD','星幣':'SGD','瑞典幣':'SEK','瑞典克朗':'SEK','紐西蘭幣':'NZD','紐幣':'NZD','紐元':'NZD','新臺幣':'TWD','新台幣':'TWD'}
    queue=[]
    for code,items in sorted(bycode.items()):
        key=sha(canonical({'source':'TDCC_BASIC','code':code}))
        item={'class_key':key,'code':code,'basic_source_sha256':sha(source),'identity_approved':False}
        if len(items)!=1 or len(grouped[code])!=1:
            item.update(status='IDENTITY_REVIEW',reason='NONUNIQUE_OR_MISSING_EXACT_CLASS_CODE')
        else:
            basic=items[0];org,sel,selector_sha=grouped[code][0]
            currency=ccys.get(basic['計價幣別'],basic['計價幣別'] if re.fullmatch('[A-Z]{3}',basic['計價幣別']) else '')
            item.update(name=basic['基金名稱'],isin=basic['基金ISIN_CODE'],currency=currency,organize_code=org['value'],selector_raw_sha256=selector_sha,selector_name=sel['name'],organize_name=org['name'])
            if not valid_isin(basic['基金ISIN_CODE']):
                item.update(status='IDENTITY_REVIEW',reason='INVALID_OR_MISSING_ISIN')
            elif not currency or norm(basic['基金名稱'])!=norm(sel['name']) or norm(basic['境外基金機構'])!=norm(org['name']):
                item.update(status='IDENTITY_REVIEW',reason='NAME_CURRENCY_OR_ORGANIZATION_MISMATCH')
            else: item.update(status='QUEUED',reason='EXACT_CODE_NAME_CURRENCY_ORGANIZATION_CANDIDATE')
        queue.append(item)
    (root/'selectors.json').write_text(json.dumps(selector_meta,ensure_ascii=False,indent=2))
    (root/'queue.json').write_text(json.dumps(queue,ensure_ascii=False,indent=2))
    (root/'selector-coverage.json').write_text(json.dumps({'organization_count':len(orgs),'selector_class_count':len(selections),'basic_class_count':len(basics),'selector_codes_not_in_basic':sorted(set(grouped)-set(bycode)),'queue_counts':dict(Counter(q['status'] for q in queue))},ensure_ascii=False,indent=2))
    return queue

def bind_catalog_snapshot(root,catalog_file):
    """Bind exact source codes to a frozen website catalog; never approve IDs."""
    root=Path(root);raw=Path(catalog_file).read_bytes()
    if not 0<len(raw)<=8_000_000:raise ValueError('CATALOG_SIZE_INVALID')
    frozen=root/'website-catalog.json'
    if frozen.exists() and frozen.read_bytes()!=raw:raise ValueError('CATALOG_SNAPSHOT_CHANGED_USE_NEW_RESEARCH_DIRECTORY')
    catalog=json.loads(raw);bycode=defaultdict(list)
    for row in catalog['instruments']:
        if row.get('source')=='tdcc_offshore_funds' and row.get('category')=='offshore_fund_share_class':bycode[row['code']].append(row)
    source=(root/'tdcc-basic.csv').read_bytes()
    basics={r['基金代碼'].strip():r for r in csv.DictReader(source.decode('utf-8-sig').splitlines())}
    queue=json.loads((root/'queue.json').read_text());bindings=[]
    isin_counts=Counter(str(r.get('isin','')).strip() for rows in bycode.values() for r in rows if valid_isin(r.get('isin')))
    for q in queue:
        rows=bycode[q['code']];basic=basics[q['code']]
        match=(len(rows)==1 and norm(rows[0]['name'])==norm(basic['基金名稱']) and
               str(rows[0].get('isin','')).strip()==str(basic['基金ISIN_CODE']).strip() and
               norm(rows[0].get('currency',''))==norm(basic['計價幣別']))
        chosen=rows[0] if len(rows)==1 else {}
        bindings.append({'class_key':q['class_key'],'code':q['code'],'catalog_uid':chosen.get('uid'),
          'status':'NORMALIZED_CODE_NAME_ISIN_CURRENCY_MATCH' if match else 'CATALOG_IDENTITY_REVIEW',
          'shared_isin_requires_review':isin_counts.get(chosen.get('isin'),0)>1,'identity_approved':False})
    result={'catalog_file_sha256':sha(raw),'basic_source_sha256':sha(source),'catalog_checked_at_utc':catalog.get('checked_at_utc'),
      'status_counts':dict(Counter(b['status'] for b in bindings)),'bindings':bindings,'identity_approved':False}
    frozen.write_bytes(raw);(root/'catalog-bindings.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    return {k:v for k,v in result.items() if k!='bindings'}

def import_queue(root,workers=8,limit=None):
    if not 1<=workers<=8:raise ValueError('workers must be 1..8')
    limit=100 if limit is None else limit
    if not 1<=limit<=100:raise ValueError('each import batch must contain 1..100 classes')
    root=Path(root); init_db(root/'research.sqlite')
    queue=json.loads((root/'queue.json').read_text())
    with closing(sqlite3.connect(root/'research.sqlite')) as db:
        done={r[0] for r in db.execute('SELECT class_key FROM acquisition')}
        for q in queue:
            if q['status']!='QUEUED' and q['class_key'] not in done:
                db.execute('INSERT INTO acquisition VALUES(?,?,?)',(q['class_key'],q['status'],json.dumps(q,ensure_ascii=False)))
        db.commit()
    if (root/'pause-requested').exists():return report(root,'OPERATOR_PAUSE')
    pending=[q for q in queue if q['status']=='QUEUED' and q['class_key'] not in done]
    if limit is not None: pending=pending[:limit]
    def get(q):
        path='/api/offshore/nav-profit/query-history'
        body={'organizeCode':q['organize_code'],'fundCode':'all','fundClassCode':q['code'],'startDate':START.strftime('%Y/%m/%d'),'endDate':END.strftime('%Y/%m/%d')}
        for attempt in range(3):
            try:
                status,raw=fetch(path,body)
                meta=save_raw(root,path,body,status,raw)
                if status not in (500,502,503,504) or attempt==2:return q,status,raw,meta
            except (OSError,TimeoutError):
                if attempt==2:raise
            time.sleep(2*(attempt+1))
    stop=False;consecutive_transports=0;paused_reason=None
    with ThreadPoolExecutor(max_workers=workers) as pool:
        iterator=iter(pending);futures={}
        def submit():
            try:q=next(iterator)
            except StopIteration:return
            futures[pool.submit(get,q)]=q
        for _ in range(workers):submit()
        while futures:
            for future in as_completed(list(futures)):
                q=futures.pop(future);meta=None;rows=[]
                try:
                    q,status,raw,meta=future.result()
                    if status in (500,502,503,504):
                        consecutive_transports+=1
                        if consecutive_transports>=3:stop=True;paused_reason='CONSECUTIVE_SOURCE_TRANSPORT_FAILURES'
                    else:consecutive_transports=0
                    if status in (401,403,429):stop=True;paused_reason=f'HTTP_{status}'
                    if 300<=status<400:stop=True;paused_reason='UNEXPECTED_SOURCE_REDIRECT'
                    if status!=200:raise ValueError(f'HTTP_{status}')
                    if raw.lstrip().lower().startswith((b'<html',b'<!doctype')):
                        stop=True;paused_reason='NON_JSON_SOURCE_RESPONSE';raise ValueError(paused_reason)
                    rows=parse_history(raw,q['currency'])
                    state='IMPORTED_UNAPPROVED_RESEARCH'
                    detail={**q,**meta,'rows':len(rows),'first_date':rows[0][0],'last_date':rows[-1][0],'status':state,'calendar_complete':False}
                except Exception as exc:
                    state='SOURCE_REQUIRES_REVIEW'
                    detail={**q,**(meta or {}),'status':state,'reason':str(exc)[:200]}
                    if not isinstance(exc,ValueError):
                        consecutive_transports+=1
                        if consecutive_transports>=3:stop=True;paused_reason='CONSECUTIVE_SOURCE_TRANSPORT_FAILURES'
                with closing(sqlite3.connect(root/'research.sqlite',timeout=30)) as db:
                    if rows:
                        db.execute('INSERT OR IGNORE INTO source_snapshots VALUES(?,?,?)',(meta['raw_sha256'],q['class_key'],json.dumps(detail,ensure_ascii=False)))
                        db.executemany('INSERT OR IGNORE INTO research_observations VALUES(?,?,?,?,?,?)',[(q['class_key'],d,n,q['currency'],meta['raw_sha256'],STATUS) for d,n in rows])
                    db.execute('INSERT OR REPLACE INTO acquisition VALUES(?,?,?)',(q['class_key'],state,json.dumps(detail,ensure_ascii=False)));db.commit()
                r=report(root)
                print(json.dumps({'attempted':sum(r['acquisition_status_counts'].values()),'imported':r['acquisition_status_counts'].get('IMPORTED_UNAPPROVED_RESEARCH',0),'rows':r['research_nav_rows_persisted']}),flush=True)
                if (root/'pause-requested').exists():stop=True;paused_reason='OPERATOR_PAUSE'
                if not stop:submit()
    return report(root,paused_reason)

def audit_store(root):
    """Verify stored prices against their immutable raw response and request."""
    root=Path(root);queue={q['class_key']:q for q in json.loads((root/'queue.json').read_text())}
    basic_sha=sha((root/'tdcc-basic.csv').read_bytes())
    catalog_binding=None
    if (root/'catalog-bindings.json').exists():
        catalog_binding=json.loads((root/'catalog-bindings.json').read_text())
        if catalog_binding['catalog_file_sha256']!=sha((root/'website-catalog.json').read_bytes()) or catalog_binding['basic_source_sha256']!=basic_sha:raise ValueError('CATALOG_BINDING_FINGERPRINT_MISMATCH')
        bound={b['class_key']:b for b in catalog_binding['bindings']}
    checked=0;observations=0;start_late=0;end_early=0;gaps=[];counts=[];reviews=[];class_dates=defaultdict(list);partition_plans={};partition_sources=defaultdict(set);partition_reviews=defaultdict(list);current_plans={}
    with closing(sqlite3.connect(root/'research.sqlite',timeout=30)) as db:
        db.execute('BEGIN')
        for key,detail in db.execute('SELECT class_key,detail_json FROM acquisition'):
            detail=json.loads(detail)
            if detail.get('partition_manifest_file'):current_plans[key]=detail['partition_manifest_file']
        if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('DATABASE_INTEGRITY_FAILED')
        if db.execute('SELECT 1 FROM research_observations GROUP BY class_key,valuation_date HAVING count(*)>1 LIMIT 1').fetchone():raise ValueError('DUPLICATE_CLASS_DATE')
        for raw_sha,key,meta in db.execute('SELECT raw_sha256,class_key,metadata_json FROM source_snapshots'):
            q=queue[key];meta=json.loads(meta);body=meta['request']
            if catalog_binding is not None and (key not in bound or bound[key]['status']!='NORMALIZED_CODE_NAME_ISIN_CURRENCY_MATCH'):raise ValueError('CATALOG_IDENTITY_REVIEW')
            if q['basic_source_sha256']!=basic_sha:raise ValueError('BASIC_SOURCE_FINGERPRINT_MISMATCH')
            if body['fundClassCode']!=q['code'] or body['organizeCode']!=q['organize_code']:raise ValueError('CLASS_REQUEST_MISMATCH')
            if meta['path']!='/api/offshore/nav-profit/query-history':raise ValueError('UNEXPECTED_SOURCE')
            if 'partition_manifest_file' in meta:
                from .fundclear_partition_history import validate_manifest
                (request_start,request_end),plan=validate_manifest(root,meta,q)
                partition_plans[(key,meta['partition_manifest_file'])]=plan
                partition_sources[(key,meta['partition_manifest_file'])].add((raw_sha,body['startDate'],body['endDate']))
            else:
                if body['startDate']!=START.strftime('%Y/%m/%d') or body['endDate']!=END.strftime('%Y/%m/%d'):raise ValueError('REQUEST_RANGE_MISMATCH')
                request_start,request_end=START,END
            if not re.fullmatch(r'raw/[a-f0-9]{64}\.[a-f0-9]{64}\.json\.gz',meta['raw_file']):raise ValueError('INVALID_RAW_REFERENCE')
            raw=gzip.decompress((root/meta['raw_file']).read_bytes())
            if sha(raw)!=raw_sha:raise ValueError('RAW_FINGERPRINT_MISMATCH')
            parsed=parse_history(raw,q['currency'],request_start,request_end)
            stored=db.execute('SELECT valuation_date,nav_per_unit,currency,status FROM research_observations WHERE class_key=? AND raw_sha256=? ORDER BY valuation_date',(key,raw_sha)).fetchall()
            if stored!=[(d,n,q['currency'],STATUS) for d,n in parsed]:raise ValueError('PRICE_ROWS_DIFFER_FROM_RAW')
            checked+=1;observations+=len(parsed);class_dates[key].extend(d for d,n in parsed)
        for identity,plan in partition_plans.items():
            expected={(leaf['source']['raw_sha256'],leaf['source']['request']['startDate'],leaf['source']['request']['endDate']) for leaf in plan['leaves'] if leaf['status']=='READY'}
            all_sources=set().union(*(sources for (key,reference),sources in partition_sources.items() if key==identity[0]))
            if not expected<=all_sources:raise ValueError('PARTITION_PRICE_SNAPSHOT_MISSING')
            if current_plans.get(identity[0])==identity[1]:
                if expected!=all_sources:raise ValueError('CURRENT_PARTITION_PRICE_SNAPSHOT_MISSING')
                partition_reviews[identity[0]].extend({'from':leaf['start'],'to':leaf['end'],'reason':leaf['reason']} for leaf in plan['leaves'] if leaf['status']!='READY')
        if set(current_plans.items())!=set(identity for identity in partition_plans if current_plans.get(identity[0])==identity[1]):
            raise ValueError('CURRENT_PARTITION_REFERENCE_MISSING')
        for key,days in class_dates.items():
            q=queue[key];days=sorted(days);counts.append(len(days))
            start_late+=days[0]>START.isoformat();end_early+=days[-1]<END.isoformat()
            dates=[date.fromisoformat(d) for d in days]
            spans=[{'from':a.isoformat(),'to':b.isoformat(),'calendar_days':(b-a).days} for a,b in zip(dates,dates[1:]) if (b-a).days>10]
            gaps.append(max(((b-a).days for a,b in zip(dates,dates[1:])),default=0))
            if spans or days[0]>START.isoformat() or days[-1]<END.isoformat() or partition_reviews[key]:
                reviews.append({'class_key':key,'code':q['code'],'isin':q.get('isin'),'name':q.get('name'),'currency':q['currency'],
                  'observations':len(days),'first_date':days[0],'last_date':days[-1],'observed_gaps_over_ten_calendar_days':spans,
                  'source_request_review_ranges':partition_reviews[key],
                  'expected_valuation_dates_not_inferred':True,'fund_inception_not_inferred':True,'status':'TEMPORAL_COVERAGE_REVIEW'})
        actual=db.execute('SELECT count(*) FROM research_observations').fetchone()[0]
        if actual!=observations:raise ValueError('UNREFERENCED_PRICE_ROWS')
    result={'checked_at_utc':stamp(),'raw_fingerprints_verified':checked,'imported_classes_verified':len(class_dates),'actual_nav_rows_verified':actual,'class_dates_unique':True,
      'classes_starting_after_requested_start':start_late,'classes_ending_before_requested_end':end_early,
      'fewest_observations_per_imported_class':min(counts,default=0),'most_observations_per_imported_class':max(counts,default=0),
      'largest_observed_calendar_gap_days':max(gaps,default=0),'gaps_over_ten_calendar_days':sum(x>10 for x in gaps),
      'website_catalog_bindings_verified':len(class_dates) if catalog_binding is not None else 0,
      'partition_manifests_verified':len(partition_plans),
      'classes_with_partition_source_review_ranges':sum(bool(v) for v in partition_reviews.values()),
      'temporal_review_classes':len(reviews),
      'calendar_completeness_approved':False,'formal_three_year_approved_funds':0,'commercial_rights_verified':False}
    (root/'audit-report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    (root/'temporal-review.json').write_text(json.dumps(reviews,ensure_ascii=False,indent=2))
    return result

def reconcile_recent_source(root,recent_file):
    """Cross-check independent TDCC recent CSV; never backfill or revise prices."""
    root=Path(root);raw=Path(recent_file).read_bytes();frozen=root/'tdcc-recent.csv'
    if not 0<len(raw)<=36_000_000:raise ValueError('RECENT_SOURCE_SIZE_INVALID')
    if frozen.exists() and frozen.read_bytes()!=raw:raise ValueError('RECENT_SNAPSHOT_CHANGED_USE_NEW_RESEARCH_DIRECTORY')
    queue={q['code']:q for q in json.loads((root/'queue.json').read_text())}
    basic={r['基金代碼'].strip():r for r in csv.DictReader((root/'tdcc-basic.csv').read_text(encoding='utf-8-sig').splitlines())}
    reader=csv.DictReader(raw.decode('utf-8-sig').splitlines())
    required={'基金代碼','基金名稱','ISINCODE','計價幣別','日期','基金淨值(金額)'}
    if not required<=set(reader.fieldnames or []):raise ValueError('RECENT_SOURCE_SCHEMA_INVALID')
    values=defaultdict(set);identity_reviews=0;identity_details=[];unavailable=0;total=0
    with closing(sqlite3.connect(root/'research.sqlite',timeout=30)) as db:
        db.execute('BEGIN');imported={r[0] for r in db.execute('SELECT DISTINCT class_key FROM source_snapshots')}
        for r in reader:
            total+=1
            if total>55000:raise ValueError('RECENT_SOURCE_ROW_LIMIT')
            q=queue.get(r['基金代碼'].strip())
            if not q or q['class_key'] not in imported:continue
            if (r['ISINCODE'].strip()!=q['isin'] or norm(r['基金名稱'])!=norm(q['name']) or
                not (norm(r['計價幣別'])==norm(basic[q['code']]['計價幣別']) or r['計價幣別'].strip()==q['currency'])):
                identity_reviews+=1
                identity_details.append({'code':q['code'],'date':r['日期'].strip(),
                  'expected':{'isin':q['isin'],'name':q['name'],'currency':q['currency']},
                  'recent':{'isin':r['ISINCODE'].strip(),'name':r['基金名稱'],'currency':r['計價幣別'].strip()},
                  'status':'IDENTITY_FIELDS_REQUIRE_REVIEW','prices_changed':False})
                continue
            d=r['日期'].strip()
            try:
                day=date(int(d[:4]),int(d[4:6]),int(d[6:])) if re.fullmatch(r'\d{8}',d) else date.fromisoformat(d.replace('/','-'))
                number=Decimal(r['基金淨值(金額)'].strip())
            except (ValueError,InvalidOperation):unavailable+=1;continue
            if not START<=day<=END:continue
            if not number.is_finite() or not Decimal('0')<number<Decimal('1000000000'):unavailable+=1;continue
            values[(q['class_key'],day.isoformat())].add(number)
        matched=0;missing=[];conflicts=[];recent_conflicts=[]
        bykey={q['class_key']:q for q in queue.values()}
        for key,numbers in sorted(values.items()):
            brief={'code':bykey[key[0]]['code'],'date':key[1]}
            if len(numbers)!=1:recent_conflicts.append(brief);continue
            prices=db.execute('SELECT nav_per_unit FROM research_observations WHERE class_key=? AND valuation_date=?',key).fetchall()
            if not prices:missing.append(brief)
            elif any(Decimal(row[0])!=next(iter(numbers)) for row in prices):conflicts.append(brief)
            else:matched+=1
    result={'checked_at_utc':stamp(),'recent_source_url':'https://opendata.tdcc.com.tw/getOD.ashx?id=3-4','recent_raw_sha256':sha(raw),
      'same_class_same_date_equal_values':matched,'missing_history_but_available_in_official_recent':missing,'conflicting_values':conflicts,
      'recent_source_duplicate_value_conflicts':recent_conflicts,'identity_review_rows':identity_reviews,'identity_review_details':identity_details,'unavailable_recent_rows':unavailable,
      'rows_modified_or_backfilled':0,'calendar_completeness_approved':False,'commercial_rights_verified':False}
    frozen.write_bytes(raw);(root/'recent-reconciliation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    return {k:(len(v) if isinstance(v,list) else v) for k,v in result.items()}

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',required=True);p.add_argument('--basic');p.add_argument('--catalog');p.add_argument('--recent');p.add_argument('--selectors',action='store_true');p.add_argument('--audit',action='store_true');p.add_argument('--limit',type=int,default=100);p.add_argument('--all-batches',action='store_true');p.add_argument('--pooled',action='store_true');p.add_argument('--workers',type=int,default=8);a=p.parse_args()
    root=Path(a.root);root.mkdir(parents=True,exist_ok=True)
    if a.selectors:
        build_queue(root,a.basic)
        if a.catalog:print(json.dumps(bind_catalog_snapshot(root,a.catalog),ensure_ascii=False))
    elif a.audit:
        print(json.dumps(audit_store(root),ensure_ascii=False,indent=2))
        if a.recent:print(json.dumps(reconcile_recent_source(root,a.recent),ensure_ascii=False,indent=2))
    else:
        transport=enable_pooled_transport() if a.pooled else None
        try:
            while True:
                r=import_queue(root,workers=a.workers,limit=a.limit)
                print(json.dumps(r,ensure_ascii=False,indent=2),flush=True)
                if not a.all_batches or r['paused_reason'] or sum(r['acquisition_status_counts'].values())>=r['catalog_classes']:break
        finally:
            if transport:transport.close()
