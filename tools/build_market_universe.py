"""Read-only, government open-data *instrument directory* (NOT market prices).

Builds a market-wide catalog from bulk official endpoints. Public sources only.
No original Excel, customer data, user watchlists, historical NAV or strategies are
modified. Feed failures are visible: never report 'complete' if any source fails.
ETF may also appear in SITCA NAV as a separate share class; do not silently equate.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import ssl
from collections import Counter
from datetime import datetime,timezone
from hashlib import sha256
from pathlib import Path
from urllib.request import Request,urlopen

SCHEMA="fiveline_market_universe_v1"
MAX_BYTES=32_000_000
FEEDS={
 "twse_listed_stocks":{
  "url":"https://openapi.twse.com.tw/v1/opendata/t187ap03_L",
  "dataset":"https://data.gov.tw/dataset/18419","kind":"listed_stock","market":"TWSE","format":"json",
  "code":("公司代號","CompanyCode"),"name":("公司簡稱","公司名稱","CompanyAbbreviation","CompanyName")
 },
 "tpex_otc_stocks":{
  "url":"https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O",
  "fallback_url":"https://mopsfin.twse.com.tw/opendata/t187ap03_O.csv",
  "dataset":"https://data.gov.tw/dataset/25036","kind":"otc_stock","market":"TPEX","format":"json",
  "code":("公司代號","SecuritiesCompanyCode","CompanyCode","SecuritiesCompanyCode"),
  "name":("公司簡稱","公司名稱","CompanyAbbreviation","CompanyName")
 },
 "twse_etfs":{
  "url":"https://openapi.twse.com.tw/v1/opendata/t187ap47_L",
  "dataset":"https://data.gov.tw/dataset/157399","kind":"etf","market":"TWSE","format":"json",
  "code":("基金代號","基金代碼","FundCode"),
  "name":("基金簡稱","基金中文名稱","基金名稱","FundName")
 },
 "sitca_domestic_funds":{
  "url":"https://www.sitca.org.tw/MemberK0000/F/03/nav.csv",
  "dataset":"https://data.gov.tw/dataset/11109","kind":"domestic_fund_share_class","market":"SITCA","format":"csv",
  "code":("基金代號",),"name":("基金名稱",),"currency":("幣別","計價幣別")
 },
 "tdcc_offshore_funds":{
  "url":"https://opendata.tdcc.com.tw/getOD.ashx?id=3-2",
  "dataset":"https://data.gov.tw/dataset/11639","kind":"offshore_fund_share_class","market":"TDCC","format":"csv",
  "code":("基金代碼",),"name":("基金名稱",),"currency":("計價幣別",),"isin":("基金ISIN_CODE",)
 }
}
# Collection of official public catalog data is not a guarantee of complete
# universes outside these sources (e.g. all global funds, TPEX ETFs, unlisted).
NON_COVERED=("櫃買中心上櫃ETF獨立完整基本資料來源仍需增補",
             "全球所有境外基金不等於臺灣核備之境外基金",
             "興櫃、期貨、ETN、權證與海外上市個股不在本階段涵蓋範圍",
             "境內基金與證交所ETF可能為同一檔基金的不同證券／級別，未核對不可合併",
             "本目錄為標的名單，不含完整歷史行情或已核准的NAV回補")

def chosen(row,names):
 for name in names:
  value=row.get(name)
  if value is not None and str(value).strip():return str(value).strip()
 return ""

def parse_bytes(raw,fmt):
 if len(raw)>MAX_BYTES or not raw:raise ValueError("official source size invalid")
 if fmt=="json":
  obj=json.loads(raw.decode("utf-8-sig"))
  if isinstance(obj,dict):
   # Some providers wrap data as {"data":[...]}.
   obj=obj.get("data",obj.get("Data"))
  if not isinstance(obj,list) or not obj:raise ValueError("official JSON is not a nonempty record array")
  return obj
 for codec in ("utf-8-sig","cp950","big5"):
  try:txt=raw.decode(codec);break
  except UnicodeDecodeError:continue
 else:raise ValueError("unknown official CSV encoding")
 if "<html" in txt[:350].lower():raise ValueError("provider returned HTML")
 rows=list(csv.DictReader(io.StringIO(txt)))
 if not rows:raise ValueError("empty official CSV")
 return rows

def download(name,timeout=45):
 cfg=FEEDS[name]
 ctx=ssl.create_default_context()
 if name=="sitca_domestic_funds" and hasattr(ssl,"VERIFY_X509_STRICT"):
  ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
 req=Request(cfg["url"],headers={"Accept":"application/json,text/csv,*/*","User-Agent":"FivelinePublicCatalogReader/1.0"})
 try:
  with urlopen(req,timeout=timeout,context=ctx) as response:
   if response.status!=200:raise ValueError("non-200 official provider")
   data=response.read(MAX_BYTES+1)
  if len(data)>MAX_BYTES:raise ValueError("excessively large official dataset")
  return data, cfg["format"],cfg["url"]
 except Exception:
  if not cfg.get("fallback_url"):raise
  backup=cfg["fallback_url"]
  with urlopen(Request(backup,headers={"User-Agent":"FivelinePublicCatalogReader/1.0"}),timeout=timeout,context=ctx) as response:
   if response.status!=200:raise ValueError("official fallback non-200")
   data=response.read(MAX_BYTES+1)
  if len(data)>MAX_BYTES:raise ValueError("official fallback too large")
  return data,"csv",backup

def normalize_source(key,raw,format_override=None,actual_url=None):
 config=FEEDS[key]
 rows=parse_bytes(raw,format_override or config["format"])
 if not isinstance(rows,list) or not rows or not isinstance(rows[0],dict):
  raise ValueError("unsupported official source body")
 fields=set().union(*(set(r) for r in rows[:25] if isinstance(r,dict)))
 if not (any(k in fields for k in config["code"]) and any(k in fields for k in config["name"])):
  raise ValueError(f"official schema changed ({key}), sample fields={list(sorted(fields))[:15]}")
 got={}
 ignored=0
 for row in rows:
  if not isinstance(row,dict):ignored+=1;continue
  code=chosen(row,config["code"])
  title=chosen(row,config["name"])
  if not code or not title or len(code)>45 or len(title)>320:
   ignored+=1;continue
  currency=chosen(row,config.get("currency",()))
  isin=chosen(row,config.get("isin",())).upper()
  # Domestic NAV can have multiple share classes; do NOT merge merely by code.
  unique=(key,code,isin,currency,title)
  id=sha256("\x1f".join(unique).encode("utf-8")).hexdigest()[:20]
  got[unique]={
   "uid":id,"source":key,"source_dataset":config["dataset"],
   "market":config["market"],"category":config["kind"],"code":code,"name":title,
   "currency":currency,"isin":isin,
   "class_identity_approved":False,
   "historical_prices_ready":False,
   "dividend_split_adjusted":False
  }
 out=sorted(got.values(),key=lambda r:(r["market"],r["code"],r["currency"],r["name"]))
 if len(out)<5:
  raise ValueError("implausibly few official directory rows")
 return {"rows":out,"source_records":len(rows),"accepted_rows":len(out),"ignored_rows":ignored,
         "source_fields":sorted(fields)[:40],"dataset":config["dataset"],"url":actual_url or config["url"]}

def build(sources:dict,errors:dict,now=None):
 now=now or datetime.now(timezone.utc)
 if now.tzinfo is None:raise ValueError("timestamp requires timezone")
 if not sources:raise ValueError("no verified market sources")
 catalog={}
 statistics={}
 for key,res in sources.items():
  if key not in FEEDS:raise ValueError("unrecognized source")
  statistics[key]={"available":True,"records":res["source_records"],
                  "accepted":res["accepted_rows"],"ignored":res["ignored_rows"],
                  "dataset":FEEDS[key]["dataset"],"actual_download_url":res.get("url",FEEDS[key]["url"]),"format":FEEDS[key]["format"],
                  "fields":res["source_fields"]}
  for row in res["rows"]:
   if row["uid"] in catalog and catalog[row["uid"]]!=row:
    raise ValueError("public UID collision")
   catalog[row["uid"]]=row
 for key,exc in errors.items():
  if key not in FEEDS:raise ValueError("unrecognized failed source")
  statistics[key]={"available":False,"error":str(exc)[:180],"dataset":FEEDS[key]["dataset"]}
 rows=sorted(catalog.values(),key=lambda r:(r["category"],r["code"],r["name"],r["uid"]))
 counts=dict(Counter(row["category"] for row in rows))
 return {
  "schema":SCHEMA,"checked_at_utc":now.astimezone(timezone.utc).isoformat(),
  "manifest_source":"TWSE / TPEx / SITCA / TDCC official open datasets",
  "license":"https://data.gov.tw/license",
  "providers":statistics,"requested_sources":list(FEEDS),
  "covered_sources":sorted(sources),"failed_sources":sorted(errors),
  "catalog_complete_for_configured_sources":set(sources)==set(FEEDS) and not errors,
  "total_rows":len(rows),"category_counts":counts,"known_coverage_gaps":list(NON_COVERED),
  "market_data_live_prices":False,
  "historical_nav_backfilled":False,
  "share_classes_approved":False,
  "original_excel_modified":False,
  "customer_records_included":False,
  "instruments":rows,
 }

def main():
 p=argparse.ArgumentParser()
 p.add_argument("--out",type=Path,default=Path("public_market_universe/latest.json"))
 p.add_argument("--allow-partial",action="store_true")
 args=p.parse_args()
 available={}
 errors={}
 for name in FEEDS:
  try:
   raw,format_used,used_url=download(name)
   parsed=normalize_source(name,raw,format_override=format_used,actual_url=used_url)
   available[name]=parsed
   print(json.dumps({"provider":name,"accepted":parsed["accepted_rows"],
     "sample_fields":parsed["source_fields"][:9]},ensure_ascii=False),flush=True)
  except Exception as e:
   errors[name]=type(e).__name__+": "+str(e)[:150]
   print(json.dumps({"provider":name,"error":errors[name]},ensure_ascii=False),flush=True)
 result=build(available,errors)
 args.out.parent.mkdir(parents=True,exist_ok=True)
 args.out.write_text(json.dumps(result,ensure_ascii=False,separators=(",",":"))+"\n",encoding="utf-8")
 print(json.dumps({k:v for k,v in result.items() if k not in ("instruments","providers")},ensure_ascii=False))
 print("PUBLIC_CATALOG_OUTPUT",args.out)
 if errors and not args.allow_partial:return 2
 return 0

if __name__=="__main__":raise SystemExit(main())
