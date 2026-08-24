---
name: portfolio-cockpit
description: 포트폴리오 콕핏 — 종목별 카드를 모아 포트폴리오 전체를 진단한다. "포트폴리오 봐줘", "콕핏 돌려줘", "집중도 어때", "환노출 계산해줘", "내 포트 진단" 같은 요청에 사용한다. 집중도(HHI), ETF 경유 숨은 중복 노출, 자산 유형별 평가, 환율 민감도를 계산한다.
---

# 포트폴리오 콕핏

## 트리거
`포트폴리오 봐줘` · `콕핏` · `집중도` · `환노출` · `내 포트 진단`

## 입력
`portfolio/holdings.yaml` — 사람이 수동 갱신한다. **계좌 연동 없음.**
파일이 오래됐으면(최근 스냅샷과 7일 이상 차이) 갱신을 먼저 요청한다.

## 절차

1. **자산 유형별 분류** — `models.AssetType`. 유형별로 다른 잣대를 쓴다:

   | basis | 적용 |
   |---|---|
   | `FUNDAMENTAL` | 개별주·리츠 → 펀더멘털 + 역DCF 가능 |
   | `INDEX_AGGREGATE` | 지수·섹터 ETF → 개별 펀더멘털 대신 지수 총계 |
   | `NO_FUNDAMENTAL` | 원자재 ETF·현금 → `alt_metrics`로 평가 |
   | `PAR_AND_YIELD` | 채권 ETF → 듀레이션·만기수익률 |

   원자재 ETF는 **펀더멘털이 애초에 없다는 사실을 명시**하고 실질금리·달러 방향으로 본다.

2. **집중도** — `concentration.hhi()` + `effective_positions()`.
   "10종목을 담았어도 유효 종목 수는 3.2개"처럼 제시한다.

3. **숨은 중복 노출** — `concentration.look_through()`.
   `sources/etf_holdings`가 미구현이면 빈 dict를 넘기고
   **"ETF 구성종목 미확보 — 집중도가 과소평가됨"을 반드시 표기한다.**
   구현 후에는 발행사 파일(T+1)인지 N-PORT(분기+60일)인지 신선도를 적는다.

4. **환노출** — `fx_exposure.foreign_ratio()` + `sensitivity()`.
   `sources/frankfurter.rate("USD","KRW")`로 현재 환율. **ECB 영업일 종가**임을 명시한다.

5. **수익 기여도** — `portfolio/snapshots/` 의 이전 스냅샷과 비교.
   스냅샷이 1개뿐이면 "기여도 산출 불가 — 스냅샷 부족"으로 남긴다.

## 출력

```
# 포트폴리오 콕핏 — <날짜>
## 구성            자산유형별 비중 · 평가 잣대
## 집중도          HHI · 유효 종목 수
## 숨은 중복 노출   직접 + ETF경유 = 실질 (미확보 시 경고)
## 자산유형별 평가  유형마다 다른 지표
## 환노출          해외비중 · 환율 시나리오 표
## 수익 기여도     스냅샷 비교 (부족 시 명시)
## 확인 필요
## 더 파볼 지점    [해석] — 비중을 정해주지 않는다
```

리밸런싱은 계산과 시나리오만 보여준다. 목표 비중을 제시하지 않는다.

## 사용 모듈
`sources/{etf_holdings,frankfurter}` ·
`core/valuation/{concentration,fx_exposure}` · `render/brief`
