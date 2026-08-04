#!/usr/bin/env python3
"""
回測：這組門檻在歷史上會觸發幾次？觸發後買進的話，後續報酬如何？

用法：
    python3 backtest.py                 # 預設抓 20 年
    python3 backtest.py --years 25
    python3 backtest.py --ticker VOO

輸出兩件事：
    1. 每次觸發的日期、當時的回撤與乖離、以及買進後 1/3/5 年的報酬
    2. 跟「無腦每月定期定額」的對照

## 讀這份結果時要小心的地方

**倖存者偏誤。** VOO 追蹤的是 S&P 500，這是一個過去一百年持續上漲的市場。
任何「跌了就買」的策略在這種市場都會看起來很好。這不保證未來如此。

**樣本數極少。** 20 年大概只會有 2~4 次觸發。從三四個樣本歸納出「這招有效」
在統計上非常薄弱——你看到的可能只是 2009 和 2020 這兩次剛好反彈很快。

**沒有計入現金成本。** 這裡假設你在觸發當天有錢可投。如果那筆錢是為了等待
而長期閒置的，真實報酬要扣掉閒置期間的機會成本，那往往足以吃掉全部優勢
（參見 Nick Maggiulli「Even God Couldn't Beat Dollar-Cost Averaging」）。

**觸發日不等於底部。** 報酬是從觸發當天算起，但觸發後通常還會繼續跌，
中間的帳面虧損可能很難承受。輸出裡的「觸發後最大續跌」就是在講這件事。
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import alert_logic
import fetch_prices

BASE_DIR = Path(__file__).resolve().parent
TRADING_DAYS_YEAR = 252


def load_config() -> dict:
    with (BASE_DIR / "config.json").open(encoding="utf-8") as f:
        return json.load(f)


def simulate(series, cfg: dict):
    """
    逐日重放歷史，套用跟正式版相同的判斷邏輯。

    這裡刻意把 repeat_reminder_days 設成 0，只記錄「新觸發」與「惡化升級」，
    不記錄「持續提醒」。否則一次持續三個月的熊市會被算成十幾次事件，
    看起來像警報很頻繁，但實際上你只會被通知一次然後每週提醒。
    我們要回答的是「歷史上發生過幾次值得進場的行情」，不是「總共收到幾則訊息」。
    """
    ma_win, hi_win = cfg["ma_window"], cfg["high_window"]
    closes, dates = series.closes, series.dates

    events = []
    state: dict = {}

    for i in range(ma_win, len(closes)):
        window_closes = closes[: i + 1]
        price = closes[i]
        ma = sum(window_closes[-ma_win:]) / ma_win
        high = max(window_closes[-hi_win:])

        dev = (price - ma) / ma * 100.0
        dd = (price - high) / high * 100.0

        buf = cfg["recovery_buffer"]
        a = alert_logic.Assessment(
            name=series.name, price=price, latest_date=dates[i], source="backtest",
            ma=ma, deviation=dev, ma_tier=alert_logic.classify(dev, cfg["ma_tiers"]),
            high_52w=high, drawdown=dd,
            dd_tier=alert_logic.classify(dd, cfg["drawdown_tiers"]),
            hold_ma_tier=alert_logic.classify(dev, cfg["ma_tiers"], buf),
            hold_dd_tier=alert_logic.classify(dd, cfg["drawdown_tiers"], buf),
        )

        d = alert_logic.decide([a], state, 0, False, dates[i])
        if d.should_notify:
            events.append((i, a))

    return events


def forward_return(closes: list[float], i: int, years: int) -> float | None:
    j = i + TRADING_DAYS_YEAR * years
    if j >= len(closes):
        return None
    return (closes[j] - closes[i]) / closes[i] * 100.0


def max_further_drop(closes: list[float], i: int, horizon: int = TRADING_DAYS_YEAR) -> float:
    """觸發後續跌的最深幅度（%）。這是你買進後要承受的帳面虧損。"""
    end = min(i + horizon, len(closes))
    trough = min(closes[i:end])
    return (trough - closes[i]) / closes[i] * 100.0


def report(series, cfg: dict) -> None:
    closes = series.closes
    print(f"\n{'=' * 66}")
    print(f"  {series.name}　{series.dates[0]} ~ {series.dates[-1]}"
          f"（{len(closes)} 個交易日，約 {len(closes)/252:.1f} 年）")
    print(f"{'=' * 66}")

    events = simulate(series, cfg)
    if not events:
        print("\n  這段期間內完全沒有觸發過任何警報。")
    else:
        print(f"\n  共 {len(events)} 次新觸發或升級"
              f"（平均約每 {len(closes)/252/len(events):.1f} 年一次）")
        print("  ※ 不含「持續提醒」，同一波行情只會在進入和惡化時各算一次\n")
        print(f"  {'日期':<12}{'回撤':>8}{'乖離':>8}{'級別':>14}"
              f"{'續跌':>8}{'+1年':>8}{'+3年':>8}{'+5年':>8}")
        print("  " + "-" * 64)
        for i, a in events:
            def fmt(v):
                return f"{v:+.0f}%" if v is not None else "   —"
            print(f"  {a.latest_date!s:<12}{a.drawdown:>7.1f}%{a.deviation:>7.1f}%"
                  f"{a.status_text:>14}"
                  f"{max_further_drop(closes, i):>7.0f}%"
                  f"{fmt(forward_return(closes, i, 1)):>8}"
                  f"{fmt(forward_return(closes, i, 3)):>8}"
                  f"{fmt(forward_return(closes, i, 5)):>8}")

        rets = [forward_return(closes, i, 3) for i, _ in events]
        rets = [r for r in rets if r is not None]
        if rets:
            print(f"\n  觸發後 3 年平均報酬：{sum(rets)/len(rets):+.1f}%"
                  f"（{len(rets)} 個樣本）")
        drops = [max_further_drop(closes, i) for i, _ in events]
        print(f"  觸發後最大續跌：平均 {sum(drops)/len(drops):.1f}%，"
              f"最深 {min(drops):.1f}%")

    total = (closes[-1] - closes[0]) / closes[0] * 100.0
    print(f"\n  參考：整段期間買進持有 {total:+.0f}%")
    print(f"  （這只是給上面的報酬數字一個對照的量尺，"
          f"不是說買進持有跟逢低加碼可以直接比——兩者投入的時間點和金額都不同）")


def main() -> int:
    ap = argparse.ArgumentParser(description="ETF 大跌警報 — 歷史回測")
    ap.add_argument("--ticker", help="只測單一標的（例如 VOO），預設全部")
    ap.add_argument("--years", type=int, default=20, help="回測年數（預設 20）")
    args = ap.parse_args()

    cfg = load_config()
    targets = [t for t in cfg["tickers"]
               if not args.ticker or t["name"] == args.ticker]
    if not targets:
        print(f"找不到標的 {args.ticker}")
        return 1

    for tcfg in targets:
        try:
            series = fetch_prices.fetch(tcfg)
        except Exception as e:  # noqa: BLE001
            print(f"\n{tcfg['name']}: 抓不到資料 — {e}")
            continue

        need = args.years * TRADING_DAYS_YEAR
        if len(series.closes) > need:
            series = fetch_prices.PriceSeries(
                series.name, series.dates[-need:], series.closes[-need:], series.source
            )
        report(series, cfg)

    print("\n" + "─" * 66)
    print("  提醒：樣本數很少，且這些市場過去都持續上漲。")
    print("  「觸發後買進報酬不錯」有很大部分只是反映市場長期向上，")
    print("  不代表這個門檻本身有預測能力。詳見本檔開頭的說明。")
    print("─" * 66)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
