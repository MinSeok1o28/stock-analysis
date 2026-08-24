"""로컬 서버 — 대시보드 열람 + 종목 검색 + 온디맨드 페이지 생성.

정적 HTML 은 파이썬을 못 돌린다. 검색해서 고른 종목의 페이지를 **그 자리에서 만들려면**
서버가 필요하다. 이 모듈이 그 역할을 한다.

    python3 -m src.pipelines.serve      →  http://127.0.0.1:8766

경로
    /                     대시보드 (dashboard/index.html)
    /stocks/<T>.html      종목 상세 (없으면 생성 안내)
    /api/search?q=        종목 검색 (자동완성)
    /api/generate?t=      해당 종목 페이지 생성 후 경로 반환
    /portfolio            보유 종목 편집기로 이동 안내

**127.0.0.1 에만 바인딩한다.** 파일을 쓰는 서버를 외부에 열지 않는다.
"""

from __future__ import annotations

import json
import re
import threading
import traceback
import urllib.parse
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ..provenance import Unavailable
from ..sources import toss

HOST, PORT = "127.0.0.1", 8766
DASH = Path("dashboard")
STOCKS = DASH / "stocks"
INDEX_CACHE = DASH / "universe.json"

_universe: list[dict] | None = None
_lock = threading.Lock()
_building: set[str] = set()


def load_universe(force: bool = False) -> list[dict]:
    """검색용 종목 마스터. 디스크 캐시 → 없으면 토스에서 받아 저장."""
    global _universe
    if _universe is not None and not force:
        return _universe
    if INDEX_CACHE.exists() and not force:
        try:
            _universe = json.loads(INDEX_CACHE.read_text(encoding="utf-8"))
            return _universe
        except json.JSONDecodeError:
            pass
    u = toss.universe()
    if isinstance(u, Unavailable):
        _universe = []
        return _universe
    _universe = [{"s": r["symbol"], "n": r["name"] or r["symbol"], "m": r["market"]}
                 for r in u.value]
    INDEX_CACHE.parent.mkdir(parents=True, exist_ok=True)
    INDEX_CACHE.write_text(json.dumps(_universe, ensure_ascii=False,
                                      separators=(",", ":")), encoding="utf-8")
    return _universe


def search(q: str, limit: int = 12) -> list[dict]:
    """종목명·티커 부분 일치. 앞에서 일치하는 것을 먼저 보여준다."""
    q = (q or "").strip()
    if len(q) < 1:
        return []
    ql = q.lower()
    rows = load_universe()
    starts, contains = [], []
    for r in rows:
        s, n = r["s"].lower(), r["n"].lower()
        if s.startswith(ql) or n.startswith(ql):
            starts.append(r)
        elif ql in s or ql in n:
            contains.append(r)
        if len(starts) >= limit:
            break
    out = (starts + contains)[:limit]
    for r in out:
        r = dict(r)
    return [{**r, "ready": (STOCKS / f"{r['s']}.html").exists()} for r in out]


def generate(ticker: str, *, with_story: bool = False, narrate: bool = True,
             force: bool = False) -> dict:
    """종목 페이지를 만든다.

    narrate=True 면 서사가 없을 때 Claude CLI 를 호출해 해석까지 쓴다.
    사실(파이프라인) → 서사(Claude) → 렌더 순서다.
    """
    tk = ticker.upper()
    with _lock:
        if tk in _building:
            return {"ok": False, "error": "이미 생성 중입니다"}
        _building.add(tk)
    try:
        from . import narrator, stock_page as sp
        note = ""
        if narrate:
            from ..narrative_io import load as load_nar
            if force or load_nar(tk).is_empty:
                if narrator.available():
                    w = narrator.write(tk)
                    note = ("서사 작성 완료" if not isinstance(w, Unavailable)
                            else f"서사 실패({str(w)[:70]}) — 사실만 표시")
                else:
                    note = "claude CLI 없음 — 사실만 표시"
        pg = sp.build(tk, date.today(), with_story=with_story)
        if isinstance(pg, Unavailable):
            return {"ok": False, "error": str(pg)}
        dest = sp.render(pg)
        return {"ok": True, "url": f"/stocks/{tk}.html",
                "narrative": not pg.narrative.is_empty, "note": note,
                "size": dest.stat().st_size}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        with _lock:
            _building.discard(tk)


_MISSING = """<!doctype html><meta charset="utf-8"><title>{t} — 생성 필요</title>
<style>body{{font:16px/1.7 "Noto Sans KR",sans-serif;max-width:640px;margin:4rem auto;
padding:0 1.5rem;color:#1a2228}}code{{background:#eef1f3;padding:.15rem .4rem;border-radius:4px}}
a{{color:#0f7268}}</style>
<h1>{t} 페이지가 아직 없습니다</h1>
<p>대시보드 상단 검색창에서 종목을 고르면 자동으로 생성됩니다.
터미널에서 직접 만들려면:</p>
<pre><code>python3 -m src.pipelines.stock_page {t}</code></pre>
<p><a href="/">← 대시보드로</a></p>"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, body: bytes, ctype="text/html; charset=utf-8", code=200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8", code)

    def do_GET(self) -> None:
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        path = u.path

        if path in ("/", "/index.html"):
            f = DASH / "index.html"
            if f.exists():
                self._send(f.read_bytes())
            else:
                self._send("<p>dashboard/index.html 이 없습니다. "
                           "<code>python3 -m src.pipelines.dashboard</code> 를 먼저 실행하세요.</p>"
                           .encode("utf-8"), code=404)
            return

        if path == "/api/search":
            self._json(search(q.get("q", [""])[0])); return

        if path == "/api/status":
            from . import narrator
            self._json({"universe": len(load_universe()),
                        "narrator": narrator.available()}); return

        if path == "/api/generate":
            tk = (q.get("t", [""])[0] or "").strip().upper()
            if not re.fullmatch(r"[A-Z0-9.\-]{1,12}", tk):
                self._json({"ok": False, "error": "티커 형식이 올바르지 않습니다"}, 400); return
            self._json(generate(tk, narrate=("nonarr" not in q),
                                force=("force" in q))); return

        if path.startswith("/stocks/"):
            name = Path(path).name
            f = STOCKS / name
            if f.exists() and f.suffix == ".html":
                self._send(f.read_bytes())
            else:
                self._send(_MISSING.format(t=name.replace(".html", "")).encode("utf-8"), code=404)
            return

        self._send(b"not found", "text/plain", 404)

    def log_message(self, fmt: str, *args) -> None:
        if "/api/search" not in self.path:
            print(f"  {self.command} {self.path}")


def serve(host: str = HOST, port: int = PORT) -> None:
    n = len(load_universe())
    print(f"투자 리서치 대시보드 → http://{host}:{port}")
    from . import narrator
    print(f"  종목 검색 인덱스 {n:,}개 " + ("(토스 마스터)" if n else "— 미확보"))
    print(f"  서사 자동 작성: {'가능 (claude CLI)' if narrator.available() else '불가 — 사실만 표시'}")
    print("  127.0.0.1 에만 열립니다. 종료: Ctrl+C")
    try:
        ThreadingHTTPServer((host, port), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\n종료")


if __name__ == "__main__":
    serve()
