"""표현 약화(톤 다운) 사전.

Notion 원문의 예: will→may, "강력한 성장"→"안정적 성장".
학술적 근거: Cohen·Malloy·Nguyen, "Lazy Prices" (2019) — 공시 문구를 바꾸지 않은 기업이
시장을 상회. 즉 '문구 변화'가 신호이고, 그중 약화 방향이 특히 의미 있다.

순수 데이터 + 순수 함수. 사전을 늘리는 것이 이 파일이 바뀌는 유일한 이유다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# (강한 표현, 약한 표현, 축 이름)
DOWNGRADE_PAIRS: tuple[tuple[str, str, str], ...] = (
    ("will", "may", "확약→가능성"),
    ("will", "could", "확약→가능성"),
    ("expect", "hope", "기대→희망"),
    ("strong", "stable", "강함→안정"),
    ("strong", "solid", "강함→견조"),
    ("robust", "resilient", "견고→회복력"),
    ("accelerating", "continued", "가속→지속"),
    ("significant", "modest", "유의미→소폭"),
    ("record", "healthy", "기록적→양호"),
    ("confident", "cautiously optimistic", "확신→조심스러운 낙관"),
    ("commit", "intend", "약속→의도"),
    ("증가", "유지", "증가→유지"),
    ("확대", "유지", "확대→유지"),
    ("강력한", "안정적", "강력→안정"),
    ("가속", "지속", "가속→지속"),
)

# 불확실성을 도입하는 어휘. 등장 빈도 증가 자체가 신호다.
HEDGE_TERMS: tuple[str, ...] = (
    "may", "might", "could", "potentially", "uncertain", "subject to",
    "no assurance", "cannot guarantee", "depending on", "if conditions",
    "불확실", "가능성", "변동될 수", "보장할 수 없", "달라질 수",
)

RISK_ESCALATION: tuple[str, ...] = (
    "material weakness", "going concern", "impairment", "restructuring",
    "covenant", "litigation", "investigation", "delisting",
    "손상", "구조조정", "소송", "조사", "계속기업",
)


@dataclass(frozen=True)
class ToneChange:
    axis: str
    before: str
    after: str
    excerpt_before: str
    excerpt_after: str

    def __str__(self) -> str:
        return f"[{self.axis}] '{self.before}'→'{self.after}'"


def _count(text: str, term: str) -> int:
    return len(re.findall(rf"\b{re.escape(term)}\b", text, flags=re.IGNORECASE))


def hedge_delta(old: str, new: str) -> list[tuple[str, int, int]]:
    """헤지 어휘 빈도 변화. (어휘, 이전, 이후) — 증가한 것만."""
    out = []
    for term in HEDGE_TERMS:
        a, b = _count(old, term), _count(new, term)
        if b > a:
            out.append((term, a, b))
    return sorted(out, key=lambda r: -(r[2] - r[1]))


def risk_terms_appeared(old: str, new: str) -> list[str]:
    """신규 등장한 위험 어휘. 사라진 것은 별도로 다룬다."""
    return [t for t in RISK_ESCALATION if _count(new, t) > 0 and _count(old, t) == 0]


def hedge_removed(old: str, new: str) -> list[tuple[str, int, int]]:
    """**사라진 헤지 어휘.** 증가보다 이쪽이 더 강한 신호인 경우가 많다.

    실제 사례(디즈니 10-K):
      FY2023 "we anticipate this trend to continue, although the extent and
              duration is **uncertain**"
      FY2024 "declines ... which we expect will continue"   ← uncertain 삭제

    "불확실하다"를 빼는 건 회사가 그것을 **기정사실로 못 박았다**는 뜻이다.
    완충 표현의 제거는 톤 강화이자 전망 확정이다.
    """
    out = []
    for term in HEDGE_TERMS:
        a, b = _count(old, term), _count(new, term)
        if a > 0 and b < a:
            out.append((term, a, b))
    return sorted(out, key=lambda r: -(r[1] - r[2]))


def risk_terms_disappeared(old: str, new: str) -> list[str]:
    """사라진 위험 어휘. 해소됐다는 뜻일 수도, 서술을 줄였다는 뜻일 수도 있다."""
    return [t for t in RISK_ESCALATION if _count(old, t) > 0 and _count(new, t) == 0]


#: 문구 확장을 추적할 주제어. 같은 주제를 더 구체적으로 쓰기 시작하면 인식 변화다.
WATCH_TOPICS: tuple[str, ...] = (
    "artificial intelligence", "generative", "large language model",
    "cybersecurity", "tariff", "supply chain", "regulation", "antitrust",
    "climate", "인공지능", "공급망", "규제",
)


@dataclass(frozen=True)
class PhraseExpansion:
    topic: str
    before: str
    after: str

    def __str__(self) -> str:
        return f"[{self.topic}] 서술 확장"


def phrase_expansions(old: str, new: str, *, ctx: int = 110, min_growth: int = 25
                      ) -> list[PhraseExpansion]:
    """주제어 주변 서술이 길고 구체적으로 바뀐 경우.

    실제 사례(디즈니):
      FY2023 "generative artificial intelligence (AI)"
      FY2024 "artificial intelligence (AI), including generative AI and
              large language model tools"
    큰 변화는 아니지만 위협·기회를 더 구체적으로 인식하기 시작한 흔적이다.
    """
    out = []
    for topic in WATCH_TOPICS:
        a, b = _excerpt(old, topic, ctx), _excerpt(new, topic, ctx)
        if not a or not b:
            continue
        if len(b) - len(a) >= min_growth:
            out.append(PhraseExpansion(topic, a, b))
    return out


def tone_downgrades(old: str, new: str, *, ctx: int = 60) -> list[ToneChange]:
    """강한 표현이 줄고 대응 약한 표현이 늘어난 축을 찾는다."""
    out = []
    for strong, weak, axis in DOWNGRADE_PAIRS:
        s_a, s_b = _count(old, strong), _count(new, strong)
        w_a, w_b = _count(old, weak), _count(new, weak)
        if s_b < s_a and w_b > w_a:
            out.append(ToneChange(axis, strong, weak,
                                  _excerpt(old, strong, ctx), _excerpt(new, weak, ctx)))
    return out


def _excerpt(text: str, term: str, ctx: int) -> str:
    m = re.search(rf"\b{re.escape(term)}\b", text, flags=re.IGNORECASE)
    if not m:                      # 다어절·한글 주제어는 단어경계가 안 맞는다
        m = re.search(re.escape(term), text, flags=re.IGNORECASE)
    if not m:
        return ""
    lo, hi = max(0, m.start() - ctx), min(len(text), m.end() + ctx)
    return ("…" if lo else "") + text[lo:hi].replace("\n", " ") + ("…" if hi < len(text) else "")
