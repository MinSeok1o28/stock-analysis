"""시세 어댑터. 현재 구현: 토스증권 Open API (2차).

어댑터로 둔 이유: 벤더가 바뀔 때 이 파일만 바뀌게 하려고.
조사에서 확인한 사례처럼 엔드포인트가 스킬 23곳에 흩어지면 벤더 하나 바뀔 때 23곳을 고쳐야 한다.

상위 계층(스킬·콕핏·브리핑)은 여기만 import하고 토스의 존재를 몰라도 된다.
"""

from __future__ import annotations

from ..provenance import Sourced, Unavailable
from . import toss

#: 교체 지점. 다른 벤더로 바꾸려면 이 세 줄만 바꾼다.
_prices = toss.prices
_overnight = toss.overnight_move
_candles = toss.daily_candles


def last_close(ticker: str) -> Sourced[float] | Unavailable:
    got = _prices([ticker])
    if isinstance(got, Unavailable):
        return got
    v = got.get(ticker.upper())
    return v if v is not None else Unavailable(f"{ticker} 종가", "응답에 해당 심볼 없음")


def quotes(tickers: list[str]) -> dict[str, Sourced[float]] | Unavailable:
    """포트폴리오 전체 시세. 토스는 200종목을 1콜로 처리한다."""
    return _prices(tickers)


def overnight_move(ticker: str) -> Sourced[float] | Unavailable:
    """직전 2일 종가 변동률. 브리핑 ±5% 신호의 입력."""
    return _overnight(ticker)


def market_cap(ticker: str, shares_outstanding: Sourced[float] | Unavailable
               ) -> Sourced[float] | Unavailable:
    """시가총액 = 주가 × 주식수.

    주식수는 SEC(1차, 무료)에서, 주가는 벤더(2차)에서 온다.
    파생값이므로 두 출처를 모두 밝힌다 — 역DCF의 market_value 입력이 된다.
    """
    if isinstance(shares_outstanding, Unavailable):
        return shares_outstanding
    px = last_close(ticker)
    if isinstance(px, Unavailable):
        return px
    shares = shares_outstanding.value
    shares = shares.value if hasattr(shares, "value") else float(shares)
    from ..provenance import Locator, Source, SourceKind, Tier
    src = Source(
        f"시가총액 파생 (주가: {px.source.name} / 주식수: {shares_outstanding.source.name})",
        Tier.VENDOR, SourceKind.API,
        Locator(url=px.source.locator.url or "",
                section=f"주식수 출처: {shares_outstanding.source.locator.url or 'n/a'}"),
    )
    return Sourced(float(px.value) * shares, src)


def daily_candles(ticker: str, count: int = 200):
    return _candles(ticker, count)
