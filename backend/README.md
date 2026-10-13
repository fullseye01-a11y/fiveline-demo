# NAV 更新後端｜隔離暫存原型

此資料夾為**未部署的後端開發原型**，不是最新行情服務。本分支**不與既有 V2.6 展示站合併**，不啟動自動更新，也不保存 API 金鑰。

## 第一階段：資料驗證與暫存（已寫出程式，待部署環境實測）

適用於已取得合法授權之供應商匯出的 CSV。需有欄位：

`instrument_id,nav_date,nav_value,currency,share_class,provider`

- `instrument_id` 為內部唯一標的編號，不可只依基金顯示名稱判斷同一類別。
- `share_class` 必須具體填寫配息／累積級別或證券類型。
- `nav_date` 為當地市場／基金淨值生效日，`nav_value` 為**未調整的原始值**。
- 所有金額皆須正值且可解析；不接受無效日期。
- 新批次只寫入 SQLite `nav_staging`，狀態固定 `pending_review`；不寫入正式績效來源。
- 相同數值重匯為可辨識重複，遇不同值一律回滾等待人工檢查，不能偷偷覆蓋。
- 任一資料列無效，整批**回滾**。資料庫路徑必須放在非公開伺服器內。

範例（須先自行備妥**合法來源**的 CSV 與本機私有資料夾）：

```sh
python -m backend.nav_staging --input /private/nav.csv --database /private/nav_staging.sqlite --batch-id review-20261013
python -m unittest discover -s backend/tests -v
```

## 尚未完成

1. 取得且確認供應商各市場／基金 NAV 合法使用、延遲與商業展示授權。
2. 部署私有後端與排程，搭配憑證管理、錯誤告警、來源可追溯性。
3. 實作台股 ETF 的分割與除息、境內外基金幣別／級別、淨值 T+1 還原規則。
4. 需要取得完整原始 Excel 資料，逐筆驗證固定 OLS、Z-Score 與六策略。
5. 核准 `pending_review` 後才能進入正式版資料層；目前**沒有核准／發布程序**。
6. 正式客戶登入與每個客戶資料隔離仍未實作。

請勿將任何 API 密鑰、持倉或投資客戶個資提交 GitHub；如果需要自動抓取，應先選擇正式資料提供商並使用伺服器端 Secrets。
