# 五線譜顧問與客戶整合網站：Alpha 0.1（獨立測試）
此模組與既有靜態網站、歷史行情試跑分離；**不是正式客戶服務**。

## 已實作
- RR1～RR5 示範風險問卷：能力與意願取較低級別，非正式金融適合度。
- 資產配置：台灣、全球股票、債券及現金；科技是股權子集合，不重複加計。
- 漸進調整只分配新資金；立即最佳化顯示估計買賣差額（未含費稅）。
- 退休情境：名目年報酬 -1%／3%／6%，扣費、月複利、月末投入與提領及通膨實質值。
- iPhone 版表單、工作儀表板、研究資料中心，所有請求無持久化個資。
- GET /health、GET /api/demo、POST /api/risk、POST /api/allocation、POST /api/retirement。

## 本版尚未完成／不得宣稱完成
- 無真實登入，無客戶資料庫，無配置保存。所有 API 客戶/登入路由一律回 403。
- 無最新 NAV、全球基金名錄和真實基金穿透持股；所有示範金額/權重及報酬是假設。
- 原始 FIX153_v19 27 工作表公式與 VBA 尚未逐項核對；沒有 Excel parity 認證。
- schema.sql 僅是一份強制 RLS 的草案，尚未套到獨立客戶資料庫及進行實際跨租戶 E2E 測試。

## 執行與 Render 部署
在 repo 根目錄執行：

    python -m unittest discover -s adviser_portal/tests -v
    python -m adviser_portal.server

Render Free Web Service：
- branch: feature/adviser-client-platform-v1
- build: python -m unittest discover -s adviser_portal/tests -v
- start: python -m adviser_portal.server
- 不需任何 Neon 或登入密鑰，PORT 由 Render 給定。

## 正式服務安全門檻
1. 獨立 client Neon DB + 非 owner、沒有 BYPASSRLS 權限的應用角色。
2. 第三方可信 IdP / OIDC、後端 Session、CSRF、停權機制。DB 的 app.org_id / app.member_id / app.role 只准後端由已驗證 session 以 SET LOCAL 提供，絕不接受瀏覽器角色參數。
3. 至少兩組組織、兩位顧問、數位客戶的越權存取測試，讀/寫/更新/刪除全部拒絕。靜態 schema 測試不等同 Neon RLS 實測。
4. 27 工作表對帳、商品 RR 合規、配息/匯率/分割還原、全市場資料覆蓋與來源商業授權。
5. 私有備份與實際還原、個資同意與審計、穩定性及回滾演練。

原有正式 GitHub Pages 和 Excel 穩定版均保持原樣。免費雲端方案會有額度/休眠限制，不保證正式營運零成本。

## 2026-10-18 更新｜全市場候選名錄已接入 Alpha

- iPhone 顯示頁面新增「基金／ETF 搜尋」，有即時輸入後手動搜尋（非每字查詢）、境內基金／在台核備境外基金／上市及上櫃 ETF／上市與上櫃股票分類、每頁最多 50 筆、下一頁以及同次頁面最多 20 項候選觀察。
- `GET /api/catalog` 由後端取得**公開、具來源資訊的快照**，驗證六個來源、筆數、類別合計、重複 UID 和時間格式。查詢支援 `query`、`kind`、`offset`、`limit`、`refresh=1`，不直接下載全份 4.5MB JSON 給 iPhone。
- 2026-10-16 的官方名錄快照，共 **12,145 筆來源紀錄**：境內 4,472、在台核備境外 5,295、上市／上櫃 ETF 共 390、上市股票 1,095、上櫃股票 893。以上是來源紀錄，不是全球所有基金，也不是跨來源去重後唯一標的數。
- 後端只做來源、代碼／名稱／ISIN 身分搜尋，不提供最新 NAV、歷史含息績效、可交易報價、真實持股、買賣建議；級別尚未正式核准。候選觀察名單只在目前開啟的網頁記憶體中，重整就消失；不連動到示範 RR／配置或正式客戶資料庫。
- Render 獨立測試服務 `fiveline-adviser-alpha-20261018`，網址：`https://fiveline-adviser-alpha-20261018.onrender.com`。本次修改 **34／34** 項程式測試通過；一次性官方名錄連線驗收已讀入 12,145 筆，狀態 Live。測試完成後 `ADVISER_CATALOG_SMOKE_ON_BOOT` 須維持 `0`，禁止每次重啟都重讀來源。
- 備份分支：`backup/adviser-alpha-before-catalog-20261018`，可還原上一個只含 RR／配置／退休示範的 Alpha 版本。
- **待辦**：可逐檔確認 ISIN／配息與幣別、合法歷史 NAV 回補／除權息與 ETF 分割、資產配置整合真實 NAV 及風險核准、完整 27 Excel 工作表對帳、私有客戶登入及 RLS E2E 測試、PDF 報告、正式異地備份／還原。

Alpha 完全不收集登入或真實客戶個資。正式客戶上線前不可將其當作已完成的顧問平台。

## 2026-10-18｜境內基金每單位 NAV 證據驗證

- 於 Alpha「基金／ETF 搜尋」的**境內基金紀錄卡**新增「查看官方每單位 NAV」按鈕，顯示嚴格核對後的淨值（畫面取小數三位）、淨值有效日期、原幣別及來源。點按才讀取，不自動輪詢，來源快取一小時。
- `GET /api/nav-evidence?uid=...` 只接受內部公開名錄中的唯一 UID；用**完整基金名稱＋代碼＋幣別**確認來源唯一，若目錄已有 ISIN 仍須一致才可顯示。若缺配對、幣別衝突、來源欄位變更、日期在未來或 NAV 數值無效，一律拒絕當作可信淨值。
- 先嘗試原有獨立唯讀 NAV 後端；如 Render→Render 連線失敗，改由同一個私有 Alpha 伺服器**直接驗證 SITCA 官方開放資料** `https://www.sitca.org.tw/MemberK0000/F/03/nav.csv`。檢查 SSL 主機與路徑、UTF8/Big5 編碼、資料欄位、1,000 筆以上完整度、合理淨值、唯一配對及 NAV 日期；沒有進行人工覆寫或伪造淨值。
- 境外來源既有 `3-20` 資料的「淨值小計(總額)」尚未確定為**每單位基金淨值**，本版不拿來顯示/換算；上市/上櫃 ETF、個股也不借用 SITCA 基金 NAV。
- 不儲存真實客戶姓名、持倉、基金級別配對、基金歷史績效。此階段「最新官方 NAV 證據」尚未被核准用於資產配置、五線譜、六策略或含息績效。
- 新增 `tests/test_nav_evidence.py` 與 `tests/test_sitca_direct_nav.py`；Render 最新 **59 / 59** 項測試通過。
- **真實線上驗收**：2026/10/18，投信投顧公會公開 NAV 資料已在 Render 實測成功核對「安聯台灣科技基金」（SITCA 代碼 `DDO04`）及「瀚亞多重收益優化組合基金 B 類型－美元」（`DIOB3`），兩項均回傳 `LATEST_NAV_EVIDENCE_ONLY`、單位 NAV 欄位與日期均存在。實測日誌刻意**不輸出價格數字**以免混入正式績效或誤當個資。
- 還原備份分支：`backup/adviser-alpha-before-nav-evidence-20261018`；本版保持原 Render 獨立 Alpha 網址。試跑後務必清空 `ADVISER_NAV_EVIDENCE_SMOKE_UID`，`ADVISER_CATALOG_SMOKE_ON_BOOT=0`。

**後續未完成**：境外逐 share class 每單位 NAV 與配息單位驗證、至少三年連續官方 NAV 歷史、基金拆併/配息/匯率、合法商用授權、NAV/資產配置聯動、27 Excel 表公式對帳、真實登入/租戶隔離及備份還原。任何一項未通過不得當作完整全市場顧問平台上線。
