"""마크다운 렌더. 출처 없는 값은 여기서 막힌다.

require_sourced()가 문지기 역할을 한다. 렌더 계층이 마지막 방어선인 이유:
중간 계층에서 실수로 맨 숫자를 만들어도 산출물로 나가지 못한다.
"""

from __future__ import annotations

from datetime import date

from ..models import Signal
from ..provenance import Sourced, Unavailable, require_sourced

DISCLAIMER = (
    "> 이 문서는 리서치 보조 산출물이며 투자 자문이 아닙니다. "
    "매매 판단은 사람이 합니다. 1차 스크리너로만 사용하고, "
    "판단에 직접 쓰는 숫자는 원문에서 재확인하십시오."
)


def fact_line(label: str, obj: Sourced | Unavailable, fmt: str = "{}") -> str:
    """사실 1줄. `[사실]` 태그와 출처를 항상 붙인다."""
    obj = require_sourced(label, obj)
    if isinstance(obj, Unavailable):
        return f"- **{label}**: {obj.cite()}"
    return f"- **{label}**: [사실] {fmt.format(obj.value)}  \n  ↳ 출처: {obj.cite()}"


def interpretation(text: str) -> str:
    return f"- [해석] {text}"


def signals_block(signals: list[Signal]) -> str:
    if not signals:
        return "_오늘 추가로 파볼 항목 없음._"
    lines = ["| 신호 | 대상 | 근거 |", "|---|---|---|"]
    for s in signals:
        lines.append(f"| {s.kind.value} | {s.ticker or '—'} | {s.reason} |")
    return "\n".join(lines)


def daily_brief(
    *,
    on: date,
    macro: dict[str, Sourced | Unavailable],
    holdings_notes: list[str],
    signals: list[Signal],
    qualitative: list[Sourced[str]] | None = None,
) -> str:
    parts = [f"# 일일 브리핑 — {on.isoformat()}", "", DISCLAIMER, "", "## 매크로·환율", ""]
    parts += [fact_line(k, v, "{:,.2f}") for k, v in macro.items()]
    parts += ["", "## 보유 종목", ""] + (holdings_notes or ["_수동 갱신 필요_"])
    parts += ["", "## 오늘의 액션 신호", "",
              "결론을 내리지 않습니다. 어디를 더 파볼지만 제시합니다.", "",
              signals_block(signals)]
    if qualitative:
        parts += ["", "## 정성 관찰 (3차 출처 — 수치 아님)", ""]
        parts += [f"- {q.value}  \n  ↳ {q.cite()}" for q in qualitative]
    return "\n".join(parts) + "\n"


def valuation_report(*, ticker: str, on: date, base_fcf: Sourced | Unavailable,
                     implied, historical, sensitivity, outliers, gap: str) -> str:
    lines = [f"# 가격 판독기 — {ticker} ({on.isoformat()})", "", DISCLAIMER, "",
             "적정주가를 산출하지 않습니다. 이 가격이 요구하는 성장률만 제시합니다.", "",
             "## 입력", "", fact_line("기준 FCF", base_fcf, "{:,.0f}"), ""]
    if outliers:
        lines += ["### 이상치 감지", ""] + [f"- {o}" for o in outliers] + [""]
    lines += ["## 역산 결과", "",
              f"- [사실] 시장이 요구하는 연평균 성장률: **{implied.value:.2%}** "
              f"(이분법 {implied.iterations}회, {'수렴' if implied.converged else '미수렴'})",
              f"- [사실] 과거 실제 성장률: " +
              (f"**{historical:.2%}**" if historical is not None else "산출 불가 — 확인 필요"),
              f"- [해석] {gap}", "",
              "## WACC 민감도", "", "| WACC | 요구 성장률 |", "|---|---|"]
    for row in sensitivity:
        v = f"{row.implied:.2%}" if row.implied is not None else f"— ({row.note})"
        lines.append(f"| {row.wacc:.0%} | {v} |")
    lines += ["", "하나의 할인율에 의존하지 않도록 민감도를 필수로 포함합니다."]
    return "\n".join(lines) + "\n"
