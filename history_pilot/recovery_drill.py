"""Offline recovery drill for a small encrypted history backup.

Decrypts a private pilot backup and reconstructs it in an ISOLATED IN-MEMORY
SQLite table to prove all rows/versions are internally recoverable. This never
connects to or overwrites the live Neon database. It is NOT a PostgreSQL restore.
"""
from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import io
import json
import sqlite3
from decimal import Decimal, InvalidOperation
from pathlib import Path

if __package__:
    from .encrypted_backup import FIELDS, MAX_ROWS, decrypt_blob
else:
    from encrypted_backup import FIELDS, MAX_ROWS, decrypt_blob

MAX_ENCRYPTED_SIZE = 8 * 1024 * 1024
MAX_PLAINTEXT_SIZE = 48 * 1024 * 1024
HASH_FIELDS = ("market", "code", "day", "open", "high", "low", "close",
               "volume_shares", "turnover_twd", "note")


def restore_drill(path: Path, passphrase: str) -> dict:
    """Validate and replay all backup versions into a disposable SQLite DB."""
    if path.stat().st_size > MAX_ENCRYPTED_SIZE:
        raise ValueError("Encrypted pilot backup exceeds safe size limit")
    encrypted = path.read_bytes()
    compressed = decrypt_blob(encrypted, passphrase)
    with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
        raw = stream.read(MAX_PLAINTEXT_SIZE + 1)
    if len(raw) > MAX_PLAINTEXT_SIZE:
        raise ValueError("Inflated backup exceeds safe size limit")
    doc = json.loads(raw)
    count = doc.get("row_count")
    if doc.get("format") != 1 or not isinstance(count, int) or isinstance(count, bool):
        raise ValueError("Unexpected backup format/count")
    if count < 0 or count > MAX_ROWS or not isinstance(doc.get("rows"), list) or count != len(doc["rows"]):
        raise ValueError("Backup row count mismatch or limit exceeded")

    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("""CREATE TABLE recovered(
            market TEXT NOT NULL, code TEXT NOT NULL, day TEXT NOT NULL,
            version INTEGER NOT NULL, open TEXT NOT NULL, high TEXT NOT NULL,
            low TEXT NOT NULL, close TEXT NOT NULL, volume_shares INTEGER NOT NULL,
            turnover_twd INTEGER, note TEXT NOT NULL, fingerprint TEXT NOT NULL,
            fetched_at TEXT NOT NULL, approved INTEGER NOT NULL, review_reason TEXT NOT NULL,
            PRIMARY KEY(market,code,day,version))""")
        for row in doc["rows"]:
            if not isinstance(row, dict) or set(row) != set(FIELDS):
                raise ValueError("Unknown or missing backup fields")
            if row["market"] not in {"TWSE", "TPEX"}:
                raise ValueError("Invalid exchange")
            day = str(row["day"])
            if dt.date.fromisoformat(day).isoformat() != day:
                raise ValueError("Invalid trade date")
            if not isinstance(row["code"], str) or not row["code"].isalnum() or len(row["code"]) > 8:
                raise ValueError("Invalid security code")
            if type(row["version"]) is not int or row["version"] < 1:
                raise ValueError("Invalid version")
            try:
                o, h, l, c = [Decimal(str(row[k])) for k in ("open", "high", "low", "close")]
                if any(not x.is_finite() or x <= 0 for x in (o, h, l, c)):
                    raise ValueError("Invalid OHLC")
                if h < max(o, c, l) or l > min(o, c, h):
                    raise ValueError("Inconsistent OHLC")
                vol = int(row["volume_shares"])
                if type(row["volume_shares"]) is not int or vol <= 0:
                    raise ValueError("Invalid volume")
                turnover = row["turnover_twd"]
                if turnover is not None and (type(turnover) is not int or turnover < 0):
                    raise ValueError("Invalid turnover")
            except (InvalidOperation, TypeError, OverflowError) as err:
                raise ValueError("Invalid numeric column") from err
            if type(row["approved"]) is not bool:
                raise ValueError("Invalid approval flag")
            # Fingerprint from official normalized staging: protects against
            # internally inconsistent snapshots even if ciphertext authentic.
            normalized = {
                "market": row["market"], "code": row["code"], "day": day,
                "open": format(o.normalize(), "f"), "high": format(h.normalize(), "f"),
                "low": format(l.normalize(), "f"), "close": format(c.normalize(), "f"),
                "volume_shares": vol, "turnover_twd": turnover, "note": row["note"],
            }
            digest = hashlib.sha256(json.dumps(normalized, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            if digest != row["fingerprint"]:
                raise ValueError("Bar fingerprint mismatch")
            try:
                conn.execute("INSERT INTO recovered VALUES (" + ",".join("?" * len(FIELDS)) + ")",
                             tuple(int(row[k]) if k == "approved" else row[k] for k in FIELDS))
            except sqlite3.IntegrityError as err:
                raise ValueError("Duplicate backup key/version") from err
        actual = conn.execute("SELECT COUNT(*) FROM recovered").fetchone()[0]
        if actual != count:
            raise ValueError("Incomplete logical restoration")
        counts = [
            {"market": m, "rows": n}
            for m,n in conn.execute("SELECT market,COUNT(*) FROM recovered GROUP BY market ORDER BY market")
        ]
        return {
            "verified": True, "isolation": "in_memory_sqlite",
            "full_postgres_restore_tested": False,
            "restored_versions": actual, "exchange_totals": counts,
            "live_database_untouched": True,
        }
    finally:
        conn.close()
