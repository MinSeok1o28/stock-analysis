---
name: company-decoder
description: 기업 해독기 — "이 회사 뭐 하는 회사야?"를 10분 안에 이해하게 만드는 한 장 요약 카드. "AAPL 분석해줘", "삼성전자 어떤 회사야", "이 회사 해독해줘", "요약 카드 만들어줘" 같은 요청에 사용한다. 돈 버는 구조, 사업부문×지역 매출 분해, 업종별 핵심 지표를 출처와 함께 제시한다.
---

# 기업 해독기

## 트리거
`<티커> 분석해줘` · `<회사> 어떤 회사야` · `해독해줘` · `요약 카드`

## 두 가지 모드

| 모드 | 조건 | 출처 등급 | 인용 |
|---|---|---|---|
| **정밀** | 10-K·사업보고서 파일이 있음 | 1차 (로컬 문서) | 페이지까지 |
| **1차 API** | 티커만 주어짐 | 1차 (SEC/DART) | URL만 |

파일이 없어도 `src/sources/sec_edgar.py`로 XBRL 재무를 직접 받는다. 업로드를 요구하지 말 것.
한국 상장사는 `open_dart.py`가 미구현이면 `확인 필요`로 표기한다.

## 절차

1. **자산 유형 확인** — `models.AssetType`. ETF·원자재면 이 스킬을 쓰지 말고 콕핏으로 보낸다.
   `supports_reverse_dcf`가 False면 그 사실을 카드 상단에 적는다.
2. **재무 골격** — `sec_edgar.annual_series()`로 Revenues / NetIncome / OperatingCashFlow / CapEx.
   `free_cash_flow()`로 FCF 계열. 전부 `Sourced`로 받는다.
3. **이상치 점검** — `core/valuation/outliers.detect()`. 이탈 연도는 카드에 명시한다.
4. **돈 버는 구조** — 10-K Item 1(Business)에서 수익 모델을 다이어그램(mermaid)으로.
   섹션 추출은 `core/narrative/sections.split_us_items()`.
5. **매출 분해** — 사업부문 × 지역 두 축. 세그먼트 주석에서 뽑는다.
   숫자가 없으면 만들지 말고 `확인 필요`.
6. **업종별 핵심 지표** — `reference/sector-metrics.md` 참조. 업종이 목록에 없으면
   가장 가까운 것을 쓰고 그 사실을 적는다.

## 출력 (한 장 카드)

```
# <회사명> (<티커>) 해독 카드 — <날짜>
> 자산유형 · 시장 · 평가 잣대

## 한 줄 요약            [해석] 2문장 이내
## 돈 버는 구조          mermaid 다이어그램
## 매출 분해             사업부문 표 / 지역 표 (각 셀에 출처)
## 업종 핵심 지표        지표명 · 값 · 추세 · 출처
## 재무 골격 5년         매출·순이익·FCF (이상치 표시)
## 확인 필요             얻지 못한 항목과 이유
## 더 파볼 지점          [해석] 3개 이내
```

마지막 절은 "무엇을 더 봐야 하는지"만 쓴다. 투자 의견을 쓰지 않는다.

## 사용 모듈
`sources/sec_edgar` `sources/open_dart` · `core/valuation/outliers` ·
`core/narrative/sections` · `render/brief.fact_line`
