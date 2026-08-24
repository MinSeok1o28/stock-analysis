"""이상치 감지. CLAUDE.md 계산 규칙: FCF가 3년 평균 대비 ±40% 이탈 시 자동 보고."""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_THRESHOLD = 0.40
DEFAULT_WINDOW = 3


@dataclass(frozen=True)
class Outlier:
    period: str
    value: float
    baseline: float
    deviation: float

    def __str__(self) -> str:
        return (f"{self.period}: {self.value:,.0f} vs 직전 {DEFAULT_WINDOW}년 평균 "
                f"{self.baseline:,.0f} ({self.deviation:+.1%})")


def detect(series: list[tuple[str, float]], *, threshold: float = DEFAULT_THRESHOLD,
           window: int = DEFAULT_WINDOW) -> list[Outlier]:
    """(기간, 값) 시계열에서 이탈 구간을 찾는다. 시간순 입력을 가정한다."""
    out = []
    for i in range(window, len(series)):
        prior = [v for _, v in series[i - window:i]]
        baseline = sum(prior) / window
        if baseline == 0:
            continue
        period, value = series[i]
        dev = (value - baseline) / abs(baseline)
        if abs(dev) >= threshold:
            out.append(Outlier(period, value, baseline, dev))
    return out


def normalized_base(series: list[tuple[str, float]], *, window: int = DEFAULT_WINDOW) -> float | None:
    """역DCF 기준 FCF. 최근 단년이 이상치면 평균을 쓰도록 유도한다."""
    if len(series) < window:
        return None
    return sum(v for _, v in series[-window:]) / window
