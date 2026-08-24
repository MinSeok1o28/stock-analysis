---
name: daily-brief
description: 일일 브리핑 — 오늘 뭘 봐야 하는지 한 화면으로. "오늘 브리핑", "브리핑 돌려줘", "오늘 뭐 봐야 해", "밤사이 어땠어" 같은 요청에 사용한다. 보유 종목 밤사이 움직임, 환율·매크로, 다가오는 이벤트를 정리하고 어떤 딥다이브 스킬을 돌릴지 신호를 낸다.
---

# 일일 브리핑

이 시스템의 오케스트레이터. **결론을 내리지 않는다. 어디를 더 파야 하는지만 짚는다.**

## 트리거
`오늘 브리핑` · `브리핑 돌려줘` · `오늘 뭐 봐야 해` · `밤사이 어땠어`

## 절차

1. **매크로** — `frankfurter.rate("USD","KRW")` (ECB 영업일 종가임을 표기),
   `fred.latest("us10y")`, `fred.latest("fedfunds")`. 키 없는 항목은 `확인 필요`.
2. **보유 종목** — `portfolio/holdings.yaml`. 시세는 `sources/prices`가 미구현이면
   `확인 필요`로 두고 움직임 판단을 생략한다. **웹검색 숫자로 채우지 않는다.**
3. **다가오는 이벤트** — 실적 일정. 미구현 소스면 명시.
4. **정성 관찰** — 관심 테마 뉴스는 웹검색(3차). `websearch.note()`로 감싼다.
   수치는 담지 않는다.
5. **신호 생성** — `models.Signal` + `SignalKind`. 규칙:

   | 조건 | 신호 |
   |---|---|
   | 실적 발표 7일 이내 | `RUN_STORY_READER` |
   | 밤사이 ±5% 이상 이동 | `RUN_PRICE_DECODER` |
   | 신규 편입 종목 | `RUN_COMPANY_DECODER` |
   | 해외 비중 70% 초과 + 환율 ±2% | `CHECK_FX_EXPOSURE` |
   | 필수 데이터 미확보 | `DATA_GAP` |

   `SignalKind`에 매수·매도 항목이 없다. 구조적으로 매매 신호를 낼 수 없다.
   `Signal.reason`에 매매 표현을 쓰면 `ValueError`가 난다.

6. **렌더** — `render/brief.daily_brief()` → `reports/daily/<날짜>-brief.md`
7. **기록** — 사용한 모든 출처를 `provenance.record()`로 `ledger/manifest.jsonl`에 남긴다.

## 임계값 조정
위 표의 숫자(7일, ±5%, 70%, ±2%)를 바꾸는 것은 이 파일 한 곳만 고치면 된다.
계산·소스 계층은 건드리지 않는다.

## 사용 모듈
`sources/{frankfurter,fred,prices,websearch}` · `models.Signal` ·
`render/brief` · `provenance.record`
