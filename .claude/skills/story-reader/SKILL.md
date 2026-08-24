---
name: story-reader
description: 스토리 리더 — "요즘 이 회사 어때?" 지난 2~3년 공시와 어닝콜의 변화를 추적한다. "AAPL 스토리 분석해줘", "이 회사 요즘 어때", "공시 변화 봐줘", "톤 변했어?" 같은 요청에 사용한다. 새로 등장한 문구, 사라진 문구, 미묘하게 약해진 표현(will→may)을 원문 인용과 함께 제시한다.
---

# 스토리 리더

요약 카드가 스냅샷이면 이건 지난 2~3년의 영화.

## 트리거
`<티커> 스토리 분석해줘` · `요즘 어때` · `공시 변화` · `톤 변했어?`

## 절차

1. **공시 수집** — `sec_edgar.annual_filings(ticker, limit=3)`. 3개년 10-K.
2. **섹션 분리** — `sections.split_us_items()`. 전체 문서를 비교하면 노이즈가 지배한다.
   비교 순서는 `sections_to_compare()`가 정한다 (Item 1A → 7 → 1 → 7A → 3).
   한쪽 연도에만 있는 섹션은 그 사실 자체가 신호다.
3. **문장 비교** — `sentence_diff.compare(old, new)`.
   `is_material`이 False면 **"변화 없음"이 결론이다.** 억지로 스토리를 만들지 않는다.
4. **톤 분석** — `hedging.tone_downgrades()` / `hedge_delta()` / `risk_terms_appeared()`.
   각 항목은 원문 발췌를 함께 제시한다.
5. **어닝콜** — `sources/transcripts.quarterly()`. `Unavailable`이면
   `확인 필요 (트랜스크립트 미확보)`로 남긴다. **웹검색으로 대체하지 않는다.**
6. **가이던스 대조** — 약속한 숫자 vs 실제 달성치. 트랜스크립트가 없으면 이 절을 생략하고
   생략 이유를 적는다.
7. **한국 상장사** — `models.Market.KR.transcript_availability`를 리포트에 명시한다.

## 출력

```
# <회사명> 스토리 — <기간> (<날짜>)
## 결론 한 줄            변화 없으면 "변화 없음"
## 신규 등장 문구         [사실] 원문 + 출처
## 사라진 문구            [사실] 원문 + 출처
## 약해진 표현            축 · 이전→이후 · 양쪽 발췌
## 헤지 어휘 증감         어휘 · 이전 빈도 → 이후 빈도
## 신규 위험 어휘         impairment / going concern 등
## 어닝콜 자신감 추이     분기별 (미확보면 확인 필요)
## 가이던스 vs 실제       (트랜스크립트 있을 때만)
## 확인 필요
## 더 파볼 지점           [해석] 3개 이내
```

## 사용 모듈
`sources/sec_edgar` `sources/transcripts` ·
`core/narrative/{sections,sentence_diff,hedging}`
