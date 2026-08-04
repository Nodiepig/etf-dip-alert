# ETF 大跌警報

每個交易日自動檢查 **VOO**、**0050**、**QQQ** 是否明顯下跌，觸發門檻才發 LINE 通知，並更新看板網頁。

跑在 GitHub Actions 上，不需要開電腦、不需要任何訂閱。

**看板：** https://nodiepig.github.io/etf-dip-alert/

---

## 兩個指標，任一達標就通知

| 指標 | 看什麼 | 🟡 | 🟠 | 🔴 |
|---|---|---|---|---|
| **高點回撤** | 現價距 52 週最高收盤價 | −10% 修正 | −20% 熊市 | −30% 重挫 |
| **均線乖離** | 現價相對 200 日均線 | −10% 留意 | −15% 進場區 | −25% 深跌區 |

為什麼要兩個？因為它們的盲點不一樣：

**均線乖離**的基準會自己跟著市場走，設定一次就能放著不管。但它落後——價格慢慢跌，均線也跟著往下，乖離率可能永遠到不了門檻。而且前一年漲越多的市場，越難觸發。

**高點回撤**貼近直覺的「從高點跌下來多少」，急跌陰跌都抓得到，市場慣例也是這樣定義修正和熊市。

實例：2026 年 7 月韓股崩盤拖累全球，台股從高點跌 18.3%。**回撤指標會亮黃燈，乖離指標完全沒反應**——因為台股前一年漲太多，均線基準還停在很低的位置。

QQQ 標示為僅供參考（`notify: false`）：照樣計算並顯示在看板上，但不發通知。

---

## 不會吵你的三道機制

1. **沒觸發的日子完全靜音。** 不會有「今天沒事」這種訊息。
2. **同一級別內每 7 天才提醒一次。** 熊市會持續好幾週，每天推播的話你第三天就會靜音，工具等於失效。
3. **遲滯緩衝 3 個百分點。** 已經在警戒中時，要回升到門檻上方 3% 才算脫離。沒有這個機制的話，價格在 −10% 附近震盪會導致「觸發 → 回到 −9% 解除 → 又掉到 −10.3% 再觸發」反覆通知——回測顯示同一波行情會產生近十次通知。

完整規則：

- 第一次進入某級別 → 通知
- 惡化到更嚴重的級別（含換另一個指標惡化）→ 立刻通知
- 停留在同一級別 → 安靜，每 7 天提醒一次
- 級別下降 → **不通知**
- 明顯脫離警戒 → 通知一次然後歸零

---

## 回測

```bash
python3 backtest.py              # 全部標的，20 年
python3 backtest.py --ticker VOO --years 25
```

輸出每次觸發的日期、當時的回撤與乖離、**觸發後最大續跌**、以及 1/3/5 年後的報酬。

**「觸發後最大續跌」是最該看的一欄**——它告訴你買進之後帳面上會先虧多少。訊號響起不代表是底部。

讀結果時要小心四件事（腳本開頭有更詳細的說明）：

- **倖存者偏誤**：這些市場過去都持續上漲，任何「跌了就買」在這種市場都會好看
- **樣本數極少**：20 年大概只有 2~4 次觸發，從三四個樣本歸納「這招有效」在統計上非常薄弱
- **沒有計入現金成本**：假設你在觸發當天有錢可投。若那筆錢是為了等待而長期閒置，機會成本往往足以吃掉全部優勢
- **觸發日不等於底部**

---

## 設定步驟

### 1. 建立 GitHub repo

在 https://github.com/new 建一個 repo，名稱 `etf-dip-alert`，**設成 Public**（公開 repo 的 Actions 分鐘數無限，私有的每月只有 2000 分鐘）。

```bash
cd ~/etf-dip-alert
git init
git add -A
git commit -m "ETF 大跌警報"
git branch -M main
git remote add origin https://github.com/Nodiepig/etf-dip-alert.git
git push -u origin main
```

### 2. 設定 LINE 金鑰

**Settings → Secrets and variables → Actions → New repository secret**，新增兩個：

| Name | Value |
|---|---|
| `LINE_CHANNEL_ACCESS_TOKEN` | 頻道存取權杖 |
| `LINE_USER_ID` | 你的 LINE User ID |

跟 price-tracker 專案用同一組，可在 `~/price-tracker/com.aries.pricetracker.plist` 找到。

### 3. 開啟 Actions 寫入權限

**Settings → Actions → General → Workflow permissions** → **Read and write permissions** → Save。

讓 Actions 能把 `data/state.json` 和 `docs/index.html` 寫回 repo。

### 4. 跑一次產生看板內容

**Actions → ETF 大跌警報 → Run workflow**，勾「只看結果，不發通知」。

跑完 repo 裡會出現 `docs/index.html`。

### 5. 開啟 GitHub Pages

**Settings → Pages** → Source 選 **Deploy from a branch** → Branch **main**、資料夾 **/docs** → Save。

⚠️ 順序不能顛倒——`docs/` 資料夾要先存在，Pages 才 build 得起來。

### 6. 測試通知

再跑一次 workflow，這次勾「強制發送通知」，確認 LINE 收得到。

---

## 本機執行

```bash
cd ~/etf-dip-alert
pip3 install -r requirements.txt

python3 test_logic.py              # 67 項邏輯測試，不連網
python3 check_etf.py --dry-run     # 抓真實價格但不發通知
python3 check_etf.py --force       # 強制發一次 LINE
python3 backtest.py                # 歷史回測

export LINE_CHANNEL_ACCESS_TOKEN="..."   # 本機要發通知才需要
export LINE_USER_ID="..."
```

---

## 調整設定

改 `config.json` 後 push 就生效。單純調參數的話直接在 GitHub 網頁上編輯更快（進 repo 點檔案 → 鉛筆圖示 → 改 → Commit）。

- **門檻**：`ma_tiers` / `drawdown_tiers` 裡的 `threshold`（負數，單位 %）
- **重複提醒間隔**：`repeat_reminder_days`，設 `0` = 同一級別內完全不重複
- **遲滯緩衝**：`recovery_buffer`，調大 = 更不容易解除警戒
- **加標的**：在 `tickers` 加一筆，需要 `stooq` 和 `yahoo` 兩種代號；`notify: false` 表示只顯示不通知
- **執行時間**：`.github/workflows/daily.yml` 的 cron（**UTC**，台北時間要減 8 小時）

---

## 已知限制

**排程時間不精準。** GitHub 忙碌時可能延遲數分鐘到半小時，偶爾跳過。對這個用途無妨——警戒狀態會持續好幾天到好幾週。

**repo 閒置 60 天後排程會被停用。** GitHub 對公開 repo 的政策，會先寄信通知。因為每次執行都會更新看板並提交，正常情況下不會觸發這個限制。

**資料來源是免費非官方服務。** 主要用 Stooq，失敗時自動改用 yfinance。哪天格式改了就會抓不到——程式會明確報錯而不是回傳可疑數字，你會在 Actions 執行紀錄裡看到失敗。抓價前有六道健全性檢查（0 值、負值、日期亂序、單日跳動超過 50% 等）。

**回撤用的是收盤價高點，不是盤中最高價。** 這會讓回撤幅度略微低估，但現價也是收盤價，用同一個基準比較才有意義。

**這兩個指標都不預測底部。** 它們只描述現況。歷史上跌破 20% 之後繼續跌到 40% 的情況並不罕見。這個工具的作用是讓你**注意到**，不是告訴你**該買**。機械式價格訊號，不是投資建議。

**新聞只列標題不做解釋。** 自動化腳本沒有判斷力區分「真正的觸發事件」和「財經媒體事後編出來的因果」，所以只給標題和連結讓你自己判讀。

---

## 檔案結構

```
etf-dip-alert/
├── .github/workflows/daily.yml   # GitHub Actions 排程
├── check_etf.py                  # 主程式
├── fetch_prices.py               # 抓價 + 健全性檢查 + 均線/回撤計算
├── alert_logic.py                # 級別判定 + 遲滯 + 通知抑制
├── generate_dashboard.py         # 產生 docs/index.html
├── backtest.py                   # 歷史回測
├── news.py                       # 觸發時抓新聞標題
├── notify_line.py                # LINE 推播
├── test_logic.py                 # 67 項邏輯測試，不連網
├── config.json                   # 標的與門檻設定
├── data/state.json               # 上次通知狀態（Actions 自動更新）
└── docs/index.html               # 看板網頁（Actions 自動更新）
```
