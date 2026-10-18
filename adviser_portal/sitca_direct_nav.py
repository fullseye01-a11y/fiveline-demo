"""Bounded on-demand SITCA official daily *per-unit* NAV CSV fallback.

This adapter never reads portfolios or writes historical prices; it validates
the official HTTPS source and hard-fails on schema, dates or unit ambiguity.
Only a one-hour in-process snapshot is cached; no automatic polling.
"""
from __future__ import annotations

import csv
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from io import StringIO
from threading import Lock
import re
import ssl
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

URL = "https://www.sitca.org.tw/MemberK0000/F/03/nav.csv"
ORIGIN = "https://data.gov.tw/dataset/11109"
MAX_BYTES = 8_000_000
MIN_VALID_ROWS = 1_000
TTL_SEC = 3600
_lock = Lock()
_cache = None
_since = 0.0


class SITCAUnavailable(Exception):
    pass


def parse_date(raw):
    v = str(raw or "").strip().replace("年", "/").replace("月", "/").replace("日", "")
    parts = re.fullmatch(r"(\d{2,4})[/\-](\d{1,2})[/\-](\d{1,2})", v)
    if parts:
        y,m,d=(int(x) for x in parts.groups())
    elif re.fullmatch(r"\d{7}",v):
        y,m,d=int(v[:3]),int(v[3:5]),int(v[5:])
    elif re.fullmatch(r"\d{8}",v):
        y,m,d=int(v[:4]),int(v[4:6]),int(v[6:])
    else:
        return None
    if y < 1911:
        if y<80 or y>200:
            return None
        y+=1911
    try:
        return date(y,m,d).isoformat()
    except ValueError:
        return None


def _utf8_or_big5(body):
    for codepage in ("utf-8-sig","cp950","big5"):
        try:
            decoded=body.decode(codepage)
        except UnicodeDecodeError:
            continue
        head=decoded[:200].casefold()
        if "<!doctype" in head or "<html" in head:
            raise SITCAUnavailable("official CSV returned an HTML page")
        return decoded
    raise SITCAUnavailable("official CSV text encoding not recognized")


def validate_csv(body):
    if not body or len(body)>MAX_BYTES:
        raise SITCAUnavailable("official CSV missing or oversized")
    rows=csv.DictReader(StringIO(_utf8_or_big5(body)))
    fields=[str(x or "").strip() for x in (rows.fieldnames or ())]
    names=set(fields)
    name_field="基金名稱"
    code_field="基金代號"
    ccy_field="幣別"
    date_field="日期"
    nav_field=next((x for x in ("基金淨值","淨值") if x in names),None)
    if (not nav_field or not all(x in names for x in
                                (name_field,code_field,ccy_field,date_field))):
        raise SITCAUnavailable("official CSV per-unit NAV source schema changed")
    obs={}
    count=0
    for row in rows:
        if not isinstance(row,dict) or None in row:
            raise SITCAUnavailable("official CSV malformed row")
        name=str(row.get(name_field,"") or "").strip()
        code=str(row.get(code_field,"") or "").strip()
        currency=str(row.get(ccy_field,"") or "").strip()
        day=parse_date(row.get(date_field))
        nav_text=str(row.get(nav_field,"") or "").replace(",","").strip()
        if not name or not code or not currency or not day:
            continue
        try:
            nav=Decimal(nav_text)
        except (ValueError,InvalidOperation):
            continue
        if not nav.is_finite() or not (Decimal("0")<nav<=Decimal("1000000000")):
            continue
        count+=1
        key=(name,code,currency)
        item={"name":name,"code":code,"currency":currency,
              "nav":str(nav),"nav_date":day,"isin":"","source":"sitca_domestic_daily"}
        previous=obs.get(key)
        if previous is None or item["nav_date"]>previous["nav_date"]:
            obs[key]=item
        elif item["nav_date"]==previous["nav_date"] and item["nav"]!=previous["nav"]:
            raise SITCAUnavailable("official source has conflicting same-day NAV observations")
    if count < MIN_VALID_ROWS or len(obs)<MIN_VALID_ROWS:
        raise SITCAUnavailable("official source has too few valid unit NAV records")
    return {
        "origin":ORIGIN,
        "checked_at_utc":datetime.now(timezone.utc).isoformat(),
        "source_count":len(obs),
        "items":list(obs.values()),
    }


def download():
    req=Request(URL,headers={
        "User-Agent":"Mozilla/5.0 (compatible; FivelineFundResearch/1.0)",
        "Accept":"text/csv,text/plain,*/*","Accept-Encoding":"identity",
        "Connection":"close",
    })
    context=ssl.create_default_context()
    # SITCA official legacy chain: retain hostname / CA checking but disable
    # X509_STRICT compatibility only for this verified official host.
    if hasattr(ssl,"VERIFY_X509_STRICT"):
        context.verify_flags &= ~ssl.VERIFY_X509_STRICT
    try:
        with urlopen(req,timeout=25,context=context) as resp:
            final=urlsplit(resp.geturl())
            if (resp.status!=200 or final.scheme!="https" or
                    final.hostname!="www.sitca.org.tw" or
                    final.path!="/MemberK0000/F/03/nav.csv"):
                raise SITCAUnavailable("unexpected official CSV transport")
            body=resp.read(MAX_BYTES+1)
    except (OSError,TimeoutError) as exc:
        raise SITCAUnavailable("official SITCA file unavailable") from exc
    return validate_csv(body)


def snapshot(fetcher=None):
    global _cache,_since
    if fetcher is not None:
        return fetcher()
    with _lock:
        t=time.monotonic()
        if _cache is None or t-_since>=TTL_SEC:
            checked=download()
            _cache=checked
            _since=t
        return _cache


def lookup(row, fetcher=None):
    found=snapshot(fetcher)
    if found.get("origin")!=ORIGIN or not isinstance(found.get("items"),list):
        raise SITCAUnavailable("official SITCA identity snapshot not verified")
    # Deliberately require the exact three identity fields; the input row is
    # already selected by an exact vetted catalog UID from the trusted cache.
    matches=[x for x in found["items"] if
             x["name"]==row["name"] and x["code"]==row["code"] and
             x["currency"]==row["currency"]]
    return {
        "mode":"government_open_data_latest_only",
        "source":"sitca_domestic_daily","source_dataset":ORIGIN,
        "matched_to_legacy_fiveline":False,
        "last_verified_at_utc":found["checked_at_utc"],
        "records":matches[:2],
    }
