#!/usr/bin/env python3
"""Isolated private TWSE/TPEx daily-bar import pilot; stdlib only, no publication."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal, InvalidOperation
from pathlib import Path

TWSE_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY"
TPEX_URL = "https://www.tpex.org.tw/web/stock/aftertrading/otc_quotes_no1430/stk_wn1430_result.php"
EMPTY = {"", "-", "--", "---", "N/A", "null", "None"}


def iso_date(value: str) -> str:
    """Convert ROC 115/10/13, ROC 1151013, or ISO to YYYY-MM-DD."""
    s = str(value).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return dt.date.fromisoformat(s).isoformat()
    match = re.fullmatch(r"(\d{2,3})/(\d{1,2})/(\d{1,2})", s)
    if match:
        y, m, d = map(int, match.groups())
    elif re.fullmatch(r"\d{7}", s):
        y, m, d = int(s[:3]), int(s[3:5]), int(s[5:7])
    else:
        raise ValueError(f"Unsupported trade date format: {s!r}")
    return dt.date(y + 1911, m, d).isoformat()


def decimal_string(value) -> str:
    if value is None or str(value).strip() in EMPTY:
        raise ValueError("missing price")
    clean = str(value).replace(",", "").strip()
    try:
        dec = Decimal(clean)
    except InvalidOperation as exc:
        raise ValueError("non-numeric price") from exc
    if not dec.is_finite() or dec <= 0:
        raise ValueError("non-positive price")
    return format(dec, "f")


def whole_number(value) -> int:
    if value is None or str(value).strip() in EMPTY:
        raise ValueError("missing trading volume/value")
    try:
        number = Decimal(str(value).replace(",", "").strip())
    except InvalidOperation as exc:
        raise ValueError("non-numeric trading volume/value") from exc
    if not number.is_finite() or number < 0 or number != number.to_integral_value():
        raise ValueError("invalid integer trading volume/value")
    return int(number)


def lookup_fields(fields: list, row: list, name_groups: list[tuple[str, ...]]):
    if len(fields) != len(row):
        raise ValueError("field count does not match data row")
    compact = [re.sub(r"[\s()（）]", "", str(f)) for f in fields]
    result = []
    for alternatives in name_groups:
        indices = [i for i, f in enumerate(compact) if f in alternatives]
        if len(indices) != 1:
            raise ValueError(f"missing/ambiguous source column: {alternatives}")
        result.append((row[indices[0]], compact[indices[0]]))
    return result


def make_bar(market, code, day, op, hi, lo, cl, vol, turnover, note="") -> dict:
    if not re.fullmatch(r"[0-9A-Za-z]{4,8}", str(code).strip()):
        raise ValueError("invalid security code")
    d = iso_date(day)
    o, h, l, c = (decimal_string(x) for x in (op, hi, lo, cl))
    if Decimal(h) < max(Decimal(o), Decimal(c), Decimal(l)) or Decimal(l) > min(Decimal(o), Decimal(c), Decimal(h)):
        raise ValueError("OHLC inconsistency")
    v = whole_number(vol)
    if v == 0:
        raise ValueError("no trade: zero volume")
    t = whole_number(turnover) if turnover is not None else None
    return {"market": market, "code": str(code).strip(), "day": d,
            "open": o, "high": h, "low": l, "close": c,
            "volume_shares": v, "turnover_twd": t, "note": str(note or "").strip()}


def parse_twse(doc: dict, code: str, year_month: str) -> tuple[list[dict], list[str]]:
    if doc.get("stat") != "OK":
        raise ValueError("TWSE response not OK")
    fields, data = doc.get("fields"), doc.get("data")
    if not isinstance(fields, list) or not isinstance(data, list):
        raise ValueError("TWSE schema changed")
    bars, errors = [], []
    for idx, row in enumerate(data):
        try:
            if not isinstance(row, list):
                raise ValueError("not a list row")
            extracted = lookup_fields(fields, row, [
                ("日期",), ("開盤價",), ("最高價",), ("最低價",), ("收盤價",),
                ("成交股數",), ("成交金額",), ("註記",)])
            (day, _), (op, _), (hi, _), (lo, _), (cl, _), (vol, _), (turn, _), (note, _) = extracted
            bar = make_bar("TWSE", code, day, op, hi, lo, cl, vol, turn, note)
            if bar["day"][:7] != year_month:
                raise ValueError("row date outside requested month")
            bars.append(bar)
        except (ValueError, TypeError) as exc:
            errors.append(f"row {idx + 1}: {exc}")
    if not bars and not errors:
        raise ValueError("TWSE empty month (no accepted trading days)")
    return bars, errors


def tpex_volume(raw, field: str | None, legacy_unit: str | None):
    if field:
        if "仟股" in field or "千股" in field:
            return whole_number(raw) * 1000
        if "成交股數" in field or "交易股數" in field:
            return whole_number(raw)
        raise ValueError(f"unknown TPEx volume unit in field {field}")
    if legacy_unit == "shares":
        return whole_number(raw)
    if legacy_unit == "thousand_shares":
        return whole_number(raw) * 1000
    raise ValueError("TPEx legacy aaData needs --tpex-legacy-volume-unit")


def parse_tpex(doc: dict, requested_day: str, selected_codes: set[str], legacy_unit=None) -> tuple[list[dict], list[str]]:
    if not isinstance(doc, dict):
        raise ValueError("TPEx schema changed: expected JSON object")
    date_str = doc.get("reportDate") or doc.get("date")
    if date_str and iso_date(str(date_str)) != requested_day:
        raise ValueError("TPEx response date differs from requested day")
    fields, data = None, None
    if isinstance(doc.get("tables"), list):
        for table in doc["tables"]:
            ff, rr = table.get("fields"), table.get("data")
            if isinstance(ff, list) and isinstance(rr, list) and any("代號" in str(f) for f in ff) and any("收盤" in str(f) for f in ff):
                fields, data = ff, rr
                break
        if fields is None:
            raise ValueError("TPEx no recognized OHLCV table")
    elif isinstance(doc.get("aaData"), list):
        data = doc["aaData"]
    else:
        raise ValueError("TPEx schema changed: missing tables/aaData")
    bars, errors = [], []
    for idx, row in enumerate(data):
        try:
            if not isinstance(row, list):
                raise ValueError("not a list row")
            if fields is None:
                if len(row) < 10:
                    raise ValueError("legacy row has too few columns")
                code, _, cl, _, op, hi, lo, vol, turn = row[:9]
                note = ""
                vol_shares = tpex_volume(vol, None, legacy_unit)
            else:
                vals = lookup_fields(fields, row, [
                    ("代號", "證券代號", "股票代號"),
                    ("開盤", "開盤價"), ("最高", "最高價"), ("最低", "最低價"),
                    ("收盤", "收盤價"),
                    ("成交股數", "交易股數", "成交仟股", "成交千股"),
                    ("成交金額元", "成交金額", "成交金額仟元", "成交金額千元")])
                (code, _), (op, _), (hi, _), (lo, _), (cl, _), (vol, v_name), (turn, t_name) = vals
                vol_shares = tpex_volume(vol, v_name, legacy_unit)
                if "仟元" in t_name or "千元" in t_name:
                    turn = whole_number(turn) * 1000
                note = ""
            if str(code).strip() not in selected_codes:
                continue
            bars.append(make_bar("TPEX", code, requested_day, op, hi, lo, cl, vol_shares, turn, note))
        except (ValueError, TypeError) as exc:
            errors.append(f"row {idx + 1}: {exc}")
    return bars, errors


def fetch_json(url: str, params: dict) -> dict:
    link = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(link, headers={"User-Agent": "FivelineHistoryPilot/0.1 (restricted research)", "Accept": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=25) as response:
                if response.status != 200:
                    raise RuntimeError(f"HTTP {response.status}")
                payload = response.read(8_000_000)
                return json.loads(payload.decode("utf-8-sig"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            if attempt == 2:
                raise RuntimeError(f"Official request failed: {url} ({type(exc).__name__})") from exc
            time.sleep(1 + attempt * 2)
    raise RuntimeError("fetch exhausted")


SCHEMA = """
CREATE TABLE IF NOT EXISTS bar_versions (
 market TEXT NOT NULL CHECK(market IN ('TWSE','TPEX')),
 code TEXT NOT NULL, day TEXT NOT NULL, version INTEGER NOT NULL,
 open TEXT NOT NULL, high TEXT NOT NULL, low TEXT NOT NULL, close TEXT NOT NULL,
 volume_shares INTEGER NOT NULL, turnover_twd INTEGER, note TEXT NOT NULL,
 fingerprint TEXT NOT NULL, fetched_at TEXT NOT NULL, approved INTEGER NOT NULL,
 review_reason TEXT NOT NULL DEFAULT '',
 PRIMARY KEY (market,code,day,version)
);
CREATE TABLE IF NOT EXISTS ingest_runs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, request_key TEXT NOT NULL,
 fetched_at TEXT NOT NULL, retrieved INTEGER NOT NULL, inserted INTEGER NOT NULL,
 unchanged INTEGER NOT NULL, pending INTEGER NOT NULL, rejected INTEGER NOT NULL,
 error_excerpt TEXT NOT NULL
);
CREATE VIEW IF NOT EXISTS approved_bars AS
 SELECT v.* FROM bar_versions v
 WHERE v.approved = 1 AND v.version = (
  SELECT MAX(x.version) FROM bar_versions x WHERE x.market=v.market AND x.code=v.code AND x.day=v.day AND x.approved=1
 );
CREATE VIEW IF NOT EXISTS pending_review AS
 SELECT * FROM bar_versions WHERE approved=0;
"""


def open_db(db_path: Path):
    db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        db_path.parent.chmod(0o700)
    except OSError:
        pass
    conn = sqlite3.connect(db_path)
    try:
        db_path.chmod(0o600)
    except OSError:
        pass
    conn.executescript(SCHEMA)
    return conn


def stage_batch(conn: sqlite3.Connection, source: str, key: str, bars: list[dict], errors: list[str]) -> dict:
    count = {"source": source, "request": key, "retrieved": len(bars) + len(errors), "inserted": 0,
             "unchanged": 0, "pending": 0, "rejected": len(errors)}
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    with conn:
        for bar in bars:
            payload = {k: bar[k] for k in ("market", "code", "day", "open", "high", "low", "close", "volume_shares", "turnover_twd", "note")}
            fp = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
            last = conn.execute("SELECT version, fingerprint FROM bar_versions WHERE market=? AND code=? AND day=? ORDER BY version DESC LIMIT 1", (bar["market"], bar["code"], bar["day"])).fetchone()
            if last and last[1] == fp:
                count["unchanged"] += 1
                continue
            reason = "data_revised" if last else ""
            if not last:
                prior = conn.execute("SELECT close FROM approved_bars WHERE market=? AND code=? AND day<? ORDER BY day DESC LIMIT 1", (bar["market"], bar["code"], bar["day"])).fetchone()
                if prior and abs(Decimal(bar["close"]) / Decimal(prior[0]) - 1) > Decimal("0.30"):
                    reason = "price_jump_over_30pct_review_corporate_action"
            approved = 0 if reason else 1
            conn.execute("""INSERT INTO bar_versions(market,code,day,version,open,high,low,close,volume_shares,turnover_twd,note,fingerprint,fetched_at,approved,review_reason)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
               (bar["market"], bar["code"], bar["day"], 1 if not last else last[0] + 1,
                bar["open"], bar["high"], bar["low"], bar["close"], bar["volume_shares"], bar["turnover_twd"], bar["note"], fp, now, approved, reason))
            count["inserted"] += 1
            if not approved:
                count["pending"] += 1
        conn.execute("INSERT INTO ingest_runs(source,request_key,fetched_at,retrieved,inserted,unchanged,pending,rejected,error_excerpt) VALUES(?,?,?,?,?,?,?,?,?)",
                     (source, key, now, count["retrieved"], count["inserted"], count["unchanged"], count["pending"], count["rejected"], "; ".join(errors[:4])[:600]))
    return count


def run(date: str, db_path: Path, twse_codes: list[str], tpex_codes: list[str], legacy_unit: str | None):
    date = iso_date(date)
    year_month = date[:7]
    roc = dt.date.fromisoformat(date).year - 1911
    roc_date = f"{roc}/{date[5:7]}/{date[8:10]}"
    counts = []
    with open_db(db_path) as conn:
        for code in twse_codes:
            doc = fetch_json(TWSE_URL, {"date": year_month.replace("-", "") + "01", "stockNo": code, "response": "json"})
            bars, errors = parse_twse(doc, code, year_month)
            counts.append(stage_batch(conn, "TWSE", f"{code}/{year_month}", bars, errors))
            time.sleep(0.7)
        if tpex_codes:
            doc = fetch_json(TPEX_URL, {"l": "zh-tw", "d": roc_date, "se": "EW"})
            bars, errors = parse_tpex(doc, date, set(tpex_codes), legacy_unit)
            found = {b["code"] for b in bars}
            errors.extend(f"code {c} absent/invalid on requested date" for c in tpex_codes if c not in found)
            counts.append(stage_batch(conn, "TPEX", date, bars, errors))
        pending = conn.execute("SELECT COUNT(*) FROM pending_review").fetchone()[0]
        approved = conn.execute("SELECT COUNT(*) FROM approved_bars").fetchone()[0]
    return {"requested_date": date, "db": str(db_path), "pilot_only": True, "batches": counts, "approved_bars": approved, "pending_review": pending}


def main():
    cli = argparse.ArgumentParser(description="Run a small restricted official OHLCV history pilot. Never publishes raw rows.")
    cli.add_argument("--date", required=True, help="YYYY-MM-DD, e.g. 2026-10-13")
    cli.add_argument("--db", type=Path, default=Path("private_data/history_pilot.sqlite3"))
    cli.add_argument("--twse", nargs="*", default=["2330", "0050"])
    cli.add_argument("--tpex", nargs="*", default=["8069", "6488"])
    cli.add_argument("--tpex-legacy-volume-unit", choices=["shares", "thousand_shares"], default=None,
                     help="Required only for old TPEx aaData without explicit column units; verify on official source before selection.")
    args = cli.parse_args()
    result = run(args.date, args.db, args.twse, args.tpex, args.tpex_legacy_volume_unit)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
