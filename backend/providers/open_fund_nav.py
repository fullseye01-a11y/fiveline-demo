"""Read-only licensed-government NAV source probe; never writes NAV or revalues portfolios.

Sources: data.gov.tw/dataset/11109 (SITCA domestic daily NAV)
         data.gov.tw/dataset/37818 (TDCC offshore global fund overview)
Probing validates transport and schema; it does not certify share-class matching.
"""
from __future__ import annotations
import argparse
import csv
from datetime import date, timedelta
from io import StringIO
import json
import re
from urllib.request import Request, urlopen

SOURCES = {
    "sitca_domestic_daily": {
        "url": "https://www.sitca.org.tw/MemberK0000/F/03/nav.csv",
        "origin": "https://data.gov.tw/dataset/11109",
        "required": (("基金名稱",), ("基金淨值", "淨值"), ("日期",)),
        "nav_fields": ("基金淨值", "淨值"),
        "date_fields": ("日期",),
    },
    "tdcc_offshore_global": {
        "url": "https://opendata.tdcc.com.tw/getOD.ashx?id=3-20",
        "origin": "https://data.gov.tw/dataset/37818",
        "required": (("基金名稱",), ("淨值日期",), ("最新淨值小計(總額)",)),
        "nav_fields": ("最新淨值小計(總額)",),
        "date_fields": ("淨值日期",),
    },
}
MAX_BYTES = 8_000_000
USER_AGENT = "FivelineNAVProvenanceCheck/0.1 (read-only; public open-data dataset)"


def download_source(source: str) -> bytes:
    url = SOURCES[source]["url"]
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/csv,application/octet-stream,*/*"})
    with urlopen(request, timeout=20) as response:
        data = response.read(MAX_BYTES+1)
    if len(data) > MAX_BYTES:
        raise ValueError("Official CSV exceeds read-only probe size limit")
    if not data.strip():
        raise ValueError("Official provider returned empty CSV")
    return data


def decode_csv(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "cp950", "big5"):
        try:
            value = raw.decode(encoding)
            if "<html" in value[:350].lower() or "<!doctype" in value[:350].lower():
                raise ValueError("Provider returned an HTML page rather than CSV")
            return value
        except UnicodeDecodeError:
            continue
    raise ValueError("Unknown official CSV character encoding")


def parse_nav_date(raw: str) -> date | None:
    s = str(raw or "").strip().replace("年","/").replace("月","/").replace("日","")
    if re.fullmatch(r"\d{8}",s):
        yy,mm,dd=int(s[:4]),int(s[4:6]),int(s[6:8])
    elif re.fullmatch(r"\d{7}",s):
        yy,mm,dd=int(s[:3]),int(s[3:5]),int(s[5:7])
    else:
        parts = re.match(r"^(\d{2,4})[-/]([01]?\d)[-/]([0-3]?\d)$", s)
        if not parts:
            return None
        yy,mm,dd = (int(x) for x in parts.groups())
    if yy < 1911:
        if yy < 80:
            return None
        yy+=1911
    try:
        return date(yy,mm,dd)
    except ValueError:
        return None


def inspect_csv(source: str, raw: bytes) -> dict:
    if source not in SOURCES:
        raise ValueError("Unknown licensed CSV source")
    config = SOURCES[source]
    data = decode_csv(raw)
    reader = csv.DictReader(StringIO(data))
    keys = tuple((x or "").strip() for x in (reader.fieldnames or []))
    fieldset = set(keys)
    matched = all(any(name in fieldset for name in names) for names in config["required"])
    if not matched:
        return {
            "source":source, "source_dataset":config["origin"],
            "schema_verified":False, "row_count":0,
            "header_fields":list(keys[:20]),
            "warning":"Official CSV columns differ from expected schema; do not publish any NAV."
        }
    date_field = next(k for k in config["date_fields"] if k in fieldset)
    nav_field = next(k for k in config["nav_fields"] if k in fieldset)
    count=0
    recent=None
    scanned=0
    date_samples=[]
    value_samples=[]
    for row in reader:
        if not row:
            continue
        scanned+=1
        if len(date_samples)<3:
            date_samples.append(str(row.get(date_field,"") or "")[:40])
            value_samples.append(str(row.get(nav_field,"") or "")[:40])
        raw_date=parse_nav_date(row.get(date_field,""))
        val=str(row.get(nav_field,"") or "").strip().replace(",","")
        if raw_date and val and re.match(r"^\d+(?:\.\d+)?$",val):
            count+=1
            if recent is None or raw_date>recent:
                recent=raw_date
    return {
        "source":source, "source_dataset":config["origin"],
        "schema_verified":True, "parsed_nav_rows":count,
        "scanned_rows":scanned,
        "first_date_samples":date_samples,
        "first_nav_samples":value_samples,
        "most_recent_nav_date":recent.isoformat() if recent else None,
        "readiness":"source_schema_only_no_fund_mapping",
        "warning":"No ISIN/share-class matching, settlement checks, split adjustment, dividend reinvestment, or production NAV updates.",
    }


def main() -> int:
    cli=argparse.ArgumentParser(description="Read-only government fund NAV CSV connectivity test")
    cli.add_argument("provider",choices=SOURCES.keys())
    args=cli.parse_args()
    try:
        data=download_source(args.provider)
        result=inspect_csv(args.provider,data)
        result["transport_ok"]=True
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0 if result["schema_verified"] and result.get("parsed_nav_rows",0)>0 else 2
    except Exception as exc:
        print(json.dumps({"source":args.provider,"transport_ok":False,
                          "error":type(exc).__name__+": "+str(exc)[:180],
                          "warning":"No NAV is published or overwritten."},
                         ensure_ascii=False,indent=2))
        return 3


if __name__=="__main__":
    raise SystemExit(main())
