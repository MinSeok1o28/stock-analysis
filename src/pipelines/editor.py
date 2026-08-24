"""포트폴리오 편집기 — 브라우저에서 보유 종목을 고치고 대시보드를 갱신한다.

`python3 -m src.pipelines.editor` → http://127.0.0.1:8765

## 설계 결정
- **holdings.yaml 이 여전히 유일한 진실의 원천이다.** 이 편집기는 YAML 을 대체하지 않고
  같은 파일을 읽고 쓴다. 브라우저에 별도 상태를 두면 진실이 둘이 된다.
- **127.0.0.1 에만 바인딩한다.** 파일을 쓰는 서버이므로 외부에 노출하지 않는다.
- 저장 전 `portfolio_io` 로 검증한다. 잘못된 입력은 저장되지 않고 이유가 화면에 뜬다.
- 저장할 때마다 `portfolio/snapshots/<날짜>.yaml` 을 남긴다 (수익 기여도의 전제).
- 종목을 추가하면 토스에서 종목명을 조회해 자동으로 채운다.

stdlib http.server 만 쓴다 (Flask 등 의존성 없음).
"""

from __future__ import annotations

import json
import traceback
import urllib.parse
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

from ..models import AssetType, Market
from ..portfolio_io import (HOLDINGS_PATH, PortfolioError, load_holdings,
                            write_snapshot)
from ..provenance import Unavailable
from ..render.dashboard import _CSS
from ..sources import toss

HOST, PORT = "127.0.0.1", 8765

_PAGE = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>포트폴리오 편집</title><style>{css}
form{{display:flex;flex-direction:column;gap:1rem}}
.row{{display:grid;gap:.5rem;grid-template-columns:1.1fr 1.6fr 1.2fr .9fr .8fr .8fr .8fr auto;align-items:end}}
label{{display:flex;flex-direction:column;gap:.2rem;font-size:.7rem;color:var(--mut);
 text-transform:uppercase;letter-spacing:.07em}}
input,select{{font:inherit;font-size:.88rem;padding:.42rem .55rem;border-radius:5px;
 border:1px solid var(--line);background:var(--card);color:var(--fg);width:100%}}
input:focus,select:focus{{outline:2px solid var(--acc);outline-offset:1px;border-color:var(--acc)}}
button{{font:inherit;font-size:.85rem;font-weight:600;padding:.5rem .95rem;border-radius:6px;
 border:1px solid var(--line);background:var(--card);color:var(--fg);cursor:pointer}}
button:hover{{border-color:var(--acc);color:var(--acc)}}
button.primary{{background:var(--acc);border-color:var(--acc);color:var(--bg)}}
button.del{{border-color:var(--warn);color:var(--warn);padding:.5rem .7rem}}
.bar{{display:flex;gap:.6rem;flex-wrap:wrap;align-items:center}}
.msg{{border-left:3px solid var(--acc);background:var(--accs);color:var(--fg2);
 padding:.7rem .95rem;border-radius:0 5px 5px 0;font-size:.87rem;white-space:pre-wrap}}
.msg.bad{{border-left-color:var(--warn);background:var(--warns);color:var(--warn)}}
@media(max-width:820px){{.row{{grid-template-columns:1fr 1fr;}}}}
</style></head><body><div class="w">
<header><h1>포트폴리오 편집</h1>
<div class="sub">{path} · 저장하면 스냅샷을 남기고 대시보드를 다시 만듭니다</div></header>
{msg}
<section><form method="post" action="/save">
<div id="rows">{rows}</div>
<div class="bar">
 <button type="button" onclick="addRow()">+ 종목 추가</button>
 <button type="submit" class="primary">저장 · 대시보드 갱신</button>
 <span class="src">기준통화 <input name="base_currency" value="{ccy}" style="width:5rem;display:inline-block"></span>
</div></form></section>
<section><h2>안내</h2><ul>
<li><b>asset_type</b> 이 평가 잣대를 결정합니다 — 개별주·리츠만 역DCF가 가능하고,
 지수·섹터 ETF는 룩스루 대상이며, 원자재 ETF는 실질금리·달러 방향으로 봅니다.</li>
<li>티커만 넣고 저장하면 종목명을 토스에서 자동으로 채웁니다.</li>
<li>이 화면은 <code>portfolio/holdings.yaml</code> 을 직접 고칩니다. 파일이 계속 유일한 원본입니다.</li>
<li>서버는 127.0.0.1 에만 열립니다. 다른 기기에서는 접속되지 않습니다.</li>
</ul></section>
<footer><a href="/dashboard">→ 대시보드 보기</a> · 종료는 터미널에서 Ctrl+C</footer>
</div>
<script>
const TYPES={types};
function rowHtml(i,d){{d=d||{{}};return `<div class="row" data-i="${{i}}">
<label>티커<input name="ticker_${{i}}" value="${{d.ticker||''}}" placeholder="AAPL / 005930" required></label>
<label>종목명 <span style="text-transform:none">(비우면 자동)</span><input name="name_${{i}}" value="${{d.name||''}}"></label>
<label>자산유형<select name="asset_type_${{i}}">${{TYPES.map(t=>`<option value="${{t}}" ${{d.asset_type===t?'selected':''}}>${{t}}</option>`).join('')}}</select></label>
<label>시장<select name="market_${{i}}"><option ${{d.market==='US'?'selected':''}}>US</option><option ${{d.market==='KR'?'selected':''}}>KR</option></select></label>
<label>수량<input name="quantity_${{i}}" type="number" step="any" min="0" value="${{d.quantity??0}}"></label>
<label>평단<input name="avg_cost_${{i}}" type="number" step="any" min="0" value="${{d.avg_cost??0}}"></label>
<label>통화<input name="currency_${{i}}" value="${{d.currency||'USD'}}"></label>
<button type="button" class="del" onclick="this.closest('.row').remove()">삭제</button></div>`;}}
let n={n};
function addRow(){{document.getElementById('rows').insertAdjacentHTML('beforeend',rowHtml(n++,{{}}));}}
</script></body></html>"""


def _rows_html(holdings: list[dict]) -> str:
    out = []
    for i, h in enumerate(holdings):
        out.append(f'''<div class="row" data-i="{i}">
<label>티커<input name="ticker_{i}" value="{h['ticker']}" required></label>
<label>종목명 <span style="text-transform:none">(비우면 자동)</span><input name="name_{i}" value="{h['name']}"></label>
<label>자산유형<select name="asset_type_{i}">''' +
            "".join(f'<option value="{t.value}"{" selected" if h["asset_type"] == t.value else ""}>{t.value}</option>'
                    for t in AssetType) + f'''</select></label>
<label>시장<select name="market_{i}">''' +
            "".join(f'<option{" selected" if h["market"] == m.value else ""}>{m.value}</option>' for m in Market) +
            f'''</select></label>
<label>수량<input name="quantity_{i}" type="number" step="any" min="0" value="{h['quantity']}"></label>
<label>평단<input name="avg_cost_{i}" type="number" step="any" min="0" value="{h['avg_cost']}"></label>
<label>통화<input name="currency_{i}" value="{h['currency']}"></label>
<button type="button" class="del" onclick="this.closest('.row').remove()">삭제</button></div>''')
    return "\n".join(out)


def _current() -> tuple[list[dict], str]:
    p = load_holdings()
    if isinstance(p, Unavailable):
        return [], "KRW"
    return ([{"ticker": s.value.ticker, "name": s.value.name,
              "asset_type": s.value.asset_type.value, "market": s.value.market.value,
              "quantity": s.value.quantity, "avg_cost": s.value.avg_cost,
              "currency": s.value.currency} for s in p.holdings],
            p.base_currency)


def _page(msg_html: str = "") -> bytes:
    rows, ccy = _current()
    html = _PAGE.format(css=_CSS, path=HOLDINGS_PATH, msg=msg_html,
                        rows=_rows_html(rows) or "", ccy=ccy, n=len(rows),
                        types=json.dumps([t.value for t in AssetType]))
    return html.encode("utf-8")


def _save(form: dict[str, list[str]]) -> str:
    idx = sorted({int(k.split("_")[-1]) for k in form if k.startswith("ticker_")})
    rows = []
    for i in idx:
        t = (form.get(f"ticker_{i}", [""])[0] or "").strip().upper()
        if not t:
            continue
        rows.append({
            "ticker": t,
            "name": (form.get(f"name_{i}", [""])[0] or "").strip(),
            "asset_type": form.get(f"asset_type_{i}", ["single_stock"])[0],
            "market": form.get(f"market_{i}", ["US"])[0],
            "quantity": float(form.get(f"quantity_{i}", ["0"])[0] or 0),
            "avg_cost": float(form.get(f"avg_cost_{i}", ["0"])[0] or 0),
            "currency": (form.get(f"currency_{i}", ["USD"])[0] or "USD").strip().upper(),
        })
    if not rows:
        raise PortfolioError("종목이 하나도 없다 — 최소 1개는 필요하다")

    blanks = [r["ticker"] for r in rows if not r["name"]]
    if blanks:
        looked = toss.names(blanks)
        for r in rows:
            if not r["name"]:
                r["name"] = looked.get(r["ticker"], r["ticker"])

    doc = {"updated": date.today().isoformat(),
           "base_currency": (form.get("base_currency", ["KRW"])[0] or "KRW").strip().upper(),
           "holdings": rows}
    backup = HOLDINGS_PATH.with_suffix(".yaml.bak")
    if HOLDINGS_PATH.exists():
        backup.write_text(HOLDINGS_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    HOLDINGS_PATH.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False),
                             encoding="utf-8")

    p = load_holdings()            # 검증. 실패하면 아래 except 가 원복한다.
    if isinstance(p, Unavailable):
        raise PortfolioError(str(p))

    snap = write_snapshot(p)
    from . import cockpit as ck
    from . import daily_brief as db
    from . import dashboard as dsh
    b = db.run()
    c = ck.run()
    dsh.render(b, c)
    return (f"저장 완료 — {len(rows)}종목\n"
            f"  {HOLDINGS_PATH} 갱신 (백업: {backup.name})\n"
            f"  스냅샷 {snap}\n"
            f"  대시보드 재생성 · 신호 {len(b.signals)}개")


class Handler(BaseHTTPRequestHandler):
    def _send(self, body: bytes, ctype="text/html; charset=utf-8", code=200) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path.startswith("/dashboard"):
            f = Path("dashboard/index.html")
            if f.exists():
                self._send(f.read_bytes())
            else:
                self._send("<p>dashboard/index.html 없음 — 먼저 저장하세요.</p>".encode("utf-8"), code=404)
            return
        self._send(_page())

    def do_POST(self) -> None:
        if not self.path.startswith("/save"):
            self._send(b"not found", code=404); return
        n = int(self.headers.get("Content-Length", 0))
        form = urllib.parse.parse_qs(self.rfile.read(n).decode("utf-8"))
        try:
            msg = f'<div class="msg">{_save(form)}</div>'
        except (PortfolioError, ValueError) as exc:
            bak = HOLDINGS_PATH.with_suffix(".yaml.bak")
            if bak.exists():
                HOLDINGS_PATH.write_text(bak.read_text(encoding="utf-8"), encoding="utf-8")
            msg = f'<div class="msg bad">저장하지 않았습니다 (원본 유지)\n{exc}</div>'
        except Exception:
            msg = f'<div class="msg bad">예기치 못한 오류\n{traceback.format_exc()[-600:]}</div>'
        self._send(_page(msg))

    def log_message(self, fmt: str, *args) -> None:
        print(f"  {self.command} {self.path}")


def serve(host: str = HOST, port: int = PORT) -> None:
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"포트폴리오 편집기 → http://{host}:{port}")
    print(f"  대상: {HOLDINGS_PATH.resolve()}")
    print("  127.0.0.1 에만 열립니다. 종료: Ctrl+C")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n종료")


if __name__ == "__main__":
    serve()
