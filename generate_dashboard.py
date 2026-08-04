"""
產生 GitHub Pages 看板（docs/index.html）。

刻意做成單一靜態檔案、內嵌 SVG、不依賴任何 CDN 或 JavaScript 函式庫：
資料在 Actions 執行時就已經抓好並烤進 HTML，打開網頁不需要再連任何外部服務。
這也表示就算哪天你不再跑這個排程，頁面還是打得開，只是數字停在最後一次更新。
"""
from __future__ import annotations

import html
from datetime import datetime

CHART_DAYS = 252
CHART_W, CHART_H = 700, 250
PAD_L, PAD_R, PAD_T, PAD_B = 54, 14, 14, 24

GAUGE_HI, GAUGE_LO = 30.0, -40.0
GAUGE_W, GAUGE_H = 700, 70
# BAR_Y 必須留出指針標籤的高度：標籤基線在 BAR_Y-18，字高 11px，
# 所以 BAR_Y 至少要 32 才不會讓文字被 viewBox 上緣切掉。
BAR_Y, BAR_H, GPAD = 34, 11, 12
LABEL_MARGIN = 32   # 指針標籤的水平安全邊界，避免貼邊時左右被切


def _window(closes: list[float], window: int, days: int):
    """回傳最近 days 天的 (收盤價, 移動平均, 滾動 52 週高)。資料不足時自動縮短。"""
    usable = min(days, len(closes) - window + 1)
    if usable < 2:
        return [], [], []
    prices = closes[-usable:]
    mas, highs = [], []
    for i in range(len(closes) - usable, len(closes)):
        mas.append(sum(closes[i - window + 1: i + 1]) / window)
        highs.append(max(closes[max(0, i - 251): i + 1]))
    return prices, mas, highs


def _polyline(values: list[float], lo: float, hi: float, n: int) -> str:
    if hi <= lo:
        hi = lo + 1
    plot_w = CHART_W - PAD_L - PAD_R
    plot_h = CHART_H - PAD_T - PAD_B
    pts = []
    for i, v in enumerate(values):
        x = PAD_L + (plot_w * i / max(n - 1, 1))
        y = PAD_T + plot_h * (1 - (v - lo) / (hi - lo))
        pts.append(f"{x:.1f},{y:.1f}")
    return " ".join(pts)


def _chart_svg(series, cfg: dict, ticker_cfg: dict) -> str:
    # 畫 level 1 的門檻線——那是你第一次收到通知的價位，比 level 2 更有參考價值。
    # 三個級別的位置在上方的刻度尺已經看得到了。
    ma_win = cfg["ma_window"]
    ma_th = min(t["threshold"] for t in cfg["ma_tiers"] if t["level"] == 1)
    dd_th = min(t["threshold"] for t in cfg["drawdown_tiers"] if t["level"] == 1)

    prices, mas, highs = _window(series.closes, ma_win, CHART_DAYS)
    if not prices:
        return '<p class="nodata">資料不足，無法繪製走勢圖</p>'

    ma_trig = [m * (1 + ma_th / 100.0) for m in mas]
    dd_trig = [h * (1 + dd_th / 100.0) for h in highs]

    lo = min(min(prices), min(ma_trig), min(dd_trig))
    hi = max(max(prices), max(mas), max(highs))
    span = hi - lo
    lo -= span * 0.07
    hi += span * 0.07

    n = len(prices)
    p = ticker_cfg["symbol_prefix"]

    ticks = []
    for frac in (0, 0.25, 0.5, 0.75, 1.0):
        val = lo + (hi - lo) * frac
        y = PAD_T + (CHART_H - PAD_T - PAD_B) * (1 - frac)
        ticks.append(
            f'<line x1="{PAD_L}" y1="{y:.1f}" x2="{CHART_W - PAD_R}" y2="{y:.1f}" class="grid"/>'
            f'<text x="{PAD_L - 8}" y="{y + 4:.1f}" class="ytick">{p}{val:,.0f}</text>'
        )

    dates = series.dates[-n:]
    return f"""<svg viewBox="0 0 {CHART_W} {CHART_H}" class="chart" role="img"
     aria-label="{html.escape(ticker_cfg['name'])} 近一年走勢與兩條觸發線">
  {''.join(ticks)}
  <text x="{PAD_L}" y="{CHART_H - 6}" class="xtick">{dates[0]}</text>
  <text x="{CHART_W - PAD_R}" y="{CHART_H - 6}" class="xtick end">{dates[-1]}</text>
  <polyline class="l-ddtrig" points="{_polyline(dd_trig, lo, hi, n)}"/>
  <polyline class="l-matrig" points="{_polyline(ma_trig, lo, hi, n)}"/>
  <polyline class="l-high"   points="{_polyline(highs, lo, hi, n)}"/>
  <polyline class="l-ma"     points="{_polyline(mas, lo, hi, n)}"/>
  <polyline class="l-price"  points="{_polyline(prices, lo, hi, n)}"/>
</svg>
<div class="legend">
  <span><i class="sw price"></i>收盤價</span>
  <span><i class="sw high"></i>52 週高</span>
  <span><i class="sw ma"></i>MA{ma_win}</span>
  <span><i class="sw ddtrig"></i>回撤通知線 {dd_th:.0f}%</span>
  <span><i class="sw matrig"></i>乖離通知線 {ma_th:.0f}%</span>
</div>"""


def _gauge_svg(value: float, tiers: list[dict], label: str) -> str:
    """一條橫向刻度尺，標出目前數值與各級門檻的相對位置。"""
    def x_of(pct: float) -> float:
        pct = max(min(pct, GAUGE_HI), GAUGE_LO)
        return GPAD + (GAUGE_W - GPAD * 2) * (GAUGE_HI - pct) / (GAUGE_HI - GAUGE_LO)

    marks = []
    for t in sorted(tiers, key=lambda t: -t["threshold"]):
        x = x_of(t["threshold"])
        marks.append(
            f'<line x1="{x:.1f}" y1="{BAR_Y - 3}" x2="{x:.1f}" y2="{BAR_Y + BAR_H + 3}" class="mark"/>'
            f'<text x="{x:.1f}" y="{BAR_Y + BAR_H + 17}" class="marklabel">{t["threshold"]:.0f}</text>'
        )

    cx = x_of(value)
    # 標籤置中對齊指針，但不能貼到左右邊緣，否則文字會被切掉
    label_x = max(LABEL_MARGIN, min(cx, GAUGE_W - LABEL_MARGIN))
    clamped = value < GAUGE_LO or value > GAUGE_HI
    gid = f"g{abs(hash(label)) % 10000}"

    return f"""<div class="gaugewrap">
<div class="gaugelabel">{html.escape(label)}</div>
<svg viewBox="0 0 {GAUGE_W} {GAUGE_H}" class="gauge" role="img"
     aria-label="{html.escape(label)} {value:+.1f}%">
  <defs><linearGradient id="{gid}" x1="0" x2="1">
    <stop offset="0" stop-color="#3fb950"/><stop offset="0.42" stop-color="#3fb950"/>
    <stop offset="0.55" stop-color="#d29922"/><stop offset="0.72" stop-color="#db6d28"/>
    <stop offset="0.88" stop-color="#f85149"/><stop offset="1" stop-color="#f85149"/>
  </linearGradient></defs>
  <rect x="{GPAD}" y="{BAR_Y}" width="{GAUGE_W - GPAD * 2}" height="{BAR_H}" rx="5"
        fill="url(#{gid})" opacity="0.8"/>
  {''.join(marks)}
  <polygon points="{cx:.1f},{BAR_Y - 5} {cx - 5:.1f},{BAR_Y - 14} {cx + 5:.1f},{BAR_Y - 14}"
           class="needle"/>
  <text x="{label_x:.1f}" y="{BAR_Y - 18}" class="needlelabel">{value:+.1f}%{'!' if clamped else ''}</text>
</svg></div>"""


def _card(a, series, cfg: dict, ticker_cfg: dict) -> str:
    p = ticker_cfg["symbol_prefix"]
    # 用 level 1 算「距離第一則通知還有多遠」——那才是你真正會先碰到的線
    ma_th = min(t["threshold"] for t in cfg["ma_tiers"] if t["level"] == 1)
    dd_th = min(t["threshold"] for t in cfg["drawdown_tiers"] if t["level"] == 1)

    ma_trigger = a.ma * (1 + ma_th / 100.0)
    dd_trigger = a.high_52w * (1 + dd_th / 100.0)
    nearest = max(ma_trigger, dd_trigger)   # 兩條線中比較高的那條會先被碰到
    which = "回撤" if dd_trigger >= ma_trigger else "乖離"
    gap = (nearest - a.price) / a.price * 100.0

    if a.triggered:
        gap_text = f"目前狀態：<strong>{a.status_text}</strong>"
    else:
        gap_text = (f"再跌 <strong>{abs(gap):.1f}%</strong>（到約 {p}{nearest:,.2f}）"
                    f"就會收到第一則通知，由{which}指標觸發")

    badge_cls = f"lv{a.level}" if a.triggered else "lv0"
    refmark = '<span class="ref">僅供參考</span>' if not a.notify else ""

    moves = []
    for lbl, v in (("近一月", a.change_1m), ("近三月", a.change_3m)):
        if v is not None:
            moves.append(f'<span>{lbl} <b class="{"dn" if v < 0 else "up"}">{v:+.1f}%</b></span>')

    return f"""<section class="card">
  <div class="cardhead">
    <div>
      <h2>{html.escape(a.name)} {refmark}<small>{html.escape(ticker_cfg['label'])}</small></h2>
      <div class="meta">收盤日 {a.latest_date}　·　{html.escape(a.source)}</div>
    </div>
    <span class="badge {badge_cls}">{a.emoji} {a.status_text}</span>
  </div>

  <div class="nums">
    <div><label>現價</label><b>{p}{a.price:,.2f}</b></div>
    <div><label>52 週高</label><b>{p}{a.high_52w:,.2f}</b></div>
    <div><label>回撤</label><b class="{'dn' if a.drawdown < 0 else 'up'}">{a.drawdown:+.2f}%</b></div>
    <div><label>MA{cfg['ma_window']}</label><b>{p}{a.ma:,.2f}</b></div>
    <div><label>乖離</label><b class="{'dn' if a.deviation < 0 else 'up'}">{a.deviation:+.2f}%</b></div>
  </div>

  <div class="moves">{''.join(moves)}</div>

  {_gauge_svg(a.drawdown, cfg["drawdown_tiers"], f"{a.name} 距 52 週高點回撤")}
  {_gauge_svg(a.deviation, cfg["ma_tiers"], f"{a.name} 相對 MA{cfg['ma_window']} 乖離")}

  <p class="gap">{gap_text}</p>
  {_chart_svg(series, cfg, ticker_cfg)}
</section>"""


CSS = """
:root{--bg:#0d1117;--card:#161b22;--line:#30363d;--fg:#e6edf3;--dim:#8b949e;
      --up:#3fb950;--dn:#f85149}
*{box-sizing:border-box}
body{margin:0;padding:20px 14px 48px;background:var(--bg);color:var(--fg);
     font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans TC",sans-serif}
.wrap{max-width:780px;margin:0 auto}
h1{font-size:20px;margin:0 0 4px}
.sub{color:var(--dim);font-size:13px;margin:0 0 20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;
      padding:18px;margin-bottom:18px}
.cardhead{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}
h2{font-size:17px;margin:0}
h2 small{color:var(--dim);font-weight:400;font-size:12px;margin-left:8px}
.ref{font-size:11px;color:var(--dim);border:1px solid var(--line);border-radius:4px;
     padding:1px 5px;margin-left:6px;vertical-align:middle}
.meta{color:var(--dim);font-size:12px;margin-top:2px}
.badge{font-size:13px;padding:4px 10px;border-radius:999px;white-space:nowrap;
       border:1px solid var(--line)}
.lv0{color:var(--up)} .lv1{color:#d29922} .lv2{color:#db6d28} .lv3{color:var(--dn)}
.nums{display:flex;gap:22px;flex-wrap:wrap;margin:16px 0 4px}
.nums label{display:block;color:var(--dim);font-size:12px}
.nums b{font-size:19px;font-variant-numeric:tabular-nums}
.moves{display:flex;gap:18px;color:var(--dim);font-size:13px;margin-bottom:10px}
.up{color:var(--up)} .dn{color:var(--dn)}
.gap{color:var(--dim);font-size:13px;margin:8px 0 12px}
.gap strong{color:var(--fg)}
svg{width:100%;height:auto;display:block}
.gaugewrap{margin:4px 0}
.gaugelabel{color:var(--dim);font-size:11px;margin-bottom:-4px}
.mark{stroke:var(--card);stroke-width:2}
.marklabel{fill:var(--dim);font-size:9px;text-anchor:middle}
.needle{fill:var(--fg)}
.needlelabel{fill:var(--fg);font-size:11px;text-anchor:middle;font-weight:600}
.chart{margin-top:8px}
.grid{stroke:var(--line);stroke-width:1;opacity:.5}
.ytick{fill:var(--dim);font-size:10px;text-anchor:end}
.xtick{fill:var(--dim);font-size:10px}
.xtick.end{text-anchor:end}
.l-price{fill:none;stroke:#58a6ff;stroke-width:1.8}
.l-high{fill:none;stroke:#3fb950;stroke-width:1.1;opacity:.75}
.l-ma{fill:none;stroke:#8b949e;stroke-width:1.3;stroke-dasharray:5 3}
.l-ddtrig{fill:none;stroke:#f0883e;stroke-width:1.1;stroke-dasharray:2 3}
.l-matrig{fill:none;stroke:#db6d28;stroke-width:1.1;stroke-dasharray:6 4;opacity:.8}
.legend{display:flex;gap:14px;flex-wrap:wrap;color:var(--dim);font-size:11px;margin-top:6px}
.legend i{display:inline-block;width:13px;height:2px;vertical-align:middle;margin-right:4px}
.sw.price{background:#58a6ff} .sw.high{background:#3fb950} .sw.ma{background:#8b949e}
.sw.ddtrig{background:#f0883e} .sw.matrig{background:#db6d28}
.nodata{color:var(--dim);font-size:13px}
footer{color:var(--dim);font-size:12px;line-height:1.8;margin-top:26px}
@media(max-width:520px){
  .nums{gap:14px}.nums b{font-size:17px}
  .cardhead{flex-direction:column}
}
"""


def build_html(assessments, series_map, cfg, generated_at: datetime) -> str:
    by_name = {t["name"]: t for t in cfg["tickers"]}
    cards = "\n".join(
        _card(a, series_map[a.name], cfg, by_name[a.name])
        for a in assessments if a.name in series_map
    )

    dd = "／".join(f'{t["emoji"]}{t["threshold"]:.0f}%' for t in
                   sorted(cfg["drawdown_tiers"], key=lambda t: -t["threshold"]))
    ma = "／".join(f'{t["emoji"]}{t["threshold"]:.0f}%' for t in
                   sorted(cfg["ma_tiers"], key=lambda t: -t["threshold"]))

    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>ETF 大跌警報</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
  <h1>ETF 大跌警報</h1>
  <p class="sub">更新於 {generated_at:%Y-%m-%d %H:%M} UTC　·
     回撤門檻 {dd}　·　乖離門檻 {ma}</p>
  {cards}
  <footer>
    每個交易日自動更新。<b>兩個指標任一達標就發 LINE 通知</b>，沒觸發的日子不會打擾你。<br>
    <br>
    <b>回撤</b>看的是「從 52 週最高點跌下來多少」，貼近直覺，急跌陰跌都抓得到。<br>
    <b>乖離</b>看的是「比過去 200 個交易日的平均便宜多少」，基準會自己跟著市場走。<br>
    兩者互補：2026 年 7 月台股從高點跌 18%，回撤指標會亮燈，
    但因為前一年漲太多、均線基準很低，乖離指標完全沒反應。<br>
    <br>
    ※ 這兩個指標都只描述現況，不預測底部。歷史上跌破 20% 之後繼續跌到 40% 的情況並不罕見。
    機械式價格訊號，不是投資建議。
  </footer>
</div>
</body>
</html>
"""
