"""1차 출처: SEC 렌더링 재무제표(R-file)에서 세그먼트·지역·제품별 매출을 추출한다. 키 불필요.

## 왜 별도 모듈인가
`companyfacts` 는 차원(dimension) 축을 제거한 값만 준다 — 사업부문별·지역별 수치가 없다.
SEC 는 각 파일링마다 **렌더링된 재무제표 HTML(R*.htm)** 을 함께 배포하는데,
여기에는 차원별 상세가 표로 들어있다. `FilingSummary.xml` 이 그 목록이다.

    FilingSummary.xml → <Report><ShortName>…Segment…</ShortName><HtmlFileName>R68.htm</…>
    R68.htm → 부문별 매출·영업이익 3개년 표

## 구조
R-file 표는 "구분 행(값 없음) → 항목 행(값 있음)" 이 반복되는 형태다.
    Americas                         ← 구분(세그먼트명)
    Segment Reporting Line Items     ← 잡음
    Net sales | 167,045 | 155,509    ← 값
따라서 "값 없는 행"을 만나면 현재 그룹명을 갱신하고, 값 있는 행을 그 그룹에 귀속시킨다.
"""

from __future__ import annotations

import html as _html
import re
from dataclasses import dataclass, field
from datetime import date

import requests

from ..models import Filing
from ..provenance import Sourced, Unavailable, primary_api
from ._http import CACHE_DIR, SourceUnavailable, throttle
from .sec_edgar import MIN_INTERVAL, _headers, annual_filings

NAME = "SEC 렌더링 재무제표"

#: 리포트 선택 규칙 — (키, ShortName 정규식, 제외 정규식)
#: 회사마다 표 제목이 다르다. 실측으로 모은 패턴 (AAPL·MSFT·NVDA 확인).
REPORT_PATTERNS: tuple[tuple[str, str, str], ...] = (
    ("segment",
     r"(?i)(segment.*(reportable|information by)"
     r"|segment revenue.*operating income"
     r"|reportable segments)",
     r"(?i)\(tables\)|policies|narrative|reconcil|unearned|parenthetical"),
    ("product",
     r"(?i)(disaggregat|by (significant )?product|revenue by market)",
     r"(?i)\(tables\)|policies|parenthetical|unearned"),
    ("geography",
     r"(?i)(by (major )?geographic|countries that|by region)",
     r"(?i)\(tables\)|policies|long-lived assets only|parenthetical"),
)

#: 실제 수치는 "(Details)" 리포트에 있다. 같은 이름의 본문 주석(텍스트)을 잡으면 안 된다.
_DETAILS = re.compile(r"(?i)\(details\)")

#: 값 행에서 골라낼 항목명
LINE_ALIASES = {
    "revenue": ("net sales", "revenue", "revenues", "total revenues", "net revenue"),
    "operating_income": ("operating income", "operating income (loss)", "segment operating income"),
}
_NOISE = re.compile(r"(?i)line items|\[abstract\]|^x$|^\+ details|^reference|^no definition")


#: 총계로 보이는 그룹 라벨. 구조 판정(합계 근사)과 함께 쓴다.
_TOTAL_LABELS = re.compile(
    r"(?i)^(total|operating segments?|segment reporting|consolidated|__total__|"
    r"reportable segments?|all other)\b")
#: 그룹 합계와 이 비율 이내로 같으면 총계 행으로 본다.
TOTAL_TOLERANCE = 0.02


@dataclass
class SegmentTable:
    kind: str                     # segment | product | geography
    periods: list[str]            # 표 헤더의 기간
    rows: dict[str, dict[str, float]]   # {그룹: {항목: 값}} — 최신 기간 기준
    source_file: str
    unit_scale: float = 1.0       # "$ in Millions" → 1e6

    @property
    def revenue(self) -> dict[str, float]:
        return {g: v["revenue"] * self.unit_scale
                for g, v in self.rows.items() if "revenue" in v}

    @property
    def operating_income(self) -> dict[str, float]:
        return {g: v["operating_income"] * self.unit_scale
                for g, v in self.rows.items() if "operating_income" in v}

    def _total_keys(self) -> set[str]:
        """총계 행을 찾는다.

        SEC 표는 총계를 별도 그룹처럼 렌더한다("Operating Segments", "Total").
        이걸 그룹으로 세면 비중이 반토막 난다 — NVDA 가 정확히 그랬다.

        판정 기준은 **표 맨 위의 미분류 총계(`__total__`)** 다.
        "나머지 합과 비슷하면 총계"라는 휴리스틱은 위험하다 —
        애플 iPhone(209.6B)이 나머지 합(206.6B)과 2% 이내라 총계로 오판됐다.
        지배적 단일 그룹이 있는 회사에서 반드시 터진다.
        """
        rev = {g: v["revenue"] * self.unit_scale
               for g, v in self.rows.items() if "revenue" in v}
        totals = {g for g in rev if _TOTAL_LABELS.match(g)}
        ref = rev.get("__total__")
        if ref:
            for g, v in rev.items():
                if g != "__total__" and v > 0 and abs(v - ref) / ref <= TOTAL_TOLERANCE:
                    totals.add(g)
        return totals

    @property
    def total_revenue(self) -> float | None:
        rev = {g: v["revenue"] * self.unit_scale
               for g, v in self.rows.items() if "revenue" in v}
        tk = self._total_keys()
        if tk:
            return max(rev[g] for g in tk)
        return sum(rev.values()) or None

    def shares(self) -> list[tuple[str, float, float | None, float | None]]:
        """(그룹, 매출, 매출비중, 영업이익률). 총계 행은 제외하고 비중을 낸다."""
        rev, oi = self.revenue, self.operating_income
        skip = self._total_keys()
        parts = {g: v for g, v in rev.items() if g not in skip}
        total = sum(parts.values())
        return [(g, v, (v / total if total else None),
                 (oi[g] / v if g in oi and v else None))
                for g, v in sorted(parts.items(), key=lambda kv: -kv[1])]


@dataclass
class SegmentReport:
    ticker: str
    fiscal_year: int
    tables: dict[str, SegmentTable] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    filing_url: str = ""


def _num(s: str) -> float | None:
    """'$ 109,158' · '(220,960)' 를 숫자로.

    SEC 렌더링은 각 그룹의 첫 값 행에 `$` 를 붙인다. 기호 제거 후 **다시 strip 해야**
    선행 공백이 남지 않는다. 이걸 놓치면 그룹마다 첫 행이 통째로 누락되어
    (애플의 경우 Services 109B 와 총계 416B) 비중이 전부 틀어진다.
    """
    t = s.strip().replace("$", "").replace(",", "").replace("%", "").strip()
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()").strip()
    if not re.fullmatch(r"-?\d+(\.\d+)?", t):
        return None
    v = float(t)
    return -v if neg else v


def _fetch(url: str, cache_name: str) -> str:
    dest = CACHE_DIR / "sec_r" / cache_name
    if dest.exists():
        return dest.read_text(encoding="utf-8", errors="replace")
    throttle("www.sec.gov", MIN_INTERVAL)
    try:
        r = requests.get(url, headers=_headers(), timeout=40)
        r.raise_for_status()
    except requests.RequestException as exc:
        raise SourceUnavailable(str(exc)) from exc
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(r.text, encoding="utf-8")
    return r.text


#: SEC 렌더링은 축 이름을 그룹 라벨에 붙인다 — "Americas | Operating segments".
_AXIS_SUFFIX = re.compile(
    r"(?i)\s*\|\s*(operating segments?|reportable segments?|segments?|"
    r"product(s| and service.*)?|geographical?( areas?)?|consolidation.*)\s*$")


def _clean_group(label: str) -> str:
    return _AXIS_SUFFIX.sub("", label).strip()


def _table_rows(html_text: str) -> list[list[str]]:
    out = []
    for tr in re.findall(r"(?is)<tr[^>]*>(.*?)</tr>", html_text):
        cells = [re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", "", c))).strip()
                 for c in re.findall(r"(?is)<t[dh][^>]*>(.*?)</t[dh]>", tr)]
        cells = [c for c in cells if c]
        if cells:
            out.append(cells)
    return out


def _parse(rows: list[list[str]], kind: str, fname: str) -> SegmentTable | None:
    if not rows:
        return None
    header = " ".join(rows[0])
    scale = 1e6 if re.search(r"(?i)in millions", header) else (
        1e9 if re.search(r"(?i)in billions", header) else
        1e3 if re.search(r"(?i)in thousands", header) else 1.0)
    periods = [c for c in (rows[1] if len(rows) > 1 else []) if re.search(r"\d{4}", c)]

    groups: dict[str, dict[str, float]] = {}
    current = "__total__"
    for cells in rows[2:]:
        label = _clean_group(cells[0])
        if _NOISE.search(label):
            continue
        values = [v for v in (_num(c) for c in cells[1:]) if v is not None]
        if not values:
            if len(label) < 70 and not label.startswith("("):
                current = label          # 구분 행 → 그룹 전환
            continue
        low = label.lower().rstrip(":")
        for key, names in LINE_ALIASES.items():
            if low in names:
                groups.setdefault(current, {}).setdefault(key, values[0])
                break
    return SegmentTable(kind, periods, groups, fname, scale) if groups else None


def fetch(ticker: str, filing: Filing | None = None) -> SegmentReport | Unavailable:
    """최신 10-K 의 세그먼트·제품·지역 표를 가져온다."""
    if filing is None:
        fs = annual_filings(ticker, limit=1)
        if isinstance(fs, Unavailable):
            return fs
        filing = fs[0]
    if not (filing.accession and filing.cik):
        return Unavailable(f"{ticker} 세그먼트", "accession/cik 미확보")

    base = (f"https://www.sec.gov/Archives/edgar/data/"
            f"{int(filing.cik)}/{filing.accession.replace('-', '')}")
    try:
        summary = _fetch(f"{base}/FilingSummary.xml", f"{ticker}_{filing.fiscal_year}_summary.xml")
    except SourceUnavailable as exc:
        return Unavailable(f"{ticker} 세그먼트", f"FilingSummary 미확보: {exc}")

    reports = []
    for rep in re.findall(r"(?s)<Report[^>]*>(.*?)</Report>", summary):
        nm = re.search(r"<ShortName>(.*?)</ShortName>", rep, re.S)
        fn = re.search(r"<HtmlFileName>(.*?)</HtmlFileName>", rep, re.S)
        if nm and fn:
            reports.append((_html.unescape(nm.group(1)).strip(), fn.group(1).strip()))

    rep_out = SegmentReport(ticker.upper(), filing.fiscal_year, filing_url=base)
    used: set[str] = set()
    for key, want, skip in REPORT_PATTERNS:
        cands = [(n, f) for n, f in reports
                 if re.search(want, n) and not re.search(skip, n)]
        # (Details) 를 최우선. 없으면 나머지 중 첫 번째.
        hit = next((f for n, f in cands if _DETAILS.search(n) and f not in used),
                   next((f for _, f in cands if f not in used), None))
        if hit:
            used.add(hit)
        if not hit:
            rep_out.notes.append(f"{key} 표 미발견 — 이 회사는 해당 공시를 하지 않을 수 있다")
            continue
        try:
            body = _fetch(f"{base}/{hit}", f"{ticker}_{filing.fiscal_year}_{hit}")
        except SourceUnavailable as exc:
            rep_out.notes.append(f"{key} 표 수신 실패: {exc}")
            continue
        t = _parse(_table_rows(body), key, hit)
        if t:
            rep_out.tables[key] = t
        else:
            rep_out.notes.append(f"{key} 표 파싱 실패 ({hit}) — 형식 확인 필요")
    if not rep_out.tables:
        return Unavailable(f"{ticker} 세그먼트",
                           "세그먼트/제품/지역 표를 하나도 얻지 못했다. " + " / ".join(rep_out.notes))
    return rep_out


def sourced(report: SegmentReport, table_key: str) -> Sourced[SegmentTable] | Unavailable:
    t = report.tables.get(table_key)
    if not t:
        return Unavailable(f"{report.ticker} {table_key}", "표 없음")
    return Sourced(t, primary_api(
        f"{NAME} {table_key} (FY{report.fiscal_year})",
        f"{report.filing_url}/{t.source_file}",
        section=f"10-K FY{report.fiscal_year} · {t.source_file} · 기간 {', '.join(t.periods[:3])}"))


if __name__ == "__main__":
    import sys
    tk = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    r = fetch(tk)
    if isinstance(r, Unavailable):
        print(r); sys.exit(1)
    print(f"── {r.ticker} FY{r.fiscal_year} ──")
    for k, t in r.tables.items():
        print(f"\n  [{k}] {t.source_file} · 기간 {t.periods[:3]} · 단위 x{t.unit_scale:,.0f}")
        for g, rev, share, m in t.shares()[:8]:
            print(f"    {g[:34]:36s} {rev/1e9:9,.1f}B "
                  + (f"{share:6.1%}" if share else "     —")
                  + (f"  영업이익률 {m:6.1%}" if m is not None else ""))
    for n in r.notes:
        print(f"  ⚠ {n}")
