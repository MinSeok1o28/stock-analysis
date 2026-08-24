"""어닝콜 트랜스크립트. 무료 안정 소스가 없는 유일한 병목.

조사 결과: API Ninjas(무료 티어 상업이용 불가), Roic.ai(5 req/min·2년), Alpha Vantage,
Finnhub(트랜스크립트만 프리미엄), FMP. 전부 2차 벤더이며 대부분 키가 필요하다.

미구현 상태에서도 이 함수는 정상 동작한다 — Unavailable을 반환한다.
스토리 리더는 이걸 받아 '확인 필요 (트랜스크립트 미확보)'로 표기하고,
절대 웹검색으로 조용히 대체하지 않는다 (CLAUDE.md 운영 원칙).
"""

from __future__ import annotations

from ..models import Market
from ..provenance import Sourced, Unavailable


def quarterly(ticker: str, year: int, quarter: int, market: Market = Market.US
              ) -> Sourced[str] | Unavailable:
    return Unavailable(
        f"{ticker} {year}Q{quarter} 어닝콜 트랜스크립트",
        "유료 벤더 미연결. FMP_API_KEY 설정 후 구현 예정"
        + ("" if market is Market.US else f" / {market.transcript_availability}"),
    )
