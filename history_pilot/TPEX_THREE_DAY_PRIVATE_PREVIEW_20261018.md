# 櫃買中心小批量歷史缺口｜三日私人暫存預檢（2026-10-18）

## 目標與既有官方日曆驗收

Render 上已成功取得櫃買中心 **115 年開休市 PDF**，來源 sha256：`b864f5e572197c54828301391e80fd6b7bae399abd19a60cf601ed460ccd8e2f`，已確認股票／債券交易日期不同，研究窗口中 2026/10/9 為休市日。

Neon 中現有 6488、8069 各 5 筆（2026/10/6～10/13）。依官方預定交易日推算，若將驗收起點提前至 2026/10/1，則 2026/10/1、10/2、10/5 分別在兩檔標的都尚無日線。這是 6 個 **候選待查日**，不是已核實每檔當天無停牌、已掛牌或應有成交。

## 這次執行什麼（上限 3 次 TPEx 行情請求）

- 檢查 Neon **日期欄位（唯讀）**，有新增任何候選日則停止，避免重複抓取。
- 將櫃買年度 PDF 再取一次，必須具有官方股票日曆特徵、四個日期與休市名稱核對通過，且 sha256 必須等於當日已驗收版本（來源改版必須人工檢查，不會默默套用舊日曆）。
- 只請求櫃買中心固定 2026/10/1、10/2、10/5 三個日別官方收盤行情來源；每次必須能讀到 6488、8069 二檔 **正確日期、成交股數單位明確、有效 OHLC、無欄位歧義** 才接受。
- 以私人 `TemporaryDirectory` 的本地 SQLite 驗證關聯完整性與日線品質；任何批次不合格直接停止，**不會將部分日線寫入 Neon**。
- 暫存資料僅供當次 Python 程式驗證，函式結束即刪掉 SQLite；私人 Render 日誌只有筆數、請求次數、狀態與布林旗標，沒有原始開高低收、股數、成交額或原始來源回應。
- **本階段完全不進行 Neon 寫入，不供公開網站、不當作 SD 或六策略回測已完成除權息／ETF 分割還原。**

## 執行與回復

1. 備份：`backup/history-pilot-before-tpex-calendar-20261018`。
2. 程式：`history_pilot/tpex_three_day_preview.py`；10 個離線測試 `tests/test_tpex_three_day_preview.py`。
3. 實測時只設 `PILOT_TPEX_3DAY_PREVIEW_ON_BOOT=1`；其他 `PILOT_FETCH_ON_BOOT`、`PILOT_BACKFILL_ON_BOOT`、`PILOT_COVERAGE_AUDIT_ON_BOOT`、`PILOT_TWSE_CALENDAR_PROBE_ON_BOOT`、`PILOT_TPEX_CALENDAR_PROBE_ON_BOOT` 必須全是 `0`。
4. Build 若通過 93 項測試，啟動日誌尋找 `HISTORY_TPEX_THREE_DAY_PREVIEW`；若失敗尋找 `HISTORY_TPEX_THREE_DAY_PREVIEW_FAILED`，須排查來源，**不能宣布回補成功**。
5. 驗收後設回全部開關 `0`，執行部署驗證 `HISTORY_IMPORTS_DISABLED`，並核對 Neon 同步紀錄、核准行情筆數不變。

接下來進入真正入庫前，必須核對經驗證的完整備份（目前先前 Drive 備份僅 18 筆），以及新加 6 筆的企業行動事件與逐日營業狀態。上市、上櫃全市場多年歷史回補仍未啟用。
