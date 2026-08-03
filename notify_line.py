"""
LINE Messaging API 推播通知。

沿用 price-tracker 專案裡同一組設定，環境變數名稱刻意保持一致：
    LINE_CHANNEL_ACCESS_TOKEN
    LINE_USER_ID

在 GitHub Actions 上這兩個值來自 repository secrets，不會出現在程式碼裡。
"""
from __future__ import annotations

import os

import requests

LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"
MAX_LEN = 4900  # LINE 單則文字上限 5000 字，留一點餘裕


def send_line_message(text: str) -> bool:
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
    user_id = os.environ.get("LINE_USER_ID")

    if not token or not user_id:
        print("[notify_line] 沒有設定 LINE_CHANNEL_ACCESS_TOKEN 或 LINE_USER_ID，跳過通知")
        print("---- 以下是原本要發送的內容 ----")
        print(text)
        return False

    if len(text) > MAX_LEN:
        text = text[: MAX_LEN - 20] + "\n…（訊息過長已截斷）"

    try:
        resp = requests.post(
            LINE_PUSH_URL,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
            json={"to": user_id, "messages": [{"type": "text", "text": text}]},
            timeout=15,
        )
    except requests.RequestException as e:
        print(f"[notify_line] 發送時發生網路錯誤：{e}")
        return False

    if resp.status_code == 200:
        print("[notify_line] 已發送 LINE 通知")
        return True

    print(f"[notify_line] 發送失敗：{resp.status_code} {resp.text}")
    return False


if __name__ == "__main__":
    ok = send_line_message("這是一則測試通知：ETF 大跌警報設定成功！")
    print("發送成功" if ok else "發送失敗，請檢查環境變數")
