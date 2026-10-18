# 境外歷史 NAV 分段補查

此流程延續隔離研究資料，不接通正式資產配置、六策略或公開上傳。
來源名錄與查詢範圍保持原快照：2023-10-18 至 2026-10-15。

## 使用

```bash
python -m adviser_portal.fundclear_partition_history --root /private/offshore-history
python -m adviser_portal.fundclear_history_import --root /private/offshore-history --audit --recent /private/tdcc-recent.csv
```

- 僅補查原有 `SOURCE_REQUIRES_REVIEW` 且已記錄 HTTP 500 的級別，每批最多 100 個。
- 依已凍結的官方名錄、有效 ISIN、幣別及原始級別代碼綁定。近期仍有正值 NAV 的級別優先。
- 若使用父基金代碼，必須有已保存的官方子級別選單，精確包含同一完整名稱與代碼；不猜代碼。
- 近期仍有正值 NAV 的級別先使用最多 31 天的區間；其餘級別先以最多 365 天核對，以減少沒有資料時的請求數。來源錯誤區間可縮小至七天內，保持原三年視窗，日期段不重疊、不漏接。近期沒有正值 NAV 不會因此被排除，也不會推定沒有歷史。
- 新請求至少間隔一秒、依序執行。502／503／504 與截斷連線的重試有上限；連續三個最小區間仍有來源錯誤時停止。401／403／429、轉址或 HTML 回應直接停止。
- 作業有系統執行鎖，第二個程序不能取得資料；`pause-requested` 可合作式暫停。成功回應可在重啟後重用。

## 保存與驗收

`responses.jsonl` 與 gzip 保存每次原始回應。可續跑工作紀錄位於 `partitions/*.work.json`；入庫則引用不可變的 `partitions/<class-key>.<manifest-sha>.json`。

不可變紀錄涵蓋完整請求視窗，逐段保存原始請求、原檔指紋及結果。每筆價格保持來源的十進位字串，直接指回該段原檔，不能以重新拼接的 JSON 冒充官方原檔。新價格與級別狀態以同一 SQLite 交易提交。

驗收重新檢查原檔、父基金綁定、分段銜接及每筆價格，另比對每個成功段均有對應價格快照。級別數與原檔數分開計數；起訖、筆數與時間間隔依同一級別的所有分段彙總。

`source_request_coverage_complete` 只代表分段請求均取得可解析的回應，不表示完整估值日曆。即使未取得區間短於十天，也會列入 `source_request_review_ranges`，不補零、不補值、不假定休市。成功分段可保留為研究價格，但級別仍保留來源覆蓋限制。任何成功 HTTP 200 段若有幣別或數值格式錯誤，整個級別不匯入。

近期官方來源的身分欄位衝突現在同時保留預期值與回傳值。9 筆衝突涉及 JSVAAU、JSVCAU、JSVIAU：凍結基本資料及歷史回應為 USD，近期檔標示 JPY；不以其中一份覆寫另一份。

官方基本資料中的「基金成立日」只可另列為來源登載的基金日期，不能當作個別級別成立日。原先 1,225 個歷史覆蓋待查級別中，有 50 個登載日期晚於視窗起點，已在研究備份另列，未據此批准身分或日曆。

所有價格仍為 `UNAPPROVED_SOURCE_OBSERVATION`，正式三年完整核准與商業權利核准維持 0／未核准。執行結果見研究備份內 `partition-recovery-report.json`、`audit-report.json` 及 `recent-reconciliation.json`。
