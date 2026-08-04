#!/usr/bin/env python3
"""
用合成資料驗證計算與通知邏輯，不碰網路。

執行：python3 test_logic.py
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import alert_logic
from fetch_prices import FetchError, PriceSeries, _sanity_check

CFG = json.loads((Path(__file__).resolve().parent / "config.json").read_text("utf-8"))
MA_T, DD_T = CFG["ma_tiers"], CFG["drawdown_tiers"]

passed = failed = 0


def check(name, got, want):
    global passed, failed
    if got == want:
        passed += 1
        print(f"  ✅ {name}")
    else:
        failed += 1
        print(f"  ❌ {name}\n       得到 {got!r}\n       預期 {want!r}")


def make_series(closes, name="TEST"):
    start = date(2024, 1, 1)
    return PriceSeries(name, [start + timedelta(days=i) for i in range(len(closes))],
                       closes, source="synthetic")


# ── 1. 均線與乖離率 ──────────────────────────────────────────────────
print("\n[1] 均線與乖離率")

s = make_series([100.0] * 200 + [80.0])
check("MA200 = 99.9", round(s.sma(200), 4), 99.9)
check("乖離率 ≈ -19.92%", round(s.deviation_pct(200), 2), -19.92)
check("持平時乖離率 = 0", round(make_series([50.0] * 250).deviation_pct(200), 6), 0.0)

try:
    make_series([100.0] * 150).sma(200)
    check("資料不足時報錯", "沒有報錯", "應丟 FetchError")
except FetchError:
    check("資料不足時報錯", True, True)

ramp = make_series([100.0] * 200 + [90.0] * 21 + [81.0])
check("近 21 日漲跌幅 = -10%", round(ramp.pct_change_over(21), 2), -10.0)
check("回溯超出範圍回傳 None", make_series([1.0] * 10).pct_change_over(63), None)


# ── 2. 52 週高點與回撤 ───────────────────────────────────────────────
print("\n[2] 52 週高點與回撤")

# 高點 120，現價 90 → 回撤 -25%
s2 = make_series([100.0] * 100 + [120.0] + [100.0] * 100 + [90.0])
check("52 週高 = 120", s2.high_over(252), 120.0)
check("回撤 = -25%", round(s2.drawdown_pct(252), 4), -25.0)

# 創新高時回撤為 0
check("創新高時回撤 = 0", round(make_series([10, 20, 30, 40.0]).drawdown_pct(252), 6), 0.0)

# 高點落在視窗外就不該被算進來
old_high = make_series([500.0] + [100.0] * 300)
check("視窗外的舊高點被排除", old_high.high_over(252), 100.0)

# 視窗比資料長時要能正常運作，不能出錯
check("資料短於視窗仍可運作", make_series([50.0, 60.0]).high_over(252), 60.0)


# ── 3. 級別判定 ──────────────────────────────────────────────────────
print("\n[3] 級別判定")

def ma_of(v):
    t = alert_logic.classify(v, MA_T)
    return t["key"] if t else None

def dd_of(v):
    t = alert_logic.classify(v, DD_T)
    return t["key"] if t else None

check("乖離  -9.9% → 未觸發", ma_of(-9.9), None)
check("乖離 -10.0% → 留意（邊界含等於）", ma_of(-10.0), "ma_watch")
check("乖離 -15.0% → 進場區", ma_of(-15.0), "ma_buy")
check("乖離 -25.0% → 深跌區", ma_of(-25.0), "ma_deep")
check("乖離 +30.0% → 未觸發", ma_of(30.0), None)

check("回撤  -9.9% → 未觸發", dd_of(-9.9), None)
check("回撤 -10.0% → 修正", dd_of(-10.0), "dd_correction")
check("回撤 -20.0% → 熊市", dd_of(-20.0), "dd_bear")
check("回撤 -30.0% → 重挫", dd_of(-30.0), "dd_severe")
check("回撤 0% → 未觸發", dd_of(0.0), None)


# ── 4. 兩指標合併 ────────────────────────────────────────────────────
print("\n[4] 兩指標取較嚴重者")

BUF = CFG["recovery_buffer"]


def mk(dd, dev, name="VOO", notify=True):
    return alert_logic.Assessment(
        name=name, price=100.0, latest_date=date(2026, 8, 3), source="synthetic",
        notify=notify, ma=120.0, deviation=dev,
        ma_tier=alert_logic.classify(dev, MA_T),
        high_52w=130.0, drawdown=dd,
        dd_tier=alert_logic.classify(dd, DD_T),
        hold_ma_tier=alert_logic.classify(dev, MA_T, BUF),
        hold_dd_tier=alert_logic.classify(dd, DD_T, BUF),
    )

check("兩者都未觸發 → level 0", mk(-5, -5).level, 0)
check("只有回撤觸發 → level 1", mk(-12, -2).level, 1)
check("只有乖離觸發 → level 1", mk(-3, -11).level, 1)
check("回撤熊市(2) + 乖離留意(1) → level 2", mk(-21, -11).level, 2)
check("回撤修正(1) + 乖離深跌(3) → level 3", mk(-11, -26).level, 3)
check("狀態文字合併兩者", mk(-21, -11).status_text, "熊市 + 留意")
check("只有回撤時的狀態文字", mk(-12, -2).status_text, "修正")
check("未觸發的狀態文字", mk(-1, -1).status_text, "未觸發")

# 這是加第二個指標的核心理由：台股 2026/7 的情境
# 從高點跌 18%，但因前一年大漲、均線基準低，乖離仍是正的
tw = mk(-18.3, +13.0)
check("台股情境：回撤觸發、乖離沒觸發", (tw.dd_tier is not None, tw.ma_tier), (True, None))
check("台股情境：整體 level = 1", tw.level, 1)


# ── 5. 通知抑制 ──────────────────────────────────────────────────────
print("\n[5] 通知抑制")

def run(dd, dev, state, day, repeat=7, recovery=True, notify=True):
    return alert_logic.decide([mk(dd, dev, notify=notify)], state, repeat, recovery, day)

d0 = date(2026, 8, 3)

st = {}
check("未觸發 → 不通知", run(-3, -3, st, d0).should_notify, False)

st = {}
check("首次觸發 → 通知", run(-21, -2, st, d0).should_notify, True)
check("隔天同級別 → 安靜", run(-22, -2, st, d0 + timedelta(days=1)).should_notify, False)
check("第 6 天 → 仍安靜", run(-22, -2, st, d0 + timedelta(days=6)).should_notify, False)
check("第 7 天 → 重複提醒", run(-22, -2, st, d0 + timedelta(days=7)).should_notify, True)

st = {}
run(-12, -2, st, d0)
check("由回撤升級 → 立刻通知", run(-21, -2, st, d0 + timedelta(days=1)).should_notify, True)

# 跨指標升級也要通知：回撤沒變，但乖離開始觸發並更嚴重
st = {}
run(-12, -2, st, d0)
check("換另一個指標惡化 → 也要通知",
      run(-12, -26, st, d0 + timedelta(days=1)).should_notify, True)

st = {}
run(-31, -2, st, d0)
check("級別下降 → 不通知", run(-21, -2, st, d0 + timedelta(days=1)).should_notify, False)

st = {}
run(-21, -2, st, d0)
r = run(-2, -2, st, d0 + timedelta(days=5))
check("脫離警戒 → 通知一次", r.should_notify, True)
check("回復通知列出標的", r.recovered, ["VOO"])
check("回復後狀態歸零", st["tickers"]["VOO"]["level"], 0)
check("回復後隔天 → 安靜", run(-2, -2, st, d0 + timedelta(days=6)).should_notify, False)

st = {}
run(-21, -2, st, d0, repeat=0)
check("repeat=0 → 30 天後仍不重複",
      run(-21, -2, st, d0 + timedelta(days=30), repeat=0).should_notify, False)


# ── 5b. 遲滯：門檻邊緣震盪不該反覆通知 ───────────────────────────────
print("\n[5b] 遲滯緩衝（回測發現的真實缺陷）")

check("hold_level >= level", (mk(-8, -2).hold_level, mk(-8, -2).level), (1, 0))
check("回撤 -8% 在緩衝區內算 level 1", mk(-8, -2).hold_level, 1)
check("回撤 -6.9% 已完全脫離", mk(-6.9, -2).hold_level, 0)

# 這是 2021 年 10-11 月 QQQ 的實際情境：在 -10% 附近來回
st = {}
check("跌到 -10.5% → 通知", run(-10.5, -2, st, d0).should_notify, True)
check("回到 -9% → 不算脫離（仍在緩衝區）",
      run(-9.0, -2, st, d0 + timedelta(days=3)).should_notify, False)
check("再掉到 -10.3% → 不重複通知",
      run(-10.3, -2, st, d0 + timedelta(days=5)).should_notify, False)
check("狀態仍維持 level 1", st["tickers"]["VOO"]["level"], 1)
check("回到 -6% → 這次才算脫離",
      run(-6.0, -2, st, d0 + timedelta(days=9)).should_notify, True)
check("脫離後歸零", st["tickers"]["VOO"]["level"], 0)

# 但緩衝不該讓「新觸發」變遲鈍——首次觸發仍用嚴格門檻
st = {}
check("未觸發時 -8% 不該通知（緩衝只用於退出）",
      run(-8.0, -2, st, d0).should_notify, False)
check("未觸發時 -8% 級別仍為 0", st["tickers"]["VOO"]["level"], 0)


# ── 6. notify=false 的標的（QQQ）─────────────────────────────────────
print("\n[6] 僅供參考的標的不發通知")

st = {}
check("QQQ 觸發但不通知", run(-31, -30, st, d0, notify=False).should_notify, False)
check("QQQ 狀態仍被記錄", st["tickers"]["VOO"]["level"], 3)

st = {}
d = alert_logic.decide(
    [mk(-31, -30, "QQQ", notify=False), mk(-21, -2, "VOO", notify=True)],
    st, 7, True, d0)
check("混合時只因 VOO 通知", d.should_notify, True)
check("通知理由不含 QQQ", "QQQ" in d.reason, False)

st = {}
d = alert_logic.decide([mk(-31, -30, "QQQ", notify=False)], st, 7, True, d0)
check("只有 QQQ 觸發 → 完全不通知", d.should_notify, False)


# ── 7. 壞資料防呆 ────────────────────────────────────────────────────
print("\n[7] 壞資料防呆")

def expect_reject(name, dates, closes):
    try:
        _sanity_check("TEST", dates, closes)
        check(name, "通過了", "應該被擋下")
    except FetchError:
        check(name, True, True)

dts = [date(2024, 1, 1) + timedelta(days=i) for i in range(5)]
expect_reject("空資料被擋下", [], [])
expect_reject("含 0 價格被擋下", dts, [100, 100, 0, 100, 100])
expect_reject("含負價格被擋下", dts, [100, 100, -5, 100, 100])
expect_reject("筆數不一致被擋下", dts, [100, 100])
expect_reject("日期亂序被擋下", list(reversed(dts)), [100] * 5)
expect_reject("單日跳動 >50% 被擋下", dts, [100, 100, 100, 40, 100])

try:
    _sanity_check("TEST", dts, [100, 102, 99, 105, 101])
    check("正常資料可通過", True, True)
except FetchError as e:
    check("正常資料可通過", f"被誤擋：{e}", True)


print(f"\n{'=' * 46}")
print(f"通過 {passed} 項，失敗 {failed} 項")
print("=" * 46)
raise SystemExit(1 if failed else 0)
