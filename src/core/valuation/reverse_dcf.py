"""역DCF — '이 가격이 요구하는 성장률'을 이분법으로 역산한다.

일반 DCF는 성장률을 넣어 가치를 구한다. 여기서는 반대로 현재 가치를 넣고 성장률을 구한다.
목표주가를 산출하지 않는 이유: 그건 곧 매매 신호가 된다 (CLAUDE.md).
이 모듈은 '시장이 요구하는 성장률'과 '과거 실제 성장률'의 격차만 제시한다.

순수 함수. I/O 없음. 네트워크 없이 테스트된다.
"""

from __future__ import annotations

from dataclasses import dataclass


class ConvergenceError(Exception):
    pass


def present_value(fcf0: float, growth: float, wacc: float,
                  terminal_growth: float, years: int) -> float:
    """2단계 DCF 현재가치.

    명시적 예측기간 `years` 동안 `growth`로 성장 후, 영구 `terminal_growth`.
    """
    if wacc <= terminal_growth:
        raise ValueError(f"WACC({wacc:.3f})가 영구성장률({terminal_growth:.3f}) 이하일 수 없다")
    pv = 0.0
    fcf = fcf0
    for t in range(1, years + 1):
        fcf = fcf * (1 + growth)
        pv += fcf / (1 + wacc) ** t
    terminal = fcf * (1 + terminal_growth) / (wacc - terminal_growth)
    return pv + terminal / (1 + wacc) ** years


@dataclass(frozen=True)
class BisectionResult:
    """이분법 결과. 수렴 진단을 함께 반환해 결과를 검증 가능하게 만든다."""

    value: float
    iterations: int
    residual: float
    bracket: tuple[float, float]
    converged: bool

    def __str__(self) -> str:
        status = "수렴" if self.converged else "미수렴"
        return f"{self.value:.4%} ({status}, {self.iterations}회, 오차 {self.residual:.3e})"


def implied_growth(
    market_value: float,
    fcf0: float,
    wacc: float,
    *,
    terminal_growth: float = 0.025,
    years: int = 10,
    lo: float = -0.50,
    hi: float = 1.00,
    tol: float = 1e-7,
    max_iter: int = 200,
) -> BisectionResult:
    """market_value가 성립하려면 필요한 연평균 성장률을 이분법으로 구한다.

    present_value는 growth에 대해 단조증가하므로 이분법이 항상 수렴한다.
    암산이 아니라 반드시 이 함수를 거치게 하는 것이 CLAUDE.md의 계산 규칙이다.
    """
    if fcf0 <= 0:
        raise ValueError(f"기준 FCF가 0 이하({fcf0}) — 역DCF 적용 불가. 이익 정상화 후 재시도 필요")

    def f(g: float) -> float:
        return present_value(fcf0, g, wacc, terminal_growth, years) - market_value

    f_lo, f_hi = f(lo), f(hi)
    if f_lo > 0:
        raise ConvergenceError(
            f"하한 성장률 {lo:.0%}에서도 가치가 시가를 초과 — 시장이 마이너스 성장을 요구. "
            "lo를 더 낮추거나 WACC 가정을 재검토하라"
        )
    if f_hi < 0:
        raise ConvergenceError(
            f"상한 성장률 {hi:.0%}로도 시가에 미달 — 요구 성장률이 비현실적으로 높다"
        )

    a, b = lo, hi
    for i in range(1, max_iter + 1):
        mid = (a + b) / 2
        fm = f(mid)
        if abs(fm) < tol * max(1.0, abs(market_value)) or (b - a) / 2 < tol:
            return BisectionResult(mid, i, abs(fm), (lo, hi), True)
        if fm < 0:
            a = mid
        else:
            b = mid
    return BisectionResult((a + b) / 2, max_iter, abs(f((a + b) / 2)), (lo, hi), False)


def cagr(first: float, last: float, years: int) -> float | None:
    """과거 실제 성장률. 부호가 다르거나 0이 섞이면 None (계산 불가를 숨기지 않는다)."""
    if years <= 0 or first <= 0 or last <= 0:
        return None
    return (last / first) ** (1 / years) - 1


@dataclass(frozen=True)
class SensitivityRow:
    wacc: float
    implied: float | None
    note: str = ""


def wacc_sensitivity(
    market_value: float,
    fcf0: float,
    waccs: tuple[float, ...] = (0.07, 0.08, 0.09, 0.10, 0.11),
    *,
    terminal_growth: float = 0.025,
    years: int = 10,
) -> list[SensitivityRow]:
    """할인율 민감도. 하나의 숫자에 의존하지 않게 만드는 필수 절차 (CLAUDE.md)."""
    rows = []
    for w in waccs:
        try:
            r = implied_growth(market_value, fcf0, w,
                               terminal_growth=terminal_growth, years=years)
            rows.append(SensitivityRow(w, r.value, "" if r.converged else "미수렴"))
        except (ConvergenceError, ValueError) as exc:
            rows.append(SensitivityRow(w, None, str(exc)[:60]))
    return rows


def gap_summary(implied: float, historical: float | None) -> str:
    """시장 요구 성장률과 과거 실적의 격차. 판단이 아니라 격차만 서술한다."""
    if historical is None:
        return "과거 성장률 산출 불가 — 확인 필요"
    diff = implied - historical
    direction = "높다" if diff > 0 else "낮다"
    return (f"시장 요구 {implied:.1%} vs 과거 실적 {historical:.1%} — "
            f"요구치가 {abs(diff):.1%}p {direction}")
