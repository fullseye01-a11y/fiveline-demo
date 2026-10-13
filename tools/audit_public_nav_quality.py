"""Fail-closed quality assessment of archived PUBLIC, unapproved official NAV snapshots.

This is an observation completeness report, NOT a claim about trading-day NAV
completeness, official corrections, adjusted total return, or approved identities.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, date, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
import argparse
import csv
import json

from tools.public_nav_history import SNAPSHOT_SCHEMA, DATASET_URLS, validate_date

REPORT_SCHEMA="public_nav_archive_quality_v1"
MAX_SNAPSHOTS=100000

def parse_datetime(s: str) -> datetime:
    try:
        t=datetime.fromisoformat(str(s))
    except (ValueError,TypeError) as exc:
        raise ValueError("invalid UTC collection timestamp") from exc
    if t.tzinfo is None:raise ValueError("collection timestamp missing UTC offset")
    return t.astimezone(timezone.utc)

def canonical_hash(events:list[dict])->str:
    text=json.dumps(events,ensure_ascii=False,sort_keys=True,separators=(",",":"))
    return sha256(text.encode("utf-8")).hexdigest()

def load_coverage(path:Path)->dict[str,dict]:
    with path.open("r",encoding="utf-8-sig",newline="") as f:
        reader=csv.DictReader(f)
        rows=list(reader)
    if len(rows)!=68 or len({r["標的ID"] for r in rows})!=68:
        raise ValueError("Excel legacy coverage is not exactly 68 unique instruments")
    return {r["標的ID"]:r for r in rows}

def read_verified_archive(path:Path)->dict:
    raw=json.loads(path.read_text(encoding="utf-8"))
    if (raw.get("schema")!=SNAPSHOT_SCHEMA
        or raw.get("all_candidates_approved") is not False
        or raw.get("legacy_history_modified") is not False):
        raise ValueError("unsupported or approved archive")
    events=raw.get("observations")
    if not isinstance(events,list) or len(events)!=raw.get("candidate_count"):
        raise ValueError("archive row count invalid")
    if canonical_hash(events)!=raw.get("observations_sha256"):
        raise ValueError("tampered archive SHA-256")
    stamp=parse_datetime(raw["collected_at_utc"])
    local=validate_date(raw["collection_local_date"])
    if abs((stamp.date()-local).days)>1:
        raise ValueError("archive collection date inconsistent with timestamp")
    valid_coverage=set(DATASET_URLS)
    seen=set()
    for event in events:
        if event.get("identity_status")!="name_currency_candidate_unapproved":
            raise ValueError("unexpected identity approval or missing flag")
        source=event.get("official_source")
        if source not in valid_coverage or event.get("official_dataset")!=DATASET_URLS[source]:
            raise ValueError("bad provenance in archive")
        for key in ("legacy_instrument_id","official_code","official_name","official_currency"):
            if not str(event.get(key) or "").strip():
                raise ValueError("empty fund identity key")
        navday=validate_date(event["nav_date"])
        if navday>stamp.date():
            raise ValueError("future NAV effective date")
        try: v=Decimal(str(event.get("nav_raw") or ""))
        except InvalidOperation as exc:raise ValueError("invalid NAV") from exc
        if not v.is_finite() or v<=0:raise ValueError("non-positive NAV")
        identity=(source,str(event["official_code"]),str(event.get("official_isin") or ""),str(event["official_currency"]))
        key=(identity,navday.isoformat())
        if key in seen:raise ValueError("duplicate fund and effective date within same snapshot")
        seen.add(key)
    raw["_verified_stamp"]=stamp
    return raw

def assess(paths:list[Path],legacy_csv:Path,checked_at:datetime|None=None)->dict:
    files=sorted(paths)
    if not files or len(files)>MAX_SNAPSHOTS:
        raise ValueError("expected at least one valid public NAV snapshot")
    coverage=load_coverage(legacy_csv)
    refs={}
    collection_ids=set()
    keys_by_legacy=defaultdict(set)
    observations=0
    conflict_keys=set()
    all_days=[]
    date_tally=Counter()
    scan_max=None
    for path in files:
        doc=read_verified_archive(path)
        runid=str(doc.get("run_id") or "").strip()
        collection_key=(doc["collection_local_date"],runid)
        if not runid or collection_key in collection_ids:
            raise ValueError("duplicate/invalid collection run identity")
        collection_ids.add(collection_key)
        time=doc["_verified_stamp"]
        scan_max=max(scan_max,time) if scan_max is not None else time
        for x in doc["observations"]:
            legacy_id=str(x["legacy_instrument_id"])
            if legacy_id not in coverage:
                raise ValueError("snapshot instrument does not belong to legacy 68")
            identity=(str(x["official_source"]),str(x["official_code"]),str(x.get("official_isin") or ""),str(x["official_currency"]))
            keys_by_legacy[legacy_id].add(identity)
            entry=refs.setdefault((legacy_id,identity),{
                "name":coverage[legacy_id]["標的名稱"],
                "legacy_id":legacy_id,
                "source":identity[0],"official_code":identity[1],
                "isin":identity[2],"currency":identity[3],
                "legacy_nav_end":coverage[legacy_id]["NAV截至日期"],
                "source_fund_name":str(x["official_name"]),
                "prices":defaultdict(set),
                "observations":0,
                "capture_dates":set(),
            })
            if entry["source_fund_name"]!=str(x["official_name"]):
                raise ValueError("source class name changed; independent identity review required")
            nav_date=validate_date(x["nav_date"]).isoformat()
            if entry["legacy_nav_end"]:
                validate_date(entry["legacy_nav_end"])
            entry["prices"][nav_date].add(str(Decimal(str(x["nav_raw"])).normalize()))
            entry["observations"]+=1
            entry["capture_dates"].add(doc["collection_local_date"])
            date_tally[nav_date]+=1
            all_days.append(nav_date)
            observations+=1
    now=checked_at or datetime.now(timezone.utc)
    if now.tzinfo is None:raise ValueError("checked_at must be timezone-aware")
    checked_iso=now.astimezone(timezone.utc).isoformat()
    rows=[]
    conflict_fund_ids=set()
    drift_fund_ids=set()
    repeated=0
    total_unique_pairs=0
    for (legacy_id,ident),entry in sorted(refs.items(),key=lambda kv:(int(kv[0][0]),kv[0][1])):
        days=sorted(entry["prices"])
        counts=sum(len(x) for x in entry["prices"].values())
        conflicts=[d for d,values in entry["prices"].items() if len(values)>1]
        repeated+=entry["observations"]-len(days)
        total_unique_pairs+=len(days)
        last=days[-1]
        end=entry["legacy_nav_end"] or None
        # Calendar span != missing market days; there is no trusted holiday calendar here.
        gap=((date.fromisoformat(days[0])-date.fromisoformat(end)).days-1) if end else None
        report={
            "legacy_id":legacy_id,
            "legacy_name":entry["name"],
            "legacy_nav_end":end,
            "official_source":entry["source"],
            "official_code":entry["official_code"],
            "official_isin":entry["isin"],
            "official_currency":entry["currency"],
            "official_name":entry["source_fund_name"],
            "collection_observations":entry["observations"],
            "distinct_capture_dates":len(entry["capture_dates"]),
            "distinct_effective_nav_dates":len(days),
            "first_effective_nav_date":days[0],
            "last_effective_nav_date":last,
            "latest_raw_nav":sorted(entry["prices"][last])[0] if len(entry["prices"][last])==1 else None,
            "repeated_observations":entry["observations"]-len(days),
            "legacy_to_first_observation_calendar_span_days":gap,
            "effective_date_lag_calendar_days":(now.date()-date.fromisoformat(last)).days,
            "conflicting_nav_dates":sorted(conflicts),
            "identity_changed_between_archives":len(keys_by_legacy[legacy_id])>1,
            "approved":False,
            "dividend_split_adjusted":False,
            "continuous_history_verified":False,
        }
        if conflicts:conflict_fund_ids.add(legacy_id)
        if report["identity_changed_between_archives"]:drift_fund_ids.add(legacy_id)
        if conflicts:report["quality"]="conflicting_same_date_nav_blocked"
        elif report["identity_changed_between_archives"]:report["quality"]="fund_identity_drift_blocked"
        elif len(days)==1:report["quality"]="only_one_distinct_nav_date"
        else:report["quality"]="multiple_nav_dates_unapproved"
        rows.append(report)
    known={r["legacy_id"] for r in rows}
    by_status=Counter(row["quality"] for row in rows)
    return {
        "schema":REPORT_SCHEMA,
        "checked_at_utc":checked_iso,
        "latest_archive_collected_at_utc":scan_max.isoformat(),
        "source_history_branch":"official-nav-history",
        "source_license":"https://data.gov.tw/license",
        "source_datasets":DATASET_URLS,
        "legacy_total":68,
        "legacy_with_public_candidate_observations":len(known),
        "legacy_without_candidate_observations":68-len(known),
        "archive_snapshot_files":len(files),
        "total_collection_observations":observations,
        "unique_fund_effective_date_pairs":total_unique_pairs,
        "repeated_collection_observations":repeated,
        "distinct_effective_nav_dates_all_funds":len(date_tally),
        "earliest_effective_nav_date":min(all_days),
        "latest_effective_nav_date":max(all_days),
        "same_day_value_conflict_funds":len(conflict_fund_ids),
        "identity_drift_funds":len(drift_fund_ids),
        "quality_status_counts":dict(by_status),
        "approved_funds":0,
        "historical_backfill_completed":False,
        "dividend_split_adjustment_completed":False,
        "calendar_span_is_not_missing_trading_days":True,
        "notice":"Capture repetitions are not new NAV days. Calendar spans are not trading-day missing counts. All share-class mappings unapproved; no historical backfill.",
        "funds":rows,
    }

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--snapshots",type=Path,default=Path("public_nav_history"))
    p.add_argument("--coverage",type=Path,default=Path("data/phase16_instrument_coverage.csv"))
    p.add_argument("--output",type=Path,default=Path("nav_quality_report.json"))
    args=p.parse_args()
    report=assess(list(args.snapshots.glob("*/*/*.json")),args.coverage)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({key:value for key,value in report.items() if key!="funds"},ensure_ascii=False))
    if report["same_day_value_conflict_funds"] or report["identity_drift_funds"]:
        print("Blocked: manual review required before incorporating changed identity or NAV.")
    return 0

if __name__=="__main__":
    raise SystemExit(main())
