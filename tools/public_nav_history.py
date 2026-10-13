"""Version-controlled PUBLIC official NAV observations; *never* approved investment history.

Only 39 name+currency screened (unapproved) legacy candidates are included.
The archived JSON is public government-open-data attribution, contains no
customer holdings or identity and is written to a separate Git branch.
One snapshot = one collection event, NOT one historical valuation series.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo
import argparse
import json
import os
import re

REPORT_URL = "https://fiveline-nav-readonly.onrender.com/api/official-nav/match-report"
DATASET_URLS={
    "sitca_domestic_daily":"https://data.gov.tw/dataset/11109",
    "tdcc_offshore_global":"https://data.gov.tw/dataset/37818",
}
PUBLIC_LICENSE="https://data.gov.tw/license"
DATE=re.compile(r"^\d{4}-\d{2}-\d{2}$")
SNAPSHOT_SCHEMA="public_official_nav_candidate_snapshot_v1"

def fetch():
    req=Request(REPORT_URL,headers={"Accept":"application/json","User-Agent":"FiveLineGovNAVArchive/1.0"})
    with urlopen(req,timeout=115) as response:
        if response.status != 200: raise RuntimeError("official source HTTP error")
        return json.load(response)

def validate_date(value):
    if not isinstance(value,str) or not DATE.fullmatch(value):
        raise ValueError("date must be ISO yyyy-mm-dd")
    return datetime.strptime(value,"%Y-%m-%d").date()

def build(report: dict, created: datetime | None=None, run_id: str="local") -> dict:
    if report.get("mode")!="read_only_matching_review" or report.get("legacy_count")!=68:
        raise ValueError("expected live read-only 68 instrument report")
    rows=report.get("records")
    if not isinstance(rows,list) or len(rows)!=68 or len({str(x.get("id")) for x in rows})!=68:
        raise ValueError("missing/duplicate historical identity")
    if report.get("reviewed_and_approved_count")!=0 or report.get("any_legacy_nav_overwritten") is not False:
        raise ValueError("legacy history integrity gate failed")
    if set(report.get("provider_success") or [])!=set(DATASET_URLS) or report.get("provider_errors"):
        raise ValueError("official provider incomplete")
    if any(x.get("approved") is not False for x in rows):
        raise ValueError("unapproved-only archive required")
    checked=report.get("checked_at_utc")
    if not isinstance(checked,str) or len(checked)<10:raise ValueError("missing source inspection UTC")
    created=created or datetime.now(timezone.utc)
    if created.tzinfo is None: raise ValueError("collection time must be tz-aware")
    local_date=created.astimezone(ZoneInfo("Asia/Taipei")).date().isoformat()
    events=[]
    seen=set()
    for row in rows:
        if row.get("status")!="single_unverified_candidate":continue
        if row.get("candidate_count")!=1 or len(row.get("candidates") or [])!=1:
            raise ValueError("ambiguous candidate record")
        x=row["candidates"][0]
        source=str(x.get("source") or "")
        code=str(x.get("code") or "").strip()
        isin=str(x.get("isin") or "").strip().upper()
        ccy=str(x.get("currency") or "").strip()
        date=str(x.get("nav_date") or "")
        name=str(x.get("name") or "").strip()
        if source not in DATASET_URLS or not code or not name or not ccy:
            raise ValueError("missing official provenance/identity")
        actual=validate_date(date)
        if actual>created.date():
            raise ValueError("future NAV date")
        try:
            nav=Decimal(str(x.get("nav") or ""))
        except InvalidOperation as e:
            raise ValueError("invalid NAV") from e
        if not nav.is_finite() or nav<=0:
            raise ValueError("NAV must be positive and finite")
        if isin and not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]",isin):
            raise ValueError("malformed official ISIN")
        identity=(source,code,isin,ccy,date)
        # Identical official share classes cannot be archived twice per observation.
        # On duplicated alias mapping, halt pending manual review.
        if identity in seen: raise ValueError("repeated provider identity in candidate mapping")
        seen.add(identity)
        events.append({
            "legacy_instrument_id":str(row["id"]),
            "official_source":source,
            "official_dataset":DATASET_URLS[source],
            "official_code":code,
            "official_isin":isin,
            "official_currency":ccy,
            "official_name":name,
            "nav_date":date,
            "nav_raw":str(nav),
            "identity_status":"name_currency_candidate_unapproved",
        })
    if not events:
        raise ValueError("zero verified-format candidate rows")
    events.sort(key=lambda e:(e["official_source"],e["official_code"],e["legacy_instrument_id"]))
    payload={
        "schema":SNAPSHOT_SCHEMA,
        "collection_local_date":local_date,
        "collected_at_utc":created.astimezone(timezone.utc).isoformat(),
        "source_report_checked_at_utc":checked,
        "run_id":str(run_id),
        "provenance":{"datasets":DATASET_URLS,"license":PUBLIC_LICENSE,"attribution":"金融監督管理委員會證券期貨局／投信投顧公會／臺灣集中保管結算所"},
        "notice":"Public official NAV values from a single inspection. NOT historical backfill, dividend/split-adjusted NAV, approved mappings, customer data, or performance advice.",
        "all_candidates_approved":False,
        "legacy_history_modified":False,
        "candidate_count":len(events),
        "observations":events,
    }
    normalized=json.dumps(events,ensure_ascii=False,sort_keys=True,separators=(",",":"))
    payload["observations_sha256"]=sha256(normalized.encode("utf-8")).hexdigest()
    return payload

def write_snapshot(payload:dict,directory:Path):
    if payload.get("schema")!=SNAPSHOT_SCHEMA:
        raise ValueError("not a public official NAV snapshot")
    day=validate_date(payload["collection_local_date"])
    run_id=payload["run_id"]
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,60}",run_id):
        raise ValueError("invalid run ID")
    path=directory/f"{day.year:04d}"/f"{day.month:02d}"/(day.isoformat()+"_run-"+run_id+".json")
    path.parent.mkdir(parents=True,exist_ok=True)
    data=json.dumps(payload,ensure_ascii=False,indent=2)+"\n"
    if path.exists():
        if path.read_text("utf-8")!=data:
            raise ValueError("existing snapshot differs; never overwrite historical evidence")
        return path
    # Exclusive creation prevents accidental overwrite of an existing run's evidence.
    with path.open("x",encoding="utf-8") as f:f.write(data)
    return path

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--output-dir",type=Path,default=Path("public_nav_history"))
    args=parser.parse_args()
    item=build(fetch(),run_id=os.environ.get("GITHUB_RUN_ID","local"))
    path=write_snapshot(item,args.output_dir)
    print(json.dumps({"snapshot_path":str(path),"public_observations":item["candidate_count"],
                      "collection_date_taipei":item["collection_local_date"],
                      "source_checked":item["source_report_checked_at_utc"],
                      "observations_sha256":item["observations_sha256"],
                      "approved":False,"legacy_modified":False},ensure_ascii=False))
if __name__=="__main__":main()
