"""Current official open-end ISIN candidates for imported research identities.

Exact certificate codes only. No guessed ISIN, fuzzy merge, historical name
continuity approval or portfolio eligibility. Current registry issue dates are
security issue dates, not an inferred fund inception date.
"""
from collections import Counter,defaultdict
from hashlib import sha256
from pathlib import Path
import json
import re
import sqlite3

from .sitca_history_capture import _Page
from .offshore_evidence import valid_isin,_norm_name

URL='https://isin.twse.com.tw/isin/C_public.jsp?strMode=7'


def parse_registry(raw,*,minimum_records=1000):
    if not isinstance(raw,bytes) or not 0<len(raw)<=4_000_000:raise ValueError('ISIN_REGISTRY_SIZE_INVALID')
    text=None
    for encoding in ('utf-8-sig','cp950','big5hkscs'):
        try:text=raw.decode(encoding);break
        except UnicodeError:continue
    if text is None:raise ValueError('ISIN_REGISTRY_ENCODING_INVALID')
    page=_Page();page.feed(text)
    headers=[r for r in page.rows if len(r)==5 and 'ISIN Code' in r[1]]
    if len(headers)!=1:raise ValueError('ISIN_REGISTRY_HEADER_NOT_UNIQUE')
    items=[]
    for row in page.rows:
        if len(row)!=5 or row in headers:continue
        found=re.fullmatch(r'(T[A-Z0-9]{5})\s+(.+)',row[0])
        if not found:continue
        if not valid_isin(row[1]):raise ValueError('OFFICIAL_REGISTRY_ISIN_CHECKSUM_INVALID')
        if not re.fullmatch(r'\d{4}/\d{2}/\d{2}',row[2]):raise ValueError('REGISTRY_ISSUE_DATE_INVALID')
        items.append({'certificate_code':found[1],'registry_name':found[2],
                      'isin':row[1],'security_issue_date':row[2]})
    if not minimum_records<=len(items)<=12000:raise ValueError('ISIN_REGISTRY_COVERAGE_INVALID')
    return items


def build_crosswalk(db_file,raw,*,minimum_records=1000):
    registry=parse_registry(raw,minimum_records=minimum_records)
    by_code=defaultdict(list)
    for item in registry:by_code[item['certificate_code']].append(item)
    source_sha=sha256(raw).hexdigest()
    counts=Counter()
    with sqlite3.connect(db_file,timeout=30) as db:
        classes=db.execute('SELECT DISTINCT source_class_key,certificate_code,name,currency FROM research_observations').fetchall()
        db.execute('''CREATE TABLE IF NOT EXISTS research_isin_candidates (
            source_class_key TEXT PRIMARY KEY, certificate_code TEXT NOT NULL,
            currency TEXT NOT NULL, candidate_isin TEXT, registry_name TEXT,
            registry_sha256 TEXT NOT NULL, status TEXT NOT NULL,
            identity_approved INTEGER NOT NULL CHECK(identity_approved=0))''')
        with db:
            for key,code,name,currency in classes:
                candidates=by_code.get(code,[])
                chosen=candidates[0] if len(candidates)==1 else None
                status=('MISSING_CERTIFICATE' if not code else
                        'CERTIFICATE_NOT_IN_CURRENT_REGISTRY' if not candidates else
                        'AMBIGUOUS_REGISTRY_CERTIFICATE' if chosen is None else
                        'EXACT_CERTIFICATE_AND_NORMALIZED_NAME_CANDIDATE' if _norm_name(name)==_norm_name(chosen['registry_name']) else
                        'CERTIFICATE_MATCH_NAME_CONTINUITY_REVIEW')
                counts[status]+=1
                db.execute('INSERT OR REPLACE INTO research_isin_candidates VALUES (?,?,?,?,?,?,?,0)',
                           (key,code,currency,chosen['isin'] if chosen else None,
                            chosen['registry_name'] if chosen else None,source_sha,status))
    return {'schema':'fiveline_research_current_isin_candidates_v1','source_url':URL,
            'registry_sha256':source_sha,'registry_records':len(registry),
            'research_class_records':len(classes),'candidate_status_counts':dict(counts),
            'historical_class_continuity_approved':False,'fund_inception_inferred_from_issue_date':False,
            'portfolio_or_six_strategies_modified':False,'three_year_complete_funds':0}


def _cli():
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--database',required=True);p.add_argument('--registry',required=True)
    args=p.parse_args()
    raw=Path(args.registry).read_bytes()
    report=build_crosswalk(args.database,raw)
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':_cli()
