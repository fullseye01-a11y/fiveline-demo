# Neon Free + Render Free + Cloudflare R2｜切換手冊（2026-10-14）

## 進度與安全界線

**目前僅完成 GitHub 程式與 Render 準備；尚未建立 Neon 專案、Cloudflare R2 bucket，沒有上傳正式資料或產生真正的異地備份。**
Neon 尚未與 ChatGPT 連接，Cloudflare R2 沒有可用的直接管理連接，需帳戶所有人在各自官方頁面完成。

現有 Render 免費 PostgreSQL **保持不變，不刪除**；到期日期 2026-11-13，正式主網站及其他 Render 服務不改。
舊 18 筆是官方回傳匯入的 **Render SQLite 暫存**；服務重啟後不保證存在。正式 Neon 連接後必須重新匯入、核實。

## 第一階段：開 Neon Free（無 30 天固定到期）

1. 在 ChatGPT 連接 Neon，並在 Neon 控制台建立專案 `fiveline-history-free`（地區建議新加坡，靠近 Render）。
2. 控制台 → Connect，選 PostgreSQL 的連線網址，確認網址包含 `sslmode=require`。在 Render 服務
   `fiveline-history-pilot-20261014` 的 Environment 裡新增 **Secret** `NEON_DATABASE_URL`。
3. Render Environment：
   - `PILOT_STORAGE_BACKEND=neon`
   - `PILOT_FETCH_ON_BOOT=0`（保留關閉，直到儲存後端與授權驗證完成）
   - `PILOT_R2_BACKUP_ON_SYNC=0`（未連接 R2 前絕不可開）
4. Render 服務 Settings 的 Build Command 更新為：
   `pip install -r history_pilot/requirements-storage.txt && cd history_pilot && python -m unittest discover -s tests -v && python -m unittest discover -s optional_tests -v`
5. 儲存部署。成功不代表有行情寫入；/health 只能確認服務運行。進 Render Logs 確認測試通過。
6. **確認資料庫可連、技術欄位、許可條件後**把 `PILOT_FETCH_ON_BOOT` 設為 `1` 並重新部署一次。查看 Logs `HISTORY_PILOT_NEON` 含 `durable_counts`，且 TWSE/TPEx 重新取得 18 筆樣本。
7. 核對後將 `PILOT_FETCH_ON_BOOT` 設回 `0`。若來源欄位發生變動，先修正程式，不擴大抓取。
8. Neon 的 free 用量應持續監測（2026-10 官網：1GB/專案、每月 100 CU-hours/專案、6 小時還原視窗），超出免費額度時不能保證零費用或永續穩定。

**重要：不可把連線 URL／API Token 貼進 ChatGPT、GitHub 或可讀取網頁，尤其此 GitHub repo 是 public。**

## 第二階段：Cloudflare R2 異地加密備份

1. Cloudflare Dashboard → Storage & databases → R2 → 啟用 R2（R2 Free Tier 也可能要求啟用計費；請設定費用監控與用量提醒）。
2. 建立私人 Bucket：`fiveline-history-private-backup`，**不要開啟 public access / 自訂公開網域**。使用 Standard storage 類別才有免費層。
3. 建立只允許此 bucket 讀寫的 R2 API token，保留以下值在 Render Environment（不是程式碼）：
   - `R2_ENDPOINT=https://<account-id>.r2.cloudflarestorage.com`
   - `R2_BUCKET=fiveline-history-private-backup`
   - `R2_ACCESS_KEY_ID=(私密)`
   - `R2_SECRET_ACCESS_KEY=(私密)`
   - `PILOT_BACKUP_PASSPHRASE=(自設長密碼，至少20字元；應由本人另外安全保存)`
4. Neon 導入和查詢成功後，才把 `PILOT_R2_BACKUP_ON_SYNC=1`；重新執行一次小型驗證匯入。R2 備份只有在資料同步後才會啟動。
5. 日誌必須同時看到 `HISTORY_PILOT_NEON` 與 `HISTORY_PILOT_R2_BACKUP`，其中 `verified_by_readback=true` 表示加密檔已從 R2 取回並通過 AES-GCM 認證檢查，**尚不表示已在全新 PostgreSQL 進行災難復原**。
6. 把兩個啟動旗標設回 `0`；後續另外規劃排程與真正的還原演練。
7. R2 Standard 提供每月 10GB-month 儲存、100 萬次 Class A 和 1,000 萬次 Class B 操作的免費層。**超額可能收費**，所以本模組單個加密備份檔最大 8 MiB、總筆數最多 50,000，且不自動刪除或大量掃描。

## 快速驗收

- GitHub 分支：`feature/official-history-sqlite-pilot`，PR #21（draft）
- 舊資料不覆蓋，首次資料修訂另外留版本，審核前不取代核准價。
- SQLite 仍為短期暫存，Neon 才是私人持久保存。
- `/health` 只代表網站 alive，不能代表連接 Neon 或 R2 成功。
- 這個測試流程目前沒有客戶登入、權限隔離與原始行情公開存取。

## 注意事項

- 官方行情再散布規則需先確認；R2 加密備份仍是自行保存，不是允許對外販售或散布的證明。
- Render free 會休眠且有運算與流量限制。Cloudflare R2 需要啟用帳單時不代表保證零費用。
- Neon Free 並非無限制長期服務承諾，可能因超量、停用、政策變更而受影響。
- 使用現有付費 Render PostgreSQL 不是此方案必要條件；**在 Neon、備份和還原測試均通過前，先不要刪除原 Render 試驗庫**。
