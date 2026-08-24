"""10-K item 섹션 분리. diff의 전처리 — 전체 문서를 비교하면 노이즈가 지배한다.

참고: lefterisloukas/edgar-crawler (GPL-3.0)가 같은 일을 더 정교하게 한다.
정밀도가 필요해지면 그쪽으로 교체하되 반환 계약(dict[str, str])을 유지할 것.
"""

from __future__ import annotations

import re

US_ITEMS: dict[str, str] = {
    "1": "Business",
    "1A": "Risk Factors",
    "3": "Legal Proceedings",
    "7": "MD&A",
    "7A": "Market Risk",
}

KR_SECTIONS: tuple[str, ...] = (
    "사업의 내용", "위험관리 및 파생거래", "이사의 경영진단 및 분석의견",
    "주요계약 및 연구개발활동", "기타 참고사항",
)

# 우선순위: 변화가 가장 의미 있는 섹션부터
DIFF_PRIORITY: tuple[str, ...] = ("1A", "7", "1", "7A", "3")


def strip_html(raw: str) -> str:
    txt = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
    txt = re.sub(r"(?s)<[^>]+>", " ", txt)
    txt = (txt.replace("&nbsp;", " ").replace("&amp;", "&")
              .replace("&#8217;", "'").replace("&#8220;", '"').replace("&#8221;", '"'))
    return re.sub(r"[ \t]{2,}", " ", txt)


def split_us_items(text: str) -> dict[str, str]:
    """'Item 1A.' 형태의 헤딩으로 분할한다. 목차 중복은 가장 긴 블록을 채택."""
    pat = re.compile(r"(?im)^\s*item\s+(\d{1,2}[AB]?)\s*[.\-:—]?\s*(.{0,80})$")
    marks = [(m.start(), m.group(1).upper()) for m in pat.finditer(text)]
    if not marks:
        return {}
    out: dict[str, str] = {}
    for idx, (pos, key) in enumerate(marks):
        end = marks[idx + 1][0] if idx + 1 < len(marks) else len(text)
        body = text[pos:end].strip()
        if key not in out or len(body) > len(out[key]):
            out[key] = body
    return {k: v for k, v in out.items() if k in US_ITEMS and len(v) > 400}


def split_kr_sections(text: str) -> dict[str, str]:
    hits = [(m.start(), m.group(0)) for s in KR_SECTIONS
            for m in re.finditer(re.escape(s), text)]
    hits.sort()
    out: dict[str, str] = {}
    for i, (pos, name) in enumerate(hits):
        end = hits[i + 1][0] if i + 1 < len(hits) else len(text)
        body = text[pos:end].strip()
        if name not in out or len(body) > len(out[name]):
            out[name] = body
    return {k: v for k, v in out.items() if len(v) > 400}


def sections_to_compare(old: dict[str, str], new: dict[str, str]) -> list[str]:
    """양쪽에 모두 존재하는 섹션만, 중요도 순으로. 한쪽만 있으면 그 사실 자체가 신호다."""
    common = set(old) & set(new)
    ordered = [k for k in DIFF_PRIORITY if k in common]
    return ordered + sorted(common - set(ordered))
