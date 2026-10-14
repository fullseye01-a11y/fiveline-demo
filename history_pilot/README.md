# 歷史行情小規模試跑（獨立模組，不影響 V2.6 展示網站）

- 程式：history_pilot.py（Python 3.10+ 標準函式庫，不需第三方套件）
- 來源：TWSE STOCK_DAY（代碼＋月份）；TPEx stk_wn1430_result.php（指定交易日＋當日多檔）。
- 預設測試：上市 2330、0050；上櫃 8069、6488；可更改參數。
- 儲存：private_data/history_pilot.sqlite3（本機或受保護後端，非 GitHub Pages 網站檔案）。
- **僅儲存未還原原始 OHLCV，成交量統一為股數，不套用分割/除息還原。** 事件資料完整前，不可用它直接推算含息總報酬或無斷點的跨期回測。
- 不公開原始行情、不加入 Git、不傳至展示站，也不放入公開 CI artifacts / Pages / CDN。

## 快速驗證（有網路、具私人持久磁碟的 Python 環境）

在此資料夾執行：

~~~bash
python3 -m unittest discover -s tests -v
python3 history_pilot.py --date 2026-10-13 --twse 2330 0050 --tpex 8069 6488 --tpex-legacy-volume-unit shares
~~~

TPEx 舊版 aaData 未包含欄位單位，必須先比對櫃買官網的成交量單位才可指定 shares 或 thousand_shares；新版 tables 如明示成交股數／成交仟股，程式會自動判定。**不要盲選。**

## 審核規則與更新邊界

1. 驗證日期、OHLC 關係、正價格、正成交量、非負成交金額及欄位結構。
2. 同一天相同資料再匯入只計 unchanged；變更新增下一版本並標記 data_revised，不直接覆蓋舊值。
3. 首次匯入且較前一已核准收盤價跳動超過 30% 時，進入待人工覆核（可能為分割、除息或異常），並非直接判定錯誤。
4. approved_bars 僅包含已核准版本；pending_review 保留待確認修訂；無對外 API。
5. 停牌／零成交不偽造成交價格；交易日曆及公司行動事件資料須另行核對。
6. 若來源回傳欄位變動，應停止並回報，不能猜測欄位；舊版 aaData 僅在確認單位後使用。
7. 每次請求限制少量代碼，勿對官網高頻掃描；實際正式資料授權及再散布方式須另外核對。

SQLite 查詢僅在受保護環境進行：

~~~bash
sqlite3 private_data/history_pilot.sqlite3 'select market, count(*) from approved_bars group by market;'
sqlite3 private_data/history_pilot.sqlite3 'select market, code, day, version, review_reason from pending_review;'
~~~

## 尚未完成

公開 GitHub Pages 靜態站無法執行 Python 或 SQLite。正式來源抓取、排程、備份及權限管理必須在私人持久化後端部署。7 項單元測試為**離線合成樣本**，不代表已在此執行環境完成即時官方回補；必須實際執行網路試跑，再回報成功率／缺漏率／逐檔審核結果。


## 第二階段：品質報告（僅私人本機）

1. 在 history_pilot 目錄執行：python3 -m unittest discover -s tests -v
2. 實測 SQLite 品質報告：python3 quality_report.py --db private_data/history_pilot.sqlite3
3. 合成測試：python3 examples/make_synthetic_demo.py，再執行 python3 quality_report.py --db private_data/synthetic_demo.sqlite3

品質報告只有批次數、資料範圍、待覆核筆數，不含原始 OHLCV。LOCAL_CHECKS_PASS 僅代表內部檢查通過，不能視為來源 API 已成功連線、完整歷史已回補或取得商用授權。
合成範例應為 8 筆核准、2 筆待覆核、1 筆歷史修訂、0 筆主鍵重複；合成資料絕不代表真實行情。

2026-10-14 狀態：執行環境官方 TWSE/TPEx 來源受 DNS 限制，沒有把 2026-10-13 真實 OHLCV 匯入，私人持久後端與正式排程尚未部署。下一階段須在私人後端實測來源資料格式、TPEx aaData 單位及授權條件。
