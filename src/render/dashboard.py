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

_CSS = """
:root{--bg:#eef1f3;--card:#fbfcfd;--card2:#e2e8ea;--fg:#141c22;--fg2:#3c4b55;--mut:#68787f;
 --line:#c6d0d5;--line2:#d8e0e4;--acc:#0f6b60;--accs:#d6e8e4;--warn:#8c6518;--warns:#efe3c8;--up:#0f6b60;}
@media(prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#0e151a;--card:#161f26;--card2:#1e2a32;
 --fg:#e4ebee;--fg2:#aebcc4;--mut:#7d8d96;--line:#2b3a43;--line2:#223038;--acc:#4fc4b2;--accs:#12332f;
 --warn:#d6a83f;--warns:#312716;--up:#4fc4b2;}}
:root[data-theme=dark]{--bg:#0e151a;--card:#161f26;--card2:#1e2a32;--fg:#e4ebee;--fg2:#aebcc4;--mut:#7d8d96;
 --line:#2b3a43;--line2:#223038;--acc:#4fc4b2;--accs:#12332f;--warn:#d6a83f;--warns:#312716;--up:#4fc4b2;}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--fg);margin:0;padding:2rem 1.25rem 5rem;
 font:16px/1.6 ui-sans-serif,system-ui,"Segoe UI","Malgun Gothic",sans-serif;-webkit-font-smoothing:antialiased}
.w{max-width:920px;margin:0 auto;display:flex;flex-direction:column;gap:2rem}
header{display:flex;flex-direction:column;gap:.35rem;border-bottom:2px solid var(--fg);padding-bottom:1.1rem}
h1{font-size:1.5rem;font-weight:650;margin:0;letter-spacing:-.02em}
h2{font-size:1.05rem;font-weight:650;margin:0 0 .7rem;letter-spacing:-.01em}
.sub{color:var(--mut);font-size:.83rem}
.kpis{display:grid;gap:.7rem;grid-template-columns:repeat(auto-fit,minmax(150px,1fr))}
.kpi{background:var(--card);border:1px solid var(--line2);border-radius:6px;padding:.85rem .95rem;
 display:flex;flex-direction:column;gap:.2rem}
.kpi .lab{font-size:.7rem;text-transform:uppercase;letter-spacing:.09em;color:var(--mut)}
.kpi .val{font-size:1.35rem;font-weight:650;font-variant-numeric:tabular-nums;letter-spacing:-.02em}
.kpi .delta{font-size:.75rem;color:var(--acc);font-variant-numeric:tabular-nums}
section{background:var(--card);border:1px solid var(--line2);border-radius:8px;padding:1.15rem 1.25rem}
.scroll{overflow-x:auto;margin:0 -.4rem;padding:0 .4rem}
table{border-collapse:collapse;width:100%;min-width:520px;font-size:.86rem}
th{text-align:left;font-size:.68rem;text-transform:uppercase;letter-spacing:.08em;color:var(--mut);
 padding:.55rem .6rem;border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:.55rem .6rem;border-bottom:1px solid var(--line2);vertical-align:top}
tbody tr:last-child td{border-bottom:0}
tbody tr:hover td{background:var(--card2)}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.bar{height:4px;border-radius:2px;background:var(--acc);opacity:.85;min-width:2px}
.hid{color:var(--acc);font-weight:600}
.chip{display:inline-block;font-size:.68rem;padding:.1rem .4rem;border-radius:3px;
 background:var(--card2);color:var(--fg2);border:1px solid var(--line2);white-space:nowrap}
.warn{background:var(--warns);color:var(--warn);border-color:var(--warn)}
.note{border-left:3px solid var(--warn);background:var(--warns);color:var(--warn);
 border-radius:0 5px 5px 0;padding:.7rem .9rem;font-size:.85rem}
.note.calm{border-left-color:var(--acc);background:var(--accs);color:var(--fg2)}
.src{color:var(--mut);font-size:.7rem;word-break:break-all;line-height:1.4}
ul{margin:0;padding-left:1.1rem;display:flex;flex-direction:column;gap:.4rem;font-size:.88rem}
li::marker{color:var(--acc)}
footer{color:var(--mut);font-size:.78rem;border-top:1px solid var(--line);padding-top:1rem}
"""

_TPL = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
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
    d = f'<span class="delta">{escape(delta)}</span>' if delta else ""
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
    out.write_text(_TPL.format(on=r.on.isoformat(), css=_CSS,
                               updated=r.portfolio.updated or "미기재",
                               body="\n".join(parts)), encoding="utf-8")
    return out


def write(*, on: date, sections: list[str], out: Path = OUT) -> Path:
    """범용 렌더. 다른 파이프라인이 쓸 수 있게 남겨둔다."""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_TPL.format(on=on.isoformat(), css=_CSS, updated="—",
                               body="\n".join(sections)), encoding="utf-8")
    return out
