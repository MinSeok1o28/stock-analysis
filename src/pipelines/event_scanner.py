"""이벤트 스캐너 — "어떤 종목을 왜 지금 봐야 하는가"를 자동으로 만든다.

관심 종목을 손으로 적는 대신, 보유·시장 랭킹·관심 목록을 후보 풀로 두고
관측 가능한 이벤트 태그를 붙인다. 태그가 겹치는 종목이 먼저 온다.

**예측하지 않는다.** 시나리오는 과거 반응 분포일 뿐이다.

`python3 -m src.pipelines.event_scanner [최대종목수]`
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from ..core.events import Bars, Candidate, detect, reaction_stats, scenarios
from ..models import AssetType, Market
from ..portfolio_io import load_holdings, load_watchlist
from ..provenance import Sourced, Unavailable
from ..sources import prices, sec_edgar, toss

REPORT_DIR = Path("reports/events")
MAX_DEEP = 12          # 일봉·8-K 까지 파는 종목 수 상한 (호출량 제어)
RANK_TOP = 6


@dataclass
class ScanResult:
    on: date
    candidates: list[Candidate]
    universe: int
    notes: list[str] = field(default_factory=list)


def _rank_pool(notes: list[str]) -> dict[str, list[str]]:
    """시장 랭킹에서 후보를 모은다. 보유하지 않은 종목이 여기서 들어온다."""
    pool: dict[str, list[str]] = {}
    for market in ("KR", "US"):
        for kind, label in (("MARKET_TRADING_AMOUNT", "거래대금 상위"),
                            ("TOP_GAINERS", "급등"), ("TOP_LOSERS", "급락")):
            r = toss.rankings(kind, market, "1d", RANK_TOP)
            if isinstance(r, Unavailable):
                notes.append(f"{market} {label} 랭킹 미확보")
                continue
            for row in r.value:
                pool.setdefault(row["symbol"], []).append(f"{market} {label} {row['rank']}위")
    return pool


def run(on: date | None = None, max_deep: int = MAX_DEEP) -> ScanResult:
    on = on or date.today()
    notes: list[str] = []

    # ── 후보 풀 ─────────────────────────────────────────────
    held: dict[str, str] = {}
    p = load_holdings()
    if not isinstance(p, Unavailable):
        held = {s.value.ticker: s.value.name for s in p.holdings
                if s.value.asset_type is AssetType.SINGLE_STOCK}
    watch: dict[str, object] = {}
    wl = load_watchlist()
    if not isinstance(wl, Unavailable):
        watch = {s.value.ticker: s.value for s in wl}

    ranks = _rank_pool(notes)
    universe = sorted(set(held) | set(watch) | set(ranks))
    if not universe:
        notes.append("후보가 없다 — 보유·관심 종목을 채우거나 랭킹 확보 필요")
        return ScanResult(on, [], 0, notes)

    # ── 이름·시세 (200종목까지 각 1콜) ────────────────────────
    names = toss.names(universe[:200])
    quotes = prices.quotes(universe[:200])
    if isinstance(quotes, Unavailable):
        notes.append(f"시세 미확보 — {quotes.reason[:60]}")
        quotes = {}

    # ── 얕은 스코어링: 랭킹·보유·관심만으로 우선순위 ───────────
    prelim: list[tuple[int, str]] = []
    for t in universe:
        pri = 0
        if t in held:
            pri += 3
        if t in watch:
            pri += 2
        pri += len(ranks.get(t, []))
        prelim.append((pri, t))
    prelim.sort(key=lambda x: (-x[0], x[1]))
    deep = [t for _, t in prelim[:max_deep]]
    if len(universe) > max_deep:
        notes.append(f"후보 {len(universe)}종목 중 상위 {max_deep}종목만 일봉·8-K 까지 조회했다 "
                     f"(호출량 제어). 나머지는 랭킹 정보만 반영됐다")

    out: list[Candidate] = []
    for t in deep:
        q = quotes.get(t)
        px = q.value if isinstance(q, Sourced) else None
        cand = Candidate(ticker=t, name=names.get(t, t), price=px or 0.0,
                         change=None, held=(t in held))
        bars_s = toss.daily_candles_paged(t, pages=2)
        if isinstance(bars_s, Unavailable):
            cand.notes.append(f"일봉 미확보 — {bars_s.reason[:50]}")
            out.append(cand)
            continue
        bars = Bars(bars_s.value)
        cand.change = bars.change()
        if px is None:
            cand.price = bars.close()

        # 실적 일정: 관심 목록의 확정일 우선, 없으면 8-K 주기 추정 (미국만)
        d_to = d_since = None
        confirmed = False
        w = watch.get(t)
        if w is not None and getattr(w, "earnings_date", None):
            d_to = w.days_to_earnings(on)
            confirmed = bool(w.earnings_confirmed)
        stat = None
        if not t.isdigit():                       # 미국 종목만 8-K 조회 가능
            evs = sec_edgar.earnings_events(t, 8)
            if isinstance(evs, Unavailable):
                cand.notes.append("실적 이력 미확보 (8-K)")
            else:
                stat = reaction_stats(bars, evs)
                d_since = (on - evs[0].filed_on).days
                if d_to is None:
                    est = sec_edgar.next_earnings_estimate(evs, on)
                    if est:
                        d_to = (est[0] - on).days
                        cand.notes.append(f"다음 실적 {est[0]} 추정 (과거 {est[1]}회 주기)")
        else:
            cand.notes.append("한국 종목 — 8-K 이력이 없어 실적 반응 통계 불가")

        cand.events = detect(bars, days_to_earnings=d_to, earnings_confirmed=confirmed,
                             days_since_earnings=d_since, in_rankings=ranks.get(t))
        cand.stat = stat
        if stat and stat.n and cand.price:
            cand.scenarios = scenarios(cand.price, stat)
        out.append(cand)

    out.sort(key=lambda c: (-c.score, -(len(c.scenarios)), c.ticker))
    return ScanResult(on, out, len(universe), notes)


def to_markdown(r: ScanResult) -> str:
    from ..render.brief import DISCLAIMER
    L = [f"# 이벤트 스캐너 — {r.on.isoformat()}", "", DISCLAIMER, "",
         f"후보 {r.universe}종목 중 {len(r.candidates)}종목 정밀 조회. "
         "**예측이 아니라 관측된 이벤트와 과거 분포입니다.**", ""]

    ranked = [c for c in r.candidates if c.events]
    if ranked:
        L += ["## 지금 볼 이유가 있는 종목", "",
              "| 종목 | 현재가 | 변동 | 왜 봐야 하나 |", "|---|---:|---:|---|"]
        for c in ranked:
            nm = f"{c.name} ({c.ticker})" + (" 🔹보유" if c.held else "")
            L.append(f"| {nm} | {c.price:,.2f} | "
                     f"{('%+.2f%%' % (c.change*100)) if c.change is not None else '—'} | {c.why} |")
        L.append("")

    for c in ranked:
        if not (c.stat and c.stat.n):
            continue
        L += [f"## {c.name} ({c.ticker})", "",
              f"- 왜: {c.why}",
              f"- [사실] 과거 실적 반응 — {c.stat.summary()}", "",
              "| 실적일 | 반응일 | 변동 | 거래량 |", "|---|---|---:|---:|"]
        for ed, rd, mv, vr in c.stat.moves[:8]:
            L.append(f"| {ed} | {rd} | {mv:+.2%} | {vr:.1f}배 |")
        if c.scenarios:
            L += ["", "### 시나리오 — 과거 분포 기준 (예측 아님)", "",
                  "| 구간 | 변동 | 가격 | 근거 |", "|---|---:|---:|---|"]
            for s in c.scenarios:
                L.append(f"| {s.label} | {s.move:+.1%} | {s.price:,.2f} | {s.basis} |")
            L.append("")
            L.append("- [해석] 위 구간은 **이 종목이 과거 실적에 얼마나 움직였는가**입니다. "
                     "이번에도 그럴 거라는 뜻이 아닙니다. 방향은 알 수 없습니다.")
        if c.notes:
            L += ["", "확인 필요: " + " / ".join(c.notes)]
        L.append("")

    quiet = [c for c in r.candidates if not c.events]
    if quiet:
        L += ["## 특이 이벤트 없음", "",
              ", ".join(f"{c.name}({c.ticker})" for c in quiet), ""]

    L += ["## 이 스캐너가 못 보는 것", "",
          "- **컨센서스 추정치** — 유료. 없으므로 '상회/하회' 시나리오를 만들 수 없다",
          "- **옵션 내재변동성** — 유료. 시장이 예상하는 변동폭을 알 수 없다",
          "- **미래 실적일 확정** — 무료 캘린더가 없다. 8-K 주기 추정이거나 수동 입력이다",
          "- **한국 종목의 실적 반응 통계** — 8-K 에 해당하는 이력이 없다",
          "- 배당락·자사주·유상증자 등 자본 이벤트"]
    if r.notes:
        L += ["", "## 확인 필요", ""] + [f"- {n}" for n in r.notes]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else MAX_DEEP
    res = run(max_deep=n)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORT_DIR / f"{res.on.isoformat()}-events.md"
    out.write_text(to_markdown(res), encoding="utf-8")
    print(f"✓ {out} ({out.stat().st_size:,} bytes)")
    print(f"  후보 {res.universe}종목 · 정밀 {len(res.candidates)}종목")
    for c in res.candidates:
        if c.events:
            print(f"  [{c.score}] {c.name[:16]:18s} {c.ticker:8s} {c.why[:88]}")
