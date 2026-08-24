"""서사(해석) 저장소. Claude 가 쓴 해석을 파일로 남긴다.

## 왜 파일인가
원본 유튜브 방식의 강점은 **자연어 서사**다 — "이건 미디어 회사가 아니라 놀이공원 회사",
"지난 3년은 신뢰 회복 스토리". 이건 계산으로 안 나오고 Claude 가 써야 한다.

그런데 채팅 출력으로만 두면 사라지고 재현되지 않는다.
파일로 남기면 버전 관리되고, 대시보드가 읽어 쓰고, 나중에 다시 검증할 수 있다.

## 경계
- **사실은 파이프라인이 조립한다** (출처 붙은 숫자)
- **해석은 Claude 가 쓴다** (이 파일)
- 렌더는 둘을 합치되 `[사실]`/`[해석]` 을 구분해 표기한다

해석에 숫자를 쓸 때는 반드시 카드에 있는 값을 인용한다. 새 숫자를 만들지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

NARRATIVE_DIR = Path("portfolio/narratives")
STALE_DAYS = 90


@dataclass(frozen=True)
class Risk:
    """이 회사가 망하는 시나리오. 원본 규칙: **정확히 3개로 압축한다.**

    10-K Item 1A 에는 20개씩 나열돼 있고 아무도 안 읽는다.
    영향이 큰 것만 골라야 읽힌다.
    """

    title: str
    detail: str = ""
    evidence: str = ""      # 근거 위치 (10-K Item 1A, 세그먼트 표 등)


@dataclass(frozen=True)
class Narrative:
    ticker: str
    updated: date | None
    one_liner: str = ""          # 피터 린치 2분 룰 — 초등학생도 이해할 한 문장
    how_it_makes_money: str = ""
    mermaid: str = ""            # 돈 버는 구조 다이어그램 (선택)
    story: str = ""              # 지난 3년 서사
    risks: list[Risk] = field(default_factory=list)
    watch_next: list[str] = field(default_factory=list)
    author: str = "claude"
    path: Path | None = None

    @property
    def is_empty(self) -> bool:
        return not (self.one_liner or self.story or self.risks)

    def staleness(self, today: date | None = None) -> tuple[int, bool]:
        if self.updated is None:
            return (-1, True)
        d = ((today or date.today()) - self.updated).days
        return (d, d > STALE_DAYS)


def path_for(ticker: str, directory: Path = NARRATIVE_DIR) -> Path:
    return directory / f"{ticker.upper()}.yaml"


def load(ticker: str, directory: Path = NARRATIVE_DIR) -> Narrative:
    """없으면 빈 Narrative 를 반환한다 — 해석이 없어도 카드는 나와야 한다."""
    p = path_for(ticker, directory)
    if not p.exists():
        return Narrative(ticker.upper(), None, path=p)
    try:
        d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return Narrative(ticker.upper(), None, path=p)
    upd = d.get("updated")
    if isinstance(upd, str):
        try:
            upd = date.fromisoformat(upd)
        except ValueError:
            upd = None
    risks = []
    for r in (d.get("risks") or [])[:3]:      # 3개로 강제
        if isinstance(r, str):
            risks.append(Risk(r))
        elif isinstance(r, dict):
            risks.append(Risk(str(r.get("title", "")), str(r.get("detail", "")),
                              str(r.get("evidence", ""))))
    return Narrative(
        ticker=ticker.upper(), updated=upd if isinstance(upd, date) else None,
        one_liner=str(d.get("one_liner", "")).strip(),
        how_it_makes_money=str(d.get("how_it_makes_money", "")).strip(),
        mermaid=str(d.get("mermaid", "")).strip(),
        story=str(d.get("story", "")).strip(),
        risks=risks,
        watch_next=[str(x) for x in (d.get("watch_next") or [])],
        author=str(d.get("author", "claude")), path=p)


def save(n: Narrative, directory: Path = NARRATIVE_DIR) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    p = path_for(n.ticker, directory)
    doc = {
        "ticker": n.ticker,
        "updated": (n.updated or date.today()).isoformat(),
        "author": n.author,
        "one_liner": n.one_liner,
        "how_it_makes_money": n.how_it_makes_money,
        "mermaid": n.mermaid,
        "story": n.story,
        "risks": [{"title": r.title, "detail": r.detail, "evidence": r.evidence}
                  for r in n.risks[:3]],
        "watch_next": n.watch_next,
    }
    p.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False,
                                default_flow_style=False), encoding="utf-8")
    return p


def template(ticker: str) -> str:
    """Claude 가 채울 빈 서식. 스킬이 이걸 참고해 쓴다."""
    return f"""# {ticker.upper()} 서사 — Claude 가 카드의 [사실] 위에 쓰는 [해석]
# 숫자는 반드시 카드에 있는 값을 인용한다. 새 숫자를 만들지 않는다.
ticker: {ticker.upper()}
updated: {date.today().isoformat()}
author: claude

# 피터 린치 2분 룰 — 초등학생도 이해할 한 문장.
# "2분 안에 설명하지 못하면 그 주식을 사지 말라."
one_liner: ""

# 돈 버는 구조. 세그먼트 표의 숫자를 근거로 서술한다.
how_it_makes_money: ""

# 선택: mermaid 다이어그램
mermaid: ""

# 지난 3년의 이야기. 스토리 리더의 문구 변화를 근거로.
story: ""

# 이 회사가 망하는 시나리오 — **정확히 3개**.
# 10-K Item 1A 에 20개가 있지만 아무도 안 읽는다. 영향 큰 것만.
risks:
  - title: ""
    detail: ""
    evidence: ""

# 다음에 지켜볼 것
watch_next: []
"""
