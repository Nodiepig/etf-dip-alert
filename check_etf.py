#!/usr/bin/env python3
"""
ETF 大跌警報 — 主程式

每個交易日檢查追蹤標的是否明顯跌破 200 日均線，觸發門檻才發 LINE 通知。

用法：
    python3 check_etf.py              # 正常執行
    python3 check_etf.py --dry-run    # 只印結果，不發通知、不寫入狀態
    python3 check_etf.py --force      # 無視門檻與重複抑制，強制發一次（測試用）
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import alert_logic
import fetch_prices
import generate_dashboard
import news
from notify_line import send_line_message

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.json"
STATE_FILE = BASE_DIR / "data" / "state.json"
DASHBOARD_FILE = BASE_DIR / "docs" / "index.html"

TRADING_DAYS_1M = 21
TRADING_DAYS_3M = 63


def load_config() -> dict:
    with CONFIG_FILE.open(encoding="utf-8") as f:
        return json.load(f)


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        with STATE_FILE.open(encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        print("[state] state.json 損壞，視為初次執行")
        return {}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with STATE_FILE.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def assess(cfg: dict, ticker_cfg: dict, today: date):
    """回傳 (評估結果, 價格序列)。序列給看板畫圖用。"""
    series = fetch_prices.fetch(ticker_cfg)
    window = cfg["ma_window"]

    deviation = series.deviation_pct(window)
    tier = alert_logic.classify(deviation, cfg["tiers"])

    a = alert_logic.Assessment(
        name=ticker_cfg["name"],
        price=series.latest_close,
        ma=series.sma(window),
        deviation=deviation,
        latest_date=series.latest_date,
        source=series.source,
        change_1m=series.pct_change_over(TRADING_DAYS_1M),
        change_3m=series.pct_change_over(TRADING_DAYS_3M),
        stale=(today - series.latest_date).days > cfg["stale_days"],
    )

    if tier:
        a.tier_key = tier["key"]
        a.tier_name = tier["name"]
        a.tier_emoji = tier["emoji"]

    return a, series


def format_line(a: alert_logic.Assessment, ticker_cfg: dict) -> str:
    p = ticker_cfg["symbol_prefix"]
    return (
        f"{a.name}  現價 {p}{a.price:,.2f}｜MA200 {p}{a.ma:,.2f}｜"
        f"乖離 {a.deviation:+.1f}% {a.tier_emoji}"
    )


def build_message(
    decision: alert_logic.Decision,
    cfg: dict,
    today: date,
) -> str:
    by_name = {t["name"]: t for t in cfg["tickers"]}
    triggered = [a for a in decision.assessments if a.triggered]

    if triggered:
        worst = min(triggered, key=lambda a: a.deviation)
        header = f"{worst.tier_emoji} {worst.name} {worst.tier_name}訊號"
    elif decision.recovered:
        header = f"✅ {'、'.join(decision.recovered)} 已脫離警戒區"
    else:
        header = "ETF 狀態更新"

    lines = [header, ""]
    for a in decision.assessments:
        lines.append(format_line(a, by_name[a.name]))
    lines.append("")

    for a in triggered:
        detail = f"{a.name} 已跌破 200 日均線 {abs(a.deviation):.1f}%，落在{a.tier_name}。"
        moves = []
        if a.change_1m is not None:
            moves.append(f"近一個月 {a.change_1m:+.1f}%")
        if a.change_3m is not None:
            moves.append(f"近三個月 {a.change_3m:+.1f}%")
        if moves:
            detail += "（" + "、".join(moves) + "）"
        lines.append(detail)

    if triggered:
        lines.append("")
        lines.append("── 相關新聞 ──")
        seen: set[str] = set()
        for a in triggered:
            tcfg = by_name[a.name]
            is_zh = tcfg["currency"] == "TWD"
            for item in news.fetch_headlines(tcfg["news_query"], chinese=is_zh, limit=3):
                if item["link"] in seen:
                    continue
                seen.add(item["link"])
                src = f"（{item['source']}）" if item["source"] else ""
                lines.append(f"• {item['title']}{src}")
                lines.append(f"  {item['link']}")
        if len(seen) == 0:
            lines.append("（這次沒抓到相關新聞）")
        lines.append("")
        lines.append("※ 標題未經查證，請自行判讀。跌幅大不一定有單一原因，"
                     "緩步陰跌往往沒有明確的觸發事件。")

    stale = [a for a in decision.assessments if a.stale]
    if stale:
        lines.append("")
        for a in stale:
            lines.append(f"⚠️ {a.name} 最新收盤日為 {a.latest_date}，非今日資料")

    lines.append("")
    lines.append(f"資料來源：{'、'.join(sorted({a.source for a in decision.assessments}))}")
    lines.append("※ 機械式價格訊號，不是投資建議。")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="ETF 大跌警報")
    parser.add_argument("--dry-run", action="store_true",
                        help="只印結果，不發通知也不寫入狀態")
    parser.add_argument("--force", action="store_true",
                        help="無視門檻與重複抑制，強制發送一次")
    args = parser.parse_args()

    cfg = load_config()
    state = load_state()
    today = date.today()

    assessments: list[alert_logic.Assessment] = []
    series_map: dict = {}
    failures: list[str] = []

    for ticker_cfg in cfg["tickers"]:
        try:
            a, series = assess(cfg, ticker_cfg, today)
            assessments.append(a)
            series_map[a.name] = series
        except Exception as e:  # noqa: BLE001
            failures.append(f"{ticker_cfg['name']}: {e}")
            print(f"[error] {ticker_cfg['name']} 評估失敗：{e}", file=sys.stderr)

    if not assessments:
        print("[error] 所有標的都抓不到資料，這次不做任何事", file=sys.stderr)
        for f in failures:
            print(f"  {f}", file=sys.stderr)
        return 1

    for a in assessments:
        print(f"  {a.name}: 現價 {a.price:,.2f} / MA200 {a.ma:,.2f} / "
              f"乖離 {a.deviation:+.2f}% / 級別 {a.tier_name or '未觸發'} "
              f"/ 收盤日 {a.latest_date} / 來源 {a.source}")

    # 看板每次都重新產生，不管有沒有觸發通知——它的用途就是讓你隨時能查
    try:
        DASHBOARD_FILE.parent.mkdir(parents=True, exist_ok=True)
        html_out = generate_dashboard.build_html(
            assessments, series_map, cfg, datetime.now(timezone.utc)
        )
        DASHBOARD_FILE.write_text(html_out, encoding="utf-8")
        print(f"[dashboard] 已產生 {DASHBOARD_FILE.relative_to(BASE_DIR)}")
    except Exception as e:  # noqa: BLE001 — 看板失敗不該影響通知
        print(f"[dashboard] 產生失敗（不影響通知）：{e}", file=sys.stderr)

    decision = alert_logic.decide(
        assessments=assessments,
        state=state,
        tiers=cfg["tiers"],
        repeat_reminder_days=cfg["repeat_reminder_days"],
        notify_on_recovery=cfg["notify_on_recovery"],
        today=today,
    )

    print(f"[decision] {decision.reason}")

    if args.force:
        decision.should_notify = True
        print("[decision] --force 已指定，強制發送")

    if not decision.should_notify:
        if not args.dry_run:
            save_state(state)
        return 0

    message = build_message(decision, cfg, today)

    if failures:
        message += "\n\n⚠️ 部分標的抓不到資料：" + "；".join(failures)

    print("---- 訊息內容 ----")
    print(message)
    print("------------------")

    if args.dry_run:
        print("[dry-run] 不發送、不寫入狀態")
        return 0

    send_line_message(message)
    save_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
