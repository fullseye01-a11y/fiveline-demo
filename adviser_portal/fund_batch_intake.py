"""Stage-1 multi-fund licensed raw NAV batch intake, offline research only.

Reads up to 100 locally supplied share-class bundles per batch against an
entire verified public fund-catalog snapshot. Never downloads NAV, writes to
DB, uploads CSV, logs unit prices, or sends client records. Source documents,
license permissions, and official calendars require independent legal review.

Acceptance here means only PROVISIONAL_HISTORY_SOURCE_COVERAGE_ONLY, not
commercial distribution rights, portfolio eligibility or annualized returns.
"""
from __future__ import annotations

from collections import Counter
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from .fund_batch_queue import _identity_index
from .offshore_evidence import valid_isin, _ccy
from .nav_history_audit import audit_history, HistoryAuditError

SCHEMA="fiveline_allfund_licensed_history_batch_intake_v1"
MAX_SUBMISSIONS=100
MAX_BATCH_BYTES=48_000_000
MAX_IDENTITY_SOURCE_BYTES=4_000_000


class BatchIntakeError(ValueError):
    pass


def _official_identity(row, manifest, identity):
    """Do not merge reused source codes; a missing ISIN needs document review."""
    isin=str(manifest.get("isin","")).strip().upper()
    currency=str(manifest.get("currency","")).strip().upper()
    if not valid_isin(isin) or not currency:
        raise BatchIntakeError("VALID_CLASS_ISIN_AND_CURRENCY_REQUIRED")
    if _ccy(currency)!=_ccy(row.get("currency")) or not _ccy(currency):
        raise BatchIntakeError("CATALOG_CURRENCY_CONFLICT")
    catalog_isin=str(row.get("isin","") or "").strip().upper()
    if catalog_isin:
        if not valid_isin(catalog_isin) or catalog_isin!=isin:
            raise BatchIntakeError("CATALOG_ISIN_CONFLICT")
        return "CATALOG_VALID_ISIN_MATCH"
    if not isinstance(identity,dict):
        raise BatchIntakeError("MISSING_ISIN_REQUIRES_ISSUER_DOCUMENT")
    for key in ("uid","code","name","source","category"):
        if identity.get(key)!=row.get(key):
            raise BatchIntakeError("IDENTITY_EVIDENCE_MISMATCH")
    if (str(identity.get("isin","")).upper()!=isin or
        _ccy(identity.get("currency"))!=currency or
        identity.get("reviewer_approved") is not True):
        raise BatchIntakeError("ISIN_REVIEWER_ATTESTATION_REQUIRED")
    url=identity.get("issuer_identity_document_url")
    if not isinstance(url,str) or len(url)>500:
        raise BatchIntakeError("IDENTITY_OFFICIAL_HTTPS_URL_REQUIRED")
    parsed=urlsplit(url)
    if (parsed.scheme!="https" or not parsed.hostname or
        parsed.username is not None or parsed.password is not None or
        parsed.fragment or parsed.port not in (None,443)):
        raise BatchIntakeError("IDENTITY_OFFICIAL_HTTPS_URL_REQUIRED")
    evidence_sha=identity.get("source_document_sha256")
    if not isinstance(evidence_sha,str) or not re.fullmatch("[a-f0-9]{64}",evidence_sha):
        raise BatchIntakeError("IDENTITY_SOURCE_SHA256_REQUIRED")
    # Attested source, not automated legal/document-content verification.
    return "REVIEWER_ATTESTED_ISSUER_ISIN_PENDING_INDEPENDENT_CHECK"


def audit_intake_batch(snapshot, submissions, *, today=None, expected_snapshot=None):
    """Independent per-file quality gate; one bad file never mutates another.

    submissions: a list of {uid, manifest, raw_csv (bytes), sessions,
    identity_evidence?}. Real files are only accepted by the offline CLI.
    """
    if not isinstance(submissions,list) or not 1<=len(submissions)<=MAX_SUBMISSIONS:
        raise BatchIntakeError("BATCH_SIZE_NOT_IN_1_TO_100")
    rows,snapshot_digest,_,_= _identity_index(snapshot)
    if expected_snapshot is not None and expected_snapshot!=snapshot_digest:
        raise BatchIntakeError("PUBLIC_CATALOG_SNAPSHOT_CHANGED")
    by_uid={item["uid"]:item for item in rows}
    seen_uid=set()
    seen_sha={}
    total_bytes=0
    results=[]
    for entry in submissions:
        if not isinstance(entry,dict):
            raise BatchIntakeError("BATCH_ENTRY_NOT_OBJECT")
        uid=entry.get("uid")
        if not isinstance(uid,str) or not uid or uid in seen_uid:
            raise BatchIntakeError("DUPLICATE_OR_BLANK_UID_IN_BATCH")
        seen_uid.add(uid)
        raw=entry.get("raw_csv")
        if not isinstance(raw,bytes):
            raise BatchIntakeError("OFFLINE_RAW_NAV_CSV_BYTES_REQUIRED")
        total_bytes+=len(raw)
        if total_bytes>MAX_BATCH_BYTES:
            raise BatchIntakeError("BATCH_EXCEEDS_MAXIMUM_RAW_BYTES")
        manifest=entry.get("manifest")
        digest=sha256(raw).hexdigest()
        findings={
            "uid":uid,"catalog_found":uid in by_uid,
            "source_sha256":digest,
            "status":"REJECTED_NEEDS_REVIEW",
            "provisional_nav_coverage_pass":False,
            "identity_gate":None,
            "history_observation_dates":0,
            "missing_expected_nav_dates":None,
            "blockers":[],
            "historical_price_rows_persisted":0,
            "total_return_approved":False,
            "ready_for_portfolio":False,
        }
        results.append(findings)
        if uid not in by_uid:
            findings["blockers"]=["UID_NOT_IN_VERIFIED_FUND_DIRECTORY"]
            continue
        if digest in seen_sha and seen_sha[digest]!=uid:
            findings["blockers"]=["RAW_NAV_FILE_REUSED_FOR_DIFFERENT_UID"]
            # Also retroactively reject the first file; no first-wins.
            for previous in results:
                if previous["uid"]==seen_sha[digest]:
                    previous.update(status="REJECTED_NEEDS_REVIEW",
                                    provisional_nav_coverage_pass=False)
                    previous["blockers"]=["RAW_NAV_FILE_REUSED_FOR_DIFFERENT_UID"]
            continue
        seen_sha[digest]=uid
        if not isinstance(manifest,dict):
            findings["blockers"]=["SOURCE_MANIFEST_REQUIRED"]
            continue
        try:
            if manifest.get("catalog_snapshot_sha256")!=snapshot_digest:
                raise BatchIntakeError("SOURCE_SNAPSHOT_BINDING_REQUIRED")
            if manifest.get("source_uid")!=uid:
                raise BatchIntakeError("MANIFEST_SOURCE_UID_MISMATCH")
            row=by_uid[uid]
            if (manifest.get("source")!=row["source"] or
                manifest.get("catalog_code")!=row["code"] or
                manifest.get("catalog_name")!=row["name"] or
                manifest.get("category")!=row["category"]):
                raise BatchIntakeError("EXACT_CATALOG_CLASS_MISMATCH")
            findings["identity_gate"]=_official_identity(
                row,manifest,entry.get("identity_evidence"))
            audited=audit_history(manifest,raw,entry.get("sessions"),today=today)
            findings["history_observation_dates"]=audited["unique_nav_dates"]
            findings["missing_expected_nav_dates"]=audited["missing_expected_nav_count"]
            findings["first_nav_date"]=audited["first_nav_date"]
            findings["last_nav_date"]=audited["last_nav_date"]
            findings["blockers"]=audited["blockers"]
            if audited["calendar_coverage_provisional_pass"]:
                findings["status"]="PROVISIONAL_SOURCE_DATE_QUALITY_PASSED_ONLY"
                findings["provisional_nav_coverage_pass"]=True
            else:
                findings["status"]="REJECTED_NEEDS_REVIEW"
        except (BatchIntakeError,HistoryAuditError,ValueError) as exc:
            # Locally-authored validation strings only; never echo source
            # paths, NAV values, CSV snippets, secrets, or user input.
            findings["blockers"]=[str(exc)[:110]]
    counters=Counter(r["status"] for r in results)
    counted=sum(r["provisional_nav_coverage_pass"] for r in results)
    return {
        "schema":SCHEMA,
        "status":"OFFLINE_BATCH_SOURCE_DATE_AUDIT_ONLY",
        "catalog_snapshot_sha256":snapshot_digest,
        "catalog_fund_records":len(rows),
        "batch_submissions":len(submissions),
        "funds_without_submitted_history_bundle":len(rows)-len(set(by_uid)&seen_uid),
        "provisional_source_date_quality_pass_count":counted,
        "rejected_or_review_count":len(results)-counted,
        "source_duplicate_identity_checks":len(seen_sha),
        "batch_bytes_checked":total_bytes,
        "results":results,
        "official_nav_3y_persisted_funds":0,
        "official_nav_3y_persisted_rows":0,
        "all_fund_3y_nav_ready":False,
        "independent_data_rights_verified":False,
        "independent_issuer_calendar_verified":False,
        "dividend_fx_split_reconciliation_approved":False,
        "historical_total_return_approved":False,
        "portfolio_or_six_strategies_modified":False,
        "customer_data_accessed":False,
        "production_database_modified":False,
        "note":"Every received bundle is checked without storing NAV. Only independently licensed original per-unit NAV and official valuation-day evidence can advance to a separately approved stage.",
    }


def _safe_file(root:Path,relative:str):
    if not isinstance(relative,str) or len(relative)>140 or not re.fullmatch(r"[a-zA-Z0-9_./-]+",relative):
        raise BatchIntakeError("INVALID_PRIVATE_FILE_REFERENCE")
    target=Path(relative)
    if target.is_absolute() or any(p in (".","..") for p in target.parts):
        raise BatchIntakeError("PRIVATE_FILE_PATH_TRAVERSAL_REJECTED")
    parent=root.resolve(strict=True)
    resolved=(parent/target).resolve(strict=True)
    if not resolved.is_relative_to(parent) or not resolved.is_file():
        raise BatchIntakeError("PRIVATE_FILE_PATH_OUTSIDE_BATCH_ROOT")
    return resolved


def run_private_batch(catalog_file, root_folder, index_file, *, today=None):
    """No network; local user-reviewed files only; no history is persisted."""
    from .catalog import validate_catalog
    catalog_bytes=Path(catalog_file).read_bytes()
    if len(catalog_bytes)>8_000_000:
        raise BatchIntakeError("CATALOG_INPUT_TOO_LARGE")
    snapshot=validate_catalog(json.loads(catalog_bytes.decode("utf-8")))
    root=Path(root_folder)
    index_path=_safe_file(root,index_file)
    if index_path.stat().st_size>120_000:
        raise BatchIntakeError("BATCH_INDEX_TOO_LARGE")
    index=json.loads(index_path.read_text(encoding="utf-8"))
    if index.get("schema")!="fiveline-private-nav-batch-file-index-v1":
        raise BatchIntakeError("BATCH_INDEX_SCHEMA_UNKNOWN")
    batches=index.get("entries")
    if not isinstance(batches,list) or not 1<=len(batches)<=MAX_SUBMISSIONS:
        raise BatchIntakeError("BATCH_INDEX_SIZE_INVALID")
    submissions=[]
    for bundle in batches:
        if not isinstance(bundle,dict) or set(bundle)-{
            "uid","manifest","csv","sessions","identity_evidence"}:
            raise BatchIntakeError("BATCH_INDEX_ENTRY_INVALID")
        one={"uid":bundle.get("uid")}
        for attr,into in (("manifest","manifest"),("csv","raw_csv"),
                          ("sessions","sessions"),("identity_evidence","identity_evidence")):
            file=bundle.get(attr)
            if file is None:
                if attr in ("manifest","csv"):
                    raise BatchIntakeError("MISSING_PRIVATE_REQUIRED_SOURCE")
                continue
            safe=_safe_file(root,file)
            max_len=8_000_000 if attr=="csv" else MAX_IDENTITY_SOURCE_BYTES
            if safe.stat().st_size>max_len:
                raise BatchIntakeError("PRIVATE_SOURCE_DOCUMENT_TOO_LARGE")
            content=safe.read_bytes()
            one[into]=(content if attr=="csv" else json.loads(content.decode("utf-8")))
        submissions.append(one)
    return audit_intake_batch(snapshot,submissions,
                              expected_snapshot=index.get("catalog_snapshot_sha256"),
                              today=today)


def _cli():
    import argparse
    parser=argparse.ArgumentParser(description="Private licensed NAV multi-fund batch audit; never writes prices")
    parser.add_argument("--catalog",required=True,help="Raw verified public market universe JSON file")
    parser.add_argument("--root",required=True,help="Private directory with bundled NAV files")
    parser.add_argument("--index",required=True,help="Relative JSON batch-index file under root")
    opts=parser.parse_args()
    report=run_private_batch(opts.catalog,opts.root,opts.index)
    print(json.dumps(report,ensure_ascii=False,indent=2))
    raise SystemExit(0 if report["rejected_or_review_count"]==0 else 2)


if __name__=="__main__":
    _cli()
