"""전체 대시보드 = 일일 브리핑(1층) + 포트폴리오 콕핏(1.5층).

Notion 원문의 '대시보드 갱신' 스킬에 대응한다.
`python3 -m src.pipelines.dashboard`
"""

from __future__ import annotations

from datetime import date
from html import escape
from pathlib import Path

from ..core.valuation.concentration import effective_positions, hhi
from ..core.valuation.fx_exposure import sensitivity
from ..provenance import Unavailable
from ..render.dashboard import _CSS, FONT_LINK, _kpi, _table
from . import cockpit as ck
from . import daily_brief as db
from . import event_scanner as es

OUT = Path("dashboard/index.html")

_TPL = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
{fonts}
<title>투자 리서치 대시보드 — {on}</title><style>{css}
nav{{display:flex;gap:.5rem;flex-wrap:wrap;font-size:.8rem}}
nav a{{color:var(--fg2);text-decoration:none;border:1px solid var(--line2);
 border-radius:99px;padding:.25rem .7rem;background:var(--card)}}
nav a:hover{{border-color:var(--acc);color:var(--acc)}}
.grid2{{display:grid;gap:1rem;grid-template-columns:repeat(auto-fit,minmax(300px,1fr))}}
.sig{{border-left:3px solid var(--acc);background:var(--accs);padding:.6rem .85rem;
 border-radius:0 5px 5px 0;font-size:.86rem;display:flex;flex-direction:column;gap:.15rem}}
.sig .k{{font-weight:650;color:var(--acc);font-size:.78rem}}
h3{{font-size:.88rem;font-weight:700;color:var(--fg2);margin:1.2rem 0 .55rem;
 letter-spacing:-.01em}}

/* ══ 종목 검색 ══════════════════════════════════════════ */
.search{{position:relative;margin:1.1rem 0 .3rem;max-width:620px}}
.search .fld{{display:flex;align-items:center;gap:.7rem;background:var(--card);
 border:2px solid var(--line);border-radius:14px;padding:.2rem .5rem .2rem 1.05rem;
 transition:border-color .12s, box-shadow .12s}}
.search .fld:focus-within{{border-color:var(--acc);
 box-shadow:0 0 0 4px color-mix(in srgb,var(--acc) 15%,transparent)}}
.search .ico{{width:20px;height:20px;flex:none;opacity:.5}}
.search input{{flex:1;min-width:0;font:inherit;font-size:1.06rem;font-weight:500;
 padding:.85rem 0;border:0;background:transparent;color:var(--fg);outline:none}}
.search input::placeholder{{color:var(--mut);font-weight:400}}
.search input::-webkit-search-cancel-button{{display:none}}
.search .clr{{border:0;background:transparent;color:var(--mut);font-size:1.3rem;
 cursor:pointer;padding:.2rem .6rem;line-height:1;border-radius:8px}}
.search .clr:hover{{background:var(--card2);color:var(--fg)}}
.search .hint{{font-size:.78rem;color:var(--mut);margin:.45rem 0 0 .3rem}}

.sr{{position:absolute;z-index:60;left:0;right:0;top:calc(100% + .5rem);
 background:var(--card);border:1px solid var(--line);border-radius:14px;
 box-shadow:0 16px 44px -14px rgba(0,0,0,.4);overflow:hidden;
 max-height:400px;overflow-y:auto}}
.sr .row{{display:grid;grid-template-columns:1fr auto;align-items:center;
 gap:.4rem 1rem;padding:.85rem 1.15rem;cursor:pointer;
 border-bottom:1px solid var(--line2)}}
.sr .row:last-child{{border-bottom:0}}
.sr .row:hover,.sr .row.on{{background:var(--accs)}}
.sr .nm{{font-size:1rem;font-weight:600;line-height:1.35;color:var(--fg)}}
.sr .nm b{{color:var(--acc)}}
.sr .meta{{grid-column:1;display:flex;gap:.5rem;align-items:center;margin-top:.1rem}}
.sr .tk{{font-family:"IBM Plex Mono",monospace;font-size:.79rem;color:var(--mut);
 letter-spacing:.02em}}
.sr .mk{{font-size:.7rem;color:var(--mut);background:var(--card2);
 padding:.08rem .38rem;border-radius:4px;border:1px solid var(--line2)}}
.sr .act{{grid-row:1/3;font-size:.75rem;font-weight:600;color:var(--acc);
 white-space:nowrap;display:flex;align-items:center;gap:.3rem}}
.sr .act.new{{color:var(--mut);font-weight:400}}
.sr .empty{{padding:1.1rem 1.15rem;color:var(--mut);font-size:.9rem}}

/* ── 진행 상태 ─────────────────────────────────────── */
.prog{{margin-top:.75rem;border-radius:12px;border:1px solid var(--line2);
 background:var(--card);overflow:hidden}}
.prog .top{{display:flex;align-items:center;gap:.7rem;padding:.9rem 1.1rem}}
.prog .spin{{width:17px;height:17px;flex:none;border:2.5px solid var(--line);
 border-top-color:var(--acc);border-radius:50%;animation:sp .8s linear infinite}}
@keyframes sp{{to{{transform:rotate(360deg)}}}}
@media(prefers-reduced-motion:reduce){{.prog .spin{{animation:none}}}}
.prog .ttl{{font-weight:600;font-size:.95rem}}
.prog .sub2{{font-size:.8rem;color:var(--mut);margin-top:.12rem}}
.prog .steps{{border-top:1px solid var(--line2);padding:.7rem 1.1rem;
 display:flex;flex-direction:column;gap:.4rem;font-size:.84rem;color:var(--mut)}}
.prog .steps .s{{display:flex;gap:.55rem;align-items:center}}
.prog .steps .s.done{{color:var(--fg2)}}
.prog .steps .s.now{{color:var(--acc);font-weight:600}}
.prog .steps .mk2{{width:1.1rem;text-align:center;flex:none}}
.prog.ok{{border-color:var(--acc)}} .prog.bad{{border-color:var(--warn)}}
.prog .msg{{padding:.9rem 1.1rem;font-size:.88rem;line-height:1.65}}
.prog.bad .msg{{color:var(--warn);background:var(--warns)}}
.prog .msg a{{color:var(--acc);font-weight:600}}
a.stk{{color:inherit;text-decoration:none;display:inline-flex;flex-direction:column;gap:.2rem}}
a.stk .go{{font-size:.7rem;font-weight:600;color:var(--acc)}}
a.stk .go.new{{color:var(--mut);font-weight:400}}
a.stk:hover .go{{text-decoration:underline}}
</style></head><body><div class="w">
<header><h1>투자 리서치 대시보드</h1>
<div class="sub">{on} · 매매 판단은 사람이 합니다. 이 화면은 어디를 더 볼지만 제시합니다.</div>
<div class="search">
 <div class="fld">
  <svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"
   stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></svg>
  <input id="q" type="search" placeholder="회사 이름이나 티커로 검색  ·  삼성전자, 엔비디아, NVDA"
   autocomplete="off" spellcheck="false" aria-label="종목 검색">
  <button class="clr" id="qc" type="button" hidden aria-label="지우기">&times;</button>
 </div>
 <div class="hint">종목을 고르면 재무·공시를 받아 분석 페이지를 만들어 새 창으로 엽니다.</div>
 <div id="sr" class="sr" hidden></div>
 <div id="prog" class="prog" hidden></div>
</div>
<nav><a href="#market">증시 현황</a><a href="#macro">매크로</a><a href="#holdings">보유 종목</a>
<a href="#major">주요 종목</a><a href="#events">이벤트</a>{watchnav}<a href="#signals">액션 신호</a><a href="#cockpit">포트폴리오</a></nav>
</header>
{body}
<script>
(function(){{
 var q=document.getElementById('q'), qc=document.getElementById('qc'),
     sr=document.getElementById('sr'), pg=document.getElementById('prog'),
     items=[], cur=-1, timer=null, busy=false;

 function esc(s){{return String(s).replace(/[&<>"]/g,function(c){{
   return {{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}}[c];}});}}
 function hl(s,t){{var i=s.toLowerCase().indexOf(t.toLowerCase());
   return i<0?esc(s):esc(s.slice(0,i))+'<b>'+esc(s.slice(i,i+t.length))+'</b>'+esc(s.slice(i+t.length));}}

 /* ── 진행 상태 ── */
 var STEPS=[['재무·공시 수집','SEC/DART · 토스 시세'],
            ['세그먼트·역DCF 계산','매출 구성 · 요구 성장률'],
            ['서사 작성','Claude 가 해석을 씁니다'],
            ['페이지 렌더','']];
 var tick=null, at=0, t0=0;
 function drawProg(name){{
   var el=STEPS.map(function(s,i){{
     var cls=i<at?'done':(i===at?'now':'');
     var mk=i<at?'✓':(i===at?'▸':'·');
     return '<div class="s '+cls+'"><span class="mk2">'+mk+'</span><span>'+esc(s[0])
       +(s[1]?' <span style="opacity:.7">— '+esc(s[1])+'</span>':'')+'</span></div>';
   }}).join('');
   var el2=Math.round((Date.now()-t0)/1000);
   pg.className='prog'; pg.hidden=false;
   pg.innerHTML='<div class="top"><div class="spin"></div>'
     +'<div><div class="ttl">'+esc(name)+' 분석 중…</div>'
     +'<div class="sub2">'+el2+'초 경과 · 보통 30초~2분 걸립니다. 이 창을 닫지 마세요.</div></div></div>'
     +'<div class="steps">'+el+'</div>';
 }}
 function startProg(name){{
   at=0; t0=Date.now(); drawProg(name);
   if(tick) clearInterval(tick);
   tick=setInterval(function(){{
     var s=(Date.now()-t0)/1000;
     at = s<12?0 : s<28?1 : s<95?2 : 3;
     drawProg(name);
   }},1000);
 }}
 function stopProg(){{ if(tick){{clearInterval(tick);tick=null;}} }}
 function done(html,bad){{
   stopProg(); pg.hidden=false;
   pg.className='prog '+(bad?'bad':'ok');
   pg.innerHTML='<div class="msg">'+html+'</div>';
 }}

 /* ── 검색 ── */
 function draw(term){{
   if(!items.length){{
     sr.innerHTML='<div class="empty">일치하는 종목이 없습니다.</div>'; sr.hidden=false; return;
   }}
   sr.innerHTML=items.map(function(r,i){{
     return '<div class="row'+(i===cur?' on':'')+'" data-i="'+i+'">'
       +'<div class="nm">'+hl(r.n,term)+'</div>'
       +'<div class="act'+(r.ready?'':' new')+'">'+(r.ready?'분석 완료 ↗':'분석 생성 →')+'</div>'
       +'<div class="meta"><span class="tk">'+esc(r.s)+'</span>'
       +'<span class="mk">'+esc(r.m)+'</span></div></div>';
   }}).join('');
   sr.hidden=false;
   Array.prototype.forEach.call(sr.querySelectorAll('.row'),function(el){{
     el.onclick=function(){{pick(items[+el.dataset.i]);}};
   }});
 }}
 function pick(r){{
   if(busy) return;
   sr.hidden=true; q.blur();
   if(r.ready){{
     window.open('stocks/'+r.s+'.html','_blank');
     done('<b>'+esc(r.n)+'</b> 분석 페이지를 새 창으로 열었습니다. '
       +'최신으로 다시 만들려면 <a href="#" id="rg">여기</a>를 누르세요.');
     var rg=document.getElementById('rg');
     if(rg) rg.onclick=function(e){{e.preventDefault();run(r,true);}};
     return;
   }}
   run(r,false);
 }}
 function run(r,force){{
   busy=true; startProg(r.n);
   fetch('/api/generate?t='+encodeURIComponent(r.s)+(force?'&force=1':''))
    .then(function(x){{return x.json();}})
    .then(function(d){{
      busy=false;
      if(d.ok){{
        done('<b>'+esc(r.n)+'</b> 분석 완료 — '+esc(d.note||(d.narrative?'서사 포함':'사실만'))
          +'. 새 창으로 엽니다.');
        window.open(d.url,'_blank');
      }} else done('생성 실패: '+esc(d.error||''),true);
    }})
    .catch(function(){{
      busy=false;
      done('이 페이지를 파일로 직접 열면 분석 생성이 동작하지 않습니다.<br>'
        +'터미널에서 <code>python3 -m src.pipelines.serve</code> 를 실행한 뒤 '
        +'<a href="http://127.0.0.1:8766">http://127.0.0.1:8766</a> 으로 접속하세요.',true);
    }});
 }}

 q.addEventListener('input',function(){{
   var t=q.value.trim(); cur=-1; qc.hidden=!t;
   if(timer) clearTimeout(timer);
   if(!t){{items=[];sr.hidden=true;return;}}
   timer=setTimeout(function(){{
     fetch('/api/search?q='+encodeURIComponent(t))
      .then(function(x){{return x.json();}})
      .then(function(d){{items=d;draw(t);}})
      .catch(function(){{
        items=[]; sr.hidden=true;
        done('검색은 로컬 서버에서 동작합니다. 터미널에서 '
          +'<code>python3 -m src.pipelines.serve</code> 실행 후 '
          +'<a href="http://127.0.0.1:8766">http://127.0.0.1:8766</a> 으로 접속하세요.',true);
      }});
   }},150);
 }});
 qc.addEventListener('click',function(){{q.value='';qc.hidden=true;items=[];sr.hidden=true;q.focus();}});
 q.addEventListener('keydown',function(e){{
   if(sr.hidden||!items.length) return;
   if(e.key==='ArrowDown'){{cur=Math.min(cur+1,items.length-1);draw(q.value.trim());e.preventDefault();}}
   else if(e.key==='ArrowUp'){{cur=Math.max(cur-1,0);draw(q.value.trim());e.preventDefault();}}
   else if(e.key==='Enter'){{if(cur<0)cur=0;pick(items[cur]);e.preventDefault();}}
   else if(e.key==='Escape'){{sr.hidden=true;}}
 }});
 document.addEventListener('click',function(e){{if(!e.target.closest('.search'))sr.hidden=true;}});

 /* ── 이벤트 표의 종목 클릭도 같은 흐름으로 ── */
 Array.prototype.forEach.call(document.querySelectorAll('a.stk'),function(a){{
   a.addEventListener('click',function(e){{
     var st=a.dataset.state, r={{s:a.dataset.t,n:a.dataset.n,ready:(st==='full')}};
     if(st==='full') return;              // 서사까지 있으면 그냥 새 창
     e.preventDefault();
     window.scrollTo({{top:0,behavior:'smooth'}});
     run(r,false);
   }});
 }});
}})();
</script>
<footer>리서치 보조 산출물이며 투자 자문이 아닙니다. 1차 스크리너로만 사용하고,
판단에 직접 쓰는 숫자는 원문에서 재확인하십시오.<br>
생성: <code>python3 -m src.pipelines.dashboard</code></footer>
</div></body></html>"""


def _mv(x) -> str:
    if x is None:
        return '<span class="src">확인 필요</span>'
    cls = "up" if x > 0 else ("down" if x < 0 else "")
    return f'<span class="{cls}">{x:+.2%}</span>'


def _name_cell(symbol: str, names: dict[str, str]) -> str:
    n = names.get(symbol.upper(), "")
    if not n or n == symbol:
        return f'<strong>{escape(symbol)}</strong>'
    return (f'<strong>{escape(n)}</strong><br>'
            f'<span class="src" style="font-size:.68rem">{escape(symbol)}</span>')


def _rank_table(v, title: str, names: dict[str, str] | None = None) -> str:
    names = names or {}
    if isinstance(v, Unavailable):
        return f'<h3>{escape(title)}</h3><p class="src">{escape(v.cite())}</p>'
    rows = [[f"{x['rank']}", _name_cell(x["symbol"], names),
             f"{x['last']:,.2f}", _mv(x["change_rate"])]
            for x in v.value]
    return (f'<h3>{escape(title)}</h3>'
            + _table(["#", "종목", "현재가", "변동"], rows, numeric_from=2)
            + f'<p class="src">{escape(v.cite())}</p>')


def render(b: db.BriefResult, c, out: Path = OUT, *, public: bool = False,
           scan=None) -> Path:
    """public=True 면 개인 자산 정보를 제외한다.

    제외: 보유 종목 목록·수량·평가액, 콕핏의 평가액과 종목별 비중,
          관심 종목 섹션 전체, 관심 종목에서 파생된 액션 신호.
    유지: 증시 현황·매크로·주요 종목 랭킹(공개 시장 데이터)·집중도 지표(비율만).
    공개 호스팅에 올릴 때 쓴다. 시스템 구조는 보여주되 내 포지션은 보여주지 않는다.
    """
    P: list[str] = []

    # KPI
    kpis = []
    for sym, (s, mv) in b.kr_indices.items():
        r = None if isinstance(mv, Unavailable) else mv.value
        kpis.append(_kpi(sym, f"{s.value:,.0f}", _mv(r) if r is not None else "확인 필요"))
    for sym, (label, s, mv) in list(b.us_indices.items())[:2]:
        r = None if isinstance(mv, Unavailable) else mv.value
        kpis.append(_kpi(f"{label} ({sym})", f"{s.value:,.2f}",
                         _mv(r) if r is not None else "확인 필요"))
    if not isinstance(b.fx_toss, Unavailable):
        kpis.append(_kpi("USD/KRW", f"{b.fx_toss.value:,.2f}", "토스 장중"))
    P.append(f'<div class="kpis" id="market">{"".join(kpis)}</div>')
    if public:
        P.append('<div class="note">공개 모드입니다. 보유 종목·수량·평가액은 표시하지 않습니다. '
                 '증시 현황·주요 종목·관심 종목·액션 신호와 집중도 지표만 보여줍니다.</div>')

    if b.notes:
        P.append('<section><h2>확인 필요</h2><ul>'
                 + "".join(f"<li>{escape(n)}</li>" for n in b.notes) + "</ul></section>")

    # 증시 현황
    kr_rows = [[escape(s), f"{v.value:,.2f}",
                _mv(None if isinstance(m, Unavailable) else m.value)]
               for s, (v, m) in b.kr_indices.items()]
    us_rows = [[f"{escape(lab)} <span class=chip>{escape(s)}</span>", f"{v.value:,.2f}",
                _mv(None if isinstance(m, Unavailable) else m.value)]
               for s, (lab, v, m) in b.us_indices.items()]
    P.append('<section><h2>증시 현황</h2><div class="grid2">'
             + f'<div><h3>한국</h3>{_table(["지수", "현재", "변동"], kr_rows)}</div>'
             + f'<div><h3>미국 <span class="chip warn">대표 ETF 대용치</span></h3>'
               f'{_table(["지수", "현재", "변동"], us_rows)}</div></div></section>')

    # 매크로
    mrows = []
    labels = {"us10y": "미 10년물", "us2y": "미 2년물", "fedfunds": "연방기금금리"}
    for k, v in b.macro.items():
        mrows.append([escape(labels.get(k, k)),
                      f'<span class="src">{escape(v.cite())}</span>' if isinstance(v, Unavailable)
                      else f'{v.value:,.3f}%<br><span class="src">{escape(v.cite())}</span>'])
    for lab, v in (("USD/KRW 장중", b.fx_toss), ("USD/KRW ECB 종가", b.fx_ecb)):
        mrows.append([escape(lab),
                      f'<span class="src">{escape(v.cite())}</span>' if isinstance(v, Unavailable)
                      else f'{v.value:,.2f}<br><span class="src">{escape(v.cite())}</span>'])
    P.append(f'<section id="macro"><h2>매크로·환율</h2>{_table(["지표", "값 · 출처"], mrows)}</section>')

    # 보유 종목
    if b.holdings_rows and not public:
        hr = []
        for h in sorted(b.holdings_rows, key=lambda x: -(abs(x["rate"] or 0))):
            hr.append([_name_cell(h["ticker"], b.names)
                       + f' <span class="chip">{escape(h["name"])}</span>',
                       f'{h["price"].value:,.2f}' if h["price"] else "확인 필요",
                       _mv(h["rate"]),
                       f'{h["value"]:,.0f}' if h["value"] else "—"])
        P.append('<section id="holdings"><h2>보유 종목 밤사이 움직임</h2>'
                 + _table(["종목", "현재가", "변동", "평가액"], hr) + '</section>')

    # 주요 종목
    P.append('<section id="major"><h2>주요 종목 <span class="chip">보유 외</span></h2>'
             '<div class="grid2">'
             + f'<div>{_rank_table(b.rankings.get("KR_amount"), "한국 거래대금 상위", b.names)}</div>'
             + f'<div>{_rank_table(b.rankings.get("US_amount"), "미국 거래대금 상위", b.names)}</div>'
             + f'<div>{_rank_table(b.rankings.get("KR_losers"), "한국 급락", b.names)}</div>'
             + f'<div>{_rank_table(b.rankings.get("US_gainers"), "미국 급등", b.names)}</div>'
             + '</div></section>')

    # 관심 종목 · 실적 캘린더 (공개 모드에서는 통째로 제외)
    if b.watch and not public:
        wr = []
        soon_t = {x.value.ticker for x in b.earnings_soon}
        for it in sorted(b.watch, key=lambda x: x.value.earnings_date or date.max):
            w = it.value
            d = w.days_to_earnings(b.on)
            if w.earnings_date is None:
                when = '<span class="src">미기재</span>'
            elif d in (None,) or d < 0:
                when = f'<span class="src">{w.earnings_date} (지남)</span>'
            else:
                cls = "chip warn" if w.ticker in soon_t else "chip"
                lbl = "오늘" if d == 0 else f"D-{d}"
                when = (f'{w.earnings_date} <span class="{cls}">{lbl}</span> '
                        f'<span class="chip">{w.certainty}</span>')
            wr.append([_name_cell(w.ticker, b.names), when, escape(w.note or "—")])
        P.append('<section id="watch"><h2>관심 종목 · 실적 캘린더</h2>'
                 '<p class="src" style="margin:0 0 .8rem">무료 실적 캘린더 소스가 없어 '
                 'portfolio/watchlist.yaml 에 수동 입력한다. 확정/추정을 구분해 표기한다.</p>'
                 + _table(["종목", "실적일", "관찰 포인트"], wr, numeric_from=3)
                 + '</section>')

    # 이벤트 스캐너 — 왜 이 종목을 봐야 하는가 + 시나리오
    if scan and scan.candidates:
        ranked = [x for x in scan.candidates if x.events and not (public and x.held)]
        if ranked:
            rows = []
            import os
            from ..narrative_io import load as _ldn
            for x in ranked:
                nm = _name_cell(x.ticker, b.names)
                has_page = os.path.exists(f"dashboard/stocks/{x.ticker}.html")
                has_nar = not _ldn(x.ticker).is_empty
                state = "full" if (has_page and has_nar) else ("facts" if has_page else "none")
                nm = (f'<a class="stk" href="stocks/{escape(x.ticker)}.html" '
                      f'data-t="{escape(x.ticker)}" data-n="{escape(x.name)}" '
                      f'data-state="{state}" target="_blank">{nm} '
                      + ('<span class="go">분석 보기 ↗</span>' if state == "full"
                         else '<span class="go new">분석 생성 →</span>') + '</a>')
                if x.held and not public:
                    nm += ' <span class="chip">보유</span>'
                tags = " ".join(
                    f'<span class="chip{" warn" if e.severity >= 3 else ""}">'
                    f'{escape(e.tag)}</span>' for e in sorted(x.events, key=lambda e: -e.severity))
                detail = escape(" · ".join(e.detail for e in
                                           sorted(x.events, key=lambda e: -e.severity)))
                rows.append([nm, f"{x.price:,.2f}", _mv(x.change),
                             f'{tags}<br><span class="src">{detail}</span>'])
            P.append('<section id="events"><h2>지금 볼 이유가 있는 종목</h2>'
                     '<p class="src" style="margin:0 0 .8rem">보유·관심 종목과 시장 랭킹을 '
                     '후보로 두고 관측 가능한 이벤트를 태그로 붙였습니다. 예측이 아닙니다.</p>'
                     + _table(["종목", "현재가", "변동", "왜 봐야 하나"], rows, numeric_from=1)
                     + "</section>")
    # 액션 신호
    watch_tickers = {w.value.ticker for w in b.watch}
    shown = [s for s in b.signals
             if not (public and s.ticker and s.ticker in watch_tickers)]
    hidden_n = len(b.signals) - len(shown)
    sigs = "".join(
        f'<div class="sig"><span class="k">{escape(s.kind.value)}'
        + (f' · {escape(s.ticker)}' if s.ticker else "") + "</span>"
        f'<span>{escape(s.reason)}</span></div>' for s in shown)
    if public and hidden_n:
        sigs += (f'<div class="sig"><span class="k">비공개</span>'
                 f'<span>관심 종목에서 파생된 신호 {hidden_n}건은 공개본에 표시하지 않습니다.</span></div>')
    P.append('<section id="signals"><h2>오늘의 액션 신호</h2>'
             '<p class="src" style="margin:0 0 .8rem">매수·매도 신호는 구조적으로 생성되지 않습니다. '
             '어느 딥다이브를 돌릴지만 제시합니다.</p>'
             f'<div style="display:flex;flex-direction:column;gap:.5rem">{sigs or "추가로 파볼 항목 없음."}</div>'
             '</section>')

    # 콕핏
    if not isinstance(c, Unavailable):
        h0, h1 = hhi(c.surface), hhi(c.effective)
        e0, e1 = effective_positions(c.surface), effective_positions(c.effective)
        lt = ([] if public else
              [[escape(x.ticker), f"{x.direct:.2%}", f"{x.via_etf:.2%}",
                f"<strong>{x.total:.2%}</strong>", f"{x.total - x.direct:+.2%}"]
               for x in c.rows if x.total >= 0.004])
        fx_rows = ([[f"{s.move:+.0%}", f"{s.krw_return:+.2%}"]
                    for s in sensitivity(0.0, c.foreign)]
                   if not isinstance(c.fx, Unavailable) else [])
        P.append('<section id="cockpit"><h2>포트폴리오 콕핏</h2>'
                 + f'<div class="kpis" style="margin-bottom:1rem">'
                   + ("" if public else _kpi("평가액", f"{c.total:,.0f}"))
                   + f'{_kpi("유효 종목 수", f"{e1:.1f}", f"표면 {e0:.1f} → 룩스루 후")}'
                   f'{_kpi("실제 노출 기업", f"{len([x for x in c.rows if x.total >= 1e-4]):,}")}'
                   f'{_kpi("해외자산", f"{c.foreign:.0%}")}</div>'
                 + ('<p class="src">종목별 노출 내역은 공개 모드에서 표시하지 않습니다.</p>'
                    if public else
                    '<h3>숨은 중복 노출 (ETF 룩스루)</h3>'
                    + _table(["종목", "직접", "ETF 경유", "실질", "증가"], lt))
                 + f'<p class="src">HHI {h0:.4f} → {h1:.4f} · 유효 종목 수 {e0:.2f} → {e1:.2f}개</p>'
                 + ('<h3>환율 시나리오</h3>' + _table(["환율 변동", "원화 환산"], fx_rows)
                    if fx_rows else "")
                 + '</section>')

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_TPL.format(on=b.on.isoformat(), css=_CSS, fonts=FONT_LINK, body="\n".join(P),
                               watchnav="" if public else '<a href="#watch">관심 종목</a>'),
                   encoding="utf-8")
    return out


if __name__ == "__main__":
    import sys
    public = "--public" in sys.argv
    on = date.today()
    b = db.run(on)
    c = ck.run(on=on)
    db.REPORT_DIR.mkdir(parents=True, exist_ok=True)
    md = db.REPORT_DIR / f"{on.isoformat()}-brief.md"
    md.write_text(db.to_markdown(b), encoding="utf-8")
    if not isinstance(c, Unavailable):
        ck.write_outputs.__wrapped__ if False else None
        ck.REPORT_DIR.mkdir(parents=True, exist_ok=True)
        (ck.REPORT_DIR / f"{on.isoformat()}-cockpit.md").write_text(
            ck.to_markdown(c), encoding="utf-8")
    scan = es.run(on)
    # 이벤트가 있는 상위 종목의 상세 페이지를 생성한다 (스토리 리더는 무거워 제외)
    from . import stock_page as sp
    made = []
    for cand in [x for x in scan.candidates if x.events][:5]:
        try:
            pg = sp.build(cand.ticker, on, with_story=False)
            if not isinstance(pg, Unavailable):
                sp.render(pg); made.append(cand.ticker)
        except Exception as exc:                     # 한 종목 실패가 전체를 막지 않게
            print(f"  ⚠ {cand.ticker} 상세 페이지 실패: {type(exc).__name__}")
    if made:
        print(f"  종목 페이지 {len(made)}개: {', '.join(made)}")
    (es.REPORT_DIR).mkdir(parents=True, exist_ok=True)
    (es.REPORT_DIR / f"{on.isoformat()}-events.md").write_text(
        es.to_markdown(scan), encoding="utf-8")
    html = render(b, c, Path("dashboard/public.html") if public else OUT,
                  public=public, scan=scan)
    print(f"✓ {html} ({html.stat().st_size:,} bytes)"
          + ("  [공개 모드 — 보유 정보 제외]" if public else ""))
    print(f"✓ {md}")
    print(f"  한국 지수 {len(b.kr_indices)} · 미국 대용치 {len(b.us_indices)} · "
          f"보유 {len(b.holdings_rows)} · 랭킹 {sum(1 for v in b.rankings.values() if not isinstance(v, Unavailable))}/6 · "
          f"신호 {len(b.signals)} · 이벤트 종목 {len([x for x in scan.candidates if x.events])}")
