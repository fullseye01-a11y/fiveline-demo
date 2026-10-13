"""NAV CSV staging validator. No network calls, API tokens or production writes."""
from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import re
import sqlite3
from pathlib import Path

COLUMNS = ("instrument_id", "nav_date", "nav_value", "currency", "share_class", "provider")
ID_RE = re.compile(r"^[A-Za-z0-9:._-]{1,64}$")
PROVIDER_RE = re.compile(r"^[A-Za-z0-9_.-]{2,80}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CURRENCY_RE = re.compile(r"^[A-Z]{3}$")

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS nav_staging (
    id INTEGER PRIMARY KEY,
    instrument_id TEXT NOT NULL,
    nav_date TEXT NOT NULL,
    nav_value TEXT NOT NULL,
    currency TEXT NOT NULL,
    share_class TEXT NOT NULL,
    provider TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    staged_at_utc TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending_review',
    UNIQUE (instrument_id, nav_date, currency, share_class, provider)
)
"""


def validate_row(row: dict[str, str]) -> tuple[str, str, str, str, str, str]:
    fields = {key: (row.get(key) or "").strip() for key in COLUMNS}
    instrument_id = fields["instrument_id"]
    if not ID_RE.fullmatch(instrument_id):
        raise ValueError("invalid instrument_id")
    nav_date = fields["nav_date"]
    if not DATE_RE.fullmatch(nav_date):
        raise ValueError("invalid nav_date format (YYYY-MM-DD)")
    try:
        date.fromisoformat(nav_date)
    except ValueError as exc:
        raise ValueError("invalid calendar date") from exc
    raw_value = fields["nav_value"]
    if len(raw_value) > 40:
        raise ValueError("NAV value too long")
    try:
        value = Decimal(raw_value)
    except InvalidOperation as exc:
        raise ValueError("invalid NAV value") from exc
    if not value.is_finite() or value <= 0:
        raise ValueError("NAV must be a finite positive number")
    currency = fields["currency"].upper()
    if not CURRENCY_RE.fullmatch(currency):
        raise ValueError("invalid ISO currency code")
    share_class = fields["share_class"]
    if not (1 <= len(share_class) <= 100) or any(ord(c) < 32 for c in share_class):
        raise ValueError("invalid share_class")
    provider = fields["provider"]
    if not PROVIDER_RE.fullmatch(provider):
        raise ValueError("invalid provider identifier")
    return (instrument_id, nav_date, format(value, "f"), currency, share_class, provider)


def ingest_csv(fileobj, connection: sqlite3.Connection, batch_id: str) -> dict:
    """Validate all rows and commit only if every row is valid and conflict-free."""
    if not batch_id or len(batch_id) > 100 or any(ord(c) < 32 for c in batch_id):
        raise ValueError("invalid batch_id")
    reader = csv.DictReader(fileobj)
    if not reader.fieldnames or not set(COLUMNS).issubset(reader.fieldnames):
        raise ValueError("CSV header must include: " + ", ".join(COLUMNS))
    connection.execute(CREATE_SQL)
    accepted = duplicates = conflicts = rejected = total = 0
    errors = []
    now = datetime.now(timezone.utc).isoformat()
    connection.execute("BEGIN")
    try:
        for line_no, raw in enumerate(reader, start=2):
            total += 1
            try:
                fields = validate_row(raw)
            except ValueError as exc:
                rejected += 1
                if len(errors) < 20:
                    errors.append({"csv_line": line_no, "error": str(exc)})
                continue
            cursor = connection.execute(
                """INSERT OR IGNORE INTO nav_staging
                (instrument_id, nav_date, nav_value, currency, share_class,
                 provider, batch_id, staged_at_utc)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (*fields, batch_id, now),
            )
            if cursor.rowcount == 1:
                accepted += 1
                continue
            previous = connection.execute(
                """SELECT nav_value FROM nav_staging
                WHERE instrument_id=? AND nav_date=? AND currency=?
                AND share_class=? AND provider=?""",
                (fields[0], fields[1], fields[3], fields[4], fields[5]),
            ).fetchone()
            if previous and Decimal(previous[0]) == Decimal(fields[2]):
                duplicates += 1
            else:
                conflicts += 1
                if len(errors) < 20:
                    errors.append({"csv_line": line_no, "error": "NAV conflict; no overwrite permitted"})
        committed = rejected == 0 and conflicts == 0
        if committed:
            connection.commit()
        else:
            connection.rollback()
            accepted = 0
    except Exception:
        connection.rollback()
        raise
    return {
        "batch_id": batch_id,
        "csv_rows": total,
        "staged_rows_committed": accepted,
        "already_staged": duplicates,
        "conflicts": conflicts,
        "rejected": rejected,
        "state": "pending_review" if committed else "rolled_back",
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate and stage provider NAV CSV; NEVER publish automatically")
    parser.add_argument("--input", type=Path, required=True, help="Provider-approved CSV source file")
    parser.add_argument("--database", type=Path, required=True, help="Local/private SQLite staging DB")
    parser.add_argument("--batch-id", type=str, required=True)
    args = parser.parse_args()
    args.database.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(args.database) as connection:
        with args.input.open("r", encoding="utf-8-sig", newline="") as fileobj:
            result = ingest_csv(fileobj, connection, args.batch_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["state"] == "pending_review" else 2


if __name__ == "__main__":
    raise SystemExit(main())
