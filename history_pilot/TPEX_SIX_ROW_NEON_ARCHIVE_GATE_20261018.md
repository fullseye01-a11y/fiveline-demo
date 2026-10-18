# TPEx 2026/10/1、10/2、10/5 六筆正式研究入庫｜安全閘門

這不是全市場資料匯入，而是 **2 個上櫃股票 × 3 個已實測交易日 = 6 筆**的受控小量寫入至私人 Neon `history_stage`。資料仍是 **未還原原始行情**，不修改五線譜、SD、六策略、Excel 或客戶記錄。

## 先決條件與備份

- 現有 `Neon` 研究主分支必須只有 66 筆已核准、待人工覆核 0、修訂 0；否則拒絕執行。
- 已建立 Neon 分支 `backup-before-tpex-six-20261018`，由 `main` 的 66 筆資料狀態衍生，確認 `Ready`（2026-10-18），此分支刻意設為 **無 Compute**，只是可還原的父分支時間點副本，不代表已獨立驗證離站/異地備份。原先加密 Google Drive 備份先前仍僅 18 筆，應繼續規劃新的完整備份。
- `PILOT_TPEX_BACKUP_BRANCH_PROOF` 必須和上面備份分支名稱完全一致；此環境變數是**部署操作確認**，不會自行連接 Neon API 證明備份真實性。因此仍需以 Neon 管理紀錄與主分支查核。
- 再次從櫃買中心取得 115 年官方 PDF，核對股票的年度開休市與既有 SHA-256 完全一致。採用上次已通過的公司日線來源，最多再請求 3 次（10/1、10/2、10/5）。
- 入庫前再次查 Neon 已有的四檔研究行情日期，只能接受 6488、8069 恰好仍缺 10/1、10/2、10/5，原 10/6～10/13 的 5 個測試交易日不能有新缺漏。
- 先在短期私人 SQLite 完成 6 筆資料的嚴格 OHLC、成交股數、成交額與唯一日期驗證，再核對 Neon baseline 沒有被其他匯入修改。任何差異就停止並刪掉暫存檔。

## 單一事務受控匯入

沿用既有 `postgres_store.persist_sqlite`，以 PostgreSQL transaction 保存新的 `(market,code,day,version)` 紀錄。程式不得覆蓋或刪除既有 66 筆原價。預期增量：6 筆；核准 72 筆；修訂 0；待覆核 0。

若 Neon 來源版本核對、付款/授權或資料檢查不通過，只輸出錯誤類別，不自動重試，不把原始價格寫入公開 GitHub、網頁、Render 日誌。

操作開關：`PILOT_TPEX_3DAY_ARCHIVE_ON_BOOT=1`，和原先任何匯入、日曆或 preview 工作**互斥**。成功後必須回到 `0`，並再做安全部署驗收。

## 未完成

本次驗證限固定六筆、不等同全市場多年的歷史資料、連續交易日人工核准、個別上市/櫃歷程、除權息、ETF 分割、總報酬與六策略績效更新。正式客戶資料與商用再分發權限仍需另行確認。

## 2026-10-18 真實執行紀錄（完成）

- 官方 TPEx 115 股票休市 PDF 已通過內容比對，3 次正式 TPEX 日別 API 抓取 6488、8069 共 **6 筆**。
- Render Build 首次 105 項測試有一項「備份錯誤訊息大小寫」比對失敗，因此該次部署被阻擋，未寫入 Neon。修正錯誤訊息後重新建置，`105 / 105` 通過，`Build successful`。
- Render 執行 `HISTORY_TPEX_SIX_ROW_NEON_ARCHIVE`：`PRIVATE_NEON_BOUNDED_SIX_ROW_ARCHIVED`；`source_days=3`、`new_versions=6`、`approved_before=66`、`approved_after=72`、`pending=0`、`temporary_quote_spool_deleted=true`，未更新正式回測。
- Neon 以 SQL 再驗證：`bar_versions=72`、`approved_bars=72`、`sync_runs=3`、`version>1=0`、`approved=false=0`。
- 原 TWSE：`2330=28`、`0050=28`；TPEx：`6488=8`、`8069=8`。新增加日期只有各自 2026/10/01、10/02、10/05。
- Neo n（Neon）已於匯入前建立 `backup-before-tpex-six-20261018`，無 compute，並實際查到 `Ready`，父分支為 `main`；此副本不等於異地加密備份。
- 執行完已將 `PILOT_TPEX_3DAY_ARCHIVE_ON_BOOT=0`、`PILOT_TPEX_BACKUP_BRANCH_PROOF` 清空，所有其他歷史抓取 / TPEx 日曆 / preview 開關亦為 `0`，並再次部署 `live`。Render 日誌有 `HISTORY_IMPORTS_DISABLED`、`HISTORY_PILOT_SERVER_READY`，最後建置 `105/105` 通過。

## 下一階段品質閘門

- 個股停復牌與掛牌起訖仍待官方證據；此批取得原始交易行情，不能因此核准所有多年歷史完整性。
- 增補實際除權息及股票/ETF 分割事件（事件類別、公告日、除權息日、支付日、現金每單位及比率、來源證據與版本）；先計算對照，再決定是否建立**額外的還原價序列**。不得覆蓋 `history_stage.bar_versions` 原價。
- 釐清分割/除權與含息報酬的不同定義；檢查配息再投入、股數調整、報酬率與策略結果一致性。尚未取得官方事件及授權就保持正式回測禁用。
- 每次新行情寫入前建立新的有效 Neon 還原分支，並確認 **72 筆的加密異地備份**，才能擴大下一批。
