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
TIERS = CFG["tiers"]

passed = failed = 0


def check(name: str, got, want):
    global passed, failed
    if got == want:
        passed += 1
        print(f"  ✅ {name}")
    else:
        failed += 1
        print(f"  ❌ {name}\n       得到 {got!r}\n       預期 {want!r}")


def make_series(closes: list[float], name="TEST") -> PriceSeries:
    start = date(2024, 1, 1)
    dates = [start + timedelta(days=i) for i in range(len(closes))]
    return PriceSeries(name, dates, closes, source="synthetic")


# ── 1. MA200 與乖離率 ────────────────────────────────────────────────
print("\n[1] 均線與乖離率計算")

# 前 200 天都是 100，最後一天跌到 80。
# MA200 取的是「最後 200 筆」= 199 個 100 + 一個 80 = 19980/200 = 99.9
s = make_series([100.0] * 200 + [80.0])
check("MA200 = 99.9", round(s.sma(200), 4), 99.9)
check("乖離率 ≈ -19.92%", round(s.deviation_pct(200), 2), -19.92)

# 完全持平時乖離率為 0
flat = make_series([50.0] * 250)
check("持平時乖離率 = 0", round(flat.deviation_pct(200), 6), 0.0)

# 資料不足要明確報錯，不能默默用少於 200 筆去算
try:
    make_series([100.0] * 150).sma(200)
    check("資料不足時報錯", "沒有報錯", "應丟 FetchError")
except FetchError:
    check("資料不足時報錯", True, True)

# 回溯漲跌幅
ramp = make_series([100.0] * 200 + [90.0] * 21 + [81.0])
check("近 21 個交易日漲跌幅 = -10%", round(ramp.pct_change_over(21), 2), -10.0)
check("回溯超出範圍回傳 None", make_series([1.0] * 10).pct_change_over(63), None)


# ── 2. 級別判定 ──────────────────────────────────────────────────────
print("\n[2] 級別判定（門檻 -10 / -15 / -25）")

def tier_of(dev):
    t = alert_logic.classify(dev, TIERS)
    return t["key"] if t else None

check("  -5%  → 未觸發", tier_of(-5.0), None)
check("  -9.9% → 未觸發", tier_of(-9.9), None)
check(" -10.0% → watch（邊界含等於）", tier_of(-10.0), "watch")
check(" -14.9% → watch", tier_of(-14.9), "watch")
check(" -15.0% → buy", tier_of(-15.0), "buy")
check(" -24.9% → buy", tier_of(-24.9), "buy")
check(" -25.0% → deep", tier_of(-25.0), "deep")
check(" -40.0% → deep", tier_of(-40.0), "deep")
check(" +30.0% → 未觸發（大漲不該觸發）", tier_of(30.0), None)


# ── 3. 通知抑制 ──────────────────────────────────────────────────────
print("\n[3] 重複通知抑制")

def mk(dev: float, name="VOO") -> alert_logic.Assessment:
    t = alert_logic.classify(dev, TIERS)
    a = alert_logic.Assessment(
        name=name, price=100.0, ma=120.0, deviation=dev,
        latest_date=date(2026, 8, 3), source="synthetic",
    )
    if t:
        a.tier_key, a.tier_name, a.tier_emoji = t["key"], t["name"], t["emoji"]
    return a


def run(dev, state, day, repeat=7, recovery=True):
    return alert_logic.decide([mk(dev)], state, TIERS, repeat, recovery, day)


d0 = date(2026, 8, 3)

# 沒事的日子不該通知
st = {}
check("未觸發時不通知", run(-3.0, st, d0).should_notify, False)

# 第一次跌破 -15% 要通知
st = {}
check("首次進入進場區 → 通知", run(-16.0, st, d0).should_notify, True)

# 隔天還在同一級別，安靜
check("隔天同級別 → 安靜", run(-17.0, st, d0 + timedelta(days=1)).should_notify, False)

# 第 6 天仍安靜，第 7 天提醒一次
check("第 6 天 → 仍安靜", run(-17.0, st, d0 + timedelta(days=6)).should_notify, False)
check("第 7 天 → 重複提醒", run(-17.0, st, d0 + timedelta(days=7)).should_notify, True)

# 惡化到深跌區要立刻通知，不受間隔限制
st = {}
run(-16.0, st, d0)
check("惡化 進場區→深跌區 → 立刻通知",
      run(-26.0, st, d0 + timedelta(days=1)).should_notify, True)

# 級別下降不該通知（避免在門檻邊緣來回洗版）
st = {}
run(-26.0, st, d0)
check("級別下降 深跌區→進場區 → 不通知",
      run(-16.0, st, d0 + timedelta(days=1)).should_notify, False)

# 完全脫離警戒通知一次，之後歸零
st = {}
run(-16.0, st, d0)
r = run(-2.0, st, d0 + timedelta(days=5))
check("回到警戒區外 → 通知一次", r.should_notify, True)
check("回復通知列出標的", r.recovered, ["VOO"])
check("回復後狀態歸零", st["tickers"]["VOO"]["tier"], None)
check("回復後隔天 → 安靜",
      run(-2.0, st, d0 + timedelta(days=6)).should_notify, False)

# repeat=0 表示完全不重複提醒
st = {}
run(-16.0, st, d0, repeat=0)
check("repeat=0 → 30 天後仍不重複",
      run(-16.0, st, d0 + timedelta(days=30), repeat=0).should_notify, False)

# 兩檔獨立計算，互不干擾
st = {}
d = alert_logic.decide([mk(-16.0, "VOO"), mk(-2.0, "0050")], st, TIERS, 7, True, d0)
check("只有 VOO 觸發時仍發出通知", d.should_notify, True)
check("0050 狀態獨立保持未觸發", st["tickers"]["0050"]["tier"], None)
d = alert_logic.decide([mk(-17.0, "VOO"), mk(-2.0, "0050")], st, TIERS, 7,
                       True, d0 + timedelta(days=1))
check("兩檔都無變化 → 安靜", d.should_notify, False)


# ── 4. 資料健全性檢查 ────────────────────────────────────────────────
print("\n[4] 壞資料防呆")

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


print(f"\n{'='*46}")
print(f"通過 {passed} 項，失敗 {failed} 項")
print("=" * 46)
raise SystemExit(1 if failed else 0)
