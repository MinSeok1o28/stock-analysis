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


@dataclass(frozen=True)
class EarningsQuality:
    """이익과 현금이 같은 방향으로 가는가.

    영상 원본의 규칙: "이익이랑 현금이 따로 놀면 경고를 띄우라."
    순이익은 늘었는데 영업현금흐름이 줄면 회계상 부풀림 가능성이 있다.
    반대(현금은 느는데 이익이 주는 것)는 일회성 손상·상각일 수 있다.
    """

    years: int
    same_direction: int
    ni_cagr: float | None
    ocf_cagr: float | None
    latest_gap: float | None      # 최근 연도 (순이익 증감률 − OCF 증감률)
    flag: str                     # ok | warn | insufficient

    @property
    def is_warning(self) -> bool:
        return self.flag == "warn"

    def __str__(self) -> str:
        if self.flag == "insufficient":
            return "이익-현금 정합성: 표본 부족"
        base = (f"최근 {self.years}년 중 {self.same_direction}년이 같은 방향")
        if self.ni_cagr is not None and self.ocf_cagr is not None:
            base += f" · 순이익 CAGR {self.ni_cagr:+.1%} vs 영업현금흐름 {self.ocf_cagr:+.1%}"
        return ("⚠ 이익과 현금이 따로 움직인다 — " + base) if self.is_warning else \
               ("이익과 현금이 같은 방향 — " + base)


def earnings_quality(net_income: list[tuple[str, float]],
                     operating_cf: list[tuple[str, float]],
                     *, min_years: int = 3, gap_threshold: float = 0.25
                     ) -> EarningsQuality:
    """순이익 vs 영업현금흐름 정합성.

    두 계열의 기간이 겹치는 구간만 본다. 방향 일치율과 최근 증감률 격차로 판정한다.
    """
    ocf = dict(operating_cf)
    pairs = [(k, v, ocf[k]) for k, v in net_income if k in ocf]
    if len(pairs) < min_years:
        return EarningsQuality(len(pairs), 0, None, None, None, "insufficient")

    same = 0
    for (_, n0, o0), (_, n1, o1) in zip(pairs, pairs[1:]):
        if (n1 - n0) * (o1 - o0) > 0:
            same += 1
    total = len(pairs) - 1

    def _cagr(vals: list[float]) -> float | None:
        if len(vals) < 2 or vals[0] <= 0 or vals[-1] <= 0:
            return None
        return (vals[-1] / vals[0]) ** (1 / (len(vals) - 1)) - 1

    ni_c = _cagr([p[1] for p in pairs])
    ocf_c = _cagr([p[2] for p in pairs])

    gap = None
    if len(pairs) >= 2:
        n0, n1 = pairs[-2][1], pairs[-1][1]
        o0, o1 = pairs[-2][2], pairs[-1][2]
        if n0 and o0:
            gap = ((n1 - n0) / abs(n0)) - ((o1 - o0) / abs(o0))

    warn = (same / total < 0.5) if total else False
    if gap is not None and abs(gap) >= gap_threshold:
        warn = True
    return EarningsQuality(total, same, ni_c, ocf_c, gap, "warn" if warn else "ok")
