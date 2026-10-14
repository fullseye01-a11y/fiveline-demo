# 官方歷史行情第 5 階段實際驗收｜2026-10-14

本文件只記錄執行結果與筆數，不含來源原始日 OHLCV，亦不公開 Neon 憑證。

## 受控回補成功

- Render 私人測試服務 `fiveline-history-pilot-20261014`，branch `feature/official-history-sqlite-pilot`
- 程式 `history_pilot/backfill.py`
- 官方來源請求 7 次：TWSE 2330/2026-09、0050/2026-09 各 1 次；TPEx 2026-10-06、07、08、12、13 各 1 次
- 官方來源解析後共 50 筆暫存候選，48 筆新版本成功寫入 Neon，2 筆已存在並保持不變
- 本次需人工覆核 0 筆，全部回補後 Neon 核准總數 66 筆
- Render 原始執行紀錄包含 `HISTORY_BACKFILL_SUCCESS`，未回傳原始行情到公開網站
- Neon SQL 查證：
  - TWSE 2330：28 筆，日期 2026-09-01 至 2026-10-13
  - TWSE 0050：28 筆，日期 2026-09-01 至 2026-10-13
  - TPEx 8069：5 筆，日期 2026-10-06 至 2026-10-13
  - TPEx 6488：5 筆，日期 2026-10-06 至 2026-10-13
  - `history_stage.bar_versions` 66 筆，`approved_bars` 66 筆
  - 待人工覆核版本 0；歷史修訂版本 0；重複 `market/code/day/version` 主鍵 0；重複版本 fingerprints 0
  - 全部已有 2 次 SQL 同步批次，累計暫存 68 筆、實際插入 66、未改變 2
- 回補完成後已將 `PILOT_BACKFILL_ON_BOOT=0`、`PILOT_FETCH_ON_BOOT=0`，再部署確認 Render Live 並出現 `HISTORY_IMPORTS_DISABLED`
- 最新部署：44 / 44 單元測試成功
- 正式客戶網站、全市場名錄與其他 Render 服務不受影響

## 尚未完成及使用限制

- 這只有四檔、指定日期與單月份資料的試跑，不是全市場歷史回補或官方完整交易日曆比對。
- 尚須針對停牌、上市日期、ETF 拆分與股票除權息建立可稽核事件表；不可將未調整 OHLC 直接用作長期復權回測。
- Google Drive 上一次經驗證的備份在回補之前製成，只有 18 筆。Neon 目前 66 筆，因此 **Google Drive 備份尚未反映這次新增的 48 筆**。已設定每週日約 19:00 台灣時間的自動加密備份任務，第一次無人值守執行需核對成功通知，不能提前聲稱最新資料已備妥。
- 禁止將原始資料公開散布，需另外確認來源條款與實際客戶服務用途。
