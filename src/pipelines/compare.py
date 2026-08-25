"""여러 종목 비교 페이지 — 한 번 선택해 한 번 기다린다.

## 왜 탭이 아니라 한 장인가
여러 종목을 고른다는 건 대개 **비교하려는 것**이다. 탭은 한 번에 하나만 보여주므로
비교를 오히려 막는다. 그래서 위에 나란히 놓는 표를 두고, 종목별 상세는 접어 둔다.

## 여기서 하지 않는 것
- 순위를 매기지 않는다. 정렬은 사용자가 고른 순서 그대로다.
- "어느 게 낫다"를 말하지 않는다. 격차는 *이 가격이 요구하는 성장률* 과
  *과거 실제 성장률* 의 차이일 뿐, 싸다·비싸다가 아니다 (CLAUDE.md 매매 신호 금지).
"""

from __future__ import annotations

import re
from datetime import date
from html import escape

from ..models import Market
from ..narrative_io import load as load_narrative
from ..render import glossary as gl
from ..render.dashboard import _CSS, FONT_LINK, _table
from .stock_page import Summary, rich

_EXTRA = """
.cmp td.sym{white-space:nowrap}
.cmp .tk{font-family:"IBM Plex Mono",monospace;font-size:.72rem;color:var(--mut)}
.gap-hi{color:var(--up);font-weight:600}
.gap-lo{color:var(--down);font-weight:600}
details.co{background:var(--card);border:1px solid var(--line2);border-radius:10px;
 margin-bottom:.75rem;overflow:hidden}
details.co>summary{cursor:pointer;padding:.95rem 1.15rem;font-weight:700;font-size:.98rem;
 display:flex;align-items:center;gap:.6rem;flex-wrap:wrap;list-style:none}
details.co>summary::-webkit-details-marker{display:none}
details.co>summary::before{content:"▸";color:var(--acc);font-size:.85rem;flex:none}
details.co[open]>summary::before{content:"▾"}
details.co[open]>summary{border-bottom:1px solid var(--line2)}
details.co .co-body{padding:1.1rem 1.15rem 1.25rem;display:flex;
 flex-direction:column;gap:1rem}
details.co h4{font-size:.8rem;font-weight:700;color:var(--mut);margin:0;
 letter-spacing:.03em}
.interp{border-left:3px solid var(--acc);background:var(--accs);
 padding:.75rem 1rem;border-radius:0 7px 7px 0;font-size:.9rem;line-height:1.75}
.interp p{margin:0 0 .7rem} .interp p:last-child{margin-bottom:0}
.tag-i{display:inline-block;font-size:.66rem;font-weight:700;color:var(--acc);
 letter-spacing:.05em;margin-right:.4rem}
.risk{background:var(--card2);border-radius:8px;padding:.75rem .9rem;font-size:.85rem;
 line-height:1.65;display:flex;flex-direction:column;gap:.3rem}
.risk .t{font-weight:700}
.risk .e{font-size:.74rem;color:var(--mut)}
.fail{border-left:3px solid var(--warn);background:var(--warns);color:var(--warn);
 padding:.75rem 1rem;border-radius:0 7px 7px 0;font-size:.86rem;line-height:1.7}
.basis{margin-top:.5rem;border-left:3px solid var(--warn);background:var(--warns);
 color:var(--warn);border-radius:0 6px 6px 0;padding:.45rem .8rem;font-size:.78rem}
.grid3{display:grid;gap:.8rem;grid-template-columns:repeat(auto-fit,minmax(240px,1fr))}
a.go{color:var(--acc);text-decoration:none;font-size:.82rem;font-weight:600}
ul.plain{font-size:.86rem;line-height:1.85;gap:.55rem}
ul.plain li::marker{color:var(--acc)}
a.back{color:var(--fg2);text-decoration:none;font-size:.8rem;border:1px solid var(--line2);
 border-radius:99px;padding:.3rem .85rem;background:var(--card);align-self:flex-start}
""" + gl.CSS

_TPL = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
{fonts}
<title>{n}종목 비교 — {on}</title><style>{css}{extra}</style></head>
<body><div class="w">
<header><a class="back" href="/">← 대시보드</a>
<h1 style="margin-top:.6rem">종목 비교 <span class="chip">{n}종목</span></h1>
<div class="sub">{on} · 순위를 매기지 않습니다. 나란히 놓고 어디를 더 볼지만 고릅니다.</div>
</header>
{body}
<footer>리서치 보조 산출물이며 투자 자문이 아닙니다. [사실]은 출처가 붙은 값이고
[해석]은 판단입니다. 판단에 쓰는 숫자는 원문에서 재확인하십시오.</footer>
</div></body></html>"""


def _money(v: float | None, market: Market) -> str:
    if v is None:
        return '<span class="src">확인 필요</span>'
    div, unit = (1e12, "조") if market is Market.KR else (1e9, "B")
    return f"{v / div:,.1f}{unit}"


def _pct(v: float | None, digits: int = 1) -> str:
    return '<span class="src">—</span>' if v is None else f"{v:.{digits}%}"


def _gap_cell(s: Summary) -> str:
    """요구 성장률 − 과거 매출 CAGR. 싸다·비싸다가 아니라 '무엇을 더 볼지' 의 출발점."""
    g = s.gap
    if g is None:
        return '<span class="src">—</span>'
    cls = "gap-hi" if g > 0 else ("gap-lo" if g < 0 else "flat")
    return f'<span class="{cls}" title="{escape(plain_gap(s))}">{g:+.1%}p</span>'


def josa(word: str, with_batchim: str, without: str) -> str:
    """받침에 따라 조사를 고른다 — '삼성전자가' vs '애플이'.

    한글 음절은 (코드 − 0xAC00) % 28 이 0이면 받침이 없다.
    한글이 아닌 글자로 끝나면 판단하지 않고 무받침 쪽을 쓴다.
    """
    for ch in reversed(word.strip()):
        if "가" <= ch <= "힣":
            return with_batchim if (ord(ch) - 0xAC00) % 28 else without
        if ch.isalnum():
            break
    return without


def plain_gap(s: Summary) -> str:
    """격차를 문장으로. 숫자를 못 읽는 사람도 뜻은 알 수 있어야 한다."""
    g = s.gap
    if g is None:
        return "요구 성장률이나 과거 성장률 중 하나를 구하지 못해 비교할 수 없습니다."
    span = s.rev_cagr_label.replace("매출 ", "").replace(" CAGR", "") or "과거"
    head = (f"지금 주가에는 앞으로 매년 {s.implied:.1%}씩 성장한다는 전제가 들어 있습니다. "
            f"최근 {span} 동안 매출은 실제로 연 {s.rev_cagr:.1%}씩 늘었습니다. ")
    if g > 0:
        return head + (f"과거보다 연 {g:.1%}p 빠른 성장을 기대하고 있는 값입니다. "
                       f"비싸다는 뜻이 아니라, 그 기대가 왜 생겼는지 확인해볼 자리라는 뜻입니다.")
    return head + (f"과거보다 연 {abs(g):.1%}p 느려져도 설명되는 값입니다. "
                   f"싸다는 뜻이 아니라, 시장이 감속을 예상하는 이유를 "
                   f"확인해볼 자리라는 뜻입니다.")


def plain_reading(s: Summary) -> list[str]:
    """이 종목의 숫자들을 문장으로 풀어 쓴 것. 판단하지 않고 읽어주기만 한다."""
    out: list[str] = []
    if s.market_cap is not None:
        cur = "원" if s.market is Market.KR else "달러"
        amount = _strip(_money(s.market_cap, s.market)) + cur
        line = f"이 회사를 통째로 사려면 약 {amount}{josa(cur, '이', '가')} 듭니다."
        if s.price is not None:
            line += f" 주가는 그걸 잘게 쪼갠 한 조각 값입니다 ({s.price:,.2f})."
        out.append(line)
    if s.gap is not None:
        out.append(plain_gap(s))
    if s.fcf_latest is not None and s.fcf_avg:
        dev = (s.fcf_latest - s.fcf_avg) / abs(s.fcf_avg)
        tail = ("최근 3년 평균과 비슷해 특이한 해는 아니었습니다." if abs(dev) < 0.40 else
                f"3년 평균 대비 {dev:+.0%}로 크게 벗어나 계산은 평균을 기준으로 삼았습니다.")
        out.append(f"장사해서 쓰고 남긴 현금이 최근 1년 "
                   f"{_strip(_money(s.fcf_latest, s.market))}입니다. {tail}")
    if s.quality_flag == "warn":
        out.append("장부상 이익과 실제 현금이 서로 다른 방향으로 움직였습니다. "
                   "분식이라는 뜻은 아니고, 왜 벌어졌는지 확인해볼 자리입니다.")
    elif s.quality_flag == "ok":
        out.append("장부상 이익과 실제 들어온 현금이 대체로 같은 방향으로 움직였습니다.")
    if s.top_segment and s.top_segment_share is not None:
        axis = s.top_segment_kind or "구분"
        out.append(f"매출을 {axis}로 나눠 보면 {s.top_segment_share:.0%}가 "
                   f"'{s.top_segment}' 한 곳에서 나옵니다. "
                   f"여기가 흔들리면 회사 전체가 흔들린다는 뜻입니다.")
    if s.reaction_median is not None and s.reaction_n:
        out.append(f"과거 실적 발표 {s.reaction_n}번 동안 발표 다음 날 주가가 "
                   f"평균 {s.reaction_median:.1%}씩 움직였습니다. 다음에도 그럴 거라는 "
                   f"보장은 없고, 이 종목이 실적에 얼마나 예민한지를 보는 값입니다.")
    return out


def _strip(html: str) -> str:
    """`_money()` 결과에서 태그를 뺀 평문. title 속성·문장에 넣기 위한 것."""
    return re.sub(r"<[^>]+>", "", html)


def _quality_cell(s: Summary) -> str:
    if not s.quality_flag:
        return '<span class="src">—</span>'
    if s.quality_flag == "warn":
        return f'<span class="chip warn" title="{escape(s.quality_text)}">따로 논다</span>'
    if s.quality_flag == "insufficient":
        return '<span class="src">표본 부족</span>'
    return f'<span class="chip" title="{escape(s.quality_text)}">같은 방향</span>'


def _table_rows(summaries: list[Summary]) -> list[list[str]]:
    rows = []
    for s in summaries:
        seg = ('<span class="src">—</span>' if not s.top_segment else
               f'{escape(s.top_segment[:22])}<br><span class="src">'
               f'{s.top_segment_share:.0%} · {escape(s.top_segment_kind or "구분")}</span>')
        react = ('<span class="src">—</span>' if s.reaction_median is None else
                 f'±{s.reaction_median:.1%}<br><span class="src">{s.reaction_n}회</span>')
        rows.append([
            f'<strong>{escape(s.name)}</strong><br><span class="tk">{escape(s.ticker)}</span>',
            f'{s.price:,.2f}' if s.price is not None else '<span class="src">—</span>',
            _money(s.market_cap, s.market),
            _pct(s.implied),
            (_pct(s.rev_cagr) + (f'<br><span class="src">{escape(s.rev_cagr_label)}</span>'
                                 if s.rev_cagr_label else "")),
            _gap_cell(s),
            (_money(s.fcf_latest, s.market) + "<br><span class='src'>평균 "
             + _money(s.fcf_avg, s.market) + "</span>"),
            _quality_cell(s),
            seg,
            react,
        ])
    return rows


def _detail(s: Summary) -> str:
    """종목 하나의 접이식 상세. 서사는 파일에서 다시 읽는다."""
    n = load_narrative(s.ticker)
    chips = [f'<span class="chip">{escape(s.ticker)}</span>']
    if s.implied is not None:
        chips.append(f'<span class="chip">요구 성장률 {s.implied:.1%}</span>')
    if not s.has_narrative:
        chips.append('<span class="chip warn">서사 없음</span>')
    elif s.basis_state and s.basis_state != "최신 보고서 기준":
        chips.append(f'<span class="chip warn">{escape(s.basis_state)}</span>')

    body: list[str] = []
    if n.one_liner:
        body.append('<div class="interp"><span class="tag-i">해석</span>'
                    f'<p>{rich(n.one_liner)}</p></div>')
    else:
        body.append('<div class="fail">서사가 없습니다 — 사실만 만들어졌습니다. '
                    '아래 전체 분석에서 계산 결과를 볼 수 있습니다.</div>')
    if s.basis_detail and s.basis_state != "최신 보고서 기준":
        body.append(f'<div class="basis"><b>{escape(s.basis_state)}</b> '
                    f'{escape(s.basis_detail)}</div>')
    if n.how_it_makes_money:
        body.append('<h4>돈 버는 구조 [해석]</h4>'
                    f'<div class="interp"><p>{rich(n.how_it_makes_money)}</p></div>')
    if n.story:
        body.append('<h4>지난 3년 [해석]</h4>'
                    f'<div class="interp"><p>{rich(n.story)}</p></div>')
    if n.risks:
        cards = "".join(
            f'<div class="risk"><span class="t">{i+1}. {escape(r.title)}</span>'
            + (f'<span>{rich(r.detail)}</span>' if r.detail else "")
            + (f'<span class="e">근거 · {escape(r.evidence)}</span>' if r.evidence else "")
            + "</div>" for i, r in enumerate(n.risks[:3]))
        body.append(f'<h4>망하는 시나리오 [해석]</h4><div class="grid3">{cards}</div>')
    if n.watch_next:
        body.append("<h4>다음에 볼 것 [해석]</h4><ul>"
                    + "".join(f"<li>{rich(x)}</li>" for x in n.watch_next[:5]) + "</ul>")
    reading = plain_reading(s)
    if reading:
        body.append('<h4>이 숫자들이 무슨 뜻인가 [사실을 풀어 쓴 것]</h4>'
                    '<ul class="plain">'
                    + "".join(f"<li>{escape(x)}</li>" for x in reading) + "</ul>")
    if s.quality_text:
        body.append(f'<p class="src">[사실] {escape(s.quality_text)}</p>')
    if s.note_count:
        body.append(f'<p class="src">확인 필요 {s.note_count}건 — 전체 분석에 있습니다.</p>')
    body.append(f'<a class="go" href="/stocks/{escape(s.ticker)}.html" target="_blank" '
                f'rel="noopener">{escape(s.name)} 전체 분석 보기 ↗</a>')

    return (f'<details class="co"><summary>{escape(s.name)}{"".join(chips)}</summary>'
            f'<div class="co-body">{"".join(body)}</div></details>')


#: 표 헤더. 이름 옆 ⓘ 에 hover 하면 용어 설명이 뜬다 (render/glossary.py).
_HEADERS = ["종목", "현재가",
            gl.header("시가총액", "market_cap"),
            gl.header("요구 성장률", "implied"),
            gl.header("과거 매출 CAGR", "rev_cagr"),
            gl.header("격차", "gap"),
            gl.header("FCF 최신", "fcf"),
            gl.header("이익-현금", "quality"),
            gl.header("최대 부문", "segment"),
            gl.header("실적 반응", "reaction")]


def render(summaries: list[Summary], failures: list[tuple[str, str]],
           on: date | None = None) -> str:
    """비교 페이지 HTML. 파일로 쓰지 않는다 — 선택마다 달라지므로 서버가 메모리에서 낸다."""
    on = on or date.today()
    P: list[str] = []

    if summaries:
        P.append('<section><h2>나란히 보기</h2>' + gl.panel()
                 + '<p class="src" style="margin:0 0 .8rem">'
                 '격차 = 요구 성장률 − 과거 매출 CAGR. '
                 '<b>싸다·비싸다가 아닙니다</b> — 이 가격이 과거보다 빠른 성장을 요구하는지만 '
                 '보여주고, 어디를 더 파볼지 고르는 데 씁니다. '
                 '통화가 섞여 있으니 시가총액·FCF 는 종목별 단위를 확인하십시오.</p>'
                 + f'<div class="cmp">{_table(_HEADERS, _table_rows(summaries), numeric_from=1, raw_headers=True)}</div>'
                 + '</section>')
        P.append('<section><h2>종목별 상세</h2>'
                 '<p class="src" style="margin:0 0 .8rem">제목을 눌러 펼칩니다.</p>'
                 + "".join(_detail(s) for s in summaries) + '</section>')

    if failures:
        items = "".join(f'<li><b>{escape(t)}</b> — {escape(why)}</li>' for t, why in failures)
        P.append(f'<section><h2>만들지 못한 종목 <span class="chip warn">{len(failures)}</span></h2>'
                 f'<ul>{items}</ul></section>')

    if not summaries and not failures:
        P.append('<section><p>선택된 종목이 없습니다.</p></section>')

    return _TPL.format(fonts=FONT_LINK, css=_CSS, extra=_EXTRA, on=on.isoformat(),
                       n=len(summaries) + len(failures), body="".join(P))
