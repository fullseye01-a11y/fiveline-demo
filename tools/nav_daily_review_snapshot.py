"""Archive *unapproved* government NAV matching candidates from Render.

GitHub Actions artifacts last 30 days; they are not durable historical NAV,
not a complete daily provider feed, and never update investor portfolios.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
import argparse
import csv
import json

ENDPOINT="https://fiveline-nav-readonly.onrender.com/api/official-nav/match-report"
COLUMNS=[
    "legacy_id","legacy_name","legacy_ccy","legacy_nav_end","status",
    "official_source","official_code","official_isin","official_name",
    "official_ccy","official_nav_date","official_nav","approved",
]

def fetch_report() -> dict:
    request=Request(ENDPOINT,headers={"Accept":"application/json","User-Agent":"FivelineReviewArchive/0.1"})
    with urlopen(request,timeout=110) as response:
        if response.status!=200:
            raise ValueError("NAV matching review API is not healthy")
        return json.load(response)

def checked_report(raw: dict) -> dict:
    if raw.get("mode")!="read_only_matching_review":
        raise ValueError("Unexpected review report mode")
    rows=raw.get("records")
    if raw.get("legacy_count")!=68 or not isinstance(rows,list) or len(rows)!=68:
        raise ValueError("Expected 68 historical instruments")
    if len({str(x.get("id")) for x in rows})!=68:
        raise ValueError("Duplicate legacy IDs")
    if raw.get("reviewed_and_approved_count")!=0 or raw.get("any_legacy_nav_overwritten") is not False:
        raise ValueError("Legacy OLS integrity gate failed")
    if any(x.get("approved") is not False for x in rows):
        raise ValueError("Matching records must be unapproved")
    if set(raw.get("provider_success",[]))!={"sitca_domestic_daily","tdcc_offshore_global"}:
        raise ValueError("Both official sources must be available for archival check")
    if raw.get("provider_errors"):
        raise ValueError("Unexpected missing provider")
    if sum(raw.get("candidate_status_counts",{}).values())!=68:
        raise ValueError("Status totals inconsistent")
    return raw

def extract_candidates(report: dict) -> list[dict]:
    records=[]
    for row in report["records"]:
        c=row["candidates"][0] if row.get("status")=="single_unverified_candidate" and row.get("candidate_count")==1 and len(row.get("candidates") or [])==1 else None
        records.append({
            "legacy_id":row["id"],
            "legacy_name":row["name"],
            "legacy_ccy":row["currency"],
            "legacy_nav_end":row["nav_end"],
            "status":row["status"],
            "official_source":c.get("source","") if c else "",
            "official_code":c.get("code","") if c else "",
            "official_isin":c.get("isin","") if c else "",
            "official_name":c.get("name","") if c else "",
            "official_ccy":c.get("currency","") if c else "",
            "official_nav_date":c.get("nav_date","") if c else "",
            "official_nav":c.get("nav","") if c else "",
            "approved":"NO",
        })
    return records

def csv_safe(value):
    value=str(value if value is not None else "")
    if value.lstrip().startswith(("=","+","-","@")):
        value="'"+value
    return value

def write_archive(report: dict, directory: Path, created_utc: datetime | None = None):
    report=checked_report(report)
    created_utc=created_utc or datetime.now(timezone.utc)
    directory.mkdir(parents=True,exist_ok=True)
    rows=extract_candidates(report)
    stamp=created_utc.strftime("%Y%m%dT%H%M%SZ")
    data={
        "schema_version":1,
        "archive_created_utc":created_utc.isoformat(),
        "source_endpoint":ENDPOINT,
        "type":"unapproved_daily_review_snapshot",
        "source_snapshot_checked_at_utc":report.get("checked_at_utc"),
        "retention_note":"GitHub Actions artifact retains for 30 days only; not a permanent historical NAV database",
        "historical_nav_backfilled":False,
        "all_candidates_unapproved":True,
        "provider_success":report["provider_success"],
        "candidate_status_counts":report["candidate_status_counts"],
        "rows":rows,
    }
    json_path=directory/("nav_review_"+stamp+".json")
    csv_path=directory/("nav_review_"+stamp+".csv")
    json_path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    with csv_path.open("w",encoding="utf-8-sig",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows([{k:csv_safe(row.get(k,"")) for k in COLUMNS} for row in rows])
    return json_path,csv_path

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--output-dir",type=Path,default=Path("tmp_nav_review"))
    args=p.parse_args()
    report=fetch_report()
    a,b=write_archive(report,args.output_dir)
    print(json.dumps({"archive_json":str(a),"archive_csv":str(b),
         "counts":report["candidate_status_counts"],"provider_success":report["provider_success"],
         "historical_nav_backfilled":False},ensure_ascii=False))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
