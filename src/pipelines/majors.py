"""주요 기업 목록 — 체크박스로 골라 한 번에 분석하기 위한 재료.

## "주요"를 무엇으로 정의하는가
임의로 고르지 않는다. 시장마다 **출처가 있는 기준**을 쓰고 그 기준을 화면에 적는다.

| 시장 | 기준 | 출처 |
|---|---|---|
| 미국 | S&P 500 편입 비중 상위 | SPDR SPY 구성종목 (**1차** — ETF 발행사 일간 파일) |
| 한국 | 시가총액 상위 | 토스 종목정보(발행주식수) × 시세 (**2차** — 파생값) |

미국은 지수 편입 자체가 "주요"의 정의라 1차 출처를 그대로 쓴다.
한국은 대응물이 없다 — KODEX 200 발행사가 자동 수집을 막아 구성종목을 못 받는다
(`sources/etf_holdings.py` 참조). 그래서 시가총액을 직접 계산하고, 그게 파생값이라는
사실을 산출물에 적는다.

## 비용
한국은 KOSPI+KOSDAQ 2,600여 종목을 200개씩 묶어 조회한다. 발행주식수는 1주 캐시라
사실상 시세 조회 비용만 든다 (실측 KOSPI 804종목 2.4초).
"""

from __future__ import annotations

from dataclasses import dataclass

from ..models import Market
from ..provenance import Sourced, Unavailable, vendor_api
from ..sources import etf_holdings, toss

KR_MARKETS = ("KOSPI", "KOSDAQ")
US_ETF = "SPY"
DEFAULT_LIMIT = 25
_BATCH = 200          # 토스가 1콜로 처리하는 최대 종목 수


@dataclass(frozen=True)
class Major:
    """주요 기업 1건."""

    ticker: str
    name: str
    market: Market
    rank: int
    metric: float             # 시가총액(원) 또는 지수 편입 비중(0~1)
    metric_kind: str          # "cap" | "weight"

    @property
    def metric_text(self) -> str:
        if self.metric_kind == "weight":
            return f"{self.metric:.2%}"
        return f"{self.metric / 1e12:,.1f}조"


def korea(limit: int = DEFAULT_LIMIT) -> Sourced[list[Major]] | Unavailable:
    """시가총액 상위. 주가 × 발행주식수 — **파생값이라 2차로 표기한다.**"""
    u = toss.universe(KR_MARKETS)
    if isinstance(u, Unavailable):
        return u
    syms = [r["symbol"] for r in u.value]
    if not syms:
        return Unavailable("한국 주요 기업", "종목 마스터가 비어 있다")

    caps: dict[str, tuple[str, float]] = {}
    failed = 0
    for i in range(0, len(syms), _BATCH):
        chunk = syms[i:i + _BATCH]
        info, px = toss.stock_info(chunk), toss.prices(chunk)
        if isinstance(info, Unavailable) or isinstance(px, Unavailable):
            failed += 1
            continue
        for sym, v in info.value.items():
            shares, quote = v.get("shares_outstanding"), px.get(sym)
            if shares and quote:
                caps[sym] = (v.get("name") or sym, shares * quote.value)
    if not caps:
        return Unavailable("한국 주요 기업", f"시가총액 계산 실패 (배치 {failed}개 실패)")

    top = sorted(caps.items(), key=lambda kv: -kv[1][1])[:limit]
    rows = [Major(sym, nm, Market.KR, i + 1, cap, "cap")
            for i, (sym, (nm, cap)) in enumerate(top)]
    note = f"{len(caps):,}종목 중 상위 {len(rows)}"
    if failed:
        note += f" · 배치 {failed}개 미확보"
    return Sourced(rows, vendor_api(
        f"토스증권 시가총액 상위 (주가 × 발행주식수, 파생값)",
        "https://openapi.tossinvest.com/api/v1/stocks", section=note))


def usa(limit: int = DEFAULT_LIMIT) -> Sourced[list[Major]] | Unavailable:
    """S&P 500 편입 비중 상위. 지수 편입 자체가 '주요'의 정의다."""
    h = etf_holdings.holdings(US_ETF)
    if isinstance(h, Unavailable):
        return h
    top = sorted(h.value.items(), key=lambda kv: -kv[1])[:limit]
    syms = [s for s, _ in top]
    names = toss.names(syms)          # 최선 노력 — 실패하면 심볼만 쓴다
    rows = [Major(s, names.get(s, s), Market.US, i + 1, w, "weight")
            for i, (s, w) in enumerate(top)]
    return Sourced(rows, h.source)


def both(limit: int = DEFAULT_LIMIT
         ) -> dict[str, Sourced[list[Major]] | Unavailable]:
    """{'KR': …, 'US': …}. 한쪽이 실패해도 다른 쪽은 나온다."""
    return {"KR": korea(limit), "US": usa(limit)}


if __name__ == "__main__":
    import sys

    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    for code, got in both(n).items():
        print(f"── {code} ──")
        if isinstance(got, Unavailable):
            print(" ", got); continue
        print(" ", got.cite()[:110])
        for m in got.value:
            print(f"  {m.rank:2d}. {m.ticker:8s} {m.name[:18]:20s} {m.metric_text:>9s}")
