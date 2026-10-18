# 歷史行情：備份恢復演練與持久化品質驗收（2026-10-14）

## Neon 已確認的實際狀態

- 私人 Neon Free 資料庫已連線，樣本官方行情已經由 Render 寫入。
- 目前 `history_stage.bar_versions` 18 筆；`history_stage.approved_bars` 18 筆；待覆核 0；成功同步批次 1 次。
- TWSE 2330 8 天、0050 8 天；TPEx 8069 與 6488 各 1 天。
- 只以同市場已觀察日期比較，TWSE 這兩檔的**內部候選**缺口 0；TPEx 僅有一個日期，屬「檢驗資料不足」，**不可當成完整回補**。
- 正式前端與客戶資料未改動。來源資料未公開。

## 這階段新增

- `history_pilot/recovery_drill.py`：針對小規模 AES-GCM 加密備份，密碼認證、限制解壓大小、逐筆驗證原始資料雜湊與 OHLCV，然後在**完全獨立的記憶體 SQLite** 裡復原所有版本；絕不覆蓋 Neon。
- `history_pilot/r2_backup.py`：當且僅當 R2 配置完成且明確啟用，備份上傳後必須重新下載並驗證檔案 SHA-256、AES-GCM、資料欄位，最後再通過上述隔離復原演練。
- `history_pilot/tests/test_recovery_drill.py`：五項合成案例（成功復原、竄改、錯誤密碼、資料指紋不符、重複版本）。

## 尚未完成

- **Cloudflare R2 私有 Bucket 與認證尚未由使用者帳戶建立／授權。** 本工具連接尚不提供可用的 R2 管理服務，不能聲稱異地備份成功。
- 隔離記憶體 SQLite 復原是**邏輯復原檢查**，不能代表 Neon/Postgres 異地災難復原已演練。
- R2 與 PostgreSQL 的免費層可能有額度、結算、容量、效能與服務政策限制。
- 若未完成外部備份保存與還原演練，仍**不應啟動全市場大量回補**。
- 當前 Render `PILOT_FETCH_ON_BOOT=0`，避免重覆自動下載；`PILOT_R2_BACKUP_ON_SYNC=0`。

## R2 完整驗收前提（只供私有帳戶管理頁操作）

1. 使用者在 Cloudflare 帳戶建立一個**未公開**的 R2 Standard Bucket，確認免費額度及費用告警。
2. 產生只限指定 Bucket 的讀寫金鑰；勿貼聊天、勿放公開 GitHub。
3. 由使用者在 Render 私人 Environment 配置 `R2_ENDPOINT`、`R2_BUCKET`、`R2_ACCESS_KEY_ID`、`R2_SECRET_ACCESS_KEY`、`PILOT_BACKUP_PASSPHRASE`。
4. 先小規模測試 R2 上傳、重新下載與加密驗證、隔離復原演練，再決定保留天數及正式排程。
5. 僅有備份成功後，才適合逐月擴大 2330、0050、8069、6488 的測試；再開始公司行動、停牌及市場開市日官方核對。
