# 全市場基金：續跑執行器與真實近期來源驗收（2026-10-18）

本次接續既有 100 級別離線歷史驗證器。基金名錄為 2026-10-16 快照，9,767 筆（境內 4,472、境外 5,295），快照 SHA256：
`8828474be61347793e245b2be7adfb7079892eb23307a6e4ce765b2986c2eb60`。

## 本次真實資料結果

實際取得並解析集保官方基本資料 3-2（5,295 列）、近期 NAV 3-4（22,890 列），對全份名錄分成 98 個最多 100 筆的區塊核對。程式為通用級別比對，沒有只處理三檔指定基金。索引只建立一次，每檔不重新下載全來源；沿用原有代碼、ISIN、完整名稱、幣別、日期、正值及同日衝突規則，另阻擋跨 UID 共用 ISIN。

| 名錄狀態 | 基金紀錄數 |
|---|---:|
| 境外近期 NAV 證據核對通過 | 4,991 |
| 境外需要複核 | 282 |
| 境外跨 UID 共用 ISIN，留待複核 | 22 |
| 境內本次未提供可驗證來源 | 4,472 |
| 合計 | 9,767 |

通過部分共 18,765 筆「基金×日期」近期觀測；每級別最新日期分布為 2026-10-15：4,558 筆、10-14：285 筆、10-13：148 筆。這是記憶體內近期證據核對，並非歷史資料庫匯入，也不證明三年連續性或商用再散布授權。

來源：
- `https://opendata.tdcc.com.tw/getOD.ashx?id=3-2`；原檔 SHA256 `2855a999becea58f962ad596fe236e18c2e618ad116b708711da23a6cd7ff966`。
- `https://opendata.tdcc.com.tw/getOD.ashx?id=3-4`；原檔 SHA256 `5b9b6f3cb2f5541e51aa87448c372b8a8e384f69843dac49b900083660f81463`。
- 本次環境對 SITCA `https://www.sitca.org.tw/MemberK0000/F/03/nav.csv` 的 HEAD 請求收到 HTTP 404；僅代表本次請求結果，尚不能認定來源永久移除。境內不得沿用假定成功或填入推估淨值。

原始 CSV 未提交公開儲存庫；報告僅保留計數與指紋。離線掃描器不能自行證明下載傳輸或法律授權。可使用相同原檔重現：

```bash
python -m adviser_portal.fund_recent_scan --catalog /private/catalog.json --tdcc-basic /private/basic.csv --tdcc-recent /private/recent.csv
```

## 歷史全市場執行與續跑

`fund_market_run.py` 接受既有私有 batch-index 路徑清單，逐批呼叫 `fund_batch_intake`，每份 index 維持 1–100 級別及既有檔案大小限制。所有名錄 UID 都計入分母，未提供歷史檔案另列缺漏，不能視為通過。空交付的狀態是 `AWAITING_HISTORY_BUNDLES`，CLI 結束碼 2。

```bash
python -m adviser_portal.fund_market_run --catalog /private/catalog.json --root /private/bundles --indexes /private/batch-index-list.json --checkpoint /private/run-checkpoint.json --max-batches 5
```

清單格式：`["batch-001.json", "batch-002.json"]`。單批格式沿用 `WHOLE_MARKET_NAV_BATCH_INTAKE_RUNBOOK_20261018.md`。

- 每批完成後原子替換 checkpoint；用同一指令再次執行即可接續。
- 所有輸入重新核對 SHA；原檔、manifest、估值日期證據、身分文件或驗證日變更時重跑該批。名錄變更則拒絕沿用舊 checkpoint。
- 先檢查全部 index 的 UID，跨批重複 UID 在寫入 checkpoint 前拒絕；跨批相同 NAV 原檔會將涉及的所有基金退回複核，包含已快取的前批結果。
- 部分執行時總通過數為 null，避免後批才發現重複檔案卻已宣稱前批核准。checkpoint 是本地操作紀錄，並非簽章、獨立法律證據或基金核准。
- 本次以真實 9,767 筆名錄測試無歷史交付的情境：98 批容量、收到歷史檔案 0、待取得 9,767；沒有將近期 TDCC CSV 假裝成三年歷史。

## 驗證與目前阻礙

249/249 自動測試通過，涵蓋續跑、變更後重驗、跨批重複原檔、名錄版本、缺檔、路徑跳脫與近期資料衝突。既有 HTTP 批次路由另修正為在任何遠端名錄讀取之前拒絕錯誤的分頁、快照與分類參數，避免錯誤請求因來源網路而逾時。

完整三年 NAV 正式匯入仍為 **0 檔／0 列**。尚待取得合法原始歷史序列、同級別估值日期與缺漏 282／共用 ISIN 22 筆身分原因的進一步逐類分析。尚未改動正式資產配置、六策略、客戶資料庫或既有正式網站。本次新增工具仍供離線操作，沒有新增公開原檔上傳或自動排程。
