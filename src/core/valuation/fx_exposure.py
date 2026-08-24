"""환노출. 해외자산 비중이 높을 때 원화 환산 수익률의 환율 민감도."""

from __future__ import annotations

from dataclasses import dataclass

from ...models import Holding


@dataclass(frozen=True)
class FxScenario:
    move: float           # 환율 변동률
    krw_return: float     # 원화 환산 총수익률


def foreign_ratio(holdings: list[Holding], values: dict[str, float]) -> float:
    total = sum(values.get(h.ticker, 0.0) for h in holdings)
    if total <= 0:
        return 0.0
    foreign = sum(values.get(h.ticker, 0.0) for h in holdings if h.is_foreign_currency)
    return foreign / total


def sensitivity(
    local_return: float,
    foreign_weight: float,
    moves: tuple[float, ...] = (-0.10, -0.05, 0.0, 0.05, 0.10),
) -> list[FxScenario]:
    """환율이 moves만큼 움직였을 때 원화 환산 수익률.

    시나리오만 제시한다. 헤지 여부나 비중 조정을 권하지 않는다 (CLAUDE.md).
    """
    return [
        FxScenario(m, (1 + local_return) * (1 + m * foreign_weight) - 1)
        for m in moves
    ]
