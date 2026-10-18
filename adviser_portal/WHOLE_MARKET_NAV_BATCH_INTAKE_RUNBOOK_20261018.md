# 全市場基金：多檔歷史 NAV 批次驗證操作規格｜2026-10-18

## 本階段真正完成

新增 `adviser_portal/fund_batch_intake.py`，將前一階段已存在的 **9,767 筆基金名錄待辦清單**，接上可逐批處理合法 NAV 來源檔案的**離線通用驗證器**。每批最多 100 檔基金級別，含境內、在台核備境外，不受安聯、瀚亞、富達的專用邏輯限制。9,767 筆名錄對應 98 批（每批上限 100；最後一批 67）。這是處理**能力**，不是 9,767 檔已經有歷史資料。

- 每次先載入已經完整驗證的公開名錄 JSON，鎖定全部來源級別 UID 與 SHA256 名錄指紋。批次清單上的每一檔都必須綁定相同快照、來源、原始代碼、名稱、基金類別、原幣及 ISIN。
- 已有正確 ISIN 的來源，按其校驗碼及幣別精確核對；原名錄缺 ISIN 的級別必須附發行公司身分文件位置、文件摘要及審核者聲明。**同 ISIN 映射至多個不同名錄級別**、**同一份原始 CSV 被交付不同 UID**、不同幣別或中文級別不一致，均阻擋自動接受。
- 每個檔案繼續使用 `nav_history_audit.py` 核對三年以上日期跨度、每單位 NAV、實際估值日清單及其 SHA256、重複日、同日多價、發行公司更正公告（例如安聯已知 2 個日期），以及商用授權與基金身分審核欄位。缺一天官方應有的 NAV 也必須標示。
- 單一檔案未通過不會被忽略：整批回覆**逐 UID 的退件理由**及通過計數，不能因其中一筆合格就把整批視為正式核准。
- 程式限制 1～100 份檔案、整批不超過 48 MB，原始 CSV 不超過 8 MB／份；防止路徑跳脫和跨資料夾指向；只在本機受控目錄讀檔，不向公開網站開放 CSV 上傳、不在 Render 啟用自動抓取、不把原始 NAV 發到 GitHub 或前端。
- 結果狀態 `PROVISIONAL_SOURCE_DATE_QUALITY_PASSED_ONLY` 代表**該檔來源、日期、級別與審核聲明的初步資料品質通過**，並**不**表示獨立驗證資料商合約、官方估值日、基金現金配息、幣別匯率、總報酬或可供客戶使用。

## 資料供應商核准後才能使用的檔案格式

受控工作目錄範例（不可放入公開 GitHub）：

```text
/private/fund-nav-batch/
  index.json
  fidelity-a-usd/
    manifest.json
    raw-nav.csv
    valuation-dates.json
  eastspring-b-usd/
    manifest.json
    raw-nav.csv
    valuation-dates.json
    issuer-identity.json
```

`index.json` 以 `schema=fiveline-private-nav-batch-file-index-v1` 和 `catalog_snapshot_sha256=<當次已驗證目錄的SHA256>` 綁定；`entries` 是最多 100 個 `uid,manifest,csv,sessions,identity_evidence` 相對路徑的陣列（只有缺 ISIN 的級別需補身分文件）。每檔原始 CSV 欄位必須**精確**為：

```csv
nav_date,isin,currency,nav_per_unit
```

`manifest.json` 的欄位至少包含：

```json
{
  "schema": "fiveline-raw-fund-nav-history-v1",
  "source_uid": "須與名錄一致的UID",
  "catalog_snapshot_sha256": "64個小寫十六進位字元",
  "source": "sitca_domestic_funds 或 tdcc_offshore_funds",
  "category": "domestic_fund_share_class 或 offshore_fund_share_class",
  "catalog_code": "原名錄代碼",
  "catalog_name": "名錄完整級別名稱",
  "isin": "該級別實際ISIN",
  "currency": "USD",
  "nav_type": "raw_unadjusted_per_unit",
  "provider_name": "正式提供者",
  "source_url": "https://官方或合法授權來源",
  "source_basis": "issuer_official",
  "source_sha256": "原始CSV位元組的SHA256",
  "session_evidence_sha256": "實際估值日期清單的SHA256",
  "rights_reviewer_approved": false,
  "issuer_identity_reviewed": false
}
```

這是**範例欄位說明**，不是可直接通過的假檔案。正式匯入前必須由有權限的審核者將審核旗標依真實權利與文件查核結果設定，且提交官方估值日清單；不得為了通過測試而手動改成 true。

測試環境管理者取得正式來源後，使用：

```bash
python -m adviser_portal.fund_batch_intake \
  --catalog /private/snapshots/market-universe-verified.json \
  --root /private/fund-nav-batch \
  --index index.json
```

CLI 只輸出 UID、來源摘要、日期及阻擋條件，不輸出 NAV 價格，也不儲存歷史時序或更動交易策略。任何退件會以非零退出碼結束，供後續隔離試跑排程判斷。

## 不得誤報的完成度

| 階段 | 現狀 |
|---|---|
| 全市場基金名錄候選 | 9,767 筆（境內 4,472、境外 5,295；依先前已驗證快照） |
| 全市場身分品質盤點及分頁待辦佇列 | 已建立 |
| 多檔批次原始 NAV 來源驗證器 | 已建立；以**合成資料**測試 |
| 真實合法三年以上 NAV 原檔已提交驗收 | **0 檔** |
| 已正式保存的經核准歷史 NAV | **0 檔／0 筆** |
| 具備三年以上正式含息績效的基金 | **0 檔** |
| 客戶登入、RLS、全市場配息、歷史 FX、27 表／六策略對帳 | 尚未完成 |

下一個真正的資料里程碑是與有權提供整批基金**歷史原始 NAV／估值日**的供應商確認商用及再散布權，取得先期批次（最多 100 檔），由隔離環境執行上述 CLI 和回報未通過的日期／級別缺口；不是再為每檔基金各自造一份專用程式。

本版只修改 `feature/adviser-client-platform-v1` 與 Alpha，保有 GitHub 還原分支 `backup/adviser-alpha-before-batch-nav-intake-20261018`；不修改 `main`、正式五線譜、Excel、六策略、Neon 顧客資料。