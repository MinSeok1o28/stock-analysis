"""종목 상세 페이지 — 사실(파이프라인) + 서사(Claude) 를 한 장으로.

원본 유튜브 방식의 강점은 자연어 서사다. 계산으로는 "이건 미디어 회사가 아니라
놀이공원 회사"가 안 나온다. 그래서 두 층을 나눠 합친다:

    [사실]  company_decoder · story_reader · event_scanner · reverse_dcf
    [해석]  portfolio/narratives/<티커>.yaml  ← Claude 가 쓴다

서사가 없으면 사실만으로도 페이지가 나온다. 없다는 사실을 화면에 적는다.

`python3 -m src.pipelines.stock_page NVDA`
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from datetime import date
from html import escape
from pathlib import Path

from ..core.events import Bars, reaction_stats, scenarios
from ..core.valuation.fundamentals import earnings_quality
from ..core.valuation.outliers import detect as detect_outliers, normalized_base
from ..core.valuation.reverse_dcf import (ConvergenceError, basis_comparison,
                                          enterprise_value, growth_axes,
                                          growth_scenarios, implied_growth,
                                          wacc_sensitivity)
from ..models import Market
from ..narrative_io import Narrative, load as load_narrative
from ..provenance import Sourced, Unavailable
from ..render.dashboard import _CSS, FONT_LINK, _kpi, _table
from ..sources import fred, prices, sec_edgar, toss
from . import company_decoder as cd
from . import story_reader as sr

OUT_DIR = Path("dashboard/stocks")
ERP = 0.045          # 주식위험프리미엄 가정. [해석]으로 표기한다.


@dataclass
class StockPage:
    ticker: str
    on: date
    card: object
    narrative: Narrative
    story: object | None = None
    stat: object | None = None
    scen: list = field(default_factory=list)
    growth: list = field(default_factory=list)
    axes: list = field(default_factory=list)
    basis: list = field(default_factory=list)
    wacc_rows: list = field(default_factory=list)
    implied: float | None = None
    wacc: float | None = None
    quality: object | None = None
    #: 서사 근거 보고서 대조 (filings.BasisCheck). 역DCF 의 basis 와 다른 것이라 이름을 나눈다.
    narrative_basis: object | None = None
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Summary:
    """비교 표 한 줄. `StockPage` 를 통째로 들고 있지 않기 위한 압축.

    여러 종목을 한 번에 돌리면 페이지 객체가 그만큼 쌓인다. 비교에 실제로 쓰는 값만
    뽑아 두고 원본은 버린다. 서사는 파일에 있으니 필요할 때 다시 읽으면 된다.
    """

    ticker: str
    name: str
    market: Market
    currency: str
    price: float | None = None
    market_cap: float | None = None          # 통화 원단위
    implied: float | None = None             # 역DCF 요구 성장률
    wacc: float | None = None
    rev_cagr: float | None = None            # 가장 짧은 매출 CAGR 축
    rev_cagr_label: str = ""
    fcf_latest: float | None = None
    fcf_avg: float | None = None
    quality_flag: str = ""                   # ok · warn · insufficient
    quality_text: str = ""
    top_segment: str = ""
    top_segment_share: float | None = None
    reaction_median: float | None = None
    reaction_n: int = 0
    has_narrative: bool = False
    basis_state: str = ""
    basis_detail: str = ""
    note_count: int = 0

    @property
    def gap(self) -> float | None:
        """요구 성장률 − 과거 실제 매출 성장률.

        양수면 '지금 가격이 과거보다 빠른 성장을 요구한다' 는 뜻이다.
        **이건 비싸다/싸다가 아니다** — 어디를 더 파야 하는지의 출발점일 뿐이다.
        """
        if self.implied is None or self.rev_cagr is None:
            return None
        return self.implied - self.rev_cagr

    @property
    def unit(self) -> tuple[float, str]:
        return (1e12, "조") if self.market is Market.KR else (1e9, "B")


def summarize(p: StockPage) -> Summary:
    """빌드된 페이지에서 비교에 쓸 값만 뽑는다."""
    c = p.card
    rev_axis = next((a for a in p.axes
                     if a.value is not None and a.label.startswith("매출")), None)
    fcf_latest = fcf_avg = None
    if not isinstance(c.fcf, Unavailable):
        vals = cd._vals(c.fcf)
        if vals:
            fcf_latest, fcf_avg = vals[-1][1], normalized_base(vals)
    seg_name, seg_share = "", None
    if c.segments is not None and getattr(c.segments, "tables", None):
        # 가장 앞선 표(부문별)의 최대 비중 항목. 매출이 어디에 쏠려 있는지의 대리 지표다.
        first = next(iter(c.segments.tables.values()), None)
        rows = first.shares() if first is not None else []
        if rows:
            seg_name, _v, seg_share, _m = rows[0]
    bc = p.narrative_basis
    return Summary(
        ticker=p.ticker, name=c.info.get("name") or p.ticker, market=c.market,
        currency=str(c.info.get("currency") or ""),
        price=None if isinstance(c.price, Unavailable) else c.price.value,
        market_cap=None if isinstance(c.market_cap, Unavailable) else c.market_cap.value,
        implied=p.implied, wacc=p.wacc,
        rev_cagr=rev_axis.value if rev_axis else None,
        rev_cagr_label=rev_axis.label if rev_axis else "",
        fcf_latest=fcf_latest, fcf_avg=fcf_avg,
        quality_flag=getattr(p.quality, "flag", "") if p.quality else "",
        quality_text=str(p.quality) if p.quality else "",
        top_segment=seg_name, top_segment_share=seg_share,
        reaction_median=p.stat.median_abs if p.stat else None,
        reaction_n=p.stat.n if p.stat else 0,
        has_narrative=not p.narrative.is_empty,
        basis_state=bc.state.value if bc is not None else "",
        basis_detail=bc.detail if bc is not None else "",
        note_count=len(p.notes))


def build(ticker: str, on: date | None = None, *, with_story: bool = True) -> StockPage | Unavailable:
    on = on or date.today()
    tk = ticker.upper()
    card = cd.run(tk, on)
    if isinstance(card, Unavailable):
        return card
    nar = load_narrative(tk)
    page = StockPage(tk, on, card, nar)

    # ── 서사 근거 보고서 대조 ────────────────────────────────
    # 날짜가 아니라 사건으로 판단한다. 90일이 지나도 새 보고서가 없으면 안 낡은 것이고,
    # 30일밖에 안 지났어도 새 10-K 가 나왔으면 낡은 것이다.
    if not nar.is_empty:
        from . import filings
        page.narrative_basis = filings.check_basis(nar, filings.latest(tk))

    # ── 이익-현금 정합성 (영상 원본 규칙) ─────────────────────
    ni = cd._vals(card.series.get("NetIncome", []))
    ocf = cd._vals(card.series.get("OperatingCashFlow", []))
    if ni and ocf:
        page.quality = earnings_quality(ni, ocf)

    # ── 실적 반응 · 시나리오 ─────────────────────────────────
    bars_s = toss.daily_candles_paged(tk, pages=2)
    if not isinstance(bars_s, Unavailable) and card.market is Market.US:
        bars = Bars(bars_s.value)
        evs = sec_edgar.earnings_events(tk, 8)
        if not isinstance(evs, Unavailable):
            page.stat = reaction_stats(bars, evs)
            if page.stat.n and not isinstance(card.price, Unavailable):
                page.scen = scenarios(card.price.value, page.stat)
        else:
            page.notes.append("실적 발표 이력 미확보 (8-K)")
    elif card.market is not Market.US:
        page.notes.append("한국 종목 — 8-K 이력이 없어 실적 반응 통계를 만들 수 없다")

    # ── 역DCF ────────────────────────────────────────────────
    fcfs = card.fcf
    if isinstance(fcfs, Unavailable):
        page.notes.append(f"역DCF 불가 — {fcfs.reason[:70]}")
    elif isinstance(card.market_cap, Unavailable) or isinstance(card.shares, Unavailable):
        page.notes.append("역DCF 불가 — 시가총액·주식수 미확보")
    else:
        series = cd._vals(fcfs)
        latest, avg = series[-1][1], normalized_base(series)
        nd = card.net_debt
        ndv = 0.0 if (nd is None or isinstance(nd, Unavailable)) else nd.value.value
        if nd is None or isinstance(nd, Unavailable):
            page.notes.append("순부채 미확보 — 시가총액 기준으로 계산했다 (레버리지 왜곡 가능)")
        ev = enterprise_value(card.market_cap.value, ndv)
        rf = fred.latest("us10y")
        if isinstance(rf, Unavailable):
            page.wacc = 0.09
            page.notes.append("무위험수익률 미확보 — WACC 9% 기본값 사용")
        else:
            page.wacc = rf.value / 100 + ERP
        # 원본 규칙: 최신 FCF 가 3년 평균 대비 ±40% 이내면 최신을 기준값으로 쓴다
        dev = (latest - avg) / abs(avg) if avg else 0.0
        page.notes.append(
            f"기준 FCF: 최신 {latest/1e9:,.1f}B 가 3년 평균 {avg/1e9:,.1f}B 대비 {dev:+.1%} → "
            + ("±40% 이내라 최신을 기준으로, 3년 평균은 병기" if abs(dev) < 0.40
               else "±40% 초과라 3년 평균을 기준으로"))
        base = latest if abs(dev) < 0.40 else avg
        try:
            page.implied = implied_growth(ev, base, page.wacc).value
        except (ConvergenceError, ValueError) as exc:
            page.notes.append(f"역DCF 수렴 실패 — {exc}")
        page.basis = basis_comparison(ev, latest, avg, page.wacc)
        page.wacc_rows = wacc_sensitivity(ev, base)
        rev = cd._vals(card.series.get("Revenues", []))
        page.axes = growth_axes(rev, series)
        hist = [(a.label.replace(" CAGR", ""), a.value) for a in page.axes
                if a.value is not None and a.label.startswith("매출")][:2]
        page.growth = growth_scenarios(
            base, page.wacc, card.shares.value, net_debt=ndv,
            current_price=None if isinstance(card.price, Unavailable) else card.price.value,
            implied=page.implied, historical=hist)

    # ── 서사 변화 (무거움 — 옵션) ────────────────────────────
    if with_story and card.market is Market.US:
        st = sr.run(tk, 3, on)
        page.story = None if isinstance(st, Unavailable) else st
        if isinstance(st, Unavailable):
            page.notes.append(f"공시 문구 비교 미확보 — {st.reason[:60]}")

    return page


# ── 렌더 ────────────────────────────────────────────────────────

_EXTRA_CSS = """
/* ── 한 줄 요약 ─────────────────────────────────────── */
.hero{background:linear-gradient(180deg,var(--accs),var(--card));
  border:1px solid var(--line2);border-left:4px solid var(--acc);
  border-radius:10px;padding:1.5rem 1.6rem}
.hero .one{font-size:1.16rem;line-height:1.8;font-weight:500;color:var(--fg)}
.hero .one strong{font-weight:700}

/* ── 사실/해석 태그 ─────────────────────────────────── */
.tag-i,.tag-f{display:inline-block;font-size:.66rem;font-weight:700;letter-spacing:.04em;
  padding:.1rem .4rem;border-radius:3px;margin-right:.45rem;vertical-align:.08em}
.tag-i{background:var(--accs);color:var(--acc);border:1px solid var(--acc)}
.tag-f{background:var(--card2);color:var(--mut);border:1px solid var(--line2)}

.interp{border-left:3px solid var(--acc);background:var(--accs);
  padding:.95rem 1.15rem;border-radius:0 8px 8px 0;font-size:.92rem;line-height:1.85}
.interp p{margin:0 0 .7rem} .interp p:last-child{margin-bottom:0}

/* ── 리스크 카드 ────────────────────────────────────── */
.risk{background:var(--card2);border:1px solid var(--line2);border-radius:9px;
  padding:1rem 1.1rem;display:flex;flex-direction:column;gap:.45rem;
  border-top:3px solid var(--up)}
.risk .t{font-weight:700;font-size:.95rem;line-height:1.5;color:var(--fg)}
.risk .d{font-size:.86rem;line-height:1.75;color:var(--fg2)}
.risk .e{font-size:.72rem;color:var(--mut);border-top:1px solid var(--line2);
  padding-top:.45rem;margin-top:.15rem}

.basis{margin-top:.6rem;border-left:3px solid var(--warn);background:var(--warns);
 color:var(--warn);border-radius:0 6px 6px 0;padding:.5rem .8rem;font-size:.8rem;
 line-height:1.6}
.basis b{color:var(--warn);margin-right:.35rem}
.basis a{color:var(--warn);white-space:nowrap}
.miss{border-left:3px solid var(--warn);background:var(--warns);color:var(--warn);
  padding:.85rem 1.1rem;border-radius:0 8px 8px 0;font-size:.87rem;line-height:1.7}
.grid3{display:grid;gap:.9rem;grid-template-columns:repeat(auto-fit,minmax(255px,1fr))}
a.back{color:var(--fg2);text-decoration:none;font-size:.8rem;border:1px solid var(--line2);
  border-radius:99px;padding:.3rem .85rem;background:var(--card);align-self:flex-start}
a.back:hover{border-color:var(--acc);color:var(--acc)}
"""


_TPL = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
{fonts}
<title>{name} ({ticker}) — 종목 분석</title><style>{css}{extra}</style></head>
<body><div class="w">
<header><a class="back" href="../index.html">← 대시보드</a>
<h1 style="margin-top:.6rem">{name} <span class="chip">{ticker}</span></h1>
<div class="sub">{sub}</div></header>
{body}
<footer>리서치 보조 산출물이며 투자 자문이 아닙니다. [사실]은 출처가 붙은 값이고
[해석]은 판단입니다. 판단에 쓰는 숫자는 원문에서 재확인하십시오.<br>
생성: <code>python3 -m src.pipelines.stock_page {ticker}</code></footer>
</div></body></html>"""


def _pct(x, cls=True):
    """등락 표기 — 상승 빨강 ▲ · 하락 파랑 ▼."""
    if x is None:
        return '<span class="src">—</span>'
    if not cls:
        return f"{x:+.2%}"
    c = "up" if x > 0 else ("down" if x < 0 else "flat")
    arrow = "▲" if x > 0 else ("▼" if x < 0 else "–")
    return f'<span class="{c}">{arrow} {abs(x):.2%}</span>'


_BOLD = re.compile(r"\*\*(.+?)\*\*")


def rich(text: str) -> str:
    """서사 본문의 **강조** 를 굵은 글씨로. escape 후 치환해 주입을 막는다."""
    out = escape(text.strip())
    out = _BOLD.sub(r"<strong>\1</strong>", out)
    return out.replace("\n\n", "</p><p>").replace("\n", "<br>")


def render(p: StockPage, out_dir: Path = OUT_DIR) -> Path:
    c, n, div, unit = p.card, p.narrative, (1e12 if p.card.market is Market.KR else 1e9), \
        ("조" if p.card.market is Market.KR else "B")
    name = c.info.get("name") or p.ticker
    P: list[str] = []

    # ① 한 줄 요약 (해석)
    if n.one_liner:
        d, stale = n.staleness(p.on)
        bc = p.narrative_basis
        cur = bc is not None and bc.state.name == "CURRENT"
        # 근거 보고서가 최신이라고 확인됐으면 '90일 지남' 잔소리를 하지 않는다.
        # 시간은 낡음의 대리 지표일 뿐이고, 여기서는 진짜 지표를 확보했다.
        meta = (f'[해석] {escape(n.author)} 작성 · {n.updated} ({d}일 전)'
                + (f' · {escape(bc.detail)}' if cur else
                   ("" if bc is not None and bc.is_warning else
                    (" · 갱신 권고" if stale else ""))))
        badge = ""
        if bc is not None and bc.is_warning:
            link = (f' <a href="{escape(n.basis.url)}" target="_blank" rel="noopener">근거 보고서 ↗</a>'
                    if n.basis.url else "")
            badge = (f'<div class="basis"><b>{escape(bc.state.value)}</b> '
                     f'{escape(bc.detail)}{link}</div>')
        P.append(f'<div class="hero"><div class="one">{rich(n.one_liner)}</div>'
                 f'<div class="src" style="margin-top:.5rem">{meta}</div>{badge}</div>')
    else:
        P.append('<div class="miss">한 줄 요약이 없습니다. '
                 '<code>company-decoder</code> 스킬로 서사를 작성하면 여기에 표시됩니다 '
                 '(<code>portfolio/narratives/{}.yaml</code>).</div>'.format(p.ticker))

    # KPI
    k = []
    if not isinstance(c.price, Unavailable):
        k.append(_kpi("현재가", f"{c.price.value:,.2f}"))
    if not isinstance(c.market_cap, Unavailable):
        k.append(_kpi("시가총액", f"{c.market_cap.value/div:,.1f}{unit}"))
    if p.implied is not None:
        k.append(_kpi("시장 요구 성장률", f"{p.implied:.1%}", f"WACC {p.wacc:.1%} 기준"))
    if p.stat and p.stat.n:
        k.append(_kpi("실적 반응 중앙값", f"±{p.stat.median_abs:.1%}", f"과거 {p.stat.n}회"))
    if k:
        P.append(f'<div class="kpis">{"".join(k)}</div>')

    # ② 돈 버는 구조 — 해석 + 세그먼트 표(사실)
    body = []
    if n.how_it_makes_money:
        body.append('<div class="interp"><span class="tag-i">해석</span>'
                    f'<p>{rich(n.how_it_makes_money)}</p></div>')
    if n.mermaid:
        body.append(f'<pre class="mermaid">{escape(n.mermaid)}</pre>')
    if c.segments and getattr(c.segments, "tables", None):
        from ..sources.sec_segments import sourced as seg_sourced
        for key, title in (("segment", "사업부문별"), ("product", "제품·서비스별"),
                           ("geography", "지역별")):
            t = c.segments.tables.get(key)
            if not t:
                continue
            rows = [[escape(g), f"{v/div:,.1f}{unit}", f"{sh:.1%}",
                     f"{m:.1%}" if m is not None else "—"] for g, v, sh, m in t.shares()]
            sc = seg_sourced(c.segments, key)
            body.append(f"<h3>{title}</h3>"
                        + _table(["구분", "매출", "비중", "영업이익률"], rows)
                        + f'<p class="src">{escape(sc.cite() if not isinstance(sc, Unavailable) else "")}</p>')
        st = c.segments.tables.get("segment")
        if st and st.operating_income:
            oi = st.operating_income
            tot = sum(v for g, v in oi.items() if g not in st._total_keys())
            gaps = [(g, sh, oi[g]/tot) for g, v, sh, m in st.shares() if g in oi and tot]
            gaps = [x for x in gaps if abs(x[2]-x[1]) >= 0.05]
            if gaps:
                body.append('<h3>매출 비중 vs 이익 기여</h3><ul>')
                for g, sh, os_ in sorted(gaps, key=lambda r: -(r[2]-r[1])):
                    cls = "up" if os_ > sh else "down"
                    body.append(f'<li><span class="tag-f">사실</span><b>{escape(g)}</b> — '
                                f'매출 {sh:.1%} / 영업이익 {os_:.1%} '
                                f'<span class="{cls}">({os_-sh:+.1%}p)</span></li>')
                body.append("</ul>")
    if body:
        P.append("<section><h2>이 회사는 뭘로 돈을 버나</h2>" + "".join(body) + "</section>")

    # ③ 최근 실적 (사실)
    rev = cd._vals(c.series.get("Revenues", []))
    if rev:
        fc = [] if isinstance(c.fcf, Unavailable) else cd._vals(c.fcf)
        ni, oi = cd._vals(c.series.get("NetIncome", [])), cd._vals(c.series.get("OperatingIncome", []))
        nid, oid, fcd = dict(ni), dict(oi), dict(fc)
        rows = []
        for i, (kk, v) in enumerate(rev[-6:]):
            prev = rev[-7+i][1] if len(rev) >= 7-i and i > 0 else None
            yo = ((v-rev[-7+i][1])/rev[-7+i][1]) if (i > 0 or len(rev) > 6) and len(rev) >= 7-i else None
            rows.append([kk, f"{v/div:,.1f}{unit}", _pct(yo),
                         f"{oid[kk]/div:,.1f}{unit}" if kk in oid else "—",
                         f"{nid[kk]/div:,.1f}{unit}" if kk in nid else "—",
                         f"{fcd[kk]/div:,.1f}{unit}" if kk in fcd else "—"])
        sec = ["<section><h2>최근 실적</h2>",
               _table(["회계연도", "매출", "YoY", "영업이익", "순이익", "FCF"], rows)]
        if p.quality:
            cls = "miss" if p.quality.is_warning else "interp"
            sec.append(f'<div class="{cls}" style="margin-top:.9rem">'
                       f'<span class="tag-f">사실</span>{escape(str(p.quality))}</div>')
        if c.outliers:
            sec.append('<h3>이상치</h3><ul>'
                       + "".join(f'<li><span class="tag-f">사실</span>{escape(str(o))}</li>'
                                 for o in c.outliers) + "</ul>")
        if p.stat and p.stat.n:
            sec.append("<h3>과거 실적 발표 시장 반응</h3>")
            sec.append(_table(["실적일", "반응일", "변동", "거래량"],
                              [[e, r, _pct(m), f"{v:.1f}배"] for e, r, m, v in p.stat.moves]))
            sec.append(f'<p class="src">{escape(p.stat.summary())}</p>')
        P.append("".join(sec) + "</section>")

    # ④ 가격이 요구하는 것
    if p.implied is not None or p.basis:
        sec = ["<section><h2>가격이 무엇을 요구하나</h2>"]
        if p.implied is not None:
            sec.append(f'<p><span class="tag-f">사실</span>현재 기업가치가 성립하려면 '
                       f'향후 10년 연평균 <b>{p.implied:.1%}</b> 의 FCF 성장이 필요합니다 '
                       f'(WACC {p.wacc:.1%}, 영구성장 2.5%).</p>')
        if p.axes:
            sec.append("<h3>과거 실제 성장률 — 구간별</h3>")
            sec.append(_table(["구간", "CAGR", "기간"],
                              [[a.label, f"{a.value:+.1%}", a.note]
                               for a in p.axes if a.value is not None]))
            sec.append('<p class="src">구간에 따라 부호가 바뀝니다. '
                       '한 구간만 보면 오해를 만듭니다.</p>')
        if p.basis:
            sec.append("<h3>기준 FCF 가 결론을 가른다</h3>")
            sec.append(_table(["기준", "FCF", "요구 성장률"],
                              [[b.label, f"{b.fcf/div:,.1f}{unit}",
                                f"{b.implied:.2%}" if b.implied is not None else escape(b.note[:40])]
                               for b in p.basis]))
        if p.wacc_rows:
            sec.append("<h3>할인율 민감도</h3>")
            sec.append(_table(["WACC", "요구 성장률"],
                              [[f"{r.wacc:.0%}",
                                f"{r.implied:.2%}" if r.implied is not None else escape(r.note[:36])]
                               for r in p.wacc_rows]))
        P.append("".join(sec) + "</section>")

    # ⑤ 시나리오 — 두 종류
    if p.growth or p.scen:
        sec = ["<section><h2>시나리오</h2>",
               '<p class="src">아래 어느 것도 예측이 아닙니다. '
               '(a)는 <b>가정에 따른 산술</b>, (b)는 <b>과거에 관측된 분포</b>입니다. '
               '방향은 제시하지 않습니다.</p>']
        if p.growth:
            sec.append("<h3>(a) 성장률을 가정하면 정당화되는 주가</h3>")
            sec.append(_table(["가정 성장률", "주가", "현재가 대비", "비고"],
                              [[f"{g.growth:.1%}", f"{g.price:,.2f}",
                                _pct(g.vs_current), escape(g.label) or "—"]
                               for g in p.growth]))
            sec.append('<p class="src">"이렇게 될 것이다"가 아니라 '
                       '"이 가정이 맞다면 이 가격이 성립한다"입니다.</p>')
        if p.scen:
            sec.append("<h3>(b) 과거 실적 반응 분포</h3>")
            sec.append(_table(["구간", "변동", "가격", "근거"],
                              [[s.label, f"{s.move:+.1%}", f"{s.price:,.2f}", escape(s.basis)]
                               for s in p.scen]))
            sec.append('<p class="src">이 종목이 <b>과거 실적에 얼마나 움직였는가</b>입니다. '
                       '이번에도 그럴 거라는 뜻이 아닙니다.</p>')
        P.append("".join(sec) + "</section>")

    # ⑥ 서사 (해석) + 공시 문구 변화 (사실)
    sec = []
    if n.story:
        sec.append('<div class="interp"><span class="tag-i">해석</span>'
                   f'<p>{rich(n.story)}</p></div>')
    if p.story and p.story.pairs:
        for pair in p.story.pairs:
            ms = pair.material_sections
            if not ms:
                continue
            sec.append(f"<h3>FY{pair.older.fiscal_year} → FY{pair.newer.fiscal_year}</h3><ul>")
            for s in ms[:3]:
                bits = [s.diff.summary()]
                if s.removed_hedges:
                    bits.append("사라진 헤지 " + ", ".join(f"{w}({a}→{b})"
                                for w, a, b in s.removed_hedges[:3]))
                if s.new_risks:
                    bits.append("신규 위험 " + ", ".join(s.new_risks))
                sec.append(f'<li><span class="tag-f">사실</span><b>Item {escape(s.key)}</b> '
                           f"{escape(' · '.join(bits))}</li>")
            sec.append("</ul>")
    if sec:
        P.append("<section><h2>지난 3년의 이야기</h2>" + "".join(sec) + "</section>")

    # ⑦ 망하는 시나리오 — 리스크 3개 (해석)
    if n.risks:
        cards = "".join(
            f'<div class="risk"><span class="t">{i+1}. {escape(r.title)}</span>'
            + (f'<span class="d">{rich(r.detail)}</span>' if r.detail else "")
            + (f'<span class="e">근거 · {escape(r.evidence)}</span>' if r.evidence else "")
            + "</div>" for i, r in enumerate(n.risks[:3]))
        P.append('<section><h2>이 회사가 망하는 시나리오</h2>'
                 '<p class="src">[해석] 10-K Item 1A 에 수십 개가 나열돼 있지만 '
                 '영향이 큰 3개로 압축했습니다.</p>'
                 f'<div class="grid3">{cards}</div></section>')

    # ⑧ 답이 안 나온 것
    miss = ["컨센서스 추정치 — 유료. '실적이 예상을 상회/하회하면' 시나리오를 만들 수 없다",
            "옵션 내재변동성 — 유료. 시장이 예상하는 변동폭을 알 수 없다",
            "어닝콜 트랜스크립트 — 유료. 경영진 톤·가이던스 달성률을 만들 수 없다"]
    if c.market is Market.KR:
        miss.append("DART 사업보고서 본문 파싱 미구현 — 사업 설명·문구 변화를 만들 수 없다")
    if n.watch_next:
        P.append("<section><h2>다음에 지켜볼 것</h2><ul>"
                 + "".join(f'<li><span class="tag-i">해석</span> {rich(x)}</li>'
                             for x in n.watch_next) + "</ul></section>")
    P.append("<section><h2>이 페이지로 답이 안 나온 것</h2><ul>"
             + "".join(f"<li>{escape(x)}</li>" for x in miss)
             + "".join(f"<li>{escape(x)}</li>" for x in (p.notes + list(c.notes))[:10])
             + "</ul></section>")

    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"{p.ticker}.html"
    sub = (f"{p.on.isoformat()} · {c.info.get('market','')} · "
           f"{c.asset_type.value} · 매매 판단은 사람이 합니다")
    dest.write_text(_TPL.format(name=escape(name), ticker=escape(p.ticker), css=_CSS, fonts=FONT_LINK,
                                extra=_EXTRA_CSS, sub=escape(sub), body="\n".join(P)),
                    encoding="utf-8")
    return dest


if __name__ == "__main__":
    tk = (sys.argv[1] if len(sys.argv) > 1 else "NVDA").upper()
    if "--init" in sys.argv:
        from ..narrative_io import path_for, template
        dest = path_for(tk)
        if dest.exists() and "--force" not in sys.argv:
            print(f"이미 있습니다: {dest}  (덮어쓰려면 --force)")
            sys.exit(1)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(template(tk), encoding="utf-8")
        print(f"✓ 서사 서식 생성: {dest}\n  채운 뒤: python3 -m src.pipelines.stock_page {tk}")
        sys.exit(0)
    pg = build(tk, with_story=("--no-story" not in sys.argv))
    if isinstance(pg, Unavailable):
        print(pg); sys.exit(1)
    d = render(pg)
    print(f"✓ {d} ({d.stat().st_size:,} bytes)")
    print(f"  서사: {'있음' if not pg.narrative.is_empty else '없음 (portfolio/narratives/%s.yaml)' % tk}")
    print(f"  요구 성장률 {pg.implied:.2%}" if pg.implied else "  역DCF 미산출")
    print(f"  실적 반응 {pg.stat.n}회" if pg.stat else "  실적 반응 없음")
    if pg.quality:
        print(f"  {pg.quality}")
