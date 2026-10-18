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

## 2026-10-18｜境外基金級別、近七日 NAV 與配息日期驗證

- 在 Alpha 同一「基金／ETF 搜尋」頁增加境外基金兩個按鍵：「**核對境外最近七日 NAV**」及「**查看官方配息日期**」，手機可獨立開關查詢，尚不支援正式儲存、回測與多幣別配置。
- 直接連接集保結算所三份各自定義的官方 CSV：
  - **3-2** 基金基本資料 `https://opendata.tdcc.com.tw/getOD.ashx?id=3-2`；資料集 `https://data.gov.tw/dataset/11639`。
  - **3-4** 最近七日 NAV `https://opendata.tdcc.com.tw/getOD.ashx?id=3-4`；資料集 `https://data.gov.tw/dataset/11641`。採用 `基金淨值(金額)`，絕不把另一份 3-20 資料的「最新淨值小計(總額)」視為每單位 NAV。
  - **3-5** 配息資料 `https://opendata.tdcc.com.tw/getOD.ashx?id=3-5`；資料集 `https://data.gov.tw/dataset/11642`。使用除息日期及頻率的 metadata，**不能**直接得出每單位現金配息額。
- 核對 `UID→基金代碼／經 ISO6166 檢碼的 ISIN／完整級別基金名稱／幣別→官方基本資料→官方最近七日 NAV`。資料不完整、同日兩價、幣別差異、來源更動、日期異常、基金級別不唯一，都會回到 REVIEW_REQUIRED，不拼接錯誤基金。
- 配息 3-5 沒有 ISIN，因此要求該代碼＋名稱＋幣別在基本資料內只有一筆候選，遇到相同除息日的配息頻率矛盾，也拒絕自動呈現。最多列出最近 10 個事件。使用者每次主動查詢才讀取官方來源，後端最多 1 小時快取；不建立持久用戶檔案。
- **真實 Render 實測**：`富達基金－全球優質債券基金 (A股C月配息美元)`（ISIN `LU2156566916`），2026/10/18 經官方來源驗證 NAV 身分和日期，回傳 `OFFSHORE_RECENT_NAV_EVIDENCE_ONLY`；同檔官方配息資料符合代碼、名稱、幣別的 **6 筆日期事件**，回傳 `OFFSHORE_DIVIDEND_DATE_METADATA_ONLY`，但 `cash_per_unit_verified=false`，`dividend_adjustment_ready=false`。
- Render 84／84 項測試通過，部署狀態 Live。測試僅列印有效旗標、證據狀態、事件筆數，**不記錄原始 NAV、配息數額、任何客戶紀錄**。程式碼修改前已存 `backup/adviser-alpha-before-offshore-evidence-20261018`。
- 正常使用時 `ADVISER_OFFSHORE_SMOKE_UID` 保持空白、`ADVISER_NAV_EVIDENCE_SMOKE_UID` 空白，`ADVISER_CATALOG_SMOKE_ON_BOOT=0`。

### 後續禁止混用的模組

仍未取得全市場持續歷史 NAV、每單位配息/再投入、股票/ETF 分割還原、幣別匯率時間序列、真正基金 RR 適合度、原 Excel 27 張工作表完整公式對帳、使用者/客戶登入與租戶 RLS。這次新增資料仍只用於 **獨立研究及畫面上檢視官方證據**，不會被加入正式五線譜、SD、六策略、資產配置或退休日程。

## 2026-10-18｜含息資料對帳預檢與三年以上 NAV 就緒度

- 新增 `adviser_portal/income_readiness.py`，API 為 `/api/offshore-evidence?uid=<唯一公開基金UID>&part=readiness`。前端同頁「**檢查含息試算資料完整度**」可檢視最近 NAV 天數、配息事件數、已通過比例欄位驗證之事件數、發行公司**每單位配息核對完成數**及五大缺漏項。
- 集保 `3-5` 中的 **`可分配淨利益/配息` 與 `本金/配息` 為來源比例**（若是有效 0–100 數值，且合計在 100±0.1 以內，才會呈現）；**不是原幣每單位現金金額**，不得將兩個百分比當作可加計的收益。單位數欄位也不直接當成經發行公司核准的單位現金配息額。
- 試算引擎、客戶/資產配置、五線譜、SD、六策略仍被明確鎖住：`ready_for_total_return=false`、`ready_for_portfolio=false`、`ready_for_six_strategies=false`、`issuer_per_unit_payouts_reconciled=0`。沒有未核准的報酬率數字。
- 實際官方來源測試（2026/10/18）對象：**富達基金－全球優質債券基金 (A股C月配息美元)**（ISIN `LU2156566916`）。研究狀態 `RESEARCH_ONLY_ADJUSTMENT_NOT_READY`，最近七日資料中找到 **4 個 NAV 日期**、**6 筆配息事件**且這 6 筆的收益/本金來源比例通過格式及合計檢查；**與發行公司公告核對的每單位現金配息仍是 0 筆**。該 6 筆事件屬官方公開資料配息日期/比例證據，並非已核准歷史含息績效。
- 程式執行前已建立 `backup/adviser-alpha-before-income-reconciliation-20261018`。更新後 Render `96 / 96` 個測試通過、線上完成官方驗證，該一次性驗證應在執行後將 `ADVISER_OFFSHORE_SMOKE_UID` 清空以關閉重啟預檢。
- **下階段**：優先接入基金公司/總代理公告（級別 ISIN、配息日期、原幣每單位金額及來源分布、幣別與避險版本），做 issuer↔TDCC 逐筆對帳；分月/全期取得至少 3 年正式 NAV，累積 NAV/總報酬 NAV 分別建表，匯率與費用逐日留存及揭露；直到資料版本與 Excel 公式經驗收，禁止推估含息年化。

注意：即使配息來源比例驗證通過，也不等於基金已完成每單位配息、期初/期末 NAV、配息再投入、配息稅費或總報酬驗證。

## 2026-10-18｜富達 2026/10 PDF 真正自動驗收與新版手機查詢

- 富達官網固定 PDF URL 會輪換月份。已確認 Render 實際下載文件 `SITE-MKT-EXMP_2610-01`（2026/10），SHA-256 `78166e33f9164cc224bde3853ababe788c5d47456a4b7a06f711b0e0f7eaae10`，3,114,338 bytes。**這個 SHA 僅屬 10 月文件；舊 7 月人工摘錄未保存其當時文件位元組。**
- 解析器不再固定頁碼，在整份 PDF 中以 `GLIAM` 與 `BGIGU` 嚴格定位唯一 A/B 分界，實際目標在第 **31 頁**。從官方文件直接解析 12 期 `2025/11～2026/10` 的原幣每單位配息、參考 NAV、除息日期與收益／本金比例；其中 9 期和舊版獨立人工摘錄相同，3 期新月份由 10 月 PDF 表格取得。
- Alpha `GET /api/offshore-evidence?uid=...&part=issuer-current` 只支援富達全球優質債券 **A 股 C 月配息美元／ISIN LU2156566916**；先核對名錄 UID、基金名稱、代碼、ISIN、幣別，再以檔案 SHA 與逐筆值驗證 PDF，其他基金不借用該價格。
- 網頁增加「**富達 10 月公告最新配息核對**」，列出 2026/08、09、10 三期原幣每單位配息及配息來源百分比，附官方 PDF 網址、版次與簡短 SHA 指紋。只在點擊時載入，後端以最長 1 小時快取；改版後 SHA 不符回 503，不顯示猜測價格。
- 舊按鍵改名「富達 7 月歷史公告對照」，明顯標示人工摘錄、原版 PDF 未保存，點擊目前官網網址顯示的是新版，不可誤當歷史 7 月原檔。
- 即使現有文件表格核對 12 期成功，**獨立集保每單位現金配息金額正式對帳仍為 0**、`ready_for_total_return=false`，不推算三年績效、再投入或客戶配置，原網站／Excel／六策略未修改。
- 新增 `issuer_current.py`、`issuer_pdf_table.py` 和相應測試；Render 最新測試通過 `137/137`，獨立服務 Live。UI 尚待 iPhone 實機點按驗收。一次性 `ADVISER_FIDELITY_PDF_TABLE_PROBE_ON_BOOT` 應在驗收後恢復 `0`。
- 來源及文件版本詳見 `adviser_portal/FIDELITY_OCTOBER_2026_PDF_AUTO_VERIFICATION.md`。修改前保留 `backup/adviser-alpha-before-fidelity-issuer-evidence-20261018`。
