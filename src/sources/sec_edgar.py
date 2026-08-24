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
from pathlib import Path

from ..models import Filing, FinancialFact, Market
from ..provenance import Sourced, Unavailable, local_filing, primary_api
from ._http import SourceUnavailable, get_json, get_text

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


ANNUAL_MIN_DAYS, ANNUAL_MAX_DAYS = 340, 380


def _is_annual(entry: dict) -> bool:
    """연간 항목인가.

    SEC 는 10-K 안에 분기값도 함께 싣는다. 기간 길이로 걸러야 한다.
    `fp == "FY"` 만 보면 90일짜리 분기값이 통과한다 (실측 확인).
    start 가 없으면 시점 항목(주식수·자산 등)이므로 그대로 통과시킨다.
    """
    start, end = entry.get("start"), entry.get("end")
    if not end:
        return False
    if not start:
        return True                      # instant fact
    days = (date.fromisoformat(end) - date.fromisoformat(start)).days
    return ANNUAL_MIN_DAYS <= days <= ANNUAL_MAX_DAYS


def annual_series(ticker: str, concept: str) -> list[Sourced[FinancialFact]] | Unavailable:
    """연도별 값을 출처와 함께 반환한다.

    **핵심 주의**: companyfacts 의 `fy` 는 *그 값이 실린 보고서의* 회계연도이지
    데이터 기간이 아니다. FY2022 10-K 에는 FY2020 비교치가 `fy=2022` 로 들어있다.
    이걸 키로 쓰면 연도가 통째로 밀린다 — 기간 종료일(`end`)로 키를 잡아야 한다.

    회계연도 라벨은 기간 종료 연도를 쓴다. 회사 자체 명명(예: 1월 결산사)과
    다를 수 있으나 시계열 정렬과 비교에는 일관되다.
    """
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
        # 기간 종료일 → (제출일, 항목). 같은 기간이 여러 보고서에 실리면 최신 제출본을 쓴다.
        best: dict[str, tuple[str, dict]] = {}
        for e in entries:
            if e.get("form") != "10-K" or not _is_annual(e):
                continue
            end = e["end"]
            filed = e.get("filed", "")
            if end not in best or filed > best[end][0]:
                best[end] = (filed, e)
        out: list[Sourced[FinancialFact]] = []
        for end in sorted(best):
            e = best[end][1]
            d = date.fromisoformat(end)
            out.append(Sourced(
                FinancialFact(concept=concept, unit=unit, period_end=d,
                              fiscal_year=d.year, value=float(e["val"])),
                primary_api(f"{NAME} {tag}", url, section=f"기간종료 {end} · 10-K"),
            ))
        if out:
            return out
    return Unavailable(f"{ticker} {concept}", f"XBRL 태그 미발견: {tags}")


def free_cash_flow(ticker: str) -> list[Sourced[FinancialFact]] | Unavailable:
    """FCF = 영업현금흐름 − CapEx. 두 계열 모두 있는 연도만 반환한다."""
    ocf = annual_series(ticker, "OperatingCashFlow")
    capex = annual_series(ticker, "CapEx")
    if isinstance(ocf, Unavailable):
        return ocf
    if isinstance(capex, Unavailable):
        return capex
    cx = {s.value.period_end: s for s in capex}
    out = []
    for s in ocf:
        fy = s.value.fiscal_year
        if s.value.period_end not in cx:
            continue
        fact = FinancialFact("FreeCashFlow", s.value.unit, s.value.period_end, fy,
                             s.value.value - cx[s.value.period_end].value.value)
        # 파생값이므로 두 원천을 모두 밝힌다. 한쪽만 인용하면 출처가 사실과 달라진다.
        out.append(Sourced(fact, primary_api(
            f"{NAME} FCF=OCF−CapEx (파생)",
            s.source.locator.url or "",
            section=(f"기간종료 {s.value.period_end} · OCF−CapEx 파생 "
                     "(CapEx: PaymentsToAcquirePropertyPlantAndEquipment)"),
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
    for form, acc, doc, fdate in zip(r["form"], r["accessionNumber"],
                                     r["primaryDocument"], r["filingDate"]):
        if form != "10-K":
            continue
        d = date.fromisoformat(fdate)
        out.append(Filing(ticker.upper(), "10-K", d.year, d,
                          accession=acc, primary_document=doc, cik=cik))
        if len(out) >= limit:
            break
    return out or Unavailable(f"{ticker} 10-K 목록", "10-K 제출 이력 없음")


RAW_DIR = Path("data/raw")


def filing_url(filing: Filing) -> str | None:
    """Archives 본문 URL. accession 의 하이픈을 제거한 경로를 쓴다."""
    if not (filing.accession and filing.primary_document and filing.cik):
        return None
    acc = filing.accession.replace("-", "")
    return (f"https://www.sec.gov/Archives/edgar/data/"
            f"{int(filing.cik)}/{acc}/{filing.primary_document}")


def filing_text(filing: Filing) -> Sourced[str] | Unavailable:
    """10-K 본문을 받아 태그를 제거한 텍스트로 반환한다. 키 불필요.

    원문 HTML 은 data/raw/<티커>/10-K/<연도>/ 에 보존한다 — 인용을 나중에 재검증하려면
    원문이 남아 있어야 한다. 확정 문서라 캐시 만료를 두지 않는다.

    출처 등급은 1차이고 kind 는 LOCAL_DOCUMENT 다 (로컬에 원문이 있으므로).
    다만 페이지 번호는 붙이지 않는다 — HTML 공시에는 인쇄 페이지 개념이 없다.
    섹션명으로 위치를 표시한다.
    """
    from ..core.narrative.sections import strip_html

    url = filing_url(filing)
    if not url:
        return Unavailable(f"{filing.ticker} {filing.fiscal_year} 10-K 본문",
                           "accession/primaryDocument 미확보")
    raw = RAW_DIR / filing.ticker / "10-K" / str(filing.fiscal_year) / filing.primary_document
    try:
        html = get_text(url, headers=_headers(), cache_path=raw, min_interval=MIN_INTERVAL)
    except SourceUnavailable as exc:
        return Unavailable(f"{filing.ticker} {filing.fiscal_year} 10-K 본문", str(exc))
    text = strip_html(html)
    if len(text) < 5_000:
        return Unavailable(f"{filing.ticker} {filing.fiscal_year} 10-K 본문",
                           f"본문이 너무 짧다({len(text)}자) — 문서 형식 확인 필요: {url}")
    return Sourced(text, local_filing(
        f"{filing.ticker} 10-K FY{filing.fiscal_year}", str(raw),
        section=f"제출 {filing.filed_on} · {url}"))


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
