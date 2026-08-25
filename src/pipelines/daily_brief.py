"""일일 브리핑 (1층). 오늘 뭘 봐야 하는지 한 화면으로.

결론을 내리지 않는다. 어디를 더 파야 하는지만 짚는다 (CLAUDE.md).
`python3 -m src.pipelines.daily_brief`
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from ..core import anomalies as anom
from ..models import (CorporateAction, Market, Signal,
                      SignalKind)
from ..portfolio_io import (Portfolio, WatchItem, load_holdings, load_watchlist,
                            upcoming_earnings)
from ..provenance import Sourced, Unavailable, record
from ..sources import frankfurter, fred, open_dart, prices, toss

REPORT_DIR = Path("reports/daily")

MOVE_THRESHOLD = 0.05        # 밤사이 ±5% → 가격 판독기 권장
EARNINGS_WINDOW = 7          # 실적 7일 이내 → 스토리 리더 권장
FX_MOVE_THRESHOLD = 0.02
FOREIGN_HEAVY = 0.70
ANOMALY_MAX_LOOKUPS = 6      # 일봉 대조는 API 호출이다 — 극단 변동 상위 몇 개만 본다
ACTION_LOOKBACK_DAYS = 120   # 공시 조회 기간


@dataclass
class BriefResult:
    on: date
    kr_indices: dict[str, tuple[Sourced[float], Sourced[float] | Unavailable]]
    us_indices: dict[str, tuple[str, Sourced[float], Sourced[float] | Unavailable]]
    macro: dict[str, Sourced[float] | Unavailable]
    fx_toss: Sourced[float] | Unavailable
    fx_ecb: Sourced[float] | Unavailable
    holdings_rows: list[dict]
    portfolio: Portfolio | None
    rankings: dict[str, Sourced[list[dict]] | Unavailable]
    watch: list[Sourced[WatchItem]]
    earnings_soon: list[Sourced[WatchItem]]
    #: 심볼 → 랭킹 등락률을 액면 그대로 읽으면 안 되는 이유 (core/anomalies.py)
    anomalies: dict[str, list[anom.Anomaly]] = field(default_factory=dict)
    #: 심볼 → 그 이유를 확정하는 공시 (1차). 한국 종목만 — DART 만 이 조회를 제공한다
    actions: dict[str, Sourced[list[CorporateAction]] | Unavailable] = field(default_factory=dict)
    signals: list[Signal] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    names: dict[str, str] = field(default_factory=dict)

    def label(self, symbol: str) -> str:
        """'005930 삼성전자' 처럼 사람이 읽을 수 있는 표시. 이름이 없으면 심볼만."""
        n = self.names.get(symbol.upper(), "")
        return f"{symbol} {n}" if n and n != symbol else symbol


def _pct(x) -> str:
    return "—" if x is None else f"{x:+.2%}"


def _rank_candidates(rk: dict) -> list[tuple[str, str, float]]:
    """검사할 랭킹 줄만 골라 (심볼, 시장, 등락률) 로. 극단 변동 순, 심볼 중복 제거.

    전 종목을 일봉과 대조하면 랭킹 1회당 수십 콜이 된다. ±EXTREME_MOVE 이상만,
    그중에서도 상위 ANOMALY_MAX_LOOKUPS 개만 본다.
    """
    seen: dict[str, tuple[str, str, float]] = {}
    for key, v in rk.items():
        if isinstance(v, Unavailable):
            continue
        mkt = key.split("_")[0]
        for x in v.value:
            sym, rate = x.get("symbol") or "", x.get("change_rate")
            if not sym or rate is None or abs(rate) < anom.EXTREME_MOVE:
                continue
            if sym not in seen or abs(rate) > abs(seen[sym][2]):
                seen[sym] = (sym, mkt, rate)
    return sorted(seen.values(), key=lambda t: -abs(t[2]))[:ANOMALY_MAX_LOOKUPS]


def _inspect_rankings(rk: dict) -> tuple[dict[str, list[anom.Anomaly]], dict]:
    """정황(일봉) → 확정(공시) 순서로 이상치를 조사한다.

    공시 확정은 한국 종목만 된다. DART 만 이 조회를 제공하고, SEC 에는 대응물이 없다 —
    미국 종목은 정황까지만 남기고 그 한계를 산출물에 적는다.
    """
    found: dict[str, list[anom.Anomaly]] = {}
    actions: dict[str, Sourced[list[CorporateAction]] | Unavailable] = {}
    for sym, mkt, rate in _rank_candidates(rk):
        c = toss.daily_candles(sym, count=30)
        if isinstance(c, Unavailable):
            found[sym] = [anom.Anomaly(anom.AnomalyKind.NO_HISTORY,
                                       f"일봉 미확보 — {c.reason[:60]}",
                                       invalidates_rate=True)]
            continue
        hits = anom.inspect(rate, c.value)
        if not hits:
            continue
        found[sym] = hits
        if anom.worth_checking(hits):
            if mkt == "KR":
                actions[sym] = open_dart.corporate_actions(sym, ACTION_LOOKBACK_DAYS)
            else:
                actions[sym] = Unavailable(
                    f"{sym} 기업행위 공시",
                    "미국 종목은 기업행위 공시 목록 조회를 붙이지 않았다 — "
                    "SEC 에 DART list.json 대응물이 없다")
    return found, actions


def run(on: date | None = None) -> BriefResult:
    on = on or date.today()
    notes: list[str] = []
    signals: list[Signal] = []

    # ── 한국 증시 ───────────────────────────────────────────────
    kr: dict[str, tuple] = {}
    idx = toss.market_indicators()
    if isinstance(idx, Unavailable):
        notes.append(f"국내 지수 미확보 — {idx.reason[:60]}")
    else:
        for sym, s in idx.items():
            kr[sym] = (s, toss.indicator_move(sym))
            record(s, subject=f"브리핑 {sym}")

    # ── 미국 증시 (대표 ETF 대용치) ─────────────────────────────
    us: dict[str, tuple] = {}
    proxies = list(toss.US_INDEX_PROXIES)
    q = toss.prices(proxies)
    if isinstance(q, Unavailable):
        notes.append(f"미국 지수 대용치 미확보 — {q.reason[:60]}")
    else:
        for sym in proxies:
            if sym in q:
                us[sym] = (toss.US_INDEX_PROXIES[sym], q[sym], toss.overnight_move(sym))
        notes.append("미국 지수는 대표 ETF 대용치다 (지수 자체가 아님)")

    # ── 매크로·환율 ─────────────────────────────────────────────
    macro = {k: fred.latest(k) for k in ("us10y", "us2y", "fedfunds")}
    for k, v in macro.items():
        if not isinstance(v, Unavailable):
            record(v, subject=f"브리핑 {k}")
    fx_toss, fx_ecb = toss.exchange_rate(), frankfurter.rate()

    # ── 보유 종목 ───────────────────────────────────────────────
    rows: list[dict] = []
    p = load_holdings()
    port = None if isinstance(p, Unavailable) else p
    if port is None or port.is_empty:
        notes.append("보유 종목 없음 — portfolio/holdings.yaml 확인")
    else:
        days, stale = port.staleness(on)
        if stale:
            notes.append(f"보유 현황이 {days}일 전 기준 — 갱신 권고")
        pq = prices.quotes(port.tickers)
        for s in port.holdings:
            h = s.value
            px = None if isinstance(pq, Unavailable) else pq.get(h.ticker)
            mv = prices.overnight_move(h.ticker)
            rate = None if isinstance(mv, Unavailable) else mv.value
            rows.append({"ticker": h.ticker, "name": h.name, "type": h.asset_type,
                         "price": px, "move": mv, "rate": rate,
                         "value": h.quantity * px.value if px else None})
            if rate is not None and abs(rate) >= MOVE_THRESHOLD:
                signals.append(Signal(
                    SignalKind.RUN_PRICE_DECODER, h.ticker,
                    f"밤사이 {rate:+.1%} 이동 (기준 ±{MOVE_THRESHOLD:.0%}) — 밸류 재점검 권장",
                    (mv.cite(),)))

    # ── 주요 종목 (보유하지 않은 것 포함) ───────────────────────
    rk: dict[str, Sourced[list[dict]] | Unavailable] = {}
    for mkt in ("KR", "US"):
        rk[f"{mkt}_amount"] = toss.rankings("MARKET_TRADING_AMOUNT", mkt, count=8)
        rk[f"{mkt}_losers"] = toss.rankings("TOP_LOSERS", mkt, "1d", count=5)
        rk[f"{mkt}_gainers"] = toss.rankings("TOP_GAINERS", mkt, "1d", count=5)

    # ── 급등락 이상치 판별 ──────────────────────────────────────
    # 랭킹 등락률을 액면 그대로 실으면 거래정지 재개·신규상장·분할이 '급락'으로 섞여 온다.
    # 일봉으로 정황을 만들고(core/anomalies), 한국 종목은 공시로 확정한다(DART, 1차).
    anomalies, actions = _inspect_rankings(rk)
    for sym, hits in anomalies.items():
        if not any(h.invalidates_rate for h in hits):
            continue
        why = " · ".join(h.kind.value for h in hits if h.invalidates_rate)
        ev = [a.detail for a in hits]
        act = actions.get(sym)
        if act is not None and not isinstance(act, Unavailable) and act.value:
            ev.append(act.cite())
        signals.append(Signal(
            SignalKind.DATA_GAP, sym,
            f"랭킹 등락률을 그대로 읽을 수 없다 ({why}) — 원문 확인 필요",
            tuple(ev)))

    if anomalies:
        bad = [k for k, v in anomalies.items() if anom.warnings(v)]
        notes.append(
            f"급등락 상위 {len(anomalies)}종목을 일봉과 대조했다 — "
            f"등락률을 그대로 읽을 수 없는 종목 {len(bad)}개"
            + (f" ({', '.join(bad)})" if bad else ""))
        if any(isinstance(a, Unavailable) for a in actions.values()):
            notes.append("미국 종목은 기업행위 공시 확정을 붙이지 않았다 — "
                         "SEC 에 DART list.json 대응물이 없다 (정황까지만)")

    # ── 관심 종목 · 실적 임박 ───────────────────────────────────
    wl = load_watchlist()
    watch = [] if isinstance(wl, Unavailable) else wl
    soon = upcoming_earnings(watch, EARNINGS_WINDOW, on)
    for s in soon:
        w = s.value
        d = w.days_to_earnings(on)
        when = "오늘" if d == 0 else f"{d}일 앞"
        src = f" · 출처 {w.earnings_source}" if w.earnings_source else ""
        signals.append(Signal(
            SignalKind.RUN_STORY_READER, w.ticker,
            f"실적 발표 {when} ({w.certainty}) — 공시 문구 변화 점검 권장{src}",
            (s.cite(),)))
        if not w.earnings_confirmed:
            notes.append(f"{w.ticker} 실적일 {w.earnings_date} 는 추정치다 — 회사 IR 확인 권장")
    if not watch:
        notes.append("관심 종목 없음 — portfolio/watchlist.yaml 에 실적일을 적으면 신호가 생성된다")

    # ── 환노출 신호 ─────────────────────────────────────────────
    if port and not port.is_empty and not isinstance(fx_ecb, Unavailable):
        from ..core.valuation.fx_exposure import foreign_ratio
        vals = {r["ticker"]: (r["value"] or 0) for r in rows}
        fr = foreign_ratio([s.value for s in port.holdings], vals)
        if fr >= FOREIGN_HEAVY:
            signals.append(Signal(SignalKind.CHECK_FX_EXPOSURE, None,
                                  f"해외자산 비중 {fr:.0%} (기준 {FOREIGN_HEAVY:.0%}) — "
                                  "원화 환산 민감도 재점검 권장", (fx_ecb.cite(),)))

    # ── 데이터 공백 신호 ────────────────────────────────────────
    gaps = [k for k, v in macro.items() if isinstance(v, Unavailable)]
    if gaps:
        signals.append(Signal(SignalKind.DATA_GAP, None,
                              f"매크로 미확보: {', '.join(gaps)}"))
    signals.append(Signal(SignalKind.DATA_GAP, None,
                          "테마 뉴스는 3차 출처(웹검색)라 자동 수집하지 않는다 — "
                          "필요 시 스킬에서 정성 관찰로 추가"))

    # ── 종목명 해석 (한 번의 호출로 전 화면에 적용) ─────────────
    wanted = {r["ticker"] for r in rows}
    wanted |= {w.value.ticker for w in watch}
    for v in rk.values():
        if not isinstance(v, Unavailable):
            wanted |= {x["symbol"] for x in v.value}
    wanted |= set(toss.US_INDEX_PROXIES)
    name_map = toss.names(sorted(wanted)[:200])
    if not name_map:
        notes.append("종목명 미확보 — 심볼로만 표시된다")

    return BriefResult(on, kr, us, macro, fx_toss, fx_ecb, rows, port, rk,
                       watch, soon, anomalies=anomalies, actions=actions,
                       signals=signals, notes=notes, names=name_map)


def _warn_mark(r: BriefResult, symbol: str) -> str:
    return " ⚠" if anom.warnings(r.anomalies.get(symbol, [])) else ""


def _anomaly_block(r: BriefResult, symbols: list[str]) -> list[str]:
    """표 아래 경고 블록. 정황(일봉)과 확정(공시)을 줄을 나눠 적는다.

    등락률을 지우지 않는다 — 벤더가 준 값은 사실이고, 그 값을 어떻게 읽어야 하는지를 덧붙인다.
    """
    L: list[str] = []
    for sym in symbols:
        hits = anom.warnings(r.anomalies.get(sym, []))
        if not hits:
            continue
        L.append(f"> ⚠ **{r.label(sym)}**")
        L += [f"> - [사실] {h}" for h in hits]
        act = r.actions.get(sym)
        if isinstance(act, Unavailable):
            L.append(f"> - 확정: {act.cite()}")
        elif act is not None and act.value:
            L += [f"> - [사실] 공시 확정 — {a.title} ({a.filed_on.isoformat()}) · {a.url}"
                  for a in act.value[:3]]
            L.append(f">   ↳ {act.cite()}")
        elif act is not None:
            L.append("> - [사실] 최근 기업행위 공시 없음 — 분할·병합·정지로 설명되지 않는다")
            L.append(f">   ↳ {act.cite()}")
        L.append(">")
    return L + [""] if L else []


def to_markdown(r: BriefResult) -> str:
    from ..render.brief import DISCLAIMER
    L = [f"# 일일 브리핑 — {r.on.isoformat()}", "", DISCLAIMER, "",
         "결론을 내리지 않습니다. 오늘 어디를 더 파볼지만 제시합니다.", ""]

    L += ["## 한국 증시", "", "| 지표 | 현재 | 변동 |", "|---|---:|---:|"]
    for sym, (s, mv) in r.kr_indices.items():
        L.append(f"| {sym} | {s.value:,.2f} | {_pct(None if isinstance(mv, Unavailable) else mv.value)} |")

    L += ["", "## 미국 증시 *(대표 ETF 대용치)*", "", "| 지수 | ETF | 현재 | 변동 |", "|---|---|---:|---:|"]
    for sym, (label, s, mv) in r.us_indices.items():
        L.append(f"| {label} | {sym} | {s.value:,.2f} | "
                 f"{_pct(None if isinstance(mv, Unavailable) else mv.value)} |")

    L += ["", "## 매크로·환율", ""]
    for k, v in r.macro.items():
        L.append(f"- **{k}**: " + (v.cite() if isinstance(v, Unavailable)
                                   else f"[사실] {v.value:,.3f}%  \n  ↳ {v.cite()}"))
    for lbl, v in (("USD/KRW (장중)", r.fx_toss), ("USD/KRW (ECB 영업일 종가)", r.fx_ecb)):
        L.append(f"- **{lbl}**: " + (v.cite() if isinstance(v, Unavailable)
                                     else f"[사실] {v.value:,.2f}  \n  ↳ {v.cite()}"))

    if r.holdings_rows:
        L += ["", "## 보유 종목 밤사이 움직임", "", "| 종목 | 현재가 | 변동 | 평가액 |", "|---|---:|---:|---:|"]
        for h in sorted(r.holdings_rows, key=lambda x: -(abs(x["rate"] or 0))):
            px = f"{h['price'].value:,.2f}" if h["price"] else "확인 필요"
            val = f"{h['value']:,.0f}" if h["value"] else "—"
            L.append(f"| {h['ticker']} {h['name']} | {px} | {_pct(h['rate'])} | {val} |")

    L += ["", "## 주요 종목 (보유 외)", ""]
    for key, title in (("KR_amount", "한국 거래대금 상위"), ("US_amount", "미국 거래대금 상위"),
                       ("KR_losers", "한국 급락"), ("US_gainers", "미국 급등")):
        v = r.rankings.get(key)
        if isinstance(v, Unavailable):
            L += [f"### {title}", "", f"- {v.cite()}", ""]
            continue
        L += [f"### {title}", "", "| # | 종목 | 현재가 | 변동 |", "|---:|---|---:|---:|"]
        L += [f"| {x['rank']} | {r.label(x['symbol'])}{_warn_mark(r, x['symbol'])} | "
              f"{x['last']:,.2f} | {_pct(x['change_rate'])} |"
              for x in v.value]
        L += ["", f"↳ {v.cite()}", ""]
        L += _anomaly_block(r, [x["symbol"] for x in v.value])

    L += ["## 오늘의 액션 신호", ""]
    if r.signals:
        L += ["| 신호 | 대상 | 근거 |", "|---|---|---|"]
        L += [f"| {s.kind.value} | {s.ticker or '—'} | {s.reason} |" for s in r.signals]
    else:
        L.append("_추가로 파볼 항목 없음._")

    if r.notes:
        L += ["", "## 확인 필요", ""] + [f"- {n}" for n in r.notes]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    res = run()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    md = REPORT_DIR / f"{res.on.isoformat()}-brief.md"
    md.write_text(to_markdown(res), encoding="utf-8")
    print(f"✓ {md} ({md.stat().st_size:,} bytes)")
    print(f"  한국 지수 {len(res.kr_indices)} · 미국 대용치 {len(res.us_indices)} · "
          f"보유 {len(res.holdings_rows)} · 신호 {len(res.signals)}")
    for s in res.signals:
        print(f"  → {s.kind.value}: {s.ticker or '—'} · {s.reason[:60]}")
