"""Conservative government fund identity, recent NAV and dividend metadata evidence.

This produces research evidence, NOT share-class approval, historical NAV backfill,
dividend reinvested adjusted NAV, or performance results.
SITCA domestic and TDCC offshore open sources have distinct identifier systems.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from io import StringIO
from urllib.request import urlopen, Request
from urllib.parse import urlencode
import argparse
import csv
import hashlib
import json
import re
import ssl

from backend.providers.open_fund_nav import parse_nav_date, decode_csv
from backend.fund_matching import normalize_currency, normalize_name

REGISTRY={
    "tdcc_offshore_basics":{
        "url":"https://opendata.tdcc.com.tw/getOD.ashx?id=3-2",
        "dataset":"https://data.gov.tw/dataset/11639",
        "required":("基金代碼","基金ISIN_CODE","基金名稱","計價幣別"),
        "scope":"Fund identity metadata; does not validate original Excel ISIN."
    },
    "tdcc_offshore_recent_nav":{
        "url":"https://opendata.tdcc.com.tw/getOD.ashx?id=3-4",
        "dataset":"https://data.gov.tw/dataset/11641",
        "required":("基金代碼","基金名稱","日期","基金淨值(金額)","ISINCODE","計價幣別"),
        "scope":"Rolling recent seven-day NAV only, NOT multi-year historical NAV."
    },
    "tdcc_offshore_dividend":{
        "url":"https://opendata.tdcc.com.tw/getOD.ashx?id=3-5",
        "dataset":"https://data.gov.tw/dataset/11642",
        "required":("基金代碼","基金名稱","基金幣別","除息日"),
        "scope":"Distribution dates and breakdown metadata; per-unit cash dividend amount NOT guaranteed."
    },
}
MAX_BYTES=35_000_000


def fetch_official(key:str)->bytes:
    if key not in REGISTRY: raise ValueError("unknown official source")
    req=Request(REGISTRY[key]["url"],headers={"User-Agent":"FiveLinePublicResearch/0.2","Accept":"text/csv,*/*"})
    context=ssl.create_default_context()
    with urlopen(req,timeout=35,context=context) as response:
        raw=response.read(MAX_BYTES+1)
    if not raw or len(raw)>MAX_BYTES: raise ValueError("empty or excessively large official CSV")
    return raw


def load_csv(key,raw):
    cfg=REGISTRY[key]
    csv_text=decode_csv(raw)
    reader=csv.DictReader(StringIO(csv_text))
    headings=tuple((x or "").strip() for x in (reader.fieldnames or ()))
    absent=[x for x in cfg["required"] if x not in headings]
    if absent:raise ValueError(f"source schema changed ({key}): missing {','.join(absent)}")
    records=[]
    for row in reader:
        if row:
            records.append({str(k).strip():str(v or "").strip() for k,v in row.items() if k is not None})
    if not records:raise ValueError("no official data rows")
    return headings,records


def valid_isin(s):
    """ISO 6166 check digit; empty/invalid means insufficient evidence."""
    s=str(s or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]",s):return False
    digits="".join(str(ord(x)-55) if x.isalpha() else x for x in s)
    total=0
    reverse=digits[::-1]
    for i,ch in enumerate(reverse):
        n=int(ch)
        if i%2==1:
            n*=2
            if n>9:n-=9
        total+=n
    return total%10==0


def clean_id(rec,isin_field="基金ISIN_CODE"):
    return (str(rec.get("基金代碼") or "").strip(), str(rec.get(isin_field) or "").strip().upper(),
            normalize_currency(rec.get("計價幣別") or rec.get("基金幣別") or ""))


def numeric_nav(text):
    s=str(text or "").replace(",","").strip()
    try:
        v=Decimal(s)
        if v.is_finite() and v>0:return v
    except InvalidOperation:pass
    return None


def evidence_review(report:dict,basic_rows:list[dict],nav_rows:list[dict],div_rows:list[dict],checked_at=None):
    if report.get("mode")!="read_only_matching_review" or report.get("legacy_count")!=68 or report.get("reviewed_and_approved_count")!=0:
        raise ValueError("unapproved matching report required")
    if len(report.get("records",[]))!=68 or any(x.get("approved") is not False for x in report["records"]):
        raise ValueError("unapproved 68 instruments required")

    basic_by_code=defaultdict(list)
    for x in basic_rows:basic_by_code[x.get("基金代碼","")].append(x)
    nav_by_code=defaultdict(list)
    for x in nav_rows:nav_by_code[x.get("基金代碼","")].append(x)
    div_by_code=defaultdict(list)
    for x in div_rows:div_by_code[x.get("基金代碼","")].append(x)

    rows=[]
    counts=Counter()
    for legacy in report["records"]:
        item={"legacy_id":legacy["id"],"legacy_name":legacy["name"],"legacy_ccy":legacy["currency"],
              "legacy_nav_end":legacy["nav_end"],
              "base_status":legacy["status"],"approved":False,
              "isin_verified_against_original_excel":False,
              "share_class_approved":False,"dividend_adjustment_ready":False,
              "split_adjustment_ready":False,"historical_backfill_ready":False,
              "same_code_isin_evidence":False,"recent_nav_days":0,
              "official_dividend_event_count":0,
              "reference_isin":"","official_code":"",
              "reason":""}
        if legacy.get("status")!="single_unverified_candidate" or legacy.get("candidate_count")!=1 or len(legacy.get("candidates") or [])!=1:
            item["status"]="not_a_unique_candidate"
            item["reason"]="Legacy identity is not uniquely screened; manual identity review required."
            rows.append(item);counts[item["status"]]+=1;continue
        c=legacy["candidates"][0]
        code=c.get("code","")
        ccy=normalize_currency(c.get("currency",""))
        isin=str(c.get("isin","")).strip().upper()
        item["official_code"]=code
        item["reference_isin"]=isin
        if c.get("source")!="tdcc_offshore_global":
            item["status"]="domestic_class_reference_missing"
            item["reason"]="Domestic share-class identity needs separate official certificate/unit-code evidence; TDCC offshore joins not applicable."
            rows.append(item);counts[item["status"]]+=1;continue
        if not valid_isin(isin):
            item["status"]="isin_missing_or_invalid"
            item["reason"]="TDCC global overview ISIN absent or checksum invalid; must not infer class."
            rows.append(item);counts[item["status"]]+=1;continue
        ref_name=normalize_name(c.get("name"))
        baseline=(code,isin,ccy)
        basics=[
          x for x in basic_by_code.get(code,[])
          if clean_id(x)==baseline and normalize_name(x.get("基金名稱"))==ref_name
        ]
        recent=[
          x for x in nav_by_code.get(code,[])
          if clean_id(x,"ISINCODE")==baseline and normalize_name(x.get("基金名稱"))==ref_name
          and parse_nav_date(x.get("日期",""))
          and numeric_nav(x.get("基金淨值(金額)")) is not None
        ]
        if len(basics)!=1:
            item["status"]="basic_identity_missing_or_ambiguous"
            item["reason"]="TDCC offshore basic list did not uniquely confirm code, ISIN, class name, and currency."
            rows.append(item);counts[item["status"]]+=1;continue
        if not recent:
            item["status"]="recent_nav_identity_missing"
            item["reason"]="Recent 7-day TDCC NAV file has no corresponding code+ISIN+name+currency row."
            rows.append(item);counts[item["status"]]+=1;continue
        item["same_code_isin_evidence"]=True
        days=sorted({parse_nav_date(x["日期"]).isoformat() for x in recent})
        item["recent_nav_days"]=len(days)
        item["recent_nav_first"]=days[0]
        item["recent_nav_last"]=days[-1]
        # This file describes distribution events and components; it does not
        # reliably supply cash payout per share for NAV adjustments.
        div=[
          x for x in div_by_code.get(code,[])
          if normalize_name(x.get("基金名稱"))==ref_name
          and normalize_currency(x.get("基金幣別"))==ccy
          and parse_nav_date(x.get("除息日",""))
        ]
        item["official_dividend_event_count"]=len(div)
        item["status"]="official_metadata_consistent_review_pending"
        item["reason"]="Cross-file official code+ISIN/class identity consistent, but original Excel ISIN/share class is not proven; distributions and split adjustments are not verified."
        rows.append(item);counts[item["status"]]+=1

    result={
      "schema":"fiveline_nav_identity_dividend_evidence_v1",
      "checked_at_utc":checked_at or datetime.now(timezone.utc).isoformat(),
      "source_datasets":{k:{"url":v["dataset"],"scope":v["scope"]} for k,v in REGISTRY.items()},
      "total_legacy":68,
      "status_counts":dict(counts),
      "official_basic_rows":len(basic_rows),
      "official_recent_nav_rows":len(nav_rows),
      "official_dividend_metadata_rows":len(div_rows),
      "reviewed_approved":0,
      "official_metadata_consistent_not_approved":counts["official_metadata_consistent_review_pending"],
      "historical_nav_backfilled":False,
      "cash_distributions_adjusted":False,
      "splits_adjusted":False,
      "records":rows,
      "warning":"No official metadata evidence alone permits merge into historic Excel OLS, total return or six strategies."
    }
    if len(rows)!=68 or sum(counts.values())!=68:raise ValueError("invalid review output")
    return result


def main():
    cli=argparse.ArgumentParser(description="Read-only official share class/NAV/dividend metadata cross-check")
    cli.add_argument("--out",default="nav_identity_evidence.json")
    cli.add_argument("--report",default="https://fiveline-nav-readonly.onrender.com/api/official-nav/match-report")
    args=cli.parse_args()
    if args.report!="https://fiveline-nav-readonly.onrender.com/api/official-nav/match-report":
        raise ValueError("Only approved public backend report is supported")
    with urlopen(Request(args.report,headers={"Accept":"application/json"}),timeout=115) as res:
        report=json.load(res)
    loaded={}
    for key in REGISTRY:
        hdr,rows=load_csv(key,fetch_official(key))
        loaded[key]=rows
        print(json.dumps({"source":key,"rows":len(rows),"header":hdr[:20],"sha256":hashlib.sha256(json.dumps(rows,sort_keys=True,ensure_ascii=False).encode()).hexdigest()},ensure_ascii=False))
    audit=evidence_review(report,loaded["tdcc_offshore_basics"],
                          loaded["tdcc_offshore_recent_nav"],loaded["tdcc_offshore_dividend"])
    from pathlib import Path
    path=Path(args.out);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(audit,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({k:v for k,v in audit.items() if k!="records"},ensure_ascii=False))
    print("AUDIT_REPORT_PATH",str(path))
if __name__=="__main__":main()
