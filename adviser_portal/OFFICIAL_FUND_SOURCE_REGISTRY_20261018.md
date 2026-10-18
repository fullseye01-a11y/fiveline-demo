# 歷史基金 NAV 下一階段｜官方級別身分及資料授權閘門

研究版日期：2026-10-18。**尚未取得三年逐日 NAV 或商業授權，不可用於客戶正式配置。**

## 實際確認的三個優先基金級別

| 基金 | 現行市場名錄代碼 | 官方基金級別 ISIN | 原幣 | 發行公司公開身分證據 |
|---|---|---|---|---|
| 安聯台灣科技基金 | DDO04 | TW000T3604Y3 | TWD | [安聯投信](https://tw.allianzgi.com/zh-tw/products-solutions/taiwan-onshore/allianz-global-investors-taiwan-technology-fund) |
| 瀚亞多重收益優化組合基金 B 類型－美元 | DIOB3 | TW000T0759D2 | USD | [瀚亞投信](https://www.eastspring.com.tw/our-funds/all-funds/funddetails?isincode=TW000T0759D2) |
| 富達基金－全球優質債券基金 A 股 C 月配息美元 | LU2156566916 | LU2156566916 | USD | [富達官方級別頁](https://www.fidelity.com.tw/fund-and-service/fundui/funds/Fidelity-Funds-Global-Income-Fund-A-MCDIST-G-USD/overview) |

- 兩檔台灣境內基金原先 SITCA 搜尋快照 ISIN 為空；本次以官方產品頁對應精確名稱、在台代碼與級別後**額外補上唯讀來源對照**，不擅自改寫全市場候選名錄。
- 官方級別名稱、原幣或代碼有任何差異，API 不回傳這筆 ISIN。尤其 B 美元與 B 台幣、A 美元不可因字面相似就合併。
- `GET /api/fund-source-qualification?uid=...` 由使用者點擊時回覆對照結果、官方連結及阻擋原因。只支援上列三個人工查核級別；其餘基金回覆待驗證，不複製別檔 ISIN。
- 目前 `commercial_data_rights_approved=false`、`three_year_history_ready=false`、`full_history_raw_nav_rows_imported=0`、`ready_for_total_return=false`。官網有 NAV/績效圖不代表已提供可再散布的完整逐日 NAV 原檔與商用授權。

## 本輪檢查與來源發現

1. TDCC 公開 `3-2` 基本資料含基金級別及 ISIN，`3-4` 為近期淨值；**不能用它假稱已掌握完整三年**。
2. 富達官方頁證實 A 股 C 月配息美元 ISIN，而 Yahoo、StockQ 等第三方歷史淨值可幫助找出資料差異與公開查詢途徑，但**不當作經授權批次蒐集與客戶服務來源**；沒有自動下載或入庫。
3. 原有 `adviser_portal/nav_history_audit.py` 存在品質閘門缺口：此前商用授權審查為 false 時，仍可能回傳「provisional coverage passed」。本輪已修正：**需要授權人工審查=true、級別身分人工審查=true、逐日歷史日期與官方估值日清單完全對帳**，才可能進入「暫定覆蓋通過」。即使兩個旗標為 true，程式仍強調這只是填表者聲明，未獨立驗證來源權利；不啟用總報酬。
4. 三筆官方級別身分補充證據可讓下一階段完整歷史原檔對帳採用統一 `ISIN+currency+nav_date` 索引，且保留 SITCA/TDCC 來源代碼。

## 實際下一步資料匯入路徑

從上述三檔逐檔尋找基金公司正式 NAV 日期序列／授權資料商導出；取得原始 CSV、最少三年估值日清單、來源檔 SHA256、基金級別與期限、授權商用條款核准後，使用**離線** `python -m adviser_portal.nav_history_audit` 測試。通過者仍不能直接寫入個資資料庫、客戶資產配置或六策略，先建獨立私有試跑資料庫及回復備份。

**未完成：** 全市場三年歷史資料、實際基金原始檔匯入、客戶身份隔離、原 Excel 27 張工作表、基金現金配息再投入、TWD 匯率、合法再散布核准及策略回測品質驗收。

僅修改 `feature/adviser-client-platform-v1` 的獨立 Alpha；主站、原 Excel、正式五線譜、Neon 客戶資料、六策略保持原樣。

備份分支：`backup/adviser-alpha-before-licensed-nav-source-20261018`。

## 本輪官方網站進一步查核及真實線上驗收

- **瀚亞**網站提供 [歷史淨值查詢與 Excel 匯出](https://www.eastspring.com.tw/our-funds/exportnav)。頁面有日期起迄、網頁顯示與 Excel 匯出兩種模式；網頁顯示說明「最多一年資訊」。這是**實際可訪問的官方查詢入口**，不是證明某次 Excel 下載已得到三年完整原始逐日資料，也不是商業轉載授權。Alpha 在瀚亞 B 美元基金卡只提供這個來源連結，`exported_three_years_verified=false`，不對其他基金類推。
- 富達 GLIAM、安聯台灣科技、瀚亞 B 美元三筆基金級別均已在 Render 的官方名錄載入時做實際對照，三筆都回傳 `ISSUER_PUBLISHED_CLASS_IDENTITY_FOUND`，沒有 ISIN/幣別/級別衝突；歷史 NAV 仍 **0 筆**。
- 品質程式已修正：任一 `rights_reviewer_approved=false` 或 `issuer_identity_reviewed=false`，即使宣稱歷史日數全部吻合，也不得回傳暫定三年覆蓋通過。新增合成測試守護此條件。
- 程式不自動下載 Excel、不從前端上傳 CSV，也不會自動把瀚亞公告或富達配息資料寫入正式回測。每個商用資料使用方式，仍須確認基金公司或資料商授權。

## 富達歷史級別代碼變更（2026/02/23）

- 富達官方 [2025/12/12 基金代碼變更通知](https://www.fidelity.com.tw/s3files/documents/announcement/web_announcement_20251212_3.pdf) 第 9 頁公告，同一 ISIN **LU2156566916** 對應 **舊代碼 GGIAM → 新代碼 GLIAM**，新基金代碼 2026/02/23 生效。公告說明是代碼轉換，非另一個配息級別，也不能據此推定每單位 NAV／單位數發生分割。
- Alpha 官方來源卡新增兩個代碼與切換日期；後續三年歷史 NAV 匯入要以 ISIN 為主鍵，保留不同期間的來源代碼；若資料商使用兩套代碼，需要逐筆核對來源資料。**目前歷史代碼序列尚未對帳**，`provider_code_alias_history_reconciled=false`。
