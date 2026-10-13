"""Conservative cross-source class/link duplicate review, never auto-deduplicates funds.

Review groups are *potential* relationships, not the same traded security and not
approval to copy NAV, prices, dividends or historical observations.
"""
from __future__ import annotations
from collections import defaultdict,Counter
from datetime import datetime,timezone
from pathlib import Path
import argparse
import json
import re
import unicodedata

SCHEMA="fiveline_market_identity_review_v1"
CATALOG_SCHEMA="fiveline_market_universe_v1"
FUND_KINDS={"etf","domestic_fund_share_class","offshore_fund_share_class"}
MAX_GROUP_PREVIEW=250
MAX_GROUP_SIZE=75
KINDS={
 "same_verified_isin_and_currency":"相同 ISIN 校驗碼與幣別：可能是同一級別（待人工核對）",
 "same_code_cross_fund_sources":"不同來源同代碼：不得視為同一級別",
 "same_normalized_name_currency":"名稱與幣別一致：仍需 ISIN 與配息級別核對",
 "same_name_different_currency":"相同名稱但不同幣別：通常不可合併"
}

def normalized_name(s):
 s=unicodedata.normalize("NFKC",str(s or "")).casefold().replace("臺","台")
 return "".join(x for x in s if x.isalnum())

def valid_isin(x):
 s=str(x or "").strip().upper()
 if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]",s):return False
 a="".join(str(ord(ch)-55) if ch.isalpha() else ch for ch in s)
 total=0
 for n,ch in enumerate(reversed(a)):
  v=int(ch)
  if n%2:
   v*=2
   if v>9:v-=9
  total+=v
 return total%10==0

def audit(catalog,checked_at=None):
 if catalog.get("schema")!=CATALOG_SCHEMA:
  raise ValueError("market catalog schema mismatch")
 if (catalog.get("share_classes_approved") is not False or
     catalog.get("historical_nav_backfilled") is not False or
     catalog.get("original_excel_modified") is not False or
     catalog.get("customer_records_included") is not False):
  raise ValueError("catalog safety gate failed")
 rows=catalog.get("instruments",[])
 if not isinstance(rows,list) or len(rows)!=catalog.get("total_rows") or not rows:
  raise ValueError("catalog integrity: bad row count")
 if len({x["uid"] for x in rows})!=len(rows):
  raise ValueError("duplicate instrument uid")
 funds=[x for x in rows if x.get("category") in FUND_KINDS]
 if not funds:raise ValueError("zero fund/ETF records")
 for r in funds:
  if r.get("class_identity_approved") is not False or r.get("historical_prices_ready") is not False:
   raise ValueError("unexpected approved or backfilled identity")

 by_isin=defaultdict(list)
 by_code=defaultdict(list)
 by_name_ccy=defaultdict(list)
 by_name=defaultdict(list)
 isin_missing=0
 isin_invalid=0
 for r in funds:
  isin=str(r.get("isin") or "").strip().upper()
  ccy=str(r.get("currency") or "").strip().upper()
  code=str(r["code"]).strip().upper()
  name=normalized_name(r["name"])
  if isin and valid_isin(isin):by_isin[(isin,ccy)].append(r)
  elif isin:isin_invalid+=1
  else:isin_missing+=1
  if ccy:
   by_code[(code,ccy)].append(r)
   if name:by_name_ccy[(name,ccy)].append(r)
  if name:by_name[name].append(r)

 result=[]
 seen=set()
 flagged_uids=set()
 group_tallies=Counter()
 def record(kind,key,matches):
  distinct={r["uid"]:r for r in matches}
  entries=sorted(distinct.values(),key=lambda a:(a["source"],a["code"],a["name"]))
  if len(entries)<2:return
  if kind=="same_code_cross_fund_sources" and len({x["source"] for x in entries})<2:return
  if kind=="same_name_different_currency" and len({x["currency"] for x in entries})<2:return
  if kind=="same_verified_isin_and_currency" and len({x["source"] for x in entries})<2:return
  token=(kind,tuple(sorted(distinct)))
  if token in seen:return
  seen.add(token)
  group_tallies[kind]+=1
  flagged_uids.update(distinct)
  group={
   "relation":kind,"description":KINDS[kind],"key_preview":str(key)[:115],
   "source_count":len({x["source"] for x in entries}),
   "record_count":len(entries),
   "needs_manual_review":True,"merge_approved":False,
   "records":[{"uid":x["uid"],"source":x["source"],"market":x["market"],
               "category":x["category"],"code":x["code"],"name":x["name"],
               "currency":x["currency"],"isin":x["isin"]}
              for x in entries[:MAX_GROUP_SIZE]],
   "display_truncated":len(entries)>MAX_GROUP_SIZE
  }
  result.append(group)
 for key,items in by_isin.items():record("same_verified_isin_and_currency",key,items)
 for key,items in by_code.items():record("same_code_cross_fund_sources",key,items)
 for key,items in by_name_ccy.items():record("same_normalized_name_currency",key,items)
 for key,items in by_name.items():record("same_name_different_currency",key,items)
 result.sort(key=lambda x:(x["relation"],x["key_preview"]))
 total=len(result)
 report={
  "schema":SCHEMA,
  "checked_at_utc":(checked_at or datetime.now(timezone.utc)).isoformat(),
  "catalog_checked_at_utc":catalog["checked_at_utc"],
  "source_record_total":len(rows),
  "fund_etf_source_record_count":len(funds),
  "distinct_uid_count":len(rows),
  "missing_isin_fund_etf_rows":isin_missing,
  "invalid_isin_fund_etf_rows":isin_invalid,
  "potential_link_group_counts":dict(group_tallies),
  "potential_link_groups_total":total,
  "groups_shown":min(total,MAX_GROUP_PREVIEW),
  "groups_truncated":total>MAX_GROUP_PREVIEW,
  "flagged_distinct_source_rows":len(flagged_uids),
  "unique_investment_products_verified":None,
  "automatic_deduplication_applied":False,
  "all_fund_classes_approved":False,
  "historical_nav_backfilled":False,
  "original_excel_modified":False,
  "customer_data_included":False,
  "notes":[
   "Cross-source matches and same-name similarity are review hints, not automatic class or ticker identity.",
   "ISIN can identify a share class when official sources, currency and documentation all match; a checksum does not itself prove identity.",
   "Missing ISIN cannot be resolved by names or ticker-code suffix; ETFs and NAV share classes are distinct price concepts.",
   "No user holdings, prices, dividend events or existing six-strategy calculations are modified."
  ],
  "groups":result[:MAX_GROUP_PREVIEW],
 }
 return report

def main():
 cli=argparse.ArgumentParser()
 cli.add_argument("--catalog",type=Path,required=True)
 cli.add_argument("--output",type=Path,required=True)
 args=cli.parse_args()
 catalog=json.loads(args.catalog.read_text(encoding="utf-8"))
 report=audit(catalog)
 args.output.parent.mkdir(parents=True,exist_ok=True)
 args.output.write_text(json.dumps(report,ensure_ascii=False,separators=(",",":"))+"\n",encoding="utf-8")
 print(json.dumps({k:v for k,v in report.items() if k not in ("groups","notes")},ensure_ascii=False))
if __name__=="__main__":main()
