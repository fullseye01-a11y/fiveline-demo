"""One-time private six-row TPEx historical stage -> Neon, guarded by backup gate.

Never edits existing bars, Excel, production analytics or public sites. Requires
an externally pre-verified Neon copy before enabling this worker. Preflight
revalidates official 115 stock PDF, 66-row old research archive and exact
unfilled selected dates. All six raw bars are staged and validated before a
single Neon transaction. No source OHLCV appears in public responses or logs.
"""
from __future__ import annotations

from contextlib import closing
from datetime import date
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import time

if __package__:
    from .tpex_three_day_preview import (
        DAYS,PILOT_CODES,SOURCE_SHA256_20261018,request_params,
    )
    from .tpex_official_stock_calendar import (
        fetch_official_pdf,extract_official_pdf,validate_pdf_text,compare_to_neon,
    )
    from .calendar_coverage import neon_read_only_dates
    from .history_pilot import TPEX_URL,fetch_json,parse_tpex,open_db,stage_batch
    from .postgres_store import persist_sqlite,private_summary
else:
    from tpex_three_day_preview import (
        DAYS,PILOT_CODES,SOURCE_SHA256_20261018,request_params,
    )
    from tpex_official_stock_calendar import (
        fetch_official_pdf,extract_official_pdf,validate_pdf_text,compare_to_neon,
    )
    from calendar_coverage import neon_read_only_dates
    from history_pilot import TPEX_URL,fetch_json,parse_tpex,open_db,stage_batch
    from postgres_store import persist_sqlite,private_summary

SCHEMA="fiveline_private_tpex_3day_six_row_neon_import_v1"
EXPECTED_PREVIOUS=66
EXPECTED_AFTER=72
BACKUP_BRANCH="backup-before-tpex-six-20261018"


def exact_six_candidate_gate(coverage):
    if coverage["pilot_missing_planned_quote_days"]!=0:
        raise ValueError("existing TPEx five-session pilot coverage changed")
    expected={(code,day) for code in PILOT_CODES for day in DAYS}
    actual={(x["code"],day) for x in coverage["instruments"]
            for day in x["expanded_missing_date_candidates"]}
    if actual != expected:
        raise ValueError("TPEx six-row backfill date identity changed")
    if coverage["official_announced_closed_session_quote_count"]!=0:
        raise ValueError("unexpected historical stock holiday quote")
    return True


def old_archive_gate(before):
    if not (before["approved_bars"]==EXPECTED_PREVIOUS and
            before["pending_review"]==0 and
            before["revisions"]==0):
        raise ValueError("Neon original 66 raw research bars are not in expected pristine state")
    return True


def run_guarded_import(dsn, *, backup_proof, read_dates=None,
                       fetch_pdf=None, fetch_quotes=None, summarize=None,
                       archive=None, pause=None):
    if not dsn:
        raise RuntimeError("Neon not configured; refuse any official requests")
    if backup_proof!=BACKUP_BRANCH:
        raise ValueError("matching Neon backup branch proof is required")
    reader=read_dates or neon_read_only_dates
    pdf=fetch_pdf or fetch_official_pdf
    quote_fetch=fetch_quotes or fetch_json
    summary=summarize or private_summary
    persist=archive or persist_sqlite
    sleep=pause or time.sleep
    old_archive_gate(summary(dsn=dsn))
    raw,digest=pdf()
    if digest!=SOURCE_SHA256_20261018:
        raise ValueError("previously verified official 115 stock PDF changed")
    holidays=validate_pdf_text(extract_official_pdf(raw))
    coverage=compare_to_neon(holidays,reader(dsn),digest)
    exact_six_candidate_gate(coverage)

    batches=[]
    with TemporaryDirectory(prefix="fiveline-tpex-six-neon-private-") as temp:
        db_path=Path(temp)/"stage.sqlite"
        with closing(open_db(db_path)) as conn:
            for index,day in enumerate(DAYS):
                if index:sleep(1.2)
                source=quote_fetch(TPEX_URL,request_params(day))
                bars,errors=parse_tpex(source,day,set(PILOT_CODES),legacy_unit=None)
                if errors or len(bars)!=2 or {b["code"] for b in bars}!=set(PILOT_CODES):
                    raise ValueError("official TPEx six-row source incomplete or invalid")
                staged=stage_batch(conn,"TPEX",day,bars,[])
                if staged["inserted"]!=2 or staged["pending"] or staged["rejected"]:
                    raise ValueError("TPEx source stage has pending review or validation failures")
                batches.append({"date":day,"validated_symbols":2})
            if conn.execute("SELECT COUNT(*) FROM approved_bars").fetchone()[0]!=6:
                raise ValueError("ephemeral stage six-row integrity check failed")
            if conn.execute("PRAGMA integrity_check").fetchone()[0]!="ok":
                raise ValueError("ephemeral SQLite corruption")
        # Abort if parallel work changed durable archive during downloads.
        old_archive_gate(summary(dsn=dsn))
        # The existing persist_sqlite performs one SQL transaction and preserves
        # each previous (market, code, day, version) record; never UPDATE/DELETE.
        saved=persist(db_path,dsn=dsn)
    after=summary(dsn=dsn)
    if (saved["incoming"]!=6 or saved["inserted"]!=6 or
        saved["unchanged"] or saved["pending"] or
        after["approved_bars"]!=EXPECTED_AFTER or
        after["pending_review"]!=0 or after["revisions"]!=0):
        raise RuntimeError("post-archive research state requires manual inspection; do NOT auto retry")
    return {
        "schema":SCHEMA,"status":"PRIVATE_NEON_BOUNDED_SIX_ROW_ARCHIVED",
        "official_source_checked":True,
        "exchange":"TPEX_STOCKS_ONLY","official_request_days":len(batches),
        "candidate_instrument_dates":6,"inserted_new_versions":saved["inserted"],
        "neon_approved_before":EXPECTED_PREVIOUS,
        "neon_approved_after":after["approved_bars"],
        "neon_pending_after":after["pending_review"],
        "backup_ref":BACKUP_BRANCH,
        "temporary_quote_spool_deleted":True,
        "public_market_quotes_distributed":False,
        "historical_series_unadjusted":True,
        "corporate_actions_verified":False,
        "production_backtests_modified":False,
    }
