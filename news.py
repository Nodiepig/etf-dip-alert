"""
觸發時抓幾則相關新聞標題，讓通知不只是一個冷冰冰的數字。

刻意保持簡單：只抓 RSS 標題和連結，不做任何摘要或因果推論。
自動化腳本沒有判斷力去區分「真正的觸發事件」和「財經媒體事後編的故事」，
硬要生成解釋只會產出看似有理、實則誤導的內容。標題和連結交給你自己判讀，
比一段假裝知道原因的文字誠實得多。
"""
from __future__ import annotations

import re
import urllib.parse
import xml.etree.ElementTree as ET

import requests

GOOGLE_NEWS_RSS = (
    "https://news.google.com/rss/search"
    "?q={query}&hl={hl}&gl={gl}&ceid={ceid}"
)
TIMEOUT = 15
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) etf-dip-alert/1.0"

TAG_RE = re.compile(r"<[^>]+>")


def _clean(text: str) -> str:
    return TAG_RE.sub("", text or "").strip()


def fetch_headlines(query: str, chinese: bool = False, limit: int = 3) -> list[dict]:
    """
    回傳 [{'title': ..., 'link': ..., 'source': ...}, ...]。
    抓不到就回空陣列——新聞是加分項，不該讓整個通知失敗。
    """
    if chinese:
        params = {"hl": "zh-TW", "gl": "TW", "ceid": "TW:zh-Hant"}
    else:
        params = {"hl": "en-US", "gl": "US", "ceid": "US:en"}

    url = GOOGLE_NEWS_RSS.format(query=urllib.parse.quote(query), **params)

    try:
        resp = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except Exception as e:  # noqa: BLE001 — 新聞失敗不影響主流程
        print(f"[news] 抓取失敗（不影響通知）：{e}")
        return []

    items: list[dict] = []
    for item in root.iterfind(".//item"):
        title = _clean(item.findtext("title", ""))
        link = (item.findtext("link") or "").strip()
        source_el = item.find("source")
        source = _clean(source_el.text) if source_el is not None else ""
        if title and link:
            items.append({"title": title, "link": link, "source": source})
        if len(items) >= limit:
            break

    return items
