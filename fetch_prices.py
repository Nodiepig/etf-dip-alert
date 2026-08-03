"""
抓取 ETF 的日線收盤價。

設計原則：寧可抓不到而報錯，也不要回傳可疑的數字。
價格資料錯了會直接導致誤判進場時機，所以每一層都有明確的合理性檢查。

資料來源優先順序：
    1. Stooq CSV  — 免金鑰、純 requests、格式穩定
    2. yfinance   — 備援，涵蓋率較好（尤其台股）

兩個都失敗就丟 FetchError，讓上層決定怎麼處理（不會回傳半殘的資料）。
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime

import requests

STOOQ_URL = "https://stooq.com/q/d/l/?s={symbol}&i=d"
TIMEOUT = 20
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) etf-dip-alert/1.0"


class FetchError(Exception):
    """所有資料來源都失敗時丟出。"""


@dataclass
class PriceSeries:
    """一檔標的的日線收盤價序列（依日期由舊到新排序）。"""

    name: str
    dates: list[date]
    closes: list[float]
    source: str

    @property
    def latest_close(self) -> float:
        return self.closes[-1]

    @property
    def latest_date(self) -> date:
        return self.dates[-1]

    def sma(self, window: int) -> float:
        if len(self.closes) < window:
            raise FetchError(
                f"{self.name}: 只有 {len(self.closes)} 筆日線資料，"
                f"不足以計算 {window} 日均線"
            )
        return sum(self.closes[-window:]) / window

    def deviation_pct(self, window: int) -> float:
        """現價相對 N 日均線的乖離率（%）。負值代表低於均線。"""
        ma = self.sma(window)
        return (self.latest_close - ma) / ma * 100.0

    def pct_change_over(self, trading_days: int) -> float | None:
        """回溯 N 個交易日的漲跌幅（%）。資料不足時回傳 None。"""
        if len(self.closes) <= trading_days:
            return None
        past = self.closes[-1 - trading_days]
        if past <= 0:
            return None
        return (self.closes[-1] - past) / past * 100.0


def _sanity_check(name: str, dates: list[date], closes: list[float]) -> None:
    """
    擋掉明顯壞掉的資料。這些檢查看起來瑣碎，但抓價工具最常見的失敗模式
    不是「抓不到」，而是「抓到看起來像數字的垃圾」——例如網站改版後
    解析到別的欄位、或是回傳了空值被當成 0。
    """
    if not closes:
        raise FetchError(f"{name}: 沒有取得任何價格資料")

    if any(c <= 0 for c in closes):
        raise FetchError(f"{name}: 價格序列中含有 0 或負數，資料不可信")

    if len(dates) != len(closes):
        raise FetchError(f"{name}: 日期與價格筆數不一致")

    if dates != sorted(dates):
        raise FetchError(f"{name}: 日期未依序排列")

    # 單日跳動超過 50% 幾乎一定是資料問題（分割除權未還原、或解析錯欄位）。
    # 真實的單日暴跌史上最極端約 -22%（1987 黑色星期一）。
    for i in range(1, len(closes)):
        prev, cur = closes[i - 1], closes[i]
        if abs(cur - prev) / prev > 0.5:
            raise FetchError(
                f"{name}: {dates[i]} 相對前一日跳動 "
                f"{(cur - prev) / prev * 100:+.1f}%，超出合理範圍，疑似資料錯誤"
            )


def _from_stooq(cfg: dict) -> PriceSeries:
    symbol = cfg["stooq"]
    url = STOOQ_URL.format(symbol=symbol)
    resp = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()

    text = resp.text.strip()
    # Stooq 查不到代號時會回一行純文字（例如 "No data"），不是 CSV
    if not text or "Date" not in text.splitlines()[0]:
        raise FetchError(f"{cfg['name']}: stooq 沒有回傳有效的 CSV（代號 {symbol}）")

    dates: list[date] = []
    closes: list[float] = []
    for row in csv.DictReader(io.StringIO(text)):
        raw_close = (row.get("Close") or "").strip()
        raw_date = (row.get("Date") or "").strip()
        if not raw_close or not raw_date or raw_close.lower() in ("n/a", "null"):
            continue
        try:
            closes.append(float(raw_close))
            dates.append(datetime.strptime(raw_date, "%Y-%m-%d").date())
        except ValueError:
            continue

    _sanity_check(cfg["name"], dates, closes)
    return PriceSeries(cfg["name"], dates, closes, source="Stooq")


def _from_yfinance(cfg: dict) -> PriceSeries:
    try:
        import yfinance
    except ImportError as e:
        raise FetchError(f"{cfg['name']}: 沒有安裝 yfinance，無法使用備援來源") from e

    ticker = yfinance.Ticker(cfg["yahoo"])
    hist = ticker.history(period="2y", interval="1d", auto_adjust=False)

    if hist is None or hist.empty:
        raise FetchError(f"{cfg['name']}: yfinance 沒有回傳資料（代號 {cfg['yahoo']}）")

    dates = [d.date() if hasattr(d, "date") else d for d in hist.index]
    closes = [float(c) for c in hist["Close"].tolist()]

    # yfinance 偶爾會在序列尾端塞 NaN（當日盤中尚未有收盤價）
    cleaned = [(d, c) for d, c in zip(dates, closes) if c == c and c > 0]
    if not cleaned:
        raise FetchError(f"{cfg['name']}: yfinance 回傳的資料全是空值")
    dates = [d for d, _ in cleaned]
    closes = [c for _, c in cleaned]

    _sanity_check(cfg["name"], dates, closes)
    return PriceSeries(cfg["name"], dates, closes, source="Yahoo Finance")


def fetch(cfg: dict, log=print) -> PriceSeries:
    """依序嘗試各資料來源，全部失敗才丟 FetchError。"""
    errors: list[str] = []

    for loader in (_from_stooq, _from_yfinance):
        try:
            series = loader(cfg)
            log(f"[fetch] {cfg['name']}: 由 {series.source} 取得 "
                f"{len(series.closes)} 筆資料，最新 {series.latest_date}")
            return series
        except Exception as e:  # noqa: BLE001 — 任何來源失敗都往下一個試
            errors.append(f"{loader.__name__}: {e}")

    raise FetchError(
        f"{cfg['name']} 所有資料來源都失敗：\n  " + "\n  ".join(errors)
    )
