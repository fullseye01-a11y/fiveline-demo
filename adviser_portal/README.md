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
