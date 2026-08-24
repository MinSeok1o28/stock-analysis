---
name: price-decoder
description: 가격 판독기 — "지금 사도 되나?"에 적정주가로 답하지 않고 거꾸로 묻는다. "AAPL 지금 사도 되나", "밸류에이션 봐줘", "역DCF 돌려줘", "이 가격 비싼가" 같은 요청에 사용한다. 역DCF로 현재 주가가 요구하는 성장률을 역산해 과거 실제 성장률과 나란히 놓는다.
---

# 가격 판독기

**목표주가·적정주가를 산출하지 않는다.** 그건 곧 매매 신호가 된다.
이 스킬은 "시장이 요구하는 성장률"과 "과거 실제 성장률"의 격차만 제시한다.

## 트리거
`<티커> 지금 사도 되나` · `밸류에이션` · `역DCF` · `이 가격 비싼가`

## 사전 확인
`models.AssetType.supports_reverse_dcf`가 False면 **중단**하고 콕핏으로 보낸다.
지수 ETF는 지수 총계로, 원자재 ETF는 `alt_metrics`(실질금리·달러 방향)로 봐야 한다.

## 절차

1. **FCF 계열** — `sec_edgar.free_cash_flow(ticker)`.
2. **이상치 점검** — `outliers.detect()`. 최근 단년이 이탈했으면
   `outliers.normalized_base()`(3년 평균)를 기준 FCF로 쓰고 그 사실을 적는다.
3. **할인율** — `sources/fred.latest("us10y")`로 무위험수익률 확인.
   WACC를 단일값으로 확정하지 말고 민감도 범위로 다룬다. FRED 키가 없으면
   기본 범위(7~11%)를 쓰고 "무위험수익률 미확인"을 적는다.
4. **역산** — `reverse_dcf.implied_growth()`. **암산 금지.** 이분법 결과의
   수렴 여부·반복 횟수를 리포트에 표시한다.
5. **민감도** — `reverse_dcf.wacc_sensitivity()`. 필수. 하나의 숫자에 의존하지 않게 한다.
6. **과거 대비** — `reverse_dcf.cagr()` + `gap_summary()`.
   `cagr`가 None이면 "산출 불가 — 확인 필요"로 남긴다.
7. **렌더** — `render/brief.valuation_report()`.

## 실패를 숨기지 않는다
- FCF ≤ 0 → `ValueError`. 이익 정상화가 필요하다는 사실을 그대로 보고한다.
- 상한 성장률로도 시가 미달 → `ConvergenceError`. "요구 성장률이 비현실적으로 높다"로 보고.
- 두 경우 모두 숫자를 만들어내지 않는다.

## 출력
`render/brief.valuation_report()` 형식을 그대로 쓴다. 마지막에 이 문장을 넣는다:

> 이 격차는 판단 재료입니다. 이 가격이 요구하는 성장이 실제로 나올 회사인지는 사람이 판단합니다.

## 사용 모듈
`sources/sec_edgar` `sources/fred` ·
`core/valuation/{reverse_dcf,outliers}` · `render/brief`
