"""1차 출처: SEC EDGAR. 키 불필요.

- companyfacts:  기업이 제출한 모든 XBRL 사실 (data.sec.gov)
- submissions:   제출 이력 → 10-K 목록
- 레이트리밋 10 req/s, User-Agent 헤더 필수 (없으면 403)

벤더가 아니라 발행 주체이므로 Tier.PRIMARY. 이 파일이 바뀌는 유일한 이유는
SEC가 스키마·엔드포인트를 바꿀 때다.
"""

from __future__ import annotations

import os
from datetime import date

from ..models import Filing, FinancialFact, Market
from ..provenance import Sourced, Unavailable, primary_api
from ._http import SourceUnavailable, get_json

BASE = "https://data.sec.gov"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
NAME = "SEC EDGAR"
MIN_INTERVAL = 0.11   # 10 req/s 아래로

# 개념명 → XBRL 태그 후보 (첫 매치 사용)
CONCEPTS: dict[str, tuple[str, ...]] = {
    "Revenues": ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"),
    "NetIncome": ("NetIncomeLoss",),
    "OperatingCashFlow": ("NetCashProvidedByUsedInOperatingActivities",),
    "CapEx": ("PaymentsToAcquirePropertyPlantAndEquipment",),
    "SharesOutstanding": ("CommonStockSharesOutstanding", "EntityCommonStockSharesOutstanding"),
}


def _headers() -> dict[str, str]:
    ua = os.environ.get("SEC_USER_AGENT", "").strip()
    if not ua:
        raise SourceUnavailable(
            "SEC_USER_AGENT 미설정 — SEC는 연락처 포함 User-Agent를 요구한다. .env 참조"
        )
    return {"User-Agent": ua, "Accept-Encoding": "gzip, deflate"}


def cik_for(ticker: str) -> str:
    data = get_json(TICKERS_URL, headers=_headers(), cache_key="company_tickers",
                    ttl_sec=604_800, min_interval=MIN_INTERVAL)
    t = ticker.upper()
    for row in data.values():
        if row["ticker"].upper() == t:
            return str(row["cik_str"]).zfill(10)
    raise SourceUnavailable(f"{ticker}: SEC 티커 목록에 없음 (미국 상장사만 조회 가능)")


def annual_series(ticker: str, concept: str) -> list[Sourced[FinancialFact]] | Unavailable:
    """연도별 값을 출처와 함께 반환한다. 10-K(FY) 원본만 사용한다."""
    try:
        cik = cik_for(ticker)
        tags = CONCEPTS.get(concept, (concept,))
        facts = get_json(f"{BASE}/api/xbrl/companyfacts/CIK{cik}.json",
                         headers=_headers(), cache_key=f"cf_{cik}",
                         min_interval=MIN_INTERVAL)
    except SourceUnavailable as exc:
        return Unavailable(f"{ticker} {concept}", str(exc))

    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    dei = facts.get("facts", {}).get("dei", {})
    for tag in tags:
        node = us_gaap.get(tag) or dei.get(tag)
        if not node:
            continue
        unit, entries = next(iter(node["units"].items()))
        url = f"{BASE}/api/xbrl/companyconcept/CIK{cik}/us-gaap/{tag}.json"
        out: dict[int, Sourced[FinancialFact]] = {}
        for e in entries:
            if e.get("form") != "10-K" or e.get("fp") != "FY" or "frame" not in e:
                continue
            fy = e["fy"]
            out[fy] = Sourced(
                FinancialFact(concept=concept, unit=unit,
                              period_end=date.fromisoformat(e["end"]),
                              fiscal_year=fy, value=float(e["val"])),
                primary_api(f"{NAME} {tag}", url, section=f"FY{fy} 10-K"),
            )
        if out:
            return [out[k] for k in sorted(out)]
    return Unavailable(f"{ticker} {concept}", f"XBRL 태그 미발견: {tags}")


def free_cash_flow(ticker: str) -> list[Sourced[FinancialFact]] | Unavailable:
    """FCF = 영업현금흐름 − CapEx. 두 계열 모두 있는 연도만 반환한다."""
    ocf = annual_series(ticker, "OperatingCashFlow")
    capex = annual_series(ticker, "CapEx")
    if isinstance(ocf, Unavailable):
        return ocf
    if isinstance(capex, Unavailable):
        return capex
    cx = {s.value.fiscal_year: s for s in capex}
    out = []
    for s in ocf:
        fy = s.value.fiscal_year
        if fy not in cx:
            continue
        fact = FinancialFact("FreeCashFlow", s.value.unit, s.value.period_end, fy,
                             s.value.value - cx[fy].value.value)
        # 파생값이므로 두 원천을 모두 밝힌다. 한쪽만 인용하면 출처가 사실과 달라진다.
        out.append(Sourced(fact, primary_api(
            f"{NAME} FCF=OCF−CapEx (파생)",
            s.source.locator.url or "",
            section=f"FY{fy} 10-K · OCF−CapEx 파생 (CapEx: PaymentsToAcquirePropertyPlantAndEquipment)",
        )))
    return out or Unavailable(f"{ticker} FreeCashFlow", "OCF/CapEx 연도 교집합 없음")


def annual_filings(ticker: str, limit: int = 4) -> list[Filing] | Unavailable:
    try:
        cik = cik_for(ticker)
        sub = get_json(f"{BASE}/submissions/CIK{cik}.json", headers=_headers(),
                        cache_key=f"sub_{cik}", min_interval=MIN_INTERVAL)
    except SourceUnavailable as exc:
        return Unavailable(f"{ticker} 10-K 목록", str(exc))
    r = sub["filings"]["recent"]
    out = []
    for form, acc, fdate in zip(r["form"], r["accessionNumber"], r["filingDate"]):
        if form != "10-K":
            continue
        d = date.fromisoformat(fdate)
        out.append(Filing(ticker.upper(), "10-K", d.year, d, accession=acc))
        if len(out) >= limit:
            break
    return out or Unavailable(f"{ticker} 10-K 목록", "10-K 제출 이력 없음")


MARKET = Market.US

if __name__ == "__main__":
    import sys
    tk = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    print(f"── {tk} FCF (SEC 1차) ──")
    res = free_cash_flow(tk)
    if isinstance(res, Unavailable):
        print(res)
    else:
        for s in res[-5:]:
            print(f"  FY{s.value.fiscal_year}  {s.value.value/1e9:>10,.1f}B {s.value.unit}   {s.cite()}")
