"""재무 골격 계산. 순수 함수 — I/O 없음.

계산을 파이프라인에 흩뿌리지 않고 여기 모은다.
방법론이 바뀔 때만 이 파일이 바뀐다 (예: 마진 정의 변경).
"""

from __future__ import annotations

from dataclasses import dataclass


def yoy(series: list[tuple[str, float]]) -> list[tuple[str, float | None]]:
    """전년 대비 증감률. 직전 값이 0 이하면 None (억지로 만들지 않는다)."""
    out: list[tuple[str, float | None]] = [(series[0][0], None)] if series else []
    for (_, prev), (label, cur) in zip(series, series[1:]):
        out.append((label, (cur - prev) / abs(prev) if prev else None))
    return out


def margin(numer: list[tuple[str, float]], denom: list[tuple[str, float]]
           ) -> list[tuple[str, float | None]]:
    """기간이 일치하는 항목만 비율을 낸다. 한쪽이 없으면 그 기간은 제외한다."""
    d = dict(denom)
    return [(k, (v / d[k]) if d.get(k) else None) for k, v in numer if k in d]


@dataclass(frozen=True)
class Streak:
    """연속 증가/감소 구간. '몇 년째 개선/악화 중인가'를 사실로 제시한다."""

    direction: str      # "증가" | "감소" | "혼조"
    years: int

    def __str__(self) -> str:
        return f"{self.years}년 연속 {self.direction}" if self.years >= 2 else "혼조"


def streak(series: list[tuple[str, float]]) -> Streak:
    if len(series) < 2:
        return Streak("혼조", 0)
    vals = [v for _, v in series]
    up = down = 1
    for prev, cur in zip(vals, vals[1:]):
        if cur > prev:
            up += 1; down = 1
        elif cur < prev:
            down += 1; up = 1
        else:
            up = down = 1
    if up >= 2:
        return Streak("증가", up)
    if down >= 2:
        return Streak("감소", down)
    return Streak("혼조", 0)


def fcf_yield(fcf: float, market_cap: float) -> float | None:
    """FCF 수익률. 밸류에이션 '판단'이 아니라 사실 하나를 더 놓는 것이다."""
    return fcf / market_cap if market_cap else None


def per_share(total: float, shares: float) -> float | None:
    return total / shares if shares else None
