"""포트폴리오 콕핏 실행 진입점 (조율 계층).

sources → core → render 를 순서대로 엮는다. 이 계층만 셋 모두를 안다.
`python3 -m src.pipelines.cockpit [holdings.yaml]`

산출물
  reports/cockpit/<날짜>-cockpit.md   날짜별 누적 (append)
  dashboard/index.html                단일 현재 상태 (덮어쓰기)
  ledger/manifest.jsonl               쓴 출처 기록 (append-only)
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ..core.valuation.concentration import (LookThrough, effective_positions, hhi,
                                            look_through, weights)
from ..core.valuation.fx_exposure import foreign_ratio, sensitivity
from ..models import AssetType, Holding
from ..portfolio_io import Portfolio, can_compute_contribution, load_holdings, list_snapshots
from ..provenance import Sourced, Unavailable, record
from ..render import dashboard as dash
from ..sources import etf_holdings, frankfurter, prices

REPORT_DIR = Path("reports/cockpit")


@dataclass
class CockpitResult:
    on: date
    portfolio: Portfolio
    values: dict[str, float]
    surface: dict[str, float]
    effective: dict[str, float]
    rows: list[LookThrough]
    missing: list[Unavailable]
    skipped: list[str]
    constituents: dict[str, dict[str, float]]
    fx: Sourced[float] | Unavailable
    foreign: float
    price_source: Sourced[float] | Unavailable
    total: float
    notes: list[str]


def run(holdings_path: Path | None = None, on: date | None = None) -> CockpitResult | Unavailable:
    on = on or date.today()
    p = load_holdings(holdings_path) if holdings_path else load_holdings()
    if isinstance(p, Unavailable):
        return p
    if p.is_empty:
        return Unavailable("콕핏", "보유 종목이 없다 — portfolio/holdings.yaml 을 채우라")

    hs: list[Holding] = [s.value for s in p.holdings]
    notes: list[str] = []
    days, stale = p.staleness(on)
    if stale:
        notes.append(f"보유 현황이 {days}일 전 기준이다 — 갱신 권고")

    q = prices.quotes(p.tickers)
    if isinstance(q, Unavailable):
        notes.append(f"시세 미확보({q.reason[:60]}) — 장부가로 대체 계산했다. 평가금액은 확인 필요")
        values = {h.ticker: h.book_value for h in hs}
        px_src: Sourced[float] | Unavailable = q
    else:
        values = {h.ticker: h.quantity * q[h.ticker].value for h in hs}
        px_src = next(iter(q.values()))
        for t, s in q.items():
            record(s, subject=f"콕핏 시세 {t}")

    surface = weights(hs, values)
    targets = etf_holdings.equity_etfs(hs)
    skipped = [h.ticker for h in hs
               if h.asset_type is not AssetType.SINGLE_STOCK
               and not h.asset_type.has_equity_constituents]
    cons, missing = etf_holdings.look_through_map(targets)
    if missing:
        notes.append(f"ETF {len(missing)}개 구성종목 미확보 — 집중도가 과소평가된다")
    rows = look_through(surface, hs, cons)

    effective = {r.ticker: r.total for r in rows}
    for h in hs:
        if h.asset_type is not AssetType.SINGLE_STOCK and not h.asset_type.has_equity_constituents:
            effective[h.ticker] = surface.get(h.ticker, 0.0)

    fx = frankfurter.rate()
    if not isinstance(fx, Unavailable):
        record(fx, subject="콕핏 환율")
    if not can_compute_contribution():
        notes.append(f"수익 기여도 산출 불가 — 스냅샷 {len(list_snapshots())}개 (2개 이상 필요)")

    return CockpitResult(on, p, values, surface, effective, rows, missing, skipped,
                         cons, fx, foreign_ratio(hs, values), px_src,
                         sum(values.values()), notes)


# ── 마크다운 ────────────────────────────────────────────────────────────

def to_markdown(r: CockpitResult) -> str:
    L = [f"# 포트폴리오 콕핏 — {r.on.isoformat()}", "", dash.DISCLAIMER, ""]
    L += [f"보유 {len(r.portfolio.holdings)}종목 · 평가액 **{r.total:,.0f}** · "
          f"보유 현황 갱신 {r.portfolio.updated} ({r.portfolio.staleness(r.on)[0]}일 전)", ""]
    if r.notes:
        L += ["## 확인 필요", ""] + [f"- {n}" for n in r.notes] + [""]

    L += ["## 표면 구성 (ETF를 한 덩어리로)", "",
          "| 종목 | 평가액 | 비중 | 자산유형 | 평가 잣대 |", "|---|---:|---:|---|---|"]
    types = {s.value.ticker: s.value.asset_type for s in r.portfolio.holdings}
    for t, w in sorted(r.surface.items(), key=lambda kv: -kv[1]):
        at = types[t]
        L.append(f"| {t} | {r.values[t]:,.0f} | {w:.2%} | {at.value} | {at.basis.value} |")
    L += ["", f"- [사실] HHI **{hhi(r.surface):.4f}** · 유효 종목 수 "
              f"**{effective_positions(r.surface):.2f}개** (실제 {len(r.surface)}종목)", ""]

    L += ["## 숨은 중복 노출 (ETF 룩스루)", ""]
    if r.skipped:
        L.append(f"- 제외: {', '.join(r.skipped)} — 주식 구성종목이 없어 룩스루 대상이 아니다")
    for t, c in r.constituents.items():
        L.append(f"- {t}: {len(c)}종목 분해")
    for m in r.missing:
        L.append(f"- ⚠ {m}")
    L += ["", "| 종목 | 직접 | ETF 경유 | 실질 | 증가 | 숨은 비율 |", "|---|---:|---:|---:|---:|---:|"]
    for row in r.rows:
        if row.total < 0.004:
            continue
        L.append(f"| {row.ticker} | {row.direct:.2%} | {row.via_etf:.2%} | "
                 f"**{row.total:.2%}** | {row.total - row.direct:+.2%} | {row.hidden_ratio:.0%} |")

    L += ["", "## 실질 집중도", "",
          f"- [사실] HHI {hhi(r.surface):.4f} → **{hhi(r.effective):.4f}**",
          f"- [사실] 유효 종목 수 {effective_positions(r.surface):.2f}개 → "
          f"**{effective_positions(r.effective):.2f}개**",
          f"- [사실] 보유 {len(r.surface)}종목 → 실제 노출 "
          f"{len([x for x in r.rows if x.total >= 1e-4])}개 기업", ""]

    L += ["## 환노출", ""]
    if isinstance(r.fx, Unavailable):
        L.append(f"- {r.fx.cite()}")
    else:
        L += [f"- [사실] 해외자산 비중 **{r.foreign:.0%}** · USD/KRW {r.fx.value:,.2f}",
              f"  ↳ 출처: {r.fx.cite()}", "",
              "| 환율 변동 | 원화 환산 수익률 |", "|---:|---:|"]
        L += [f"| {s.move:+.0%} | {s.krw_return:+.2%} |" for s in sensitivity(0.0, r.foreign)]

    L += ["", "## 더 파볼 지점", ""]
    top = max(r.rows, key=lambda x: x.total, default=None)
    if top:
        L.append(f"- [해석] {top.ticker} 실질 노출 {top.total:.1%} — 표면 "
                 f"{r.surface.get(top.ticker, 0):.1%}보다 {top.total - r.surface.get(top.ticker, 0):.1%}p 높다. "
                 f"기업 해독기·가격 판독기로 개별 점검 대상.")
    hidden = [x for x in r.rows if x.direct == 0 and x.via_etf >= 0.02]
    if hidden:
        L.append(f"- [해석] 직접 보유가 없는데 실질 노출 2% 이상: "
                 f"{', '.join(f'{x.ticker} {x.total:.1%}' for x in hidden[:5])}")
    L.append("- 비중을 정해주지 않습니다. 어디를 더 볼지만 제시합니다.")
    return "\n".join(L) + "\n"


def write_outputs(r: CockpitResult) -> tuple[Path, Path]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    md = REPORT_DIR / f"{r.on.isoformat()}-cockpit.md"
    md.write_text(to_markdown(r), encoding="utf-8")
    html = dash.render_cockpit(r)
    return md, html


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    res = run(path)
    if isinstance(res, Unavailable):
        print(res); sys.exit(1)
    md, html = write_outputs(res)
    print(f"✓ {md}  ({md.stat().st_size:,} bytes)")
    print(f"✓ {html}  ({html.stat().st_size:,} bytes)")
    print(f"  보유 {len(res.portfolio.holdings)}종목 · 평가액 {res.total:,.0f}")
    print(f"  HHI {hhi(res.surface):.4f} → {hhi(res.effective):.4f} · "
          f"유효 {effective_positions(res.surface):.2f} → {effective_positions(res.effective):.2f}개")
    for n in res.notes:
        print(f"  ⚠ {n}")
