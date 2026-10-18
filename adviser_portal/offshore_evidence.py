"""Official TDCC offshore 7-day NAV and distribution *metadata* evidence.

Identity gate: catalog UID -> TDCC 3-2 basics -> TDCC 3-4 recent unit
NAV, requiring the same unique code + valid ISIN + normalized complete class
name + currency. TDCC 3-5 lacks ISIN and does NOT prove cash amount per share:
only matching event dates / frequency are previewed, never total return.

Research-only: no customer information, no portfolio valuation, no backtest,
no DB writes. Each complete official-source CSV is fetched once per hour only
after the user explicitly requests that evidence.
"""
from __future__ import annotations

from datetime import datetime, date, timezone
from decimal import Decimal, InvalidOperation
from io import StringIO
from threading import Lock
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import csv
import re
import ssl
import time
import unicodedata

from .nav_evidence import choose_instrument

REGISTRY = {
    "basic": {
        "url":"https://opendata.tdcc.com.tw/getOD.ashx?id=3-2",
        "dataset":"https://data.gov.tw/dataset/11639",
        "required":("基金代碼","基金ISIN_CODE","基金名稱","計價幣別"),
    },
    "recent": {
        "url":"https://opendata.tdcc.com.tw/getOD.ashx?id=3-4",
        "dataset":"https://data.gov.tw/dataset/11641",
        "required":("基金代碼","ISINCODE","基金名稱","計價幣別","日期","基金淨值(金額)"),
    },
    "dividend": {
        "url":"https://opendata.tdcc.com.tw/getOD.ashx?id=3-5",
        "dataset":"https://data.gov.tw/dataset/11642",
        "required":("基金代碼","基金名稱","基金幣別","除息日","配息頻率"),
    },
}
MAX_BYTES=36_000_000
MAX_ROWS=55_000
MIN_RECORDS=250
TTL_SECONDS=3600
MAX_AGE_DAYS=14
_locks={key:Lock() for key in REGISTRY}
_snapshots={}
_saved_at={}


class OffshoreUnavailable(Exception):
    pass


def _norm_name(value):
    value=unicodedata.normalize("NFKC",str(value or "")).casefold().replace("臺","台")
    return "".join(x for x in value if x.isalnum())


CCYS={
    "美元":"USD","美金":"USD","USD":"USD",
    "歐元":"EUR","EUR":"EUR",
    "澳幣":"AUD","澳元":"AUD","AUD":"AUD",
    "日幣":"JPY","日圓":"JPY","JPY":"JPY",
    "新臺幣":"TWD","新台幣":"TWD","臺幣":"TWD","台幣":"TWD","NTD":"TWD","TWD":"TWD",
    "英鎊":"GBP","GBP":"GBP",
    "港幣":"HKD","HKD":"HKD",
    "人民幣":"CNY","CNY":"CNY","RMB":"CNY",
    "南非幣":"ZAR","ZAR":"ZAR",
    "加幣":"CAD","加元":"CAD","CAD":"CAD",
    "紐幣":"NZD","紐元":"NZD","NZD":"NZD",
    "瑞士法郎":"CHF","CHF":"CHF",
}
def _ccy(value):
    text=unicodedata.normalize("NFKC",str(value or "")).strip().upper()
    return CCYS.get(text,"")


def _date(value):
    value=str(value or "").strip().replace("年","/").replace("月","/").replace("日","")
    try:
        if re.fullmatch(r"\d{8}",value):
            return date(int(value[:4]),int(value[4:6]),int(value[6:]))
        if re.fullmatch(r"\d{7}",value):
            y=int(value[:3])
            if y<80 or y>200:return None
            return date(1911+y,int(value[3:5]),int(value[5:]))
        match=re.fullmatch(r"(\d{2,4})[-/](\d{1,2})[-/](\d{1,2})",value)
        if not match:return None
        y,m,d=(int(x) for x in match.groups())
        if y<1911:
            if y<80 or y>200:return None
            y+=1911
        return date(y,m,d)
    except ValueError:
        return None


def valid_isin(value):
    isin=str(value or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]",isin):
        return False
    expansion="".join(str(ord(c)-55) if c.isalpha() else c for c in isin)
    total=0
    for i,char in enumerate(expansion[::-1]):
        d=int(char)
        if i%2:d*=2
        if d>9:d-=9
        total+=d
    return total%10==0


def _decimal(value):
    try:
        n=Decimal(str(value or "").strip().replace(",",""))
    except (InvalidOperation,ValueError):
        return None
    return n if n.is_finite() and Decimal("0")<n<Decimal("1000000000") else None


def parse_official_csv(key, raw):
    if key not in REGISTRY:
        raise ValueError("unknown TDCC public source")
    if not isinstance(raw,bytes) or not raw or len(raw)>MAX_BYTES:
        raise OffshoreUnavailable("official TDCC response empty/oversize")
    text=None
    for encoding in ("utf-8-sig","cp950","big5"):
        try:
            text=raw.decode(encoding)
            break
        except UnicodeDecodeError:
            pass
    if text is None or "<html" in text[:500].lower() or "<!doctype" in text[:500].lower():
        raise OffshoreUnavailable("official TDCC response not text CSV")
    reader=csv.DictReader(StringIO(text))
    fields=tuple(str(x or "").strip() for x in (reader.fieldnames or ()))
    if len(fields)!=len(set(fields)) or not set(REGISTRY[key]["required"]).issubset(fields):
        raise OffshoreUnavailable("official TDCC CSV fields changed")
    items=[]
    for row in reader:
        if not isinstance(row,dict) or None in row:
            raise OffshoreUnavailable("official TDCC malformed CSV row")
        items.append({str(k).strip():str(v or "").strip() for k,v in row.items()})
        if len(items)>MAX_ROWS:
            raise OffshoreUnavailable("official TDCC CSV row limit exceeded")
    if len(items)<MIN_RECORDS:
        raise OffshoreUnavailable("official TDCC CSV coverage unexpectedly small")
    return items


def download(key):
    if key not in REGISTRY:raise ValueError("unknown TDCC dataset")
    req=Request(REGISTRY[key]["url"],headers={
        "User-Agent":"FivelineAdviserResearch/0.2",
        "Accept":"text/csv,text/plain,*/*","Accept-Encoding":"identity",
        "Connection":"close",
    })
    try:
        with urlopen(req,timeout=32,context=ssl.create_default_context()) as res:
            dest=urlsplit(res.geturl())
            if (res.status!=200 or dest.scheme!="https" or
                dest.hostname!="opendata.tdcc.com.tw" or dest.path!="/getOD.ashx" or
                dest.query!=urlsplit(REGISTRY[key]["url"]).query):
                raise OffshoreUnavailable("official TDCC transport / redirect mismatch")
            raw=res.read(MAX_BYTES+1)
    except (OSError,TimeoutError) as exc:
        raise OffshoreUnavailable("official TDCC source temporarily unreachable") from exc
    return parse_official_csv(key,raw)


def snapshot(key, fetcher=None):
    if key not in REGISTRY:raise ValueError("unknown TDCC dataset")
    if fetcher is not None:
        return fetcher(key)
    with _locks[key]:
        now=time.monotonic()
        if key not in _snapshots or now-_saved_at.get(key,0)>=TTL_SECONDS:
            fresh=download(key)
            _snapshots[key]=fresh
            _saved_at[key]=time.monotonic()
        return _snapshots[key]


def _identity(row):
    if row["category"]!="offshore_fund_share_class" or row["source"]!="tdcc_offshore_funds":
        raise ValueError("非境外基金級別")
    code=str(row["code"]).strip().upper()
    isin=str(row["isin"]).strip().upper()
    currency=_ccy(row["currency"])
    fundname=_norm_name(row["name"])
    if not code or not valid_isin(isin) or not currency or not fundname:
        return None
    return code,isin,currency,fundname


def _review(row,reason):
    return {
      "status":"REVIEW_REQUIRED","instrument_uid":row["uid"],
      "display_name":row["name"],"code":row["code"],"isin":row["isin"],
      "currency":row["currency"],"nav":None,"nav_date":None,
      "recent_nav_days":[],"dividend_event_count":None,
      "reason":reason,"class_identity_approved":False,
      "usable_for_portfolio":False,"usable_for_backtest":False,
      "cash_dividend_per_share_verified":False,
    }


def _eligible_rows(rows, ident, isin_field, currency_field):
    code,isin,ccy,name=ident
    return [r for r in rows if
        str(r.get("基金代碼","")).upper()==code
        and str(r.get(isin_field,"")).strip().upper()==isin
        and _ccy(r.get(currency_field,""))==ccy
        and _norm_name(r.get("基金名稱",""))==name]


def latest_nav(uid, fetcher=None, today=None):
    row=choose_instrument(uid)
    if row["category"]!="offshore_fund_share_class":
        raise ValueError("非境外基金標的")
    ident=_identity(row)
    if ident is None:
        return _review(row,"基金代碼、ISIN 校驗碼、原幣別或完整級別名稱不足，不能自動配對。")
    basic=snapshot("basic",fetcher=fetcher)
    bs=_eligible_rows(basic,ident,"基金ISIN_CODE","計價幣別")
    if len(bs)!=1:
        return _review(row,"TDCC 基本資料未能唯一確認代碼、ISIN、級別名稱及幣別。")
    recent=snapshot("recent",fetcher=fetcher)
    matched=_eligible_rows(recent,ident,"ISINCODE","計價幣別")
    latest={}
    for r in matched:
        day=_date(r.get("日期"))
        nav=_decimal(r.get("基金淨值(金額)"))
        if day is None or nav is None:continue
        k=day.isoformat()
        if k in latest and latest[k]!=nav:
            return _review(row,"官方同級別、同日期 NAV 有互相衝突的紀錄，需人工審核。")
        latest[k]=nav
    if not latest:
        return _review(row,"官方最近七日 NAV 檔未找到相同 ISIN、幣別及名稱的有效級別。")
    day=max(latest)
    asof=today or datetime.now(timezone.utc).date()
    age=(asof-date.fromisoformat(day)).days
    if age<0:
        return _review(row,"資料日期晚於目前時間，拒絕判定為最新淨值。")
    return {
      "status":"OFFSHORE_RECENT_NAV_EVIDENCE_ONLY",
      "instrument_uid":row["uid"],"display_name":row["name"],
      "code":row["code"],"isin":row["isin"],
      "currency":ident[2],"nav":str(latest[day]),"nav_date":day,
      "age_calendar_days":age,"stale_warning":age>MAX_AGE_DAYS,
      "recent_nav_days":sorted(latest,reverse=True)[:7],
      "source":"TDCC","source_datasets":[REGISTRY[k]["dataset"] for k in ("basic","recent")],
      "dividend_event_count":None,
      "reason":"已依 TDCC 基本資料與最近七日 NAV 交叉核對，但非歷史含息序列或正式客戶基金級別核准。",
      "class_identity_approved":False,"usable_for_portfolio":False,
      "usable_for_backtest":False,"cash_dividend_per_share_verified":False,
    }


def dividend_events(uid, fetcher=None, today=None):
    row=choose_instrument(uid)
    if row["category"]!="offshore_fund_share_class":
        raise ValueError("非境外基金標的")
    ident=_identity(row)
    if ident is None:
        return _review(row,"ISIN 或幣別未通過基本校驗，無法配對配息日期。")
    basic_records=snapshot("basic",fetcher=fetcher)
    basics=_eligible_rows(basic_records,ident,"基金ISIN_CODE","計價幣別")
    code,isin,ccy,name=ident
    # The dividend 3-5 file has NO ISIN. Another basic share class with
    # identical code+name+currency but different ISIN would be inseparable.
    same_without_isin=[r for r in basic_records
        if str(r.get("基金代碼","")).upper()==code
        and _ccy(r.get("計價幣別",""))==ccy
        and _norm_name(r.get("基金名稱",""))==name]
    if len(basics)!=1 or len(same_without_isin)!=1:
        return _review(row,"配息來源沒有 ISIN，官方基本資料的代碼、名稱、幣別不足以唯一確認級別。")
    records=snapshot("dividend",fetcher=fetcher)
    selected=[]
    seen={}
    for r in records:
        if (str(r.get("基金代碼","")).upper()!=code or
            _ccy(r.get("基金幣別",""))!=ccy or
            _norm_name(r.get("基金名稱",""))!=name):
            continue
        ex=_date(r.get("除息日"))
        if ex is None:continue
        d=ex.isoformat()
        freq=str(r.get("配息頻率",""))[:45]
        if d in seen:
            if seen[d]!=freq:
                return _review(row,"相同除息日出現不同配息頻率紀錄，暫停配對。")
            continue
        seen[d]=freq
        selected.append({"ex_date":d,"frequency":freq})
    selected.sort(key=lambda x:x["ex_date"],reverse=True)
    return {
        "status":"OFFSHORE_DIVIDEND_DATE_METADATA_ONLY",
        "instrument_uid":row["uid"],"display_name":row["name"],
        "code":row["code"],"isin":row["isin"],"currency":ccy,
        "nav":None,"nav_date":None,
        "event_count":len(selected),
        "events":selected[:10],
        "source":"TDCC","source_dataset":REGISTRY["dividend"]["dataset"],
        "reason":"官方配息日期檔缺少 ISIN；僅以已確認的基本級別代碼、名稱、幣別提供事件參考，不能推算每單位配息額或含息報酬。",
        "class_identity_approved":False,"usable_for_portfolio":False,
        "usable_for_backtest":False,"cash_dividend_per_share_verified":False,
        "dividend_adjustment_ready":False,
    }
