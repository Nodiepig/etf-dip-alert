"""
判斷「要不要通知」的邏輯，跟抓價與發送完全分離，方便單獨測試。

核心問題不是「現在跌多少」，而是「這件事值不值得再打擾你一次」。
大盤跌破 200 日均線 15% 之後，這個狀態通常會持續好幾週。如果每天都推播，
你會在第三天就把通知關掉——那就等於這個工具失效了。

所以規則是：
    1. 第一次進入某個級別 → 通知
    2. 從較輕的級別升到較重的級別（例如 🟡 → 🟠）→ 通知
    3. 已經在同一級別內 → 預設安靜，但每 N 天提醒一次（可設定為 0 = 完全不重複）
    4. 級別下降（例如 🟠 → 🟡）→ 不通知，避免反覆震盪造成來回洗版
    5. 完全脫離警戒（回到均線 -10% 以上）→ 通知一次，然後歸零
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta


@dataclass
class Assessment:
    """單一標的的評估結果。"""

    name: str
    price: float
    ma: float
    deviation: float          # 乖離率 %，負值代表低於均線
    latest_date: date
    source: str
    tier_key: str | None = None      # None 代表未觸發任何級別
    tier_name: str = ""
    tier_emoji: str = "✅"
    change_1m: float | None = None
    change_3m: float | None = None
    stale: bool = False

    @property
    def triggered(self) -> bool:
        return self.tier_key is not None


@dataclass
class Decision:
    """整體通知決策。"""

    should_notify: bool
    reason: str
    assessments: list[Assessment] = field(default_factory=list)
    recovered: list[str] = field(default_factory=list)


def classify(deviation: float, tiers: list[dict]) -> dict | None:
    """
    回傳乖離率對應到的最嚴重級別。tiers 的 threshold 都是負數。
    不假設 config 裡的順序，一律由最嚴重（threshold 最小）開始比對。
    """
    for tier in sorted(tiers, key=lambda t: t["threshold"]):
        if deviation <= tier["threshold"]:
            return tier
    return None


def _tier_rank(tier_key: str | None, tiers: list[dict]) -> int:
    """把級別轉成可比較的嚴重度分數，未觸發 = 0，越嚴重數字越大。"""
    if tier_key is None:
        return 0
    order = [t["key"] for t in sorted(tiers, key=lambda t: -t["threshold"])]
    return order.index(tier_key) + 1 if tier_key in order else 0


def decide(
    assessments: list[Assessment],
    state: dict,
    tiers: list[dict],
    repeat_reminder_days: int,
    notify_on_recovery: bool,
    today: date,
) -> Decision:
    """
    比對本次評估與上次狀態，決定要不要推播。
    會就地更新 state（呼叫端負責寫回檔案）。
    """
    reasons: list[str] = []
    recovered: list[str] = []
    per_ticker = state.setdefault("tickers", {})

    for a in assessments:
        prev = per_ticker.get(a.name, {})
        prev_tier = prev.get("tier")
        prev_notified_raw = prev.get("last_notified")
        prev_notified = (
            date.fromisoformat(prev_notified_raw) if prev_notified_raw else None
        )

        cur_rank = _tier_rank(a.tier_key, tiers)
        prev_rank = _tier_rank(prev_tier, tiers)

        if cur_rank > prev_rank:
            # 新觸發，或是惡化到更嚴重的級別
            reasons.append(f"{a.name} 進入{a.tier_name}（乖離 {a.deviation:+.1f}%）")
            prev = {"tier": a.tier_key, "last_notified": today.isoformat()}

        elif cur_rank > 0 and cur_rank == prev_rank:
            # 維持同一級別：預設安靜，只在超過間隔時提醒一次
            due = (
                repeat_reminder_days > 0
                and prev_notified is not None
                and today - prev_notified >= timedelta(days=repeat_reminder_days)
            )
            if due:
                reasons.append(
                    f"{a.name} 仍在{a.tier_name}（乖離 {a.deviation:+.1f}%，"
                    f"已持續 {(today - prev_notified).days} 天）"
                )
                prev = {"tier": a.tier_key, "last_notified": today.isoformat()}
            else:
                prev = {
                    "tier": a.tier_key,
                    "last_notified": prev_notified.isoformat() if prev_notified else None,
                }

        elif cur_rank == 0 and prev_rank > 0:
            # 完全脫離警戒
            if notify_on_recovery:
                recovered.append(a.name)
                reasons.append(f"{a.name} 已回到均線 -10% 以內")
            prev = {"tier": None, "last_notified": None}

        else:
            # 級別下降但仍在警戒中，或本來就沒事：安靜
            prev = {
                "tier": a.tier_key,
                "last_notified": prev_notified.isoformat() if prev_notified else None,
            }

        per_ticker[a.name] = prev

    state["last_run"] = today.isoformat()

    return Decision(
        should_notify=bool(reasons),
        reason="；".join(reasons) if reasons else "無變化，不通知",
        assessments=assessments,
        recovered=recovered,
    )
