# 第三階段｜證交所 2026 官方開休市曆日與 Neon 研究庫核對

**狀態：只做一次性官方日曆讀取 + Neon 日期欄位唯讀核對。**

- 官方來源：`https://www.twse.com.tw/holidaySchedule/holidaySchedule?response=json`，證交所年度 `stat=ok / queryYear=2026` 表格。只對 2026 年、27 筆已知特殊日事件實作，若欄位、年份、日期、筆數或事件類型改變，直接報錯等待人工確認。
- 已核對主要 2026 休市參考：02/12、02/13、09/25、09/28、10/09、10/26，並分辨 1/2、2/11、2/23 等特殊「開市」紀錄，不可把「最後交易日／開始交易日」錯判休市。
- 只針對 0050、2330 已有的 2026-09-01～2026-10-13 範例範圍比較。預定每檔 28 個交易日，當中 9/25、9/28、10/9 屬公告休市；**預定開市不是已獨立核實的實際開市**，颱風臨停及個別標的停牌仍須逐日公告核對。
- 最小資料原則：Neon 只查 `SELECT market,code,day FROM history_stage.approved_bars`，交易標記 `SET TRANSACTION READ ONLY`。沒有抓取或顯示單一開高低收／成交量、沒有更動任何行情報價。
- 結果紀錄只有預定開市筆數、已有日期筆數差異、官方來源 SHA256、異常日期（最多每檔12個）及永遠關閉的正式核准旗標。`actual_daily_sessions_verified`、`complete_historical_coverage_verified`、`tpex_calendar_verified`、`stock_etf_corporate_actions_adjusted` 均為 false。
- 上櫃的休市表來自櫃買中心另一網站，**不會**將證交所 2026 開休市結果直接當成上櫃的正式日期證據。
- 未取得資料使用授權前，不在公開網站發行原始行情、不整批回補所有股票及 ETF、不修改主圖、SD、六策略或 Excel。

## Render 安全部署

Render 原獨立測試服務 `fiveline-history-pilot-20261014`，GitHub 分支 `feature/official-history-sqlite-pilot`。

1. 已備份程式：`backup/history-pilot-before-official-twse-calendar-20261014`。
2. 用原本的測試 Build Command 執行全部 Python 測試（原55項加新增合成案例）。
3. 將 `PILOT_FETCH_ON_BOOT=0`、`PILOT_BACKFILL_ON_BOOT=0`、`PILOT_COVERAGE_AUDIT_ON_BOOT=0`，只暫開 `PILOT_TWSE_CALENDAR_PROBE_ON_BOOT=1`。它與所有抓取/入庫任務互斥、執行一次後不排程。
4. 確認紀錄 `HISTORY_TWSE_OFFICIAL_CALENDAR` 及 Neon 版本/核准數/同步次數不變。
5. 關閉日曆試跑開關並再次驗證安全部署。若 API 或欄位異常，僅會看到 `HISTORY_TWSE_CALENDAR_FAILED <ExceptionType>`，不寫入任何報價。

## 下一項尚未完成

(1) 證交所「實際開市」另取證（不能僅依週一至週五推定）；(2) 櫃買中心年度休市資料與個別停牌核驗；(3) 已上市日期與摘牌事件版本庫；(4) 企業行動（除權息/拆分）調整資料與總報酬抽樣逐筆對帳；(5) 全市場多年度分批回補與合法商業資料授權。

本階段不能宣稱全市場多年的回補已完成。

## 2026-10-14 Render 現場驗收結果（已執行）

- 既有 Render 測試服務真正向證交所年度 JSON 端點提出 **一次** HTTPS 請求。
- 官方回傳 `stat=ok` 且 `queryYear=2026`，27 筆已知年度特殊開休市紀錄，對應來源 SHA-256 `7fefe785ea7155a5004a2eb74486ad865ea5c4b5f02ee0cffbbdacb1ca2ea390`。
- 0050、2330，在指定 2026-09-01 至 2026-10-13 研究期間，每檔 28 個公告排除休市後的**預定**交易日；Neon 日期對應缺漏 0，公告休市日卻有行情 0。
- Render Build `69/69` Python 測試通過；新部署顯示 `live`，應用程式紀錄 `HISTORY_TWSE_OFFICIAL_CALENDAR`。
- 檢查後 Neon `bar_versions=66`，`approved_bars=66`，`sync_runs=2`，`pending=0`，與檢查前相同。
- `actual_daily_sessions_verified=false`，`complete_historical_coverage_verified=false`，`neon_mutated=false`；沒有開啟行情匯入，也沒有處理分割／配息還原。

**注意**：此處 0 缺漏是年度公告預定交易日期的對照，並非來源正式核准、日曆臨時異動檢查或全市場資料完整性保證。
