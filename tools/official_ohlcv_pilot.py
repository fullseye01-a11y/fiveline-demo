"""Guarded official TWSE/TPEx historic OHLCV research pilot.

Never edits Excel, trading strategies, fund NAV, client data or public market
directory records. Raw unadjusted official quotes enter a temporary SQLite file
for research; only *aggregate* integrity metadata may be committed publicly.
Neither source's bulk quote redistribution rights are presumed.
"""
from __future__ import annotations

import argparse
from collections import Counter,defaultdict
from datetime import datetime,date,timezone
from decimal import Decimal,InvalidOperation
from hashlib import sha256
from http.client import HTTPException
import json
from pathlib import Path
import re
import sqlite3
import ssl
import time
from urllib.parse import urlencode
from urllib.request import Request,urlopen

SCHEMA="fiveline_ohlcv_history_pilot_v1"
TWSE_URL="https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY"
TPEX_URL="https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes"
HEADERS={"User-Agent":"Mozilla/5.0 (compatible; FiveLineHistoricalResearch/0.1)",
         "Accept":"application/json,text/plain,*/*","Accept-Encoding":"identity","Connection":"close"}
MAX_BYTES=7_000_000
PILOTS=(("TWSE","2330","listed_stock"),("TWSE","0050","etf"),
        ("TPEX","6488","otc_stock"),("TPEX","006201","etf"))
TWSE_MONTHS=("20241001","20251001","20260901")
TPEX_DAYS=("2025/10/08","2026/09/30")
FIELDS_TWSE=("日期","成交股數","成交金額","開盤價","最高價","最低價","收盤價")
FIELDS_TPEX=("代號","收盤","開盤","最高","最低","成交股數","成交金額(元)")

def roc_date(text):
 s=str(text or "").strip()
 m=re.fullmatch(r"(\d{2,3})/(\d{1,2})/(\d{1,2})",s)
 if not m:raise ValueError("invalid ROC trading date")
 yy,mm,dd=map(int,m.groups())
 if yy<80:raise ValueError("unknown historic date")
 return date(yy+1911,mm,dd).isoformat()

def decimal_or_none(raw):
 s=str(raw or "").strip().replace(",","")
 if s in ("","--","---","N/A","-"):return None
 try:
  v=Decimal(s)
  return v if v.is_finite() else None
 except (InvalidOperation,ValueError):return None

def price_row(market,code,kind,day,opening,high,low,close,volume,turnover,source):
 o,h,l,c=[decimal_or_none(x) for x in (opening,high,low,close)]
 v,amt=decimal_or_none(volume),decimal_or_none(turnover)
 if None in (o,h,l,c,v) or min(o,h,l,c)<=0 or v<0:
  raise ValueError("OHLC or volume is nonnumeric/invalid")
 if h<max(o,c,l) or l>min(o,c,h):
  raise ValueError("invalid OHLC high/low enclosure")
 if amt is not None and amt<0:raise ValueError("negative trading turnover")
 date.fromisoformat(day)
 return {"market":market,"symbol":code,"category":kind,"trade_date":day,
         "open":str(o),"high":str(h),"low":str(l),"close":str(c),
         "volume_shares":str(v),"turnover_twd":str(amt) if amt is not None else None,
         "price_adjustment":"RAW_NOT_ADJUSTED",
         "identity_status":"OFFICIAL_SYMBOL_RESEARCH_ONLY",
         "source":source}

def parse_twse_month(payload,code,kind,requested_month):
 if not isinstance(payload,dict) or payload.get("stat")!="OK":
  raise ValueError("TWSE monthly endpoint did not return OK")
 fields=payload.get("fields")
 if not isinstance(fields,list) or not all(x in fields for x in FIELDS_TWSE):
  raise ValueError("TWSE OHLC fields changed")
 rows=payload.get("data")
 if not isinstance(rows,list) or not rows:raise ValueError("TWSE month has no data")
 seen=set();out=[]
 for cells in rows:
  if not isinstance(cells,list) or len(cells)!=len(fields):raise ValueError("TWSE row schema changed")
  m=dict(zip(fields,cells))
  day=roc_date(m["日期"])
  if day[:7]!=requested_month[:4]+"-"+requested_month[4:6]:raise ValueError("TWSE API month mismatch")
  if day in seen:raise ValueError("duplicate TWSE date")
  seen.add(day)
  # Suspended/no-trade days can lack an OHLC price; do not impute any close.
  if any(decimal_or_none(m[x]) is None for x in ("開盤價","最高價","最低價","收盤價")):continue
  out.append(price_row("TWSE",code,kind,day,m["開盤價"],m["最高價"],m["最低價"],m["收盤價"],m["成交股數"],m["成交金額"],"TWSE_STOCK_DAY"))
 if not out:raise ValueError("TWSE valid OHLC count was zero")
 return sorted(out,key=lambda x:x["trade_date"])

def parse_tpex_day(payload,requested_day,targets):
 if not isinstance(payload,dict) or str(payload.get("stat")).lower()!="ok":
  raise ValueError("TPEx dated quotes did not return ok")
 if payload.get("date")!=requested_day.replace("/",""):
  raise ValueError("TPEx quote requested date mismatch")
 allrows=[]
 tables=payload.get("tables")
 if not isinstance(tables,list) or not tables:raise ValueError("TPEx response has no tables")
 for table in tables:
  fields=table.get("fields")
  rows=table.get("data")
  if not isinstance(fields,list) or not isinstance(rows,list):raise ValueError("TPEx table schema")
  if not all(x in fields for x in FIELDS_TPEX):continue
  for vals in rows:
   if not isinstance(vals,list) or len(vals)!=len(fields):
    raise ValueError("TPEx quote row schema")
   cells=dict(zip(fields,vals))
   sym=str(cells["代號"]).strip()
   if sym not in targets:continue
   raw=["開盤","最高","最低","收盤"]
   if any(decimal_or_none(cells[x]) is None for x in raw):continue
   try:
    allrows.append(price_row("TPEX",sym,targets[sym],requested_day.replace("/","-"),
       cells["開盤"],cells["最高"],cells["最低"],cells["收盤"],
       cells["成交股數"],cells["成交金額(元)"],"TPEX_DAILY_QUOTES"))
   except ValueError as ex:raise ValueError(f"TPEx {sym}: {ex}") from ex
 found=Counter(x["symbol"] for x in allrows)
 if not all(found[sym]==1 for sym in targets):raise ValueError("TPEx selected codes missing or duplicated")
 return sorted(allrows,key=lambda x:x["symbol"])

def fetch_json(url):
 # Exchange web servers occasionally abort chunked HTTP transfers. Retry only
 # transport-level incomplete/invalid-body failures, never a schema mismatch.
 last=None
 for attempt in range(4):
  try:
   req=Request(url,headers=HEADERS)
   with urlopen(req,timeout=32,context=ssl.create_default_context()) as response:
    if response.status!=200:raise ValueError("source HTTP status not 200")
    b=response.read(MAX_BYTES+1)
   if not b or len(b)>MAX_BYTES:raise ValueError("source response empty or too large")
   return json.loads(b.decode("utf-8-sig")),sha256(b).hexdigest()
  except (OSError,HTTPException,ValueError,json.JSONDecodeError) as error:
   last=error
   if attempt<3:time.sleep(1.25*(attempt+1))
 raise ValueError("official historical response unusable after four attempts") from last

def requests_spec():
 spec=[]
 for market,symbol,category in PILOTS:
  if market=="TWSE":
   for month in TWSE_MONTHS:
    url=TWSE_URL+"?"+urlencode({"date":month,"stockNo":symbol,"response":"json"})
    spec.append(("TWSE",symbol,category,month,url))
 for day in TPEX_DAYS:
  url=TPEX_URL+"?"+urlencode({"date":day,"response":"json"})
  spec.append(("TPEX",None,None,day,url))
 return spec

def make_summary(rows,requests,now=None):
 now=now or datetime.now(timezone.utc)
 if now.tzinfo is None:raise ValueError("timezone required")
 by_symbol=defaultdict(list)
 for x in rows:by_symbol[(x["market"],x["symbol"])].append(x)
 symbols=[]
 for market,symbol,category in PILOTS:
  quotes=sorted(by_symbol[(market,symbol)],key=lambda x:x["trade_date"])
  if not quotes:raise ValueError("pilot OHLC missing "+symbol)
  if len({x["trade_date"] for x in quotes})!=len(quotes):
   raise ValueError("date conflict/duplication")
  symbols.append({
   "market":market,"code":symbol,"category":category,
   "price_days":len(quotes),
   "earliest_observed_date":quotes[0]["trade_date"],
   "latest_observed_date":quotes[-1]["trade_date"],
   "continuous_calendar_coverage_verified":False,
   "split_dividend_adjusted":False,
   "historical_total_return_approved":False
  })
 return {
  "schema":SCHEMA,"checked_at_utc":now.astimezone(timezone.utc).isoformat(),
  "requested_sources":len(requests),
  "pilot_symbols":len(PILOTS),
  "pilot_quote_rows":len(rows),
  "sources_verified":sorted({r["source"] for r in rows}),
  "provenance":[{"market":r["market"],"effective_period":r["period"],
                 "payload_sha256":r["sha256"],"source":"TWSE official monthly" if r["market"]=="TWSE" else "TPEx official date-scoped"}
                for r in requests],
  "currency":"TWD",
  "production_coverage_complete":False,
  "all_market_history_backfilled":False,
  "continuous_trading_calendar_verified":False,
  "raw_quotes_publicly_redistributed":False,
  "price_adjustments_applied":False,
  "dividend_adjustments_applied":False,
  "original_excel_modified":False,
  "production_analyses_modified":False,
  "database_location":"ephemeral private research-stage SQLite artifact; not the public website",
  "rights_and_reuse":"Commercial/public historical quote rights require per-source review before redistribution",
  "missing_work":["Trading holiday calendar validation",
                  "Corporate actions and split history",
                  "ETF dividend/cash adjustment",
                  "Full historical exchange-data license check",
                  "Full market end-to-end backfill and incremental refresh"],
  "instruments":symbols}

def build_sqlite(rows,out):
 out.parent.mkdir(parents=True,exist_ok=True)
 if out.exists():raise ValueError("refuse overwrite existing research DB")
 connection=sqlite3.connect(out)
 try:
  connection.executescript("""
  PRAGMA foreign_keys=ON;
  CREATE TABLE quote_raw (
    market TEXT NOT NULL,
    symbol TEXT NOT NULL,
    category TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    open TEXT NOT NULL, high TEXT NOT NULL,
    low TEXT NOT NULL, close TEXT NOT NULL,
    volume_shares TEXT NOT NULL, turnover_twd TEXT,
    source TEXT NOT NULL,
    price_adjustment TEXT NOT NULL DEFAULT 'RAW_NOT_ADJUSTED',
    identity_status TEXT NOT NULL DEFAULT 'OFFICIAL_SYMBOL_RESEARCH_ONLY',
    PRIMARY KEY(market,symbol,trade_date)
  );
  CREATE INDEX ix_quote_raw_symbol_date ON quote_raw(market,symbol,trade_date);
  """)
  columns=("market","symbol","category","trade_date","open","high","low","close",
           "volume_shares","turnover_twd","source","price_adjustment","identity_status")
  for row in rows:
   if row["price_adjustment"]!="RAW_NOT_ADJUSTED":raise ValueError("unexpected adjusted series")
   try:
    connection.execute("INSERT INTO quote_raw VALUES ("+",".join("?" for _ in columns)+")",
        tuple(row.get(k) for k in columns))
   except sqlite3.IntegrityError as ex:
    raise ValueError("same instrument/date collided; refuse to silently override") from ex
  connection.commit()
  if connection.execute("PRAGMA integrity_check").fetchone()[0]!="ok":raise ValueError("SQLite integrity failed")
 finally:connection.close()

def run(out_db,report_path,now=None):
 collected=[];provenance=[]
 for market,code,kind,period,url in requests_spec():
  payload,digest=fetch_json(url)
  if market=="TWSE":records=parse_twse_month(payload,code,kind,period)
  else:records=parse_tpex_day(payload,period,{"6488":"otc_stock","006201":"etf"})
  collected.extend(records)
  provenance.append({"market":market,"period":period,"sha256":digest})
  # Deliberate pacing. This is a tiny pilot, not mass 120-month scraping.
  time.sleep(.65)
 report=make_summary(collected,provenance,now)
 build_sqlite(collected,out_db)
 report_path.parent.mkdir(parents=True,exist_ok=True)
 report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
 return report

def main():
 p=argparse.ArgumentParser()
 p.add_argument("--sqlite",type=Path,default=Path("/tmp/fiveline_research_ohlcv.sqlite"))
 p.add_argument("--report",type=Path,default=Path("/tmp/fiveline_ohlcv_pilot.json"))
 args=p.parse_args()
 s=run(args.sqlite,args.report)
 print(json.dumps(s,ensure_ascii=False,indent=2))
 print("Temporary research SQLite:",args.sqlite)
 print("No production adjustment, investor account, or Excel data changed.")
if __name__=="__main__":main()
