"""Non-mutating NAV historical backfill readiness gate for the 68 legacy instruments.

Public GitHub reports contain ONLY *unapproved* government NAV collection evidence.
No customer data or privately reviewed identity manifests may be committed there.
Private evidence can be supplied offline with --evidence for an advisory dry run,
and is never embedded in the public output. There are intentionally no write paths
to the historical Excel/OLS/strategy engine.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlparse

SCHEMA="fiveline_nav_backfill_preflight_v1"
QUALITY_SCHEMA="public_nav_archive_quality_v1"
REQUIRED_FIELDS=(
 "原 Excel 獨立識別／基金級別","ISIN 或境內受監管級別識別",
 "級別與配息類型證據","完整歷史 NAV 與授權",
 "官方交易／估值日期曆","逐單位現金配息證據",
 "分割／合併事件紀錄","逐筆歷史連續性及含息績效對帳",
)
APPROVAL_FLAGS=("identity_approved","share_class_approved","nav_source_license_approved",
                "distribution_coverage_confirmed","split_coverage_confirmed",
                "history_reconciled","performance_reconciled")
OFFICIAL_SOURCES={"sitca_domestic_daily","tdcc_offshore_global"}
CURRENCY_ALIASES={"新臺幣":"TWD","新台幣":"TWD","台幣":"TWD","臺幣":"TWD","美元":"USD",
                   "美金":"USD","南非幣":"ZAR"}

def ccy(value):
    text=str(value or "").strip()
    return CURRENCY_ALIASES.get(text,text.upper())

def iso_date(value):
    if not isinstance(value,str):
        raise ValueError("invalid ISO date")
    x=date.fromisoformat(value)
    if x.isoformat()!=value:raise ValueError("nonstandard date")
    return x

def valid_positive(raw):
    try:
        x=Decimal(str(raw))
        return x.is_finite() and x>0
    except (InvalidOperation,ValueError):
        return False

def valid_nonnegative(raw):
    try:
        x=Decimal(str(raw))
        return x.is_finite() and x>=0
    except (InvalidOperation,ValueError):
        return False

def official_url(url):
    try:
        p=urlparse(str(url or ""))
        return p.scheme=="https" and bool(p.netloc) and not p.username and not p.password
    except ValueError:
        return False

def legacy_rows(path:Path):
    with path.open("r",encoding="utf-8-sig",newline="") as f:
        out=list(csv.DictReader(f))
    if len(out)!=68 or len({x.get("標的ID") for x in out})!=68:
        raise ValueError("legacy coverage must contain 68 unique IDs")
    return {x["標的ID"]:x for x in out}

def verify_quality(doc):
    if doc.get("schema")!=QUALITY_SCHEMA or doc.get("legacy_total")!=68:
        raise ValueError("wrong public NAV quality schema")
    if (doc.get("approved_funds")!=0 or doc.get("historical_backfill_completed") is not False
        or doc.get("dividend_split_adjustment_completed") is not False
        or doc.get("calendar_span_is_not_missing_trading_days") is not True):
        raise ValueError("public archive integrity/approval state changed")
    funds=doc.get("funds")
    if not isinstance(funds,list) or len({x["legacy_id"] for x in funds})!=len(funds):
        raise ValueError("repeated legacy fund ID / corrupt report")
    if len({x["legacy_id"] for x in funds})>68 or len(funds)<1:
        raise ValueError("invalid fund quality observations")
    for f in funds:
        if f.get("approved") is not False or f.get("dividend_split_adjusted") is not False:
            raise ValueError("unexpected approved fund in public observations")
    return funds

def private_evidence_records(evidence):
    if evidence is None:return {}
    if not isinstance(evidence,dict) or evidence.get("schema")!="fiveline_private_nav_evidence_v1":
        raise ValueError("wrong private evidence schema")
    rows=evidence.get("instruments",[])
    if not isinstance(rows,list) or len(rows)>68 or len({str(x.get("legacy_id")) for x in rows})!=len(rows):
        raise ValueError("invalid private evidence identities")
    return {str(x["legacy_id"]):x for x in rows}

def check_bundle(item, history_item, legacy):
    """Evidence is a *dry-run* verification; cannot attest external documents itself.

    Each error is returned as a blocker, not silently ignored. Even a successful
    local check is staging-only pending human signoff in a private client system.
    """
    reasons=[]
    if history_item is None:
        reasons.append("NO_OFFICIAL_CANDIDATE")
        return reasons
    if history_item.get("conflicting_nav_dates"):
        reasons.append("SAME_DATE_NAV_CONFLICT")
    if history_item.get("identity_changed_between_archives"):
        reasons.append("OFFICIAL_IDENTITY_DRIFT")
    if item is None:
        reasons.extend(["INDEPENDENT_EXCEL_CLASS_EVIDENCE_REQUIRED",
                        "LICENSED_HISTORY_AND_CASH_EVENT_EVIDENCE_REQUIRED"])
        return reasons
    match=("official_source","official_code","official_isin","official_currency")
    if any(str(item.get(k,"")).strip()!=str(history_item.get(k,"")).strip() for k in match):
        reasons.append("OFFICIAL_IDENTITY_MISMATCH")
    if ccy(legacy["幣別"])!=ccy(item.get("official_currency")):
        reasons.append("CURRENCY_MISMATCH")
    if not item.get("original_excel_identifier") or not item.get("share_class"):
        reasons.append("ORIGINAL_EXCEL_CLASS_ID_REQUIRED")
    if not official_url(item.get("independent_class_evidence_url")):
        reasons.append("CLASS_EVIDENCE_URL_REQUIRED")
    if not item.get("reviewer") or not item.get("reviewed_at"):
        reasons.append("MANUAL_REVIEW_TRACE_REQUIRED")
    else:
        try:iso_date(item["reviewed_at"])
        except ValueError:reasons.append("MANUAL_REVIEW_DATE_INVALID")
    if str(item.get("distribution_policy","")) not in ("accumulating","distributing"):
        reasons.append("DISTRIBUTION_POLICY_NOT_PROVEN")
    for f in APPROVAL_FLAGS:
        if item.get(f) is not True:reasons.append("REVIEW_FLAG_MISSING_"+f.upper())
    if not official_url(item.get("historical_nav_source_url")):
        reasons.append("HISTORICAL_NAV_SOURCE_REQUIRED")
    cal=item.get("verified_valuation_dates")
    series=item.get("historical_nav")
    if not isinstance(cal,list) or not cal or not isinstance(series,list) or not series:
        reasons.append("HISTORICAL_NAV_SERIES_AND_OFFICIAL_CALENDAR_REQUIRED")
    else:
        try:
            cdates=[iso_date(x) for x in cal]
            if cdates!=sorted(set(cdates)):reasons.append("CALENDAR_DUPLICATE_OR_UNORDERED")
            pairs=[]
            for row in series:
                nd=iso_date(row["nav_date"])
                if not valid_positive(row["nav"]):reasons.append("INVALID_HISTORICAL_NAV");break
                pairs.append(nd)
            if pairs!=sorted(set(pairs)):
                reasons.append("NAV_DATE_DUPLICATE_OR_UNORDERED")
            if pairs!=cdates:
                reasons.append("UNVERIFIED_OR_MISSING_EXPECTED_NAV_DATES")
            old=iso_date(legacy["NAV截至日期"])
            if cdates[0]<=old:reasons.append("NAV_STARTS_BEFORE_LEGACY_END")
            latest=iso_date(history_item["last_effective_nav_date"])
            if cdates[-1]<latest or pairs[-1]<latest:
                reasons.append("NAV_SERIES_NOT_CONTINUOUS_THROUGH_LATEST_SNAPSHOT")
        except (KeyError,TypeError,ValueError):
            reasons.append("INVALID_DATE_OR_NAV_SERIES")
    # Every event must be unit-cash, in fund valuation currency, including
    # explicit empty lists documented as complete by the reviewer.
    divs=item.get("dividend_events")
    splits=item.get("split_events")
    if not isinstance(divs,list):
        reasons.append("CASH_DIVIDEND_EVENTS_NOT_REVIEWED")
    else:
        try:
            for d in divs:
                iso_date(d["ex_date"])
                if ccy(d.get("currency"))!=ccy(legacy["幣別"]) or not valid_nonnegative(d.get("cash_per_unit")):
                    raise ValueError("invalid dividend per share/currency")
                if not official_url(d.get("official_evidence_url")):raise ValueError("missing dividend proof")
        except (TypeError,KeyError,ValueError):
            reasons.append("INVALID_DIVIDEND_EVENT_OR_CURRENCY")
    if not isinstance(splits,list):
        reasons.append("SPLIT_EVENTS_NOT_REVIEWED")
    else:
        try:
            for sp in splits:
                iso_date(sp["effective_date"])
                if not valid_positive(sp.get("units_after_per_before")):
                    raise ValueError("invalid split ratio")
                if not official_url(sp.get("official_evidence_url")):raise ValueError("missing split proof")
        except (TypeError,KeyError,ValueError):
            reasons.append("INVALID_SPLIT_EVENT_OR_PROOF")
    if item.get("distribution_policy")=="accumulating" and divs:
        reasons.append("ACCUMULATING_CLASS_HAS_CASH_DISTRIBUTION_EVENTS")
    if not official_url(item.get("reconciliation_document_url")):
        reasons.append("INDEPENDENT_RECONCILIATION_PROOF_REQUIRED")
    return list(dict.fromkeys(reasons))

def make_report(public_quality:dict,coverage:dict,evidence:dict|None=None):
    live=verify_quality(public_quality)
    mapped=defaultdict(list)
    for entry in live:
        idx=str(entry["legacy_id"])
        if idx not in coverage:raise ValueError("quality fund not among original 68")
        mapped[idx].append(entry)
    priv=private_evidence_records(evidence)
    if any(x not in coverage for x in priv):raise ValueError("evidence contains unknown legacy fund ID")
    summary=Counter()
    lines=[]
    for legacy_id,legacy in sorted(coverage.items(),key=lambda kv:int(kv[0])):
        found=mapped.get(legacy_id,[])
        if len(found)>1:
            reasons=["MULTIPLE_OFFICIAL_IDENTITIES_FOR_ONE_LEGACY_FUND"]
            status="blocked"
        elif not found:
            reasons=["NO_OFFICIAL_CANDIDATE","OFFICIAL_FEED_OR_MARKET_SOURCE_REQUIRED"]
            status="blocked"
        else:
            reasons=check_bundle(priv.get(legacy_id),found[0],legacy)
            status="staging_eligible_manual_signoff_required" if not reasons else "blocked"
        # Never export private evidence, reviewers, documents, customer holdings or account identifiers.
        line={"legacy_id":legacy_id,
              "legacy_name":legacy["標的名稱"],
              "legacy_currency":legacy["幣別"],
              "legacy_nav_end":legacy["NAV截至日期"],
              "official_source":found[0].get("official_source") if len(found)==1 else None,
              "official_nav_last_date":found[0].get("last_effective_nav_date") if len(found)==1 else None,
              "distinct_official_nav_dates":found[0].get("distinct_effective_nav_dates",0) if len(found)==1 else 0,
              "status":status,"blockers":reasons,
              "eligible_for_production_backfill":False}
        summary[status]+=1
        lines.append(line)
    if len(lines)!=68:raise ValueError("must check exactly 68 funds")
    return {
      "schema":SCHEMA,
      "checked_at_utc":datetime.now(timezone.utc).isoformat(),
      "source_quality_checked_at_utc":public_quality["checked_at_utc"],
      "legacy_total":68,
      "public_archive_candidate_count":len(mapped),
      "staging_eligible_count":summary["staging_eligible_manual_signoff_required"],
      "production_backfill_authorized":False,
      "production_excel_changed":False,
      "dividend_adjustments_applied":False,
      "split_adjustments_applied":False,
      "read_only_report":True,
      "evidence_submitted":evidence is not None,
      "status_counts":dict(summary),
      "requirement_categories":list(REQUIRED_FIELDS),
      "notes":[
        "A public NAV observation is not a continuous historical price series.",
        "Observed calendar spans are not counts of missing market valuation days.",
        "Private reviewer evidence is checked only offline, never sent to the public Git branch.",
        "Even passing private evidence makes a fund staging-eligible only; production backfill requires separate approval."
      ],
      "funds":lines
    }

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--quality",type=Path,required=True)
    parser.add_argument("--coverage",type=Path,default=Path("data/phase16_instrument_coverage.csv"))
    parser.add_argument("--evidence",type=Path,default=None,help="Private review manifest: local only, do not commit.")
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    quality=json.loads(args.quality.read_text("utf-8"))
    evidence=json.loads(args.evidence.read_text("utf-8")) if args.evidence else None
    report=make_report(quality,legacy_rows(args.coverage),evidence)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({k:v for k,v in report.items() if k!="funds"},ensure_ascii=False))
    if report["staging_eligible_count"]:
        print("Note: staging-only. Production backfill remains BLOCKED for every fund.")

if __name__=="__main__":
    main()
