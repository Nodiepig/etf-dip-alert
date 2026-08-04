# ETF 大跌警報

每個交易日自動檢查 **VOO** 和 **0050** 是否明顯跌破 200 日均線，觸發門檻才發 LINE 通知。

跑在 GitHub Actions 上，不需要開電腦、不需要任何訂閱。

---

## 它做什麼

| 級別 | 條件（相對 200 日均線的乖離率） |
|---|---|
| 🟡 留意 | −10% 以下 |
| 🟠 進場區 | −15% 以下 |
| 🔴 深跌區 | −25% 以下 |

沒觸發的日子**完全不會通知**。這點很重要——如果每天推播，你會在第三天把通知關掉，那工具就等於失效了。

觸發時的通知長這樣：

```
🟠 VOO 進場區訊號

VOO  現價 $560.00｜MA200 $678.50｜乖離 -17.5% 🟠
0050  現價 NT$91.20｜MA200 NT$95.10｜乖離 -4.1% ✅

VOO 已跌破 200 日均線 17.5%，落在進場區。（近一個月 -12.3%、近三個月 -19.8%）

── 相關新聞 ──
• Stocks tumble as tariff fears mount（Reuters）
  https://news.google.com/...

※ 標題未經查證，請自行判讀。
```

---

## 設定步驟

### 1. 建立 GitHub repo

在 https://github.com/new 建一個新的 repo，名字建議 `etf-dip-alert`。

**設成 Public。** 公開 repo 的 Actions 分鐘數是無限的，私有 repo 每月只有 2000 分鐘。這個專案沒有任何機密內容——LINE 金鑰是存在 GitHub Secrets 裡，不會出現在程式碼中。

建好後把這個資料夾推上去：

```bash
cd ~/etf-dip-alert
git init
git add -A
git commit -m "ETF 大跌警報"
git branch -M main
git remote add origin https://github.com/nodiepig/etf-dip-alert.git
git push -u origin main
```

### 2. 設定 LINE 金鑰

到 repo 的 **Settings → Secrets and variables → Actions → New repository secret**，新增兩個：

| Name | Value |
|---|---|
| `LINE_CHANNEL_ACCESS_TOKEN` | 你的頻道存取權杖 |
| `LINE_USER_ID` | 你的 LINE User ID |

這兩個值跟 price-tracker 專案用的是同一組，可以在 `~/price-tracker/com.aries.pricetracker.plist` 裡面找到。

### 3. 開啟寫入權限

**Settings → Actions → General → Workflow permissions**，選 **Read and write permissions**，儲存。

這是為了讓 Actions 能把 `data/state.json` 寫回 repo——這個檔案記錄「上次通知到哪個級別」，沒有它就無法避免重複通知。

### 4. 開啟 GitHub Pages

**Settings → Pages**：

- Source 選 **Deploy from a branch**
- Branch 選 **main**，資料夾選 **/docs**
- Save

網址會是 `https://nodiepig.github.io/etf-dip-alert/`。第一次要等幾分鐘才會生效。

（頁面第一次執行後才會產生內容，所以先做完下一步再來看。）

### 5. 測試一次

到 **Actions** 分頁 → 左邊選「ETF 大跌警報」→ 右邊 **Run workflow**：

- 先勾 **dry_run** 跑一次，看 log 裡的價格數字對不對
- 再勾 **force** 跑一次，確認 LINE 真的收得到

兩個都正常就完成了，之後每個交易日早上 9 點（台北時間）自動執行。

---

## 本機執行

平常不需要，但要調參數或除錯時很方便：

```bash
cd ~/etf-dip-alert
pip3 install -r requirements.txt

python3 test_logic.py              # 跑邏輯測試（不連網）
python3 check_etf.py --dry-run     # 抓真實價格但不發通知
python3 check_etf.py --force       # 強制發一次 LINE

# 本機要發通知的話需要先設環境變數
export LINE_CHANNEL_ACCESS_TOKEN="..."
export LINE_USER_ID="..."
```

---

## 調整設定

改 `config.json`，push 上去就生效：

- **門檻**：改 `tiers` 裡的 `threshold`（負數，單位 %）
- **重複提醒間隔**：`repeat_reminder_days`，預設 7 天。設 `0` 表示同一級別內完全不重複提醒
- **加標的**：在 `tickers` 陣列裡加一筆，需要填 `stooq` 和 `yahoo` 兩種代號
- **執行時間**：改 `.github/workflows/daily.yml` 裡的 cron（**UTC 時間**，台北時間要減 8 小時）

---

## 通知規則細節

不是「跌破就每天吵你」，而是：

1. 第一次進入某級別 → 通知
2. 惡化到更嚴重的級別（🟡→🟠）→ 立刻通知
3. 停留在同一級別 → 安靜，每 7 天提醒一次
4. 級別下降（🟠→🟡）→ **不通知**，避免在門檻邊緣來回洗版
5. 完全脫離警戒（回到 −10% 以內）→ 通知一次然後歸零

---

## 已知限制

**排程時間不精準。** GitHub 忙碌時可能延遲數分鐘到半小時，偶爾會跳過。對這個用途無妨——乖離 −15% 的狀態會持續好幾天到好幾週，不像搶特價要分秒必爭。

**repo 閒置 60 天後排程會被停用。** 這是 GitHub 對公開 repo 的政策，會先寄信通知。正常情況下每次通知都會提交 state.json，算是活動；但如果連續兩個月完全沒觸發任何警報，就可能被停掉。收到 GitHub 的信時去 Actions 頁面按一下重新啟用即可。

**資料來源可能改變。** 主要用 Stooq，失敗時自動改用 yfinance。兩個都是免費非官方來源，哪天格式改了就會抓不到——程式會明確報錯而不是回傳可疑數字，你會在 Actions 的執行紀錄裡看到失敗。

**200 日均線不是預測工具。** 它只描述「現在的價格相對過去 200 個交易日的平均偏離多少」。乖離大代表市場正在發生某些事，不代表現在是底部——歷史上跌破均線 20% 之後繼續跌到 40% 的情況並不罕見。這個工具的作用是讓你**注意到**，不是告訴你**該買**。

**新聞只列標題不做解釋。** 自動化腳本沒有判斷力去區分「真正的觸發事件」和「財經媒體事後編出來的因果」，所以只提供標題和連結讓你自己判讀。

---

## 檔案結構

```
etf-dip-alert/
├── .github/workflows/daily.yml   # GitHub Actions 排程
├── check_etf.py                  # 主程式
├── fetch_prices.py               # 抓價 + 資料健全性檢查
├── alert_logic.py                # 級別判定 + 重複通知抑制
├── news.py                       # 觸發時抓新聞標題
├── notify_line.py                # LINE 推播
├── test_logic.py                 # 邏輯測試（37 項，不連網）
├── config.json                   # 標的與門檻設定
└── data/state.json               # 上次通知狀態（Actions 自動更新）
```
