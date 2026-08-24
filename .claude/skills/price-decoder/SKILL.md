---
name: price-decoder
description: 가격 판독기 — "지금 사도 되나?"에 적정주가로 답하지 않고 거꾸로 묻는다. "AAPL 지금 사도 되나", "밸류에이션 봐줘", "역DCF 돌려줘", "이 가격 비싼가" 같은 요청에 사용한다. 역DCF로 현재 시가총액이 요구하는 성장률을 이분법으로 역산하고 과거 실제 성장률과 나란히 놓는다.
---

# 가격 판독기

**목표주가·적정주가를 산출하지 않는다.** 그건 곧 매매 신호가 된다.
"시장이 요구하는 성장률"과 "과거 실제 성장률"의 격차만 제시한다.

## 트리거
`<티커> 지금 사도 되나` · `밸류에이션` · `역DCF` · `이 가격 비싼가`

## 1. 사전 확인

`models.AssetType.supports_reverse_dcf` 가 False 면 **중단하고 콕핏으로 보낸다.**
개별주·리츠만 역DCF 가 성립한다. 지수 ETF 는 지수 총계로, 원자재 ETF 는
`alt_metrics`(실질금리·달러 방향)로 본다.

## 2. 입력을 모은다

전용 파이프라인은 아직 없다. 아래를 순서대로 실행해 값을 얻는다.

```python
from src.sources import sec_edgar, open_dart, prices, fred
from src.core.valuation.outliers import detect, normalized_base
from src.core.valuation.reverse_dcf import implied_growth, wacc_sensitivity, cagr, gap_summary

fcfs   = sec_edgar.free_cash_flow("AAPL")          # 한국이면 open_dart.free_cash_flow
series = [(f"FY{s.value.fiscal_year}", s.value.value) for s in fcfs]
base   = normalized_base(series)                    # 3년 평균 — 단년 이상치를 피한다
shares = sec_edgar.annual_series("AAPL", "SharesOutstanding")[-1]
mcap   = prices.last_close("AAPL").value * shares.value.value
rf     = fred.latest("us10y").value / 100           # 무위험수익률
```

WACC 는 단일값으로 확정하지 말고 `rf + 주식위험프리미엄` 을 기준점으로 두되
**민감도 범위로 다룬다.** ERP 가정은 `[해석]` 으로 표기한다.
FRED 키가 없으면 기본 범위(7~11%)를 쓰고 "무위험수익률 미확인"을 적는다.

## 3. 역산한다 — 암산 금지

```python
r = implied_growth(mcap, base, wacc)                # 이분법. 수렴 진단 포함
rows = wacc_sensitivity(mcap, base)                 # 필수 — 하나의 할인율에 의존하지 않는다
h = cagr(series[0][1], series[-1][1], len(series) - 1)
print(gap_summary(r.value, h))
```

**과거 성장률은 시작점에 크게 좌우된다.** 3년·5년·전체 구간을 함께 제시하고
어느 구간인지 명시한다. 한 구간만 보여주면 오해를 만든다.

## 4. 실패를 숨기지 않는다

| 상황 | 예외 | 보고 |
|---|---|---|
| FCF ≤ 0 | `ValueError` | 역DCF 불가. 이익 정상화 필요를 그대로 보고 |
| 상한 성장률로도 시가 미달 | `ConvergenceError` | "요구 성장률이 비현실적으로 높다" |
| WACC ≤ 영구성장률 | `ValueError` | 가정 재검토 필요 |

세 경우 모두 **숫자를 만들어내지 않는다.**

## 5. 출력

`render/brief.valuation_report()` 형식을 쓴다. 반드시 포함할 것:

- 기준 FCF 와 그것이 3년 평균인지 단년인지
- 이분법 수렴 여부·반복 횟수
- WACC 민감도 표
- 시장 요구 성장률 vs 과거 실적 (구간 명시)
- 마지막 문장:
  > 이 격차는 판단 재료입니다. 이 가격이 요구하는 성장이 실제로 나올 회사인지는 사람이 판단합니다.

## 모듈
`sources/{sec_edgar,open_dart,prices,fred}` ·
`core/valuation/{reverse_dcf,outliers}` · `render/brief`
