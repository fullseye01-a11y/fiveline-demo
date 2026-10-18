"""Public SITCA daily-query capture into isolated, unapproved research storage.

No portfolio mapping, ISIN inference, calendar completion or legal approval.
Dates bind to the submitted date and response's form echo, not an independently
verified row-level date. Public response bytes are retained outside the repo.
"""
from datetime import date
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import build_opener, HTTPCookieProcessor, HTTPSHandler, Request
from urllib.parse import urlencode
from http.cookiejar import CookieJar
import json
import re
import sqlite3
import ssl
import time

URL = 'https://www.sitca.org.tw/ROC/Industry/IN2106.aspx?pid=IN2213_02'
MAX_BYTES = 8_000_000
HEADERS = ['類型代號','公司代號','公司名稱','受益憑證代號','基金統編','基金名稱','幣別','淨值','前一日淨值','漲跌']
DATE_FIELD = 'ctl00$ContentPlaceHolder1$txtQ_Date'


class CaptureError(ValueError):
    pass


class _Page(HTMLParser):
    def __init__(self):
        super().__init__(); self.inputs={}; self.rows=[]; self.row=None; self.cell=None
    def handle_starttag(self, tag, attrs):
        attrs=dict(attrs)
        if tag=='input' and attrs.get('name'):
            self.inputs[attrs['name']]=(attrs.get('type',''),attrs.get('value',''))
        if tag=='tr':self.row=[];self.cell=None
        elif tag in ('td','th') and self.row is not None:self.cell=[]
    def handle_data(self, data):
        if self.cell is not None:self.cell.append(data)
    def handle_endtag(self, tag):
        # Legacy SITCA headers open <td> but close </th>.
        if tag in ('td','th') and self.row is not None and self.cell is not None:
            self.row.append(''.join(self.cell).strip());self.cell=None
        elif tag=='tr' and self.row is not None:
            self.rows.append(self.row);self.row=None;self.cell=None


def parse_capture(raw, requested_day, *, minimum_rows=1000):
    if type(requested_day) is not date or not isinstance(raw,bytes) or not 0<len(raw)<=MAX_BYTES:
        raise CaptureError('CAPTURE_DATE_OR_SIZE_INVALID')
    page=_Page()
    try:page.feed(raw.decode('utf-8-sig'))
    except (UnicodeError,ValueError):raise CaptureError('CAPTURE_ENCODING_INVALID') from None
    if page.inputs.get(DATE_FIELD,('', ''))[1]!=requested_day.strftime('%Y%m%d'):
        raise CaptureError('REQUESTED_DATE_NOT_ECHOED')
    headers=[n for n,r in enumerate(page.rows) if r==HEADERS]
    if len(headers)!=1:raise CaptureError('PER_UNIT_NAV_TABLE_NOT_UNIQUE')
    observations=[];seen=set();unavailable=0;missing_certificate=0
    for row in page.rows[headers[0]+1:]:
        if len(row)!=10:continue
        kind,company,company_name,certificate,taxid,name,ccy,nav,previous,change=row
        if (not name or not re.fullmatch(r'A\d{4}',company) or
                not taxid or not re.fullmatch(r'[A-Z]{3}',ccy)):
            raise CaptureError('PUBLIC_SOURCE_CLASS_IDENTITY_INVALID')
        if not certificate:missing_certificate+=1
        key=json.dumps([company,certificate,taxid,name,ccy],ensure_ascii=False,separators=(',',':'))
        if key in seen:raise CaptureError('DUPLICATE_PUBLIC_CLASS_ROW')
        seen.add(key)
        try:n=Decimal(nav.replace(',',''))
        except InvalidOperation:n=None
        if n is None or not n.is_finite() or not Decimal(0)<n<Decimal('1000000000'):
            unavailable+=1;continue
        observations.append({'source_class_key':sha256(key.encode()).hexdigest(),
                             'company':company,'certificate_code':certificate,'tax_id':taxid,
                             'name':name,'currency':ccy,'nav_per_unit':str(n)})
    if len(seen)<minimum_rows:raise CaptureError('PUBLIC_DAILY_TABLE_COVERAGE_TOO_SMALL')
    if len(seen)>24000:raise CaptureError('PUBLIC_DAILY_TABLE_TOO_LARGE')
    canonical=json.dumps(observations,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
    return {'requested_date':requested_day.isoformat(),'source_url':URL,
            'raw_sha256':sha256(raw).hexdigest(),'table_sha256':sha256(canonical).hexdigest(),
            'source_class_rows':len(seen),'numeric_nav_observations':len(observations),
            'unavailable_or_nonpositive_rows':unavailable,
            'missing_certificate_code_rows':missing_certificate,
            'date_binding':'REQUEST_AND_FORM_ECHO_ONLY',
            'independent_row_date_verified':False,'class_identity_approved':False,
            'commercial_data_rights_verified':False,'three_year_history_ready':False,
            'observations':observations}


def capture_days(days, root, *, fetcher=None, minimum_rows=1000):
    """Explicit bounded batch, maximum 31 dates; persists after every good date."""
    if (not isinstance(days,list) or not 1<=len(days)<=31 or
            any(type(d) is not date or d>date.today() for d in days) or len(set(days))!=len(days)):
        raise CaptureError('EXPLICIT_DATE_BATCH_INVALID')
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    if fetcher is None:
        context=ssl.create_default_context()
        if hasattr(ssl,'VERIFY_X509_STRICT'):context.verify_flags &= ~ssl.VERIFY_X509_STRICT
        opener=build_opener(HTTPCookieProcessor(CookieJar()),HTTPSHandler(context=context))
        def read(request):
            with opener.open(request,timeout=30) as response:
                if response.status!=200 or response.geturl()!=URL:
                    raise CaptureError('PUBLIC_QUERY_TRANSPORT_MISMATCH')
                raw=response.read(MAX_BYTES+1)
                if len(raw)>MAX_BYTES:raise CaptureError('CAPTURE_RESPONSE_TOO_LARGE')
                return raw
        def fetcher(day):
            page=_Page();page.feed(read(URL).decode('utf-8-sig'))
            fields={k:v for k,(t,v) in page.inputs.items() if t=='hidden'}
            fields.update({DATE_FIELD:day.strftime('%Y%m%d'),
                           'ctl00$ContentPlaceHolder1$ddlQ_Comid':'',
                           'ctl00$ContentPlaceHolder1$BtnQuery':'查詢'})
            return read(Request(URL,data=urlencode(fields).encode()))
    summaries=[]
    with sqlite3.connect(root/'research.sqlite') as db:
        db.execute('''CREATE TABLE IF NOT EXISTS source_snapshots (
            raw_sha256 TEXT PRIMARY KEY, requested_date TEXT NOT NULL,
            table_sha256 TEXT NOT NULL, metadata_json TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS research_observations (
            raw_sha256 TEXT NOT NULL, source_class_key TEXT NOT NULL,
            name TEXT NOT NULL, company TEXT NOT NULL, certificate_code TEXT NOT NULL,
            tax_id TEXT NOT NULL, currency TEXT NOT NULL, nav_per_unit TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status='UNAPPROVED_SOURCE_OBSERVATION'),
            PRIMARY KEY(raw_sha256,source_class_key))''')
        for day in days:
            target=root/(day.isoformat()+'.html')
            reused=target.exists()
            raw=target.read_bytes() if reused else fetcher(day)
            captured=parse_capture(raw,day,minimum_rows=minimum_rows)
            summary={k:v for k,v in captured.items() if k!='observations'}
            prior_dates=db.execute('SELECT requested_date FROM source_snapshots WHERE table_sha256=?',
                                   (summary['table_sha256'],)).fetchall()
            if any(value[0]!=summary['requested_date'] for value in prior_dates):
                raise CaptureError('IDENTICAL_TABLE_FOR_DIFFERENT_DATES_REQUIRES_REVIEW')
            with db:
                db.execute('INSERT OR IGNORE INTO source_snapshots VALUES (?,?,?,?)',
                           (summary['raw_sha256'],summary['requested_date'],summary['table_sha256'],json.dumps(summary)))
                for item in captured['observations']:
                    db.execute('INSERT OR IGNORE INTO research_observations VALUES (?,?,?,?,?,?,?,?,?)',
                        (summary['raw_sha256'],item['source_class_key'],item['name'],item['company'],
                         item['certificate_code'],item['tax_id'],item['currency'],item['nav_per_unit'],
                         'UNAPPROVED_SOURCE_OBSERVATION'))
            if not reused:
                temporary=target.with_suffix('.part');temporary.write_bytes(raw);temporary.replace(target)
            summary['reused_local_snapshot']=reused
            summaries.append(summary)
            if not reused and len(days)>1:time.sleep(0.5)
    report={'schema':'fiveline_sitca_unapproved_history_capture_v1','snapshots':summaries,
            'captured_dates':len(summaries),
            'numeric_nav_observations':sum(x['numeric_nav_observations'] for x in summaries),
            'three_year_complete_funds':0,'official_history_approved_rows':0,
            'portfolio_or_six_strategies_modified':False}
    (root/'capture-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    return report


def _cli():
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dates',nargs='+',required=True)
    p.add_argument('--root',required=True)
    args=p.parse_args()
    print(json.dumps(capture_days([date.fromisoformat(d) for d in args.dates],args.root),ensure_ascii=False,indent=2))


if __name__=='__main__':_cli()
