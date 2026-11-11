---
name: tw-stock-screener
description: 依四個條件篩選台股上市股票（三率三升＋月營收成長、EPS 持續成長、三大法人低調建倉、預估殖利率），產生可互動的 HTML 儀表板（dashboard.html）和 CSV（screen_result.csv）。
---

# 台股選股儀表板 SKILL

這個技能會依四個條件篩選台股上市股票，產生可互動的 HTML 儀表板（dashboard.html）和 CSV（screen_result.csv）。完整程式就在本 repo 的 `tw_screener.py` 與 `dashboard_template.html`，不需要外部檔案就能重建。

## 選股條件（預設門檻，儀表板上都可以調）

| # | 條件 | 預設定義 | 資料來源 |
|---|------|----------|----------|
| ① | 三率三升＋月營收成長 | 最新一季單季毛利率、營益率、淨利率都高於上一季（可切換為去年同季），而且最近 3 個月的月營收 YoY 都 > 0% | MOPS 綜合損益表彙總 ajax_t163sb04、月營收 t21sc03 |
| ② | EPS 持續成長 | 最近 3 季的單季 EPS 都高於去年同季，且最新一季 EPS > 0 | MOPS 綜合損益表彙總 |
| ③ | 三大法人低調建倉 | 近 20 個交易日外資（不含外資自營商）＋投信累計買超 ≥ 股本 0.3%，買超天數 ≥ 11 天，同期股價漲幅 < 10% | TWSE rwd/zh/fund/T86、rwd/zh/afterTrading/MI_INDEX、OpenAPI t187ap03_L（股本、產業） |
| ④ | 預估殖利率 > 5% | 證交所殖利率 × 近四季 EPS ÷ 去年全年 EPS（假設配息率不變）；缺季報或 EPS 為負時，改用證交所公布的殖利率 | OpenAPI exchangeReport/BWIBBU_ALL |

## 計算細節

- 季報公布的是年初累計數，單季 = 本季累計 − 上季累計（Q1 直接用）。
- 只篩 4 碼普通股。金融保險業沒有毛利欄位，條件 ① 不適用。
- 季報公告截止日：Q1 5/15、Q2 8/14、Q3 11/14、Q4 次年 3/31。程式只採用已過截止日的最新一季。
- 月營收在次月 10 日前公告。最新月份如果少於 100 家公布，就往前退一個月。

## 執行流程

1. **建立檔案**：`tw_screener.py` 和 `dashboard_template.html` 放在同一個資料夾，另有 `requirements.txt`（requests、pandas、lxml 各一行）。
2. **先跑模擬資料**：執行 `python tw_screener.py --demo --out demo.html --csv demo.csv`。用 Playwright 開啟頁面，確認沒有 JS 錯誤，KPI、篩選、排序都正常。
3. **確認網路**：用 curl 測試 openapi.twse.com.tw、www.twse.com.tw、mopsov.twse.com.tw。如果回傳 307，或頁面出現「因為安全性考量，您所執行的頁面無法呈現」，就是被防火牆擋了（見下方「已知限制」）。
4. **跑真實資料**：執行 `python tw_screener.py`（可加 `--days 20 --months 3 --date YYYY-MM-DD --json raw.json`）。每次請求間隔 3 秒，第一次大約 5～10 分鐘。結果快取在 `./cache`，重跑只補缺的部分。
5. **核對數字**：
   - 抽查台積電（2330）的單季營收和 EPS，與公開資訊觀測站的單季數字比對，確認累計相減正確。
   - 確認三率、EPS 欄位不是大量空值，否則代表 MOPS 欄位名稱變了，要調整 `fetch_income_cum` 裡的 `pick_col` 關鍵字。
   - 確認法人統計的交易日數是 20 天，頁首會顯示天數。
6. **交付**：把 dashboard.html 發佈成 artifact，或傳檔給使用者，並列出四項全符合的股票。附註本頁只是篩選工具，不構成投資建議。

## 已知限制（2026-11 實測）

- **MOPS 擋雲端 IP**：mops.twse.com.tw、mopsov.twse.com.tw 對雲端主機回 307 或「因為安全性考量…」頁面，開網域白名單也沒用。條件 ① 和 ② 要用的歷史季報和月營收只能從台灣本地網路抓，所以要在使用者自己的電腦上執行（或請使用者自己跑）。
  - 2026-11-11 在 Muse 雲端 VM 復測：OpenAPI 兩支與 TWSE T86 單次請求皆回 HTTP 200 並拿到當日資料；MOPS 季報 ajax_t163sb04 則被限速至約 15 KB/s——單季彙總表 1.6 MB 以逾時 300 秒設定約 107 秒可完整下載，且解析與台積電（2330）累計數核對正確。因此雲端並非完全跑不動，但全量（10 季季報＋3 個月營收）需數十分鐘，程式的下載逾時必須由 30 秒拉長至 300 秒；本機（台灣網路）執行仍是首選，約 5～10 分鐘。
- **www.twse.com.tw 限流**：雲端 IP 連續請求約 10 次後會被擋（307）。程式遇到 307 或 403 會立即停止，用已取得的交易日計算，不要一直重試。
- **OpenAPI 只有最新一期**：t187ap05_L（最新月營收）、t187ap06_L_*／t187ap17_L（本季已公布的季報和三率）沒有歷史資料，只能當輔助。BWIBBU_ALL（殖利率、本益比、淨值比）和 t187ap03_L 在雲端可以正常抓。
- 不要用繞過防火牆的方法（換 IP、偽造來源等）。被擋就說明原因，改在本地執行。

## 想調整時

- 預設門檻在 `tw_screener.py` 的 `DEFAULTS`。儀表板的初始值也從這裡讀。
- 新增條件時，要同時改 Python 的 `screen()`（輸出 CSV 用）和範本 JS 的 `evalStock()`、COLS、KPI、mode 選項，兩邊邏輯要一致。
