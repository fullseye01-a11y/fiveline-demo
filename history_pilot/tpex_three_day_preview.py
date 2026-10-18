"""Bounded TPEx Oct 1/2/5 *ephemeral* OHLCV source preview.

After the 115 official STOCK (not bond) calendar PDF passes conservative text
validation, retrieve at most three TPEx daily snapshots. Parse only 6488/8069,
stage in a private auto-deleted SQLite file, report counts only, and NEVER sync
or publish raw quotes. This is not proof of listing history or adjustments.
"""
from __future__ import annotations

from contextlib import closing
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import time

if __package__:
    from .tpex_official_stock_calendar import (
        fetch_official_pdf, extract_official_pdf, validate_pdf_text,
        compare_to_neon, PILOT_CODES,
    )
    from .calendar_coverage import neon_read_only_dates
    from .history_pilot import TPEX_URL, fetch_json, parse_tpex, open_db, stage_batch
else:
    from tpex_official_stock_calendar import (
        fetch_official_pdf, extract_official_pdf, validate_pdf_text,
        compare_to_neon, PILOT_CODES,
    )
    from calendar_coverage import neon_read_only_dates
    from history_pilot import TPEX_URL, fetch_json, parse_tpex, open_db, stage_batch

DAYS = ("2026-10-01", "2026-10-02", "2026-10-05")
MAX_REQUESTS = 3
SOURCE_SHA256_20261018 = "b864f5e572197c54828301391e80fd6b7bae399abd19a60cf601ed460ccd8e2f"
SCHEMA = "fiveline_tpex_gap_three_day_ephemeral_preview_v1"


def request_params(day):
    assert day in DAYS
    d=date.fromisoformat(day)
    return {"l":"zh-tw","d":f"{d.year-1911}/{d:%m/%d}","se":"EW"}


def preview(dsn, *, official_pdf_fetch=None, quotes_fetch=None,
            read_dates=None, wait=None) -> dict:
    if not dsn:
        raise RuntimeError("Neon private date read-only connection is required")
    pdf_call=official_pdf_fetch or fetch_official_pdf
    price_call=quotes_fetch or fetch_json
    dates_call=read_dates or neon_read_only_dates
    sleep=wait or time.sleep

    # Cross-check BOTH annual official stock calendar and durable Neon dates
    # before requesting any price data.
    binary,digest=pdf_call()
    if digest != SOURCE_SHA256_20261018:
        raise ValueError("official stock-calendar PDF digest changed; requires source re-review")
    closed=validate_pdf_text(extract_official_pdf(binary))
    current=dates_call(dsn)
    coverage=compare_to_neon(closed,current,digest)
    expected={(code,day) for code in PILOT_CODES for day in DAYS}
    actual={(x["code"],day)
            for x in coverage["instruments"] for day in x["expanded_missing_date_candidates"]}
    if actual != expected or coverage["pilot_missing_planned_quote_days"] != 0:
        raise ValueError("the six expected private gap candidates are no longer exactly missing")

    batches=[]
    with TemporaryDirectory(prefix="fiveline-otc-private-3days-") as folder:
        with closing(open_db(Path(folder)/"ephemeral_quotes.sqlite")) as db:
            for i,day in enumerate(DAYS):
                if i:sleep(1.2)
                doc=price_call(TPEX_URL,request_params(day))
                bars,errors=parse_tpex(doc,day,set(PILOT_CODES),legacy_unit=None)
                found={b["code"] for b in bars}
                if errors or len(bars)!=2 or found!=set(PILOT_CODES):
                    raise ValueError("selected TPEx stock data unavailable/ambiguous; no Neon write")
                staged=stage_batch(db,"TPEX",day,bars,[])
                if staged["inserted"]!=2 or staged["rejected"] or staged["pending"]:
                    raise ValueError("TPEx private stage requires manual review; no Neon write")
                batches.append({"day":day,"selected_instruments":2,"staged_valid_unadjusted_bars":2})
            n=db.execute("SELECT COUNT(*) FROM approved_bars").fetchone()[0]
            if n!=len(DAYS)*len(PILOT_CODES):
                raise ValueError("ephemeral staging count mismatch")
            if db.execute("PRAGMA integrity_check").fetchone()[0]!="ok":
                raise ValueError("SQLite integrity check failed")
    # Ephemeral SQLite is deleted above. No approved data copied to Neon.
    return {
        "schema":SCHEMA,"status":"PRIVATE_EPHEMERAL_SOURCE_PREVIEW",
        "official_market":"TPEX_STOCKS_ONLY",
        "calendar_pdf_sha256":digest,"approved_annual_calendar_2026":False,
        "official_document_revalidated_for_date_labels":True,
        "planned_exchange_requests":MAX_REQUESTS,
        "completed_exchange_requests":len(batches),
        "candidate_instrument_days":len(expected),
        "private_staged_source_rows":sum(b["staged_valid_unadjusted_bars"] for b in batches),
        "batches":batches,
        "neon_write_performed":False,
        "ephemeral_raw_sqlite_deleted":True,
        "raw_quotes_publicly_redistributed":False,
        "listing_halt_history_verified":False,
        "corporate_action_adjustments_verified":False,
        "production_backtests_modified":False,
    }
