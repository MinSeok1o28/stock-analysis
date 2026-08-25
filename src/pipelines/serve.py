"""로컬 서버 — 대시보드 열람 + 종목 검색 + 온디맨드 페이지 생성.

정적 HTML 은 파이썬을 못 돌린다. 검색해서 고른 종목의 페이지를 **그 자리에서 만들려면**
서버가 필요하다. 이 모듈이 그 역할을 한다.

    python3 -m src.pipelines.serve      →  http://127.0.0.1:8766

경로
    /                     대시보드 (dashboard/index.html)
    /stocks/<T>.html      종목 상세 (없으면 생성 안내)
    /api/search?q=        종목 검색 (자동완성)
    /api/generate?t=      해당 종목 페이지 생성 후 경로 반환
    /api/batch?t=A,B,C    여러 종목 동시 생성 시작 → 작업 id
    /api/batch/status?j=  진행 상황 (종목별 queued/running/ok/error)
    /compare?j=           완료된 작업의 비교 페이지
    /portfolio            보유 종목 편집기로 이동 안내

**127.0.0.1 에만 바인딩한다.** 파일을 쓰는 서버를 외부에 열지 않는다.
"""

from __future__ import annotations

import itertools
import json
import os
import re
import threading
import traceback
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
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

#: 동시에 띄울 `claude` CLI 개수. 병목이 CLI 응답 대기(I/O)라 스레드로 충분하다.
#: 너무 올리면 구독 한도에 부딪힌다 — 그래서 상한을 둔다.
BATCH_WORKERS = max(1, min(8, int(os.environ.get("BATCH_WORKERS", "4"))))
BATCH_MAX_TICKERS = 20


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
                "size": dest.stat().st_size, "summary": sp.summarize(pg)}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        with _lock:
            _building.discard(tk)


# ─────────────────────────────────────────────────────────────
# 배치 생성 — 여러 종목을 한 번에
#
# 순차로 돌리면 이득이 없다. 5종목 × 60초 = 300초는 한 번에 기다리든 다섯 번
# 나눠 기다리든 같다. **동시에 돌려야** 총 대기가 준다 (300초 → 70~90초).
# 파이프라인 부분은 2초 남짓이고 나머지가 전부 CLI 응답 대기라 스레드로 충분하다.
# ─────────────────────────────────────────────────────────────

_job_seq = itertools.count(1)
_jobs: dict[str, "BatchJob"] = {}
_jobs_lock = threading.Lock()

JOB_KEEP = 8          # 최근 작업만 들고 있는다. 메모리에만 사는 결과다.


@dataclass
class BatchJob:
    id: str
    tickers: list[str]
    workers: int
    state: dict[str, str] = field(default_factory=dict)      # ticker → queued|running|ok|error
    note: dict[str, str] = field(default_factory=dict)       # ticker → 진행/실패 사유
    summaries: dict[str, object] = field(default_factory=dict)
    done: bool = False

    def snapshot(self) -> dict:
        rows = [{"t": t, "state": self.state.get(t, "queued"), "note": self.note.get(t, "")}
                for t in self.tickers]
        fin = sum(1 for r in rows if r["state"] in ("ok", "error"))
        return {"job": self.id, "done": self.done, "workers": self.workers,
                "total": len(self.tickers), "finished": fin, "rows": rows}


def _run_batch(job: BatchJob, narrate: bool, force: bool) -> None:
    """작업 스레드. 종목마다 generate() 를 부르고 결과를 job 에 적는다."""
    def one(tk: str) -> None:
        with _jobs_lock:
            job.state[tk] = "running"
        try:
            r = generate(tk, narrate=narrate, force=force)
        except Exception as exc:                       # 한 종목이 죽어도 나머지는 간다
            r = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        with _jobs_lock:
            if r.get("ok"):
                job.state[tk] = "ok"
                job.note[tk] = str(r.get("note") or "")
                job.summaries[tk] = r.get("summary")
            else:
                job.state[tk] = "error"
                job.note[tk] = str(r.get("error") or "알 수 없는 실패")

    try:
        with ThreadPoolExecutor(max_workers=job.workers) as ex:
            list(ex.map(one, job.tickers))
    finally:
        with _jobs_lock:
            job.done = True


def start_batch(tickers: list[str], *, workers: int = BATCH_WORKERS,
                narrate: bool = True, force: bool = False) -> BatchJob:
    """중복을 제거하고 순서를 지킨 채 작업을 띄운다."""
    seen, ordered = set(), []
    for t in tickers:
        tk = t.strip().upper()
        if tk and tk not in seen:
            seen.add(tk); ordered.append(tk)
    ordered = ordered[:BATCH_MAX_TICKERS]
    job = BatchJob(id=f"j{next(_job_seq)}", tickers=ordered,
                   workers=max(1, min(8, workers)))
    job.state = {t: "queued" for t in ordered}
    with _jobs_lock:
        _jobs[job.id] = job
        for old in list(_jobs)[:-JOB_KEEP]:
            _jobs.pop(old, None)
    threading.Thread(target=_run_batch, args=(job, narrate, force), daemon=True).start()
    return job


def compare_page(job: BatchJob) -> str:
    """완료된 작업 → 비교 페이지 HTML. 선택마다 달라지므로 파일로 쓰지 않는다."""
    from . import compare
    ok = [job.summaries[t] for t in job.tickers if job.state.get(t) == "ok"
          and job.summaries.get(t) is not None]
    bad = [(t, job.note.get(t, "실패")) for t in job.tickers
           if job.state.get(t) != "ok"]
    return compare.render(ok, bad)


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
            r = generate(tk, narrate=("nonarr" not in q), force=("force" in q))
            r.pop("summary", None)      # 비교용 dataclass — 단건 응답에는 싣지 않는다
            self._json(r); return

        if path == "/api/batch":
            raw = (q.get("t", [""])[0] or "").split(",")
            tks = [x.strip().upper() for x in raw if x.strip()]
            bad = [x for x in tks if not re.fullmatch(r"[A-Z0-9.\-]{1,12}", x)]
            if bad:
                self._json({"ok": False, "error": f"티커 형식 오류: {', '.join(bad[:3])}"}, 400)
                return
            if not tks:
                self._json({"ok": False, "error": "종목이 선택되지 않았습니다"}, 400); return
            try:
                w = int(q.get("workers", [str(BATCH_WORKERS)])[0])
            except ValueError:
                w = BATCH_WORKERS
            job = start_batch(tks, workers=w, narrate=("nonarr" not in q),
                              force=("force" in q))
            self._json({"ok": True, **job.snapshot()}); return

        if path == "/api/batch/status":
            job = _jobs.get(q.get("j", [""])[0])
            if job is None:
                self._json({"ok": False, "error": "작업을 찾을 수 없습니다"}, 404); return
            self._json({"ok": True, **job.snapshot()}); return

        if path == "/compare":
            job = _jobs.get(q.get("j", [""])[0])
            if job is None:
                self._send("<p>작업을 찾을 수 없습니다. 서버를 다시 시작했다면 "
                           "결과가 사라집니다 — 메모리에만 남기기 때문입니다.</p>"
                           .encode("utf-8"), code=404)
                return
            try:
                self._send(compare_page(job).encode("utf-8"))
            except Exception:
                traceback.print_exc()
                self._send("<p>비교 페이지 생성 실패 — 서버 로그를 확인하세요.</p>"
                           .encode("utf-8"), code=500)
            return

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
    print(f"  배치 동시 실행: {BATCH_WORKERS}개 (BATCH_WORKERS 로 조절, 최대 8)")
    print("  127.0.0.1 에만 열립니다. 종료: Ctrl+C")
    try:
        ThreadingHTTPServer((host, port), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\n종료")


if __name__ == "__main__":
    serve()
