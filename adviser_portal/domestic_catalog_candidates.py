"""Offline current-catalog candidates for isolated SITCA observations.

Explicit HTML line breaks separate regulatory notes from the complete class
name. Only unique exact normalized legal-name/currency candidates are used to
look up current certificate/tax/currency keys. Historical continuity is never
approved, and source price identities and values are never rewritten.
"""
from collections import Counter,defaultdict
from contextlib import closing
from hashlib import sha256
from pathlib import Path
import json,re,sqlite3,unicodedata
from datetime import date,datetime
from adviser_portal.sitca_history_capture import _Page,HEADERS,DATE_FIELD
from adviser_portal.offshore_evidence import _norm_name,_ccy

class LegalPage(_Page):
    def handle_starttag(self,tag,attrs):
        if tag=='br' and self.cell is not None:self.cell.append('\u241e')
        super().handle_starttag(tag,attrs)

def current_candidates(raw,catalog,minimum_rows=1000):
    if not isinstance(raw,bytes) or not 0<len(raw)<=8_000_000:raise ValueError('SOURCE_HTML_SIZE_INVALID')
    page=LegalPage();page.feed(raw.decode('utf-8-sig'))
    stamp=page.inputs.get(DATE_FIELD,('',''))[1]
    if not re.fullmatch(r'\d{8}',stamp):raise ValueError('CURRENT_SOURCE_QUERY_DATE_REQUIRED')
    source_day=date(int(stamp[:4]),int(stamp[4:6]),int(stamp[6:]))
    catalog_day=datetime.fromisoformat(catalog['checked_at_utc'].replace('Z','+00:00')).date()
    if not 0<=(catalog_day-source_day).days<=7:raise ValueError('CURRENT_SOURCE_CATALOG_DATE_MISMATCH')
    headers=[i for i,row in enumerate(page.rows) if row==HEADERS]
    if len(headers)!=1:raise ValueError('SOURCE_TABLE_HEADER_NOT_UNIQUE')
    rows=[r for r in page.rows[headers[0]+1:] if len(r)==10]
    if not minimum_rows<=len(rows)<=24000:raise ValueError('SOURCE_TABLE_COVERAGE_INVALID')
    byname=defaultdict(list)
    for item in catalog['instruments']:
        if item.get('source')=='sitca_domestic_funds' and item.get('category')=='domestic_fund_share_class':
            byname[(_norm_name(item['name']),_ccy(item['currency']))].append(item)
    found=[];reviews=[]
    for row in rows:
        kind,company,company_name,certificate,tax,name,currency,*_=row
        base={'certificate_code':certificate,'tax_id':tax,'currency':currency,'company':company,'full_source_name':name.replace('\u241e','')}
        if not re.fullmatch(r'A\d{4}',company) or not tax or not certificate or not re.fullmatch('[A-Z]{3}',currency):
            reviews.append({**base,'reason':'SOURCE_KEYS_INCOMPLETE'});continue
        pieces=name.split('\u241e');legal=pieces[0].strip()
        tails=[unicodedata.normalize('NFKC',t).strip() for t in pieces[1:] if t.strip()]
        if not all(t.lstrip('(').startswith(('本基金','本子基金','基金之','基金有','保護型保本基金')) for t in tails):
            reviews.append({**base,'reason':'UNRECOGNIZED_LINEBREAK_CONTENT'});continue
        options=byname[(_norm_name(legal),currency)]
        if len(options)!=1:
            reviews.append({**base,'legal_name':legal,'reason':'NO_UNIQUE_COMPLETE_LEGAL_NAME_CURRENCY_MATCH'});continue
        item=options[0]
        found.append({**base,'legal_name':legal,'catalog_uid':item['uid'],'catalog_code':item['code'],'catalog_name':item['name'],
                      'status':'CURRENT_COMPLETE_LEGAL_NAME_CURRENCY_CANDIDATE','identity_approved':False})
    uid_counts=Counter(x['catalog_uid'] for x in found)
    key_counts=Counter((x['certificate_code'],x['tax_id'],x['currency']) for x in found)
    unique=[]
    for item in found:
        if uid_counts[item['catalog_uid']]!=1 or key_counts[(item['certificate_code'],item['tax_id'],item['currency'])]!=1:
            reviews.append({**item,'reason':'NONUNIQUE_BIDIRECTIONAL_CLASS_CANDIDATE'})
        else:unique.append(item)
    return unique,reviews,len(rows),source_day.isoformat()

def build_candidates(database,raw,catalog_bytes,output_folder,minimum_rows=1000,registry_raw=None):
    catalog=json.loads(catalog_bytes);current,reviews,source_rows,source_day=current_candidates(raw,catalog,minimum_rows)
    lookup={(r['certificate_code'],r['tax_id'],r['currency']):r for r in current}
    source_sha=sha256(raw).hexdigest();catalog_sha=sha256(catalog_bytes).hexdigest()
    folder=Path(output_folder);folder.mkdir(parents=True,exist_ok=True)
    if not Path(database).is_file():raise ValueError('EXISTING_ISOLATED_RESEARCH_DATABASE_REQUIRED')
    with closing(sqlite3.connect(database,timeout=30)) as db:
        snapshot=db.execute('SELECT requested_date FROM source_snapshots WHERE raw_sha256=?',(source_sha,)).fetchone()
        if not snapshot or snapshot[0]!=source_day:raise ValueError('SOURCE_HTML_NOT_BOUND_TO_IMPORTED_SNAPSHOT')
        if db.execute("SELECT 1 FROM research_observations WHERE status<>'UNAPPROVED_SOURCE_OBSERVATION' LIMIT 1").fetchone():raise ValueError('ONLY_UNAPPROVED_RESEARCH_OBSERVATIONS_SUPPORTED')
        db.execute('''CREATE TABLE IF NOT EXISTS research_catalog_candidates (
          source_class_key TEXT PRIMARY KEY,catalog_uid TEXT,catalog_code TEXT,
          source_html_sha256 TEXT NOT NULL,catalog_file_sha256 TEXT NOT NULL,
          status TEXT NOT NULL,identity_approved INTEGER NOT NULL CHECK(identity_approved=0))''')
        classes=db.execute('SELECT DISTINCT source_class_key,certificate_code,tax_id,currency FROM research_observations').fetchall()
        matched=0
        for key,certificate,tax,currency in classes:
            candidate=lookup.get((certificate,tax,currency));matched+=candidate is not None
            db.execute('INSERT OR REPLACE INTO research_catalog_candidates VALUES(?,?,?,?,?,?,0)',
              (key,candidate['catalog_uid'] if candidate else None,candidate['catalog_code'] if candidate else None,source_sha,catalog_sha,
               'CURRENT_CERTIFICATE_TAX_CURRENCY_CANDIDATE_HISTORICAL_CONTINUITY_UNAPPROVED' if candidate else 'NO_CURRENT_CLASS_CANDIDATE'))
        db.commit()
        grouped=db.execute('''SELECT b.catalog_uid,count(*),count(DISTINCT s.requested_date),min(s.requested_date),max(s.requested_date)
          FROM research_observations r JOIN source_snapshots s ON r.raw_sha256=s.raw_sha256
          JOIN research_catalog_candidates b ON r.source_class_key=b.source_class_key
          WHERE b.catalog_uid IS NOT NULL GROUP BY b.catalog_uid''').fetchall()
        actual=db.execute('SELECT count(*) FROM research_observations').fetchone()[0]
    byuid={r[0]:r for r in grouped};funds=[];registry_status=Counter();bycert=defaultdict(list)
    if registry_raw is not None:
        from adviser_portal.research_isin_crosswalk import parse_registry
        for item in parse_registry(registry_raw):bycert[item['certificate_code']].append(item)
    for item in current:
        data=byuid.get(item['catalog_uid']);observations=data[1] if data else 0;dates=data[2] if data else 0
        candidates=bycert[item['certificate_code']]
        chosen=candidates[0] if len(candidates)==1 else None
        isin_status=('REGISTRY_NOT_SUPPLIED' if registry_raw is None else
                     'NO_UNIQUE_CURRENT_REGISTRY_CANDIDATE' if chosen is None else
                     'EXACT_CERTIFICATE_AND_LEGAL_NAME_CANDIDATE' if _norm_name(chosen['registry_name'])==_norm_name(item['legal_name']) else
                     'EXACT_CERTIFICATE_CANDIDATE_NAME_CONTINUITY_REVIEW')
        if registry_raw is not None:registry_status[isin_status]+=1
        funds.append({**item,'candidate_isin':chosen['isin'] if chosen else None,'isin_status':isin_status,
          'research_observations':observations,'distinct_requested_dates':dates,
          'first_requested_date':data[3] if data else None,'last_requested_date':data[4] if data else None,
          'duplicate_candidate_date_review':observations!=dates,'independent_row_valuation_dates_verified':False,
          'historical_name_continuity_approved':False,'usable_for_portfolio':False})
    linked_uids={item['catalog_uid'] for item in current}
    unlinked=[item for item in catalog['instruments'] if item.get('source')=='sitca_domestic_funds' and item.get('category')=='domestic_fund_share_class' and item['uid'] not in linked_uids]
    for item in unlinked:
        funds.append({'catalog_uid':item['uid'],'catalog_code':item['code'],'catalog_name':item['name'],'currency':_ccy(item['currency']),
          'status':'NO_CURRENT_SOURCE_CLASS_CANDIDATE','research_observations':None,'distinct_requested_dates':None,
          'identity_approved':False,'historical_name_continuity_approved':False,'usable_for_portfolio':False})
    result={'schema':'sitca_current_catalog_candidates_v1','source_html_sha256':source_sha,'source_requested_date':source_day,'catalog_file_sha256':catalog_sha,
      'current_source_class_rows':source_rows,'current_unique_catalog_candidates':len(current),'current_source_rows_requiring_review':len(reviews),
      'catalog_records_retained':len(funds),'catalog_records_without_current_candidate':len(unlinked),
      'historical_source_class_records':len(classes),'historical_source_classes_with_current_candidate':matched,
      'catalog_candidates_with_research_observations':sum((f['research_observations'] or 0)>0 for f in funds),
      'candidate_linked_observations':sum(f['research_observations'] or 0 for f in funds),'total_research_observations':actual,
      'candidate_duplicate_date_reviews':sum(f.get('duplicate_candidate_date_review',False) for f in funds),
      'current_registry_candidate_status_counts':dict(registry_status),'registry_raw_sha256':sha256(registry_raw).hexdigest() if registry_raw is not None else None,
      'identities_approved':0,'historical_price_rows_rewritten':0,'valuation_calendars_approved':False,'commercial_rights_verified':False}
    (folder/'catalog-candidate-report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    (folder/'catalog-current-candidates.json').write_text(json.dumps(funds,ensure_ascii=False,indent=2))
    (folder/'catalog-source-identity-review.json').write_text(json.dumps(reviews,ensure_ascii=False,indent=2))
    return result

if __name__=='__main__':
    import argparse,gzip
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--database',required=True);p.add_argument('--source-html',required=True);p.add_argument('--catalog',required=True)
    p.add_argument('--registry');p.add_argument('--output',required=True);a=p.parse_args()
    path=Path(a.source_html)
    if path.suffix=='.gz':
        with gzip.open(path,'rb') as f:raw=f.read(8_000_001)
    else:raw=path.read_bytes()
    print(json.dumps(build_candidates(a.database,raw,Path(a.catalog).read_bytes(),a.output,
                     registry_raw=Path(a.registry).read_bytes() if a.registry else None),ensure_ascii=False,indent=2))
