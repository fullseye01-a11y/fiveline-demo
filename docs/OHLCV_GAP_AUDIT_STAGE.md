# 官方歷史日線｜交易日缺漏品質閘門（開發分支）

狀態：**僅研究／測試，未接入五線譜、SD 通道、六策略、基金 NAV、客戶資料或公開報價。**

此工作包接續官方 OHLCV 研究：既有 SQLite 抽樣標的為 2330、0050、6488、006201；Neon 獨立研究庫目前另保存 2330、0050、6488、8069 共 66 筆（兩組試驗標的不同）。原始價格仍不得視為含息、拆分還原行情。此版本新增可測試的資料缺漏稽核，**不代表已完成全市場回補**。

## 稽核輸入（研究機中，不應提交逐筆價格至 GitHub）

\`\`\`bash
python -m tools.official_history_gap_audit \
  --sqlite /private/staged_official_ohlcv.sqlite \
  --calendar /private/official_calendar.csv \
  --windows /private/official_instrument_windows.csv \
  --exceptions /private/verified_no_trade_exceptions.csv \
  --report /tmp/ohlcv_gap_audit_summary.json
\`\`\`

如果尚未取得日曆／上市日期證據，可省略那三個 CSV 參數；報表將標為**缺少證據／尚未確認連續性**，不會假設週一至週五都是交易日。

**官方日曆 CSV 欄位：** \`market,date,is_open,evidence_url\`。每個自然日列一筆，\`market\` 僅允許 \`TWSE\`／\`TPEX\`，日期格式 \`YYYY-MM-DD\`，\`is_open\` 是 \`true\`／\`false\`。來源 URL 需指向對應交易所 HTTPS 官方網域。星期例假日、颱風臨時停止交易、補班與春節結算但不交易日應列為 \`false\`，不得以一般平日推算。來源：臺灣證交所「市場開休市日期」及櫃買中心「開（休）市日期表」，並須核對臨時公告。

**正式標的日期窗口 CSV 欄位：** \`market,symbol,start_date,end_date,evidence_url\`；日期範圍須從上市／掛牌紀錄與稽核截點確認，不能以現有行情的最早／最晚日期冒充完整窗口。

**停牌／無成交例外 CSV 欄位：** \`market,symbol,date,reason,evidence_url\`，\`reason\` 只允許 \`suspended\`、\`trading_halt\`、\`no_trade\`。每個例外必須有官方證據，否則缺漏日仍報警。

## 安全閘門

- 驗證 OHLC 包含關係、正價格、非負成交量及同一標的日期不重複；發現異常就中止。
- 跨市場交易日曆分別核對；未涵蓋的日曆日期明確列為「未知」，不能當作假日；來源標註不等於官方完整性人工審核。
- 公開報告不包含開高低收、成交股數與成交金額，最多顯示每檔前 20 個缺漏日期。
- 即使「暫定日期連續」通過，\`production_coverage_complete\`、\`official_calendar_completeness_reviewed\`、配息／分割還原、正式策略發布旗標仍全為 \`false\`。
- 每批回補必須先確立來源批量存取及商業使用權、日期窗口、例外清單；官方資料來源與版本須留存。不要因 GitHub Actions 通過就直接改正式回測。

本階段：\`python -m unittest discover -s tests -p 'test_official_history_gap_audit.py' -v\`，十項缺漏稽核合成案例驗證；另有五項回補清單測試。不使用正式行情、不需要 API token。

## 分批回補計畫（只產生清單，尚未執行大量下載）

```bash
python -m tools.official_history_backfill_plan --sqlite /private/staged_official_ohlcv.sqlite \
  --calendar /private/official_calendar.csv --windows /private/official_instrument_windows.csv \
  --limit 20 --report /tmp/ohlcv_backfill_plan.json
```

缺漏的 TWSE 股票與上市 ETF 按「標的×月份」合併成一個 STOCK_DAY 工作單元；TPEx 上櫃股票與 ETF 按「交易日」合併成一個 dailyQuotes 工作單元。各批只請求**目前研究 SQLite 缺少**的交易日；有觀測資料就不會重複排入。單次最多 100 個工作單元，預設 20。日曆任何日期未經官方證據核對，該標的停止排程並列出未驗證日數。

此程式**不會連網下載、不會寫 Neon、不會執行除權息與分割還原**。實際批次下載、逐筆版本化入庫、全市場擴展與合法再利用，仍待來源授權、資料容量和執行端驗證完成。
