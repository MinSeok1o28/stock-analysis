"""연도별 공시 문장 단위 비교. 신규 등장 / 사라진 문구.

사람이 수백 페이지 문서 여러 개를 나란히 놓고 비교하는 건 사실상 불가능하다.
그걸 대신하는 것이 이 모듈의 존재 이유다.

핵심 규칙: 변화가 없으면 '변화 없음'을 반환한다. 억지로 스토리를 만들지 않는다.
stdlib difflib만 사용 — 순수, 네트워크 없음.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

_SENT = re.compile(r"(?<=[.!?。])\s+|\n{2,}")


def split_sentences(text: str, *, min_len: int = 25) -> list[str]:
    return [s.strip() for s in _SENT.split(text) if len(s.strip()) >= min_len]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[\d,.$%()]", "", s.lower())).strip()


@dataclass(frozen=True)
class SentenceDiff:
    added: list[str]
    removed: list[str]
    modified: list[tuple[str, str, float]]   # (이전, 이후, 유사도)
    unchanged_ratio: float

    @property
    def is_material(self) -> bool:
        return bool(self.added or self.removed or self.modified)

    def summary(self) -> str:
        if not self.is_material:
            return "변화 없음 (문장 단위 실질 변경 미검출)"
        return (f"신규 {len(self.added)}문장 · 삭제 {len(self.removed)}문장 · "
                f"수정 {len(self.modified)}문장 · 동일 비율 {self.unchanged_ratio:.1%}")


def compare(old: str, new: str, *, modify_threshold: float = 0.72) -> SentenceDiff:
    """문장 단위 비교.

    modify_threshold 이상 유사하면 '수정', 그 아래면 신규/삭제로 본다.
    Lazy Prices 논문의 유사도 접근을 문장 수준으로 적용한 것.
    """
    a, b = split_sentences(old), split_sentences(new)
    na, nb = [_norm(s) for s in a], [_norm(s) for s in b]
    same_a, same_b = set(), set()
    for i, x in enumerate(na):
        for j, y in enumerate(nb):
            if j in same_b:
                continue
            if x == y:
                same_a.add(i); same_b.add(j); break

    cand_a = [i for i in range(len(a)) if i not in same_a]
    cand_b = [j for j in range(len(b)) if j not in same_b]

    modified: list[tuple[str, str, float]] = []
    used_b: set[int] = set()
    for i in cand_a:
        best, best_r = None, 0.0
        for j in cand_b:
            if j in used_b:
                continue
            r = difflib.SequenceMatcher(None, na[i], nb[j]).ratio()
            if r > best_r:
                best, best_r = j, r
        if best is not None and best_r >= modify_threshold:
            modified.append((a[i], b[best], best_r))
            used_b.add(best); same_a.add(i)

    added = [b[j] for j in cand_b if j not in used_b]
    removed = [a[i] for i in cand_a if i not in same_a]
    total = max(len(a), len(b), 1)
    return SentenceDiff(added, removed, modified, len(same_b) / total)
