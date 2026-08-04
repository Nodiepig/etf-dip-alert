"""
產生 GitHub Pages 看板（docs/index.html）。

刻意做成單一靜態檔案、內嵌 SVG、不依賴任何 CDN 或 JavaScript 函式庫：
資料在 Actions 執行時就已經抓好並烤進 HTML，打開網頁不需要再連任何外部服務。
這也表示就算哪天你不再跑這個排程，頁面還是打得開，只是數字停在最後一次更新。
"""
from __future__ import annotations

import html
from datetime import date, datetime

CHART_DAYS = 252          # 近一年交易日
CHART_W, CHART_H = 700, 260
PAD_L, PAD_R, PAD_T, PAD_B = 52, 14, 16, 26

# 刻度尺的範圍：+30% ~ -35%
GAUGE_HI, GAUGE_LO = 30.0, -35.0


def _ma_series(closes: list[float], window: int, days: int) -> tuple[list[float], list[float]]:
    """回傳最近 days 天的 (收盤價, 對應的移動平均)。資料不足時自動縮短。"""
    usable = min(days, len(closes) - window + 1)
    if usable < 2:
        return [], []
    prices = closes[-usable:]
    mas = []
    for i in range(len(closes) - usable, len(closes)):
        mas.append(sum(closes[i - window + 1: i + 1]) / window)
    return prices, mas


def _polyline(values: list[float], lo: float, hi: float, n: int) -> str:
    """把數值序列轉成 SVG polyline 的 points 字串。"""
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
    """價格 vs MA200 vs 觸發線的對照圖。"""
    window = cfg["ma_window"]
    buy_threshold = min(t["threshold"] for t in cfg["tiers"] if t["key"] == "buy")

    prices, mas = _ma_series(series.closes, window, CHART_DAYS)
    if not prices:
        return '<p class="nodata">資料不足，無法繪製走勢圖</p>'

    # 觸發線 = 均線 × (1 + 門檻%)，代表「跌到這條線就會通知你」
    triggers = [m * (1 + buy_threshold / 100.0) for m in mas]

    lo = min(min(prices), min(triggers))
    hi = max(max(prices), max(mas))
    span = hi - lo
    lo -= span * 0.08
    hi += span * 0.08

    n = len(prices)
    p = ticker_cfg["symbol_prefix"]

    # Y 軸刻度
    ticks = []
    for frac in (0, 0.25, 0.5, 0.75, 1.0):
        val = lo + (hi - lo) * frac
        y = PAD_T + (CHART_H - PAD_T - PAD_B) * (1 - frac)
        ticks.append(
            f'<line x1="{PAD_L}" y1="{y:.1f}" x2="{CHART_W - PAD_R}" y2="{y:.1f}" '
            f'class="grid"/>'
            f'<text x="{PAD_L - 8}" y="{y + 4:.1f}" class="ytick">{p}{val:,.0f}</text>'
        )

    # X 軸只標頭尾日期
    dates = series.dates[-n:]
    x_labels = (
        f'<text x="{PAD_L}" y="{CHART_H - 8}" class="xtick">{dates[0]}</text>'
        f'<text x="{CHART_W - PAD_R}" y="{CHART_H - 8}" class="xtick end">{dates[-1]}</text>'
    )

    return f"""<svg viewBox="0 0 {CHART_W} {CHART_H}" class="chart" role="img"
     aria-label="{html.escape(ticker_cfg['name'])} 近一年價格與 {window} 日均線對照">
  {''.join(ticks)}
  {x_labels}
  <polyline class="line-trigger" points="{_polyline(triggers, lo, hi, n)}"/>
  <polyline class="line-ma"      points="{_polyline(mas, lo, hi, n)}"/>
  <polyline class="line-price"   points="{_polyline(prices, lo, hi, n)}"/>
</svg>
<div class="legend">
  <span><i class="sw price"></i>收盤價</span>
  <span><i class="sw ma"></i>{window} 日均線</span>
  <span><i class="sw trigger"></i>進場門檻（均線 {buy_threshold:.0f}%）</span>
</div>"""


def _gauge_svg(deviation: float, cfg: dict) -> str:
    """一條橫向刻度尺，標出目前乖離率與各級門檻的相對位置。"""
    w, h = 700, 62
    bar_y, bar_h = 20, 12
    pad = 12
    inner = w - pad * 2

    def x_of(pct: float) -> float:
        pct = max(min(pct, GAUGE_HI), GAUGE_LO)
        return pad + inner * (GAUGE_HI - pct) / (GAUGE_HI - GAUGE_LO)

    marks = []
    for tier in sorted(cfg["tiers"], key=lambda t: -t["threshold"]):
        x = x_of(tier["threshold"])
        marks.append(
            f'<line x1="{x:.1f}" y1="{bar_y - 4}" x2="{x:.1f}" y2="{bar_y + bar_h + 4}" '
            f'class="mark"/>'
            f'<text x="{x:.1f}" y="{bar_y + bar_h + 20}" class="marklabel">'
            f'{tier["threshold"]:.0f}%</text>'
        )

    cur_x = x_of(deviation)
    clamped = deviation < GAUGE_LO or deviation > GAUGE_HI

    return f"""<svg viewBox="0 0 {w} {h}" class="gauge" role="img"
     aria-label="目前乖離率 {deviation:+.1f}%">
  <defs>
    <linearGradient id="g" x1="0" x2="1">
      <stop offset="0"    stop-color="#3fb950"/>
      <stop offset="0.46" stop-color="#3fb950"/>
      <stop offset="0.56" stop-color="#d29922"/>
      <stop offset="0.70" stop-color="#db6d28"/>
      <stop offset="0.85" stop-color="#f85149"/>
      <stop offset="1"    stop-color="#f85149"/>
    </linearGradient>
  </defs>
  <rect x="{pad}" y="{bar_y}" width="{inner}" height="{bar_h}" rx="6" fill="url(#g)" opacity="0.85"/>
  {''.join(marks)}
  <polygon points="{cur_x:.1f},{bar_y - 6} {cur_x - 6:.1f},{bar_y - 16} {cur_x + 6:.1f},{bar_y - 16}"
           class="needle"/>
  <text x="{cur_x:.1f}" y="{bar_y - 20}" class="needlelabel">
    {deviation:+.1f}%{'（超出刻度）' if clamped else ''}
  </text>
</svg>"""


def _card(a, series, cfg: dict, ticker_cfg: dict) -> str:
    p = ticker_cfg["symbol_prefix"]
    buy = min(t["threshold"] for t in cfg["tiers"] if t["key"] == "buy")
    trigger_price = a.ma * (1 + buy / 100.0)
    gap = (trigger_price - a.price) / a.price * 100.0

    if a.triggered:
        status = f'<span class="badge t-{a.tier_key}">{a.tier_emoji} {a.tier_name}</span>'
        gap_text = f"已在門檻內"
    else:
        status = '<span class="badge t-ok">✅ 未觸發</span>'
        gap_text = (
            f"還要再跌 <strong>{abs(gap):.1f}%</strong> 才會通知你"
            f"（跌到約 {p}{trigger_price:,.2f}）"
        )

    moves = []
    if a.change_1m is not None:
        moves.append(f'<span>近一月 <b class="{"dn" if a.change_1m < 0 else "up"}">'
                     f'{a.change_1m:+.1f}%</b></span>')
    if a.change_3m is not None:
        moves.append(f'<span>近三月 <b class="{"dn" if a.change_3m < 0 else "up"}">'
                     f'{a.change_3m:+.1f}%</b></span>')

    return f"""<section class="card">
  <div class="cardhead">
    <div>
      <h2>{html.escape(a.name)} <small>{html.escape(ticker_cfg['label'])}</small></h2>
      <div class="meta">收盤日 {a.latest_date}　·　{html.escape(a.source)}</div>
    </div>
    {status}
  </div>

  <div class="nums">
    <div><label>現價</label><b>{p}{a.price:,.2f}</b></div>
    <div><label>MA{cfg['ma_window']}</label><b>{p}{a.ma:,.2f}</b></div>
    <div><label>乖離率</label><b class="{'dn' if a.deviation < 0 else 'up'}">{a.deviation:+.2f}%</b></div>
  </div>

  <div class="moves">{''.join(moves)}</div>

  {_gauge_svg(a.deviation, cfg)}

  <p class="gap">{gap_text}</p>

  {_chart_svg(series, cfg, ticker_cfg)}
</section>"""


CSS = """
:root{--bg:#0d1117;--card:#161b22;--line:#30363d;--fg:#e6edf3;--dim:#8b949e;
      --up:#3fb950;--dn:#f85149}
*{box-sizing:border-box}
body{margin:0;padding:20px 14px 48px;background:var(--bg);color:var(--fg);
     font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans TC",sans-serif}
.wrap{max-width:760px;margin:0 auto}
h1{font-size:20px;margin:0 0 4px}
.sub{color:var(--dim);font-size:13px;margin:0 0 22px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;
      padding:18px;margin-bottom:18px}
.cardhead{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}
h2{font-size:17px;margin:0}
h2 small{color:var(--dim);font-weight:400;font-size:13px;margin-left:6px}
.meta{color:var(--dim);font-size:12px;margin-top:2px}
.badge{font-size:13px;padding:4px 10px;border-radius:999px;white-space:nowrap;
       border:1px solid var(--line)}
.t-ok{color:var(--up)} .t-watch{color:#d29922} .t-buy{color:#db6d28} .t-deep{color:var(--dn)}
.nums{display:flex;gap:26px;flex-wrap:wrap;margin:16px 0 6px}
.nums label{display:block;color:var(--dim);font-size:12px}
.nums b{font-size:21px;font-variant-numeric:tabular-nums}
.moves{display:flex;gap:18px;color:var(--dim);font-size:13px;margin-bottom:6px}
.up{color:var(--up)} .dn{color:var(--dn)}
.gap{color:var(--dim);font-size:13px;margin:2px 0 14px}
.gap strong{color:var(--fg)}
svg{width:100%;height:auto;display:block}
.gauge{margin:6px 0 2px}
.mark{stroke:var(--bg);stroke-width:2}
.marklabel{fill:var(--dim);font-size:10px;text-anchor:middle}
.needle{fill:var(--fg)}
.needlelabel{fill:var(--fg);font-size:12px;text-anchor:middle;font-weight:600}
.chart{margin-top:10px}
.grid{stroke:var(--line);stroke-width:1;opacity:.5}
.ytick{fill:var(--dim);font-size:10px;text-anchor:end}
.xtick{fill:var(--dim);font-size:10px}
.xtick.end{text-anchor:end}
.line-price{fill:none;stroke:#58a6ff;stroke-width:1.8}
.line-ma{fill:none;stroke:#8b949e;stroke-width:1.4;stroke-dasharray:5 3}
.line-trigger{fill:none;stroke:#db6d28;stroke-width:1.2;stroke-dasharray:2 4}
.legend{display:flex;gap:16px;flex-wrap:wrap;color:var(--dim);font-size:12px;margin-top:6px}
.legend i{display:inline-block;width:14px;height:2px;vertical-align:middle;margin-right:5px}
.sw.price{background:#58a6ff} .sw.ma{background:#8b949e} .sw.trigger{background:#db6d28}
.nodata{color:var(--dim);font-size:13px}
footer{color:var(--dim);font-size:12px;line-height:1.8;margin-top:26px}
footer a{color:#58a6ff}
@media(max-width:520px){
  .nums{gap:16px}.nums b{font-size:18px}
  .cardhead{flex-direction:column}
}
"""


def build_html(assessments, series_map, cfg, generated_at: datetime) -> str:
    by_name = {t["name"]: t for t in cfg["tickers"]}
    cards = "\n".join(
        _card(a, series_map[a.name], cfg, by_name[a.name])
        for a in assessments
        if a.name in series_map
    )

    tier_rows = "、".join(
        f'{t["emoji"]} {t["name"]} {t["threshold"]:.0f}%'
        for t in sorted(cfg["tiers"], key=lambda t: -t["threshold"])
    )

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
  <p class="sub">更新於 {generated_at:%Y-%m-%d %H:%M} UTC　·　門檻：{tier_rows}</p>
  {cards}
  <footer>
    每個交易日自動更新。跌破門檻時會發 LINE 通知，沒觸發的日子不會打擾你。<br>
    走勢圖的橘色虛線是進場門檻（均線 −15%），價格跌到那條線以下就會收到通知。<br>
    <br>
    ※ 200 日均線只描述「現價相對過去 200 個交易日平均偏離多少」，不預測底部。
    歷史上跌破均線 20% 之後繼續跌到 40% 的情況並不罕見。這是機械式價格訊號，不是投資建議。
  </footer>
</div>
</body>
</html>
"""
