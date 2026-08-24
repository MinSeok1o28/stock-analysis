"""이벤트 감지. 순수 함수 — I/O 없음.

"이 종목을 왜 지금 봐야 하는가"를 사실로 만든다.
예측하지 않는다. 관측된 상태와 과거 분포만 제시한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import median

# ── 임계값. 여기만 고치면 민감도가 바뀐다 ──────────────────────
VOLUME_SPIKE = 2.0        # 20일 평균 대비
MOVE_BIG = 0.05           # 일간 ±5%
NEAR_HIGH = 0.03          # 52주 고점 대비 -3% 이내
NEAR_LOW = 0.05           # 52주 저점 대비 +5% 이내
EARNINGS_SOON_DAYS = 7
EARNINGS_JUST_DAYS = 3
VALUATION_GAP = 0.05      # 요구 성장률 − 과거 실적 5%p


@dataclass(frozen=True)
class Event:
    tag: str
    detail: str
    severity: int = 1         # 1 관찰 · 2 주목 · 3 우선

    def __str__(self) -> str:
        return f"[{self.tag}] {self.detail}"


@dataclass
class Bars:
    """일봉 시계열 래퍼. 계산을 한 곳에 모은다."""

    rows: list[dict]          # {date, open, high, low, close, volume}

    @property
    def last(self) -> dict:
        return self.rows[-1]

    def close(self, i: int = -1) -> float:
        return self.rows[i]["close"]

    def change(self, i: int = -1) -> float | None:
        if len(self.rows) < abs(i) + 1:
            return None
        prev = self.rows[i - 1]["close"]
        return (self.rows[i]["close"] - prev) / prev if prev else None

    def volume_ratio(self, window: int = 20) -> float | None:
        if len(self.rows) < window + 1:
            return None
        avg = sum(r["volume"] for r in self.rows[-window - 1:-1]) / window
        return self.rows[-1]["volume"] / avg if avg else None

    def window(self, days: int = 252) -> list[dict]:
        return self.rows[-days:]

    def high_low(self, days: int = 252) -> tuple[float, float]:
        w = self.window(days)
        return max(r["high"] or r["close"] for r in w), min(r["low"] or r["close"] for r in w)

    def index_of(self, d: str) -> int | None:
        for i, r in enumerate(self.rows):
            if r["date"] == d:
                return i
        return None

    def first_index_on_or_after(self, d: date, limit: int = 6) -> int | None:
        for k in range(limit):
            i = self.index_of((d + timedelta(days=k)).isoformat())
            if i is not None:
                return i
        return None


@dataclass(frozen=True)
class ReactionStat:
    """과거 실적 발표 반응 분포. 예측이 아니라 관측된 과거다."""

    moves: list[tuple[str, str, float, float]]   # (실적일, 반응일, 수익률, 거래량배수)

    @property
    def n(self) -> int:
        return len(self.moves)

    @property
    def median_abs(self) -> float | None:
        return median([abs(m[2]) for m in self.moves]) if self.moves else None

    @property
    def max_abs(self) -> float | None:
        return max((abs(m[2]) for m in self.moves), default=None)

    @property
    def up_count(self) -> int:
        return sum(1 for m in self.moves if m[2] > 0)

    @property
    def mean(self) -> float | None:
        return sum(m[2] for m in self.moves) / self.n if self.moves else None

    def summary(self) -> str:
        if not self.moves:
            return "과거 반응 표본 없음"
        return (f"절대변동 중앙값 {self.median_abs:.1%} · 최대 {self.max_abs:.1%} · "
                f"상승 {self.up_count}/{self.n}회 · 평균 {self.mean:+.1%}")


def reaction_stats(bars: Bars, events: list, window: int = 20) -> ReactionStat:
    """과거 실적 발표일 전후 반응을 모은다.

    **마감 후 발표면 반응은 다음 거래일이다.** 제출일 당일을 재면 발표 전날을
    재는 셈이라 반응 크기가 실제의 1/3 수준으로 나온다.
    """
    out = []
    for e in events:
        base = bars.first_index_on_or_after(e.filed_on)
        if base is None:
            continue
        i = base + 1 if e.after_close else base
        if i <= 0 or i >= len(bars.rows):
            continue
        r = bars.change(i)
        if r is None:
            continue
        lo = max(0, i - window)
        avg = (sum(x["volume"] for x in bars.rows[lo:i]) / (i - lo)) if i > lo else 0
        out.append((e.filed_on.isoformat(), bars.rows[i]["date"], r,
                    bars.rows[i]["volume"] / avg if avg else 0.0))
    return ReactionStat(out)


@dataclass(frozen=True)
class Scenario:
    """과거 분포 기준 시나리오. **예측이 아니다.**"""

    label: str
    move: float
    price: float
    basis: str


def scenarios(price: float, stat: ReactionStat) -> list[Scenario]:
    if not stat.moves or stat.median_abs is None:
        return []
    med, mx = stat.median_abs, stat.max_abs
    return [
        Scenario("상단", +mx, price * (1 + mx), f"과거 최대 상승폭 수준 (표본 {stat.n})"),
        Scenario("상단 중앙", +med, price * (1 + med), "과거 절대변동 중앙값"),
        Scenario("변화 없음", 0.0, price, "과거 반응이 작았던 경우"),
        Scenario("하단 중앙", -med, price * (1 - med), "과거 절대변동 중앙값"),
        Scenario("하단", -mx, price * (1 - mx), f"과거 최대 하락폭 수준 (표본 {stat.n})"),
    ]


def detect(bars: Bars, *, days_to_earnings: int | None = None,
           earnings_confirmed: bool = False, days_since_earnings: int | None = None,
           valuation_gap: float | None = None,
           in_rankings: list[str] | None = None) -> list[Event]:
    """관측 가능한 이벤트를 태그로 만든다."""
    ev: list[Event] = []

    if days_to_earnings is not None and 0 <= days_to_earnings <= EARNINGS_SOON_DAYS:
        kind = "확정" if earnings_confirmed else "추정"
        ev.append(Event("실적임박", f"D-{days_to_earnings} ({kind})", 3))
    if days_since_earnings is not None and 0 <= days_since_earnings <= EARNINGS_JUST_DAYS:
        ev.append(Event("실적직후", f"{days_since_earnings}거래일 전 발표", 3))

    vr = bars.volume_ratio()
    if vr and vr >= VOLUME_SPIKE:
        ev.append(Event("거래량이상", f"20일 평균 대비 {vr:.1f}배", 2))

    ch = bars.change()
    if ch is not None and abs(ch) >= MOVE_BIG:
        ev.append(Event("급변", f"직전 대비 {ch:+.1%}", 2))

    hi, lo = bars.high_low()
    px = bars.close()
    if hi and (hi - px) / hi <= NEAR_HIGH:
        ev.append(Event("52주고점권", f"고점 {hi:,.2f} 대비 {(px/hi - 1):+.1%}", 1))
    elif lo and (px - lo) / lo <= NEAR_LOW:
        ev.append(Event("52주저점권", f"저점 {lo:,.2f} 대비 {(px/lo - 1):+.1%}", 2))

    if valuation_gap is not None and abs(valuation_gap) >= VALUATION_GAP:
        ev.append(Event("밸류갭",
                        f"요구 성장률이 과거 실적보다 {valuation_gap:+.1%}p", 2))

    for r in (in_rankings or []):
        ev.append(Event("시장상위", r, 1))
    return ev


@dataclass
class Candidate:
    ticker: str
    name: str
    price: float
    change: float | None
    events: list[Event] = field(default_factory=list)
    stat: ReactionStat | None = None
    scenarios: list[Scenario] = field(default_factory=list)
    held: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def score(self) -> int:
        """태그 심각도 합. 여러 이벤트가 겹치는 종목이 먼저다."""
        return sum(e.severity for e in self.events)

    @property
    def why(self) -> str:
        return " · ".join(str(e) for e in sorted(self.events, key=lambda e: -e.severity))
