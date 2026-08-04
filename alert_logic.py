"""
判斷「要不要通知」的邏輯，跟抓價與發送完全分離，方便單獨測試。

## 兩個指標

**均線乖離**：現價 vs 200 日均線。抓「相對長期平均變便宜」。
對急跌敏感，但對陰跌遲鈍——價格慢慢跌，均線跟著往下，乖離率可能永遠到不了門檻。

**高點回撤**：現價 vs 52 週最高收盤價。這是直覺上的「從高點跌下來多少」，
補上均線的落後性。市場慣例是 −10% 叫修正、−20% 叫熊市。

兩個指標各自分成 level 1/2/3 三級，**取較嚴重者**作為整體級別，任一達標就通知。
兩者互補而非重複：2026 年 7 月台股從高點跌 18%，回撤指標會亮燈，
但因為前一年漲太多、均線基準很低，乖離指標完全沒反應。

## 通知抑制

核心問題不是「現在跌多少」，而是「這件事值不值得再打擾你一次」。
跌破門檻的狀態通常會持續好幾週，每天推播的話你第三天就會靜音，工具等於失效。

    1. 第一次進入某個級別 → 通知
    2. 惡化到更嚴重的級別 → 通知
    3. 已經在同一級別內 → 預設安靜，每 N 天提醒一次（設 0 = 完全不重複）
    4. 級別下降 → 不通知，避免在門檻邊緣來回震盪造成洗版
    5. 完全脫離警戒 → 通知一次，然後歸零
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta


@dataclass
class Assessment:
    """單一標的的評估結果。"""

    name: str
    price: float
    latest_date: date
    source: str
    notify: bool = True

    # 指標一：均線乖離
    ma: float = 0.0
    deviation: float = 0.0
    ma_tier: dict | None = None

    # 指標二：高點回撤
    high_52w: float = 0.0
    drawdown: float = 0.0
    dd_tier: dict | None = None

    # 用「放寬後的門檻」重新判定的級別，只在退出警戒時使用（見下方遲滯說明）
    hold_ma_tier: dict | None = None
    hold_dd_tier: dict | None = None

    change_1m: float | None = None
    change_3m: float | None = None
    stale: bool = False

    @property
    def level(self) -> int:
        """整體嚴重度，取兩個指標中較嚴重者。0 = 未觸發。"""
        return max(
            self.ma_tier["level"] if self.ma_tier else 0,
            self.dd_tier["level"] if self.dd_tier else 0,
        )

    @property
    def hold_level(self) -> int:
        """
        放寬門檻後的級別，必定 >= level。

        用途是遲滯（hysteresis）：已經在警戒中時，要「明顯」回升才算脫離。
        沒有這個機制的話，價格在 -10% 門檻附近震盪會導致
        觸發 → 回到 -9% 解除 → 又掉到 -10.3% 再觸發 → 反覆通知。
        回測顯示這在真實資料上會讓同一波行情產生近十次通知。
        """
        return max(
            self.hold_ma_tier["level"] if self.hold_ma_tier else 0,
            self.hold_dd_tier["level"] if self.hold_dd_tier else 0,
        )

    @property
    def triggered(self) -> bool:
        return self.level > 0

    @property
    def worst_tier(self) -> dict | None:
        """觸發級別較高的那個指標；同級時以回撤優先（比較貼近直覺）。"""
        cands = [t for t in (self.dd_tier, self.ma_tier) if t]
        return max(cands, key=lambda t: t["level"]) if cands else None

    @property
    def emoji(self) -> str:
        t = self.worst_tier
        return t["emoji"] if t else "✅"

    @property
    def status_text(self) -> str:
        """例如「熊市 + 進場區」或「修正」或「未觸發」。"""
        parts = []
        if self.dd_tier:
            parts.append(self.dd_tier["name"])
        if self.ma_tier:
            parts.append(self.ma_tier["name"])
        return " + ".join(parts) if parts else "未觸發"


@dataclass
class Decision:
    """整體通知決策。"""

    should_notify: bool
    reason: str
    assessments: list[Assessment] = field(default_factory=list)
    recovered: list[str] = field(default_factory=list)


def classify(value: float, tiers: list[dict], buffer: float = 0.0) -> dict | None:
    """
    回傳數值對應到的最嚴重級別。threshold 都是負數。
    不假設 config 裡的順序，一律由最嚴重（threshold 最小）開始比對。

    buffer 會放寬門檻（例如 buffer=3 時，-10% 的門檻變成 -7% 就算觸發），
    用於遲滯判定——讓「退出警戒」比「進入警戒」更難，避免邊緣震盪反覆通知。
    """
    for tier in sorted(tiers, key=lambda t: t["threshold"]):
        if value <= tier["threshold"] + buffer:
            return tier
    return None


def decide(
    assessments: list[Assessment],
    state: dict,
    repeat_reminder_days: int,
    notify_on_recovery: bool,
    today: date,
) -> Decision:
    """
    比對本次評估與上次狀態，決定要不要推播。
    會就地更新 state（呼叫端負責寫回檔案）。
    notify=False 的標的照樣記錄狀態，但不列入通知理由。
    """
    reasons: list[str] = []
    recovered: list[str] = []
    per_ticker = state.setdefault("tickers", {})

    for a in assessments:
        prev = per_ticker.get(a.name, {})
        prev_level = prev.get("level", 0) or 0
        raw = prev.get("last_notified")
        prev_notified = date.fromisoformat(raw) if raw else None

        # 已經在警戒中時改用放寬門檻，要明顯回升才算脫離（遲滯）
        cur_level = a.level if prev_level == 0 else max(a.level, a.hold_level)
        note = None

        if cur_level > prev_level:
            note = f"{a.name} 進入{a.status_text}（回撤 {a.drawdown:+.1f}%、乖離 {a.deviation:+.1f}%）"
            prev = {"level": cur_level, "last_notified": today.isoformat()}

        elif cur_level > 0 and cur_level == prev_level:
            due = (
                repeat_reminder_days > 0
                and prev_notified is not None
                and today - prev_notified >= timedelta(days=repeat_reminder_days)
            )
            if due:
                note = (f"{a.name} 仍在{a.status_text}"
                        f"（已持續 {(today - prev_notified).days} 天）")
                prev = {"level": cur_level, "last_notified": today.isoformat()}
            else:
                prev = {"level": cur_level,
                        "last_notified": prev_notified.isoformat() if prev_notified else None}

        elif cur_level == 0 and prev_level > 0:
            if notify_on_recovery and a.notify:
                recovered.append(a.name)
                note = f"{a.name} 已脫離警戒區"
            prev = {"level": 0, "last_notified": None}

        else:
            prev = {"level": cur_level,
                    "last_notified": prev_notified.isoformat() if prev_notified else None}

        # 只有設定要通知的標的才會產生通知理由，但狀態一律記錄
        if note and a.notify:
            reasons.append(note)

        per_ticker[a.name] = prev

    state["last_run"] = today.isoformat()

    return Decision(
        should_notify=bool(reasons),
        reason="；".join(reasons) if reasons else "無變化，不通知",
        assessments=assessments,
        recovered=recovered,
    )
