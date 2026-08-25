"""1차 출처: 금융감독원 OpenDART (한국 공시·재무). 무료 키 필요.

발급: https://opendart.fss.or.kr/ → 인증키 신청/관리 (이메일 인증만, 즉시 발급)
`.env` 의 `OPENDART_API_KEY` 에 넣는다. 일 20,000건.

## SEC 와 다른 점
- 종목코드가 아니라 **corp_code**(8자리 고유번호)로 조회한다. corpCode.xml(ZIP)로 매핑한다.
- 계정 이름이 한글이고 회사마다 표기가 다르다 → `_ACCOUNT_ALIASES` 로 흡수한다.
- 각 항목이 당기/전기/전전기 3개년을 한꺼번에 담고 있다 → 한 번 호출로 3년치가 나온다.
- 연결(CFS)이 없는 회사가 있어 별도(OFS)로 폴백한다.
"""

from __future__ import annotations

import io
import os
import zipfile
from datetime import date, timedelta
from xml.etree import ElementTree as ET

import requests

from ..models import CorporateAction, Filing, FinancialFact, Market
from ..provenance import Sourced, Unavailable, primary_api
from ._http import CACHE_DIR, SourceUnavailable, get_json, require_env, throttle

BASE = "https://opendart.fss.or.kr/api"
NAME = "OpenDART"
MARKET = Market.KR
MIN_INTERVAL = 0.2

#: 정기보고서 코드
REPORT_ANNUAL = "11011"      # 사업보고서
REPORT_H1 = "11012"          # 반기
REPORT_Q1, REPORT_Q3 = "11013", "11014"

#: 개념 → 계정명 후보(순서대로 시도) + 재무제표 구분.
#: 회사마다 표기가 달라 별칭이 필요하다. sj_div 를 함께 걸어 동명이인 계정을 막는다.
_ACCOUNT_ALIASES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "Revenues":           (("매출액", "수익(매출액)", "영업수익", "매출"), ("IS", "CIS")),
    "OperatingIncome":    (("영업이익", "영업이익(손실)"), ("IS", "CIS")),
    "NetIncome":          (("당기순이익", "당기순이익(손실)", "연결당기순이익"), ("IS", "CIS")),
    "OperatingCashFlow":  (("영업활동현금흐름", "영업활동으로인한현금흐름",
                            "영업활동으로 인한 현금흐름"), ("CF",)),
    "CapEx":              (("유형자산의 취득", "유형자산의취득"), ("CF",)),
    "TotalAssets":        (("자산총계",), ("BS",)),
    "TotalEquity":        (("자본총계",), ("BS",)),
}

_corp_index: dict[str, dict] | None = None


def _key() -> str:
    return require_env("OPENDART_API_KEY",
                       "https://opendart.fss.or.kr/ 에서 무료 발급 (이메일 인증만)")


def corp_index(force: bool = False) -> dict[str, dict] | Unavailable:
    """종목코드 → {corp_code, corp_name}. ZIP 안의 XML 이라 별도로 처리한다."""
    global _corp_index
    if _corp_index is not None and not force:
        return _corp_index
    cache = CACHE_DIR / "opendart" / "CORPCODE.xml"
    try:
        if cache.exists() and not force:
            raw = cache.read_bytes()
        else:
            throttle("opendart.fss.or.kr", MIN_INTERVAL)
            r = requests.get(f"{BASE}/corpCode.xml", params={"crtfc_key": _key()}, timeout=90)
            r.raise_for_status()
            if r.content[:2] != b"PK":
                return Unavailable("DART 고유번호", f"ZIP 아님 — {r.text[:120]}")
            raw = zipfile.ZipFile(io.BytesIO(r.content)).read("CORPCODE.xml")
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(raw)
    except (SourceUnavailable, requests.RequestException, zipfile.BadZipFile, KeyError) as exc:
        return Unavailable("DART 고유번호", f"{NAME}: {exc}")

    idx: dict[str, dict] = {}
    for e in ET.fromstring(raw).iter("list"):
        sc = (e.findtext("stock_code") or "").strip()
        if sc:
            idx[sc] = {"corp_code": (e.findtext("corp_code") or "").strip(),
                       "corp_name": (e.findtext("corp_name") or "").strip()}
    _corp_index = idx
    return idx


def corp_code(ticker: str) -> str | Unavailable:
    idx = corp_index()
    if isinstance(idx, Unavailable):
        return idx
    row = idx.get(ticker.strip())
    if not row:
        return Unavailable(f"{ticker} 고유번호", "DART 상장사 목록에 없다 (6자리 종목코드 필요)")
    return row["corp_code"]


def corp_name(ticker: str) -> str:
    idx = corp_index()
    return "" if isinstance(idx, Unavailable) else (idx.get(ticker, {}) or {}).get("corp_name", "")


def _statements(cc: str, year: int, report: str = REPORT_ANNUAL) -> list[dict] | Unavailable:
    """연결(CFS) 우선, 없으면 별도(OFS)."""
    last = None
    for fs_div in ("CFS", "OFS"):
        try:
            d = get_json(f"{BASE}/fnlttSinglAcntAll.json",
                         params={"crtfc_key": _key(), "corp_code": cc, "bsns_year": str(year),
                                 "reprt_code": report, "fs_div": fs_div},
                         cache_key=f"fs_{cc}_{year}_{report}_{fs_div}",
                         ttl_sec=2_592_000, min_interval=MIN_INTERVAL)
        except SourceUnavailable as exc:
            return Unavailable(f"{cc} {year} 재무제표", f"{NAME}: {exc}")
        if d.get("status") == "000":
            return d.get("list", [])
        last = f"{d.get('status')} {d.get('message', '')}"
    return Unavailable(f"{cc} {year} 재무제표", f"{NAME}: {last}")


def _amount(v) -> float | None:
    if v in (None, "", "-"):
        return None
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return None


def annual_series(ticker: str, concept: str, years: int = 3
                  ) -> list[Sourced[FinancialFact]] | Unavailable:
    """연도별 값. 각 항목이 당기·전기·전전기를 담고 있어 1회 호출로 3년이 나온다.

    회계연도 라벨은 사업연도(bsns_year) 기준이다.
    """
    alias = _ACCOUNT_ALIASES.get(concept)
    if not alias:
        return Unavailable(f"{ticker} {concept}", f"{NAME} 계정 매핑 없음: {concept}")
    names, divs = alias
    cc = corp_code(ticker)
    if isinstance(cc, Unavailable):
        return cc

    latest = date.today().year - 1
    rows = None
    for y in range(latest, latest - 3, -1):
        r = _statements(cc, y)
        if not isinstance(r, Unavailable) and r:
            rows, base_year = r, y
            break
    if rows is None:
        return Unavailable(f"{ticker} {concept}", f"{NAME}: 최근 3개년 사업보고서 조회 실패")

    hit = next((a for a in rows
                if (a.get("account_nm") or "").replace(" ", "") in
                   {n.replace(" ", "") for n in names}
                and (a.get("sj_div") or "") in divs), None)
    if hit is None:
        return Unavailable(f"{ticker} {concept}",
                           f"{NAME}: 계정 미발견 {names} (표기가 다를 수 있음)")

    url = (f"{BASE}/fnlttSinglAcntAll.json?corp_code={cc}"
           f"&bsns_year={base_year}&reprt_code={REPORT_ANNUAL}")
    out: list[Sourced[FinancialFact]] = []
    for offset, field in ((0, "thstrm_amount"), (1, "frmtrm_amount"), (2, "bfefrmtrm_amount")):
        v = _amount(hit.get(field))
        if v is None:
            continue
        fy = base_year - offset
        out.append(Sourced(
            FinancialFact(concept, "KRW", date(fy, 12, 31), fy, v),
            primary_api(f"{NAME} {hit.get('account_nm')}", url,
                        section=f"{base_year} 사업보고서 · {hit.get('sj_nm')} · "
                                f"{hit.get(field.replace('_amount', '_nm')) or f'FY{fy}'} · "
                                f"접수번호 {hit.get('rcept_no')}"),
        ))
    out.sort(key=lambda s: s.value.fiscal_year)
    return out[-years:] if out else Unavailable(f"{ticker} {concept}", f"{NAME}: 금액 없음")


def free_cash_flow(ticker: str, years: int = 3) -> list[Sourced[FinancialFact]] | Unavailable:
    """FCF = 영업활동현금흐름 − 유형자산의 취득.

    한국 공시의 CapEx 표기는 회사마다 편차가 커서 미확보 시 그 사실을 반환한다.
    """
    ocf = annual_series(ticker, "OperatingCashFlow", years)
    if isinstance(ocf, Unavailable):
        return ocf
    capex = annual_series(ticker, "CapEx", years)
    if isinstance(capex, Unavailable):
        return Unavailable(f"{ticker} FreeCashFlow",
                           f"CapEx 미확보 — {capex.reason[:80]}")
    cx = {s.value.fiscal_year: s for s in capex}
    out = []
    for s in ocf:
        fy = s.value.fiscal_year
        if fy not in cx:
            continue
        out.append(Sourced(
            FinancialFact("FreeCashFlow", "KRW", s.value.period_end, fy,
                          s.value.value - cx[fy].value.value),
            primary_api(f"{NAME} FCF=영업활동현금흐름−유형자산의취득 (파생)",
                        s.source.locator.url or "",
                        section=f"FY{fy} 사업보고서 · 파생값"),
        ))
    return out or Unavailable(f"{ticker} FreeCashFlow", "연도 교집합 없음")


def annual_filings(ticker: str, limit: int = 3) -> list[Filing] | Unavailable:
    """사업보고서 목록. 공시 원문 URL 은 rcept_no 로 구성한다."""
    cc = corp_code(ticker)
    if isinstance(cc, Unavailable):
        return cc
    end = date.today()
    try:
        d = get_json(f"{BASE}/list.json",
                     params={"crtfc_key": _key(), "corp_code": cc,
                             "bgn_de": f"{end.year - 6}0101", "end_de": end.strftime("%Y%m%d"),
                             "pblntf_ty": "A", "page_count": "100"},
                     cache_key=f"list_{cc}", ttl_sec=86_400, min_interval=MIN_INTERVAL)
    except SourceUnavailable as exc:
        return Unavailable(f"{ticker} 사업보고서 목록", f"{NAME}: {exc}")
    if d.get("status") != "000":
        return Unavailable(f"{ticker} 사업보고서 목록",
                           f"{NAME}: {d.get('status')} {d.get('message', '')}")
    out = []
    for it in d.get("list", []):
        if "사업보고서" not in (it.get("report_nm") or ""):
            continue
        rd = date.fromisoformat(f"{it['rcept_dt'][:4]}-{it['rcept_dt'][4:6]}-{it['rcept_dt'][6:]}")
        out.append(Filing(ticker, "사업보고서", rd.year - 1, rd,
                          accession=it.get("rcept_no"), cik=cc))
        if len(out) >= limit:
            break
    return out or Unavailable(f"{ticker} 사업보고서 목록", "정기공시에 사업보고서 없음")


#: 가격 비교 가능성을 깨는 공시. report_nm 부분일치로 거른다.
#: DART 는 공시유형 코드로 이걸 한 번에 뽑아주지 않는다 — 제목 매칭이 현실적인 방법이다.
ACTION_KEYWORDS = (
    "분할", "병합", "감자", "액면",                      # 주식 수 자체가 바뀌는 것
    "거래정지", "정리매매", "상장폐지", "상장적격성", "관리종목",  # 거래가 끊겼던 것
    "무상증자", "유상증자",                              # 권리락으로 기준가가 바뀌는 것
)

ACTION_LOOKBACK_DAYS = 120


def corporate_actions(ticker: str, days: int = ACTION_LOOKBACK_DAYS
                      ) -> Sourced[list[CorporateAction]] | Unavailable:
    """최근 `days` 일 기업행위 공시. 급등락 이상치의 원인을 **확정**하는 1차 근거.

    `core/anomalies.py` 는 일봉만 보고 정황까지만 만든다. 정지였는지 분할이었는지,
    아니면 실제 급락이었는지는 여기서 갈린다 — 정황과 확정을 섞지 않는다.

    빈 리스트도 정상 응답이다. "해당 기간 기업행위 공시 없음" 이라는 사실이기 때문이다.
    """
    cc = corp_code(ticker)
    if isinstance(cc, Unavailable):
        return cc
    end = date.today()
    bgn = end - timedelta(days=max(1, days))
    params = {"crtfc_key": _key(), "corp_code": cc,
              "bgn_de": bgn.strftime("%Y%m%d"), "end_de": end.strftime("%Y%m%d"),
              "page_count": "100"}
    try:
        d = get_json(f"{BASE}/list.json", params=params,
                     cache_key=f"actions_{cc}_{bgn:%Y%m%d}", ttl_sec=21_600,
                     min_interval=MIN_INTERVAL)
    except SourceUnavailable as exc:
        return Unavailable(f"{ticker} 기업행위 공시", f"{NAME}: {exc}")
    if d.get("status") == "013":       # 조회 결과 없음 — 오류가 아니라 사실이다
        return Sourced([], _action_source(ticker, cc, bgn, end))
    if d.get("status") != "000":
        return Unavailable(f"{ticker} 기업행위 공시",
                           f"{NAME}: {d.get('status')} {d.get('message', '')}")

    out: list[CorporateAction] = []
    for it in d.get("list", []):
        nm = (it.get("report_nm") or "").strip()
        if not any(k in nm for k in ACTION_KEYWORDS):
            continue
        raw = it.get("rcept_dt") or ""
        if len(raw) != 8:
            continue
        out.append(CorporateAction(
            ticker.upper(),
            date.fromisoformat(f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"),
            " ".join(nm.split()),          # DART 제목은 공백이 여러 칸 들어 있다
            it.get("rcept_no")))
    out.sort(key=lambda x: x.filed_on, reverse=True)
    return Sourced(out, _action_source(ticker, cc, bgn, end))


def _action_source(ticker: str, cc: str, bgn: date, end: date):
    return primary_api(
        f"{NAME} 공시목록 {ticker}",
        f"https://opendart.fss.or.kr/api/list.json?corp_code={cc}"
        f"&bgn_de={bgn:%Y%m%d}&end_de={end:%Y%m%d}",
        section=f"{bgn.isoformat()}~{end.isoformat()} 기업행위 키워드 매칭")


def filing_viewer_url(filing: Filing) -> str:
    return f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={filing.accession}"


if __name__ == "__main__":
    import sys
    tk = sys.argv[1] if len(sys.argv) > 1 else "005930"
    print(f"── {tk} {corp_name(tk)} ──")
    for c in ("Revenues", "OperatingIncome", "NetIncome"):
        r = annual_series(tk, c)
        if isinstance(r, Unavailable):
            print(f"  {c:18s} {r.reason[:70]}")
        else:
            print(f"  {c:18s} " + "  ".join(
                f"FY{s.value.fiscal_year}:{s.value.value/1e12:.1f}조" for s in r))
    f = free_cash_flow(tk)
    print("  FCF               " + (f.reason[:70] if isinstance(f, Unavailable)
          else "  ".join(f"FY{s.value.fiscal_year}:{s.value.value/1e12:.1f}조" for s in f)))
    fs = annual_filings(tk, 3)
    if not isinstance(fs, Unavailable):
        for x in fs:
            print(f"  사업보고서 FY{x.fiscal_year} 접수 {x.filed_on} {filing_viewer_url(x)}")
