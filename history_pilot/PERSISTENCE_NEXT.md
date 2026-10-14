# 第三階段：PostgreSQL 私人保存與加密備份（2026-10-14）

## 已實際建立
- Render workspace: My Workspace
- 免費 Postgres: fiveline-history-stage-20261014，Singapore
- Database ID: dpg-db7d7k8473hc73cuvhl0-a
- 到期：2026-11-13 00:58:57 UTC（台灣時間 2026-11-13 08:58:57）
- URL: https://dashboard.render.com/d/dpg-db7d7k8473hc73cuvhl0-a
- 原私人測試服務：fiveline-history-pilot-20261014
- GitHub 私人資料分支（注意：程式儲存庫本身為公開）：feature/official-history-sqlite-pilot

**重要：PostgreSQL 目前已建立，但尚未透過 DATABASE_URL 綁定 Web Service。不可聲稱原本 18 筆 SQLite 行情已成功搬入 PostgreSQL。** Render 的連接憑證無法由目前連接工具安全取得；不得寫進公開 repo 或此文件。

## 核心架構
- 官方 TWSE／TPEx 回傳 → 原格式解析 → 本機暫存 SQLite。
- `postgres_store.persist_sqlite()` 在 `DATABASE_URL` 配置後，把暫存的所有版本以版本化方式同步進私人 Postgres 的 `history_stage.bar_versions`。
- 來源日線如發生修改，新增待覆核版本，保留舊核准價。
- `history_stage.approved_bars` 只讀核准版本；沒有公用行情 API。
- `gap_audit.py` 僅回報同一市場其他標的有交易之日的「候選缺口」，停牌、剛上市、未上市交易日等須以官方資訊另核對。

## 安全綁定步驟（需由 Render 帳戶持有人操作）
1. 進 Render PostgreSQL 的 **Connect**，找到 **Internal Database URL**。僅供同一工作區、同一地區的服務使用。
2. 進 Render 服務 `fiveline-history-pilot-20261014` → **Environment**，新增 `DATABASE_URL`，其值為上一步的完整 Internal URL。**不要貼到 ChatGPT / GitHub / 網頁公開欄位。**
3. 在該 Render 服務 **Settings** → **Build Command** 改成：
   `pip install -r history_pilot/requirements-storage.txt && cd history_pilot && python -m unittest discover -s tests -v`
4. 按 Render **Save and deploy**，確認 app logs 出現 `HISTORY_PILOT_POSTGRES` 與 `durable_counts`，而非 `HISTORY_PILOT_POSTGRES_NOT_CONNECTED`。不要只看健康檢查 HTTP 200。
5. 服務採 `autoDeploy=no`，且會在服務每次啟動時再執行小規模樣本抓取（當前環境設定）。這不是定時全市場資料抓取。

## 可選擇的手動加密備份工具
- 配置本機 Python 套件：`pip install -r requirements-storage.txt`（需接入私有資料庫）。
- 在同一私有環境以環境變數 `DATABASE_URL`、`PILOT_BACKUP_PASSPHRASE` 提供存取資料。
- 建立（只支援小規模 50,000 筆內的試跑）：
  `python encrypted_backup.py create --path /private-export/market-history-20261014.enc`
- 驗證：`python encrypted_backup.py verify --path /private-export/market-history-20261014.enc`
- 檔案 AES-256-GCM + scrypt 驗證加密；確認密碼與獨立私有目的地後再保管。密碼遺失無法復原；密碼與密文不得放在同一位置。
- 由於 Render 免費 Web Service 的暫存檔系統會在休眠、重啟或部署時消失，將檔案放在 /tmp **並不構成真正備份**。**目前未完成外部備份目的地、排程或復原演練。**

## 免費限制
Render 免費 Postgres 30 天後到期且沒有受管理備份；免費 Web 無持久磁碟。2026-11-13 前必須選擇長期儲存方案，否則不能承諾資料持續保留。未取得相應授權前不得再散布官方原始行情。

## 驗收進度
- 資料庫建立：完成
- 私人 PG schema、版本同步程式：已提交開發分支、離線測試通過
- 保守缺口檢查：已提交
- 小規模 AES-GCM 加密／解密／竄改測試：離線通過（尚無真實 PG 備份）
- 機密 DATABASE_URL 綁定：**未完成**
- PG 內真實行情寫入：**未完成**
- 雲端異地備份／還原演練：**未完成**
- 正式全市場日線回補：**未啟用**
