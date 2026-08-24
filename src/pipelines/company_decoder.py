"""기업 해독기 (2층). "이 회사 뭐 하는 회사야?" 를 한 장으로.

파이프라인은 **검증된 사실만** 조립한다 — 출처 붙은 숫자, 원문 발췌, 못 구한 항목 목록.
돈 버는 구조 다이어그램·업종별 지표 선택 같은 해석은 `.claude/skills/company-decoder`
의 절차를 따라 Claude 가 이 산출물 위에서 수행한다.

`python3 -m src.pipelines.company_decoder AAPL`
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from ..core.narrative.sections import US_ITEMS, split_us_items
from ..core.valuation.fundamentals import (fcf_yield, margin, per_share, streak, yoy)
from ..core.valuation.outliers import detect, normalized_base
from ..models import AssetType, Market
from ..provenance import Sourced, Unavailable, record
from ..sources import open_dart, prices, sec_edgar, toss

REPORT_DIR = Path("reports/cards")

#: 세그먼트·지역 매출 언급을 찾는 단서. companyfacts 에 차원 축이 없어 본문에서 찾는다.
SEGMENT_TERMS = ("reportable segment", "operating segment", "revenue by",
                 "segment revenue", "geographic", "by region", "disaggregat")
#: 위 단서를 포함하는 문장 하나를 통째로 잡는다.
SEGMENT_SENTENCE = re.compile(
    r"[^.]{0,200}(?:" + "|".join(SEGMENT_TERMS) + r")[^.]{0,200}\.", re.I)


@dataclass
class CompanyCard:
    ticker: str
    on: date
    market: Market
    asset_type: AssetType
    info: dict
    series: dict[str, list[Sourced]]
    fcf: list[Sourced] | Unavailable
    outliers: list
    shares: Sourced | Unavailable
    price: Sourced | Unavailable
    market_cap: Sourced | Unavailable
    business_excerpt: str
    segment_hints: list[str]
    filing_cite: str
    notes: list[str] = field(default_factory=list)


def _vals(rows) -> list[tuple[str, float]]:
    return [(f"FY{s.value.fiscal_year}", s.value.value) for s in rows]


def run(ticker: str, on: date | None = None) -> CompanyCard | Unavailable:
    on = on or date.today()
    tk = ticker.upper()
    market = Market.KR if tk.isdigit() else Market.US
    notes: list[str] = []

    # 기본 정보 (토스는 KR·US 둘 다 준다)
    info_s = toss.stock_info([tk])
    info = {} if isinstance(info_s, Unavailable) else info_s.value.get(tk, {})
    if not info:
        notes.append("종목 기본 정보 미확보 — 이름·시장·주식수를 심볼로만 표기")

    sec_type = (info.get("security_type") or "").upper()
    asset_type = AssetType.INDEX_ETF if sec_type == "ETF" else AssetType.SINGLE_STOCK
    if not asset_type.supports_reverse_dcf:
        notes.append(f"{tk} 는 {asset_type.value} 다 — 개별 펀더멘털이 아니라 "
                     f"{'/'.join(asset_type.alt_metrics)} 로 봐야 한다. 콕핏을 쓰라")

    # 재무 골격
    series: dict[str, list[Sourced]] = {}
    fcf: list[Sourced] | Unavailable = Unavailable(f"{tk} FCF", "미조회")
    outliers: list = []
    shares: Sourced | Unavailable = Unavailable(f"{tk} 주식수", "미조회")
    business, hints, cite = "", [], ""

    if market is Market.US:
        for concept in ("Revenues", "NetIncome", "OperatingCashFlow", "CapEx"):
            r = sec_edgar.annual_series(tk, concept)
            if isinstance(r, Unavailable):
                notes.append(f"{concept} 미확보 — {r.reason[:60]}")
            else:
                series[concept] = r
                record(r[-1], subject=f"해독기 {tk} {concept}")
        fcf = sec_edgar.free_cash_flow(tk)
        if not isinstance(fcf, Unavailable):
            outliers = detect(_vals(fcf))
        sh = sec_edgar.annual_series(tk, "SharesOutstanding")
        # 타입을 Sourced[float] 로 통일한다 — 한국 경로(토스)와 형태를 맞춰야
        # 아래 시가총액 계산과 렌더가 분기 없이 돈다.
        shares = (sh[-1].map(lambda f: f.value) if not isinstance(sh, Unavailable) else sh)

        fs = sec_edgar.annual_filings(tk, limit=1)
        if isinstance(fs, Unavailable):
            notes.append(f"10-K 미확보 — {fs.reason[:60]}")
        else:
            t = sec_edgar.filing_text(fs[0])
            if isinstance(t, Unavailable):
                notes.append(f"10-K 본문 미확보 — {t.reason[:60]}")
            else:
                cite = t.cite()
                items = split_us_items(t.value)
                if "1" in items:
                    body = re.sub(r"\s+", " ", items["1"])
                    business = body[:2400]
                    seen = set()
                    hints = []
                    for m in SEGMENT_SENTENCE.findall(body):
                        h = re.sub(r"\s+", " ", m).strip()
                        if len(h) > 40 and h not in seen:
                            seen.add(h); hints.append(h[:240])
                        if len(hints) >= 6:
                            break
                else:
                    notes.append("10-K Item 1(Business) 섹션을 분리하지 못했다")
        notes.append("사업부문×지역 매출 분해는 companyfacts 에 차원 축이 없어 "
                     "본문 단서만 제시한다 — 정확한 수치는 10-K 세그먼트 주석에서 확인 필요")
    else:
        # 한국: OpenDART. 각 항목이 3개년을 담고 있어 1회 호출로 시계열이 나온다.
        for concept in ("Revenues", "OperatingIncome", "NetIncome"):
            r = open_dart.annual_series(tk, concept)
            if isinstance(r, Unavailable):
                notes.append(f"{concept} 미확보 — {r.reason[:70]}")
            else:
                series[concept] = r
                record(r[-1], subject=f"해독기 {tk} {concept}")
        fcf = open_dart.free_cash_flow(tk)
        if isinstance(fcf, Unavailable):
            notes.append(f"FCF 미확보 — {fcf.reason[:70]}")
        else:
            outliers = detect(_vals(fcf))
        n = info.get("shares_outstanding")
        if n:
            shares = Sourced(float(n), info_s.source)
        fs = open_dart.annual_filings(tk, limit=1)
        if isinstance(fs, Unavailable):
            notes.append(f"사업보고서 목록 미확보 — {fs.reason[:70]}")
        else:
            cite = f"[1차] DART 사업보고서 FY{fs[0].fiscal_year} — {open_dart.filing_viewer_url(fs[0])}"
            notes.append("사업보고서 본문 파싱은 미구현이다 — 위 링크에서 직접 확인하라. "
                         "재무 수치는 정형 API 로 받은 값이다")
        notes.append(Market.KR.transcript_availability
                     + " — 스토리 리더의 어닝콜 축을 쓸 수 없다")

    # 시가총액
    px = prices.last_close(tk)
    if isinstance(px, Unavailable):
        market_cap: Sourced | Unavailable = px
    else:
        if isinstance(shares, Unavailable):
            market_cap = Unavailable(f"{tk} 시가총액", "주식수 미확보")
        else:
            market_cap = prices.market_cap(tk, shares)

    return CompanyCard(tk, on, market, asset_type, info, series, fcf, outliers,
                       shares, px, market_cap, business, hints, cite, notes)


def to_markdown(c: CompanyCard) -> str:
    from ..render.brief import DISCLAIMER, fact_line
    name = c.info.get("name") or c.ticker
    eng = c.info.get("english_name") or ""
    L = [f"# {name} ({c.ticker}) 해독 카드 — {c.on.isoformat()}", "", DISCLAIMER, ""]
    L.append(f"- {eng} · {c.info.get('market', '시장 미상')} · {c.info.get('currency', '')} · "
             f"자산유형 {c.asset_type.value} · 평가 잣대 {c.asset_type.basis.value}")

    L += ["", "## 규모", ""]
    L.append(fact_line("현재가", c.price, "{:,.2f}"))
    L.append(fact_line("시가총액", c.market_cap, "{:,.0f}"))
    L.append(fact_line("주식수", c.shares, "{:,.0f}"))

    if c.series.get("Revenues"):
        rev = _vals(c.series["Revenues"])
        ni = _vals(c.series.get("NetIncome", []))
        fc = [] if isinstance(c.fcf, Unavailable) else _vals(c.fcf)
        g = dict(yoy(rev)); nm = dict(margin(ni, rev)) if ni else {}
        fm = dict(margin(fc, rev)) if fc else {}
        oi = _vals(c.series.get("OperatingIncome", []))
        om = dict(margin(oi, rev)) if oi else {}
        oid = dict(oi)
        unit, div = ("조", 1e12) if c.market is Market.KR else ("B", 1e9)
        head = "| 회계연도 | 매출 | YoY |"
        sep = "|---|---:|---:|"
        if oi:
            head += " 영업이익 | 영업이익률 |"; sep += "---:|---:|"
        head += " 순이익 | 순이익률 | FCF | FCF 마진 |"
        sep += "---:|---:|---:|---:|"
        L += ["", "## 재무 골격", "", head, sep]
        nid, fcd = dict(ni), dict(fc)
        for k, v in rev[-6:]:
            row = (f"| {k} | {v/div:,.1f}{unit} | "
                   f"{('%+.1f%%' % (g[k]*100)) if g.get(k) is not None else '—'} |")
            if oi:
                row += (f" {(f'{oid[k]/div:,.1f}{unit}' if k in oid else '—')} |"
                        f" {('%.1f%%' % (om[k]*100)) if om.get(k) is not None else '—'} |")
            row += (f" {(f'{nid[k]/div:,.1f}{unit}' if k in nid else '—')} |"
                    f" {('%.1f%%' % (nm[k]*100)) if nm.get(k) is not None else '—'} |"
                    f" {(f'{fcd[k]/div:,.1f}{unit}' if k in fcd else '—')} |"
                    f" {('%.1f%%' % (fm[k]*100)) if fm.get(k) is not None else '—'} |")
            L.append(row)
        L += ["", f"- [사실] 매출 {streak(rev)}"]
        if fc:
            L.append(f"- [사실] FCF {streak(fc)}")
            base = normalized_base(fc)
            if base and not isinstance(c.market_cap, Unavailable):
                y = fcf_yield(base, c.market_cap.value)
                if y is not None:
                    L.append(f"- [사실] FCF 수익률 {y:.2%} "
                             f"(기준 FCF 3년 평균 {base/div:,.1f}{unit})")
        if c.series.get("Revenues") and isinstance(c.shares, Sourced):
            ps = per_share(rev[-1][1], c.shares.value)
            if ps: L.append(f"- [사실] 주당 매출 {ps:,.2f}")

    if c.outliers:
        L += ["", "### 이상치 감지", ""] + [f"- [사실] {o}" for o in c.outliers]

    if c.business_excerpt:
        L += ["", "## 돈 버는 구조 — 10-K Item 1 발췌", "",
              f"> {c.business_excerpt[:1600]}…", "", f"↳ 출처: {c.filing_cite}"]
    if c.segment_hints:
        L += ["", "### 세그먼트·지역 단서 (본문)", ""] + [f"- [사실] {h}" for h in c.segment_hints]

    L += ["", "## 확인 필요", ""] + ([f"- {n}" for n in c.notes] or ["- 없음"])
    L += ["", "## 더 파볼 지점", "",
          "- [해석] 위 수치는 1차 스크리너입니다. 투자 판단에 쓰는 숫자는 원문에서 재확인하십시오.",
          "- 가격 판독기(`price-decoder`)로 이 시가총액이 요구하는 성장률을 역산할 수 있습니다.",
          "- 스토리 리더(`story-reader`)로 최근 공시 문구 변화를 볼 수 있습니다."]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    tk = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    card = run(tk)
    if isinstance(card, Unavailable):
        print(card); sys.exit(1)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORT_DIR / f"{card.ticker}-decoder-{card.on.isoformat()}.md"
    out.write_text(to_markdown(card), encoding="utf-8")
    print(f"✓ {out} ({out.stat().st_size:,} bytes)")
    print(f"  {card.info.get('name', card.ticker)} · {card.market.value} · "
          f"재무 계열 {len(card.series)}종 · 이상치 {len(card.outliers)}건 · "
          f"본문 {len(card.business_excerpt):,}자 · 세그먼트 단서 {len(card.segment_hints)}건")
    for n in card.notes:
        print(f"  ⚠ {n[:100]}")
