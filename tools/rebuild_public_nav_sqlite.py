"""Offline SQLite rebuild from immutable public fund NAV snapshots.

Production read-side is isolated. Import never reads/writes original Excel files.
The rebuilt DB is a disposable materialized view: the Git branch JSON files are
the durable public source of truth, not Render's ephemeral filesystem.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
from datetime import datetime
import argparse
import hashlib
import json
import sqlite3
from tools.public_nav_history import SNAPSHOT_SCHEMA, validate_date

DDL="""
PRAGMA foreign_keys=ON;
CREATE TABLE archives(
 archive_id TEXT PRIMARY KEY,
 collected_at_utc TEXT NOT NULL,
 local_date TEXT NOT NULL,
 observations_sha256 TEXT NOT NULL,
 source_checked_at_utc TEXT NOT NULL,
 imported_at_utc TEXT NOT NULL DEFAULT (datetime('now')),
 CHECK(length(observations_sha256)=64)
);
CREATE TABLE official_funds(
 fund_id INTEGER PRIMARY KEY AUTOINCREMENT,
 source TEXT NOT NULL,
 official_code TEXT NOT NULL,
 isin TEXT NOT NULL,
 currency TEXT NOT NULL,
 official_name TEXT NOT NULL,
 approval_status TEXT NOT NULL DEFAULT 'UNAPPROVED',
 UNIQUE(source,official_code,isin,currency),
 CHECK(approval_status='UNAPPROVED')
);
CREATE TABLE nav_raw_history(
 fund_id INTEGER NOT NULL REFERENCES official_funds(fund_id),
 nav_date TEXT NOT NULL,
 raw_nav TEXT NOT NULL,
 adjustment_status TEXT NOT NULL DEFAULT 'NOT_ADJUSTED',
 identity_review TEXT NOT NULL DEFAULT 'PENDING',
 PRIMARY KEY(fund_id,nav_date),
 CHECK(adjustment_status='NOT_ADJUSTED'),
 CHECK(identity_review='PENDING')
);
CREATE TABLE archive_observations(
 archive_id TEXT NOT NULL REFERENCES archives(archive_id),
 fund_id INTEGER NOT NULL REFERENCES official_funds(fund_id),
 nav_date TEXT NOT NULL,
 legacy_id TEXT NOT NULL,
 PRIMARY KEY(archive_id,fund_id,nav_date),
 FOREIGN KEY(fund_id,nav_date) REFERENCES nav_raw_history(fund_id,nav_date)
);
CREATE VIEW history_quality AS
 SELECT f.source,f.official_code,f.isin,f.currency,f.official_name,
        f.approval_status,COUNT(h.nav_date) AS available_public_observation_days,
        MIN(h.nav_date) AS earliest_collected_nav_date,
        MAX(h.nav_date) AS latest_collected_nav_date
 FROM official_funds AS f LEFT JOIN nav_raw_history h ON h.fund_id=f.fund_id
 GROUP BY f.fund_id;
"""

def import_archive(conn:sqlite3.Connection, data:dict):
    if data.get("schema")!=SNAPSHOT_SCHEMA or data.get("all_candidates_approved") is not False or data.get("legacy_history_modified") is not False:
        raise ValueError("unsafe or unsupported public candidate archive")
    observed=data.get("observations")
    if not isinstance(observed,list) or len(observed)!=data.get("candidate_count"):
        raise ValueError("candidate count mismatch")
    hash_check=hashlib.sha256(json.dumps(observed,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode("utf-8")).hexdigest()
    if hash_check!=data.get("observations_sha256"):
        raise ValueError("archive SHA-256 mismatch")
    date=validate_date(data["collection_local_date"])
    key=date.isoformat()+"_run-"+str(data["run_id"])
    conn.execute("INSERT INTO archives(archive_id,collected_at_utc,local_date,observations_sha256,source_checked_at_utc) VALUES (?,?,?,?,?)",
                 (key,data["collected_at_utc"],date.isoformat(),hash_check,data["source_report_checked_at_utc"]))
    seen=set()
    for x in observed:
        if x.get("identity_status")!="name_currency_candidate_unapproved":
            raise ValueError("observation has not been verified as unapproved-only")
        ident=(x["official_source"],x["official_code"],x["official_isin"],x["official_currency"])
        nav_date=validate_date(x["nav_date"]).isoformat()
        if x["official_dataset"]!=data["provenance"]["datasets"].get(x["official_source"]):
            raise ValueError("source provenance mismatch")
        try: nav=Decimal(x["nav_raw"])
        except InvalidOperation as e:raise ValueError("invalid NAV") from e
        if not nav.is_finite() or nav<=0:raise ValueError("invalid NAV quantity")
        nav_s=str(nav)
        identity_day=(*ident,nav_date)
        if identity_day in seen:raise ValueError("duplicate identity/day in one archive")
        seen.add(identity_day)
        conn.execute("INSERT OR IGNORE INTO official_funds(source,official_code,isin,currency,official_name) VALUES (?,?,?,?,?)",
                     (*ident,x["official_name"]))
        row=conn.execute("SELECT fund_id,official_name FROM official_funds WHERE source=? AND official_code=? AND isin=? AND currency=?",ident).fetchone()
        if row[1]!=x["official_name"]:
            # Official renames require identity review; no silent mutation.
            raise ValueError("provider identity changed name; manual review required")
        fund_id=row[0]
        existing=conn.execute("SELECT raw_nav FROM nav_raw_history WHERE fund_id=? AND nav_date=?",(fund_id,nav_date)).fetchone()
        if existing:
            if Decimal(existing[0])!=nav:
                raise ValueError("official NAV revision/conflict: archive preserved but DB merge halted")
        else:
            conn.execute("INSERT INTO nav_raw_history(fund_id,nav_date,raw_nav) VALUES (?,?,?)",(fund_id,nav_date,nav_s))
        conn.execute("INSERT INTO archive_observations(archive_id,fund_id,nav_date,legacy_id) VALUES (?,?,?,?)",
                     (key,fund_id,nav_date,str(x["legacy_instrument_id"])))

def rebuild(paths:list[Path], output:Path) ->dict:
    paths=sorted(paths)
    if not paths:raise ValueError("no archived public NAV snapshots")
    if output.exists():raise FileExistsError("never overwrite existing materialized SQLite DB")
    output.parent.mkdir(parents=True,exist_ok=True)
    conn=sqlite3.connect(":memory:")
    try:
        conn.executescript(DDL)
        for path in paths:
            data=json.loads(path.read_text(encoding="utf-8"))
            with conn:
                import_archive(conn,data)
        counts={table:conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in ("archives","official_funds","nav_raw_history")}
        counts.update({"identity_approved":0,"dividend_split_adjusted":False,"legacy_excel_modified":False})
        conn.execute("PRAGMA foreign_key_check")
        # Write new DB atomically; do not overwrite a previous destination.
        temp=output.with_name(output.name+".building")
        if temp.exists():raise FileExistsError("stale build temp: inspect rather than overwrite")
        out=sqlite3.connect(str(temp))
        try:conn.backup(out)
        finally:out.close()
        temp.rename(output)
        return counts
    finally:
        conn.close()

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--snapshots",type=Path,default=Path("public_nav_history"))
    p.add_argument("--output",type=Path,default=Path("daily_nav_readonly.sqlite"))
    a=p.parse_args()
    files=list(a.snapshots.glob("*/*/*.json"))
    result=rebuild(files,a.output)
    print(json.dumps({"db_path":str(a.output),**result},ensure_ascii=False))
if __name__=="__main__": main()
