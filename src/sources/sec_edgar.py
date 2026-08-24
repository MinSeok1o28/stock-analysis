"""1차 출처: SEC EDGAR. 키 불필요.

- companyfacts:  기업이 제출한 모든 XBRL 사실 (data.sec.gov)
- submissions:   제출 이력 → 10-K 목록
- 레이트리밋 10 req/s, User-Agent 헤더 필수 (없으면 403)

벤더가 아니라 발행 주체이므로 Tier.PRIMARY. 이 파일이 바뀌는 유일한 이유는
SEC가 스키마·엔드포인트를 바꿀 때다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
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
    # 회사마다 태그가 다르다. NVDA 는 ProductiveAssets, MSFT·AAPL 은 PP&E.
    "CapEx": ("PaymentsToAcquirePropertyPlantAndEquipment",
              "PaymentsToAcquireProductiveAssets",
              "PaymentsToAcquirePropertyPlantAndEquipmentAndIntangibleAssets",
              "PaymentsForCapitalImprovements"),
    "SharesOutstanding": ("CommonStockSharesOutstanding", "EntityCommonStockSharesOutstanding"),
    # ── 순부채 계산용 (기업가치 = 시가총액 + 순부채) ──
    "Cash": ("CashAndCashEquivalentsAtCarryingValue",),
    "ShortTermInvestments": ("MarketableSecuritiesCurrent", "ShortTermInvestments",
                             "AvailableForSaleSecuritiesDebtSecuritiesCurrent",
                             "OtherShortTermInvestments"),
    "LongTermInvestments": ("MarketableSecuritiesNoncurrent",),
    "TotalDebt": ("LongTermDebt",),
    "ShortTermDebt": ("CommercialPaper", "ShortTermBorrowings"),
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

    # 후보 태그를 **전부** 시도하고 가장 좋은 것을 고른다.
    # 첫 매치를 쓰면 옛 연도만 몇 건 있는 폐기 태그가 선택된다
    # (NVDA 의 PaymentsToAcquirePropertyPlantAndEquipment 가 2010~2012 만 보유).
    # 기준: 최신 기간이 가장 늦은 것 → 동률이면 항목 수가 많은 것.
    best_out: list[Sourced[FinancialFact]] = []
    best_key: tuple = ()
    for tag in tags:
        node = us_gaap.get(tag) or dei.get(tag)
        if not node:
            continue
        unit, entries = next(iter(node["units"].items()))
        url = f"{BASE}/api/xbrl/companyconcept/CIK{cik}/us-gaap/{tag}.json"
        picked: dict[str, tuple[str, dict]] = {}
        for e in entries:
            if e.get("form") != "10-K" or not _is_annual(e):
                continue
            end = e["end"]
            filed = e.get("filed", "")
            if end not in picked or filed > picked[end][0]:
                picked[end] = (filed, e)
        if not picked:
            continue
        out: list[Sourced[FinancialFact]] = []
        for end in sorted(picked):
            e = picked[end][1]
            d = date.fromisoformat(end)
            out.append(Sourced(
                FinancialFact(concept=concept, unit=unit, period_end=d,
                              fiscal_year=d.year, value=float(e["val"])),
                primary_api(f"{NAME} {tag}", url, section=f"기간종료 {end} · 10-K"),
            ))
        key = (out[-1].value.period_end.isoformat(), len(out))
        if key > best_key:
            best_out, best_key = out, key
    if best_out:
        return best_out
    return Unavailable(f"{ticker} {concept}", f"XBRL 태그 미발견: {tags}")


#: 최소 연도 수. 이보다 적으면 시계열 판단이 불가능하다.
FCF_MIN_YEARS = 3


def free_cash_flow(ticker: str) -> list[Sourced[FinancialFact]] | Unavailable:
    """FCF = 영업현금흐름 − CapEx. 두 계열 모두 있는 연도만 반환한다.

    **최신 연도가 빠지면 조용히 넘기지 않는다.** CapEx 태그가 회사마다 달라서,
    맞지 않는 태그를 잡으면 옛 연도 몇 개만 겹쳐 그럴듯한 오답이 나온다
    (NVDA 가 FY2010~2012 만 반환한 실제 사례).

    OCF 전체 이력과 비교하는 건 무의미하다 — CapEx 태그는 도입 시점이 늦은 경우가 많다.
    검사해야 할 것은 **가장 최근 OCF 기간이 교집합에 들어있는가** 다.
    """
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
    if not out:
        return Unavailable(f"{ticker} FreeCashFlow", "OCF/CapEx 연도 교집합 없음")
    if out[-1].value.period_end != ocf[-1].value.period_end:
        return Unavailable(
            f"{ticker} FreeCashFlow",
            f"최신 OCF({ocf[-1].value.period_end})가 CapEx 와 겹치지 않는다 — "
            f"CapEx 태그 불일치로 보인다. 교집합 최신은 {out[-1].value.period_end}. "
            f"CONCEPTS['CapEx'] 후보 확인 필요")
    if len(out) < FCF_MIN_YEARS:
        return Unavailable(
            f"{ticker} FreeCashFlow",
            f"{len(out)}개년만 확보 (최소 {FCF_MIN_YEARS}년 필요) — 시계열 판단 불가")
    return out


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


@dataclass(frozen=True)
class NetDebt:
    """순부채 = 총차입 − (현금 + 단기투자). 역DCF 의 기업가치 산출에 필요하다.

    long_term_investments 는 기본적으로 제외한다 — 장기 유가증권은 즉시 상환에
    쓸 수 있다고 보기 어렵다. 다만 애플처럼 규모가 크면 결과를 크게 바꾸므로
    함께 표기해 판단 재료로 남긴다.
    """

    total_debt: float
    cash: float
    short_term_investments: float
    long_term_investments: float
    as_of: date

    @property
    def value(self) -> float:
        return self.total_debt - self.cash - self.short_term_investments

    @property
    def value_incl_lt(self) -> float:
        return self.value - self.long_term_investments

    @property
    def is_net_cash(self) -> bool:
        return self.value < 0

    def __str__(self) -> str:
        sign = "순현금" if self.is_net_cash else "순부채"
        return (f"{sign} {abs(self.value)/1e9:,.1f}B "
                f"(차입 {self.total_debt/1e9:,.1f} − 현금 {self.cash/1e9:,.1f} "
                f"− 단기투자 {self.short_term_investments/1e9:,.1f})")


def net_debt(ticker: str) -> Sourced[NetDebt] | Unavailable:
    """최신 회계연도 기준 순부채. 하나라도 못 구하면 0 으로 채우지 않고 그 사실을 남긴다."""
    parts: dict[str, tuple[float, date] | None] = {}
    missing: list[str] = []
    for key in ("TotalDebt", "ShortTermDebt", "Cash",
                "ShortTermInvestments", "LongTermInvestments"):
        r = annual_series(ticker, key)
        if isinstance(r, Unavailable):
            parts[key] = None
            if key in ("TotalDebt", "Cash"):
                missing.append(key)
        else:
            parts[key] = (r[-1].value.value, r[-1].value.period_end)
    if missing:
        return Unavailable(f"{ticker} 순부채", f"필수 항목 미확보: {', '.join(missing)}")

    def v(k: str) -> float:
        return parts[k][0] if parts.get(k) else 0.0

    as_of = parts["Cash"][1]
    nd = NetDebt(total_debt=v("TotalDebt") + v("ShortTermDebt"), cash=v("Cash"),
                 short_term_investments=v("ShortTermInvestments"),
                 long_term_investments=v("LongTermInvestments"), as_of=as_of)
    note = "장기투자 미확보" if not parts.get("LongTermInvestments") else ""
    return Sourced(nd, primary_api(
        f"{NAME} 순부채 (LongTermDebt+CommercialPaper−Cash−단기투자, 파생)",
        f"{BASE}/api/xbrl/companyfacts/CIK{cik_for(ticker)}.json",
        section=f"기간종료 {as_of} · 10-K{' · ' + note if note else ''}"))


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
