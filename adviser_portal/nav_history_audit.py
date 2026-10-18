"""Offline, provenance-aware historical per-unit NAV intake and coverage audit.

Input MUST be a source-licensed, issuer/share-class-specific historical export.
No scraping, no customer data, no public file upload, no DB or portfolio writes.

A weekdays-generated calendar is NOT an authoritative fund NAV calendar.
For provisional day completeness provide an independently evidenced
issuer share-class NAV-date list. Lacking it, the result is "DATE_EVIDENCE_MISSING".
Even a fully passing coverage check is research-only until rights, corrections,
distributions, splits, FX and deployment controls are separately approved.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from io import StringIO
import json
import re
from urllib.parse import urlsplit

from .offshore_evidence import valid_isin

REQUIRED_COLUMNS=("nav_date","isin","currency","nav_per_unit")
MAX_CSV_BYTES=8_000_000
MAX_OBSERVATIONS=5_000
MIN_HISTORY_YEARS=3
MAX_FUTURE_DAYS=0


class HistoryAuditError(ValueError):
    pass


def _three_year_cutoff(today:date):
    try:
        return today.replace(year=today.year-3)
    except ValueError:  # 29 Feb 2024 -> 28 Feb 2021
        return today.replace(year=today.year-3,day=28)


def _date_iso(value:str):
    if not isinstance(value,str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}",value):
        raise HistoryAuditError("NAV date must be YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HistoryAuditError("Invalid NAV date") from exc


def _nav_amount(value:str):
    if not isinstance(value,str) or not re.fullmatch(r"\d+(?:\.\d{1,8})?",value):
        raise HistoryAuditError("NAV must be positive decimal text; no totals, ratios or adjusted prices")
    try:
        x=Decimal(value)
    except InvalidOperation as exc:
        raise HistoryAuditError("NAV decimal invalid") from exc
    if not x.is_finite() or x<=0 or x>=Decimal("1000000000"):
        raise HistoryAuditError("NAV not economically valid")
    return x


def _manifest(meta:dict, body:bytes, today:date):
    if not isinstance(meta,dict) or len(meta)>24:
        raise HistoryAuditError("Source manifest required")
    if meta.get("schema")!="fiveline-raw-fund-nav-history-v1":
        raise HistoryAuditError("Source manifest schema unsupported")
    isin=str(meta.get("isin","")).strip().upper()
    currency=str(meta.get("currency","")).strip().upper()
    if not valid_isin(isin):
        raise HistoryAuditError("Share class ISIN checksum invalid")
    if not re.fullmatch(r"[A-Z]{3}",currency):
        raise HistoryAuditError("Currency must use ISO 4217 3-letter code")
    if meta.get("nav_type")!="raw_unadjusted_per_unit":
        raise HistoryAuditError("Only raw unadjusted per-unit NAV is accepted")
    if not re.fullmatch(r"[0-9a-f]{64}",str(meta.get("source_sha256",""))):
        raise HistoryAuditError("SHA-256 provenance required")
    actual=sha256(body).hexdigest()
    if actual!=meta["source_sha256"]:
        raise HistoryAuditError("Source bytes do not match SHA-256")
    source=meta.get("source_url","")
    uri=urlsplit(source) if isinstance(source,str) else None
    if not uri or uri.scheme!="https" or not uri.hostname or uri.username or uri.password:
        raise HistoryAuditError("HTTPS issuer or licensed provider source URL required")
    if not re.fullmatch(r"[A-Za-z0-9 ._-]{2,100}",str(meta.get("provider_name",""))):
        raise HistoryAuditError("Provider name required")
    if meta.get("source_basis") not in ("issuer_official","licensed_vendor"):
        raise HistoryAuditError("Source basis not identified")
    # This is reviewer-attested metadata, NOT independent legal approval.
    if type(meta.get("rights_reviewer_approved")) is not bool:
        raise HistoryAuditError("Rights review flag must be explicit boolean")
    if type(meta.get("issuer_identity_reviewed")) is not bool:
        raise HistoryAuditError("Share-class identity review flag must be explicit boolean")
    cutoff=_three_year_cutoff(today)
    return isin,currency,cutoff,actual


def parse_nav_csv(body:bytes,isin:str,currency:str,today:date):
    if not isinstance(body,bytes) or not body or len(body)>MAX_CSV_BYTES:
        raise HistoryAuditError("Historical NAV CSV absent/oversized")
    try:
        text=body.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HistoryAuditError("Historical NAV export must be UTF-8") from exc
    if "<html" in text[:100].lower():
        raise HistoryAuditError("Source returned HTML, not CSV")
    reader=csv.DictReader(StringIO(text))
    headers=reader.fieldnames or ()
    if (len(headers)!=len(set(headers)) or
        tuple(headers)!=REQUIRED_COLUMNS):
        raise HistoryAuditError("Unexpected NAV source schema or column mapping")
    dates={}
    count=0
    duplicate_identical=0
    conflicts=[]
    for item in reader:
        count+=1
        if count>MAX_OBSERVATIONS:
            raise HistoryAuditError("NAV observations exceed processing limit")
        if not isinstance(item,dict) or None in item:
            raise HistoryAuditError("NAV CSV row malformed")
        if str(item["isin"]).strip().upper()!=isin or str(item["currency"]).strip().upper()!=currency:
            raise HistoryAuditError("Mixed share classes or foreign currencies in one export")
        day=_date_iso(item["nav_date"])
        if day>today:
            raise HistoryAuditError("Future NAV observation")
        number=_nav_amount(item["nav_per_unit"])
        if day in dates:
            if dates[day]!=number:
                conflicts.append(day.isoformat())
            else:
                duplicate_identical+=1
            continue
        dates[day]=number
    if not dates:
        raise HistoryAuditError("No fund NAV records")
    if conflicts:
        raise HistoryAuditError("Conflicting NAV values for one share-class date")
    return dates,duplicate_identical


def _session_evidence(official_dates,isin,currency,meta):
    """Caller-reviewed NAV dates, not inferred exchange weekdays."""
    if not isinstance(official_dates,dict) or official_dates.get("schema")!="issuer-share-class-nav-days-v1":
        return None,False
    if (official_dates.get("isin")!=isin or official_dates.get("currency")!=currency or
        official_dates.get("source_sha256")!=meta.get("session_evidence_sha256") or
        not re.fullmatch(r"[0-9a-f]{64}",str(official_dates.get("source_sha256","")))):
        raise HistoryAuditError("Issuer session evidence identity/digest mismatch")
    raw=official_dates.get("expected_nav_dates")
    if not isinstance(raw,list) or not raw or len(raw)>MAX_OBSERVATIONS or len(raw)!=len(set(raw)):
        raise HistoryAuditError("Expected NAV-date evidence missing, duplicated or oversized")
    expected={_date_iso(x) for x in raw}
    digest=sha256("\n".join(sorted(raw)).encode("utf-8")).hexdigest()
    if digest!=official_dates["source_sha256"]:
        raise HistoryAuditError("Issuer NAV-date list bytes do not match hash")
    checked=official_dates.get("reviewer_approved") is True
    return expected,checked


def audit_history(meta:dict,raw_csv:bytes,expected_sessions=None,today=None):
    """Return exact coverage diagnostics and immutable source identity; no NAV values.

    A 3-year date-span alone never proves complete daily coverage. Also:
    zero missing official NAV dates is not enough absent reviewed session evidence.
    """
    today=today or date.today()
    isin,currency,cutoff,digest=_manifest(meta,raw_csv,today)
    observations,dupes=parse_nav_csv(raw_csv,isin,currency,today)
    days=sorted(observations)
    first,last=days[0],days[-1]
    expected,reviewed=_session_evidence(expected_sessions,isin,currency,meta)
    missing=set() if expected is None else expected-set(days)
    unexpected=set() if expected is None else set(days)-expected
    window_span=(first<=cutoff)
    # Fund NAV is not published on every calendar day: weekends, holidays
    # and issuer-specific closed days are validated by the supplied official
    # NAV-date list, never by manufactured weekday expectations.
    stale=(today-last).days>14
    # A verified list of expected NAV dates may end on the previous dealing
    # day; a date span alone is insufficient to claim history coverage.
    # Reviewer-attested coverage is not even a provisional pass until the
    # source-rights and unique share-class identity checks have also passed.
    rights_ok=meta.get("rights_reviewer_approved") is True
    class_identity_ok=meta.get("issuer_identity_reviewed") is True
    coverage_ok=(
        window_span and not stale and expected is not None and reviewed
        and rights_ok and class_identity_ok
        and len(expected)==len(days) and not missing and not unexpected and dupes==0
        and min(expected)<=cutoff and max(expected)==last
    )
    blockers=[]
    if not window_span:blockers.append("LESS_THAN_THREE_YEARS_OF_NAV")
    if stale:blockers.append("NAV_RECENT_ENDPOINT_STALE")
    if expected is None:blockers.append("ISSUER_SESSION_CALENDAR_NOT_SUPPLIED")
    elif not reviewed:blockers.append("ISSUER_SESSION_CALENDAR_NOT_REVIEWED")
    if missing:blockers.append("MISSING_EXPECTED_NAV_DATES")
    if unexpected:blockers.append("NAV_DATES_NOT_IN_ISSUER_SESSION_LIST")
    if dupes:blockers.append("DUPLICATED_SAME_NAV_DATE")
    if not meta["rights_reviewer_approved"]:blockers.append("DATA_LICENSE_REVIEW_NOT_APPROVED")
    if not class_identity_ok:blockers.append("SHARE_CLASS_OR_PROVIDER_REVIEW_PENDING")
    # Reconciliation of dividends, FX, splits and client suitability is
    # a separate strict prerequisite even after individual CSV passes.
    return {
        "schema":"fiveline_nav_history_source_audit_v1",
        "status":"PROVISIONAL_HISTORY_SOURCE_COVERAGE_ONLY" if coverage_ok
             else "HISTORY_SOURCE_NEEDS_REVIEW",
        "isin":isin,"currency":currency,
        "source_basis":meta["source_basis"],"source_sha256":digest,
        "first_nav_date":first.isoformat(),
        "last_nav_date":last.isoformat(),
        "unique_nav_dates":len(days),
        "identical_duplicate_count":dupes,
        "historical_date_span_three_years":window_span,
        "issuer_expected_nav_dates_count":len(expected) if expected is not None else None,
        "missing_expected_nav_count":len(missing),
        "unexpected_nav_date_count":len(unexpected),
        "sample_missing_dates":[d.isoformat() for d in sorted(missing)[:8]],
        "reviewed_issuer_nav_calendar":reviewed,
        "source_license_approved_attested_only":meta["rights_reviewer_approved"],
        "calendar_coverage_provisional_pass":coverage_ok,
        "issuer_amounts_distributions_checked":False,
        "fx_history_verified":False,
        "splits_and_unit_changes_reviewed":False,
        "ready_for_total_return":False,
        "ready_for_portfolio":False,
        "ready_for_six_strategies":False,
        "database_modified":False,"customer_records_read":False,
        "blockers":blockers,
        "note":"Only date/identity/source quality audit. Calendar and rights assertions are supplied by a reviewer; no live license/API rights verified.",
    }


def _cli():
    import argparse
    from pathlib import Path
    parser=argparse.ArgumentParser(description="Private research-only licensed NAV audit")
    parser.add_argument("--manifest",required=True)
    parser.add_argument("--csv",required=True)
    parser.add_argument("--sessions",help="Optional reviewed issuer expected NAV-date JSON")
    args=parser.parse_args()
    meta=json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    csv_bytes=Path(args.csv).read_bytes()
    sessions=(json.loads(Path(args.sessions).read_text(encoding="utf-8"))
              if args.sessions else None)
    result=audit_history(meta,csv_bytes,sessions)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    raise SystemExit(0 if result["calendar_coverage_provisional_pass"] else 2)


if __name__=="__main__":
    _cli()
