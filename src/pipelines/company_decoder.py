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
from ..sources import open_dart, prices, sec_edgar, sec_segments, toss

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
    segments: object | None = None       # SegmentReport | None
    net_debt: object | None = None       # Sourced[NetDebt] | Unavailable | None
    debt_series: list = field(default_factory=list)
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
        # 세그먼트: companyfacts 에 차원 축이 없으므로 SEC 렌더링 재무제표(R-file)에서 가져온다
        seg = sec_segments.fetch(tk, fs[0] if not isinstance(fs, Unavailable) else None)
        if isinstance(seg, Unavailable):
            notes.append(f"세그먼트 표 미확보 — {seg.reason[:90]}")
            seg = None
        else:
            for n in seg.notes:
                notes.append(f"세그먼트: {n}")
        # 순부채 + 총차입 추이
        nd = sec_edgar.net_debt(tk)
        if isinstance(nd, Unavailable):
            notes.append(f"순부채 미확보 — {nd.reason[:70]}")
        dser = sec_edgar.annual_series(tk, "TotalDebt")
        debt_series = [] if isinstance(dser, Unavailable) else dser
        seg = None if 'seg' not in dir() else seg
    else:
        seg = None; nd = Unavailable(f"{tk} 순부채", "한국은 미구현"); debt_series = []
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

    return CompanyCard(ticker=tk, on=on, market=market, asset_type=asset_type,
                       info=info, series=series, fcf=fcf, outliers=outliers,
                       shares=shares, price=px, market_cap=market_cap,
                       business_excerpt=business, segment_hints=hints, filing_cite=cite,
                       notes=notes, segments=seg, net_debt=nd, debt_series=debt_series)


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

    # ── 매출 구성 (세그먼트·제품·지역) ──────────────────────────
    if c.segments and getattr(c.segments, "tables", None):
        from ..sources.sec_segments import sourced as seg_sourced
        titles = {"segment": "사업부문별", "product": "제품·서비스별", "geography": "지역별"}
        L += ["", "## 매출 구성", ""]
        for key, title in titles.items():
            t = c.segments.tables.get(key)
            if not t:
                continue
            sc = seg_sourced(c.segments, key)
            L += [f"### {title}", "", "| 구분 | 매출 | 비중 | 영업이익률 |", "|---|---:|---:|---:|"]
            for g, v, share, m in t.shares():
                L.append(f"| {g} | {v/div:,.1f}{unit} | {share:.1%} | "
                         + (f"{m:.1%} |" if m is not None else "— |"))
            if t.total_revenue:
                L.append(f"| **합계** | **{t.total_revenue/div:,.1f}{unit}** | 100% | — |")
            L += ["", f"↳ 출처: {sc.cite() if not isinstance(sc, Unavailable) else sc.cite()}", ""]
        # 이익 기여 vs 매출 비중의 괴리 — 원본 카드의 핵심 통찰
        st = c.segments.tables.get("segment")
        if st and st.operating_income:
            oi = st.operating_income
            tot_oi = sum(v for g, v in oi.items() if g not in st._total_keys())
            rows = [(g, sh, oi[g] / tot_oi) for g, v, sh, m in st.shares()
                    if g in oi and tot_oi]
            gaps = [(g, sh, os_) for g, sh, os_ in rows if abs(os_ - sh) >= 0.05]
            if gaps:
                L += ["### 매출 비중 vs 이익 기여", ""]
                for g, sh, os_ in sorted(gaps, key=lambda r: -(r[2] - r[1])):
                    L.append(f"- [사실] **{g}** — 매출 {sh:.1%} / 영업이익 {os_:.1%} "
                             f"({os_ - sh:+.1%}p)")
                L.append("- [해석] 매출 비중과 이익 기여가 벌어지는 부문이 실제 돈줄입니다.")
                L.append("")

    # ── 재무 건전성 ─────────────────────────────────────────────
    if c.net_debt is not None and not isinstance(c.net_debt, Unavailable):
        nd = c.net_debt.value
        L += ["## 재무 건전성", "",
              f"- [사실] {nd}  \n  ↳ 출처: {c.net_debt.cite()}"]
        if nd.long_term_investments:
            L.append(f"- [사실] 장기투자 {nd.long_term_investments/div:,.1f}{unit} 포함 시 "
                     f"{'순현금' if nd.value_incl_lt < 0 else '순부채'} "
                     f"{abs(nd.value_incl_lt)/div:,.1f}{unit}")
        if c.debt_series:
            ds = _vals(c.debt_series)[-4:]
            L += ["", "| 회계연도 | 총차입 |", "|---|---:|"]
            L += [f"| {k} | {v/div:,.1f}{unit} |" for k, v in ds]
            if len(ds) >= 2:
                d = (ds[-1][1] - ds[0][1]) / ds[0][1] if ds[0][1] else None
                if d is not None:
                    L.append("")
                    L.append(f"- [사실] {ds[0][0]}→{ds[-1][0]} 총차입 {d:+.1%} "
                             + ("(디레버리징)" if d < -0.02 else "(증가)" if d > 0.02 else "(횡보)"))
        L.append("")

    if c.business_excerpt:
        L += ["", "## 돈 버는 구조 — 10-K Item 1 발췌", "",
              f"> {c.business_excerpt[:1600]}…", "", f"↳ 출처: {c.filing_cite}"]
    if c.segment_hints:
        L += ["", "### 세그먼트·지역 단서 (본문)", ""] + [f"- [사실] {h}" for h in c.segment_hints]

    L += ["", "## 확인 필요 (데이터 미확보)", ""] + ([f"- {n}" for n in c.notes] or ["- 없음"])

    L += ["", "## 이 카드로 답이 안 나온 것 — 다음에 팔 질문", ""]
    L.append("- 최근 분기 추세는? → 이 카드는 연간 10-K 기준. 분기 실적·어닝콜 확인 필요")
    if c.segments and c.segments.tables.get("segment"):
        L.append("- 부문별 이익 추세가 구조적인가 일시적인가? → 3개년 부문 표는 R-file 에 있으나 "
                 "현재 최신 연도만 파싱한다. 다년 비교는 스토리 리더로")
    else:
        L.append("- 사업부문별 매출·이익 분해 → 세그먼트 표를 얻지 못했다. "
                 "10-K 세그먼트 주석을 직접 확인")
    if isinstance(c.fcf, Unavailable) or not c.series.get("Revenues"):
        L.append("- 재무 골격이 비어 있다 → 원문 재무제표 직접 확인 필요")
    L.append("- 경영진 교체·자본배분 방향 → 최근 8-K·프록시(DEF 14A) 확인")
    L.append("- 이 회사가 망하는 시나리오는? → 10-K Item 1A(Risk Factors)와 "
             "스토리 리더의 신규 위험 어휘를 함께 볼 것")

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
