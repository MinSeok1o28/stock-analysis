"""스토리 리더 (2층). 지난 2~3년 공시 문구의 변화를 추적한다.

요약 카드가 스냅샷이면 이건 영화. 사람이 수백 페이지 문서 여러 개를 나란히 놓고
비교하는 건 사실상 불가능한데, 그걸 대신한다.

**변화가 없으면 "변화 없음"이 결론이다.** 억지로 스토리를 만들지 않는다 (CLAUDE.md).

`python3 -m src.pipelines.story_reader NVDA`
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from ..core.narrative.hedging import (ToneChange, hedge_delta, risk_terms_appeared,
                                      tone_downgrades)
from ..core.narrative.sections import US_ITEMS, sections_to_compare, split_us_items
from ..core.narrative.sentence_diff import SentenceDiff, compare
from ..models import Filing, Market
from ..provenance import Sourced, Unavailable, record
from ..sources import sec_edgar, transcripts

REPORT_DIR = Path("reports/story")
MAX_EXCERPTS = 6          # 섹션당 인용 개수 상한. 다 보여주면 읽히지 않는다.


@dataclass
class SectionChange:
    key: str
    label: str
    diff: SentenceDiff
    tone: list[ToneChange]
    hedges: list[tuple[str, int, int]]
    new_risks: list[str]

    @property
    def is_material(self) -> bool:
        return bool(self.diff.is_material or self.tone or self.hedges or self.new_risks)


@dataclass
class YearPair:
    older: Filing
    newer: Filing
    sections: list[SectionChange]
    missing_sections: list[str] = field(default_factory=list)
    parsed: bool = True          # 섹션 분리에 성공했는가

    @property
    def verdict(self) -> str:
        if not self.parsed:
            return "확인 필요 — 섹션 분리 실패 (문서 서식 변경 가능성)"
        if not self.material_sections:
            return "변화 없음"
        return f"{len(self.material_sections)}개 섹션 변화"

    @property
    def material_sections(self) -> list[SectionChange]:
        return [s for s in self.sections if s.is_material]


@dataclass
class StoryResult:
    ticker: str
    on: date
    filings: list[Filing]
    pairs: list[YearPair]
    transcript: Sourced | Unavailable
    market: Market
    notes: list[str] = field(default_factory=list)


def run(ticker: str, years: int = 3, on: date | None = None) -> StoryResult | Unavailable:
    on = on or date.today()
    notes: list[str] = []

    fs = sec_edgar.annual_filings(ticker, limit=years)
    if isinstance(fs, Unavailable):
        return fs
    if len(fs) < 2:
        return Unavailable(f"{ticker} 스토리", f"비교할 10-K 가 부족하다 ({len(fs)}개)")

    texts: dict[int, str] = {}
    for f in fs:
        t = sec_edgar.filing_text(f)
        if isinstance(t, Unavailable):
            notes.append(f"FY{f.fiscal_year} 본문 미확보 — {t.reason[:70]}")
            continue
        texts[f.fiscal_year] = t.value
        record(t, subject=f"{ticker} 10-K FY{f.fiscal_year}")

    ordered = sorted((f for f in fs if f.fiscal_year in texts), key=lambda f: f.fiscal_year)
    if len(ordered) < 2:
        return Unavailable(f"{ticker} 스토리", "본문을 2개년 이상 확보하지 못했다")

    pairs: list[YearPair] = []
    for older, newer in zip(ordered, ordered[1:]):
        a = split_us_items(texts[older.fiscal_year])
        b = split_us_items(texts[newer.fiscal_year])
        keys = sections_to_compare(a, b)
        only = sorted((set(a) ^ set(b)) & set(US_ITEMS))
        changes = []
        for k in keys:
            changes.append(SectionChange(
                key=k, label=US_ITEMS.get(k, k),
                diff=compare(a[k], b[k]),
                tone=tone_downgrades(a[k], b[k]),
                hedges=hedge_delta(a[k], b[k])[:5],
                new_risks=risk_terms_appeared(a[k], b[k]),
            ))
        if not keys:
            notes.append(f"FY{older.fiscal_year}→FY{newer.fiscal_year}: "
                         "공통 섹션을 찾지 못했다 — 문서 서식 변경 가능성. "
                         "'변화 없음'이 아니라 '비교 불가'다.")
        pairs.append(YearPair(older, newer, changes, only, parsed=bool(keys)))

    market = Market.KR if ticker.isdigit() else Market.US
    tr = transcripts.quarterly(ticker, on.year, (on.month - 1) // 3 + 1, market)
    if isinstance(tr, Unavailable):
        notes.append("어닝콜 트랜스크립트 미확보 — 가이던스 대조와 경영진 자신감 추적을 생략했다. "
                     "웹검색으로 대체하지 않는다.")
    if market is Market.KR:
        notes.append(Market.KR.transcript_availability)

    return StoryResult(ticker.upper(), on, ordered, pairs, tr, market, notes)


def to_markdown(r: StoryResult) -> str:
    from ..render.brief import DISCLAIMER
    span = f"FY{r.filings[0].fiscal_year}–FY{r.filings[-1].fiscal_year}"
    L = [f"# {r.ticker} 스토리 리더 — {span} ({r.on.isoformat()})", "", DISCLAIMER, ""]

    total = sum(len(p.material_sections) for p in r.pairs)
    L += ["## 결론 한 줄", ""]
    unparsed = [p for p in r.pairs if not p.parsed]
    if unparsed:
        L.append(f"- **확인 필요** — {len(unparsed)}개 구간에서 섹션 분리에 실패했다. "
                 "변화가 없는 것이 아니라 비교하지 못한 것이다.")
    elif total:
        L.append(f"- 유의미한 변화가 검출된 섹션 {total}개.")
    else:
        L.append("- **변화 없음** — 문장·표현 수준에서 실질 변경이 검출되지 않았다.")
    L += ["", "## 비교 대상", "", "| 회계연도 | 제출일 | 원문 |", "|---|---|---|"]
    for f in r.filings:
        L.append(f"| FY{f.fiscal_year} | {f.filed_on} | `{f.primary_document}` |")

    for p in r.pairs:
        L += ["", f"## FY{p.older.fiscal_year} → FY{p.newer.fiscal_year}", ""]
        if p.missing_sections:
            L.append(f"- [사실] 한쪽에만 존재하는 섹션: {', '.join(p.missing_sections)} "
                     "— 섹션 신설·삭제 자체가 신호다")
        if not p.parsed:
            L.append("- **확인 필요** — 섹션을 분리하지 못해 비교하지 못했다")
            continue
        if not p.material_sections:
            L.append("- **변화 없음** (비교한 모든 섹션에서 실질 변경 미검출)")
            continue
        for s in p.material_sections:
            L += ["", f"### Item {s.key} · {s.label}", "", f"- {s.diff.summary()}"]
            if s.tone:
                L += ["", "**약해진 표현**", ""]
                for t in s.tone:
                    L += [f"- [사실] {t}",
                          f"  - 이전: …{t.excerpt_before}…" if t.excerpt_before else "",
                          f"  - 이후: …{t.excerpt_after}…" if t.excerpt_after else ""]
            if s.hedges:
                L += ["", "**헤지 어휘 증가**", ""]
                L += [f"- [사실] `{w}` {a}회 → {b}회" for w, a, b in s.hedges]
            if s.new_risks:
                L += ["", "**신규 위험 어휘**", "",
                      f"- [사실] {', '.join(s.new_risks)}"]
            if s.diff.added:
                L += ["", "**신규 문장**", ""]
                L += [f"- [사실] {x}" for x in s.diff.added[:MAX_EXCERPTS]]
                if len(s.diff.added) > MAX_EXCERPTS:
                    L.append(f"- _…외 {len(s.diff.added) - MAX_EXCERPTS}건_")
            if s.diff.removed:
                L += ["", "**사라진 문장**", ""]
                L += [f"- [사실] {x}" for x in s.diff.removed[:MAX_EXCERPTS]]
                if len(s.diff.removed) > MAX_EXCERPTS:
                    L.append(f"- _…외 {len(s.diff.removed) - MAX_EXCERPTS}건_")

    L += ["", "## 어닝콜", ""]
    L.append(f"- {r.transcript.cite() if isinstance(r.transcript, Unavailable) else '확보'}")

    if r.notes:
        L += ["", "## 확인 필요", ""] + [f"- {n}" for n in r.notes]

    L += ["", "## 더 파볼 지점", ""]
    hot = [(p, s) for p in r.pairs for s in p.material_sections
           if s.new_risks or len(s.tone) >= 2]
    for p, s in hot[:3]:
        why = (f"신규 위험 어휘 {', '.join(s.new_risks)}" if s.new_risks
               else f"표현 약화 {len(s.tone)}건")
        L.append(f"- [해석] Item {s.key}({s.label}) — {why}. 원문에서 맥락 확인 권장.")
    L.append("- 이 문서는 변화를 짚을 뿐 매매 판단을 제시하지 않습니다.")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    tk = sys.argv[1] if len(sys.argv) > 1 else "NVDA"
    yrs = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    res = run(tk, yrs)
    if isinstance(res, Unavailable):
        print(res); sys.exit(1)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORT_DIR / f"{res.ticker}-story-{res.on.isoformat()}.md"
    out.write_text(to_markdown(res), encoding="utf-8")
    print(f"✓ {out} ({out.stat().st_size:,} bytes)")
    for p in res.pairs:
        print(f"  FY{p.older.fiscal_year}→FY{p.newer.fiscal_year}: {p.verdict}")
        for s in p.material_sections:
            bits = [s.diff.summary()]
            if s.tone: bits.append(f"톤다운 {len(s.tone)}")
            if s.new_risks: bits.append(f"신규위험 {','.join(s.new_risks)}")
            print(f"    Item {s.key:3s} {s.label:16s} {' · '.join(bits)[:88]}")
