# 富達官方最新 2026/10 配息：真正 PDF 自動解析與版本驗證

## 新發現：官方固定網址不是不可變版本

2026/10/18 在 Render 實際下載的官方 PDF：
- 網址：`https://www.fidelityinternational.com/legal/documents/FF/TW-zh/fdpc.ff.TW-zh.TW.pdf`
- SHA-256：`78166e33f9164cc224bde3853ababe788c5d47456a4b7a06f711b0e0f7eaae10`
- 檔案大小：3,114,338 bytes
- **版次代碼：`SITE-MKT-EXMP_2610-01` = 2026 年 10 月**。
- 版面自動掃描發現 GLIAM A 股 C 月配息美元和 BGIGU B 股 C 月配息美元共同位於**實際 PDF 第 31 頁**。舊手動七月摘錄的第 33 頁不是最新位置。

**重要更正**：先前工程文件將這個實際 10 月檔案的 SHA 錯誤地歸類為 2026/07 歷史摘錄。已更正程式：歷史 7 月摘錄保留人工參考，`issuer_document_bytes_verified=false`，未保存當年原版 PDF；目前指紋**只屬於 10 月官方原檔**。不能把會變動的官方固定 URL 當成永久的 7 月歷史原始檔。

## 實際 2026/10 GLIAM 驗證

- 在 Render 上 **真正下載一次官方 PDF**；核對固定 HTTPS 主機和 URL、下載大小、PDF 格式及 SHA256。
- 不依固定頁碼，逐頁定位唯一 `GLIAM` + `BGIGU`，防止 A/B 級別混淆。讀取 `SITE-MKT-EXMP_2610-01`，並以真正 PDF 文字解碼器擷取 A 級別 12 期獨立月份。
- 每期核對除息日、前一月報酬參考月份、原幣每單位配息、NAV、月配息率、收益及本金比例。不同基金級別、缺漏／重複日期、不合理比例、PDF 被改動、文件位元組不符，全部拒絕。
- 10 月文件涵蓋 **2025/11～2026/10 共 12 期**，其中 **2025/11～2026/07 共 9 期**與之前人工確認的 7 月舊版重疊且**金額、收益與本金比例完全相同**；**2026/08、09、10 三期新增月份**由同一個 10 月 PDF 原始表格讀出。
- 實際 Render 日誌：`ADVISER_FIDELITY_GLIAM_TABLE_VERIFIED`、`page_number=31`、`ex_date_rows=12`、`pdf_sha256` 相同；只輸出計數與證據狀態，不輸出投資人資料或原始完整 PDF。
- 當時版本 129／129 項測試通過，真正源檔自動驗證成功。後續程式增加 `part=issuer-current`、iPhone 上的「**富達 10 月公告最新配息核對**」，可查看最新三個月份的每單位原幣金額及收益／本金比例。PDF 只由使用者點按才讀，並快取最多一小時。
- `part=issuer` 保留「7 月歷史人工摘錄」作參考，但清楚揭露舊 PDF 位元組並未留存，官網固定 URL 已更新；不再錯誤歸屬目前 PDF 的 SHA。
- **即使 10 月 PDF 12 筆完整核對成功，也仍不代表集保 3-5 金額對帳核准**：該來源缺少已確定的每單位現金金額，需要正式 issuer↔TDCC 對照；`tdcc_amounts_reconciled=0`、`ready_for_total_return=false`、`usable_for_portfolio=false`。
- 沒有取得至少三年完整原始 NAV、配息再投入時點、歷史匯率、費用、拆併／級別變更證據之前，不能把這些公告數字當成績效、退休試算或策略訊號。

## 資料安全與版本回復

- 只修改 `feature/adviser-client-platform-v1` 獨立顧問 Alpha；不修改 `main`、舊網站、27 張 Excel、正式五線譜、SD、六策略、Neon 顧客資料。
- 修改前完整 GitHub 還原分支：`backup/adviser-alpha-before-fidelity-issuer-evidence-20261018`。
- `ADVISER_FIDELITY_PDF_TABLE_PROBE_ON_BOOT=1` 僅供**一次性的非公開成功驗收**，成功後須恢復 `0`，任何正式使用者點按才進行官方文件核對。任何來源更新導致 SHA 不一致須回 503，不改寫原始人工版本，也不能沿用舊分配額。

## 下一步

按 ISIN 建立嚴謹三年以上歷史 NAV、定期分版本發行人公告索引、付款日／再投入 NAV、台幣匯率及費用/拆併核對；授權、客戶登入／隔離與原 Excel 對帳尚未完成。
