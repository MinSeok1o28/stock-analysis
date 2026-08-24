"""HTML 대시보드. 단일 현재 상태를 덮어쓴다 (reports/ 는 날짜별 append).

뷰어의 테마를 따른다. 명시 선택(data-theme) · OS 설정 · 기본값 셋 다 처리한다.
자체 완결이라 외부 리소스를 불러오지 않는다.
"""

from __future__ import annotations

from datetime import date
from html import escape
from pathlib import Path

from ..provenance import Sourced, Unavailable, require_sourced

OUT = Path("dashboard/index.html")

DISCLAIMER = ("> 이 문서는 리서치 보조 산출물이며 투자 자문이 아닙니다. 매매 판단은 사람이 합니다. "
              "1차 스크리너로만 사용하고, 판단에 직접 쓰는 숫자는 원문에서 재확인하십시오.")

#: 웹폰트는 있으면 쓰고 없으면(오프라인) 시스템 폰트로 떨어진다.
FONT_LINK = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
             '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
             '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
             'family=Noto+Sans+KR:wght@400;500;700&'
             'family=IBM+Plex+Mono:wght@400;500;600&display=swap">')

_CSS = """
/* ── 색 토큰 ─────────────────────────────────────────────
   한국 관행: 상승 빨강 · 하락 파랑. 쨍하지 않게 채도를 낮췄다.
   UI 강조(--acc)는 청록이라 빨강·파랑 어느 쪽과도 섞이지 않는다. */
:root{
  --bg:#f2f4f5; --card:#fff; --card2:#e8ecee; --fg:#1a2228; --fg2:#43525c;
  --mut:#75858e; --line:#ccd6db; --line2:#e0e7ea;
  --acc:#0f7268; --accs:#e0f0ed;
  --warn:#8a6414; --warns:#f6ecd6;
  --up:#c2544e;   --ups:#fbeceb;      /* 상승 — 연한 빨강 */
  --down:#3a6ba6; --downs:#e9f0f8;    /* 하락 — 연한 파랑 */
  --flat:#75858e;
}
@media(prefers-color-scheme:dark){:root:not([data-theme=light]){
  --bg:#0f151a; --card:#18212a; --card2:#212d38; --fg:#e6edf2; --fg2:#b3c1cb;
  --mut:#7f8f9a; --line:#2c3b47; --line2:#22303b;
  --acc:#54c9b8; --accs:#123430;
  --warn:#d9ab45; --warns:#332813;
  --up:#ef8b84;   --ups:#3a2220;
  --down:#7fb2e8; --downs:#1b2a3c;
  --flat:#7f8f9a;
}}
:root[data-theme=dark]{
  --bg:#0f151a; --card:#18212a; --card2:#212d38; --fg:#e6edf2; --fg2:#b3c1cb;
  --mut:#7f8f9a; --line:#2c3b47; --line2:#22303b;
  --acc:#54c9b8; --accs:#123430;
  --warn:#d9ab45; --warns:#332813;
  --up:#ef8b84;   --ups:#3a2220;
  --down:#7fb2e8; --downs:#1b2a3c;
  --flat:#7f8f9a;
}

*{box-sizing:border-box}
body{
  background:var(--bg); color:var(--fg); margin:0; padding:2.2rem 1.25rem 6rem;
  font-family:"Noto Sans KR","Malgun Gothic","Apple SD Gothic Neo",
              ui-sans-serif,system-ui,sans-serif;
  font-size:16px; line-height:1.75; letter-spacing:-.005em;
  -webkit-font-smoothing:antialiased; text-rendering:optimizeLegibility;
}
.w{max-width:940px;margin:0 auto;display:flex;flex-direction:column;gap:1.6rem}

/* ── 타이포 ───────────────────────────────────────────── */
h1{font-size:1.72rem;font-weight:700;margin:0;letter-spacing:-.03em;line-height:1.25}
h2{font-size:1.14rem;font-weight:700;margin:0 0 1rem;letter-spacing:-.02em;
   display:flex;align-items:center;gap:.55rem}
h2::before{content:"";width:3px;height:1.05em;background:var(--acc);border-radius:2px;flex:none}
h3{font-size:.9rem;font-weight:700;color:var(--fg2);margin:1.4rem 0 .6rem;
   letter-spacing:-.01em}
h3:first-child{margin-top:0}
p{margin:0 0 .8rem} p:last-child{margin-bottom:0}
strong,b{font-weight:700;color:var(--fg)}
.sub{color:var(--mut);font-size:.84rem;line-height:1.6}
.num,td.n,th.n,.kpi .val,.kpi .delta{
  font-family:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,monospace;
  font-variant-numeric:tabular-nums;}

/* ── 섹션 ─────────────────────────────────────────────── */
header{display:flex;flex-direction:column;gap:.4rem;padding-bottom:1.2rem;
  border-bottom:2px solid var(--fg)}
section{background:var(--card);border:1px solid var(--line2);border-radius:10px;
  padding:1.35rem 1.5rem;box-shadow:0 1px 2px rgba(0,0,0,.04)}
@media(prefers-color-scheme:dark){:root:not([data-theme=light]) section{box-shadow:none}}

/* ── KPI ──────────────────────────────────────────────── */
.kpis{display:grid;gap:.75rem;grid-template-columns:repeat(auto-fit,minmax(152px,1fr))}
.kpi{background:var(--card);border:1px solid var(--line2);border-radius:9px;
  padding:.9rem 1rem;display:flex;flex-direction:column;gap:.25rem}
.kpi .lab{font-size:.7rem;font-weight:500;letter-spacing:.05em;color:var(--mut)}
.kpi .val{font-size:1.4rem;font-weight:600;letter-spacing:-.03em;line-height:1.2}
.kpi .delta{font-size:.76rem;color:var(--mut)}

/* ── 표 ───────────────────────────────────────────────── */
.scroll{overflow-x:auto;margin:0 -.5rem;padding:0 .5rem}
table{border-collapse:collapse;width:100%;min-width:500px;font-size:.87rem}
th{text-align:left;font-size:.7rem;font-weight:700;letter-spacing:.04em;color:var(--mut);
  padding:.6rem .7rem;border-bottom:1.5px solid var(--line);white-space:nowrap}
td{padding:.62rem .7rem;border-bottom:1px solid var(--line2);vertical-align:top;
  line-height:1.55}
tbody tr:last-child td{border-bottom:0}
tbody tr:hover td{background:var(--card2)}
td.n,th.n{text-align:right;white-space:nowrap}

/* ── 등락 색 ──────────────────────────────────────────── */
.up{color:var(--up);font-weight:600}
.down{color:var(--down);font-weight:600}
.flat{color:var(--flat)}
.up-bg{background:var(--ups)} .down-bg{background:var(--downs)}
.bar{height:5px;border-radius:3px;background:var(--acc);opacity:.8;min-width:2px}

/* ── 칩·주석 ──────────────────────────────────────────── */
.chip{display:inline-block;font-size:.68rem;font-weight:500;padding:.14rem .45rem;
  border-radius:4px;background:var(--card2);color:var(--fg2);
  border:1px solid var(--line2);white-space:nowrap;letter-spacing:0}
.chip.warn{background:var(--warns);color:var(--warn);border-color:var(--warn)}
.chip.up{background:var(--ups);color:var(--up);border-color:var(--up)}
.chip.down{background:var(--downs);color:var(--down);border-color:var(--down)}
.src{color:var(--mut);font-size:.72rem;line-height:1.5;word-break:break-all;
  font-weight:400}
.note{border-left:3px solid var(--warn);background:var(--warns);color:var(--warn);
  border-radius:0 7px 7px 0;padding:.8rem 1rem;font-size:.86rem;line-height:1.65}
.note.calm{border-left-color:var(--acc);background:var(--accs);color:var(--fg2)}
ul{margin:0;padding-left:1.15rem;display:flex;flex-direction:column;gap:.45rem;
  font-size:.89rem;line-height:1.7}
li::marker{color:var(--acc)}
footer{color:var(--mut);font-size:.78rem;border-top:1px solid var(--line);
  padding-top:1.2rem;line-height:1.7}
a{color:var(--acc)}
"""


_TPL = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
{fonts}
<title>포트폴리오 콕핏 — {on}</title><style>{css}</style></head><body><div class="w">
<header><h1>포트폴리오 콕핏</h1>
<div class="sub">{on} · 보유 현황 갱신 {updated} · 매매 판단은 사람이 합니다</div></header>
{body}
<footer>이 화면은 리서치 보조 산출물이며 투자 자문이 아닙니다.
1차 스크리너로만 사용하고, 판단에 직접 쓰는 숫자는 원문에서 재확인하십시오.<br>
생성: <code>python3 -m src.pipelines.cockpit</code></footer>
</div></body></html>"""


def _cell(obj: Sourced | Unavailable, fmt: str = "{}") -> str:
    obj = require_sourced("dashboard cell", obj)
    if isinstance(obj, Unavailable):
        return f'<span class="chip warn">{escape(obj.cite())}</span>'
    return f'{fmt.format(obj.value)}<br><span class="src">{escape(obj.cite())}</span>'


def _kpi(lab: str, val: str, delta: str = "") -> str:
    """delta 는 이미 마크업일 수 있다(등락 색). 호출부가 escape 책임을 진다."""
    inner = delta if delta.startswith("<") else escape(delta)
    d = f'<span class="delta">{inner}</span>' if delta else ""
    return f'<div class="kpi"><span class="lab">{escape(lab)}</span><span class="val">{val}</span>{d}</div>'


def _table(headers: list[str], rows: list[list[str]], numeric_from: int = 1) -> str:
    h = "".join(f'<th{" class=n" if i >= numeric_from else ""}>{escape(x)}</th>'
                for i, x in enumerate(headers))
    b = "".join("<tr>" + "".join(
        f'<td{" class=n" if i >= numeric_from else ""}>{c}</td>' for i, c in enumerate(r)
    ) + "</tr>" for r in rows)
    return f'<div class="scroll"><table><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table></div>'


def render_cockpit(r, out: Path = OUT) -> Path:
    """CockpitResult → HTML. 파이프라인이 호출한다."""
    from ..core.valuation.concentration import effective_positions, hhi
    from ..core.valuation.fx_exposure import sensitivity

    types = {s.value.ticker: s.value.asset_type for s in r.portfolio.holdings}
    h0, h1 = hhi(r.surface), hhi(r.effective)
    e0, e1 = effective_positions(r.surface), effective_positions(r.effective)
    firms = len([x for x in r.rows if x.total >= 1e-4])
    parts: list[str] = []

    parts.append('<div class="kpis">'
                 + _kpi("평가액", f"{r.total:,.0f}")
                 + _kpi("보유 종목", f"{len(r.surface)}")
                 + _kpi("유효 종목 수", f"{e1:.1f}", f"표면 {e0:.1f} → 룩스루 후")
                 + _kpi("실제 노출 기업", f"{firms:,}")
                 + _kpi("해외자산", f"{r.foreign:.0%}")
                 + "</div>")

    if r.notes:
        parts.append('<section><h2>확인 필요</h2><ul>'
                     + "".join(f"<li>{escape(n)}</li>" for n in r.notes) + "</ul></section>")

    rows = []
    mx = max(r.surface.values(), default=1)
    for t, w in sorted(r.surface.items(), key=lambda kv: -kv[1]):
        at = types[t]
        rows.append([f'{escape(t)} <span class="chip">{escape(at.value)}</span>',
                     f"{r.values[t]:,.0f}", f"{w:.2%}",
                     f'<div class="bar" style="width:{max(2, w / mx * 100):.0f}%"></div>'])
    parts.append("<section><h2>표면 구성</h2>"
                 + _table(["종목", "평가액", "비중", ""], rows)
                 + f'<p class="src">HHI {h0:.4f} · 유효 종목 수 {e0:.2f}개 '
                   f'(ETF를 한 덩어리로 셀 때)</p></section>')

    lt = []
    for x in r.rows:
        if x.total < 0.004:
            continue
        hid = f'<span class="hid">{x.hidden_ratio:.0%}</span>' if x.via_etf > 0 else "—"
        lt.append([escape(x.ticker), f"{x.direct:.2%}", f"{x.via_etf:.2%}",
                   f"<strong>{x.total:.2%}</strong>", f"{x.total - x.direct:+.2%}", hid])
    meta = "".join(f'<span class="chip">{escape(t)} {len(c)}종목 분해</span> '
                   for t, c in r.constituents.items())
    meta += "".join(f'<span class="chip">{escape(t)} 제외 · 주식 구성종목 없음</span> '
                    for t in r.skipped)
    meta += "".join(f'<span class="chip warn">{escape(m.cite())}</span> ' for m in r.missing)
    parts.append("<section><h2>숨은 중복 노출 (ETF 룩스루)</h2>"
                 + f'<p style="margin:0 0 .7rem">{meta}</p>'
                 + _table(["종목", "직접", "ETF 경유", "실질", "증가", "숨은 비율"], lt)
                 + f'<p class="src">HHI {h0:.4f} → {h1:.4f} · '
                   f'유효 종목 수 {e0:.2f}개 → {e1:.2f}개 · 보유 {len(r.surface)}종목 → '
                   f'실제 {firms:,}개 기업</p></section>')

    fx_rows = []
    if not isinstance(r.fx, Unavailable):
        fx_rows = [[f"{s.move:+.0%}", f"{s.krw_return:+.2%}"] for s in sensitivity(0.0, r.foreign)]
    parts.append("<section><h2>환노출</h2>"
                 + f'<p style="margin:0 0 .7rem">해외자산 {r.foreign:.0%} · USD/KRW '
                   f'{_cell(r.fx, "{:,.2f}")}</p>'
                 + (_table(["환율 변동", "원화 환산 수익률"], fx_rows) if fx_rows else "")
                 + "</section>")

    top = max(r.rows, key=lambda x: x.total, default=None)
    bullets = []
    if top:
        gap = top.total - r.surface.get(top.ticker, 0)
        bullets.append(f"{top.ticker} 실질 노출 {top.total:.1%} — 표면 "
                       f"{r.surface.get(top.ticker, 0):.1%}보다 {gap:.1%}p 높다. "
                       "기업 해독기·가격 판독기로 개별 점검 대상.")
    hidden = [x for x in r.rows if x.direct == 0 and x.via_etf >= 0.02]
    if hidden:
        bullets.append("직접 보유가 없는데 실질 노출 2% 이상: "
                       + ", ".join(f"{x.ticker} {x.total:.1%}" for x in hidden[:5]))
    bullets.append("비중을 정해주지 않습니다. 어디를 더 볼지만 제시합니다.")
    parts.append("<section><h2>더 파볼 지점</h2><ul>"
                 + "".join(f"<li>{escape(b)}</li>" for b in bullets) + "</ul></section>")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_TPL.format(on=r.on.isoformat(), css=_CSS, fonts=FONT_LINK,
                               updated=r.portfolio.updated or "미기재",
                               body="\n".join(parts)), encoding="utf-8")
    return out


def write(*, on: date, sections: list[str], out: Path = OUT) -> Path:
    """범용 렌더. 다른 파이프라인이 쓸 수 있게 남겨둔다."""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_TPL.format(on=on.isoformat(), css=_CSS, fonts=FONT_LINK, updated="—",
                               body="\n".join(sections)), encoding="utf-8")
    return out
