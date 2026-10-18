# Render 官方歷史日線品質驗證｜2026-10-14

執行對象：既有 Render 免費測試服務 `fiveline-history-pilot-20261014`，分支 `feature/official-history-sqlite-pilot`。**非正式客戶網站，絕不可公布原始逐筆日價格。**

## 新增功能

- `calendar_coverage.py` 僅從 Neon `history_stage.approved_bars` 讀取 `market,code,day` 三欄，且交易開始先執行 `SET TRANSACTION READ ONLY`，不讀取開高低收或量能。
- 只要沒有完整的官方交易日曆與各檔掛牌期間 CSV，一律回報 `provisional_coverage_passed=false`；另一檔同市場曾交易的日期只能視為「疑似缺漏」，不能取代官方日期。
- 若將來另行以私人 CSV 提供官方證據，可檢查各檔實際應交易日、停牌例外、上市前或休市仍有行情的異常。日期需正確、來源網址需為證交所／櫃買中心官方 HTTPS 網域。仍需人工核實公告與再利用條款；程式的網址驗證本身不構成來源真實性與授權保證。
- `server.py` 新增選用開關 `PILOT_COVERAGE_AUDIT_ON_BOOT=1`：只在服務啟動時執行一次讀取與品質摘要；與任何抓取／匯入開關互斥，若同時啟用則服務拒絕啟動。
- Render 私人日誌只記錄筆數與品質旗標。健康端點和前台公開行情權限保持原狀。沒有人為確認官方日曆完整性、除權息／分割事件前，`full_market_history_verified` 永遠為 false。

## CSV 證據資料格式（尚未提供，不能填造）

`calendar.csv`: `market,date,is_open,evidence_url`

`windows.csv`: `market,symbol,start_date,end_date,evidence_url`

`exceptions.csv`: `market,symbol,date,reason,evidence_url`，原因只能 `suspended`、`trading_halt`、`no_trade`。

三份 CSV 不直接放在公開 GitHub 檔案；需逐段確認來源與可用權限後再置入私人執行端。

## 預定測試操作與驗收

1. 備份原分支至 `backup/history-pilot-before-calendar-gate-20261014`。
2. 既有 44 個測試 + 11 個日曆驗證合成測試，Render Build 應全部通過。
3. Render 關閉 `PILOT_FETCH_ON_BOOT` 及 `PILOT_BACKFILL_ON_BOOT`，僅暫開 `PILOT_COVERAGE_AUDIT_ON_BOOT=1` 並部署。
4. 確認啟動紀錄 `HISTORY_COVERAGE_AUDIT`、核准日線為 66，且日曆驗證仍未通過；Neon 各表筆數、版本及未核准數部署前後必須一致。
5. 測試後關閉 `PILOT_COVERAGE_AUDIT_ON_BOOT`，保留程式可回退，正式網站不改動。

**未完成**：多年度日曆、全上市上櫃 ETF 股票回補、同日交易歸因、企業行動 / 配息事件與總報酬算法比對，以及大規模自動下載授權評估。
