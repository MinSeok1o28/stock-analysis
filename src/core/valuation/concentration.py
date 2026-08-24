"""집중도. HHI + ETF 경유 숨은 중복 노출(룩스루).

콕핏이 자산 유형별로 다른 잣대를 쓰므로 models.AssetType에만 의존한다.
새 자산 유형이 추가돼도 이 파일은 바뀌지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...models import AssetType, Holding


def weights(holdings: list[Holding], values: dict[str, float]) -> dict[str, float]:
    total = sum(values.get(h.ticker, 0.0) for h in holdings)
    if total <= 0:
        return {}
    return {h.ticker: values.get(h.ticker, 0.0) / total for h in holdings}


def hhi(w: dict[str, float]) -> float:
    """허핀달 지수. 0~1. 1/n이 균등분산, 1이 완전집중."""
    return sum(x * x for x in w.values())


def effective_positions(w: dict[str, float]) -> float:
    """유효 종목 수 = 1/HHI. 10종목을 담았어도 실제로는 3종목일 수 있다."""
    h = hhi(w)
    return 1 / h if h > 0 else 0.0


@dataclass(frozen=True)
class LookThrough:
    ticker: str
    direct: float
    via_etf: float

    @property
    def total(self) -> float:
        return self.direct + self.via_etf

    @property
    def hidden_ratio(self) -> float:
        return self.via_etf / self.total if self.total > 0 else 0.0


def look_through(
    w: dict[str, float],
    holdings: list[Holding],
    etf_constituents: dict[str, dict[str, float]],
) -> list[LookThrough]:
    """개별주 + 그 종목을 담은 ETF를 경유한 실질 노출을 합산한다.

    etf_constituents: {ETF티커: {구성종목: 비중}}  ← sources/etf_holdings 에서 온다.
    이 데이터가 없으면 빈 dict를 넘기고, 산출물에는 '확인 필요'로 표기해야 한다.
    """
    kinds = {h.ticker: h.asset_type for h in holdings}
    direct = {t: x for t, x in w.items() if kinds.get(t) == AssetType.SINGLE_STOCK}
    hidden: dict[str, float] = {}
    for etf, etf_w in w.items():
        if kinds.get(etf) in (AssetType.SINGLE_STOCK, AssetType.CASH, None):
            continue
        for name, share in etf_constituents.get(etf, {}).items():
            hidden[name] = hidden.get(name, 0.0) + etf_w * share
    rows = [LookThrough(t, direct.get(t, 0.0), hidden.get(t, 0.0))
            for t in set(direct) | set(hidden)]
    return sorted(rows, key=lambda r: -r.total)
